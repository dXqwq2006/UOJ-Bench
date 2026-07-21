"""Shared evaluation result and verdict primitives.

Generated-code execution is routed through backend-neutral adapters. This module
contains no subprocess or HTTP compile/run implementation; it only keeps the report
dataclasses, execution metadata constants, and status/verdict helpers used by package
generation, verification, and submission flows.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Final, Protocol


GENERATED_TEST_OUTPUT_LIMIT_BYTES = 256 * 1024 * 1024
SOLUTION_OUTPUT_LIMIT_BYTES = 64 * 1024 * 1024
STDERR_CAPTURE_LIMIT_BYTES = 64 * 1024
EXECUTION_TIME_LIMIT_SEC: Final[int] = 5
EXECUTION_MEMORY_LIMIT_BYTES: Final[int] = 1024 * 1024 * 1024
EXECUTION_MEMORY_LIMIT_MB: Final[int] = 1024
EXECUTION_BACKEND_LIGHTCPVERIFIER: Final[str] = "lightcpverifier"
EXECUTION_BACKEND_LOCAL: Final[str] = "local"
EXECUTION_CATEGORY_COMPILE: Final[str] = "compile"
EXECUTION_CATEGORY_GENERATOR: Final[str] = "generator"
EXECUTION_CATEGORY_VALIDATOR: Final[str] = "validator"
EXECUTION_CATEGORY_CHECKER: Final[str] = "checker"
EXECUTION_CATEGORY_BRUTE_FORCE: Final[str] = "brute_force"
EXECUTION_CATEGORY_CANDIDATE: Final[str] = "candidate"
EXECUTION_CATEGORY_SOLUTION: Final[str] = "solution"
EXECUTION_CATEGORY_SUBMISSION: Final[str] = "submission"
EXECUTION_CATEGORY_UNSPECIFIED: Final[str] = "unspecified"
# Exit code reserved for "this solution declares the input is out of its supported
# scale". The protocol is documented in docs/RUN_ARTIFACTS.md and surfaces here
# as the UNSUPPORTED test-point status.
UNSUPPORTED_INPUT_EXIT_CODE = 67


class TestSpecLike(Protocol):
    index: int
    input_path: str
    answer_path: str


class PackageTimeLimitLike(Protocol):
    time_limit_ms: int


@dataclass(frozen=True)
class CommandResult:
    command: list[str]
    exit_code: int
    stdout: str
    stderr: str
    time_ms: int
    memory_kb: int | None = None
    timed_out: bool = False
    output_limit_exceeded: bool = False
    memory_limit_exceeded: bool = False
    memory_limit_bytes: int | None = None
    execution_category: str = EXECUTION_CATEGORY_UNSPECIFIED
    time_limit_sec: int | None = None
    requested_time_limit_sec: int | None = None
    requested_memory_limit_bytes: int | None = None
    execution_backend: str = EXECUTION_BACKEND_LIGHTCPVERIFIER
    execution_backend_deprecated: bool = False

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class TestPointResult:
    index: int
    input_path: str
    answer_path: str
    status: str
    time_ms: int
    memory_kb: int | None
    exit_code: int | None = None
    detail: str = ""
    execution_category: str = EXECUTION_CATEGORY_UNSPECIFIED
    time_limit_sec: int | None = None
    memory_limit_bytes: int | None = None
    execution_backend: str = EXECUTION_BACKEND_LIGHTCPVERIFIER
    execution_backend_deprecated: bool = False

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def status_from_run_result(result: CommandResult) -> tuple[str, str]:
    if result.timed_out:
        return "TLE", result.stderr or "Timed out"
    if result.output_limit_exceeded:
        return "OLE", result.stderr or "Output limit exceeded"
    if result.memory_limit_exceeded:
        return "MLE", _memory_limit_detail(result)
    if result.exit_code == UNSUPPORTED_INPUT_EXIT_CODE:
        return (
            "UNSUPPORTED",
            result.stderr or "Input is outside this solution's supported range",
        )
    if result.exit_code != 0:
        return "RE", result.stderr or f"Exited with code {result.exit_code}"
    return "AC", ""


def test_point_result(
    spec: TestSpecLike, status: str, run_result: CommandResult, detail: str
) -> TestPointResult:
    return TestPointResult(
        index=spec.index,
        input_path=spec.input_path,
        answer_path=spec.answer_path,
        status=status,
        time_ms=run_result.time_ms,
        memory_kb=run_result.memory_kb,
        exit_code=run_result.exit_code,
        detail="" if status == "AC" else detail,
        execution_category=run_result.execution_category,
        time_limit_sec=run_result.time_limit_sec,
        memory_limit_bytes=run_result.memory_limit_bytes,
        execution_backend=run_result.execution_backend,
        execution_backend_deprecated=run_result.execution_backend_deprecated,
    )


def overall_verdict(expected: str, tests: list[TestPointResult]) -> str:
    if not tests:
        return "NO_TESTS"
    all_accepted = all(test.status == "AC" for test in tests)
    if expected == "REJECTED":
        return "UNEXPECTED_AC" if all_accepted else "REJECTED"
    if all_accepted:
        return "AC"
    if any(test.status == "UNSUPPORTED" for test in tests) and all(
        test.status in {"AC", "UNSUPPORTED"} for test in tests
    ):
        return "PARTIAL_AC"
    return next(test.status for test in tests if test.status != "AC")


def first_failed_test(tests: list[TestPointResult]) -> str | None:
    for test in tests:
        if test.status != "AC":
            return test.input_path
    return None


def first_failure_detail(tests: list[TestPointResult]) -> str:
    for test in tests:
        if test.status != "AC":
            return test.detail
    return ""


def normalize_lightcp_status(status: str) -> str:
    normalized = " ".join(
        status.lower().replace("_", " ").replace("-", " ").split()
    )
    if normalized in {
        "tle",
        "time limit",
        "time limit exceeded",
        "timed out",
        "timeout",
    }:
        return "TLE"
    if normalized in {"mle", "memory limit", "memory limit exceeded"}:
        return "MLE"
    if normalized in {"ole", "output limit", "output limit exceeded"}:
        return "OLE"
    if normalized in {"accepted", "compiled", "exited", "ok"}:
        return "AC"
    if normalized in {"wa", "wrong answer"}:
        return "WA"
    return "RE"


def execution_time_limit_sec(_requested_timeout_sec: int | None = None) -> int:
    """Return the effective wall-clock limit for any generated-code execution."""

    return EXECUTION_TIME_LIMIT_SEC


def execution_time_limit_ms(_requested_timeout_ms: int | None = None) -> int:
    return EXECUTION_TIME_LIMIT_SEC * 1000


def execution_memory_limit_bytes(
    _requested_memory_limit_bytes: int | None = None,
) -> int:
    return EXECUTION_MEMORY_LIMIT_BYTES


def execution_memory_limit_mb(_requested_memory_limit_mb: int | None = None) -> int:
    return EXECUTION_MEMORY_LIMIT_MB


def compile_error_detail(compile_result: object) -> str:
    if not isinstance(compile_result, dict):
        return "missing compile result"
    stderr = str(compile_result.get("stderr") or "").strip()
    if stderr:
        return stderr.splitlines()[-1][:300]
    exit_code = compile_result.get("exit_code")
    return f"compiler exited with code {exit_code}"


def timeout(package: PackageTimeLimitLike) -> int:
    """Return the effective wall-clock budget in seconds for one invocation."""

    return execution_time_limit_sec(package.time_limit_ms // 1000)


# Backward-compatible private aliases for current internal callers. New code should
# use the public neutral names above.
_compile_error_detail = compile_error_detail
_first_failed_test = first_failed_test
_first_failure_detail = first_failure_detail
_normalize_lightcp_status = normalize_lightcp_status
_overall_verdict = overall_verdict
_status_from_run_result = status_from_run_result
_test_point_result = test_point_result
_timeout = timeout


def _memory_limit_detail(result: CommandResult) -> str:
    limit = result.memory_limit_bytes
    if limit:
        detail = f"Memory limit exceeded ({max(1, limit // (1 << 20))} MiB)"
    else:
        detail = "Memory limit exceeded"
    stderr = result.stderr.strip()
    if stderr:
        detail += f": {stderr[:300]}"
    return detail
