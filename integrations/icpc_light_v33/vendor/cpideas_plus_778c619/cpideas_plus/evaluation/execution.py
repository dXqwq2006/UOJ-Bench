"""Backend-neutral generated-code execution contract and factory."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence

from .local_runtime import EXECUTION_CATEGORY_UNSPECIFIED

VERIFIER_BACKENDS = ("lightcpverifier", "local")


@dataclass(frozen=True)
class CustomTestResult:
    status: str
    ok: bool
    stdout: str
    stderr: str
    exit_status: int | None
    signal: str | None
    time_ms: int
    memory_bytes: int
    payload: dict[str, Any]
    execution_category: str = EXECUTION_CATEGORY_UNSPECIFIED
    time_limit_ms: int | None = None
    memory_limit_mb: int | None = None
    max_output_bytes: int | None = None
    requested_time_limit_ms: int | None = None
    requested_memory_limit_mb: int | None = None
    execution_backend: str = "lightcpverifier"
    execution_backend_deprecated: bool = False
    wall_time_limit_ms: int | None = None
    requested_max_output_bytes: int | None = None
    compilation_cpu_time_limit_ms: int | None = None
    compilation_memory_limit_mb: int | None = None
    compilation_process_limit: int | None = None


@dataclass(frozen=True)
class CustomTestBatchCase:
    """One input in a batch custom-test request.

    ``argv`` overrides the batch-level default when present. ``copy_in_files``
    is merged with the batch-level files, replacing files with the same name.
    ``id`` is opaque and is echoed by LightCPVerifier.
    """

    stdin: str
    id: str | int | float | None = None
    argv: Sequence[str] | None = None
    copy_in_files: Mapping[str, str] | None = None


@dataclass(frozen=True)
class CustomTestValidatorSpec:
    """Optional validator program run before each input in a custom-test batch."""

    code: str
    language: str | None = None
    source_name: str | None = None
    argv: Sequence[str] | None = None
    compile_copy_in_files: Mapping[str, str] | None = None
    time_limit_ms: int | None = None
    memory_limit_mb: int | None = None
    max_output_bytes: int | None = None


@dataclass(frozen=True)
class CustomTestValidationResult:
    """Validator outcome associated with one batch input."""

    status: str
    ok: bool
    stdout: str
    stderr: str
    exit_status: int | None
    signal: str | None
    time_ns: int
    time_ms: int
    memory_bytes: int
    raw_status: str | None
    cached: bool
    output_truncated: bool
    payload: dict[str, Any]


@dataclass(frozen=True)
class CustomTestBatchCaseResult:
    """Execution and validation outcome for one input in a batch."""

    index: int
    id: str | int | float | None
    executed: bool
    status: str
    ok: bool
    stdout: str
    stderr: str
    exit_status: int | None
    signal: str | None
    time_ns: int
    time_ms: int
    memory_bytes: int
    raw_status: str | None
    cached: bool
    output_truncated: bool
    validation: CustomTestValidationResult
    payload: dict[str, Any]


@dataclass(frozen=True)
class CustomTestBatchResult:
    """Parsed response from LightCPVerifier's batch custom-test endpoint."""

    status: str
    ok: bool
    total: int
    valid: int
    invalid: int
    validator_errors: int
    output_truncated: bool
    captured_output_bytes: int
    max_batch_output_bytes: int
    compile_error: str | None
    validator_compile_error: str | None
    results: tuple[CustomTestBatchCaseResult, ...]
    payload: dict[str, Any]
    time_limit_ms: int
    memory_limit_mb: int
    max_output_bytes: int
    requested_time_limit_ms: int
    requested_memory_limit_mb: int
    execution_category: str = EXECUTION_CATEGORY_UNSPECIFIED
    execution_backend: str = "lightcpverifier"
    execution_backend_deprecated: bool = False
    wall_time_limit_ms: int | None = None
    requested_max_output_bytes: int | None = None
    validator_time_limit_ms: int | None = None
    validator_wall_time_limit_ms: int | None = None
    validator_memory_limit_mb: int | None = None
    validator_max_output_bytes: int | None = None
    requested_validator_time_limit_ms: int | None = None
    requested_validator_memory_limit_mb: int | None = None
    requested_validator_max_output_bytes: int | None = None
    compilation_cpu_time_limit_ms: int | None = None
    compilation_memory_limit_mb: int | None = None
    compilation_process_limit: int | None = None
    validator_compilation_cpu_time_limit_ms: int | None = None
    validator_compilation_memory_limit_mb: int | None = None
    validator_compilation_process_limit: int | None = None


class CustomTestRunner(Protocol):
    backend: str

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
    ) -> CustomTestResult: ...


def create_custom_test_runner(
    backend: str,
    *,
    lightcpverifier_url: str = "http://127.0.0.1:8081",
) -> CustomTestRunner:
    if backend == "lightcpverifier":
        from .sandbox import LightCPVerifierRunner

        return LightCPVerifierRunner(base_url=lightcpverifier_url)
    if backend == "local":
        from .local_executor import LocalCustomTestRunner

        return LocalCustomTestRunner()
    raise ValueError(f"verifier backend must be one of {VERIFIER_BACKENDS}")
