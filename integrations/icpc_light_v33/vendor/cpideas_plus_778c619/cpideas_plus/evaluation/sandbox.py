"""Sandboxed program execution adapters.

The generated-code backend is ``LightCPVerifierRunner`` — an HTTP adapter for the
vendored LightCPVerifier service (``vendor/LightCPVerifier``). It provides
``health``, ``submit`` / ``get_result``, and custom-test endpoints. The verifier
already runs in a hardened sandbox, so it is the runtime for untrusted
LLM-produced code.
"""

from __future__ import annotations

import json
import math
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from .local_runtime import (
    EXECUTION_BACKEND_LIGHTCPVERIFIER,
    EXECUTION_CATEGORY_UNSPECIFIED,
    execution_memory_limit_mb,
    execution_time_limit_ms,
)
from .execution import (
    CustomTestBatchCase,
    CustomTestBatchCaseResult,
    CustomTestBatchResult,
    CustomTestResult,
    CustomTestValidationResult,
    CustomTestValidatorSpec,
)


_BATCH_REQUEST_OVERHEAD_SEC = 15.0
_BATCH_REQUEST_TIMEOUT_CAP_SEC = 3600.0
_JS_MAX_SAFE_INTEGER = (1 << 53) - 1
_STRICT_DYNAMIC_TIME_LIMIT_MS = (100, 30_000)
_STRICT_DYNAMIC_MEMORY_LIMIT_MB = (16, 2_048)


@dataclass(frozen=True)
class SandboxResult:
    command: list[str]
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False


@dataclass(frozen=True)
class LightCPVerifierSubmitResult:
    sid: int


@dataclass(frozen=True)
class LightCPVerifierResult:
    status_code: int
    payload: dict[str, Any]

    @property
    def is_judging(self) -> bool:
        return self.status_code == 404 or self.payload.get("status") in {
            "queued",
            "Judging",
        }


class SandboxRunner(Protocol):
    def run(self, command: list[str], cwd: Path, timeout_sec: int) -> SandboxResult: ...


class LightCPVerifierRunner:
    """HTTP adapter for the vendored LightCPVerifier service.

    Three classes of endpoint are wrapped:

    * Health and submission status (``health``, ``submit``, ``get_result``) for the
      regular contest-judging workflow. They expect the verifier to host pre-prepared
      problem packages.
    * ``custom_test`` and ``custom_test_batch`` for OJ-style execution probes. The
      single-input method is used by the package verifier when
      ``runner == "lightcpverifier"``; the batch method prepares each program once.
    * ``ensure_downloaded`` / ``vendor-path`` helpers used by the CLI.

    All calls default to ``http://127.0.0.1:8081`` to match
    ``docker-compose.lightcpverifier.yml``. The constructor's ``executable`` parameter
    is reserved for a future in-process embed; currently unused.  By default the
    adapter retains the shared fixed execution profile.  Callers that already have
    trusted per-problem limits can opt in to strict dynamic forwarding; that mode
    rejects unsupported limits and requires the service to attest that it applied
    them unchanged.
    """

    backend = EXECUTION_BACKEND_LIGHTCPVERIFIER

    def __init__(
        self,
        executable: Path | None = None,
        base_url: str = "http://127.0.0.1:8081",
        repo_dir: Path = Path("vendor/LightCPVerifier"),
        timeout_sec: int = 10,
        strict_dynamic_limits: bool = False,
    ):
        if not isinstance(strict_dynamic_limits, bool):
            raise TypeError("strict_dynamic_limits must be boolean")
        self.executable = executable
        self.base_url = base_url.rstrip("/")
        self.repo_dir = repo_dir
        self.timeout_sec = timeout_sec
        self.strict_dynamic_limits = strict_dynamic_limits

    def run(self, command: list[str], cwd: Path, timeout_sec: int) -> SandboxResult:
        if self.executable is None:
            return SandboxResult(
                command=command,
                exit_code=127,
                stdout="",
                stderr="LightCPVerifier is not configured. Provide an executable path before running verifier jobs.",
            )
        return SandboxResult(
            command=[str(self.executable), *command],
            exit_code=127,
            stdout="",
            stderr="LightCPVerifier local executable mode was removed; use the HTTP service endpoints instead.",
        )

    def ensure_downloaded(self) -> None:
        expected = self.repo_dir / "server.js"
        if not expected.exists():
            raise FileNotFoundError(
                f"LightCPVerifier was not found at {self.repo_dir}. "
                "Clone https://github.com/YanagiOrigami/LightCPVerifier.git into vendor/LightCPVerifier."
            )

    def health(self) -> dict[str, Any]:
        return self._request_json("GET", "/health")

    def submit(
        self,
        pid: str,
        language: str,
        code: str,
        continue_on_failure: bool = False,
    ) -> LightCPVerifierSubmitResult:
        request_body: dict[str, Any] = {
            "pid": pid,
            "lang": language,
            "code": code,
        }
        if continue_on_failure:
            request_body["continueOnFailure"] = True
        payload = self._request_json(
            "POST",
            "/submit",
            request_body,
        )
        try:
            return LightCPVerifierSubmitResult(sid=int(payload["sid"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(
                f"Unexpected LightCPVerifier submit response: {payload}"
            ) from exc

    def get_result(self, sid: int, short: bool = False) -> LightCPVerifierResult:
        query = "?short=1" if short else ""
        try:
            payload = self._request_json("GET", f"/result/{sid}{query}")
            return LightCPVerifierResult(status_code=200, payload=payload)
        except LightCPVerifierHTTPError as exc:
            if exc.status_code == 404:
                return LightCPVerifierResult(
                    status_code=404, payload={"status": "Judging"}
                )
            raise

    def custom_test(
        self,
        language: str,
        code: str,
        stdin: str,
        time_limit_ms: int = 2000,
        memory_limit_mb: int = 256,
        max_output_bytes: int = 1024 * 1024,
        execution_category: str = EXECUTION_CATEGORY_UNSPECIFIED,
        argv: Sequence[str] | None = None,
        copy_in_files: Mapping[str, str] | None = None,
        compile_copy_in_files: Mapping[str, str] | None = None,
        compile_only: bool = False,
        source_name: str | None = None,
    ) -> CustomTestResult:
        requested_time_limit_ms = time_limit_ms
        requested_memory_limit_mb = memory_limit_mb
        requested_max_output_bytes = max_output_bytes
        time_limit_ms, memory_limit_mb = _custom_test_execution_limits(
            requested_time_limit_ms,
            requested_memory_limit_mb,
            strict_dynamic_limits=self.strict_dynamic_limits,
        )
        request_body: dict[str, Any] = {
            "lang": language,
            "code": code,
            "stdin": stdin,
            "timeLimitMs": time_limit_ms,
            "memoryLimitMb": memory_limit_mb,
            "maxOutputBytes": max_output_bytes,
        }
        if argv:
            request_body["argv"] = list(argv)
        if copy_in_files:
            request_body["copyInFiles"] = dict(copy_in_files)
        if compile_copy_in_files is not None:
            request_body["compileCopyInFiles"] = dict(compile_copy_in_files)
        if compile_only:
            request_body["compileOnly"] = True
        if source_name:
            request_body["sourceName"] = source_name
        payload = self._request_json("POST", "/custom-test", request_body)
        candidate_limits = _response_limits(
            payload,
            "candidate",
            time_limit_ms=time_limit_ms,
            memory_limit_mb=memory_limit_mb,
            max_output_bytes=max_output_bytes,
        )
        if self.strict_dynamic_limits:
            _require_matching_dynamic_limits(
                payload,
                "candidate",
                candidate_limits,
                requested_time_limit_ms=requested_time_limit_ms,
                requested_memory_limit_mb=requested_memory_limit_mb,
            )
        compilation_limits = _response_compilation_limits(
            payload, "candidateCompilation"
        )
        return CustomTestResult(
            status=str(payload.get("status", "unknown")),
            ok=bool(payload.get("ok", False)),
            stdout=str(payload.get("stdout", "")),
            stderr=str(payload.get("stderr", "")),
            exit_status=_optional_int(payload.get("exitStatus")),
            signal=None
            if payload.get("signal") is None
            else str(payload.get("signal")),
            time_ms=int(payload.get("timeMs", 0)),
            memory_bytes=int(payload.get("memoryBytes", 0)),
            payload=payload,
            execution_category=execution_category,
            time_limit_ms=candidate_limits[0],
            memory_limit_mb=candidate_limits[2],
            max_output_bytes=candidate_limits[3],
            requested_time_limit_ms=requested_time_limit_ms,
            requested_memory_limit_mb=requested_memory_limit_mb,
            execution_backend=EXECUTION_BACKEND_LIGHTCPVERIFIER,
            execution_backend_deprecated=False,
            wall_time_limit_ms=candidate_limits[1],
            requested_max_output_bytes=requested_max_output_bytes,
            compilation_cpu_time_limit_ms=compilation_limits[0],
            compilation_memory_limit_mb=compilation_limits[1],
            compilation_process_limit=compilation_limits[2],
        )

    def custom_test_batch(
        self,
        language: str,
        code: str,
        tests: Sequence[str | CustomTestBatchCase],
        time_limit_ms: int = 2000,
        memory_limit_mb: int = 256,
        max_output_bytes: int = 1024 * 1024,
        execution_category: str = EXECUTION_CATEGORY_UNSPECIFIED,
        argv: Sequence[str] | None = None,
        copy_in_files: Mapping[str, str] | None = None,
        compile_copy_in_files: Mapping[str, str] | None = None,
        source_name: str | None = None,
        validator: CustomTestValidatorSpec | None = None,
    ) -> CustomTestBatchResult:
        """Run one compiled program against a batch of independently judged inputs.

        A string in ``tests`` is shorthand for ``CustomTestBatchCase(stdin=...)``.
        When a validator is supplied, invalid inputs are returned with
        ``status == "invalid_test_data"`` and are not passed to the main program.
        Validator infrastructure failures use ``status == "validator_error"``.
        """

        requested_time_limit_ms = time_limit_ms
        requested_memory_limit_mb = memory_limit_mb
        requested_max_output_bytes = max_output_bytes
        time_limit_ms, memory_limit_mb = _custom_test_execution_limits(
            requested_time_limit_ms,
            requested_memory_limit_mb,
            strict_dynamic_limits=self.strict_dynamic_limits,
        )

        request_tests = [_batch_case_request(test) for test in tests]
        request_body: dict[str, Any] = {
            "lang": language,
            "code": code,
            "tests": request_tests,
            "timeLimitMs": time_limit_ms,
            "memoryLimitMb": memory_limit_mb,
            "maxOutputBytes": max_output_bytes,
        }
        if argv is not None:
            request_body["argv"] = list(argv)
        if copy_in_files is not None:
            request_body["copyInFiles"] = dict(copy_in_files)
        if compile_copy_in_files is not None:
            request_body["compileCopyInFiles"] = dict(compile_copy_in_files)
        if source_name:
            request_body["sourceName"] = source_name

        validator_time_limit_ms = time_limit_ms
        validator_memory_limit_mb = memory_limit_mb
        validator_max_output_bytes = max_output_bytes
        requested_validator_time_limit_ms: int | None = None
        requested_validator_memory_limit_mb: int | None = None
        requested_validator_max_output_bytes: int | None = None
        if validator is not None:
            requested_validator_time_limit_ms = (
                requested_time_limit_ms
                if validator.time_limit_ms is None
                else validator.time_limit_ms
            )
            requested_validator_memory_limit_mb = (
                requested_memory_limit_mb
                if validator.memory_limit_mb is None
                else validator.memory_limit_mb
            )
            requested_validator_max_output_bytes = (
                requested_max_output_bytes
                if validator.max_output_bytes is None
                else validator.max_output_bytes
            )
            validator_time_limit_ms, validator_memory_limit_mb = (
                _custom_test_execution_limits(
                    requested_validator_time_limit_ms,
                    requested_validator_memory_limit_mb,
                    strict_dynamic_limits=self.strict_dynamic_limits,
                )
            )
            validator_body: dict[str, Any] = {"code": validator.code}
            if validator.language is not None:
                validator_body["lang"] = validator.language
            if validator.source_name:
                validator_body["sourceName"] = validator.source_name
            if validator.argv is not None:
                validator_body["argv"] = list(validator.argv)
            if validator.compile_copy_in_files is not None:
                validator_body["compileCopyInFiles"] = dict(
                    validator.compile_copy_in_files
                )
            if validator.time_limit_ms is not None:
                validator_body["timeLimitMs"] = validator_time_limit_ms
            if validator.memory_limit_mb is not None:
                validator_body["memoryLimitMb"] = validator_memory_limit_mb
            if validator.max_output_bytes is not None:
                validator_max_output_bytes = validator.max_output_bytes
                validator_body["maxOutputBytes"] = validator_max_output_bytes
            request_body["validator"] = validator_body

        timeout_sec = _batch_request_timeout_sec(
            default_timeout_sec=self.timeout_sec,
            test_count=len(request_tests),
            time_limit_ms=time_limit_ms,
            validator_time_limit_ms=(
                validator_time_limit_ms if validator is not None else None
            ),
        )
        payload = self._request_json(
            "POST",
            "/custom-test/batch",
            request_body,
            timeout_sec=timeout_sec,
        )
        candidate_limits = _response_limits(
            payload,
            "candidate",
            time_limit_ms=time_limit_ms,
            memory_limit_mb=memory_limit_mb,
            max_output_bytes=max_output_bytes,
        )
        if self.strict_dynamic_limits:
            _require_matching_dynamic_limits(
                payload,
                "candidate",
                candidate_limits,
                requested_time_limit_ms=requested_time_limit_ms,
                requested_memory_limit_mb=requested_memory_limit_mb,
            )
        parsed_validator_limits = (
            None
            if validator is None
            else _response_limits(
                payload,
                "validator",
                time_limit_ms=validator_time_limit_ms,
                memory_limit_mb=validator_memory_limit_mb,
                max_output_bytes=validator_max_output_bytes,
            )
        )
        if self.strict_dynamic_limits and parsed_validator_limits is not None:
            _require_matching_dynamic_limits(
                payload,
                "validator",
                parsed_validator_limits,
                requested_time_limit_ms=requested_validator_time_limit_ms,
                requested_memory_limit_mb=requested_validator_memory_limit_mb,
            )
        compilation_limits = _response_compilation_limits(
            payload, "candidateCompilation"
        )
        validator_compilation_limits = _response_compilation_limits(
            payload, "validatorCompilation"
        )
        result_items = payload.get("results")
        if not isinstance(result_items, list):
            raise RuntimeError(
                f"Unexpected LightCPVerifier batch response: {payload}"
            )
        results = tuple(_batch_case_result(item) for item in result_items)
        if len(results) != len(request_tests):
            raise RuntimeError(
                "Unexpected LightCPVerifier batch response: "
                f"expected {len(request_tests)} results, got {len(results)}"
            )
        for expected_index, (request_test, result) in enumerate(
            zip(request_tests, results)
        ):
            if result.index != expected_index:
                raise RuntimeError(
                    "Unexpected LightCPVerifier batch response order: "
                    f"expected index {expected_index}, got {result.index}"
                )
            if result.id != request_test.get("id"):
                raise RuntimeError(
                    "Unexpected LightCPVerifier batch response id at index "
                    f"{expected_index}: expected {request_test.get('id')!r}, "
                    f"got {result.id!r}"
                )
        return CustomTestBatchResult(
            status=str(payload.get("status", "unknown")),
            ok=bool(payload.get("ok", False)),
            total=int(payload.get("total", len(results))),
            valid=int(payload.get("valid", 0)),
            invalid=int(payload.get("invalid", 0)),
            validator_errors=int(payload.get("validatorErrors", 0)),
            output_truncated=bool(payload.get("outputTruncated", False)),
            captured_output_bytes=int(payload.get("capturedOutputBytes", 0)),
            max_batch_output_bytes=int(payload.get("maxBatchOutputBytes", 0)),
            compile_error=_optional_string(payload.get("compileError")),
            validator_compile_error=_optional_string(
                payload.get("validatorCompileError")
            ),
            results=results,
            payload=payload,
            time_limit_ms=candidate_limits[0],
            memory_limit_mb=candidate_limits[2],
            max_output_bytes=candidate_limits[3],
            requested_time_limit_ms=requested_time_limit_ms,
            requested_memory_limit_mb=requested_memory_limit_mb,
            execution_category=execution_category,
            execution_backend=EXECUTION_BACKEND_LIGHTCPVERIFIER,
            execution_backend_deprecated=False,
            wall_time_limit_ms=candidate_limits[1],
            requested_max_output_bytes=requested_max_output_bytes,
            validator_time_limit_ms=(
                None if parsed_validator_limits is None else parsed_validator_limits[0]
            ),
            validator_wall_time_limit_ms=(
                None if parsed_validator_limits is None else parsed_validator_limits[1]
            ),
            validator_memory_limit_mb=(
                None if parsed_validator_limits is None else parsed_validator_limits[2]
            ),
            validator_max_output_bytes=(
                None if parsed_validator_limits is None else parsed_validator_limits[3]
            ),
            requested_validator_time_limit_ms=requested_validator_time_limit_ms,
            requested_validator_memory_limit_mb=requested_validator_memory_limit_mb,
            requested_validator_max_output_bytes=requested_validator_max_output_bytes,
            compilation_cpu_time_limit_ms=compilation_limits[0],
            compilation_memory_limit_mb=compilation_limits[1],
            compilation_process_limit=compilation_limits[2],
            validator_compilation_cpu_time_limit_ms=(
                validator_compilation_limits[0]
            ),
            validator_compilation_memory_limit_mb=(
                validator_compilation_limits[1]
            ),
            validator_compilation_process_limit=(
                validator_compilation_limits[2]
            ),
        )

    def _request_json(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        timeout_sec: float | None = None,
    ) -> dict[str, Any]:
        url = self.base_url + path
        data = None
        headers = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(
                request,
                timeout=self.timeout_sec if timeout_sec is None else timeout_sec,
            ) as response:
                raw = response.read().decode("utf-8")
                if not raw:
                    return {}
                parsed = json.loads(raw)
                if not isinstance(parsed, dict):
                    raise RuntimeError(
                        f"Expected JSON object from LightCPVerifier, got: {parsed!r}"
                    )
                return parsed
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            payload: dict[str, Any]
            try:
                parsed = json.loads(raw)
                payload = parsed if isinstance(parsed, dict) else {"message": raw}
            except json.JSONDecodeError:
                payload = {"message": raw}
            raise LightCPVerifierHTTPError(exc.code, payload) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"LightCPVerifier request failed: {exc.reason}") from exc


class LightCPVerifierHTTPError(RuntimeError):
    def __init__(self, status_code: int, payload: dict[str, Any]):
        self.status_code = status_code
        self.payload = payload
        super().__init__(f"LightCPVerifier HTTP {status_code}: {payload}")


def _custom_test_execution_limits(
    requested_time_limit_ms: int,
    requested_memory_limit_mb: int,
    *,
    strict_dynamic_limits: bool,
) -> tuple[int, int]:
    if not strict_dynamic_limits:
        return (
            execution_time_limit_ms(requested_time_limit_ms),
            execution_memory_limit_mb(requested_memory_limit_mb),
        )
    _strict_integer_limit(
        requested_time_limit_ms,
        "time_limit_ms",
        *_STRICT_DYNAMIC_TIME_LIMIT_MS,
    )
    _strict_integer_limit(
        requested_memory_limit_mb,
        "memory_limit_mb",
        *_STRICT_DYNAMIC_MEMORY_LIMIT_MB,
    )
    return requested_time_limit_ms, requested_memory_limit_mb


def _strict_integer_limit(value: int, name: str, minimum: int, maximum: int) -> None:
    if type(value) is not int:
        raise TypeError(f"{name} must be an integer")
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")


def _require_matching_dynamic_limits(
    payload: Mapping[str, Any],
    owner: str,
    effective: tuple[int, int, int, int],
    *,
    requested_time_limit_ms: int | None,
    requested_memory_limit_mb: int | None,
) -> None:
    limits = payload.get("limits")
    selected = limits.get(owner) if isinstance(limits, Mapping) else None
    if not isinstance(selected, Mapping):
        raise RuntimeError(
            "strict dynamic limits require an explicit LightCPVerifier "
            f"response limits.{owner} object"
        )
    if selected.get("cpuTimeMs") != requested_time_limit_ms:
        raise RuntimeError(
            f"LightCPVerifier effective {owner} time limit differs from the "
            f"request: requested {requested_time_limit_ms}, got {effective[0]}"
        )
    if selected.get("memoryMb") != requested_memory_limit_mb:
        raise RuntimeError(
            f"LightCPVerifier effective {owner} memory limit differs from the "
            f"request: requested {requested_memory_limit_mb}, got {effective[2]}"
        )


def _response_limits(
    payload: Mapping[str, Any],
    owner: str,
    *,
    time_limit_ms: int,
    memory_limit_mb: int,
    max_output_bytes: int,
) -> tuple[int, int, int, int]:
    """Read server-confirmed CPU/wall/memory/output limits.

    Older LightCPVerifier responses did not carry ``limits``.  The fallback is
    retained for the generic client, while ver3 production consumers reject an
    old service through the health API revision check.
    """

    limits = payload.get("limits")
    if limits is None:
        return (
            time_limit_ms,
            time_limit_ms * 2,
            memory_limit_mb,
            max_output_bytes,
        )
    if not isinstance(limits, Mapping):
        raise RuntimeError("LightCPVerifier response limits must be an object")
    selected = limits.get(owner)
    if not isinstance(selected, Mapping):
        raise RuntimeError(
            f"LightCPVerifier response limits.{owner} must be an object"
        )
    names_and_fallbacks = (
        ("cpuTimeMs", time_limit_ms),
        ("wallTimeMs", time_limit_ms * 2),
        ("memoryMb", memory_limit_mb),
        ("maxOutputBytes", max_output_bytes),
    )
    parsed: list[int] = []
    for name, fallback in names_and_fallbacks:
        value = selected.get(name, fallback)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise RuntimeError(
                f"LightCPVerifier response limits.{owner}.{name} "
                "must be a positive integer"
            )
        parsed.append(value)
    return parsed[0], parsed[1], parsed[2], parsed[3]


def _response_compilation_limits(
    payload: Mapping[str, Any], owner: str
) -> tuple[int | None, int | None, int | None]:
    limits = payload.get("limits")
    if limits is None:
        return None, None, None
    if not isinstance(limits, Mapping):
        raise RuntimeError("LightCPVerifier response limits must be an object")
    selected = limits.get(owner)
    if selected is None:
        return None, None, None
    if not isinstance(selected, Mapping):
        raise RuntimeError(
            f"LightCPVerifier response limits.{owner} must be an object or null"
        )
    parsed: list[int] = []
    for name in ("cpuTimeMs", "memoryMb", "processLimit"):
        value = selected.get(name)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise RuntimeError(
                f"LightCPVerifier response limits.{owner}.{name} "
                "must be a positive integer"
            )
        parsed.append(value)
    return parsed[0], parsed[1], parsed[2]


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _batch_case_request(test: str | CustomTestBatchCase) -> dict[str, Any]:
    if isinstance(test, str):
        return {"stdin": test}
    if not isinstance(test, CustomTestBatchCase):
        raise TypeError(
            "batch tests must be strings or CustomTestBatchCase instances"
        )
    request: dict[str, Any] = {"stdin": test.stdin}
    if test.id is not None:
        request["id"] = _batch_id(test.id, "batch test id")
    if test.argv is not None:
        request["argv"] = list(test.argv)
    if test.copy_in_files is not None:
        request["copyInFiles"] = dict(test.copy_in_files)
    return request


def _validation_result(payload: Any) -> CustomTestValidationResult:
    if not isinstance(payload, dict):
        raise RuntimeError(
            f"Unexpected LightCPVerifier batch validation response: {payload!r}"
        )
    return CustomTestValidationResult(
        status=str(payload.get("status", "unknown")),
        ok=bool(payload.get("ok", False)),
        stdout=str(payload.get("stdout", "")),
        stderr=str(payload.get("stderr", "")),
        exit_status=_optional_int(payload.get("exitStatus")),
        signal=_optional_string(payload.get("signal")),
        time_ns=int(payload.get("timeNs", 0)),
        time_ms=int(payload.get("timeMs", 0)),
        memory_bytes=int(payload.get("memoryBytes", 0)),
        raw_status=_optional_string(payload.get("rawStatus")),
        cached=bool(payload.get("cached", False)),
        output_truncated=bool(payload.get("outputTruncated", False)),
        payload=payload,
    )


def _batch_case_result(payload: Any) -> CustomTestBatchCaseResult:
    if not isinstance(payload, dict):
        raise RuntimeError(
            f"Unexpected LightCPVerifier batch case response: {payload!r}"
        )
    return CustomTestBatchCaseResult(
        index=int(payload.get("index", 0)),
        id=_batch_id(payload.get("id"), "batch response id"),
        executed=bool(payload.get("executed", False)),
        status=str(payload.get("status", "unknown")),
        ok=bool(payload.get("ok", False)),
        stdout=str(payload.get("stdout", "")),
        stderr=str(payload.get("stderr", "")),
        exit_status=_optional_int(payload.get("exitStatus")),
        signal=_optional_string(payload.get("signal")),
        time_ns=int(payload.get("timeNs", 0)),
        time_ms=int(payload.get("timeMs", 0)),
        memory_bytes=int(payload.get("memoryBytes", 0)),
        raw_status=_optional_string(payload.get("rawStatus")),
        cached=bool(payload.get("cached", False)),
        output_truncated=bool(payload.get("outputTruncated", False)),
        validation=_validation_result(payload.get("validation")),
        payload=payload,
    )


def _batch_request_timeout_sec(
    *,
    default_timeout_sec: float,
    test_count: int,
    time_limit_ms: int,
    validator_time_limit_ms: int | None,
) -> float:
    # go-judge's wall-clock limit is twice the CPU limit used by the endpoint.
    per_case_ms = time_limit_ms
    if validator_time_limit_ms is not None:
        per_case_ms += validator_time_limit_ms
    estimate = _BATCH_REQUEST_OVERHEAD_SEC + test_count * per_case_ms * 2 / 1000
    return max(
        float(default_timeout_sec),
        min(_BATCH_REQUEST_TIMEOUT_CAP_SEC, estimate),
    )


def _batch_id(
    value: Any,
    field_name: str,
) -> str | int | float | None:
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field_name} must be a string or finite number")
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise ValueError(f"{field_name} must be a string or finite number")
    if (
        isinstance(value, int)
        or (isinstance(value, float) and value.is_integer())
    ) and abs(value) > _JS_MAX_SAFE_INTEGER:
        raise ValueError(
            f"{field_name} integer must be within JavaScript's safe range; "
            "use a string for larger identifiers"
        )
    return value
