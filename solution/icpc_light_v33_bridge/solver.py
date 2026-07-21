"""Translate UOJ-Bench typed tasks to a standalone ICPC Light job bridge.

The bridge is intentionally a separate process.  The UOJ-Bench runner never
receives Docker access, model credentials, or a complete skill workspace, and
the skills process never receives the benchmark dataset or the UOJ API key.
"""

from __future__ import annotations

from dataclasses import dataclass
import copy
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import threading
import time
from typing import Any, Mapping, Optional

from solution.api import (
    FaultCoverageInput,
    FaultExposureInput,
    GenerationInput,
    HackCandidate,
    HackingInput,
    SolutionCandidate,
    SolverCapabilities,
    SolverFeedback,
    SolverTurn,
    TestCaseCandidate,
    TestCaseFormat,
)


ICPC_LIGHT_MODEL = "gpt-5.6-sol"
ICPC_LIGHT_REASONING_EFFORT = "ultra"
BRIDGE_ENV = "ICPC_LIGHT_UOJ_BRIDGE"
BRIDGE_CONFIG_ENV = "ICPC_LIGHT_UOJ_BRIDGE_CONFIG"
BRIDGE_TIMEOUT_ENV = "ICPC_LIGHT_UOJ_BRIDGE_TIMEOUT_SECONDS"
DEFAULT_BRIDGE_TIMEOUT_SECONDS = 6 * 60 * 60 + 5 * 60
MAX_BRIDGE_RESPONSE_BYTES = 16 * 1024 * 1024
MAX_BRIDGE_STDERR_BYTES = 1024 * 1024
MAX_CANDIDATE_BYTES = 4 * 1024 * 1024
PUBLIC_METADATA_FIELDS = frozenset(
    {
        "difficulty",
        "difficulty-source",
        "display_problem_id",
        "hack_id",
        "hackable",
        "language",
        "memory_limit_mb",
        "problem_id",
        "submission_id",
        "title_en",
        "title_zh",
        "time_limit_ms",
        "wrong_id",
    }
)
_BINDING_LOCK = threading.Lock()
_FROZEN_CONFIG_BINDING: tuple[str, str] | None = None
_FROZEN_PIPELINE_SIGNATURE: str | None = None


class BridgeProtocolError(RuntimeError):
    """The isolated job bridge failed or returned an invalid response."""


def _stop_bridge_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
    else:
        process.terminate()
    try:
        process.wait(timeout=2)
        return
    except subprocess.TimeoutExpired:
        pass
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
    else:
        process.kill()
    process.wait(timeout=2)


def _run_bridge_process(
    bridge: Path,
    encoded: bytes,
    environment: Mapping[str, str],
    timeout: float,
) -> tuple[int, bytes, bytes]:
    """Run the bridge with bounded pipe readers and process-group cleanup."""

    process = subprocess.Popen(
        [str(bridge)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=bridge.parent,
        env=dict(environment),
        start_new_session=(os.name == "posix"),
    )
    assert process.stdin is not None
    assert process.stdout is not None
    assert process.stderr is not None
    overflow = threading.Event()
    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []

    def read_bounded(
        stream: Any, chunks: list[bytes], maximum: int
    ) -> None:
        total = 0
        try:
            while True:
                chunk = stream.read(65536)
                if not chunk:
                    return
                total += len(chunk)
                if total > maximum:
                    overflow.set()
                    return
                chunks.append(chunk)
        finally:
            stream.close()

    def write_request() -> None:
        try:
            process.stdin.write(encoded)
            process.stdin.flush()
        except (BrokenPipeError, OSError):
            pass
        finally:
            process.stdin.close()

    readers = (
        threading.Thread(
            target=read_bounded,
            args=(process.stdout, stdout_chunks, MAX_BRIDGE_RESPONSE_BYTES),
            daemon=True,
        ),
        threading.Thread(
            target=read_bounded,
            args=(process.stderr, stderr_chunks, MAX_BRIDGE_STDERR_BYTES),
            daemon=True,
        ),
    )
    writer = threading.Thread(target=write_request, daemon=True)
    for thread in (*readers, writer):
        thread.start()
    deadline = time.monotonic() + timeout
    timed_out = False
    try:
        while process.poll() is None:
            if overflow.wait(timeout=0.05):
                break
            if time.monotonic() >= deadline:
                timed_out = True
                break
        if overflow.is_set() or timed_out:
            _stop_bridge_process(process)
        else:
            process.wait()
    finally:
        if process.poll() is None:
            _stop_bridge_process(process)
        for stream in (process.stdin, process.stdout, process.stderr):
            try:
                stream.close()
            except OSError:
                pass
        for thread in (*readers, writer):
            thread.join(timeout=2)
    if timed_out:
        raise BridgeProtocolError("bridge execution timed out and was terminated")
    if overflow.is_set():
        raise BridgeProtocolError("bridge stdout/stderr exceeded its bounded capture")
    return int(process.returncode or 0), b"".join(stdout_chunks), b"".join(stderr_chunks)


def _public_metadata(value: Mapping[str, Any]) -> dict[str, Any]:
    scalar = (str, int, float, bool, type(None))
    return {
        key: item
        for key, item in value.items()
        if key in PUBLIC_METADATA_FIELDS and isinstance(item, scalar)
    }


def _timeout_seconds() -> float:
    raw = os.environ.get(BRIDGE_TIMEOUT_ENV)
    if raw is None:
        return float(DEFAULT_BRIDGE_TIMEOUT_SECONDS)
    try:
        value = float(raw)
    except ValueError as exc:
        raise BridgeProtocolError(f"{BRIDGE_TIMEOUT_ENV} must be numeric") from exc
    if not 1 <= value <= DEFAULT_BRIDGE_TIMEOUT_SECONDS:
        raise BridgeProtocolError(
            f"{BRIDGE_TIMEOUT_ENV} must be in [1, {DEFAULT_BRIDGE_TIMEOUT_SECONDS}]"
        )
    return value


def _read_config_binding() -> tuple[tuple[str, str], Mapping[str, Any]]:
    raw = os.environ.get(BRIDGE_CONFIG_ENV)
    if not raw:
        raise BridgeProtocolError(f"{BRIDGE_CONFIG_ENV} is required")
    path = Path(raw)
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise BridgeProtocolError(
            f"{BRIDGE_CONFIG_ENV} must name an absolute regular file"
        )
    metadata = path.stat()
    if metadata.st_nlink != 1:
        raise BridgeProtocolError("bridge config must not be hard-linked")
    if metadata.st_size > 1024 * 1024:
        raise BridgeProtocolError("bridge config exceeds 1 MiB")
    data = path.read_bytes()
    if len(data) > 1024 * 1024:
        raise BridgeProtocolError("bridge config exceeds 1 MiB")
    try:
        value = json.loads(data.decode("utf-8"))
        canonical = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise BridgeProtocolError("bridge config is not canonicalizable UTF-8 JSON") from exc
    if not isinstance(value, Mapping):
        raise BridgeProtocolError("bridge config must be a JSON object")
    digest = hashlib.sha256(canonical).hexdigest()
    binding = (str(path.resolve(strict=True)), digest)
    return binding, value


def _freeze_config_binding() -> tuple[str, str]:
    binding, _ = _read_config_binding()
    global _FROZEN_CONFIG_BINDING
    with _BINDING_LOCK:
        if _FROZEN_CONFIG_BINDING is None:
            _FROZEN_CONFIG_BINDING = binding
        elif _FROZEN_CONFIG_BINDING != binding:
            raise BridgeProtocolError("bridge config changed within this benchmark process")
    return binding


def _current_frozen_config(
    expected: tuple[str, str]
) -> tuple[str, Mapping[str, Any]]:
    binding, value = _read_config_binding()
    if binding != expected:
        raise BridgeProtocolError("bridge config changed after solver construction")
    return binding[0], value


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _bind_pipeline_identity(
    identity: Mapping[str, Any], config_sha256: str, request_sha256: str
) -> None:
    required_fields = {
        "schema_version",
        "profile",
        "model",
        "reasoning_effort",
        "request_sha256",
        "surface_sha256",
        "skill_bundle_sha256",
        "expected_skill_bundle_sha256",
        "copied_skills_sha256",
        "skill_files",
        "skill_bytes",
        "bridge_config_sha256",
        "agent_command",
        "candidate_sha256",
        "pipeline_signature_sha256",
    }
    if set(identity) != required_fields:
        raise BridgeProtocolError("bridge response pipeline identity fields are invalid")
    required_hashes = (
        "request_sha256",
        "surface_sha256",
        "skill_bundle_sha256",
        "expected_skill_bundle_sha256",
        "copied_skills_sha256",
        "bridge_config_sha256",
        "candidate_sha256",
        "pipeline_signature_sha256",
    )
    if identity.get("schema_version") != 1:
        raise BridgeProtocolError("bridge response has an invalid pipeline identity schema")
    if identity.get("model") != ICPC_LIGHT_MODEL:
        raise BridgeProtocolError("bridge response changed the frozen model identity")
    if identity.get("reasoning_effort") != ICPC_LIGHT_REASONING_EFFORT:
        raise BridgeProtocolError("bridge response changed the frozen reasoning effort")
    if not isinstance(identity.get("profile"), str) or not identity.get("profile"):
        raise BridgeProtocolError("bridge response has an invalid pipeline profile")
    if any(not _is_sha256(identity.get(name)) for name in required_hashes):
        raise BridgeProtocolError("bridge response has an invalid pipeline identity hash")
    if identity.get("bridge_config_sha256") != config_sha256:
        raise BridgeProtocolError("bridge response does not match the frozen config")
    if identity.get("request_sha256") != request_sha256:
        raise BridgeProtocolError("bridge response does not match the current request")
    if identity.get("skill_bundle_sha256") != identity.get(
        "expected_skill_bundle_sha256"
    ):
        raise BridgeProtocolError("bridge response reports a drifting skill bundle")
    if not isinstance(identity.get("skill_files"), int) or isinstance(
        identity.get("skill_files"), bool
    ) or identity.get("skill_files", 0) < 1:
        raise BridgeProtocolError("bridge response has an invalid skill file count")
    if not isinstance(identity.get("skill_bytes"), int) or isinstance(
        identity.get("skill_bytes"), bool
    ) or identity.get("skill_bytes", 0) < 1:
        raise BridgeProtocolError("bridge response has an invalid skill byte count")
    command = identity.get("agent_command")
    if not isinstance(command, list) or not command:
        raise BridgeProtocolError("bridge response has an invalid agent identity")
    for index, item in enumerate(command):
        if not isinstance(item, Mapping) or set(item) not in (
            {"index", "value"},
            {"index", "value", "sha256"},
        ):
            raise BridgeProtocolError("bridge response has an invalid agent identity item")
        if item.get("index") != index or not isinstance(item.get("value"), str) or not item.get(
            "value"
        ):
            raise BridgeProtocolError("bridge response has an invalid agent identity item")
        if "sha256" in item and not _is_sha256(item.get("sha256")):
            raise BridgeProtocolError("bridge response has an invalid agent command hash")
    signature_value = {
        key: identity.get(key)
        for key in (
            "profile",
            "model",
            "reasoning_effort",
            "skill_bundle_sha256",
            "expected_skill_bundle_sha256",
            "copied_skills_sha256",
            "bridge_config_sha256",
            "agent_command",
        )
    }
    signature = hashlib.sha256(
        json.dumps(
            signature_value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    if identity.get("pipeline_signature_sha256") != signature:
        raise BridgeProtocolError("bridge response has an invalid pipeline signature")
    global _FROZEN_PIPELINE_SIGNATURE
    with _BINDING_LOCK:
        if _FROZEN_PIPELINE_SIGNATURE is None:
            _FROZEN_PIPELINE_SIGNATURE = signature
        elif _FROZEN_PIPELINE_SIGNATURE != signature:
            raise BridgeProtocolError("pipeline identity changed within this benchmark process")


def _effective_bridge_timeout_seconds(config: Mapping[str, Any]) -> float:
    outer = _timeout_seconds()
    inner = config.get("timeout_seconds")
    if isinstance(inner, (int, float)) and not isinstance(inner, bool):
        if outer < float(inner) + 30:
            raise BridgeProtocolError(
                f"{BRIDGE_TIMEOUT_ENV} must exceed the agent timeout by at least 30 seconds"
            )
    return outer


def _bridge_path() -> Path:
    raw = os.environ.get(BRIDGE_ENV)
    if not raw:
        raise BridgeProtocolError(f"{BRIDGE_ENV} is required")
    path = Path(raw)
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise BridgeProtocolError(f"{BRIDGE_ENV} must name an absolute regular file")
    if not os.access(path, os.X_OK):
        raise BridgeProtocolError(f"{BRIDGE_ENV} is not executable")
    return path.resolve(strict=True)


def _candidate_text(raw: Any, label: str) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise BridgeProtocolError(f"bridge {label} is missing or empty")
    if len(raw.encode("utf-8")) > MAX_CANDIDATE_BYTES:
        raise BridgeProtocolError(f"bridge {label} exceeds {MAX_CANDIDATE_BYTES} bytes")
    return raw


def _python_generator_for_raw_input(raw_input: str) -> str:
    return "import sys\nsys.stdout.write(" + repr(raw_input) + ")\n"


def _bridge_environment(config_path: str) -> dict[str, str]:
    """Build a small non-secret environment for the control-plane bridge."""

    environment = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    for name in ("LANG", "LC_ALL", "TZ"):
        value = os.environ.get(name)
        if value:
            environment[name] = value
    environment[BRIDGE_CONFIG_ENV] = config_path
    return environment


@dataclass
class _BridgeSession:
    task: str
    request: dict[str, Any]
    expected_config_binding: tuple[str, str]
    _started: bool = False
    _transcript: list[dict[str, Any]] | None = None

    @property
    def initial_request(self) -> dict[str, Any]:
        return copy.deepcopy(self.request)

    @property
    def transcript(self) -> list[dict[str, Any]]:
        return copy.deepcopy(self._transcript or [])

    def record_feedback(self, feedback: SolverFeedback) -> None:
        raise ValueError("icpc_light_v33_bridge sessions are one-shot")

    def next(self, feedback: Optional[SolverFeedback] = None) -> SolverTurn[Any]:
        if feedback is not None:
            raise ValueError("icpc_light_v33_bridge sessions do not accept feedback")
        if self._started:
            raise ValueError("icpc_light_v33_bridge session already produced its turn")
        self._started = True

        encoded = json.dumps(
            self.request,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        request_sha256 = hashlib.sha256(encoded).hexdigest()
        config_path, config = _current_frozen_config(self.expected_config_binding)
        bridge = _bridge_path()
        bridge_environment = _bridge_environment(config_path)
        bridge_timeout = _effective_bridge_timeout_seconds(config)
        try:
            returncode, stdout, stderr = _run_bridge_process(
                bridge,
                encoded,
                bridge_environment,
                bridge_timeout,
            )
        except OSError as exc:
            raise BridgeProtocolError(
                f"bridge execution failed: {type(exc).__name__}: {exc}"
            ) from exc

        if returncode != 0:
            diagnostic = stderr.decode("utf-8", errors="replace")[-2000:]
            raise BridgeProtocolError(
                f"bridge exited {returncode}"
                + (f": {diagnostic}" if diagnostic else "")
            )
        if len(stdout) > MAX_BRIDGE_RESPONSE_BYTES:
            raise BridgeProtocolError("bridge response exceeds 16 MiB")
        try:
            response = json.loads(stdout.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise BridgeProtocolError("bridge response is not one UTF-8 JSON object") from exc
        if not isinstance(response, dict) or response.get("schema_version") != 1:
            raise BridgeProtocolError("bridge response has an unsupported schema")
        if response.get("status") != "completed":
            error = response.get("error")
            raise BridgeProtocolError(f"bridge did not complete: {error!r}")
        identity = response.get("pipeline_identity")
        if not isinstance(identity, Mapping):
            raise BridgeProtocolError("bridge response omitted pipeline_identity")
        if set(response) != {
            "schema_version",
            "status",
            "candidate",
            "raw_text",
            "message",
            "transcript",
            "usage",
            "pipeline_identity",
        }:
            raise BridgeProtocolError("completed bridge response fields are invalid")
        _bind_pipeline_identity(
            identity, self.expected_config_binding[1], request_sha256
        )

        transcript = response.get("transcript")
        usage = response.get("usage")
        message = response.get("message")
        raw_text = response.get("raw_text")
        if not isinstance(transcript, list):
            raise BridgeProtocolError("bridge transcript must be an array")
        if not isinstance(usage, Mapping):
            raise BridgeProtocolError("bridge usage must be an object")
        if not isinstance(message, Mapping):
            raise BridgeProtocolError("bridge message must be an object")
        if set(message) != {
            "role",
            "content",
            "pipeline_identity",
            "receipt_sha256",
        }:
            raise BridgeProtocolError("bridge message fields are invalid")
        if message.get("role") != "assistant" or not isinstance(
            message.get("content"), str
        ):
            raise BridgeProtocolError("bridge message role/content is invalid")
        if message.get("pipeline_identity") != identity:
            raise BridgeProtocolError("bridge message changed the pipeline identity")
        if not _is_sha256(message.get("receipt_sha256")):
            raise BridgeProtocolError("bridge message receipt hash is invalid")
        if not isinstance(raw_text, str):
            raise BridgeProtocolError("bridge raw_text must be a string")
        self._transcript = copy.deepcopy(transcript)

        candidate_raw = response.get("candidate")
        if not isinstance(candidate_raw, Mapping):
            raise BridgeProtocolError("completed bridge candidate must be an object")

        if self.task == "generation":
            if set(candidate_raw) != {"kind", "content"}:
                raise BridgeProtocolError("generation candidate fields are invalid")
            if candidate_raw.get("kind") != "solution":
                raise BridgeProtocolError("generation bridge returned the wrong candidate kind")
            content = _candidate_text(candidate_raw.get("content"), "solution")
            candidate: Any = SolutionCandidate(content)
        elif self.task == "hacking":
            if set(candidate_raw) != {"kind", "format", "content"}:
                raise BridgeProtocolError("hacking candidate fields are invalid")
            if candidate_raw.get("kind") != "hack":
                raise BridgeProtocolError("hacking bridge returned the wrong candidate kind")
            content = _candidate_text(candidate_raw.get("content"), "hack")
            candidate_format = candidate_raw.get("format")
            if candidate_format == "raw_input":
                content = _python_generator_for_raw_input(content)
            elif candidate_format != "python_generator":
                raise BridgeProtocolError("hack candidate format is unsupported")
            candidate = HackCandidate(content)
        elif self.task in {"fault_coverage", "fault_exposure"}:
            label = self.task.replace("_", " ")
            if set(candidate_raw) != {"kind", "format", "content"}:
                raise BridgeProtocolError(f"{label} candidate fields are invalid")
            if candidate_raw.get("kind") != "test_case":
                raise BridgeProtocolError(
                    f"{label} bridge returned the wrong candidate kind"
                )
            content = _candidate_text(candidate_raw.get("content"), "test case")
            try:
                candidate_format = TestCaseFormat(candidate_raw.get("format"))
            except (TypeError, ValueError) as exc:
                raise BridgeProtocolError(
                    f"{label} candidate format is unsupported"
                ) from exc
            candidate = TestCaseCandidate(content, candidate_format)
        else:
            raise BridgeProtocolError(f"unsupported bridge task: {self.task}")

        if hashlib.sha256(
            _candidate_text(candidate_raw.get("content"), "candidate").encode("utf-8")
        ).hexdigest() != identity.get("candidate_sha256"):
            raise BridgeProtocolError("bridge candidate does not match its pipeline identity")

        return SolverTurn(
            candidate=candidate,
            raw_text=raw_text,
            message=copy.deepcopy(message),
            usage=copy.deepcopy(dict(usage)),
            error=None,
        )


class ICLightBridgeSolver:
    """One-shot public-only generation and adversarial-test adapter."""

    capabilities = SolverCapabilities(
        generation=True,
        hacking=True,
        repair=False,
        generation_feedback=False,
        hacking_feedback=False,
        repair_feedback=False,
        fault_coverage=True,
        fault_exposure=True,
    )

    def __init__(self, model: str):
        if model != ICPC_LIGHT_MODEL:
            raise ValueError(f"icpc_light_v33_bridge requires model {ICPC_LIGHT_MODEL!r}")
        self.model = model
        self._expected_config_binding = _freeze_config_binding()

    def start_generation(self, task: GenerationInput) -> _BridgeSession:
        return _BridgeSession(
            "generation",
            {
                "schema_version": 1,
                "task": "generation",
                "model": self.model,
                "reasoning_effort": ICPC_LIGHT_REASONING_EFFORT,
                "input": {
                    "problem_id": task.problem_id,
                    "problem_statement": task.problem_statement,
                    "language": task.language,
                    "chinese": task.chinese,
                    "metadata": _public_metadata(task.metadata),
                },
            },
            expected_config_binding=self._expected_config_binding,
        )

    def start_hacking(self, task: HackingInput) -> _BridgeSession:
        return _BridgeSession(
            "hacking",
            {
                "schema_version": 1,
                "task": "hacking",
                "model": self.model,
                "reasoning_effort": ICPC_LIGHT_REASONING_EFFORT,
                "input": {
                    "problem_id": task.problem_id,
                    "problem_statement": task.problem_statement,
                    "submission_code": task.submission_code,
                    "submission_language": task.submission_language,
                    "chinese": task.chinese,
                    "metadata": _public_metadata(task.metadata),
                },
            },
            expected_config_binding=self._expected_config_binding,
        )

    def start_repair(self, task: Any) -> Any:
        raise NotImplementedError("ICPC Light v3.3 does not emit UOJ search/replace patches")

    def start_fault_coverage(self, task: FaultCoverageInput) -> _BridgeSession:
        return _BridgeSession(
            "fault_coverage",
            {
                "schema_version": 1,
                "task": "fault_coverage",
                "model": self.model,
                "reasoning_effort": ICPC_LIGHT_REASONING_EFFORT,
                "input": {
                    "problem_id": task.problem_id,
                    "problem_statement": task.problem_statement,
                    "metadata": _public_metadata(task.metadata),
                },
            },
            expected_config_binding=self._expected_config_binding,
        )

    def start_fault_exposure(self, task: FaultExposureInput) -> _BridgeSession:
        return _BridgeSession(
            "fault_exposure",
            {
                "schema_version": 1,
                "task": "fault_exposure",
                "model": self.model,
                "reasoning_effort": ICPC_LIGHT_REASONING_EFFORT,
                "input": {
                    "problem_id": task.problem_id,
                    "problem_statement": task.problem_statement,
                    "submission_id": task.submission_id,
                    "submission_code": task.submission_code,
                    "submission_language": task.submission_language,
                    "metadata": _public_metadata(task.metadata),
                },
            },
            expected_config_binding=self._expected_config_binding,
        )
