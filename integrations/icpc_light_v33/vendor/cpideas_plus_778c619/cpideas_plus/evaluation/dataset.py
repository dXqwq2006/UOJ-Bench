"""Program x Dataset evaluation on top of LightCPVerifier custom-test batches.

The low-level runner reports sandbox process facts.  This module adds the
orchestrator-facing contract needed by data-generation and regression tools:
deterministic chunking, stable per-case verdicts, optional output comparison,
and a compact hash-bound receipt.  It never falls back to host execution.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Any, Mapping, Protocol, Sequence

from .execution import (
    CustomTestBatchCase,
    CustomTestBatchCaseResult,
    CustomTestBatchResult,
    CustomTestResult,
    CustomTestValidatorSpec,
)


DEFAULT_DATASET_BATCH_SIZE = 128
DEFAULT_DATASET_REQUEST_BYTES = 60 * 1024 * 1024
DATASET_API_REVISION = "cpideas-program-dataset-v1"
DATASET_COMPARISONS = ("none", "tokens", "exact")
_MAX_SAFE_JSON_INTEGER = (1 << 53) - 1


class DatasetRunner(Protocol):
    """The subset of the LightCPVerifier adapter used by this module."""

    def custom_test(
        self,
        language: str,
        code: str,
        stdin: str,
        **kwargs: Any,
    ) -> CustomTestResult: ...

    def custom_test_batch(
        self,
        language: str,
        code: str,
        tests: Sequence[CustomTestBatchCase],
        **kwargs: Any,
    ) -> CustomTestBatchResult: ...


def _frozen_text_mapping(
    value: Mapping[str, str] | None,
) -> Mapping[str, str]:
    if value is None:
        return MappingProxyType({})
    copied: dict[str, str] = {}
    for name, content in value.items():
        if not isinstance(name, str) or not isinstance(content, str):
            raise TypeError("dataset copy-in file names and contents must be strings")
        copied[name] = content
    return MappingProxyType(copied)


def _tuple_or_none(value: Sequence[str] | None) -> tuple[str, ...] | None:
    if value is None:
        return None
    result = tuple(value)
    if not all(isinstance(item, str) for item in result):
        raise TypeError("dataset argv items must be strings")
    return result


def _validate_source_name(value: str | None, owner: str) -> None:
    if value is not None and not isinstance(value, str):
        raise TypeError(f"{owner} source_name must be a string or None")


def _validate_test_id(value: str | int | float | None) -> None:
    if value is None or isinstance(value, str):
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("dataset test id must be a string, finite number, or None")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("dataset test id must be finite")
    if float(value).is_integer() and abs(value) > _MAX_SAFE_JSON_INTEGER:
        raise ValueError("integer dataset test id must be JSON-safe")


@dataclass(frozen=True)
class DatasetProgram:
    """One source program evaluated over an ordered dataset."""

    language: str
    code: str
    source_name: str | None = None
    argv: Sequence[str] | None = None
    copy_in_files: Mapping[str, str] | None = None
    compile_copy_in_files: Mapping[str, str] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.language, str) or not self.language:
            raise ValueError("dataset program language must be non-empty")
        if not isinstance(self.code, str):
            raise TypeError("dataset program code must be a string")
        _validate_source_name(self.source_name, "dataset program")
        object.__setattr__(self, "argv", _tuple_or_none(self.argv))
        object.__setattr__(
            self, "copy_in_files", _frozen_text_mapping(self.copy_in_files)
        )
        object.__setattr__(
            self,
            "compile_copy_in_files",
            _frozen_text_mapping(self.compile_copy_in_files),
        )


@dataclass(frozen=True)
class DatasetValidator:
    """Optional input validator compiled once and run before every case."""

    code: str
    language: str | None = None
    source_name: str | None = None
    argv: Sequence[str] | None = None
    compile_copy_in_files: Mapping[str, str] | None = None
    time_limit_ms: int | None = None
    memory_limit_mb: int | None = None
    max_output_bytes: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.code, str):
            raise TypeError("dataset validator code must be a string")
        if self.language is not None and not isinstance(self.language, str):
            raise TypeError("dataset validator language must be a string or None")
        _validate_source_name(self.source_name, "dataset validator")
        object.__setattr__(self, "argv", _tuple_or_none(self.argv))
        object.__setattr__(
            self,
            "compile_copy_in_files",
            _frozen_text_mapping(self.compile_copy_in_files),
        )

    def as_custom_test_spec(self) -> CustomTestValidatorSpec:
        return CustomTestValidatorSpec(
            code=self.code,
            language=self.language,
            source_name=self.source_name,
            argv=self.argv,
            compile_copy_in_files=self.compile_copy_in_files,
            time_limit_ms=self.time_limit_ms,
            memory_limit_mb=self.memory_limit_mb,
            max_output_bytes=self.max_output_bytes,
        )


@dataclass(frozen=True)
class DatasetTest:
    """One input and optional oracle output in an ordered dataset."""

    stdin: str
    id: str | int | float | None = None
    expected_output: str | None = None
    argv: Sequence[str] | None = None
    copy_in_files: Mapping[str, str] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.stdin, str):
            raise TypeError("dataset stdin must be a string")
        if self.expected_output is not None and not isinstance(
            self.expected_output, str
        ):
            raise TypeError("dataset expected_output must be a string or None")
        _validate_test_id(self.id)
        object.__setattr__(self, "argv", _tuple_or_none(self.argv))
        object.__setattr__(
            self, "copy_in_files", _frozen_text_mapping(self.copy_in_files)
        )


@dataclass(frozen=True)
class DatasetCompilation:
    status: str
    ok: bool
    diagnostic: str
    source_sha256: str
    cached: bool
    time_ms: int
    requested_time_limit_ms: int
    effective_time_limit_ms: int | None
    effective_wall_time_limit_ms: int | None
    requested_memory_limit_mb: int
    effective_memory_limit_mb: int | None
    requested_max_output_bytes: int
    effective_max_output_bytes: int | None
    compiler_cpu_time_limit_ms: int | None
    compiler_memory_limit_mb: int | None
    compiler_process_limit: int | None
    raw: CustomTestResult | None = None


@dataclass(frozen=True)
class DatasetChunk:
    index: int
    start: int
    stop: int
    request_bytes_estimate: int
    status: str
    ok: bool
    total: int
    valid: int
    invalid: int
    validator_errors: int
    output_truncated: bool
    captured_output_bytes: int
    max_batch_output_bytes: int
    effective_time_limit_ms: int
    effective_wall_time_limit_ms: int | None
    effective_memory_limit_mb: int
    effective_max_output_bytes: int
    effective_validator_time_limit_ms: int | None
    effective_validator_wall_time_limit_ms: int | None
    effective_validator_memory_limit_mb: int | None
    effective_validator_max_output_bytes: int | None


@dataclass(frozen=True)
class DatasetCaseResult:
    index: int
    id: str | int | float | None
    verdict: str
    data_status: str
    executed: bool
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
    test_spec_sha256: str
    input_sha256: str
    expected_output_sha256: str | None
    stdout_sha256: str
    comparison: str
    comparison_match: bool | None
    diagnostic: str
    raw: CustomTestBatchCaseResult | None = None


@dataclass(frozen=True)
class DatasetEvaluation:
    status: str
    ok: bool
    evaluation_complete: bool
    error: str | None
    comparison: str
    program_language: str
    program_source_name: str | None
    source_sha256: str
    program_compile_files_sha256: str
    program_runtime_sha256: str
    validator_sha256: str | None
    validator_language: str | None
    validator_source_name: str | None
    validator_compile_files_sha256: str | None
    validator_runtime_sha256: str | None
    compilation: DatasetCompilation
    cases: tuple[DatasetCaseResult, ...]
    chunks: tuple[DatasetChunk, ...]
    requested_time_limit_ms: int
    effective_time_limit_ms: int | None
    effective_wall_time_limit_ms: int | None
    requested_memory_limit_mb: int
    effective_memory_limit_mb: int | None
    max_output_bytes: int
    effective_max_output_bytes: int | None
    max_batch_output_bytes: int | None
    requested_validator_time_limit_ms: int | None
    effective_validator_time_limit_ms: int | None
    effective_validator_wall_time_limit_ms: int | None
    requested_validator_memory_limit_mb: int | None
    effective_validator_memory_limit_mb: int | None
    requested_validator_max_output_bytes: int | None
    effective_validator_max_output_bytes: int | None
    batch_size: int
    max_request_bytes: int

    def to_receipt(self) -> dict[str, Any]:
        """Return a JSON-serializable, output-size-bounded evaluation receipt."""

        counts: dict[str, int] = {}
        for case in self.cases:
            counts[case.verdict] = counts.get(case.verdict, 0) + 1
        return {
            "schema_version": 1,
            "kind": "cpideas.program_dataset_evaluation",
            "status": self.status,
            "ok": self.ok,
            "evaluation_complete": self.evaluation_complete,
            "error": None if self.error is None else _preview(self.error),
            "comparison": self.comparison,
            "program": {
                "language": self.program_language,
                "source_name": self.program_source_name,
                "source_sha256": self.source_sha256,
                "compile_files_sha256": self.program_compile_files_sha256,
                "runtime_spec_sha256": self.program_runtime_sha256,
            },
            "validator": (
                None
                if self.validator_sha256 is None
                else {
                    "language": self.validator_language,
                    "source_name": self.validator_source_name,
                    "source_sha256": self.validator_sha256,
                    "compile_files_sha256": self.validator_compile_files_sha256,
                    "runtime_spec_sha256": self.validator_runtime_sha256,
                }
            ),
            "configuration": {
                "requested_time_limit_ms": self.requested_time_limit_ms,
                "effective_time_limit_ms": self.effective_time_limit_ms,
                "effective_wall_time_limit_ms": (
                    self.effective_wall_time_limit_ms
                ),
                "requested_memory_limit_mb": self.requested_memory_limit_mb,
                "effective_memory_limit_mb": self.effective_memory_limit_mb,
                "requested_max_output_bytes": self.max_output_bytes,
                "effective_max_output_bytes": self.effective_max_output_bytes,
                "max_batch_output_bytes": self.max_batch_output_bytes,
                "validator_limits": (
                    None
                    if self.validator_sha256 is None
                    else {
                        "requested_time_limit_ms": (
                            self.requested_validator_time_limit_ms
                        ),
                        "effective_time_limit_ms": (
                            self.effective_validator_time_limit_ms
                        ),
                        "effective_wall_time_limit_ms": (
                            self.effective_validator_wall_time_limit_ms
                        ),
                        "requested_memory_limit_mb": (
                            self.requested_validator_memory_limit_mb
                        ),
                        "effective_memory_limit_mb": (
                            self.effective_validator_memory_limit_mb
                        ),
                        "requested_max_output_bytes": (
                            self.requested_validator_max_output_bytes
                        ),
                        "effective_max_output_bytes": (
                            self.effective_validator_max_output_bytes
                        ),
                    }
                ),
                "chunk_count": len(self.chunks),
                "batch_size": self.batch_size,
                "max_request_bytes": self.max_request_bytes,
            },
            "compilation": {
                "status": self.compilation.status,
                "ok": self.compilation.ok,
                "diagnostic": _preview(self.compilation.diagnostic),
                "cached": self.compilation.cached,
                "time_ms": self.compilation.time_ms,
                "runtime_profile_for_subsequent_execution": {
                    "requested_time_limit_ms": (
                        self.compilation.requested_time_limit_ms
                    ),
                    "effective_time_limit_ms": (
                        self.compilation.effective_time_limit_ms
                    ),
                    "effective_wall_time_limit_ms": (
                        self.compilation.effective_wall_time_limit_ms
                    ),
                    "requested_memory_limit_mb": (
                        self.compilation.requested_memory_limit_mb
                    ),
                    "effective_memory_limit_mb": (
                        self.compilation.effective_memory_limit_mb
                    ),
                    "requested_max_output_bytes": (
                        self.compilation.requested_max_output_bytes
                    ),
                    "effective_max_output_bytes": (
                        self.compilation.effective_max_output_bytes
                    ),
                },
                "compiler_limits": {
                    "cpu_time_ms": self.compilation.compiler_cpu_time_limit_ms,
                    "memory_mb": self.compilation.compiler_memory_limit_mb,
                    "process_limit": self.compilation.compiler_process_limit,
                },
            },
            "summary": {
                "total": len(self.cases),
                "verdict_counts": dict(sorted(counts.items())),
            },
            "chunks": [
                {
                    "index": chunk.index,
                    "start": chunk.start,
                    "stop": chunk.stop,
                    "request_bytes_estimate": chunk.request_bytes_estimate,
                    "status": chunk.status,
                    "ok": chunk.ok,
                    "total": chunk.total,
                    "valid": chunk.valid,
                    "invalid": chunk.invalid,
                    "validator_errors": chunk.validator_errors,
                    "output_truncated": chunk.output_truncated,
                    "captured_output_bytes": chunk.captured_output_bytes,
                    "max_batch_output_bytes": chunk.max_batch_output_bytes,
                    "effective_time_limit_ms": chunk.effective_time_limit_ms,
                    "effective_wall_time_limit_ms": (
                        chunk.effective_wall_time_limit_ms
                    ),
                    "effective_memory_limit_mb": chunk.effective_memory_limit_mb,
                    "effective_max_output_bytes": (
                        chunk.effective_max_output_bytes
                    ),
                    "effective_validator_time_limit_ms": (
                        chunk.effective_validator_time_limit_ms
                    ),
                    "effective_validator_wall_time_limit_ms": (
                        chunk.effective_validator_wall_time_limit_ms
                    ),
                    "effective_validator_memory_limit_mb": (
                        chunk.effective_validator_memory_limit_mb
                    ),
                    "effective_validator_max_output_bytes": (
                        chunk.effective_validator_max_output_bytes
                    ),
                }
                for chunk in self.chunks
            ],
            "cases": [
                {
                    "index": case.index,
                    "id": case.id,
                    "verdict": case.verdict,
                    "data_status": case.data_status,
                    "executed": case.executed,
                    "exit_status": case.exit_status,
                    "signal": case.signal,
                    "time_ns": case.time_ns,
                    "time_ms": case.time_ms,
                    "memory_bytes": case.memory_bytes,
                    "raw_status": case.raw_status,
                    "cached": case.cached,
                    "output_truncated": case.output_truncated,
                    "test_spec_sha256": case.test_spec_sha256,
                    "input_sha256": case.input_sha256,
                    "expected_output_sha256": case.expected_output_sha256,
                    "stdout_sha256": case.stdout_sha256,
                    "stdout_bytes": len(case.stdout.encode("utf-8")),
                    "stderr_preview": _preview(case.stderr),
                    "comparison_match": case.comparison_match,
                    "diagnostic": _preview(case.diagnostic),
                }
                for case in self.cases
            ],
        }

    @property
    def receipt(self) -> dict[str, Any]:
        return self.to_receipt()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _mapping_sha256(value: Mapping[str, str] | None) -> str:
    return _canonical_sha256(dict(value or {}))


def _test_spec_sha256(test: DatasetTest) -> str:
    return _canonical_sha256(
        {
            "stdin": test.stdin,
            "expected_output": test.expected_output,
            "argv": None if test.argv is None else list(test.argv),
            "copy_in_files": dict(test.copy_in_files or {}),
        }
    )


def _preview(value: str, limit: int = 1000) -> str:
    return value if len(value) <= limit else value[:limit] + "\n... truncated ..."


def _normalized_status(value: str | None) -> str:
    return " ".join(
        (value or "").lower().replace("_", " ").replace("-", " ").split()
    )


def _compare(actual: str, expected: str, comparison: str) -> bool:
    if comparison == "exact":
        return actual == expected
    if comparison == "tokens":
        return actual.split() == expected.split()
    raise ValueError(f"comparison {comparison!r} does not compare outputs")


def _data_status(raw: CustomTestBatchCaseResult) -> str:
    status = raw.validation.status
    if status == "invalid_test_data":
        return "INVALID_TEST_DATA"
    if status == "validator_error":
        return "VALIDATOR_ERROR"
    if status == "not_run":
        return "NOT_RUN"
    if status in {"valid", "not_requested"}:
        return "VALID"
    return status.upper() or "UNKNOWN"


def _classify_case(
    index: int,
    test: DatasetTest,
    raw: CustomTestBatchCaseResult,
    comparison: str,
    *,
    validator_requested: bool,
) -> DatasetCaseResult:
    data_status = _data_status(raw)
    output_truncated = bool(
        raw.output_truncated or raw.validation.output_truncated
    )
    match: bool | None = None
    diagnostic = raw.stderr or raw.validation.stderr
    if output_truncated:
        verdict = "INFRA"
        diagnostic = diagnostic or "LightCPVerifier response output was truncated"
    elif raw.status == "invalid_test_data" or data_status == "INVALID_TEST_DATA":
        verdict = "INVALID_TEST_DATA"
    elif raw.status == "validator_error" or data_status == "VALIDATOR_ERROR":
        verdict = "VALIDATOR_ERROR"
    elif validator_requested and raw.validation.status == "not_requested":
        verdict = "INFRA"
        diagnostic = "validator was requested but the result says not_requested"
    elif not validator_requested and raw.validation.status != "not_requested":
        verdict = "INFRA"
        diagnostic = (
            "validator was not requested but the response contains status="
            f"{raw.validation.status}"
        )
    elif data_status != "VALID":
        verdict = "INFRA"
        diagnostic = diagnostic or (
            f"unrecognized validator/data status: {raw.validation.status}"
        )
    elif not raw.executed:
        verdict = "INFRA"
        diagnostic = diagnostic or f"program was not executed (status={raw.status})"
    else:
        normalized = _normalized_status(raw.raw_status or raw.status)
        if normalized in {
            "tle",
            "time limit",
            "time limit exceeded",
            "timed out",
            "timeout",
        }:
            verdict = "TLE"
        elif normalized in {"mle", "memory limit", "memory limit exceeded"}:
            verdict = "MLE"
        elif normalized in {"ole", "output limit", "output limit exceeded"}:
            verdict = "OLE"
        elif normalized in {
            "internal error",
            "infrastructure error",
            "system error",
            "unknown",
        }:
            verdict = "INFRA"
            diagnostic = diagnostic or f"execution infrastructure status: {raw.status}"
        elif raw.exit_status is None and raw.signal is None:
            verdict = "INFRA"
            diagnostic = diagnostic or f"execution returned no exit status: {raw.status}"
        elif raw.exit_status != 0 or not raw.ok:
            verdict = "RE"
        elif comparison == "none":
            verdict = "EXECUTED"
        else:
            expected = test.expected_output
            if expected is None:  # guarded by evaluate(), kept fail-closed here
                verdict = "INFRA"
                diagnostic = "expected output is missing"
            else:
                match = _compare(raw.stdout, expected, comparison)
                verdict = "AC" if match else "WA"
    return DatasetCaseResult(
        index=index,
        id=test.id,
        verdict=verdict,
        data_status=data_status,
        executed=raw.executed,
        stdout=raw.stdout,
        stderr=raw.stderr,
        exit_status=raw.exit_status,
        signal=raw.signal,
        time_ns=raw.time_ns,
        time_ms=raw.time_ms,
        memory_bytes=raw.memory_bytes,
        raw_status=raw.raw_status,
        cached=raw.cached,
        output_truncated=output_truncated,
        test_spec_sha256=_test_spec_sha256(test),
        input_sha256=_sha256_text(test.stdin),
        expected_output_sha256=(
            None
            if test.expected_output is None
            else _sha256_text(test.expected_output)
        ),
        stdout_sha256=_sha256_text(raw.stdout),
        comparison=comparison,
        comparison_match=match,
        diagnostic=diagnostic,
        raw=raw,
    )


def _not_executed_case(
    index: int,
    test: DatasetTest,
    comparison: str,
    diagnostic: str,
    *,
    data_status: str = "NOT_RUN",
    verdict: str = "NOT_EXECUTED",
) -> DatasetCaseResult:
    return DatasetCaseResult(
        index=index,
        id=test.id,
        verdict=verdict,
        data_status=data_status,
        executed=False,
        stdout="",
        stderr="",
        exit_status=None,
        signal=None,
        time_ns=0,
        time_ms=0,
        memory_bytes=0,
        raw_status=None,
        cached=False,
        output_truncated=False,
        test_spec_sha256=_test_spec_sha256(test),
        input_sha256=_sha256_text(test.stdin),
        expected_output_sha256=(
            None
            if test.expected_output is None
            else _sha256_text(test.expected_output)
        ),
        stdout_sha256=_sha256_text(""),
        comparison=comparison,
        comparison_match=None,
        diagnostic=diagnostic,
        raw=None,
    )


class DatasetEvaluator:
    """Evaluate one program over an ordered dataset without host fallback."""

    def __init__(
        self,
        runner: DatasetRunner,
        *,
        batch_size: int = DEFAULT_DATASET_BATCH_SIZE,
        max_request_bytes: int = DEFAULT_DATASET_REQUEST_BYTES,
    ) -> None:
        if not isinstance(batch_size, int) or not 1 <= batch_size <= 128:
            raise ValueError("dataset batch_size must be between 1 and 128")
        if not isinstance(max_request_bytes, int) or max_request_bytes < 1024:
            raise ValueError("dataset max_request_bytes must be at least 1024")
        self.runner = runner
        self.batch_size = batch_size
        self.max_request_bytes = max_request_bytes

    def compile(
        self,
        program: DatasetProgram,
        *,
        time_limit_ms: int = 2000,
        memory_limit_mb: int = 256,
        max_output_bytes: int = 1024 * 1024,
    ) -> DatasetCompilation:
        source_sha256 = _sha256_text(program.code)
        try:
            raw = self.runner.custom_test(
                language=program.language,
                code=program.code,
                stdin="",
                time_limit_ms=time_limit_ms,
                memory_limit_mb=memory_limit_mb,
                max_output_bytes=max_output_bytes,
                argv=program.argv,
                copy_in_files=program.copy_in_files,
                compile_copy_in_files=program.compile_copy_in_files,
                compile_only=True,
                source_name=program.source_name,
            )
        except Exception as exc:
            return DatasetCompilation(
                status="INFRA_ERROR",
                ok=False,
                diagnostic=f"{type(exc).__name__}: {exc}",
                source_sha256=source_sha256,
                cached=False,
                time_ms=0,
                requested_time_limit_ms=time_limit_ms,
                effective_time_limit_ms=None,
                effective_wall_time_limit_ms=None,
                requested_memory_limit_mb=memory_limit_mb,
                effective_memory_limit_mb=None,
                requested_max_output_bytes=max_output_bytes,
                effective_max_output_bytes=None,
                compiler_cpu_time_limit_ms=None,
                compiler_memory_limit_mb=None,
                compiler_process_limit=None,
                raw=None,
            )
        diagnostic = raw.stderr or str(raw.payload.get("compileError", ""))
        normalized_compile_status = _normalized_status(raw.status)
        status = "COMPILED" if raw.ok else "COMPILE_ERROR"
        if raw.ok and normalized_compile_status != "compiled":
            status = "INFRA_ERROR"
        elif not raw.ok and normalized_compile_status not in {
            "compile error",
            "compilation error",
        }:
            status = "INFRA_ERROR"
        return DatasetCompilation(
            status=status,
            ok=raw.ok,
            diagnostic=diagnostic,
            source_sha256=source_sha256,
            cached=bool(raw.payload.get("cached", False)),
            time_ms=raw.time_ms,
            requested_time_limit_ms=time_limit_ms,
            effective_time_limit_ms=raw.time_limit_ms,
            effective_wall_time_limit_ms=raw.wall_time_limit_ms,
            requested_memory_limit_mb=memory_limit_mb,
            effective_memory_limit_mb=raw.memory_limit_mb,
            requested_max_output_bytes=max_output_bytes,
            effective_max_output_bytes=raw.max_output_bytes,
            compiler_cpu_time_limit_ms=raw.compilation_cpu_time_limit_ms,
            compiler_memory_limit_mb=raw.compilation_memory_limit_mb,
            compiler_process_limit=raw.compilation_process_limit,
            raw=raw,
        )

    def evaluate(
        self,
        program: DatasetProgram,
        tests: Sequence[DatasetTest],
        *,
        comparison: str = "none",
        validator: DatasetValidator | None = None,
        time_limit_ms: int = 2000,
        memory_limit_mb: int = 256,
        max_output_bytes: int = 1024 * 1024,
    ) -> DatasetEvaluation:
        if comparison not in DATASET_COMPARISONS:
            raise ValueError(f"comparison must be one of {DATASET_COMPARISONS}")
        ordered = tuple(tests)
        if not ordered:
            raise ValueError("dataset tests must be non-empty")
        if not all(isinstance(test, DatasetTest) for test in ordered):
            raise TypeError("dataset tests must be DatasetTest instances")
        if comparison != "none" and any(
            test.expected_output is None for test in ordered
        ):
            raise ValueError(
                f"comparison={comparison!r} requires expected_output for every test"
            )
        chunks = self._chunk_tests(program, ordered, validator)
        compilation = self.compile(
            program,
            time_limit_ms=time_limit_ms,
            memory_limit_mb=memory_limit_mb,
            max_output_bytes=max_output_bytes,
        )
        validator_sha256 = (
            None if validator is None else _sha256_text(validator.code)
        )
        validator_compile_files_sha256 = (
            None
            if validator is None
            else _mapping_sha256(validator.compile_copy_in_files)
        )
        validator_language = (
            None
            if validator is None
            else (validator.language or program.language)
        )
        validator_source_name = (
            None if validator is None else validator.source_name
        )
        validator_runtime_sha256 = (
            None
            if validator is None
            else _canonical_sha256(
                {
                    "argv": None
                    if validator.argv is None
                    else list(validator.argv),
                    "time_limit_ms": validator.time_limit_ms,
                    "memory_limit_mb": validator.memory_limit_mb,
                    "max_output_bytes": validator.max_output_bytes,
                }
            )
        )
        requested_validator_time_limit_ms = (
            None
            if validator is None
            else (
                time_limit_ms
                if validator.time_limit_ms is None
                else validator.time_limit_ms
            )
        )
        requested_validator_memory_limit_mb = (
            None
            if validator is None
            else (
                memory_limit_mb
                if validator.memory_limit_mb is None
                else validator.memory_limit_mb
            )
        )
        requested_validator_max_output_bytes = (
            None
            if validator is None
            else (
                max_output_bytes
                if validator.max_output_bytes is None
                else validator.max_output_bytes
            )
        )
        if not compilation.ok:
            diagnostic = compilation.diagnostic or compilation.status
            cases = tuple(
                _not_executed_case(index, test, comparison, diagnostic)
                for index, test in enumerate(ordered)
            )
            return self._evaluation(
                status=(
                    "compile_error"
                    if compilation.status == "COMPILE_ERROR"
                    else "infrastructure_error"
                ),
                complete=False,
                error=diagnostic,
                comparison=comparison,
                program=program,
                validator_sha256=validator_sha256,
                validator_language=validator_language,
                validator_source_name=validator_source_name,
                validator_compile_files_sha256=validator_compile_files_sha256,
                validator_runtime_sha256=validator_runtime_sha256,
                compilation=compilation,
                cases=cases,
                chunks=(),
                time_limit_ms=time_limit_ms,
                memory_limit_mb=memory_limit_mb,
                max_output_bytes=max_output_bytes,
                effective_time_limit_ms=None,
                effective_wall_time_limit_ms=None,
                effective_memory_limit_mb=None,
                effective_max_output_bytes=None,
                max_batch_output_bytes=None,
                requested_validator_time_limit_ms=(
                    requested_validator_time_limit_ms
                ),
                effective_validator_time_limit_ms=None,
                effective_validator_wall_time_limit_ms=None,
                requested_validator_memory_limit_mb=(
                    requested_validator_memory_limit_mb
                ),
                effective_validator_memory_limit_mb=None,
                requested_validator_max_output_bytes=(
                    requested_validator_max_output_bytes
                ),
                effective_validator_max_output_bytes=None,
            )

        case_results: list[DatasetCaseResult] = []
        chunk_records: list[DatasetChunk] = []
        terminal_status: str | None = None
        terminal_diagnostic = ""
        next_index = 0
        effective_time_limit_ms: int | None = None
        effective_wall_time_limit_ms: int | None = None
        effective_memory_limit_mb: int | None = None
        effective_max_output_bytes: int | None = None
        max_batch_output_bytes: int | None = None
        effective_validator_time_limit_ms: int | None = None
        effective_validator_wall_time_limit_ms: int | None = None
        effective_validator_memory_limit_mb: int | None = None
        effective_validator_max_output_bytes: int | None = None
        for chunk_index, (start, stop, estimate) in enumerate(chunks):
            request_tests = [
                CustomTestBatchCase(
                    stdin=test.stdin,
                    id=test.id,
                    argv=test.argv,
                    copy_in_files=test.copy_in_files,
                )
                for test in ordered[start:stop]
            ]
            try:
                batch = self.runner.custom_test_batch(
                    language=program.language,
                    code=program.code,
                    tests=request_tests,
                    time_limit_ms=time_limit_ms,
                    memory_limit_mb=memory_limit_mb,
                    max_output_bytes=max_output_bytes,
                    argv=program.argv,
                    copy_in_files=program.copy_in_files,
                    compile_copy_in_files=program.compile_copy_in_files,
                    source_name=program.source_name,
                    validator=(
                        None if validator is None else validator.as_custom_test_spec()
                    ),
                )
            except Exception as exc:
                terminal_status = "infrastructure_error"
                terminal_diagnostic = f"{type(exc).__name__}: {exc}"
                break
            chunk_records.append(
                DatasetChunk(
                    index=chunk_index,
                    start=start,
                    stop=stop,
                    request_bytes_estimate=estimate,
                    status=batch.status,
                    ok=batch.ok,
                    total=batch.total,
                    valid=batch.valid,
                    invalid=batch.invalid,
                    validator_errors=batch.validator_errors,
                    output_truncated=batch.output_truncated,
                    captured_output_bytes=batch.captured_output_bytes,
                    max_batch_output_bytes=batch.max_batch_output_bytes,
                    effective_time_limit_ms=batch.time_limit_ms,
                    effective_wall_time_limit_ms=batch.wall_time_limit_ms,
                    effective_memory_limit_mb=batch.memory_limit_mb,
                    effective_max_output_bytes=batch.max_output_bytes,
                    effective_validator_time_limit_ms=(
                        batch.validator_time_limit_ms
                    ),
                    effective_validator_wall_time_limit_ms=(
                        batch.validator_wall_time_limit_ms
                    ),
                    effective_validator_memory_limit_mb=(
                        batch.validator_memory_limit_mb
                    ),
                    effective_validator_max_output_bytes=(
                        batch.validator_max_output_bytes
                    ),
                )
            )
            observed_limits = (
                batch.time_limit_ms,
                batch.wall_time_limit_ms,
                batch.memory_limit_mb,
                batch.max_output_bytes,
                batch.max_batch_output_bytes,
                batch.validator_time_limit_ms,
                batch.validator_wall_time_limit_ms,
                batch.validator_memory_limit_mb,
                batch.validator_max_output_bytes,
            )
            expected_limits = (
                effective_time_limit_ms,
                effective_wall_time_limit_ms,
                effective_memory_limit_mb,
                effective_max_output_bytes,
                max_batch_output_bytes,
                effective_validator_time_limit_ms,
                effective_validator_wall_time_limit_ms,
                effective_validator_memory_limit_mb,
                effective_validator_max_output_bytes,
            )
            limits_mismatch = False
            if any(
                value is not None and value <= 0 for value in observed_limits
            ) or (
                validator is not None
                and any(value is None for value in observed_limits[-4:])
            ):
                terminal_status = "infrastructure_error"
                terminal_diagnostic = (
                    f"batch returned invalid effective limits: {observed_limits}"
                )
                break
            if all(value is None for value in expected_limits):
                (
                    effective_time_limit_ms,
                    effective_wall_time_limit_ms,
                    effective_memory_limit_mb,
                    effective_max_output_bytes,
                    max_batch_output_bytes,
                    effective_validator_time_limit_ms,
                    effective_validator_wall_time_limit_ms,
                    effective_validator_memory_limit_mb,
                    effective_validator_max_output_bytes,
                ) = observed_limits
            elif observed_limits != expected_limits:
                limits_mismatch = True
            if batch.compile_error:
                terminal_status = "compile_error"
                terminal_diagnostic = batch.compile_error
                break
            if batch.validator_compile_error:
                terminal_status = "validator_error"
                terminal_diagnostic = batch.validator_compile_error
                break
            if batch.total != stop - start or len(batch.results) != stop - start:
                terminal_status = "infrastructure_error"
                terminal_diagnostic = (
                    f"batch result length mismatch: expected {stop - start}, "
                    f"reported total={batch.total}, got {len(batch.results)} results"
                )
                break
            if (
                batch.captured_output_bytes < 0
                or batch.captured_output_bytes > batch.max_batch_output_bytes
            ):
                terminal_status = "infrastructure_error"
                terminal_diagnostic = (
                    "batch captured-output accounting is invalid: "
                    f"captured={batch.captured_output_bytes}, "
                    f"budget={batch.max_batch_output_bytes}"
                )
                break
            validation_statuses = [
                item.validation.status for item in batch.results
            ]
            expected_valid = (
                len(batch.results)
                if validator is None
                else validation_statuses.count("valid")
            )
            expected_invalid = validation_statuses.count("invalid_test_data")
            expected_validator_errors = validation_statuses.count(
                "validator_error"
            )
            if (
                batch.valid,
                batch.invalid,
                batch.validator_errors,
            ) != (
                expected_valid,
                expected_invalid,
                expected_validator_errors,
            ):
                terminal_status = "infrastructure_error"
                terminal_diagnostic = (
                    "batch validator counters are inconsistent with per-case "
                    "results: reported "
                    f"{(batch.valid, batch.invalid, batch.validator_errors)}, "
                    "expected "
                    f"{(expected_valid, expected_invalid, expected_validator_errors)}"
                )
                break
            chunk_case_start = len(case_results)
            for offset, (test, raw) in enumerate(
                zip(ordered[start:stop], batch.results)
            ):
                if raw.index != offset or raw.id != test.id:
                    terminal_status = "infrastructure_error"
                    terminal_diagnostic = (
                        "batch result identity mismatch: "
                        f"expected index/id {offset}/{test.id!r}, "
                        f"got {raw.index}/{raw.id!r}"
                    )
                    break
                case_results.append(
                    _classify_case(
                        start + offset,
                        test,
                        raw,
                        comparison,
                        validator_requested=validator is not None,
                    )
                )
                next_index = start + offset + 1
            if terminal_status is not None:
                break
            chunk_cases = case_results[chunk_case_start:]
            expected_batch_ok = (
                not batch.output_truncated
                and all(item.ok for item in batch.results)
            )
            expected_batch_status = (
                "completed_with_truncated_output"
                if batch.output_truncated
                else "completed"
            )
            if limits_mismatch:
                terminal_status = "infrastructure_error"
                terminal_diagnostic = (
                    "effective execution limits changed between chunks: "
                    f"expected {expected_limits}, got {observed_limits}"
                )
            elif batch.ok != expected_batch_ok:
                terminal_status = "infrastructure_error"
                terminal_diagnostic = (
                    "batch ok flag is inconsistent with its per-case results: "
                    f"reported {batch.ok}, expected {expected_batch_ok}"
                )
            elif batch.status != expected_batch_status:
                terminal_status = "infrastructure_error"
                terminal_diagnostic = (
                    "batch status is inconsistent with truncation metadata: "
                    f"reported {batch.status!r}, expected "
                    f"{expected_batch_status!r}"
                )
            elif batch.output_truncated and not any(
                case.output_truncated for case in chunk_cases
            ):
                terminal_status = "infrastructure_error"
                terminal_diagnostic = (
                    "batch reported truncated output without identifying an "
                    "affected case"
                )
            if terminal_status is not None:
                case_results[chunk_case_start:] = [
                    replace(
                        case,
                        verdict="INFRA",
                        output_truncated=(
                            case.output_truncated or batch.output_truncated
                        ),
                        comparison_match=None,
                        diagnostic=terminal_diagnostic,
                    )
                    for case in chunk_cases
                ]
                next_index = stop
                break

        if terminal_status is not None:
            data_status = (
                "VALIDATOR_ERROR"
                if terminal_status == "validator_error"
                else "NOT_RUN"
            )
            case_results.extend(
                _not_executed_case(
                    index,
                    ordered[index],
                    comparison,
                    terminal_diagnostic,
                    data_status=data_status,
                )
                for index in range(next_index, len(ordered))
            )
            return self._evaluation(
                status=terminal_status,
                complete=False,
                error=terminal_diagnostic,
                comparison=comparison,
                program=program,
                validator_sha256=validator_sha256,
                validator_language=validator_language,
                validator_source_name=validator_source_name,
                validator_compile_files_sha256=validator_compile_files_sha256,
                validator_runtime_sha256=validator_runtime_sha256,
                compilation=compilation,
                cases=tuple(case_results),
                chunks=tuple(chunk_records),
                time_limit_ms=time_limit_ms,
                memory_limit_mb=memory_limit_mb,
                max_output_bytes=max_output_bytes,
                effective_time_limit_ms=effective_time_limit_ms,
                effective_wall_time_limit_ms=effective_wall_time_limit_ms,
                effective_memory_limit_mb=effective_memory_limit_mb,
                effective_max_output_bytes=effective_max_output_bytes,
                max_batch_output_bytes=max_batch_output_bytes,
                requested_validator_time_limit_ms=(
                    requested_validator_time_limit_ms
                ),
                effective_validator_time_limit_ms=(
                    effective_validator_time_limit_ms
                ),
                effective_validator_wall_time_limit_ms=(
                    effective_validator_wall_time_limit_ms
                ),
                requested_validator_memory_limit_mb=(
                    requested_validator_memory_limit_mb
                ),
                effective_validator_memory_limit_mb=(
                    effective_validator_memory_limit_mb
                ),
                requested_validator_max_output_bytes=(
                    requested_validator_max_output_bytes
                ),
                effective_validator_max_output_bytes=(
                    effective_validator_max_output_bytes
                ),
            )

        incomplete = any(
            case.verdict in {"INFRA", "VALIDATOR_ERROR", "NOT_EXECUTED"}
            for case in case_results
        )
        return self._evaluation(
            status="incomplete" if incomplete else "completed",
            complete=not incomplete,
            error=None,
            comparison=comparison,
            program=program,
            validator_sha256=validator_sha256,
            validator_language=validator_language,
            validator_source_name=validator_source_name,
            validator_compile_files_sha256=validator_compile_files_sha256,
            validator_runtime_sha256=validator_runtime_sha256,
            compilation=compilation,
            cases=tuple(case_results),
            chunks=tuple(chunk_records),
            time_limit_ms=time_limit_ms,
            memory_limit_mb=memory_limit_mb,
            max_output_bytes=max_output_bytes,
            effective_time_limit_ms=effective_time_limit_ms,
            effective_wall_time_limit_ms=effective_wall_time_limit_ms,
            effective_memory_limit_mb=effective_memory_limit_mb,
            effective_max_output_bytes=effective_max_output_bytes,
            max_batch_output_bytes=max_batch_output_bytes,
            requested_validator_time_limit_ms=requested_validator_time_limit_ms,
            effective_validator_time_limit_ms=effective_validator_time_limit_ms,
            effective_validator_wall_time_limit_ms=(
                effective_validator_wall_time_limit_ms
            ),
            requested_validator_memory_limit_mb=(
                requested_validator_memory_limit_mb
            ),
            effective_validator_memory_limit_mb=(
                effective_validator_memory_limit_mb
            ),
            requested_validator_max_output_bytes=(
                requested_validator_max_output_bytes
            ),
            effective_validator_max_output_bytes=(
                effective_validator_max_output_bytes
            ),
        )

    def _evaluation(
        self,
        *,
        status: str,
        complete: bool,
        error: str | None,
        comparison: str,
        program: DatasetProgram,
        validator_sha256: str | None,
        validator_language: str | None,
        validator_source_name: str | None,
        validator_compile_files_sha256: str | None,
        validator_runtime_sha256: str | None,
        compilation: DatasetCompilation,
        cases: tuple[DatasetCaseResult, ...],
        chunks: tuple[DatasetChunk, ...],
        time_limit_ms: int,
        memory_limit_mb: int,
        max_output_bytes: int,
        effective_time_limit_ms: int | None,
        effective_wall_time_limit_ms: int | None,
        effective_memory_limit_mb: int | None,
        effective_max_output_bytes: int | None,
        max_batch_output_bytes: int | None,
        requested_validator_time_limit_ms: int | None,
        effective_validator_time_limit_ms: int | None,
        effective_validator_wall_time_limit_ms: int | None,
        requested_validator_memory_limit_mb: int | None,
        effective_validator_memory_limit_mb: int | None,
        requested_validator_max_output_bytes: int | None,
        effective_validator_max_output_bytes: int | None,
    ) -> DatasetEvaluation:
        success_verdict = "EXECUTED" if comparison == "none" else "AC"
        ok = complete and all(case.verdict == success_verdict for case in cases)
        return DatasetEvaluation(
            status=status,
            ok=ok,
            evaluation_complete=complete,
            error=error,
            comparison=comparison,
            program_language=program.language,
            program_source_name=program.source_name,
            source_sha256=_sha256_text(program.code),
            program_compile_files_sha256=_mapping_sha256(
                program.compile_copy_in_files
            ),
            program_runtime_sha256=_canonical_sha256(
                {
                    "argv": None if program.argv is None else list(program.argv),
                    "copy_in_files": dict(program.copy_in_files or {}),
                }
            ),
            validator_sha256=validator_sha256,
            validator_language=validator_language,
            validator_source_name=validator_source_name,
            validator_compile_files_sha256=validator_compile_files_sha256,
            validator_runtime_sha256=validator_runtime_sha256,
            compilation=compilation,
            cases=cases,
            chunks=chunks,
            requested_time_limit_ms=time_limit_ms,
            effective_time_limit_ms=effective_time_limit_ms,
            effective_wall_time_limit_ms=effective_wall_time_limit_ms,
            requested_memory_limit_mb=memory_limit_mb,
            effective_memory_limit_mb=effective_memory_limit_mb,
            max_output_bytes=max_output_bytes,
            effective_max_output_bytes=effective_max_output_bytes,
            max_batch_output_bytes=max_batch_output_bytes,
            requested_validator_time_limit_ms=requested_validator_time_limit_ms,
            effective_validator_time_limit_ms=effective_validator_time_limit_ms,
            effective_validator_wall_time_limit_ms=(
                effective_validator_wall_time_limit_ms
            ),
            requested_validator_memory_limit_mb=requested_validator_memory_limit_mb,
            effective_validator_memory_limit_mb=effective_validator_memory_limit_mb,
            requested_validator_max_output_bytes=(
                requested_validator_max_output_bytes
            ),
            effective_validator_max_output_bytes=(
                effective_validator_max_output_bytes
            ),
            batch_size=self.batch_size,
            max_request_bytes=self.max_request_bytes,
        )

    def _chunk_tests(
        self,
        program: DatasetProgram,
        tests: tuple[DatasetTest, ...],
        validator: DatasetValidator | None,
    ) -> list[tuple[int, int, int]]:
        base_size = self._request_bytes_estimate(program, (), validator)
        item_sizes = [self._test_request_bytes(test) for test in tests]
        chunks: list[tuple[int, int, int]] = []
        start = 0
        while start < len(tests):
            stop = start
            last_size = base_size
            while stop < len(tests) and stop - start < self.batch_size:
                separator = 1 if stop > start else 0
                size = last_size + separator + item_sizes[stop]
                if size > self.max_request_bytes:
                    if stop == start:
                        raise ValueError(
                            f"dataset test {start} cannot fit the configured "
                            f"max_request_bytes={self.max_request_bytes} "
                            f"(estimated {size} bytes)"
                        )
                    break
                stop += 1
                last_size = size
            chunks.append((start, stop, last_size))
            start = stop
        return chunks

    @staticmethod
    def _request_bytes_estimate(
        program: DatasetProgram,
        tests: Sequence[DatasetTest],
        validator: DatasetValidator | None,
    ) -> int:
        body: dict[str, Any] = {
            "lang": program.language,
            "code": program.code,
            "sourceName": program.source_name,
            "argv": list(program.argv or ()),
            "copyInFiles": dict(program.copy_in_files or {}),
            "compileCopyInFiles": dict(program.compile_copy_in_files or {}),
            "tests": [],
        }
        if validator is not None:
            body["validator"] = {
                "lang": validator.language,
                "code": validator.code,
                "sourceName": validator.source_name,
                "argv": list(validator.argv or ()),
                "compileCopyInFiles": dict(
                    validator.compile_copy_in_files or {}
                ),
            }
        base_size = len(
            json.dumps(body, ensure_ascii=True, separators=(",", ":")).encode(
                "utf-8"
            )
        )
        if not tests:
            return base_size
        return base_size + sum(
            DatasetEvaluator._test_request_bytes(test) for test in tests
        ) + len(tests) - 1

    @staticmethod
    def _test_request_bytes(test: DatasetTest) -> int:
        value = {
            "stdin": test.stdin,
            "id": test.id,
            "argv": None if test.argv is None else list(test.argv),
            "copyInFiles": dict(test.copy_in_files or {}),
        }
        return len(
            json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode(
                "utf-8"
            )
        )


__all__ = [
    "DATASET_API_REVISION",
    "DATASET_COMPARISONS",
    "DEFAULT_DATASET_BATCH_SIZE",
    "DEFAULT_DATASET_REQUEST_BYTES",
    "DatasetCaseResult",
    "DatasetChunk",
    "DatasetCompilation",
    "DatasetEvaluation",
    "DatasetEvaluator",
    "DatasetProgram",
    "DatasetRunner",
    "DatasetTest",
    "DatasetValidator",
]
