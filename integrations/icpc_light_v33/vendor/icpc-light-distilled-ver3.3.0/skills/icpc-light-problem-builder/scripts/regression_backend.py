#!/usr/bin/env python3
"""Program x Dataset execution backends for the ICPC Light regression gate.

This module is deliberately the only place where the ver3 skill knows about
CPIdeas.  The regression gate keeps ownership of its plan, verdict, and receipt
semantics; a backend only compiles a source and reports observed process facts
for an ordered dataset.

The production backend is LightCPVerifier through CPIdeas.  The local backend
exists solely for explicit ``--test-mode`` compatibility and never acts as an
automatic fallback.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence


MAX_CAPTURE_BYTES = 64 * 1024 * 1024
LIGHTCP_BATCH_SIZE = 128
LIGHTCP_MAX_REQUEST_BYTES = 60 * 1024 * 1024
LIGHTCP_MAX_OUTPUT_BYTES = 16 * 1024 * 1024
LIGHTCP_API_REVISION = "cpideas-custom-test-batch-v3"
LIGHTCP_DATASET_API_REVISION = "cpideas-program-dataset-v1"
LIGHTCP_CPP_PROFILE = "gnu++17-O2-pipe-online-judge-I-dot-package-testlib-v4"
BACKEND_EVIDENCE_SCHEMA_VERSION = 1
COMPILE_CONTEXT_POLICY_REVISION = "role-source-only-v1"


class BackendError(RuntimeError):
    """The selected execution backend cannot satisfy the requested operation."""


class SourceLike(Protocol):
    role: str
    rel: str
    path: Path


@dataclass(frozen=True)
class BackendSource:
    role: str
    rel: str
    path: Path


@dataclass(frozen=True)
class ProgramResult:
    """Backend-neutral facts observed for one process invocation."""

    returncode: int | None
    timed_out: bool
    duration_seconds: float
    stdout: bytes
    stderr: bytes
    memory_bytes: int = 0
    launch_error: str | None = None
    sandbox_verdict: str | None = None
    sandbox_status: str | None = None

    def compact(self, *, include_output_hash: bool = True) -> dict[str, Any]:
        result: dict[str, Any] = {
            "returncode": self.returncode,
            "timed_out": self.timed_out,
            "duration_seconds": round(self.duration_seconds, 6),
            "memory_bytes": self.memory_bytes,
            "stderr_preview": preview_bytes(self.stderr),
        }
        if self.launch_error is not None:
            result["launch_error"] = self.launch_error
        if self.sandbox_verdict is not None:
            result["sandbox_verdict"] = self.sandbox_verdict
        if self.sandbox_status is not None:
            result["sandbox_status"] = self.sandbox_status
        if include_output_hash:
            result["stdout_sha256"] = sha256_bytes(self.stdout)
            result["stdout_bytes"] = len(self.stdout)
        return result


@dataclass(frozen=True)
class DatasetInvocation:
    """One ordered invocation of a prepared program."""

    stdin: bytes = b""
    argv: tuple[str, ...] = ()
    copy_in_files: Mapping[str, bytes] = field(default_factory=dict)
    case_id: str | int | float | None = None


@dataclass(frozen=True)
class PreparedProgram:
    """A backend-owned executable reference bound to an original source."""

    role: str
    source_rel: str
    source_path: Path
    source_sha256: str
    opaque: Any = None


class ProgramDatasetBackend(Protocol):
    """Minimal compile + ordered Program x Dataset contract."""

    name: str

    def configuration(
        self,
        *,
        requested_program_timeout_seconds: float,
        requested_compile_timeout_seconds: float,
        requested_memory_limit_mb: int,
    ) -> dict[str, Any]: ...

    def compile_sources(
        self,
        sources: Sequence[SourceLike],
        *,
        problem_dir: Path,
        build_dir: Path,
        timeout: float,
    ) -> tuple[dict[str, PreparedProgram], list[dict[str, Any]], list[str]]: ...

    def run_dataset(
        self,
        program: PreparedProgram,
        dataset: Sequence[DatasetInvocation],
        *,
        problem_dir: Path,
        timeout: float,
    ) -> list[ProgramResult]: ...

    def execution_evidence(self) -> dict[str, Any]: ...


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return sha256_bytes(encoded)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def preview_bytes(data: bytes, limit: int = 1000) -> str:
    clipped = data[:limit].decode("utf-8", errors="replace")
    return clipped + ("\n... truncated ..." if len(data) > limit else "")


def process_succeeded(result: ProgramResult) -> bool:
    return (
        not result.timed_out
        and result.launch_error is None
        and result.returncode == 0
    )


def validated_lightcp_service_identity(health: Any) -> dict[str, Any]:
    if not isinstance(health, dict) or health.get("ok") is not True:
        raise BackendError(
            f"LightCPVerifier health check returned an unhealthy payload: {health!r}"
        )
    identity = health.get("service")
    if not isinstance(identity, dict):
        raise BackendError(
            "LightCPVerifier health payload has no service identity; rebuild "
            "the matching attested CPIdeas image"
        )
    expected_identity = {
        "apiRevision": LIGHTCP_API_REVISION,
        "compilerProfile": LIGHTCP_CPP_PROFILE,
    }
    for key, expected in expected_identity.items():
        if identity.get(key) != expected:
            raise BackendError(
                f"LightCPVerifier service identity {key}={identity.get(key)!r}; "
                f"expected {expected!r}"
            )
    for key in ("buildId", "imageId"):
        value = identity.get(key)
        if not isinstance(value, str) or re.fullmatch(
            r"sha256:[0-9a-f]{64}", value
        ) is None:
            raise BackendError(
                f"LightCPVerifier service identity {key} is not an attested "
                "SHA-256 digest; start it with the CPIdeas Docker helper"
            )
    policy = identity.get("executionPolicy")
    if not isinstance(policy, dict):
        raise BackendError("LightCPVerifier service identity has no executionPolicy")
    runtime = policy.get("runtime")
    compilation = policy.get("compilation")
    batch = policy.get("batch")
    if not all(isinstance(value, dict) for value in (runtime, compilation, batch)):
        raise BackendError("LightCPVerifier executionPolicy is incomplete")
    required_positive = (
        (runtime, "minimumCpuTimeMs"),
        (runtime, "maximumCpuTimeMs"),
        (runtime, "wallTimeMultiplier"),
        (runtime, "minimumMemoryMb"),
        (runtime, "maximumMemoryMb"),
        (runtime, "maximumOutputBytes"),
        (batch, "maxTests"),
        (batch, "maxCapturedOutputBytes"),
    )
    for owner, key in required_positive:
        value = owner.get(key)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
            raise BackendError(
                f"LightCPVerifier executionPolicy {key} is not positive: {value!r}"
            )
    cpp_compile = compilation.get("cpp")
    if not isinstance(cpp_compile, dict) or any(
        not isinstance(cpp_compile.get(key), int) or cpp_compile[key] <= 0
        for key in ("cpuTimeMs", "memoryMb", "processLimit")
    ):
        raise BackendError("LightCPVerifier C++ compilation policy is incomplete")
    return dict(identity)


def cpideas_module_bindings() -> dict[str, str]:
    """Hash-bind the exact client modules that interpret sandbox evidence."""

    bindings: dict[str, str] = {}
    for name in (
        "cpideas_plus.evaluation.dataset",
        "cpideas_plus.evaluation.sandbox",
        "cpideas_plus.evaluation.execution",
        "cpideas_plus.evaluation.local_runtime",
    ):
        module = importlib.import_module(name)
        raw_path = getattr(module, "__file__", None)
        if not isinstance(raw_path, str):
            raise BackendError(f"CPIdeas module {name} has no hashable file")
        path = Path(raw_path)
        if not path.is_file() or path.is_symlink():
            raise BackendError(f"CPIdeas module {name} is not a regular file")
        bindings[name] = sha256_file(path)
    return bindings


def create_backend(
    name: str,
    *,
    test_mode: bool,
    lightcpverifier_url: str,
    program_time_limit_ms: int,
    memory_limit_mb: int,
) -> ProgramDatasetBackend:
    """Create exactly the requested backend; never downgrade implicitly."""

    if name == "local":
        if not test_mode:
            raise BackendError(
                "the local execution backend is testing-only; pass --test-mode "
                "or select --execution-backend lightcpverifier"
            )
        return LocalProgramDatasetBackend()
    if name == "lightcpverifier":
        return LightCPVerifierProgramDatasetBackend(
            lightcpverifier_url,
            program_time_limit_ms=program_time_limit_ms,
            memory_limit_mb=memory_limit_mb,
        )
    raise BackendError(f"unsupported execution backend: {name}")


def _compiler_command() -> list[str]:
    configured = os.environ.get("CXX", "").strip()
    if configured:
        command = shlex.split(configured)
        if command and shutil.which(command[0]):
            return command
        raise BackendError("CXX does not name an executable compiler")
    for candidate in ("c++", "g++", "clang++"):
        resolved = shutil.which(candidate)
        if resolved:
            return [resolved]
    raise BackendError("no executable C++ compiler found (CXX/c++/g++/clang++)")


def _run_local_command(
    command: list[str],
    *,
    cwd: Path,
    timeout: float,
    stdin: bytes | None = None,
) -> ProgramResult:
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            input=stdin,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
            shell=False,
        )
        result = ProgramResult(
            returncode=completed.returncode,
            timed_out=False,
            duration_seconds=time.monotonic() - started,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )
    except subprocess.TimeoutExpired as exc:
        result = ProgramResult(
            returncode=None,
            timed_out=True,
            duration_seconds=time.monotonic() - started,
            stdout=exc.stdout or b"",
            stderr=exc.stderr or b"",
        )
    except OSError as exc:
        result = ProgramResult(
            returncode=None,
            timed_out=False,
            duration_seconds=time.monotonic() - started,
            stdout=b"",
            stderr=b"",
            launch_error=str(exc),
        )
    if len(result.stdout) > MAX_CAPTURE_BYTES or len(result.stderr) > MAX_CAPTURE_BYTES:
        return ProgramResult(
            returncode=None,
            timed_out=False,
            duration_seconds=result.duration_seconds,
            stdout=result.stdout[:MAX_CAPTURE_BYTES],
            stderr=result.stderr[:MAX_CAPTURE_BYTES],
            launch_error=f"process output exceeded {MAX_CAPTURE_BYTES} bytes",
        )
    return result


class LocalProgramDatasetBackend:
    """The ver2 subprocess behavior, available only by explicit test-mode opt-in."""

    name = "local"

    def __init__(self) -> None:
        self._invocations: list[dict[str, Any]] = []

    def configuration(
        self,
        *,
        requested_program_timeout_seconds: float,
        requested_compile_timeout_seconds: float,
        requested_memory_limit_mb: int,
    ) -> dict[str, Any]:
        return {
            "name": self.name,
            "sandboxed": False,
            "testing_only": True,
            "requested_program_timeout_seconds": requested_program_timeout_seconds,
            "effective_program_timeout_seconds": requested_program_timeout_seconds,
            "verdict_time_limit_seconds": requested_program_timeout_seconds,
            "sandbox_effective_time_limit_seconds": requested_program_timeout_seconds,
            "timeout_classification": "sandbox-enforced",
            "requested_compile_timeout_seconds": requested_compile_timeout_seconds,
            "effective_compile_timeout_seconds": requested_compile_timeout_seconds,
            "requested_memory_limit_mb": requested_memory_limit_mb,
            "effective_memory_limit_mb": None,
            "dataset_batch_size": 1,
            "service_identity": None,
            "execution_evidence_schema_version": BACKEND_EVIDENCE_SCHEMA_VERSION,
            "adapter_sha256": sha256_file(Path(__file__).resolve()),
        }

    def execution_evidence(self) -> dict[str, Any]:
        invocations = list(self._invocations)
        return {
            "schema_version": BACKEND_EVIDENCE_SCHEMA_VERSION,
            "kind": "icpc-light.program-dataset-execution-evidence",
            "backend": self.name,
            "sandboxed": False,
            "testing_only": True,
            "adapter_sha256": sha256_file(Path(__file__).resolve()),
            "invocation_count": len(invocations),
            "invocations_sha256": canonical_sha256(invocations),
            "invocations": invocations,
        }

    def compile_sources(
        self,
        sources: Sequence[SourceLike],
        *,
        problem_dir: Path,
        build_dir: Path,
        timeout: float,
    ) -> tuple[dict[str, PreparedProgram], list[dict[str, Any]], list[str]]:
        compiler = _compiler_command()
        programs: dict[str, PreparedProgram] = {}
        records: list[dict[str, Any]] = []
        errors: list[str] = []
        for index, source in enumerate(sources):
            binary = build_dir / f"program-{index:02d}"
            command = [
                *compiler,
                "-std=c++17",
                "-O2",
                "-pipe",
                "-DONLINE_JUDGE",
                "-I",
                str(problem_dir / "package"),
                "-I",
                str(problem_dir),
                str(source.path),
                "-o",
                str(binary),
            ]
            result = _run_local_command(command, cwd=problem_dir, timeout=timeout)
            record = {
                "role": source.role,
                "source": source.rel,
                "source_sha256": sha256_file(source.path),
                "command": command,
                "result": result.compact(),
                "status": "passed" if process_succeeded(result) else "failed",
            }
            records.append(record)
            if process_succeeded(result) and binary.is_file():
                programs[source.role] = PreparedProgram(
                    role=source.role,
                    source_rel=source.rel,
                    source_path=source.path,
                    source_sha256=sha256_file(source.path),
                    opaque=binary,
                )
            else:
                errors.append(f"compilation failed for {source.role} ({source.rel})")
        return programs, records, errors

    def run_dataset(
        self,
        program: PreparedProgram,
        dataset: Sequence[DatasetInvocation],
        *,
        problem_dir: Path,
        timeout: float,
    ) -> list[ProgramResult]:
        binary = program.opaque
        if not isinstance(binary, Path):
            raise BackendError(f"local program {program.role} has no executable path")
        results: list[ProgramResult] = []
        for invocation in dataset:
            if invocation.copy_in_files:
                with tempfile.TemporaryDirectory(
                    prefix="icpc-light-runtime-files-"
                ) as raw_temp:
                    runtime_dir = Path(raw_temp)
                    for name, content in invocation.copy_in_files.items():
                        target = _safe_runtime_file(runtime_dir, name)
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(content)
                    argv = [
                        str(runtime_dir / token)
                        if token in invocation.copy_in_files
                        else token
                        for token in invocation.argv
                    ]
                    results.append(
                        _run_local_command(
                            [str(binary), *argv],
                            cwd=problem_dir,
                            timeout=timeout,
                            stdin=invocation.stdin,
                        )
                    )
            else:
                results.append(
                    _run_local_command(
                        [str(binary), *invocation.argv],
                        cwd=problem_dir,
                        timeout=timeout,
                        stdin=invocation.stdin,
                    )
                )
        invocation = {
            "index": len(self._invocations),
            "role": program.role,
            "source": program.source_rel,
            "source_sha256": program.source_sha256,
            "requested_case_count": len(dataset),
            "requested_case_ids_sha256": canonical_sha256(
                [
                    item.case_id if item.case_id is not None else index
                    for index, item in enumerate(dataset)
                ]
            ),
            "status": "completed",
            "evaluation_complete": True,
            "case_results_sha256": canonical_sha256(
                [result.compact() for result in results]
            ),
        }
        invocation["evidence_sha256"] = canonical_sha256(invocation)
        self._invocations.append(invocation)
        return results


def _safe_runtime_file(root: Path, name: str) -> Path:
    candidate = Path(name)
    if not name or candidate.is_absolute() or ".." in candidate.parts:
        raise BackendError(f"unsafe runtime copy-in path: {name!r}")
    target = root.joinpath(*candidate.parts)
    try:
        target.resolve(strict=False).relative_to(root.resolve())
    except (OSError, RuntimeError, ValueError) as exc:
        raise BackendError(f"unsafe runtime copy-in path: {name!r}") from exc
    return target


def _decode_text(data: bytes, label: str) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BackendError(
            f"LightCPVerifier only accepts UTF-8 text for {label}: {exc}"
        ) from exc


def _lightcp_timed_out(status: str) -> bool:
    """Trust the sandbox timeout status; do not reclassify elapsed telemetry."""

    normalized = " ".join(
        status.lower().replace("_", " ").replace("-", " ").split()
    )
    return normalized in {
        "tle",
        "time limit",
        "time limit exceeded",
        "timed out",
        "timeout",
    }


def _lightcp_source_text(path: Path) -> str:
    # LightCPVerifier's fixed C++ profile supplies ONLINE_JUDGE and the
    # image-owned /lib/testlib include root. Submit only this hash-bound source.
    return _decode_text(path.read_bytes(), str(path))


def _compile_copy_in_files() -> dict[str, str]:
    """Return the role-isolated compiler context.

    ICPC Light programs are single translation units.  Giving one role the
    other role sources lets an adversarial candidate include the model, oracle,
    validator, or checker during compilation.  The submitted source is already
    copied separately by LightCPVerifier; fixed testlib headers come from the
    verifier image, so no problem-owned C/C++ file is copied here.
    """

    return {}


def compile_context_sha256() -> str:
    """Bind the deliberately empty problem-owned compiler copy-in mapping."""

    return canonical_sha256(_compile_copy_in_files())


def _compact_evaluation_receipt(evaluation: Any) -> dict[str, Any]:
    """Keep full batch/resource evidence while bounding per-case receipt size."""

    try:
        receipt = evaluation.to_receipt()
    except (AttributeError, TypeError, ValueError) as exc:
        raise BackendError(
            "CPIdeas DatasetEvaluation does not expose a valid receipt"
        ) from exc
    if not isinstance(receipt, dict):
        raise BackendError("CPIdeas DatasetEvaluation receipt is not an object")
    compact = dict(receipt)
    cases = compact.pop("cases", None)
    if not isinstance(cases, list):
        raise BackendError("CPIdeas DatasetEvaluation receipt has no case array")
    compact["case_results_binding"] = {
        "count": len(cases),
        "sha256": canonical_sha256(cases),
    }
    return compact


class LightCPVerifierProgramDatasetBackend:
    """CPIdeas adapter; imports are intentionally isolated and fail closed."""

    name = "lightcpverifier"

    def __init__(
        self,
        base_url: str,
        *,
        program_time_limit_ms: int,
        memory_limit_mb: int,
    ):
        try:
            from cpideas_plus.evaluation.dataset import (  # type: ignore[import-not-found]
                DATASET_API_REVISION,
                DatasetEvaluator,
                DatasetProgram,
                DatasetTest,
            )
            from cpideas_plus.evaluation.sandbox import (  # type: ignore[import-not-found]
                LightCPVerifierRunner,
            )
        except (ImportError, AttributeError) as exc:
            raise BackendError(
                "the lightcpverifier backend requires CPIdeas-Plus with "
                "cpideas_plus.evaluation.dataset available; install that package "
                "or expose its src directory on PYTHONPATH"
            ) from exc

        self._DatasetProgram = DatasetProgram
        self._DatasetTest = DatasetTest
        if DATASET_API_REVISION != LIGHTCP_DATASET_API_REVISION:
            raise BackendError(
                f"CPIdeas dataset API revision {DATASET_API_REVISION!r}; "
                f"expected {LIGHTCP_DATASET_API_REVISION!r}"
            )
        self._dataset_api_revision = DATASET_API_REVISION
        self._client_module_sha256 = cpideas_module_bindings()
        if type(program_time_limit_ms) is not int or not 100 <= program_time_limit_ms <= 30000:
            raise BackendError("program time limit must be an integer from 100 to 30000 ms")
        if type(memory_limit_mb) is not int or not 16 <= memory_limit_mb <= 2048:
            raise BackendError("program memory limit must be an integer from 16 to 2048 MiB")
        self._program_time_limit_ms = program_time_limit_ms
        self._effective_time_limit_ms = program_time_limit_ms
        self._effective_memory_limit_mb = memory_limit_mb
        self._runner = LightCPVerifierRunner(
            base_url=base_url,
            strict_dynamic_limits=True,
        )
        self._evaluator = DatasetEvaluator(
            self._runner,
            batch_size=LIGHTCP_BATCH_SIZE,
            max_request_bytes=LIGHTCP_MAX_REQUEST_BYTES,
        )
        self._invocations: list[dict[str, Any]] = []
        self._base_url = base_url
        try:
            health = self._runner.health()
        except Exception as exc:
            raise BackendError(
                f"LightCPVerifier health check failed at {base_url}: {exc}"
            ) from exc
        self._service_identity = validated_lightcp_service_identity(health)
        runtime_policy = self._service_identity["executionPolicy"]["runtime"]
        if not (
            runtime_policy["minimumCpuTimeMs"]
            <= self._program_time_limit_ms
            <= runtime_policy["maximumCpuTimeMs"]
        ):
            raise BackendError("statement time limit is outside the attested service policy")
        if not (
            runtime_policy["minimumMemoryMb"]
            <= self._effective_memory_limit_mb
            <= runtime_policy["maximumMemoryMb"]
        ):
            raise BackendError("statement memory limit is outside the attested service policy")

    def configuration(
        self,
        *,
        requested_program_timeout_seconds: float,
        requested_compile_timeout_seconds: float,
        requested_memory_limit_mb: int,
    ) -> dict[str, Any]:
        requested_time_limit_ms = round(requested_program_timeout_seconds * 1000)
        if requested_time_limit_ms != self._program_time_limit_ms:
            raise BackendError("requested program timeout differs from the statement policy")
        if requested_memory_limit_mb != self._effective_memory_limit_mb:
            raise BackendError("requested memory limit differs from the statement policy")
        return {
            "name": self.name,
            "sandboxed": True,
            "testing_only": False,
            "service_url": self._base_url,
            "requested_program_timeout_seconds": requested_program_timeout_seconds,
            "effective_program_timeout_seconds": self._effective_time_limit_ms / 1000,
            "verdict_time_limit_seconds": requested_program_timeout_seconds,
            "sandbox_effective_time_limit_seconds": self._effective_time_limit_ms / 1000,
            "timeout_classification": "sandbox-enforced",
            "requested_compile_timeout_seconds": requested_compile_timeout_seconds,
            "effective_compile_timeout_seconds": None,
            "compile_limit_control": "LightCPVerifier service profile",
            "requested_memory_limit_mb": requested_memory_limit_mb,
            "effective_memory_limit_mb": self._effective_memory_limit_mb,
            "compile_context_policy_revision": COMPILE_CONTEXT_POLICY_REVISION,
            "dataset_batch_size": LIGHTCP_BATCH_SIZE,
            "max_request_bytes": self._evaluator.max_request_bytes,
            "max_output_bytes_per_stream": LIGHTCP_MAX_OUTPUT_BYTES,
            "cpp_compiler_profile": LIGHTCP_CPP_PROFILE,
            "dataset_api_revision": self._dataset_api_revision,
            "client_module_sha256": self._client_module_sha256,
            "service_identity": self._service_identity,
            "execution_evidence_schema_version": BACKEND_EVIDENCE_SCHEMA_VERSION,
            "adapter_sha256": sha256_file(Path(__file__).resolve()),
        }

    def execution_evidence(self) -> dict[str, Any]:
        invocations = list(self._invocations)
        return {
            "schema_version": BACKEND_EVIDENCE_SCHEMA_VERSION,
            "kind": "icpc-light.program-dataset-execution-evidence",
            "backend": self.name,
            "sandboxed": True,
            "testing_only": False,
            "adapter_sha256": sha256_file(Path(__file__).resolve()),
            "dataset_api_revision": self._dataset_api_revision,
            "client_module_sha256": self._client_module_sha256,
            "service_identity": self._service_identity,
            "invocation_count": len(invocations),
            "invocations_sha256": canonical_sha256(invocations),
            "invocations": invocations,
        }

    def compile_sources(
        self,
        sources: Sequence[SourceLike],
        *,
        problem_dir: Path,
        build_dir: Path,
        timeout: float,
    ) -> tuple[dict[str, PreparedProgram], list[dict[str, Any]], list[str]]:
        # Only each SourceLike.path is read below. The surrounding problem tree
        # is intentionally unavailable to the compiler.
        del problem_dir, build_dir
        programs: dict[str, PreparedProgram] = {}
        records: list[dict[str, Any]] = []
        errors: list[str] = []
        for source in sources:
            digest = sha256_file(source.path)
            compilation_evidence: dict[str, Any] | None = None
            try:
                code = _lightcp_source_text(source.path)
                compile_files = _compile_copy_in_files()
                dataset_program = self._DatasetProgram(
                    language="cpp",
                    code=code,
                    source_name=source.rel,
                    compile_copy_in_files=compile_files,
                )
                result = self._evaluator.compile(
                    dataset_program,
                    time_limit_ms=self._program_time_limit_ms,
                    memory_limit_mb=self._effective_memory_limit_mb,
                    max_output_bytes=LIGHTCP_MAX_OUTPUT_BYTES,
                )
                compilation_evidence = {
                    "schema_version": 1,
                    "kind": "cpideas.dataset_compilation",
                    "dataset_api_revision": self._dataset_api_revision,
                    "source_name": source.rel,
                    "source_sha256": result.source_sha256,
                    "compile_context_policy_revision": COMPILE_CONTEXT_POLICY_REVISION,
                    "compile_copy_in_files_sha256": canonical_sha256(compile_files),
                    "status": result.status,
                    "ok": result.ok,
                    "cached": result.cached,
                    "time_ms": result.time_ms,
                    "runtime_profile_for_subsequent_execution": {
                        "requested_time_limit_ms": result.requested_time_limit_ms,
                        "effective_time_limit_ms": result.effective_time_limit_ms,
                        "effective_wall_time_limit_ms": (
                            result.effective_wall_time_limit_ms
                        ),
                        "requested_memory_limit_mb": (
                            result.requested_memory_limit_mb
                        ),
                        "effective_memory_limit_mb": (
                            result.effective_memory_limit_mb
                        ),
                        "requested_max_output_bytes": (
                            result.requested_max_output_bytes
                        ),
                        "effective_max_output_bytes": (
                            result.effective_max_output_bytes
                        ),
                    },
                    "compiler_limits": {
                        "cpu_time_ms": result.compiler_cpu_time_limit_ms,
                        "memory_mb": result.compiler_memory_limit_mb,
                        "process_limit": result.compiler_process_limit,
                    },
                }
                expected_compiler_limits = self._service_identity[
                    "executionPolicy"
                ]["compilation"]["cpp"]
                compiler_limits_match = compilation_evidence["compiler_limits"] == {
                    "cpu_time_ms": expected_compiler_limits["cpuTimeMs"],
                    "memory_mb": expected_compiler_limits["memoryMb"],
                    "process_limit": expected_compiler_limits["processLimit"],
                }
                expected_runtime_profile = {
                    "requested_time_limit_ms": self._program_time_limit_ms,
                    "effective_time_limit_ms": self._effective_time_limit_ms,
                    "effective_wall_time_limit_ms": round(
                        self._effective_time_limit_ms
                        * self._service_identity["executionPolicy"]["runtime"][
                            "wallTimeMultiplier"
                        ]
                    ),
                    "requested_memory_limit_mb": self._effective_memory_limit_mb,
                    "effective_memory_limit_mb": self._effective_memory_limit_mb,
                    "requested_max_output_bytes": LIGHTCP_MAX_OUTPUT_BYTES,
                    "effective_max_output_bytes": LIGHTCP_MAX_OUTPUT_BYTES,
                }
                runtime_profile_match = (
                    compilation_evidence[
                        "runtime_profile_for_subsequent_execution"
                    ]
                    == expected_runtime_profile
                )
                compilation_evidence["evidence_sha256"] = canonical_sha256(
                    compilation_evidence
                )
                trustworthy = (
                    result.ok
                    and result.source_sha256 == digest
                    and compiler_limits_match
                    and runtime_profile_match
                )
                observed = ProgramResult(
                    returncode=0 if trustworthy else None,
                    timed_out=False,
                    duration_seconds=result.time_ms / 1000,
                    stdout=b"",
                    stderr=result.diagnostic.encode("utf-8"),
                    launch_error=(
                        None
                        if trustworthy
                        else (
                            f"compile status/evidence mismatch: {result.status}; "
                            f"source_hash_match={result.source_sha256 == digest}; "
                            f"compiler_limits_match={compiler_limits_match}; "
                            f"runtime_profile_match={runtime_profile_match}"
                        )
                    ),
                )
                status = "passed" if trustworthy else "failed"
            except Exception as exc:
                code = None
                compile_files = None
                observed = ProgramResult(
                    returncode=None,
                    timed_out=False,
                    duration_seconds=0.0,
                    stdout=b"",
                    stderr=b"",
                    launch_error=f"{type(exc).__name__}: {exc}",
                )
                status = "failed"
            record = {
                "role": source.role,
                "source": source.rel,
                "source_sha256": digest,
                "command": [
                    "$LIGHTCPVERIFIER",
                    "POST",
                    "/custom-test",
                    "compileOnly",
                    source.role,
                ],
                "result": observed.compact(),
                "compilation_evidence": compilation_evidence,
                "status": status,
            }
            records.append(record)
            if status == "passed":
                programs[source.role] = PreparedProgram(
                    role=source.role,
                    source_rel=source.rel,
                    source_path=source.path,
                    source_sha256=digest,
                    opaque={
                        "code": code,
                        "compile_copy_in_files": compile_files,
                        "source_name": source.rel,
                    },
                )
            else:
                errors.append(f"compilation failed for {source.role} ({source.rel})")
        return programs, records, errors

    def run_dataset(
        self,
        program: PreparedProgram,
        dataset: Sequence[DatasetInvocation],
        *,
        problem_dir: Path,
        timeout: float,
    ) -> list[ProgramResult]:
        del problem_dir
        if not dataset:
            return []
        if round(timeout * 1000) != self._program_time_limit_ms:
            raise BackendError("dataset timeout differs from the statement policy")
        if not isinstance(program.opaque, dict):
            raise BackendError(f"LightCP program {program.role} is not prepared")
        tests = [
            self._DatasetTest(
                stdin=_decode_text(item.stdin, f"{program.role} stdin"),
                id=item.case_id if item.case_id is not None else index,
                argv=item.argv,
                copy_in_files={
                    name: _decode_text(content, f"{program.role} copy-in {name}")
                    for name, content in item.copy_in_files.items()
                },
            )
            for index, item in enumerate(dataset)
        ]
        source = self._DatasetProgram(
            language="cpp",
            code=program.opaque["code"],
            source_name=program.opaque["source_name"],
            compile_copy_in_files=program.opaque["compile_copy_in_files"],
        )
        try:
            evaluation = self._evaluator.evaluate(
                source,
                tests,
                comparison="none",
                time_limit_ms=self._program_time_limit_ms,
                memory_limit_mb=self._effective_memory_limit_mb,
                max_output_bytes=LIGHTCP_MAX_OUTPUT_BYTES,
            )
        except Exception as exc:
            raise BackendError(
                f"LightCPVerifier dataset execution failed for {program.role}: {exc}"
            ) from exc
        cases = tuple(evaluation.cases)
        if len(cases) != len(dataset):
            raise BackendError(
                f"dataset result length mismatch for {program.role}: "
                f"expected {len(dataset)}, got {len(cases)}"
            )
        normalized_evaluation_status = " ".join(
            str(evaluation.status)
            .lower()
            .replace("_", " ")
            .replace("-", " ")
            .split()
        )
        overall_infrastructure_failure = (
            not bool(evaluation.evaluation_complete)
            or normalized_evaluation_status
            in {"infrastructure error", "infra error", "internal error", "unknown"}
        )
        infrastructure_verdicts = {
            "INFRA",
            "VALIDATOR_ERROR",
            "INVALID_TEST_DATA",
            "NOT_EXECUTED",
        }
        represented_overall_failure = any(
            str(case.verdict).upper() in infrastructure_verdicts
            or bool(case.output_truncated)
            or case.raw is None
            for case in cases
        )
        force_overall_infrastructure = (
            overall_infrastructure_failure and not represented_overall_failure
        )
        results: list[ProgramResult] = []
        for index, case in enumerate(cases):
            raw = case.raw
            if case.index != index:
                raise BackendError(
                    f"dataset result order mismatch for {program.role}: "
                    f"expected {index}, got {case.index}"
                )
            expected_id = tests[index].id
            if case.id != expected_id:
                raise BackendError(
                    f"dataset result identity mismatch for {program.role} case "
                    f"{index}: expected id {expected_id!r}, got {case.id!r}"
                )
            verdict = str(case.verdict).upper()
            if raw is None:
                diagnostic = str(case.diagnostic or evaluation.error or evaluation.status)
                results.append(
                    ProgramResult(
                        returncode=None,
                        timed_out=False,
                        duration_seconds=0.0,
                        stdout=str(case.stdout).encode("utf-8"),
                        stderr=str(case.stderr).encode("utf-8"),
                        launch_error=(
                            "LightCPVerifier returned no low-level execution "
                            f"result (verdict={verdict}): {diagnostic}"
                        ),
                        sandbox_verdict=verdict,
                        sandbox_status=str(evaluation.status),
                    )
                )
                continue
            status = str(raw.status)
            raw_status = str(raw.raw_status or status)
            timed_out = verdict == "TLE" or _lightcp_timed_out(raw_status)
            if force_overall_infrastructure:
                timed_out = False
            normalized_status = " ".join(
                raw_status.lower().replace("_", " ").replace("-", " ").split()
            )
            returncode = raw.exit_status
            if raw.signal is not None and returncode in {None, 0}:
                # Match subprocess semantics: a signal is a non-zero runtime
                # failure and therefore classifies as RE in the regression layer.
                returncode = -1
            launch_error: str | None = None
            if case.output_truncated or raw.output_truncated:
                launch_error = (
                    case.diagnostic or "LightCPVerifier output was truncated"
                )
            elif force_overall_infrastructure:
                launch_error = str(
                    evaluation.error
                    or f"dataset evaluation was incomplete ({evaluation.status})"
                )
            elif verdict in infrastructure_verdicts:
                launch_error = str(
                    case.diagnostic
                    or f"dataset case failed before a trustworthy result ({verdict})"
                )
            elif not raw.executed:
                launch_error = f"program was not executed (status={status})"
            elif verdict in {"MLE", "OLE", "RE"}:
                # ver3 retains the four legacy candidate verdict classes.  A
                # sandbox resource/runtime failure is a program failure, not an
                # orchestrator failure, so expose it as subprocess-like nonzero.
                if returncode in {None, 0}:
                    returncode = -1
            elif verdict != "EXECUTED" and not timed_out:
                launch_error = f"unsupported sandbox verdict: {verdict}"
            elif raw.ok is not True and not timed_out:
                # Never infer success merely from exit_status == 0 when the
                # sandbox explicitly says that execution was unsuccessful.
                if returncode in {None, 0}:
                    returncode = -1
            elif not timed_out and normalized_status in {
                "internal error",
                "infrastructure error",
                "system error",
                "file error",
                "unknown",
            }:
                launch_error = f"sandbox execution failed (status={status})"
            elif returncode is None and not timed_out:
                launch_error = f"execution returned no exit status (status={status})"
            results.append(
                ProgramResult(
                    returncode=returncode,
                    timed_out=timed_out,
                    duration_seconds=(
                        raw.time_ns / 1_000_000_000
                        if raw.time_ns
                        else raw.time_ms / 1000
                    ),
                    stdout=raw.stdout.encode("utf-8"),
                    stderr=raw.stderr.encode("utf-8"),
                    memory_bytes=max(0, int(getattr(raw, "memory_bytes", 0))),
                    launch_error=launch_error,
                    sandbox_verdict=verdict,
                    sandbox_status=raw_status,
                )
            )
        compact_evaluation = _compact_evaluation_receipt(evaluation)
        invocation = {
            "index": len(self._invocations),
            "role": program.role,
            "source": program.source_rel,
            "source_sha256": program.source_sha256,
            "requested_case_count": len(dataset),
            "requested_case_ids_sha256": canonical_sha256(
                [test.id for test in tests]
            ),
            "status": str(evaluation.status),
            "evaluation_complete": bool(evaluation.evaluation_complete),
            "evaluation": compact_evaluation,
            "program_results_sha256": canonical_sha256(
                [result.compact() for result in results]
            ),
        }
        invocation["evidence_sha256"] = canonical_sha256(invocation)
        self._invocations.append(invocation)
        return results
