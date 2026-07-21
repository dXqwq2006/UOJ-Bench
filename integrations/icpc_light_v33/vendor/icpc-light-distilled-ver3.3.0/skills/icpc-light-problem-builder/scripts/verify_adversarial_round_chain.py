#!/usr/bin/env python3
"""Verify the append-only, hash-bound ICPC Light adversarial-round chain."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from regression_backend import (
    COMPILE_CONTEXT_POLICY_REVISION,
    LIGHTCP_CPP_PROFILE,
    compile_context_sha256,
)
from statement_resources import StatementResourceError, load_statement_resources


GENERATOR = "icpc-light-adversarial-round-recorder"
SCHEMA_VERSION = 1
RECEIPT_ROOT = PurePosixPath("audit/adversarial-round-receipts")
PLAN_ROOT = PurePosixPath("audit/adversarial-round-plans")
RECEIPT_RE = re.compile(r"round-(\d{2,})\.json")
HASH_RE = re.compile(r"[0-9a-f]{64}")
PLACEHOLDERS = {"", "-", "none", "n/a", "na", "tbd", "todo", "pending"}
BACKEND_EVIDENCE_SCHEMA_VERSION = 1
DATASET_API_REVISION = "cpideas-program-dataset-v1"


class ContractError(ValueError):
    pass


@dataclass
class ChainResult:
    issues: list[str]
    rounds: int
    killed: list[str]
    survivors: list[str]
    receipt_hashes: list[str]

    @property
    def passed(self) -> bool:
        return not self.issues

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "gate": "icpc-light-adversarial-round-chain",
            "status": "pass" if self.passed else "fail",
            "rounds": self.rounds,
            "killed": self.killed,
            "survivors": self.survivors,
            "receipt_hashes": self.receipt_hashes,
            "chain_sha256": canonical_digest(self.receipt_hashes),
            "issues": self.issues,
        }


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_digest(value: Any) -> str:
    data = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return sha256_bytes(data)


def compact_execution_program_result(
    container: dict[str, Any],
    *,
    execution_key: str,
    label: str,
    issues: list[str],
) -> dict[str, Any] | None:
    """Reconstruct the exact ProgramResult.compact payload saved by the backend."""

    execution = container.get(execution_key)
    if not isinstance(execution, dict):
        issues.append(f"{label}: route execution record is missing")
        return None
    timed_out = execution.get("timed_out")
    returncode = execution.get("exit_code")
    duration = execution.get("duration_seconds")
    memory_bytes = execution.get("memory_bytes")
    launch_error = execution.get("spawn_error")
    sandbox_verdict = execution.get("sandbox_verdict")
    sandbox_status = execution.get("sandbox_status")
    stdout = execution.get("stdout")
    stderr = execution.get("stderr")
    streams_valid = all(
        isinstance(stream, dict)
        and type(stream.get("size")) is int
        and stream["size"] >= 0
        and HASH_RE.fullmatch(str(stream.get("sha256", ""))) is not None
        and isinstance(stream.get("preview_utf8"), str)
        and type(stream.get("preview_truncated")) is bool
        for stream in (stdout, stderr)
    )
    if (
        type(timed_out) is not bool
        or (
            returncode is not None
            and (type(returncode) is not int)
        )
        or not isinstance(duration, (int, float))
        or isinstance(duration, bool)
        or not math.isfinite(float(duration))
        or duration < 0
        or type(memory_bytes) is not int
        or memory_bytes < 0
        or (launch_error is not None and not isinstance(launch_error, str))
        or (sandbox_verdict is not None and not isinstance(sandbox_verdict, str))
        or (sandbox_status is not None and not isinstance(sandbox_status, str))
        or not streams_valid
    ):
        issues.append(f"{label}: route execution record cannot bind a program result")
        return None
    assert isinstance(stdout, dict) and isinstance(stderr, dict)
    stderr_prefix = stderr["preview_utf8"].encode("utf-8")[:1000]
    compact: dict[str, Any] = {
        "returncode": returncode,
        "timed_out": timed_out,
        "duration_seconds": round(duration, 6),
        "memory_bytes": memory_bytes,
        "stderr_preview": stderr_prefix.decode("utf-8", errors="replace")
        + ("\n... truncated ..." if stderr["size"] > 1000 else ""),
    }
    if launch_error is not None:
        compact["launch_error"] = launch_error
    if sandbox_verdict is not None:
        compact["sandbox_verdict"] = sandbox_verdict
    if sandbox_status is not None:
        compact["sandbox_status"] = sandbox_status
    compact["stdout_sha256"] = stdout["sha256"]
    compact["stdout_bytes"] = stdout["size"]
    return compact


def compact_route_program_result(
    route: dict[str, Any], *, label: str, issues: list[str]
) -> dict[str, Any] | None:
    return compact_execution_program_result(
        route, execution_key="execution", label=label, issues=issues
    )


def validate_round_execution_evidence(
    receipt: dict[str, Any],
    backend: Any,
    routes: list[Any],
    *,
    label: str,
    issues: list[str],
) -> None:
    evidence = receipt.get("execution_backend_evidence")
    if not isinstance(backend, dict) or not isinstance(evidence, dict):
        issues.append(f"{label}: per-invocation backend evidence is missing")
        return
    if (
        backend.get("dataset_api_revision") != DATASET_API_REVISION
        or backend.get("execution_evidence_schema_version")
        != BACKEND_EVIDENCE_SCHEMA_VERSION
    ):
        issues.append(f"{label}: backend dataset/evidence API revision is invalid")
    adapter = Path(__file__).resolve().with_name("regression_backend.py")
    adapter_sha256 = (
        sha256_file(adapter)
        if adapter.is_file() and not adapter.is_symlink()
        else None
    )
    if (
        backend.get("adapter_sha256") != adapter_sha256
        or evidence.get("adapter_sha256") != adapter_sha256
    ):
        issues.append(f"{label}: regression backend adapter hash is stale")
    for key, expected in (
        ("schema_version", BACKEND_EVIDENCE_SCHEMA_VERSION),
        ("kind", "icpc-light.program-dataset-execution-evidence"),
        ("backend", "lightcpverifier"),
        ("sandboxed", True),
        ("testing_only", False),
        ("dataset_api_revision", DATASET_API_REVISION),
    ):
        if evidence.get(key) != expected:
            issues.append(f"{label}.execution_backend_evidence.{key}: expected {expected!r}")
    for key in ("service_identity", "client_module_sha256"):
        if evidence.get(key) != backend.get(key):
            issues.append(f"{label}: execution evidence {key} differs from backend config")
    invocations = evidence.get("invocations")
    if not isinstance(invocations, list):
        issues.append(f"{label}: execution evidence invocations must be an array")
        return
    expected_invocations: list[dict[str, Any]] = []
    checker_source = receipt.get("checker_source")
    for route in routes:
        if not isinstance(route, dict):
            continue
        route_id = route.get("route_id")
        expected_invocations.append(
            {
                "kind": "route",
                "container": route,
                "execution_key": "execution",
                "source": route.get("source"),
                "role": f"wrong:{route_id}",
                "case_id": route_id,
            }
        )
        checker_record = route.get("checker")
        if (
            isinstance(checker_record, dict)
            and checker_record.get("execution_backend") == "lightcpverifier"
        ):
            expected_invocations.append(
                {
                    "kind": "checker",
                    "container": route,
                    "execution_key": "checker",
                    "source": checker_source,
                    "role": "checker",
                    "case_id": f"checker:{route_id}",
                }
            )
    if (
        evidence.get("invocation_count") != len(invocations)
        or len(invocations) != len(expected_invocations)
    ):
        issues.append(
            f"{label}: execution evidence count differs from route/checker executions"
        )
    if evidence.get("invocations_sha256") != canonical_digest(invocations):
        issues.append(f"{label}: execution evidence invocation hash is invalid")
    service = backend.get("service_identity")
    policy = service.get("executionPolicy") if isinstance(service, dict) else None
    batch_policy = policy.get("batch") if isinstance(policy, dict) else None
    runtime_policy = policy.get("runtime") if isinstance(policy, dict) else None
    expected_budget = (
        batch_policy.get("maxCapturedOutputBytes")
        if isinstance(batch_policy, dict)
        else None
    )
    wall_multiplier = (
        runtime_policy.get("wallTimeMultiplier")
        if isinstance(runtime_policy, dict)
        else None
    )
    requested_time_ms = backend.get("requested_program_timeout_seconds")
    if isinstance(requested_time_ms, (int, float)) and not isinstance(
        requested_time_ms, bool
    ):
        requested_time_ms = round(requested_time_ms * 1000)
    else:
        requested_time_ms = None
    configured_time_ms = backend.get("sandbox_effective_time_limit_seconds")
    if isinstance(configured_time_ms, (int, float)) and not isinstance(configured_time_ms, bool):
        configured_time_ms = round(configured_time_ms * 1000)
    else:
        configured_time_ms = None
    forbidden = {"INFRA", "VALIDATOR_ERROR", "INVALID_TEST_DATA", "NOT_EXECUTED"}
    for index, invocation in enumerate(invocations):
        item_label = f"{label} execution invocation[{index}]"
        if not isinstance(invocation, dict):
            issues.append(f"{item_label}: must be an object")
            continue
        core = dict(invocation)
        digest = core.pop("evidence_sha256", None)
        if digest != canonical_digest(core):
            issues.append(f"{item_label}: evidence hash is invalid")
        expected = (
            expected_invocations[index]
            if index < len(expected_invocations)
            else {}
        )
        container = expected.get("container")
        if not isinstance(container, dict):
            container = {}
        source = expected.get("source")
        expected_role = expected.get("role")
        case_id = expected.get("case_id")
        if (
            invocation.get("index") != index
            or invocation.get("role") != expected_role
            or invocation.get("source") != (
                source.get("path") if isinstance(source, dict) else None
            )
            or invocation.get("source_sha256") != (
                source.get("sha256") if isinstance(source, dict) else None
            )
            or invocation.get("requested_case_count") != 1
            or invocation.get("status") != "completed"
            or invocation.get("evaluation_complete") is not True
        ):
            issues.append(f"{item_label}: program/request binding is invalid")
        if invocation.get("requested_case_ids_sha256") != canonical_digest([case_id]):
            issues.append(f"{item_label}: requested case-id binding is invalid")
        compact_result = compact_execution_program_result(
            container,
            execution_key=str(expected.get("execution_key") or "execution"),
            label=item_label,
            issues=issues,
        )
        if compact_result is not None and invocation.get(
            "program_results_sha256"
        ) != canonical_digest([compact_result]):
            issues.append(f"{item_label}: program-result binding is invalid")
        evaluation = invocation.get("evaluation")
        if not isinstance(evaluation, dict):
            issues.append(f"{item_label}: evaluation is missing")
            continue
        if (
            evaluation.get("schema_version") != 1
            or evaluation.get("kind") != "cpideas.program_dataset_evaluation"
            or evaluation.get("status") != "completed"
            or evaluation.get("evaluation_complete") is not True
            or evaluation.get("error") is not None
            or evaluation.get("comparison") != "none"
            or evaluation.get("validator") is not None
        ):
            issues.append(f"{item_label}: evaluation is incomplete")
        program = evaluation.get("program")
        if not isinstance(program, dict) or (
            program.get("source_name") != (
                source.get("path") if isinstance(source, dict) else None
            )
            or program.get("source_sha256") != (
                source.get("sha256") if isinstance(source, dict) else None
            )
        ):
            issues.append(f"{item_label}: evaluation program binding is invalid")
        binding = evaluation.get("case_results_binding")
        summary = evaluation.get("summary")
        counts = summary.get("verdict_counts") if isinstance(summary, dict) else None
        expected_evaluation_ok: bool | None = None
        if (
            not isinstance(binding, dict)
            or binding.get("count") != 1
            or HASH_RE.fullmatch(str(binding.get("sha256", ""))) is None
            or not isinstance(summary, dict)
            or summary.get("total") != 1
            or not isinstance(counts, dict)
            or any(
                not isinstance(key, str)
                or type(value) is not int
                or value <= 0
                for key, value in (counts.items() if isinstance(counts, dict) else [])
            )
            or sum(counts.values()) != 1
            or forbidden & set(counts or {})
            or set(counts or {}) - {"EXECUTED", "TLE", "MLE", "OLE", "RE"}
        ):
            issues.append(f"{item_label}: case-result summary is invalid")
        elif isinstance(counts, dict):
            expected_evaluation_ok = counts == {"EXECUTED": 1}
            if evaluation.get("ok") is not expected_evaluation_ok:
                issues.append(
                    f"{item_label}: evaluation.ok contradicts case verdicts"
                )
            evidence_verdict = next(iter(counts))
            execution = container.get(str(expected.get("execution_key")))
            sandbox_verdict = (
                execution.get("sandbox_verdict")
                if isinstance(execution, dict)
                else None
            )
            program_timed_out = (
                execution.get("timed_out")
                if isinstance(execution, dict)
                else None
            )
            if expected.get("kind") == "route":
                route_verdict = container.get("verdict")
                if evidence_verdict == "EXECUTED":
                    allowed_route_verdicts = {"AC", "WA"}
                elif evidence_verdict in {"TLE", "MLE", "OLE", "RE"}:
                    allowed_route_verdicts = {evidence_verdict}
                else:
                    allowed_route_verdicts = set()
                verdict_matches = route_verdict in allowed_route_verdicts
            else:
                # A checker exit assigned to WA is reported by the sandbox as
                # RE/non-zero on some backends.  It is still trustworthy when
                # the source-bound checker record and exit contract agree.
                checker_exit = execution.get("exit_code")
                verdict_matches = (
                    evidence_verdict == "EXECUTED"
                    if checker_exit == 0
                    else evidence_verdict in {"EXECUTED", "RE"}
                )
            if (
                sandbox_verdict != evidence_verdict
                or (program_timed_out is True) != (evidence_verdict == "TLE")
                or not verdict_matches
            ):
                issues.append(
                    f"{item_label}: evidence verdict does not match execution/verdict"
                )
        configuration = evaluation.get("configuration")
        chunks = evaluation.get("chunks")
        if not isinstance(configuration, dict) or not isinstance(chunks, list):
            issues.append(f"{item_label}: chunk/resource evidence is missing")
            continue
        if (
            configuration.get("requested_time_limit_ms") != requested_time_ms
            or configuration.get("effective_time_limit_ms") != configured_time_ms
            or configuration.get("requested_memory_limit_mb")
            != backend.get("requested_memory_limit_mb")
            or configuration.get("effective_memory_limit_mb")
            != backend.get("effective_memory_limit_mb")
            or configuration.get("requested_max_output_bytes")
            != backend.get("max_output_bytes_per_stream")
            or configuration.get("effective_max_output_bytes")
            != backend.get("max_output_bytes_per_stream")
            or configuration.get("max_batch_output_bytes") != expected_budget
            or configuration.get("chunk_count") != len(chunks)
            or configuration.get("batch_size") != backend.get("dataset_batch_size")
            or configuration.get("max_request_bytes") != backend.get("max_request_bytes")
        ):
            issues.append(f"{item_label}: evaluation resource limits differ from backend")
        cursor = 0
        for chunk_index, chunk in enumerate(chunks):
            if not isinstance(chunk, dict):
                issues.append(f"{item_label}: chunk[{chunk_index}] is invalid")
                continue
            start = chunk.get("start")
            stop = chunk.get("stop")
            captured = chunk.get("captured_output_bytes")
            if (
                chunk.get("index") != chunk_index
                or start != cursor
                or type(start) is not int
                or type(stop) is not int
                or stop <= start
                or chunk.get("total") != stop - start
                or chunk.get("status") != "completed"
                or type(chunk.get("ok")) is not bool
                or (
                    expected_evaluation_ok is True
                    and chunk.get("ok") is not True
                )
                or chunk.get("valid") != stop - start
                or type(chunk.get("valid")) is not int
                or chunk.get("invalid") != 0
                or type(chunk.get("invalid")) is not int
                or chunk.get("validator_errors") != 0
                or type(chunk.get("validator_errors")) is not int
                # Response truncation is an infrastructure failure.  A real
                # OLE is instead the sandbox's bounded program-output verdict.
                or chunk.get("output_truncated") is not False
                or type(captured) is not int
                or type(expected_budget) is not int
                or not 0 <= captured <= expected_budget
                or chunk.get("max_batch_output_bytes") != expected_budget
                or chunk.get("effective_time_limit_ms") != configured_time_ms
                or chunk.get("effective_wall_time_limit_ms") != (
                    round(configured_time_ms * wall_multiplier)
                    if isinstance(configured_time_ms, int)
                    and isinstance(wall_multiplier, (int, float))
                    else None
                )
                or chunk.get("effective_memory_limit_mb")
                != backend.get("effective_memory_limit_mb")
                or chunk.get("effective_max_output_bytes")
                != backend.get("max_output_bytes_per_stream")
            ):
                issues.append(f"{item_label}: chunk[{chunk_index}] evidence is invalid")
                continue
            cursor = stop
        if cursor != 1:
            issues.append(f"{item_label}: chunks do not cover its one requested case")
        if (
            expected_evaluation_ok is False
            and chunks
            and all(
                isinstance(chunk, dict) and chunk.get("ok") is True
                for chunk in chunks
            )
        ):
            issues.append(f"{item_label}: chunk ok flags contradict failed case verdicts")


def token_digest(path: Path) -> str:
    return sha256_bytes(b"\0".join(path.read_bytes().split()))


def safe_relative(raw: Any, *, label: str) -> PurePosixPath:
    if not isinstance(raw, str) or not raw.strip() or "\\" in raw:
        raise ContractError(f"{label} is not a normalized problem-relative path")
    path = PurePosixPath(raw)
    if path.is_absolute() or path == PurePosixPath(".") or ".." in path.parts:
        raise ContractError(f"{label} leaves the problem directory")
    if any(part.startswith(".") for part in path.parts):
        raise ContractError(f"{label} must not contain hidden path components")
    if path.as_posix() != raw:
        raise ContractError(f"{label} is not normalized POSIX syntax")
    return path


def regular_path(root: Path, relative: PurePosixPath, *, label: str) -> Path:
    path = root.joinpath(*relative.parts)
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise ContractError(f"{label} traverses a symbolic link")
        if not current.exists():
            break
    try:
        path.resolve(strict=False).relative_to(root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ContractError(f"{label} resolves outside the problem directory") from exc
    if not path.is_file() or path.is_symlink() or path.stat().st_size == 0:
        raise ContractError(f"{label} is missing, empty, or unsafe: {path}")
    return path


def read_json(path: Path, *, label: str) -> Any:
    try:
        with path.open("r", encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContractError(f"cannot read {label}: {exc}") from exc


def binding_matches(
    root: Path, raw: Any, *, expected: PurePosixPath | None, label: str
) -> tuple[bool, Path | None]:
    if not isinstance(raw, dict):
        return False, None
    try:
        relative = safe_relative(raw.get("path"), label=f"{label}.path")
        path = regular_path(root, relative, label=label)
    except ContractError:
        return False, None
    if expected is not None and relative != expected:
        return False, path
    return (
        raw.get("size") == path.stat().st_size
        and raw.get("sha256") == sha256_file(path)
        and isinstance(raw.get("sha256"), str)
        and HASH_RE.fullmatch(raw["sha256"]) is not None,
        path,
    )


def successful_compile(raw: Any, backend: Any) -> bool:
    if not (
        isinstance(raw, dict)
        and isinstance(raw.get("command"), list)
        and bool(raw.get("command"))
        and raw.get("timed_out") is False
        and raw.get("spawn_error") is None
        and raw.get("exit_code") == 0
        and not isinstance(raw.get("exit_code"), bool)
        and isinstance(raw.get("stdout"), dict)
        and isinstance(raw.get("stderr"), dict)
    ):
        return False
    evidence = raw.get("compilation_evidence")
    if not isinstance(evidence, dict):
        return False
    core = dict(evidence)
    digest = core.pop("evidence_sha256", None)
    if not isinstance(backend, dict):
        return False
    requested_seconds = backend.get("requested_program_timeout_seconds")
    effective_seconds = backend.get("sandbox_effective_time_limit_seconds")
    requested_memory = backend.get("requested_memory_limit_mb")
    effective_memory = backend.get("effective_memory_limit_mb")
    service = backend.get("service_identity")
    service_compiler_profile = (
        service.get("compilerProfile") if isinstance(service, dict) else None
    )
    service_policy = service.get("executionPolicy") if isinstance(service, dict) else None
    runtime_policy = (
        service_policy.get("runtime") if isinstance(service_policy, dict) else None
    )
    compilation_policy = (
        service_policy.get("compilation") if isinstance(service_policy, dict) else None
    )
    cpp_policy = (
        compilation_policy.get("cpp")
        if isinstance(compilation_policy, dict)
        else None
    )
    wall_multiplier = (
        runtime_policy.get("wallTimeMultiplier")
        if isinstance(runtime_policy, dict)
        else None
    )
    output_limit = backend.get("max_output_bytes_per_stream")
    compile_context_revision = backend.get("compile_context_policy_revision")
    compiler_profile = backend.get("cpp_compiler_profile")
    if (
        not isinstance(requested_seconds, (int, float))
        or isinstance(requested_seconds, bool)
        or not isinstance(effective_seconds, (int, float))
        or isinstance(effective_seconds, bool)
        or type(requested_memory) is not int
        or type(effective_memory) is not int
        or not isinstance(wall_multiplier, (int, float))
        or isinstance(wall_multiplier, bool)
        or type(output_limit) is not int
        or compile_context_revision != COMPILE_CONTEXT_POLICY_REVISION
        or compiler_profile != LIGHTCP_CPP_PROFILE
        or service_compiler_profile != LIGHTCP_CPP_PROFILE
        or not isinstance(cpp_policy, dict)
    ):
        return False
    requested_time_ms = round(requested_seconds * 1000)
    effective_time_ms = round(effective_seconds * 1000)
    expected_profile = {
        "requested_time_limit_ms": requested_time_ms,
        "effective_time_limit_ms": effective_time_ms,
        "effective_wall_time_limit_ms": round(effective_time_ms * wall_multiplier),
        "requested_memory_limit_mb": requested_memory,
        "effective_memory_limit_mb": effective_memory,
        "requested_max_output_bytes": output_limit,
        "effective_max_output_bytes": output_limit,
    }
    return (
        digest == canonical_digest(core)
        and evidence.get("schema_version") == 1
        and evidence.get("kind") == "cpideas.dataset_compilation"
        and evidence.get("dataset_api_revision") == DATASET_API_REVISION
        and evidence.get("source_name") == raw.get("source")
        and evidence.get("source_sha256") == raw.get("source_sha256")
        and evidence.get("compile_context_policy_revision")
        == compile_context_revision
        and evidence.get("compile_copy_in_files_sha256")
        == compile_context_sha256()
        and evidence.get("status") == "COMPILED"
        and evidence.get("ok") is True
        and evidence.get("runtime_profile_for_subsequent_execution")
        == expected_profile
        and evidence.get("compiler_limits")
        == {
            "cpu_time_ms": cpp_policy.get("cpuTimeMs"),
            "memory_mb": cpp_policy.get("memoryMb"),
            "process_limit": cpp_policy.get("processLimit"),
        }
    )


def derived_verdict(
    route: dict[str, Any], test: dict[str, Any], answer_path: Path
) -> str | None:
    execution = route.get("execution")
    if not isinstance(execution, dict):
        return None
    sandbox_verdict = execution.get("sandbox_verdict")
    if sandbox_verdict in {"TLE", "MLE", "OLE", "RE"}:
        return sandbox_verdict
    if execution.get("timed_out") is True:
        return "TLE"
    if execution.get("spawn_error") is not None:
        return None
    if execution.get("exit_code") != 0:
        return "RE"
    evidence = route.get("comparison_evidence")
    mode = test.get("comparison")
    if not isinstance(evidence, dict) or evidence.get("mode") != mode:
        return None
    if mode == "tokens":
        answer_digest = token_digest(answer_path)
        actual = evidence.get("actual_normalized_sha256")
        if evidence.get("answer_normalized_sha256") != answer_digest:
            return None
        return "AC" if actual == answer_digest else "WA"
    if mode == "exact":
        answer_digest = sha256_file(answer_path)
        stdout = execution.get("stdout")
        if not isinstance(stdout, dict) or evidence.get("answer_sha256") != answer_digest:
            return None
        if evidence.get("actual_sha256") != stdout.get("sha256"):
            return None
        return "AC" if evidence.get("actual_sha256") == answer_digest else "WA"
    if mode == "checker":
        checker = route.get("checker")
        if (
            not isinstance(checker, dict)
            or checker.get("timed_out") is not False
            or checker.get("spawn_error") is not None
            or evidence.get("checker_exit_code") != checker.get("exit_code")
        ):
            return None
        checker_exit = checker.get("exit_code")
        checker_sandbox_verdict = checker.get("sandbox_verdict")
        if checker.get("execution_backend") != "lightcpverifier":
            return None
        wa_codes = test.get("checker_wa_exit_codes")
        if checker_exit == 0 and checker_sandbox_verdict == "EXECUTED":
            return "AC"
        if (
            isinstance(wa_codes, list)
            and checker_exit in wa_codes
            and checker_sandbox_verdict in {"EXECUTED", "RE"}
        ):
            return "WA"
        return None
    return None


def verify_chain(problem_root: Path, minimum: int, maximum: int) -> ChainResult:
    issues: list[str] = []
    try:
        current_resources = load_statement_resources(problem_root)
    except StatementResourceError as exc:
        return ChainResult(
            [f"statement resource policy is invalid: {exc}"], 0, [], [], []
        )
    current_resource_record = current_resources.as_dict()
    receipt_dir = problem_root.joinpath(*RECEIPT_ROOT.parts)
    if receipt_dir.is_symlink() or not receipt_dir.is_dir():
        return ChainResult([f"missing regular receipt directory: {receipt_dir}"], 0, [], [], [])
    numbered: dict[int, Path] = {}
    for path in sorted(receipt_dir.iterdir()):
        if path.is_symlink() or not path.is_file():
            continue
        match = RECEIPT_RE.fullmatch(path.name)
        if match:
            number = int(match.group(1))
            if number < 1 or path.name != f"round-{number:02d}.json":
                continue
            if number in numbered:
                return ChainResult(
                    [f"duplicate receipt encoding for round {number}"],
                    0,
                    [],
                    [],
                    [],
                )
            numbered[number] = path
    if not numbered:
        return ChainResult(["no adversarial round receipts found"], 0, [], [], [])
    expected_numbers = set(range(1, max(numbered) + 1))
    if set(numbered) != expected_numbers:
        issues.append("round receipts must be consecutive from 1")
    completed = len(numbered)
    if not minimum <= completed <= maximum:
        issues.append(f"receipt count {completed} is outside required range {minimum}..{maximum}")

    recorder = Path(__file__).resolve().with_name("record_adversarial_round.py")
    recorder_hash = sha256_file(recorder) if recorder.is_file() and not recorder.is_symlink() else None
    prior_hash: str | None = None
    prior_survivors: set[str] = set()
    prior_survivor_sources: dict[str, tuple[Any, Any]] = {}
    all_killed: list[str] = []
    final_survivors: list[str] = []
    receipt_hashes: list[str] = []
    for number in sorted(numbered):
        path = numbered[number]
        label = f"round {number} receipt"
        try:
            receipt = read_json(path, label=label)
        except ContractError as exc:
            issues.append(str(exc))
            continue
        if not isinstance(receipt, dict):
            issues.append(f"{label}: top level must be an object")
            continue
        receipt_hash = sha256_file(path)
        receipt_hashes.append(receipt_hash)
        core = dict(receipt)
        evidence_digest = core.pop("evidence_sha256", None)
        if evidence_digest != canonical_digest(core):
            issues.append(f"{label}: evidence_sha256 is invalid")
        for key, expected in (
            ("schema_version", SCHEMA_VERSION),
            ("generator", GENERATOR),
            ("status", "passed"),
            ("round", number),
            ("recorder_sha256", recorder_hash),
            ("execution_mode", "production"),
            ("production", True),
        ):
            if receipt.get(key) != expected:
                issues.append(f"{label}.{key}: expected {expected!r}")
        backend = receipt.get("execution_backend")
        if not isinstance(backend, dict):
            issues.append(f"{label}: execution backend evidence is missing")
        else:
            for key, expected in (
                ("name", "lightcpverifier"),
                ("sandboxed", True),
                ("testing_only", False),
                ("compile_context_policy_revision", COMPILE_CONTEXT_POLICY_REVISION),
                ("cpp_compiler_profile", LIGHTCP_CPP_PROFILE),
            ):
                if backend.get(key) != expected:
                    issues.append(
                        f"{label}.execution_backend.{key}: expected {expected!r}"
                    )
            expected_backend_resources = {
                "requested_program_timeout_seconds": (
                    current_resources.time_limit_ms / 1000
                ),
                "effective_program_timeout_seconds": (
                    current_resources.time_limit_ms / 1000
                ),
                "verdict_time_limit_seconds": current_resources.time_limit_ms / 1000,
                "sandbox_effective_time_limit_seconds": (
                    current_resources.time_limit_ms / 1000
                ),
                "requested_memory_limit_mb": current_resources.memory_limit_mib,
                "effective_memory_limit_mb": current_resources.memory_limit_mib,
            }
            for key, expected in expected_backend_resources.items():
                if backend.get(key) != expected:
                    issues.append(
                        f"{label}.execution_backend.{key}: expected {expected!r}"
                    )
        if receipt.get("statement_resources") != current_resource_record:
            issues.append(f"{label}: statement resource binding is stale or invalid")
        if receipt.get(
            "statement_resources_sha256"
        ) != current_resources.canonical_sha256():
            issues.append(f"{label}: statement resource hash is stale or invalid")
        if number == 1 and receipt.get("trigger") != "initial-matrix":
            issues.append("round 1 receipt trigger must be initial-matrix")
        if not isinstance(receipt.get("delta"), str) or receipt["delta"].strip().lower() in PLACEHOLDERS:
            issues.append(f"{label}: delta must be concrete")

        plan_rel = PLAN_ROOT / f"round-{number:02d}.json"
        plan_ok, plan_path = binding_matches(
            problem_root, receipt.get("plan"), expected=plan_rel, label=f"{label} plan"
        )
        if not plan_ok or plan_path is None:
            issues.append(f"{label}: plan binding is stale or invalid")
            plan = None
        else:
            try:
                plan = read_json(plan_path, label=f"round {number} plan")
            except ContractError as exc:
                issues.append(str(exc))
                plan = None
        if isinstance(plan, dict):
            if plan.get("round") != number:
                issues.append(f"round {number} plan number differs from receipt")
            if plan.get("trigger") != receipt.get("trigger"):
                issues.append(f"round {number} trigger differs from plan")
            if not isinstance(plan.get("delta"), str) or plan["delta"].strip() != receipt.get("delta"):
                issues.append(f"round {number} delta differs from plan")
            planned_timeout = plan.get("timeout_seconds")
            if "timeout_seconds" in plan and (
                not isinstance(planned_timeout, (int, float))
                or isinstance(planned_timeout, bool)
                or float(planned_timeout)
                != current_resources.time_limit_ms / 1000
            ):
                issues.append(f"round {number} plan time limit differs from statement")
            planned_memory = plan.get("memory_limit_mb")
            if "memory_limit_mb" in plan and (
                type(planned_memory) is not int
                or planned_memory != current_resources.memory_limit_mib
            ):
                issues.append(f"round {number} plan memory limit differs from statement")

        previous = receipt.get("previous_receipt")
        if number == 1:
            if previous is not None:
                issues.append("round 1 previous_receipt must be null")
        else:
            previous_rel = RECEIPT_ROOT / f"round-{number - 1:02d}.json"
            previous_ok, previous_path = binding_matches(
                problem_root,
                previous,
                expected=previous_rel,
                label=f"{label} previous receipt",
            )
            if not previous_ok or previous_path is None or sha256_file(previous_path) != prior_hash:
                issues.append(f"{label}: previous receipt link is stale or invalid")

        tests_raw = receipt.get("tests")
        tests: dict[str, tuple[dict[str, Any], Path]] = {}
        plan_tests = {
            item.get("test_id"): item
            for item in (plan.get("tests", []) if isinstance(plan, dict) else [])
            if isinstance(item, dict) and isinstance(item.get("test_id"), str)
        }
        if not isinstance(tests_raw, list) or not tests_raw:
            issues.append(f"{label}: tests must be a non-empty list")
            tests_raw = []
        for index, test in enumerate(tests_raw):
            if not isinstance(test, dict) or not isinstance(test.get("test_id"), str):
                issues.append(f"{label}.tests[{index}] is invalid")
                continue
            test_id = test["test_id"]
            if test_id in tests:
                issues.append(f"{label}: duplicate test_id {test_id}")
                continue
            for field in ("input", "answer"):
                binding = test.get(field)
                try:
                    relative = safe_relative(
                        binding.get("path") if isinstance(binding, dict) else None,
                        label=f"{label} test {test_id} {field}.path",
                    )
                except ContractError:
                    issues.append(
                        f"{label}: test {test_id} {field} path is invalid"
                    )
                    continue
                if relative.parts[:2] != ("package", "tests"):
                    issues.append(
                        f"{label}: test {test_id} {field} must be below package/tests/"
                    )
            input_ok, _ = binding_matches(
                problem_root, test.get("input"), expected=None, label=f"{label} test {test_id} input"
            )
            answer_ok, answer_path = binding_matches(
                problem_root, test.get("answer"), expected=None, label=f"{label} test {test_id} answer"
            )
            if not input_ok or not answer_ok or answer_path is None:
                issues.append(f"{label}: test {test_id} bindings are stale or invalid")
                continue
            if test.get("comparison") not in {"tokens", "exact", "checker"}:
                issues.append(f"{label}: test {test_id} has invalid comparison")
                continue
            wa_codes = test.get("checker_wa_exit_codes")
            if (
                not isinstance(wa_codes, list)
                or not wa_codes
                or any(
                    not isinstance(code, int) or isinstance(code, bool) or code <= 0
                    for code in wa_codes
                )
                or len(set(wa_codes)) != len(wa_codes)
            ):
                issues.append(f"{label}: test {test_id} has invalid checker WA codes")
                continue
            planned_test = plan_tests.get(test_id)
            if not isinstance(planned_test, dict) or (
                planned_test.get("comparison", "tokens") != test.get("comparison")
                or planned_test.get("checker_wa_exit_codes", [1, 2]) != wa_codes
            ):
                issues.append(
                    f"{label}: test {test_id} comparison contract differs from plan"
                )
            tests[test_id] = (test, answer_path)

        uses_checker = any(
            test.get("comparison") == "checker" for test, _path in tests.values()
        )
        checker_source = receipt.get("checker_source")
        checker_compile = receipt.get("checker_compile")
        expected_checker_rel = PurePosixPath("package/checker.cpp")
        if uses_checker:
            if not isinstance(plan, dict) or plan.get("checker_source") != (
                expected_checker_rel.as_posix()
            ):
                issues.append(
                    f"{label}: production checker tests lack planned checker_source"
                )
            checker_ok, _checker_path = binding_matches(
                problem_root,
                checker_source,
                expected=expected_checker_rel,
                label=f"{label} checker source",
            )
            if not checker_ok:
                issues.append(f"{label}: checker source binding is stale or invalid")
            if (
                not isinstance(checker_source, dict)
                or not isinstance(checker_compile, dict)
                or checker_compile.get("source") != checker_source.get("path")
                or checker_compile.get("source_sha256")
                != checker_source.get("sha256")
                or not successful_compile(checker_compile, backend)
            ):
                issues.append(f"{label}: checker lacks a successful bound compile receipt")
        elif checker_source is not None or checker_compile is not None:
            issues.append(f"{label}: unused checker source/compile evidence is present")

        routes_raw = receipt.get("routes")
        active: set[str] = set()
        killed: list[str] = []
        survivors: list[str] = []
        current_sources: dict[str, tuple[Any, Any]] = {}
        if not isinstance(routes_raw, list) or not routes_raw:
            issues.append(f"{label}: routes must be a non-empty list")
            routes_raw = []
        for index, route in enumerate(routes_raw):
            if not isinstance(route, dict) or not isinstance(route.get("route_id"), str):
                issues.append(f"{label}.routes[{index}] is invalid")
                continue
            route_id = route["route_id"]
            if route_id in active:
                issues.append(f"{label}: duplicate route_id {route_id}")
            active.add(route_id)
            source_ok, _ = binding_matches(
                problem_root, route.get("source"), expected=None, label=f"{label} route {route_id} source"
            )
            if not source_ok:
                issues.append(f"{label}: route {route_id} source binding is stale")
            source_binding = route.get("source")
            if isinstance(source_binding, dict):
                current_sources[route_id] = (
                    source_binding.get("path"),
                    source_binding.get("sha256"),
                )
            compile_record = route.get("compile")
            if (
                not isinstance(source_binding, dict)
                or not isinstance(compile_record, dict)
                or compile_record.get("source") != source_binding.get("path")
                or compile_record.get("source_sha256")
                != source_binding.get("sha256")
                or not successful_compile(compile_record, backend)
            ):
                issues.append(f"{label}: route {route_id} lacks a successful compile receipt")
            test_id = route.get("breaker_test_id")
            if test_id not in tests:
                issues.append(f"{label}: route {route_id} names an unknown breaker test")
                continue
            test, answer_path = tests[test_id]
            verdict = derived_verdict(route, test, answer_path)
            if verdict is None or route.get("verdict") != verdict:
                issues.append(f"{label}: route {route_id} verdict is not machine-derived")
                continue
            if test.get("comparison") == "checker":
                comparison_evidence = route.get("comparison_evidence")
                if (
                    not isinstance(comparison_evidence, dict)
                    or not isinstance(checker_source, dict)
                    or comparison_evidence.get("checker_source_sha256")
                    != checker_source.get("sha256")
                ):
                    issues.append(
                        f"{label}: route {route_id} checker source hash is unbound"
                    )
            outcome = "survived" if verdict == "AC" else "killed"
            if route.get("outcome") != outcome:
                issues.append(f"{label}: route {route_id} outcome differs from verdict")
            (survivors if outcome == "survived" else killed).append(route_id)
        validate_round_execution_evidence(
            receipt,
            backend,
            routes_raw,
            label=label,
            issues=issues,
        )
        if number > 1 and not prior_survivors <= active:
            issues.append(f"{label}: not every prior survivor was carried forward")
        if number > 1:
            for route_id in sorted(prior_survivors & active):
                if current_sources.get(route_id) != prior_survivor_sources.get(route_id):
                    issues.append(
                        f"{label}: carried survivor {route_id} changed source path or content"
                    )
        if receipt.get("killed") != killed:
            issues.append(f"{label}: aggregate killed list differs from route verdicts")
        if receipt.get("survivors") != survivors:
            issues.append(f"{label}: aggregate survivors list differs from route verdicts")
        all_killed.extend(killed)
        final_survivors = survivors
        prior_survivors = set(survivors)
        prior_survivor_sources = {
            route_id: current_sources.get(route_id, (None, None))
            for route_id in survivors
        }
        prior_hash = receipt_hash

    return ChainResult(
        issues=issues,
        rounds=completed,
        killed=all_killed,
        survivors=final_survivors,
        receipt_hashes=receipt_hashes,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--problem-dir", type=Path, required=True)
    parser.add_argument("--min-rounds", type=int, required=True)
    parser.add_argument("--max-rounds", type=int, required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if not args.problem_dir.is_dir() or args.problem_dir.is_symlink():
        parser.error("--problem-dir must be an existing non-symlink directory")
    if args.min_rounds < 1 or args.max_rounds < args.min_rounds:
        parser.error("round range must satisfy 1 <= min <= max")
    args.problem_dir = args.problem_dir.resolve()
    return args


def main() -> int:
    args = parse_args()
    result = verify_chain(args.problem_dir, args.min_rounds, args.max_rounds)
    if args.json:
        print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    else:
        stream = sys.stdout if result.passed else sys.stderr
        print(
            "Adversarial round chain: "
            + ("PASS" if result.passed else f"FAIL ({len(result.issues)} issue(s))"),
            file=stream,
        )
        for issue in result.issues:
            print(f"- {issue}", file=stream)
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
