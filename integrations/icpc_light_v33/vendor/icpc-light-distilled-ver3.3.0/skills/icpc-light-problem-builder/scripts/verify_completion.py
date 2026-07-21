#!/usr/bin/env python3
"""Verify an ICPC Light release candidate before independent readiness review.

This gate deliberately does not read ``audit/readiness.md``.  A successful run
atomically writes a hash-bound receipt (``audit/completion-gate.json`` by
default).  The independent readiness reviewer consumes that receipt and
``verify_readiness.py`` later checks that none of its inputs changed.

The gate is structural and executable, but it is not a theorem prover.  It
requires the dedicated blind-stage verifier, validates stable audit schemas,
reuses sandbox compilation evidence from the canonical machine regression,
and binds the saved evidence by SHA-256.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

import run_stage_agent as stage_runner
import run_regression_gate as regression_gate
import verify_adversarial_round_chain as adversarial_chain
from regression_backend import (
    BACKEND_EVIDENCE_SCHEMA_VERSION,
    COMPILE_CONTEXT_POLICY_REVISION,
    LIGHTCP_API_REVISION,
    LIGHTCP_CPP_PROFILE,
    LIGHTCP_DATASET_API_REVISION,
    LIGHTCP_MAX_OUTPUT_BYTES,
    BackendSource,
    canonical_sha256,
    compile_context_sha256,
    cpideas_module_bindings,
    create_backend,
)
from statement_resources import (
    StatementResourceError,
    StatementResources,
    load_statement_resources,
)


GATE_NAME = "icpc-light-pre-readiness-completion"
RECEIPT_SCHEMA_VERSION = 1
DEFAULT_RECEIPT_REL = "audit/completion-gate.json"
BLIND_VERIFIER_NAME = "verify_blind_stage.py"
REGRESSION_EXECUTOR_NAME = "run_regression_gate.py"
REGRESSION_MACHINE_REL = "audit/regression-machine.json"
COVERAGE_MATRIX_REL = "audit/coverage-matrix.json"
REGRESSION_PLAN_SCHEMA_VERSION = 3
RESOURCE_POLICY_SCHEMA_VERSION = 1
RESOURCE_POLICY_DESIGN_FIELDS = (
    "intended_complexity",
    "maximum_scale",
    "time_limit_rationale",
    "memory_limit_rationale",
)
CANONICAL_SAMPLE_MANIFEST = "package/samples/manifest.json"
SELECTED_STANDARD_ROUTE_REL = "audit/private/selected-standard-route.cpp"
PRECLASSIFICATION_VALIDATOR = (
    Path(__file__).resolve().parents[2]
    / "grade-test-data-buildability"
    / "scripts"
    / "validate_report.py"
)
REQUIRED_AGENT_MODEL = "gpt-5.6-sol"
REQUIRED_REASONING_EFFORT = "xhigh"
BLIND_TIME_LIMIT_SECONDS = 7200
PRE_READINESS_STAGES = (
    "preclassification",
    "solution-draft",
    "std-materialization",
    "solution-validation",
    "build-hardening",
)

REQUIRED_AUDIT_FILES = (
    "run-state.md",
    "blind-summary.md",
    "blind-claim-reviews.json",
    "data-buildability.md",
    "private/selected-standard-route.cpp",
    "contract.md",
    "solution-review-draft.md",
    "std-materialization.md",
    "solution-review.md",
    "wrong-solutions.md",
    "test-manifest.md",
    "coverage-matrix.json",
    "adversarial-rounds.md",
    "regression-plan.json",
    "regression.md",
)

GRADE_REQUIRED_FIELDS = (
    "schema_version",
    "agent_model",
    "agent_reasoning_effort",
    "preclassification",
    "scam_status",
    "data_buildability",
    "workflow_profile",
    "decision",
    "confidence",
    "provisional",
    "wrong_solution_min",
    "wrong_solution_max",
    "adversarial_round_mode",
    "adversarial_round_min",
    "adversarial_round_max",
    "stop_reason",
    "risk_tags",
    "required_checks",
    "regrade_triggers",
)

SOLUTION_REVIEW_REQUIRED_FIELDS = (
    "schema_version",
    "agent_model",
    "agent_reasoning_effort",
    "review_status",
    "std_compilation",
    "public_samples",
    "tiny_differential",
    "materialization_mode",
    "materialization_delta_review",
    "std_path",
    "std_sha256",
    "std_provenance_path",
    "std_provenance_sha256",
)

DRAFT_REVIEW_REQUIRED_FIELDS = (
    "schema_version",
    "agent_model",
    "agent_reasoning_effort",
    "review_status",
    "blind_source_path",
    "blind_source_sha256",
)

MATERIALIZATION_REQUIRED_FIELDS = (
    "schema_version",
    "agent_model",
    "agent_reasoning_effort",
    "status",
    "materialization_mode",
    "blind_source_path",
    "blind_source_sha256",
    "std_path",
    "std_sha256",
)

REGRESSION_REQUIRED_FIELDS = (
    "schema_version",
    "agent_model",
    "agent_reasoning_effort",
    "status",
    "validator",
    "differential",
    "wrong_routes",
    "privacy_scan",
    "limit_coverage",
    "differential_mode",
    "differential_cases",
    "differential_consecutive_seeds",
    "generated_inputs_validated",
    "wrong_routes_checked",
    "survivability_inputs_checked",
    "accepted_alternatives_checked",
    "accepted_non_jury_outputs_checked",
    "accepted_alternative_strategy",
    "release_tests_checked",
    "repro_command",
)

CPP_SUFFIXES = {".c", ".cc", ".cpp", ".cxx"}
HASH_RE = re.compile(r"[0-9a-f]{64}")
GRADE_STOP_REASONS = {
    "none",
    "shortcut-unresolved",
    "unverifiable-contract",
    "unverifiable-oracle",
    "unverifiable-generation",
    "unverifiable-checker",
    "unverifiable-protocol",
    "unverifiable-numeric",
    "unbounded-adversarial-plan",
    "adversarial-budget-exhausted",
    "outside-scope",
}

REQUIRED_ROUTE_AXES = {
    "alternative-full-routes",
    "alternative-implementations",
    "resource-fragile-exact-routes",
    "fallback-repair-portfolio-routes",
    "combined-heuristics",
    "proof-and-implementation-gaps",
}
COVERAGE_OBLIGATION_KINDS = {
    "acceptance",
    "boundary",
    "contract",
    "proof-boundary",
    "resource",
    "structure",
    "wrong-route",
}
COVERAGE_VARIANT_MODES = {
    "aggregate",
    "exact",
    "high-multiplicity",
    "just-inside",
    "just-outside",
    "ordinary",
    "scaled",
    "structural-noise",
}


class ContractError(ValueError):
    """Raised for malformed stable artifacts or unsafe paths."""


@dataclass
class Check:
    check_id: str
    status: str = "pass"
    evidence: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)

    def fail(self, message: str) -> None:
        self.status = "fail"
        self.issues.append(message)

    def add(self, evidence: str) -> None:
        self.evidence.append(evidence)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.check_id,
            "status": self.status,
            "evidence": self.evidence,
            "issues": self.issues,
        }


class Report:
    def __init__(self, problem_dir: Path, receipt_rel: str, skip_compile: bool) -> None:
        self.problem_dir = problem_dir
        self.receipt_rel = receipt_rel
        self.skip_compile = skip_compile
        self.checks: list[Check] = []
        self.tracked: set[Path] = set()
        self.watched_trees: dict[str, list[str]] = {}
        self.facts: dict[str, Any] = {}
        self.blind_gate: dict[str, Any] = {}

    def new_check(self, check_id: str) -> Check:
        check = Check(check_id)
        self.checks.append(check)
        return check

    def track(self, path: Path) -> None:
        try:
            relative = path.relative_to(self.problem_dir)
        except ValueError as exc:
            raise ContractError(f"tracked input escapes problem directory: {path}") from exc
        self.tracked.add(relative)

    @property
    def issues(self) -> list[str]:
        return [
            f"{check.check_id}: {issue}"
            for check in self.checks
            for issue in check.issues
        ]

    @property
    def passed(self) -> bool:
        return not self.issues and all(check.status == "pass" for check in self.checks)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Verify the complete ICPC Light package before readiness review and "
            "write a hash-bound completion receipt."
        )
    )
    parser.add_argument("--problem-dir", type=Path, required=True)
    parser.add_argument(
        "--receipt-out",
        default=DEFAULT_RECEIPT_REL,
        help=(
            "Problem-relative receipt path "
            f"(default: {DEFAULT_RECEIPT_REL})."
        ),
    )
    parser.add_argument(
        "--skip-compile",
        action="store_true",
        help="Diagnostic only; a run that skips compilation cannot pass.",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if not args.problem_dir.exists() or not args.problem_dir.is_dir():
        parser.error(f"problem directory is not an existing directory: {args.problem_dir}")
    if args.problem_dir.is_symlink():
        parser.error("--problem-dir itself must not be a symbolic link")
    args.problem_dir = args.problem_dir.resolve()
    try:
        args.receipt_rel, args.receipt_path = safe_problem_path(
            args.problem_dir, args.receipt_out, label="--receipt-out", require_exists=False
        )
    except ContractError as exc:
        parser.error(str(exc))
    return args


def safe_problem_path(
    problem_dir: Path,
    raw: Any,
    *,
    label: str,
    require_exists: bool,
) -> tuple[str, Path]:
    if not isinstance(raw, str) or not raw.strip() or "\\" in raw:
        raise ContractError(f"{label} must be a non-empty normalized problem-relative path")
    pure = PurePosixPath(raw)
    if pure.is_absolute() or pure == PurePosixPath(".") or ".." in pure.parts:
        raise ContractError(f"{label} must stay below the problem directory")
    if any(part.startswith(".") for part in pure.parts):
        raise ContractError(f"{label} must not contain hidden path components")
    normalized = pure.as_posix()
    if normalized != raw:
        raise ContractError(f"{label} must use normalized POSIX path syntax")
    path = problem_dir.joinpath(*pure.parts)
    current = problem_dir
    for part in pure.parts:
        current /= part
        if current.is_symlink():
            raise ContractError(f"{label} traverses a symbolic link: {normalized}")
        if not current.exists():
            break
    try:
        path.resolve(strict=False).relative_to(problem_dir)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ContractError(f"{label} resolves outside the problem directory") from exc
    if require_exists and not path.exists():
        raise ContractError(f"{label} does not exist: {normalized}")
    return normalized, path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json_sha256(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def require_file(report: Report, check: Check, path: Path, label: str) -> bool:
    if path.is_symlink():
        check.fail(f"{label} must not be a symbolic link: {path}")
        return False
    if not path.exists():
        check.fail(f"missing {label}: {path}")
        return False
    if not path.is_file():
        check.fail(f"{label} is not a regular file: {path}")
        return False
    try:
        size = path.stat().st_size
    except OSError as exc:
        check.fail(f"cannot stat {label} {path}: {exc}")
        return False
    if size == 0:
        check.fail(f"{label} is empty: {path}")
        return False
    report.track(path)
    return True


def yaml_scalar(raw: str) -> Any:
    value = raw.strip()
    if not value:
        return None
    if value in {"[]", "[ ]"}:
        return []
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [yaml_scalar(part) for part in inner.split(",")]
    lower = value.lower()
    if lower == "true":
        return True
    if lower == "false":
        return False
    if lower in {"null", "~"}:
        return None
    if re.fullmatch(r"[-+]?\d+", value):
        return int(value)
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value.strip("`")


def parse_front_matter(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ContractError(f"cannot read UTF-8 front matter from {path}: {exc}") from exc
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ContractError(f"{path} must start with a YAML front matter delimiter")
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration as exc:
        raise ContractError(f"{path} has no closing YAML front matter delimiter") from exc

    result: dict[str, Any] = {}
    active_list: str | None = None
    for line_number, line in enumerate(lines[1:end], start=2):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if line[:1].isspace() and stripped.startswith("-"):
            if active_list is None:
                raise ContractError(f"{path}:{line_number}: list item has no field")
            current = result.get(active_list)
            if not isinstance(current, list):
                current = []
                result[active_list] = current
            current.append(yaml_scalar(stripped[1:].strip()))
            continue
        match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_-]*)\s*:\s*(.*)", stripped)
        if match is None:
            raise ContractError(f"{path}:{line_number}: unsupported YAML syntax")
        key, raw_value = match.groups()
        if key in result:
            raise ContractError(f"{path}:{line_number}: duplicate field {key!r}")
        if raw_value.strip():
            result[key] = yaml_scalar(raw_value)
            active_list = None
        else:
            result[key] = []
            active_list = key
    return result


def require_fields(
    check: Check, data: dict[str, Any], fields: Iterable[str], label: str
) -> None:
    for field_name in fields:
        if field_name not in data:
            check.fail(f"{label} is missing required field {field_name!r}")


def require_int(check: Check, data: dict[str, Any], key: str, label: str) -> int | None:
    value = data.get(key)
    if type(value) is not int:
        check.fail(f"{label}.{key} must be an integer")
        return None
    return value


def load_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContractError(f"cannot read UTF-8 JSON {path}: {exc}") from exc


def invalidate_receipt(path: Path) -> None:
    """Remove a previous passed receipt before any new verification begins."""
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        raise ContractError(f"receipt destination exists and is not a file: {path}")


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink():
        raise ContractError(f"receipt parent is a symbolic link: {path.parent}")
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(rendered)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def parse_utc(raw: Any, label: str) -> datetime | None:
    if not isinstance(raw, str) or not raw.endswith("Z"):
        return None
    try:
        value = datetime.fromisoformat(raw[:-1] + "+00:00")
    except ValueError:
        return None
    if value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        return None
    return value.astimezone(timezone.utc)


def exact_stage_command(value: Any, problem_dir: Path) -> bool:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in value
    ):
        return False
    if len(value) < 2 or Path(value[0]).name != "codex" or value[1] != "exec":
        return False
    model_positions = [i for i, item in enumerate(value) if item == "--model"]
    cd_positions = [i for i, item in enumerate(value) if item == "--cd"]
    efforts = [
        value[i + 1]
        for i, item in enumerate(value[:-1])
        if item in {"-c", "--config"}
        and value[i + 1].startswith("model_reasoning_effort=")
    ]
    return (
        len(model_positions) == 1
        and model_positions[0] + 1 < len(value)
        and value[model_positions[0] + 1] == REQUIRED_AGENT_MODEL
        and efforts == [f'model_reasoning_effort="{REQUIRED_REASONING_EFFORT}"']
        and len(cd_positions) == 1
        and cd_positions[0] + 1 < len(value)
        and Path(value[cd_positions[0] + 1]).resolve() == problem_dir
    )


def current_receipt_file(
    report: Report,
    check: Check,
    raw: Any,
    *,
    label: str,
    require_nonempty: bool,
) -> str | None:
    if not isinstance(raw, dict):
        check.fail(f"{label} must be a file receipt object")
        return None
    raw_path = raw.get("path")
    try:
        relative, path = safe_problem_path(
            report.problem_dir,
            raw_path,
            label=f"{label}.path",
            require_exists=False,
        )
    except ContractError as exc:
        check.fail(str(exc))
        return None
    current = stage_runner.file_state(path, relative)
    if raw != current:
        check.fail(f"{label} no longer matches the current file: {relative}")
        return None
    if require_nonempty and current.get("status") != "present-nonempty":
        check.fail(f"{label} is not a non-empty regular file: {relative}")
        return None
    if current.get("status") in {"present-nonempty", "empty"}:
        report.track(path)
    return relative


def check_stage_execution_receipts(
    report: Report, stages: tuple[str, ...] = PRE_READINESS_STAGES
) -> None:
    """Require real exact-model executions for every completed non-blind stage."""
    check = report.new_check("stage-execution-receipts")
    prior_finished: datetime | None = None
    receipt_hashes: dict[str, str] = {}
    for stage in stages:
        contract = stage_runner.STAGES[stage]
        relative = f"audit/private/stage-executions/{stage}/current.json"
        path = report.problem_dir / relative
        if not require_file(report, check, path, f"{stage} current execution receipt"):
            continue
        try:
            receipt = load_json(path)
        except ContractError as exc:
            check.fail(str(exc))
            continue
        if not isinstance(receipt, dict):
            check.fail(f"{relative} must contain a JSON object")
            continue
        try:
            validated_summary = stage_runner.require_prior_stage_receipt(
                report.problem_dir, stage
            )
        except (OSError, ValueError, stage_runner.ContractError) as exc:
            check.fail(f"{stage} recursive production receipt validation failed: {exc}")
            continue
        if validated_summary != {
            "stage": stage,
            "path": relative,
            "sha256": sha256_file(path),
        }:
            check.fail(f"{stage} recursive receipt summary is inconsistent")
        # The recursive stage-receipt validator below checks immutable attempts,
        # logs, and hash-bound preexisting archives. Do not feed that private
        # archive through the release-tree scanner: it intentionally preserves
        # stale empty/hidden artifacts that were removed from canonical paths.
        expected_scalars = {
            "schema_version": 1,
            "runner": "icpc-light-stage-agent-runner",
            "stage": stage,
            "execution_mode": "production-codex",
            "model": REQUIRED_AGENT_MODEL,
            "reasoning_effort": REQUIRED_REASONING_EFFORT,
            "exit_code": 0,
            "spawn_error": None,
            "interrupted": False,
            "success": True,
            "prompt_unchanged": True,
            "inputs_unchanged": True,
            "outputs_materially_updated": True,
            "output_trees_materially_updated": True,
            "codex_jsonl_required": True,
        }
        for key, expected in expected_scalars.items():
            if receipt.get(key) != expected:
                check.fail(f"{stage} receipt.{key} must be {expected!r}")
        if not exact_stage_command(receipt.get("command"), report.problem_dir):
            check.fail(
                f"{stage} receipt command is not exact production "
                f"{REQUIRED_AGENT_MODEL}/{REQUIRED_REASONING_EFFORT} codex exec"
            )
        started = parse_utc(receipt.get("started_at_utc"), f"{stage}.started")
        finished = parse_utc(receipt.get("finished_at_utc"), f"{stage}.finished")
        if started is None or finished is None or finished < started:
            check.fail(f"{stage} receipt has invalid start/finish timestamps")
        elif prior_finished is not None and started < prior_finished:
            check.fail(f"{stage} started before the preceding stage completed")
        if finished is not None:
            prior_finished = finished

        prompt_rel = current_receipt_file(
            report,
            check,
            receipt.get("prompt"),
            label=f"{stage} receipt.prompt",
            require_nonempty=True,
        )
        if prompt_rel is not None and not prompt_rel.startswith("audit/private/"):
            check.fail(f"{stage} prompt must stay under audit/private/")
        current_receipt_file(
            report,
            check,
            receipt.get("stdout_log"),
            label=f"{stage} receipt.stdout_log",
            require_nonempty=True,
        )
        current_receipt_file(
            report,
            check,
            receipt.get("stderr_log"),
            label=f"{stage} receipt.stderr_log",
            require_nonempty=False,
        )

        inputs = receipt.get("inputs")
        outputs = receipt.get("outputs")
        if not isinstance(inputs, list):
            check.fail(f"{stage} receipt.inputs must be a list")
            inputs = []
        if not isinstance(outputs, list):
            check.fail(f"{stage} receipt.outputs must be a list")
            outputs = []
        input_paths: set[str] = set()
        output_paths: set[str] = set()
        optional_output_paths = set(contract.optional_outputs)
        for index, item in enumerate(inputs):
            found = current_receipt_file(
                report,
                check,
                item,
                label=f"{stage} receipt.inputs[{index}]",
                require_nonempty=True,
            )
            if found:
                if found in input_paths:
                    check.fail(f"{stage} receipt duplicates input {found}")
                input_paths.add(found)
        for index, item in enumerate(outputs):
            raw_path = item.get("path") if isinstance(item, dict) else None
            found = current_receipt_file(
                report,
                check,
                item,
                label=f"{stage} receipt.outputs[{index}]",
                require_nonempty=raw_path not in optional_output_paths,
            )
            if found:
                if found in optional_output_paths:
                    try:
                        _, optional_path = safe_problem_path(
                            report.problem_dir,
                            found,
                            label=f"{stage} optional output",
                            require_exists=False,
                        )
                    except ContractError as exc:
                        check.fail(str(exc))
                    else:
                        state = stage_runner.file_state(optional_path, found)
                        safely_absent = (
                            not optional_path.exists()
                            and not optional_path.is_symlink()
                        )
                        if state.get("status") != "present-nonempty" and not safely_absent:
                            check.fail(f"{stage} optional output is unsafe: {found}")
                if found in output_paths:
                    check.fail(f"{stage} receipt duplicates output {found}")
                output_paths.add(found)
        missing_inputs = set(contract.inputs) - input_paths
        missing_outputs = set(contract.outputs) - output_paths
        if missing_inputs:
            check.fail(
                f"{stage} receipt omits required inputs: {sorted(missing_inputs)}"
            )
        if missing_outputs:
            check.fail(
                f"{stage} receipt omits required outputs: {sorted(missing_outputs)}"
            )
        missing_optional_outputs = optional_output_paths - output_paths
        if missing_optional_outputs:
            check.fail(
                f"{stage} receipt omits optional watched outputs: "
                f"{sorted(missing_optional_outputs)}"
            )

        trees = receipt.get("output_trees")
        if not isinstance(trees, list):
            check.fail(f"{stage} receipt.output_trees must be a list")
            trees = []
        tree_by_path: dict[str, dict[str, Any]] = {}
        for index, item in enumerate(trees):
            if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                check.fail(f"{stage} receipt.output_trees[{index}] is invalid")
                continue
            tree_rel = item["path"]
            if tree_rel in tree_by_path:
                check.fail(f"{stage} receipt duplicates output tree {tree_rel}")
                continue
            tree_by_path[tree_rel] = item
        preexisting_trees = receipt.get("preexisting_output_trees")
        if not isinstance(preexisting_trees, list):
            check.fail(f"{stage} receipt.preexisting_output_trees must be a list")
            preexisting_trees = []
        preexisting_by_path: dict[str, dict[str, Any]] = {}
        for index, item in enumerate(preexisting_trees):
            if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                check.fail(
                    f"{stage} receipt.preexisting_output_trees[{index}] is invalid"
                )
                continue
            tree_rel = item["path"]
            if tree_rel in preexisting_by_path:
                check.fail(f"{stage} receipt duplicates preexisting tree {tree_rel}")
                continue
            preexisting_by_path[tree_rel] = item
        optional_tree_paths = set(contract.optional_output_trees)
        for tree_rel in (*contract.output_trees, *contract.optional_output_trees):
            recorded = tree_by_path.get(tree_rel)
            if recorded is None:
                check.fail(f"{stage} receipt omits output tree {tree_rel}")
                continue
            preexisting = preexisting_by_path.get(tree_rel)
            if tree_rel in optional_tree_paths and preexisting is None:
                check.fail(
                    f"{stage} receipt omits preexisting snapshot for optional tree "
                    f"{tree_rel}"
                )
                continue
            try:
                stage_runner.validate_current_output_tree(
                    report.problem_dir,
                    stage,
                    tree_rel,
                    recorded,
                    preexisting=preexisting,
                )
            except (OSError, stage_runner.ContractError) as exc:
                check.fail(f"cannot validate {stage} output tree {tree_rel}: {exc}")
        if stage == "preclassification":
            blind_gate = receipt.get("blind_prerequisite_gate")
            if not isinstance(blind_gate, dict) or blind_gate.get("exit_code") != 0:
                check.fail("preclassification receipt lacks a passed blind prerequisite")
        receipt_hashes[stage] = sha256_file(path)

    if not check.issues:
        check.add(
            "ordered production receipts bind preclassification, solution draft, "
            "std materialization, concrete std validation, and build/hardening"
        )
        report.facts["stage_execution_receipts"] = receipt_hashes
        report.facts["stage_runner_sha256"] = sha256_file(
            Path(stage_runner.__file__).resolve()
        )


def check_blind_stage(report: Report) -> None:
    check = report.new_check("blind-stage")
    verifier = Path(__file__).resolve().with_name(BLIND_VERIFIER_NAME)
    if verifier == Path(__file__).resolve() or verifier.is_symlink() or not verifier.is_file():
        check.fail(
            f"dedicated {BLIND_VERIFIER_NAME} is missing; completion fails closed"
        )
        return
    try:
        verifier_hash = sha256_file(verifier)
        completed = subprocess.run(
            [sys.executable, str(verifier), "--problem-dir", str(report.problem_dir)],
            cwd=report.problem_dir,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        check.fail(f"could not execute {BLIND_VERIFIER_NAME}: {exc}")
        return
    if completed.returncode != 0:
        diagnostic = (completed.stderr or completed.stdout).strip()
        if len(diagnostic) > 6000:
            diagnostic = diagnostic[:6000] + "\n... output truncated ..."
        check.fail(
            f"{BLIND_VERIFIER_NAME} exited {completed.returncode}"
            + (f":\n{diagnostic}" if diagnostic else " without diagnostics")
        )
        return
    check.add(f"{BLIND_VERIFIER_NAME}: exit 0")
    report.blind_gate = {
        "verdict": "passed",
        "verifier": BLIND_VERIFIER_NAME,
        "verifier_sha256": verifier_hash,
    }


def check_required_artifacts(report: Report) -> None:
    check = report.new_check("required-artifacts")
    require_file(report, check, report.problem_dir / "statement.md", "statement.md")
    audit = report.problem_dir / "audit"
    if audit.is_symlink() or not audit.is_dir():
        check.fail(f"missing regular audit directory: {audit}")
        return
    for name in REQUIRED_AUDIT_FILES:
        path = audit / name
        if require_file(report, check, path, f"audit/{name}"):
            check.add(f"audit/{name}")
    blind_files = tree_files(
        report, "blind-solves/icpc-light", check, allow_empty=True
    )
    if blind_files:
        check.add(f"blind-solves/icpc-light: {len(blind_files)} retained file(s)")


def markdown_scalar(text: str, key: str) -> str | None:
    match = re.search(
        rf"(?im)^\s*(?:[-*]\s*)?(?:\*\*)?{re.escape(key)}"
        rf"(?:\*\*)?\s*[:：]\s*(.*?)\s*$",
        text,
    )
    if match is None:
        return None
    return match.group(1).strip().strip("`*_ ")


def check_run_state_policy(report: Report) -> None:
    check = report.new_check("run-state-policy")
    path = report.problem_dir / "audit/run-state.md"
    if not require_file(report, check, path, "audit/run-state.md"):
        return
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        check.fail(f"cannot read audit/run-state.md: {exc}")
        return
    expected = {
        "agent_model": REQUIRED_AGENT_MODEL,
        "agent_reasoning_effort": REQUIRED_REASONING_EFFORT,
        "model_policy_status": "enforced",
        "blind_status": "complete",
        "blind_time_limit_seconds": str(BLIND_TIME_LIMIT_SECONDS),
        "blind_failure_reason": "none",
    }
    for key, required in expected.items():
        actual = markdown_scalar(text, key)
        if actual != required:
            check.fail(f"run-state {key} must be {required!r}, got {actual!r}")
    if not check.issues:
        check.add(
            f"model={REQUIRED_AGENT_MODEL}; reasoning={REQUIRED_REASONING_EFFORT}; "
            f"blind limit={BLIND_TIME_LIMIT_SECONDS}s; blind complete"
        )
        report.facts["agent_model"] = REQUIRED_AGENT_MODEL
        report.facts["agent_reasoning_effort"] = REQUIRED_REASONING_EFFORT
        report.facts["model_policy_status"] = "enforced"
        report.facts["blind_time_limit_seconds"] = BLIND_TIME_LIMIT_SECONDS


def check_grade(
    report: Report, *, require_continuing: bool = True
) -> dict[str, Any] | None:
    check = report.new_check("data-buildability")
    path = report.problem_dir / "audit/data-buildability.md"
    if not path.is_file() or path.is_symlink():
        check.fail(f"missing regular data-buildability report: {path}")
        return None
    validator = PRECLASSIFICATION_VALIDATOR
    if validator.is_symlink() or not validator.is_file():
        check.fail(f"missing bundled preclassification validator: {validator}")
    else:
        try:
            completed = subprocess.run(
                [
                    sys.executable,
                    str(validator),
                    "--report",
                    str(path),
                    "--json",
                ],
                cwd=report.problem_dir,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            check.fail(f"could not execute preclassification validator: {exc}")
        else:
            if completed.returncode != 0:
                diagnostic = (completed.stderr or completed.stdout).strip()
                if len(diagnostic) > 4000:
                    diagnostic = diagnostic[:4000] + "\n... output truncated ..."
                check.fail(
                    "replaceable preclassification interface validator failed"
                    + (f":\n{diagnostic}" if diagnostic else "")
                )
            else:
                report.facts["preclassification_validator_sha256"] = sha256_file(
                    validator
                )
    try:
        grade = parse_front_matter(path)
    except ContractError as exc:
        check.fail(str(exc))
        return None
    require_fields(check, grade, GRADE_REQUIRED_FIELDS, "data-buildability front matter")
    if grade.get("schema_version") != 2:
        check.fail("data-buildability schema_version must be integer 2")
    if grade.get("agent_model") != REQUIRED_AGENT_MODEL:
        check.fail(f"data-buildability.agent_model must be {REQUIRED_AGENT_MODEL!r}")
    if grade.get("agent_reasoning_effort") != REQUIRED_REASONING_EFFORT:
        check.fail(
            "data-buildability.agent_reasoning_effort must be "
            f"{REQUIRED_REASONING_EFFORT!r}"
        )
    if require_continuing:
        if grade.get("decision") != "continue":
            check.fail("data-buildability decision must be 'continue'")
        if grade.get("provisional") is not False:
            check.fail("data-buildability provisional must be YAML boolean false")
        if grade.get("scam_status") not in {"none", "confirmed"}:
            check.fail(
                "continuing data-buildability scam_status must be 'none' or "
                "'confirmed'"
            )
        if grade.get("stop_reason") != "none":
            check.fail("data-buildability stop_reason must be 'none'")
    if grade.get("stop_reason") not in GRADE_STOP_REASONS:
        check.fail("data-buildability.stop_reason is outside the schema-v2 enum")
    if grade.get("confidence") not in {"low", "medium", "high"}:
        check.fail("data-buildability.confidence must be low, medium, or high")
    for key in ("risk_tags", "required_checks", "regrade_triggers"):
        if not isinstance(grade.get(key), list):
            check.fail(f"data-buildability.{key} must be a YAML list")
        elif not all(isinstance(item, str) and non_placeholder(item) for item in grade[key]):
            check.fail(f"data-buildability.{key} must contain non-empty strings")

    minimum = require_int(check, grade, "wrong_solution_min", "data-buildability")
    maximum = require_int(check, grade, "wrong_solution_max", "data-buildability")
    round_min = require_int(check, grade, "adversarial_round_min", "data-buildability")
    round_max = require_int(check, grade, "adversarial_round_max", "data-buildability")
    pre = grade.get("preclassification")
    profile = grade.get("workflow_profile")
    data_grade = grade.get("data_buildability")
    mode = grade.get("adversarial_round_mode")
    if require_continuing:
        if pre == "P1-random-strong":
            valid = (
                data_grade == "D0-direct"
                and profile == "L0-simple-standard"
                and (minimum, maximum, mode, round_min, round_max)
                == (3, 5, "single", 1, 1)
            )
        elif pre == "P2-structured-bounded":
            valid = (
                data_grade == "D1-structured"
                and profile
                in {
                    "L1-ordinary",
                    "L1G-greedy-deceptive",
                    "L1C-constructive-output",
                    "L1F-flow-model-like",
                }
                and (minimum, maximum, mode, round_min, round_max)
                == (5, 8, "single", 1, 1)
            )
        elif pre == "P3-adversarial-intensive":
            valid = (
                data_grade == "D2-specialist"
                and profile == "L2-high-risk"
                and (minimum, maximum, mode, round_min, round_max)
                == (8, 10, "bounded-multi", 1, 3)
            )
        else:
            valid = False
        if not valid:
            check.fail(
                "preclassification, profile, quota, or round range is inconsistent"
            )
    else:
        # The replaceable schema-v2 validator is authoritative for legal
        # continuing, escalation, and terminal transitions.  This gate only
        # records the transition; package stages separately require continue.
        if pre not in {
            "P1-random-strong",
            "P2-structured-bounded",
            "P3-adversarial-intensive",
            "S-stop",
        }:
            check.fail("data-buildability preclassification is outside schema-v2")
        if grade.get("decision") not in {"continue", "escalate", "stop"}:
            check.fail("data-buildability decision is outside schema-v2")
    if not check.issues:
        check.add(
            f"schema-v2 {pre}; profile={profile}; "
            f"decision={grade.get('decision')}; scam_status={grade.get('scam_status')}"
        )
    return grade


def check_verified_claims(report: Report) -> list[dict[str, str]]:
    check = report.new_check("verified-blind-claims")
    path = report.problem_dir / "audit/blind-claim-reviews.json"
    try:
        data = load_json(path)
    except ContractError as exc:
        check.fail(str(exc))
        return []
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        check.fail("blind claim reviews must be a schema_version 1 JSON object")
        return []
    reviews = data.get("reviews")
    if not isinstance(reviews, list):
        check.fail("blind claim reviews.reviews must be a list")
        return []
    verified: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for index, review in enumerate(reviews):
        if not isinstance(review, dict):
            check.fail(f"blind claim reviews[{index}] must be an object")
            continue
        active = review.get("active")
        invalidated_by = review.get("invalidated_by")
        if type(active) is not bool:
            check.fail(
                f"blind claim reviews[{index}].active must be a JSON boolean"
            )
            continue
        if active is True and invalidated_by is not None:
            check.fail(
                f"blind claim reviews[{index}] active claim must have "
                "invalidated_by: null"
            )
            continue
        if active is False:
            try:
                invalidation_rel, invalidation_path = safe_problem_path(
                    report.problem_dir,
                    invalidated_by,
                    label=f"blind claim reviews[{index}].invalidated_by",
                    require_exists=True,
                )
            except ContractError as exc:
                check.fail(str(exc))
                continue
            require_file(
                report,
                check,
                invalidation_path,
                f"blind claim invalidation evidence {invalidation_rel}",
            )
            continue
        if not (
            review.get("claim_type") == "full-solution"
            and review.get("independent") is True
            and review.get("status") == "verified"
        ):
            continue
        source_raw = review.get("source_path")
        digest = review.get("source_sha256")
        try:
            source_rel, source = safe_problem_path(
                report.problem_dir,
                source_raw,
                label=f"blind claim reviews[{index}].source_path",
                require_exists=True,
            )
        except ContractError as exc:
            check.fail(str(exc))
            continue
        if not isinstance(digest, str) or HASH_RE.fullmatch(digest) is None:
            check.fail(
                f"blind claim reviews[{index}].source_sha256 must be 64 lowercase hex digits"
            )
            continue
        if not require_file(report, check, source, f"verified blind source {source_rel}"):
            continue
        try:
            actual = sha256_file(source)
        except OSError as exc:
            check.fail(f"cannot hash verified blind source {source_rel}: {exc}")
            continue
        if actual != digest:
            check.fail(f"verified blind source hash changed: {source_rel}")
            continue
        for field_name in ("review_report", "execution_receipt"):
            try:
                evidence_rel, evidence_path = safe_problem_path(
                    report.problem_dir,
                    review.get(field_name),
                    label=f"blind claim reviews[{index}].{field_name}",
                    require_exists=True,
                )
            except ContractError as exc:
                check.fail(str(exc))
                continue
            if not require_file(
                report, check, evidence_path, f"verified claim evidence {evidence_rel}"
            ):
                continue
        key = (source_rel, digest)
        if key not in seen:
            seen.add(key)
            verified.append({"source_path": source_rel, "source_sha256": digest})
    if not verified:
        check.fail("no independently verified full blind claim with bound source/hash")
    else:
        check.add(f"{len(verified)} independently verified full blind source(s)")
    return verified


def check_selected_standard_route(
    report: Report,
    grade: dict[str, Any] | None,
    verified_claims: list[dict[str, str]],
) -> Path | None:
    """Bind the preclassifier-selected executable route used to build std.

    The path is fixed so downstream schemas do not gain another set of fragile
    frontmatter fields.  Stage receipts hash it as a required output/input.
    Ordinary and unresolved-shortcut runs must preserve an active verified
    blind route byte-for-byte.  A confirmed simpler route may replace it only
    on a non-provisional continuing P1/P2/P3 transition.
    """

    check = report.new_check("selected-standard-route")
    selected = report.problem_dir / SELECTED_STANDARD_ROUTE_REL
    if not require_file(report, check, selected, SELECTED_STANDARD_ROUTE_REL):
        return None
    try:
        digest = sha256_file(selected)
    except OSError as exc:
        check.fail(f"cannot hash {SELECTED_STANDARD_ROUTE_REL}: {exc}")
        return None
    if grade is None:
        check.fail("cannot validate selected standard route without a valid grade")
        return None

    scam_status = grade.get("scam_status")
    if scam_status in {"none", "suspected"}:
        if not any(item["source_sha256"] == digest for item in verified_claims):
            check.fail(
                "without a confirmed simpler route, selected-standard-route.cpp "
                "must be byte-identical to an active verified blind source"
            )
        route_kind = "verified-blind"
    elif scam_status == "confirmed":
        if not (
            grade.get("preclassification")
            in {
                "P1-random-strong",
                "P2-structured-bounded",
                "P3-adversarial-intensive",
            }
            and grade.get("decision") == "continue"
            and grade.get("provisional") is False
            and grade.get("stop_reason") == "none"
        ):
            check.fail(
                "a confirmed simpler selected route requires a non-provisional "
                "continuing P1/P2/P3 grade"
            )
        route_kind = "verified-simpler"
    else:
        check.fail("selected route has an invalid scam_status transition")
        route_kind = "invalid"

    if not check.issues:
        check.add(
            f"{SELECTED_STANDARD_ROUTE_REL} is bound as {route_kind}: {digest}"
        )
        report.facts["selected_standard_route_path"] = SELECTED_STANDARD_ROUTE_REL
        report.facts["selected_standard_route_sha256"] = digest
        report.facts["selected_standard_route_kind"] = route_kind
        return selected
    return None


def check_solution_draft_and_materialization(
    report: Report,
    verified_claims: list[dict[str, str]],
    selected_route: Path | None,
) -> str | None:
    check = report.new_check("solution-draft-materialization")
    draft_path = report.problem_dir / "audit/solution-review-draft.md"
    material_path = report.problem_dir / "audit/std-materialization.md"
    try:
        draft = parse_front_matter(draft_path)
        material = parse_front_matter(material_path)
    except ContractError as exc:
        check.fail(str(exc))
        return None
    require_fields(check, draft, DRAFT_REVIEW_REQUIRED_FIELDS, "solution draft")
    require_fields(
        check, material, MATERIALIZATION_REQUIRED_FIELDS, "std materialization"
    )
    for label, data in (("solution draft", draft), ("std materialization", material)):
        if data.get("schema_version") != 1:
            check.fail(f"{label}.schema_version must be integer 1")
        if data.get("agent_model") != REQUIRED_AGENT_MODEL:
            check.fail(f"{label}.agent_model must be {REQUIRED_AGENT_MODEL!r}")
        if data.get("agent_reasoning_effort") != REQUIRED_REASONING_EFFORT:
            check.fail(
                f"{label}.agent_reasoning_effort must be "
                f"{REQUIRED_REASONING_EFFORT!r}"
            )
    if draft.get("review_status") != "passed":
        check.fail("solution draft.review_status must be 'passed'")
    if material.get("status") != "passed":
        check.fail("std materialization.status must be 'passed'")
    source_path = draft.get("blind_source_path")
    source_hash = draft.get("blind_source_sha256")
    if not any(
        item["source_path"] == source_path and item["source_sha256"] == source_hash
        for item in verified_claims
    ):
        check.fail("solution draft must bind one active verified blind source")
    if (
        material.get("blind_source_path") != source_path
        or material.get("blind_source_sha256") != source_hash
    ):
        check.fail("std materialization blind provenance differs from solution draft")
    if selected_route is None:
        check.fail("selected standard route did not pass its provenance gate")
        selected_hash = None
    else:
        selected_hash = sha256_file(selected_route)
    if material.get("std_path") != "package/std.cpp":
        check.fail("std materialization.std_path must be package/std.cpp")
    std_path = report.problem_dir / "package/std.cpp"
    if require_file(report, check, std_path, "package/std.cpp"):
        actual_hash = sha256_file(std_path)
        if material.get("std_sha256") != actual_hash:
            check.fail("std materialization.std_sha256 does not match package/std.cpp")
    else:
        actual_hash = None
    mode = material.get("materialization_mode")
    if mode not in {"exact-copy", "adapted"}:
        check.fail("std materialization.materialization_mode must be exact-copy or adapted")
    if (
        mode == "exact-copy"
        and actual_hash is not None
        and selected_hash is not None
        and actual_hash != selected_hash
    ):
        check.fail(
            "exact-copy materialization must be byte-identical to the selected "
            "standard route"
        )
    if mode == "adapted":
        try:
            text = material_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            check.fail(f"cannot read std materialization delta description: {exc}")
        else:
            delta = re.search(
                r"(?ims)^##\s+Semantic Deltas\s*$\s*(.+?)(?=^##\s|\Z)", text
            )
            if delta is None or not non_placeholder(delta.group(1)):
                check.fail("adapted materialization requires a concrete Semantic Deltas section")
    if not check.issues:
        check.add(f"draft proof and {mode} std materialization are hash-bound")
        report.facts["std_materialization_mode"] = mode
        report.facts["std_provenance_path"] = SELECTED_STANDARD_ROUTE_REL
        report.facts["std_provenance_sha256"] = selected_hash
    return mode if not check.issues else None


def check_solution_provenance(
    report: Report,
    materialization_mode: str | None,
    selected_route: Path | None,
) -> Path | None:
    check = report.new_check("std-provenance")
    path = report.problem_dir / "audit/solution-review.md"
    try:
        review = parse_front_matter(path)
    except ContractError as exc:
        check.fail(str(exc))
        return None
    require_fields(check, review, SOLUTION_REVIEW_REQUIRED_FIELDS, "solution-review front matter")
    if review.get("schema_version") != 1:
        check.fail("solution-review schema_version must be integer 1")
    if review.get("agent_model") != REQUIRED_AGENT_MODEL:
        check.fail(f"solution-review.agent_model must be {REQUIRED_AGENT_MODEL!r}")
    if review.get("agent_reasoning_effort") != REQUIRED_REASONING_EFFORT:
        check.fail(
            "solution-review.agent_reasoning_effort must be "
            f"{REQUIRED_REASONING_EFFORT!r}"
        )
    for key in (
        "review_status",
        "std_compilation",
        "materialization_delta_review",
    ):
        if review.get(key) != "passed":
            check.fail(f"solution-review.{key} must be exactly 'passed'")
    for key in ("public_samples", "tiny_differential"):
        if review.get(key) != "pending-machine-regression":
            check.fail(
                f"solution-review.{key} must be 'pending-machine-regression'; "
                "only regression-machine.json may certify execution"
            )
    if review.get("materialization_mode") != materialization_mode:
        check.fail(
            "solution-review.materialization_mode must match std-materialization.md"
        )
    try:
        std_rel, std = safe_problem_path(
            report.problem_dir,
            review.get("std_path"),
            label="solution-review.std_path",
            require_exists=True,
        )
    except ContractError as exc:
        check.fail(str(exc))
        return None
    if std_rel != "package/std.cpp":
        check.fail("solution-review.std_path must be exactly package/std.cpp")
    std_hash = review.get("std_sha256")
    if not isinstance(std_hash, str) or HASH_RE.fullmatch(std_hash) is None:
        check.fail("solution-review.std_sha256 must be 64 lowercase hex digits")
    elif require_file(report, check, std, "package/std.cpp"):
        actual = sha256_file(std)
        if actual != std_hash:
            check.fail("solution-review.std_sha256 does not match package/std.cpp")
        report.facts["std_path"] = std_rel
        report.facts["std_sha256"] = actual

    provenance_path = review.get("std_provenance_path")
    provenance_hash = review.get("std_provenance_sha256")
    selected_hash = sha256_file(selected_route) if selected_route is not None else None
    if (
        selected_route is None
        or provenance_path != SELECTED_STANDARD_ROUTE_REL
        or provenance_hash != selected_hash
    ):
        check.fail(
            "solution-review std provenance must match the fixed selected standard "
            "route source/path hash"
        )
    if (
        provenance_path != report.facts.get("std_provenance_path")
        or provenance_hash != report.facts.get("std_provenance_sha256")
    ):
        check.fail(
            "solution-review std provenance must match the reviewed draft and "
            "materialization provenance"
        )
    if not check.issues:
        check.add(f"{std_rel} hash and selected-route provenance are bound")
    return std


def normalize_header(value: str) -> str:
    value = re.sub(r"[`*]", "", value.strip().lower())
    return re.sub(r"[^a-z0-9]+", "_", value).strip("_")


def split_markdown_row(line: str) -> list[str]:
    stripped = line.strip()
    if not stripped.startswith("|"):
        return []
    if stripped.endswith("|"):
        stripped = stripped[1:-1]
    else:
        stripped = stripped[1:]
    cells: list[str] = []
    current: list[str] = []
    escaped = False
    for char in stripped:
        if escaped:
            current.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == "|":
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    current.append("\\" if escaped else "")
    cells.append("".join(current).strip())
    return cells


def markdown_table(path: Path, required_headers: set[str]) -> list[dict[str, str]]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    for index, line in enumerate(lines[:-1]):
        cells = split_markdown_row(line)
        headers = [normalize_header(cell) for cell in cells]
        if not required_headers.issubset(headers):
            continue
        separator = split_markdown_row(lines[index + 1])
        if len(separator) != len(headers) or not all(
            re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in separator
        ):
            continue
        rows: list[dict[str, str]] = []
        for raw in lines[index + 2 :]:
            values = split_markdown_row(raw)
            if not values:
                break
            if len(values) < len(headers):
                values.extend([""] * (len(headers) - len(values)))
            rows.append(dict(zip(headers, values[: len(headers)])))
        return rows
    raise ContractError(
        f"{path} has no Markdown table containing headers {sorted(required_headers)}"
    )


def unwrap_markdown_path(value: str) -> str:
    stripped = value.strip().strip("`")
    match = re.fullmatch(r"\[[^]]*\]\(([^)]+)\)", stripped)
    if match:
        stripped = match.group(1).strip()
    return stripped.strip("<>")


def non_placeholder(value: str) -> bool:
    normalized = value.strip().strip("`*_ ").lower()
    return normalized not in {"", "-", "none", "n/a", "na", "tbd", "todo", "pending"}


def check_wrong_solutions(
    report: Report, grade: dict[str, Any] | None
) -> list[tuple[str, Path]]:
    check = report.new_check("qualified-wrong-solutions")
    tree_files(report, "audit/private/wrong-solutions", check)
    path = report.problem_dir / "audit/wrong-solutions.md"
    try:
        rows = markdown_table(
            path,
            {
                "route_id",
                "wrong_assumption",
                "why_plausible",
                "hardening_applied",
                "trivial_dominator_check",
                "survivability_evidence",
                "private_source",
                "compile_status",
                "public_samples",
                "ordinary_case",
                "expected_failure",
                "breaker_family_test",
                "breaker_status",
                "observed_verdict",
                "priority",
                "introduced_round",
                "killed_round",
                "qualified",
            },
        )
    except (OSError, UnicodeError, ContractError) as exc:
        check.fail(str(exc))
        return []

    qualified_rows = [
        row
        for row in rows
        if row.get("qualified", "").strip().strip("`*_ ").lower()
        in {"yes", "true", "qualified"}
    ]
    minimum = grade.get("wrong_solution_min") if grade else None
    maximum = grade.get("wrong_solution_max") if grade else None
    if type(minimum) is not int or type(maximum) is not int:
        check.fail("cannot establish wrong-solution quota from data-buildability")
    elif not minimum <= len(qualified_rows) <= maximum:
        check.fail(
            f"qualified wrong-solution count {len(qualified_rows)} is outside "
            f"the graded range {minimum}..{maximum}"
        )

    sources: list[tuple[str, Path]] = []
    matrix: list[dict[str, str]] = []
    seen_routes: set[str] = set()
    seen_paths: set[str] = set()
    seen_hashes: set[str] = set()
    rejected_re = re.compile(
        r"(?i)(?:\brejected\b|\bwrong[ -]?answer\b|\bWA\b|\bTLE\b|\bMLE\b|\bRE\b|"
        r"time[ -]?limit|memory[ -]?limit|runtime[ -]?error|overflow)"
    )
    for index, row in enumerate(qualified_rows):
        label = f"qualified wrong row {index + 1}"
        route_id = row.get("route_id", "").strip().strip("`")
        if not route_id or route_id in seen_routes:
            check.fail(f"{label}: route_id must be non-empty and unique")
        seen_routes.add(route_id)

        breaker = row.get("breaker_family_test", "")
        if not non_placeholder(breaker):
            check.fail(f"{label}: breaker family/test must be concrete")
        breaker_raw = unwrap_markdown_path(breaker)
        try:
            breaker_rel, breaker_path = safe_problem_path(
                report.problem_dir,
                breaker_raw,
                label=f"{label} breaker input",
                require_exists=True,
            )
        except ContractError as exc:
            check.fail(str(exc))
            breaker_rel = ""
        else:
            if not breaker_rel.startswith(
                "package/tests/breakers/"
            ) or not breaker_rel.endswith(".in"):
                check.fail(
                    f"{label}: breaker must be a package/tests/breakers/*.in input"
                )
            require_file(report, check, breaker_path, f"{label} breaker input")
        verdict = row.get("observed_verdict", "")
        if not non_placeholder(verdict) or rejected_re.search(verdict) is None:
            check.fail(f"{label}: observed verdict must record an actual rejection")
        for key in ("compile_status", "public_samples", "ordinary_case"):
            if row.get(key, "").strip().strip("`*_ ").lower() != "passed":
                check.fail(f"{label}: {key} must be passed")
        if row.get("breaker_status", "").strip().strip("`*_ ").lower() not in {
            "passed",
            "rejected",
        }:
            check.fail(f"{label}: breaker_status must record passed/rejected")
        for key in (
            "wrong_assumption",
            "why_plausible",
            "hardening_applied",
            "survivability_evidence",
        ):
            if not non_placeholder(row.get(key, "")):
                check.fail(f"{label}: {key} must be concrete")
        dominator = row.get("trivial_dominator_check", "").strip().strip("`*_ ")
        if not non_placeholder(dominator) or re.search(
            r"(?i)\b(passed|no[- ]dominator|strongest[- ]natural)\b", dominator
        ) is None:
            check.fail(
                f"{label}: trivial_dominator_check must record a passed "
                "strongest-natural-variant review"
            )
        expected_failure = row.get("expected_failure", "").strip().upper()
        expected_match = re.search(r"\b(WA|TLE|MLE|OLE|RE)\b", expected_failure)
        observed_match = re.search(r"(?i)\b(WA|TLE|MLE|OLE|RE)\b", verdict)
        if expected_match is None:
            check.fail(
                f"{label}: expected_failure must name WA, TLE, MLE, OLE, or RE"
            )
        if observed_match is None:
            check.fail(
                f"{label}: observed_verdict must name WA, TLE, MLE, OLE, or RE"
            )
        elif expected_match is not None and observed_match.group(1).upper() != expected_match.group(1):
            check.fail(f"{label}: observed verdict differs from expected failure")
        if not non_placeholder(row.get("priority", "")):
            check.fail(f"{label}: priority must be concrete")
        round_values: dict[str, str] = {}
        for key in ("introduced_round", "killed_round"):
            raw_round = row.get(key, "").strip().strip("`")
            if not re.fullmatch(r"[1-9]\d*", raw_round):
                check.fail(f"{label}: {key} must be a positive integer")
            round_values[key] = raw_round

        source_raw = unwrap_markdown_path(row.get("private_source", ""))
        try:
            source_rel, source = safe_problem_path(
                report.problem_dir,
                source_raw,
                label=f"{label} private source",
                require_exists=True,
            )
        except ContractError as exc:
            check.fail(str(exc))
            continue
        if not source_rel.startswith("audit/private/wrong-solutions/"):
            check.fail(
                f"{label}: private source must be below audit/private/wrong-solutions/"
            )
        if source_rel in seen_paths:
            check.fail(f"{label}: source path is reused by another qualified route")
        seen_paths.add(source_rel)
        if not require_file(report, check, source, f"{label} source"):
            continue
        digest = sha256_file(source)
        if digest in seen_hashes:
            check.fail(f"{label}: source content duplicates another qualified route")
        seen_hashes.add(digest)
        if source.suffix.lower() not in CPP_SUFFIXES:
            check.fail(f"{label}: qualified source must be compilable C/C++")
        sources.append((route_id, source))
        matrix.append(
            {
                "route_id": route_id,
                "source": source_rel,
                "source_sha256": digest,
                "breaker_input": breaker_rel,
                "expected_verdict": (
                    expected_match.group(1) if expected_match is not None else ""
                ),
                "observed_verdict": (
                    observed_match.group(1).upper()
                    if observed_match is not None
                    else ""
                ),
                "hardening_applied": row.get("hardening_applied", "").strip(),
                "trivial_dominator_check": dominator,
                "survivability_evidence": row.get(
                    "survivability_evidence", ""
                ).strip(),
                **round_values,
            }
        )

    if not check.issues:
        check.add(f"{len(qualified_rows)} qualified rows with distinct sources and breakers")
    report.facts["wrong_solutions_qualified"] = len(qualified_rows)
    report.facts["wrong_route_matrix"] = matrix
    return sources


def check_adversarial_rounds(
    report: Report, grade: dict[str, Any] | None
) -> int | None:
    check = report.new_check("adversarial-rounds")
    path = report.problem_dir / "audit/adversarial-rounds.md"
    try:
        rows = markdown_table(
            path,
            {
                "round",
                "trigger",
                "active_routes",
                "new_attack_hypothesis",
                "new_changed_breakers",
                "killed",
                "survivors",
                "commands",
                "material_result",
            },
        )
    except (OSError, UnicodeError, ContractError) as exc:
        check.fail(str(exc))
        return None
    rounds: set[int] = set()
    for index, row in enumerate(rows):
        raw = row.get("round", "").strip().strip("`")
        if not re.fullmatch(r"[1-9]\d*", raw):
            check.fail(f"adversarial round row {index + 1}: round must be a positive integer")
            continue
        number = int(raw)
        if number != index + 1:
            check.fail(
                f"adversarial round row {index + 1}: rows must be ordered consecutively from 1"
            )
        if number in rounds:
            check.fail(f"adversarial round {number} is duplicated")
        rounds.add(number)
        if not non_placeholder(row.get("trigger", "")):
            check.fail(f"adversarial round {number}: trigger is missing")
        if not non_placeholder(row.get("material_result", "")):
            check.fail(f"adversarial round {number}: material result is missing")
        for key in (
            "active_routes",
            "new_attack_hypothesis",
            "new_changed_breakers",
            "commands",
        ):
            if not non_placeholder(row.get(key, "")):
                check.fail(f"adversarial round {number}: {key} is missing")
        for key in ("killed", "survivors"):
            if not isinstance(row.get(key), str) or not row.get(key, "").strip():
                check.fail(f"adversarial round {number}: {key} is missing")
        if number == 1 and row.get("trigger", "").strip().strip("`") != "initial-matrix":
            check.fail("adversarial round 1 trigger must be initial-matrix")
    if rounds and rounds != set(range(1, max(rounds) + 1)):
        check.fail("adversarial rounds must be consecutive from 1")
    completed = len(rounds)
    minimum = grade.get("adversarial_round_min") if grade else None
    maximum = grade.get("adversarial_round_max") if grade else None
    if type(minimum) is not int or type(maximum) is not int:
        check.fail("cannot establish adversarial-round range from data-buildability")
    elif not minimum <= completed <= maximum:
        check.fail(
            f"completed adversarial rounds {completed} are outside graded range "
            f"{minimum}..{maximum}"
        )

    def route_list(raw: str, label: str) -> list[str]:
        value = raw.strip().strip("`*_ ")
        if value.lower() in {"none", "n/a", "na", "-", "[]"}:
            return []
        tokens = [
            token.strip().strip("`*_ ")
            for token in re.split(r"[,;\s]+", value)
            if token.strip().strip("`*_ ")
        ]
        if any(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", token) is None for token in tokens):
            check.fail(f"{label}: route list contains an invalid route ID")
        if len(tokens) != len(set(tokens)):
            check.fail(f"{label}: route list contains duplicates")
        return tokens

    if type(minimum) is int and type(maximum) is int:
        chain = adversarial_chain.verify_chain(report.problem_dir, minimum, maximum)
        for issue in chain.issues:
            check.fail(f"machine receipt chain: {issue}")
        plan_files = tree_files(
            report,
            "audit/adversarial-round-plans",
            check,
            allow_empty=False,
        )
        receipt_files = tree_files(
            report,
            "audit/adversarial-round-receipts",
            check,
            allow_empty=False,
        )
        if len(plan_files) != completed or len(receipt_files) != completed:
            check.fail(
                "adversarial round plan/receipt file counts must equal the Markdown round count"
            )
        if chain.rounds != completed:
            check.fail(
                f"machine receipt chain records {chain.rounds} rounds, Markdown records {completed}"
            )
        actual_route_rounds: dict[str, dict[str, int]] = {}
        already_killed: set[str] = set()
        for number in range(1, completed + 1):
            receipt_path = (
                report.problem_dir
                / "audit/adversarial-round-receipts"
                / f"round-{number:02d}.json"
            )
            try:
                receipt = load_json(receipt_path)
            except ContractError as exc:
                check.fail(str(exc))
                continue
            if not isinstance(receipt, dict):
                check.fail(f"adversarial round {number}: receipt must be an object")
                continue
            route_ids = [
                item.get("route_id")
                for item in receipt.get("routes", [])
                if isinstance(item, dict) and isinstance(item.get("route_id"), str)
            ]
            killed_ids = receipt.get("killed")
            survivor_ids = receipt.get("survivors")
            if not isinstance(killed_ids, list) or not all(
                isinstance(item, str) for item in killed_ids
            ):
                killed_ids = []
            if not isinstance(survivor_ids, list) or not all(
                isinstance(item, str) for item in survivor_ids
            ):
                survivor_ids = []
            resurrected = already_killed & set(route_ids)
            if resurrected:
                check.fail(
                    f"adversarial round {number}: killed routes reappeared: {sorted(resurrected)}"
                )
            for route_id in route_ids:
                actual_route_rounds.setdefault(route_id, {"introduced": number})
            for route_id in killed_ids:
                actual_route_rounds.setdefault(route_id, {"introduced": number})[
                    "killed"
                ] = number
            already_killed.update(killed_ids)
            if number <= len(rows):
                row = rows[number - 1]
                if row.get("trigger", "").strip().strip("`") != receipt.get("trigger"):
                    check.fail(
                        f"adversarial round {number}: Markdown trigger differs from receipt"
                    )
                comparisons = {
                    "active_routes": route_ids,
                    "killed": killed_ids,
                    "survivors": survivor_ids,
                }
                for field, expected_ids in comparisons.items():
                    actual_ids = route_list(
                        row.get(field, ""),
                        f"adversarial round {number}.{field}",
                    )
                    if actual_ids != expected_ids:
                        check.fail(
                            f"adversarial round {number}: Markdown {field} differs from machine receipt"
                        )
        if chain.survivors:
            check.fail(
                "final adversarial round still has survivor routes: "
                + ", ".join(chain.survivors)
            )
        matrix = report.facts.get("wrong_route_matrix")
        if isinstance(matrix, list):
            for item in matrix:
                route_id = item.get("route_id")
                actual = actual_route_rounds.get(str(route_id))
                if actual is None or "killed" not in actual:
                    check.fail(
                        f"qualified wrong route {route_id} lacks an actual adversarial-round kill"
                    )
                    continue
                try:
                    claimed_introduced = int(item.get("introduced_round", ""))
                    claimed_killed = int(item.get("killed_round", ""))
                except (TypeError, ValueError):
                    continue
                if claimed_introduced != actual["introduced"]:
                    check.fail(
                        f"wrong route {route_id}: introduced_round differs from machine chain"
                    )
                if claimed_killed != actual["killed"]:
                    check.fail(
                        f"wrong route {route_id}: killed_round differs from machine chain"
                    )
        chain_verifier_path = Path(adversarial_chain.__file__).resolve()
        round_recorder_path = chain_verifier_path.with_name(
            "record_adversarial_round.py"
        )
        report.facts["adversarial_round_chain_sha256"] = (
            adversarial_chain.canonical_digest(chain.receipt_hashes)
        )
        report.facts["adversarial_round_receipt_hashes"] = chain.receipt_hashes
        report.facts["adversarial_round_verifier_sha256"] = sha256_file(
            chain_verifier_path
        )
        report.facts["adversarial_round_recorder_sha256"] = sha256_file(
            round_recorder_path
        )
    matrix = report.facts.get("wrong_route_matrix")
    if isinstance(matrix, list):
        for item in matrix:
            try:
                introduced = int(item.get("introduced_round", ""))
                killed = int(item.get("killed_round", ""))
            except (TypeError, ValueError):
                continue
            route_id = item.get("route_id")
            if introduced > killed:
                check.fail(f"wrong route {route_id}: introduced round exceeds killed round")
            if killed > completed:
                check.fail(f"wrong route {route_id}: killed round exceeds completed rounds")
    if not check.issues:
        check.add(f"{completed} consecutive completed adversarial round(s)")
    report.facts["adversarial_rounds_completed"] = completed
    return completed


def check_test_manifest(report: Report) -> int:
    check = report.new_check("test-manifest")
    path = report.problem_dir / "audit/test-manifest.md"
    try:
        rows = markdown_table(
            path,
            {
                "family_id",
                "purpose",
                "command_or_fixed_file",
                "seed_params",
                "size_limits_reached",
                "target_routes",
                "validator_status",
                "introduced_round",
            },
        )
    except (OSError, UnicodeError, ContractError) as exc:
        check.fail(str(exc))
        return 0
    if not rows:
        check.fail("test manifest must contain at least one purposeful family")
    seen: set[str] = set()
    manifest_families: dict[str, dict[str, Any]] = {}

    def comma_items(raw: str) -> list[str]:
        value = raw.strip().strip("`*_ ")
        return [
            item.strip().strip("`*_ ")
            for item in re.split(r"[,;]", value)
            if item.strip().strip("`*_ ")
        ]

    def route_items(raw: str) -> list[str]:
        value = raw.strip().strip("`*_ ")
        return [
            item.strip().strip("`*_ ")
            for item in re.split(r"[,;\s]+", value)
            if item.strip().strip("`*_ ")
        ]

    for index, row in enumerate(rows, 1):
        family_id = row.get("family_id", "").strip().strip("`")
        if not family_id or family_id in seen:
            check.fail(f"test family row {index}: family_id must be non-empty and unique")
        seen.add(family_id)
        for key in (
            "purpose",
            "command_or_fixed_file",
            "seed_params",
            "size_limits_reached",
            "target_routes",
            "introduced_round",
        ):
            if not non_placeholder(row.get(key, "")):
                check.fail(f"test family {family_id or index}: {key} must be concrete")
        if row.get("validator_status", "").strip().strip("`*_ ").lower() != "passed":
            check.fail(f"test family {family_id or index}: validator_status must be passed")
        seed_params = comma_items(row.get("seed_params", ""))
        target_routes = route_items(row.get("target_routes", ""))
        if len(seed_params) != len(set(seed_params)):
            check.fail(f"test family {family_id or index}: seed_params contains duplicates")
        if len(target_routes) != len(set(target_routes)):
            check.fail(f"test family {family_id or index}: target_routes contains duplicates")
        if family_id:
            manifest_families[family_id] = {
                "purpose": " ".join(row.get("purpose", "").split()),
                "command_or_fixed_file": row.get(
                    "command_or_fixed_file", ""
                ).strip().strip("`"),
                "seed_params": seed_params,
                "target_routes": target_routes,
            }
    if not check.issues:
        check.add(
            f"{len(rows)} purposeful family row(s), all replayable, targeted, "
            "and validator-passed"
        )
    report.facts["test_families"] = len(rows)
    report.facts["test_family_ids"] = sorted(seen)
    report.facts["test_manifest_families"] = manifest_families
    return len(rows)


def check_coverage_matrix(report: Report) -> dict[str, Any] | None:
    """Validate the compact semantic-to-concrete-test coverage contract.

    The matrix stays private and deliberately avoids the source OI ledger
    pipeline.  It nevertheless makes family, route, limit, recipe, and input
    provenance machine-checkable instead of accepting an unbound prose claim.
    """

    check = report.new_check("coverage-matrix")
    path = report.problem_dir / COVERAGE_MATRIX_REL
    if not require_file(report, check, path, COVERAGE_MATRIX_REL):
        return None
    try:
        matrix = load_json(path)
    except ContractError as exc:
        check.fail(str(exc))
        return None
    if not isinstance(matrix, dict):
        check.fail("coverage matrix root must be a JSON object")
        return None
    if matrix.get("schema_version") != 1:
        check.fail("coverage matrix schema_version must be integer 1")

    def string_list(
        raw: Any,
        label: str,
        *,
        allow_empty: bool = False,
    ) -> list[str]:
        if not isinstance(raw, list) or (not raw and not allow_empty):
            qualifier = "possibly empty" if allow_empty else "non-empty"
            check.fail(f"{label} must be a {qualifier} string array")
            return []
        values: list[str] = []
        for index, value in enumerate(raw):
            if not isinstance(value, str) or not value.strip():
                check.fail(f"{label}[{index}] must be a non-empty string")
                continue
            normalized = value.strip()
            if normalized in values:
                check.fail(f"{label} duplicates {normalized!r}")
                continue
            values.append(normalized)
        return values

    try:
        plan = load_json(report.problem_dir / "audit/regression-plan.json")
    except ContractError as exc:
        check.fail(str(exc))
        plan = {}
    if not isinstance(plan, dict):
        check.fail("regression plan must be an object for coverage cross-checking")
        plan = {}
    release_items = plan.get("release_tests")
    if not isinstance(release_items, list):
        check.fail("regression plan release_tests must be available to coverage matrix")
        release_items = []
    release_inputs = {
        item.get("input")
        for item in release_items
        if isinstance(item, dict) and isinstance(item.get("input"), str)
    }
    release_limit_tags_by_input = {
        item["input"]: set(item.get("limit_tags", []))
        for item in release_items
        if isinstance(item, dict)
        and isinstance(item.get("input"), str)
        and isinstance(item.get("limit_tags"), list)
    }
    required_limit_tags = set(
        string_list(plan.get("required_limit_tags"), "regression required_limit_tags")
    )
    wrong_matrix = report.facts.get("wrong_route_matrix")
    if not isinstance(wrong_matrix, list):
        check.fail("qualified wrong-route matrix is unavailable")
        wrong_matrix = []
    qualified_route_ids = {
        item.get("route_id")
        for item in wrong_matrix
        if isinstance(item, dict) and isinstance(item.get("route_id"), str)
    }

    route_axes_raw = matrix.get("route_axes")
    if not isinstance(route_axes_raw, list):
        check.fail("coverage matrix route_axes must be an array")
        route_axes_raw = []
    seen_axes: set[str] = set()
    for index, item in enumerate(route_axes_raw):
        label = f"coverage route_axes[{index}]"
        if not isinstance(item, dict):
            check.fail(f"{label} must be an object")
            continue
        axis = item.get("axis")
        if not isinstance(axis, str) or axis not in REQUIRED_ROUTE_AXES:
            check.fail(f"{label}.axis must be one of {sorted(REQUIRED_ROUTE_AXES)}")
            continue
        if axis in seen_axes:
            check.fail(f"coverage route axis is duplicated: {axis}")
        seen_axes.add(axis)
        status = item.get("status")
        if status not in {"covered", "not-applicable", "escalate"}:
            check.fail(f"{label}.status must be covered, not-applicable, or escalate")
        if status == "escalate":
            check.fail(f"{label} remains escalated and cannot certify completion")
        basis = item.get("basis")
        if not isinstance(basis, str) or not non_placeholder(basis):
            check.fail(f"{label}.basis must be concrete")
        route_ids = string_list(
            item.get("route_ids"), f"{label}.route_ids", allow_empty=True
        )
        unknown = set(route_ids) - qualified_route_ids
        if unknown:
            check.fail(f"{label}.route_ids contains unknown routes: {sorted(unknown)}")
        obligation_ids = string_list(
            item.get("obligation_ids"),
            f"{label}.obligation_ids",
            allow_empty=True,
        )
        if status == "covered" and not route_ids and not obligation_ids:
            check.fail(f"{label} covered status needs route_ids or obligation_ids")
    missing_axes = REQUIRED_ROUTE_AXES - seen_axes
    if missing_axes:
        check.fail(f"coverage matrix omits route axes: {sorted(missing_axes)}")

    obligations_raw = matrix.get("obligations")
    if not isinstance(obligations_raw, list) or not obligations_raw:
        check.fail("coverage matrix obligations must be a non-empty array")
        obligations_raw = []
    obligations: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(obligations_raw):
        label = f"coverage obligations[{index}]"
        if not isinstance(item, dict):
            check.fail(f"{label} must be an object")
            continue
        obligation_id = item.get("obligation_id")
        if not isinstance(obligation_id, str) or not re.fullmatch(
            r"[A-Za-z][A-Za-z0-9_-]*", obligation_id
        ):
            check.fail(f"{label}.obligation_id is invalid")
            continue
        if obligation_id in obligations:
            check.fail(f"duplicate coverage obligation_id: {obligation_id}")
            continue
        kind = item.get("kind")
        if kind not in COVERAGE_OBLIGATION_KINDS:
            check.fail(
                f"{label}.kind must be one of {sorted(COVERAGE_OBLIGATION_KINDS)}"
            )
        description = item.get("description")
        if not isinstance(description, str) or not non_placeholder(description):
            check.fail(f"{label}.description must be concrete")
        family_ids = string_list(item.get("family_ids"), f"{label}.family_ids")
        target_routes = string_list(
            item.get("target_route_ids"),
            f"{label}.target_route_ids",
            allow_empty=True,
        )
        unknown = set(target_routes) - qualified_route_ids
        if unknown:
            check.fail(
                f"{label}.target_route_ids contains unknown routes: {sorted(unknown)}"
            )
        required_modes = string_list(
            item.get("required_variant_modes"),
            f"{label}.required_variant_modes",
        )
        unknown_modes = set(required_modes) - COVERAGE_VARIANT_MODES
        if unknown_modes:
            check.fail(
                f"{label}.required_variant_modes contains unsupported values: "
                f"{sorted(unknown_modes)}"
            )
        required_combinations = string_list(
            item.get("required_composed_dimensions"),
            f"{label}.required_composed_dimensions",
            allow_empty=True,
        )
        obligations[obligation_id] = {
            "family_ids": family_ids,
            "target_route_ids": target_routes,
            "required_variant_modes": required_modes,
            "required_composed_dimensions": required_combinations,
        }

    axes_obligation_ids = {
        obligation_id
        for item in route_axes_raw
        if isinstance(item, dict)
        for obligation_id in (
            item.get("obligation_ids")
            if isinstance(item.get("obligation_ids"), list)
            else []
        )
        if isinstance(obligation_id, str)
    }
    unknown_axis_obligations = axes_obligation_ids - set(obligations)
    if unknown_axis_obligations:
        check.fail(
            "route axes reference unknown obligation IDs: "
            f"{sorted(unknown_axis_obligations)}"
        )

    scale_axes_raw = matrix.get("scale_axes")
    if not isinstance(scale_axes_raw, list) or not scale_axes_raw:
        check.fail("coverage matrix scale_axes must be a non-empty array")
        scale_axes_raw = []
    scale_axes: dict[str, dict[str, Any]] = {}
    covered_limit_tags: set[str] = set()
    for index, item in enumerate(scale_axes_raw):
        label = f"coverage scale_axes[{index}]"
        if not isinstance(item, dict):
            check.fail(f"{label} must be an object")
            continue
        axis_id = item.get("axis_id")
        if not isinstance(axis_id, str) or not re.fullmatch(
            r"[A-Za-z][A-Za-z0-9_-]*", axis_id
        ):
            check.fail(f"{label}.axis_id is invalid")
            continue
        if axis_id in scale_axes:
            check.fail(f"duplicate coverage scale axis: {axis_id}")
            continue
        description = item.get("description")
        if not isinstance(description, str) or not non_placeholder(description):
            check.fail(f"{label}.description must be concrete")
        tags = string_list(item.get("limit_tags"), f"{label}.limit_tags")
        unknown = set(tags) - required_limit_tags
        if unknown:
            check.fail(f"{label}.limit_tags contains unknown tags: {sorted(unknown)}")
        covered_limit_tags.update(tags)
        inputs = string_list(item.get("input_paths"), f"{label}.input_paths")
        unknown_inputs = set(inputs) - release_inputs
        if unknown_inputs:
            check.fail(
                f"{label}.input_paths are absent from regression release tests: "
                f"{sorted(unknown_inputs)}"
            )
        for input_path in inputs:
            missing_on_test = set(tags) - release_limit_tags_by_input.get(
                input_path, set()
            )
            if missing_on_test:
                check.fail(
                    f"{label}.input_paths maps {input_path} to tags not carried "
                    f"by that regression test: {sorted(missing_on_test)}"
                )
        composed = string_list(item.get("composed_with"), f"{label}.composed_with")
        scale_axes[axis_id] = {
            "input_paths": inputs,
            "composed_with": composed,
            "limit_tags": tags,
        }
    if covered_limit_tags != required_limit_tags:
        check.fail(
            "coverage scale axes must cover exactly the regression required limit "
            f"tags; missing={sorted(required_limit_tags - covered_limit_tags)}, "
            f"extra={sorted(covered_limit_tags - required_limit_tags)}"
        )

    families_raw = matrix.get("families")
    if not isinstance(families_raw, list) or not families_raw:
        check.fail("coverage matrix families must be a non-empty array")
        families_raw = []
    families: dict[str, dict[str, Any]] = {}
    mapped_inputs: set[str] = set()
    targeted_routes: set[str] = set()
    referenced_obligations: set[str] = set()
    for index, item in enumerate(families_raw):
        label = f"coverage families[{index}]"
        if not isinstance(item, dict):
            check.fail(f"{label} must be an object")
            continue
        family_id = item.get("family_id")
        if not isinstance(family_id, str) or not re.fullmatch(
            r"[A-Za-z][A-Za-z0-9_-]*", family_id
        ):
            check.fail(f"{label}.family_id is invalid")
            continue
        if family_id in families:
            check.fail(f"duplicate coverage family_id: {family_id}")
            continue
        purpose = item.get("purpose")
        if not isinstance(purpose, str) or not non_placeholder(purpose):
            check.fail(f"{label}.purpose must be concrete")
        input_bindings = item.get("inputs")
        if not isinstance(input_bindings, list) or not input_bindings:
            check.fail(f"{label}.inputs must be a non-empty binding array")
            input_bindings = []
        family_inputs: list[str] = []
        for binding_index, binding in enumerate(input_bindings):
            binding_label = f"{label}.inputs[{binding_index}]"
            if not isinstance(binding, dict):
                check.fail(f"{binding_label} must be an object")
                continue
            try:
                input_rel, input_path = safe_problem_path(
                    report.problem_dir,
                    binding.get("path"),
                    label=f"{binding_label}.path",
                    require_exists=True,
                )
            except ContractError as exc:
                check.fail(str(exc))
                continue
            if input_rel not in release_inputs:
                check.fail(f"{binding_label}.path is not a regression release input")
            if input_rel in family_inputs:
                check.fail(f"{label}.inputs duplicates {input_rel}")
            family_inputs.append(input_rel)
            if require_file(report, check, input_path, f"coverage input {input_rel}"):
                digest = binding.get("sha256")
                if not isinstance(digest, str) or HASH_RE.fullmatch(digest) is None:
                    check.fail(f"{binding_label}.sha256 must be 64 lowercase hex digits")
                elif digest != sha256_file(input_path):
                    check.fail(f"{binding_label}.sha256 is stale")
            mapped_inputs.add(input_rel)

        generation = item.get("generation")
        seed_params: list[str] = []
        if not isinstance(generation, dict):
            check.fail(f"{label}.generation must be an object")
        else:
            kind = generation.get("kind")
            if kind not in {"fixed", "generator"}:
                check.fail(f"{label}.generation.kind must be fixed or generator")
            seed_params = string_list(
                generation.get("seed_params"), f"{label}.generation.seed_params"
            )
            if kind == "generator":
                try:
                    source_rel, source_path = safe_problem_path(
                        report.problem_dir,
                        generation.get("source"),
                        label=f"{label}.generation.source",
                        require_exists=True,
                    )
                except ContractError as exc:
                    check.fail(str(exc))
                else:
                    if not source_rel.startswith("package/generators/"):
                        check.fail(
                            f"{label}.generation.source must stay below package/generators/"
                        )
                    require_file(
                        report, check, source_path, f"coverage generator {source_rel}"
                    )
                string_list(generation.get("args"), f"{label}.generation.args")
            elif kind == "fixed":
                recipe = generation.get("recipe")
                if not isinstance(recipe, str) or not non_placeholder(recipe):
                    check.fail(f"{label}.generation.recipe must explain the fixed witness")
                for forbidden_field in ("source", "args"):
                    if forbidden_field in generation:
                        check.fail(
                            f"{label}.generation.{forbidden_field} must be omitted "
                            "for fixed data"
                        )
            if not seed_params:
                check.fail(f"{label}.generation must preserve seed/case parameters")

        obligation_ids = string_list(
            item.get("target_obligation_ids"),
            f"{label}.target_obligation_ids",
        )
        unknown = set(obligation_ids) - set(obligations)
        if unknown:
            check.fail(
                f"{label}.target_obligation_ids contains unknown IDs: {sorted(unknown)}"
            )
        referenced_obligations.update(obligation_ids)
        route_ids = string_list(
            item.get("target_route_ids"),
            f"{label}.target_route_ids",
            allow_empty=True,
        )
        unknown = set(route_ids) - qualified_route_ids
        if unknown:
            check.fail(f"{label}.target_route_ids contains unknown routes: {sorted(unknown)}")
        targeted_routes.update(route_ids)
        axis_ids = string_list(
            item.get("scale_axis_ids"),
            f"{label}.scale_axis_ids",
            allow_empty=True,
        )
        unknown = set(axis_ids) - set(scale_axes)
        if unknown:
            check.fail(f"{label}.scale_axis_ids contains unknown axes: {sorted(unknown)}")
        variant_modes = string_list(
            item.get("variant_modes"), f"{label}.variant_modes"
        )
        unknown_modes = set(variant_modes) - COVERAGE_VARIANT_MODES
        if unknown_modes:
            check.fail(
                f"{label}.variant_modes contains unsupported values: {sorted(unknown_modes)}"
            )
        families[family_id] = {
            "inputs": family_inputs,
            "obligation_ids": obligation_ids,
            "route_ids": route_ids,
            "scale_axis_ids": axis_ids,
            "variant_modes": variant_modes,
            "composed_dimensions": string_list(
                item.get("composed_dimensions"),
                f"{label}.composed_dimensions",
            ),
        }

        manifest_families = report.facts.get("test_manifest_families")
        manifest_family = (
            manifest_families.get(family_id)
            if isinstance(manifest_families, dict)
            else None
        )
        if not isinstance(manifest_family, dict):
            check.fail(f"{label} has no matching test-manifest row")
        else:
            if " ".join(str(purpose).split()) != manifest_family.get("purpose"):
                check.fail(f"{label}.purpose differs from test-manifest.md")
            if set(seed_params) != set(manifest_family.get("seed_params", [])):
                check.fail(f"{label}.generation.seed_params differs from test-manifest.md")
            if set(route_ids) != set(manifest_family.get("target_routes", [])):
                check.fail(f"{label}.target_route_ids differs from test-manifest.md")
            command_cell = str(
                manifest_family.get("command_or_fixed_file", "")
            ).lower()

            def mentions_manifest_token(value: str) -> bool:
                """Match one command/path token, not a filename substring."""

                if not value:
                    return False
                token_chars = r"A-Za-z0-9_./-"
                return re.search(
                    rf"(?<![{token_chars}]){re.escape(value.lower())}"
                    rf"(?![{token_chars}])",
                    command_cell,
                ) is not None

            if isinstance(generation, dict) and generation.get("kind") == "generator":
                source_value = generation.get("source")
                source_name = (
                    PurePosixPath(source_value).name
                    if isinstance(source_value, str)
                    else ""
                )
                source_stem = PurePosixPath(source_name).stem
                source_path_stem = (
                    PurePosixPath(source_value).with_suffix("").as_posix()
                    if isinstance(source_value, str) and source_value
                    else ""
                )
                source_mentions = (
                    source_value if isinstance(source_value, str) else "",
                    source_path_stem,
                    source_name,
                    source_stem,
                    f"./{source_name}" if source_name else "",
                    f"./{source_stem}" if source_stem else "",
                )
                if not any(
                    mentions_manifest_token(candidate)
                    for candidate in source_mentions
                    if candidate
                ):
                    check.fail(
                        f"{label}.generation.source is absent from the test-manifest command"
                    )
            elif isinstance(generation, dict) and generation.get("kind") == "fixed":
                missing_manifest_inputs = [
                    input_path
                    for input_path in family_inputs
                    if not mentions_manifest_token(input_path)
                    and not (
                        len(family_inputs) == 1
                        and mentions_manifest_token(PurePosixPath(input_path).name)
                    )
                ]
                if missing_manifest_inputs:
                    check.fail(
                        f"{label} fixed inputs are absent from the test-manifest "
                        f"file cell: {missing_manifest_inputs}"
                    )

    manifest_family_ids = set(report.facts.get("test_family_ids", []))
    if set(families) != manifest_family_ids:
        check.fail(
            "coverage families must match audit/test-manifest.md exactly; "
            f"missing={sorted(manifest_family_ids - set(families))}, "
            f"extra={sorted(set(families) - manifest_family_ids)}"
        )
    if mapped_inputs != release_inputs:
        check.fail(
            "coverage families must bind every regression release input; "
            f"missing={sorted(release_inputs - mapped_inputs)}, "
            f"extra={sorted(mapped_inputs - release_inputs)}"
        )
    if targeted_routes != qualified_route_ids:
        check.fail(
            "coverage families must target every qualified wrong route; "
            f"missing={sorted(qualified_route_ids - targeted_routes)}, "
            f"extra={sorted(targeted_routes - qualified_route_ids)}"
        )
    if referenced_obligations != set(obligations):
        check.fail(
            "every coverage obligation must reach a concrete family; "
            f"unreferenced={sorted(set(obligations) - referenced_obligations)}"
        )
    for obligation_id, obligation in obligations.items():
        missing_families = set(obligation["family_ids"]) - set(families)
        if missing_families:
            check.fail(
                f"obligation {obligation_id} references unknown families: "
                f"{sorted(missing_families)}"
            )
        reverse = {
            family_id
            for family_id, family in families.items()
            if obligation_id in family["obligation_ids"]
        }
        if reverse != set(obligation["family_ids"]):
            check.fail(
                f"obligation {obligation_id} family links are not bidirectional"
            )
        obligation_routes = {
            route_id
            for family_id in obligation["family_ids"]
            for route_id in families.get(family_id, {}).get("route_ids", [])
        }
        if not set(obligation["target_route_ids"]).issubset(obligation_routes):
            check.fail(
                f"obligation {obligation_id} target routes are not carried by "
                "its linked families"
            )
        observed_modes = {
            mode
            for family_id in obligation["family_ids"]
            for mode in families.get(family_id, {}).get("variant_modes", [])
        }
        if not set(obligation["required_variant_modes"]).issubset(observed_modes):
            check.fail(
                f"obligation {obligation_id} required variant modes are not "
                "covered by its linked families"
            )
        required_combinations = set(obligation["required_composed_dimensions"])
        if required_combinations and not any(
            required_combinations.issubset(
                set(families.get(family_id, {}).get("composed_dimensions", []))
            )
            for family_id in obligation["family_ids"]
        ):
            check.fail(
                f"obligation {obligation_id} required composed dimensions are "
                "not jointly covered by any one linked family"
            )
    for axis_id, axis in scale_axes.items():
        reverse_inputs = {
            input_path
            for family in families.values()
            if axis_id in family["scale_axis_ids"]
            for input_path in family["inputs"]
        }
        if reverse_inputs != set(axis["input_paths"]):
            check.fail(f"scale axis {axis_id} input links are not bidirectional")
        required_compositions = set(axis["composed_with"])
        if required_compositions and not any(
            axis_id in family["scale_axis_ids"]
            and required_compositions.issubset(set(family["composed_dimensions"]))
            for family in families.values()
        ):
            check.fail(
                f"scale axis {axis_id} composed_with dimensions are not jointly "
                "carried by any one linked family"
            )

    if not check.issues:
        report.facts["coverage_matrix"] = {
            "families": len(families),
            "obligations": len(obligations),
            "route_axes": len(seen_axes),
            "scale_axes": len(scale_axes),
            "release_inputs": len(mapped_inputs),
            "sha256": sha256_file(path),
        }
        check.add(
            f"bound {len(obligations)} obligations through {len(families)} "
            f"families to {len(mapped_inputs)} regression inputs"
        )
    return matrix if not check.issues else None


def check_regression(report: Report) -> dict[str, Any] | None:
    check = report.new_check("regression")
    path = report.problem_dir / "audit/regression.md"
    try:
        regression = parse_front_matter(path)
    except ContractError as exc:
        check.fail(str(exc))
        return None
    require_fields(check, regression, REGRESSION_REQUIRED_FIELDS, "regression front matter")
    if regression.get("schema_version") != 1:
        check.fail("regression schema_version must be integer 1")
    if regression.get("agent_model") != REQUIRED_AGENT_MODEL:
        check.fail(f"regression.agent_model must be {REQUIRED_AGENT_MODEL!r}")
    if regression.get("agent_reasoning_effort") != REQUIRED_REASONING_EFFORT:
        check.fail(
            "regression.agent_reasoning_effort must be "
            f"{REQUIRED_REASONING_EFFORT!r}"
        )
    for key in (
        "status",
        "validator",
        "differential",
        "wrong_routes",
        "privacy_scan",
        "limit_coverage",
    ):
        if regression.get(key) != "passed":
            check.fail(f"regression.{key} must be exactly 'passed'")
    command = regression.get("repro_command")
    if not isinstance(command, str) or not non_placeholder(command):
        check.fail("regression.repro_command must be a non-empty reproducible command")
    mode = regression.get("differential_mode")
    cases = require_int(check, regression, "differential_cases", "regression")
    consecutive = require_int(
        check, regression, "differential_consecutive_seeds", "regression"
    )
    validated = require_int(
        check, regression, "generated_inputs_validated", "regression"
    )
    wrong_checked = require_int(check, regression, "wrong_routes_checked", "regression")
    survivability_checked = require_int(
        check, regression, "survivability_inputs_checked", "regression"
    )
    alternatives_checked = require_int(
        check, regression, "accepted_alternatives_checked", "regression"
    )
    non_jury_outputs_checked = require_int(
        check, regression, "accepted_non_jury_outputs_checked", "regression"
    )
    alternative_strategy = regression.get("accepted_alternative_strategy")
    tests_checked = require_int(check, regression, "release_tests_checked", "regression")
    if mode == "tiny-exhaustive":
        if cases is not None and cases < 1:
            check.fail("regression tiny-exhaustive mode requires differential_cases >= 1")
        if consecutive is not None and consecutive != 0:
            check.fail(
                "regression tiny-exhaustive mode requires "
                "differential_consecutive_seeds: 0"
            )
    elif mode == "random-seeds":
        if consecutive is not None and consecutive < 5000:
            check.fail("regression random-seeds mode requires at least 5000 consecutive seeds")
        if cases is not None and consecutive is not None and cases < consecutive:
            check.fail("regression differential_cases cannot be below consecutive seeds")
    else:
        check.fail(
            "regression.differential_mode must be tiny-exhaustive or random-seeds"
        )
    if validated is not None and validated < 1:
        check.fail("regression.generated_inputs_validated must be positive")
    if survivability_checked is not None and wrong_checked is not None and (
        survivability_checked < 3 * wrong_checked
    ):
        check.fail(
            "regression.survivability_inputs_checked must cover at least three "
            "inputs per qualified wrong route"
        )
    if alternatives_checked is not None and alternatives_checked < 0:
        check.fail("regression.accepted_alternatives_checked cannot be negative")
    if non_jury_outputs_checked is not None and non_jury_outputs_checked < 0:
        check.fail("regression.accepted_non_jury_outputs_checked cannot be negative")
    if alternative_strategy not in {
        "programs",
        "no-known-alternative",
        "not-required",
    }:
        check.fail("regression.accepted_alternative_strategy is invalid")
    expected_wrong = report.facts.get("wrong_solutions_qualified")
    if wrong_checked is not None and wrong_checked != expected_wrong:
        check.fail(
            "regression.wrong_routes_checked must equal the qualified wrong-route count"
        )
    expected_tests = report.facts.get("test_files")
    if tests_checked is not None and tests_checked != expected_tests:
        check.fail("regression.release_tests_checked must equal package test file count")
    machine = report.facts.get("machine_regression")
    if not isinstance(machine, dict):
        check.fail("regression has no passed canonical machine execution receipt")
    else:
        comparisons = {
            "differential_mode": mode,
            "differential_cases_completed": cases,
            "differential_consecutive_seeds": consecutive,
            "generated_inputs_validated": validated,
            "wrong_routes_checked": wrong_checked,
            "survivability_inputs_checked": survivability_checked,
            "accepted_alternatives_checked": alternatives_checked,
            "accepted_non_jury_outputs_checked": non_jury_outputs_checked,
            "accepted_alternative_strategy": alternative_strategy,
            "release_tests_checked": tests_checked,
        }
        for key, expected in comparisons.items():
            if machine.get(key) != expected:
                check.fail(
                    f"regression.{key} does not match machine receipt: "
                    f"{expected!r} != {machine.get(key)!r}"
                )
    if not check.issues:
        check.add(
            f"{mode}; cases={cases}; consecutive={consecutive}; "
            f"validated={validated}; wrongs={wrong_checked}; "
            f"survivability={survivability_checked}; alternatives={alternatives_checked}; "
            f"non-jury-outputs={non_jury_outputs_checked}; tests={tests_checked}"
        )
    return regression


def tree_files(
    report: Report,
    root_rel: str,
    check: Check,
    *,
    allow_empty: bool = False,
) -> list[Path]:
    root = report.problem_dir / root_rel
    if root.is_symlink() or not root.is_dir():
        check.fail(f"missing regular directory: {root_rel}")
        return []
    files: list[Path] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(report.problem_dir).as_posix()
        if any(part.startswith(".") for part in path.relative_to(root).parts):
            check.fail(f"hidden entries are forbidden in watched tree: {relative}")
            continue
        if path.is_symlink():
            check.fail(f"symbolic links are forbidden in watched tree: {relative}")
        elif path.is_file():
            if allow_empty:
                try:
                    path.stat()
                except OSError as exc:
                    check.fail(f"cannot stat watched-tree file {relative}: {exc}")
                else:
                    report.track(path)
                    files.append(path)
            elif require_file(report, check, path, relative):
                files.append(path)
        elif not path.is_dir():
            check.fail(f"unsupported filesystem entry in watched tree: {relative}")
    report.watched_trees[root_rel] = [
        path.relative_to(report.problem_dir).as_posix() for path in files
    ]
    return files


def check_package(report: Report) -> tuple[Path | None, Path | None, Path | None, list[Path]]:
    check = report.new_check("release-package")
    package_files = tree_files(report, "package", check)
    package = report.problem_dir / "package"
    std = package / "std.cpp"
    validator = package / "validator.cpp"
    checker = package / "checker.cpp"
    oracle = package / "brute.cpp"
    for path, label in ((std, "package/std.cpp"), (validator, "package/validator.cpp")):
        require_file(report, check, path, label)
    if not require_file(report, check, oracle, "package/brute.cpp"):
        oracle = None
    elif std.is_file() and sha256_file(std) == sha256_file(oracle):
        check.fail("package/brute.cpp source hash must differ from package/std.cpp")
    if checker.exists() or checker.is_symlink():
        require_file(report, check, checker, "package/checker.cpp")
    else:
        checker = None

    generators = [
        path
        for path in package_files
        if path.is_relative_to(package / "generators")
    ]
    tests = [
        path
        for path in package_files
        if path.is_relative_to(package / "tests") and path.suffix == ".in"
    ]
    sample_manifest = package / "samples" / "manifest.json"
    if not generators:
        check.fail("package/generators must contain a non-empty regular file")
    if not tests:
        check.fail("package/tests must contain at least one non-empty .in file")
    require_file(
        report,
        check,
        sample_manifest,
        "package/samples/manifest.json",
    )
    if oracle is not None and oracle.suffix.lower() not in CPP_SUFFIXES:
        check.fail("package/brute.cpp must be C/C++")
    if not check.issues:
        check.add(
            f"std, brute, validator, {len(generators)} generator file(s), "
            f"and {len(tests)} test file(s)"
        )
    report.facts.update(
        {
            "oracle_path": "package/brute.cpp" if oracle else None,
            "validator_path": "package/validator.cpp",
            "checker_path": "package/checker.cpp" if checker else None,
            "generator_files": len(generators),
            "test_files": len(tests),
        }
    )
    return std if std.is_file() else None, oracle, checker, [validator]


def validate_regression_artifact_bindings(
    report: Report, check: Check, receipt: dict[str, Any]
) -> tuple[set[str], list[str]]:
    bindings = receipt.get("artifact_bindings")
    if not isinstance(bindings, dict):
        check.fail("machine regression artifact_bindings must be an object")
        return set(), []
    files = bindings.get("files")
    if not isinstance(files, list) or not files:
        check.fail("machine regression artifact_bindings.files must be non-empty")
        return set(), []
    paths: list[str] = []
    for index, item in enumerate(files):
        label = f"machine regression artifact_bindings.files[{index}]"
        if not isinstance(item, dict):
            check.fail(f"{label} must be an object")
            continue
        try:
            relative, path = safe_problem_path(
                report.problem_dir,
                item.get("path"),
                label=f"{label}.path",
                require_exists=True,
            )
        except ContractError as exc:
            check.fail(str(exc))
            continue
        if not require_file(report, check, path, label):
            continue
        digest = sha256_file(path)
        size = path.stat().st_size
        if item.get("sha256") != digest or item.get("size") != size:
            check.fail(f"{label} hash/size binding is stale")
        paths.append(relative)
    if paths != sorted(set(paths)):
        check.fail("machine regression artifact-bound paths must be unique and sorted")
    if bindings.get("files_binding_sha256") != canonical_json_sha256(files):
        check.fail("machine regression artifact file-list binding is stale")

    manifest = bindings.get("sample_manifest")
    sample_ids: list[str] = []
    if not isinstance(manifest, dict):
        check.fail("machine regression sample_manifest binding must be an object")
    else:
        if manifest.get("path") != CANONICAL_SAMPLE_MANIFEST:
            check.fail("machine regression binds the wrong canonical sample manifest")
        manifest_path = report.problem_dir / CANONICAL_SAMPLE_MANIFEST
        if manifest.get("sha256") != (
            sha256_file(manifest_path) if manifest_path.is_file() else None
        ):
            check.fail("machine regression canonical sample manifest hash is stale")
        statement_path = report.problem_dir / "statement.md"
        if manifest.get("statement_path") != "statement.md":
            check.fail("machine regression sample manifest binds the wrong statement path")
        if manifest.get("statement_sha256") != (
            sha256_file(statement_path) if statement_path.is_file() else None
        ):
            check.fail("machine regression sample manifest statement hash is stale")
        sample_ids_raw = manifest.get("sample_ids")
        if not isinstance(sample_ids_raw, list) or not sample_ids_raw or any(
            not isinstance(item, str) or not item for item in sample_ids_raw
        ):
            check.fail("machine regression canonical sample IDs are invalid")
        else:
            sample_ids = sample_ids_raw
        if manifest.get("sample_count") != len(sample_ids):
            check.fail("machine regression canonical sample count is inconsistent")

    oracle = bindings.get("oracle")
    if not isinstance(oracle, dict):
        check.fail("machine regression oracle binding must be an object")
    else:
        std = report.problem_dir / "package/std.cpp"
        brute = report.problem_dir / "package/brute.cpp"
        expected = {
            "source": "package/brute.cpp",
            "std_source": "package/std.cpp",
            "source_sha256": sha256_file(brute) if brute.is_file() else None,
            "std_source_sha256": sha256_file(std) if std.is_file() else None,
            "source_hashes_distinct": True,
            "independent_from_std": True,
        }
        for key, value in expected.items():
            if oracle.get(key) != value:
                check.fail(f"machine regression oracle.{key} must be {value!r}")
        for key in ("independence_basis", "applicability"):
            value = oracle.get(key)
            if not isinstance(value, str) or not value.strip():
                check.fail(f"machine regression oracle.{key} must be non-empty")

    checker_contract = bindings.get("checker_verdict_contract")
    if checker_contract != receipt.get("checker_verdict_contract"):
        check.fail("machine regression checker verdict contracts disagree")
    if not isinstance(checker_contract, dict):
        check.fail("machine regression checker verdict contract must be an object")
    else:
        accepted = checker_contract.get("accepted_exit_codes")
        wrong = checker_contract.get("wrong_answer_exit_codes")
        presentation = checker_contract.get("presentation_error_exit_codes")
        if not all(
            isinstance(value, list)
            and value
            and all(type(code) is int and 0 <= code <= 255 for code in value)
            for value in (accepted, wrong, presentation)
        ):
            check.fail("machine regression checker verdict code classes are invalid")
        elif (
            set(accepted) & (set(wrong) | set(presentation))
            or set(wrong) & set(presentation)
            or 0 not in accepted
        ):
            check.fail("machine regression checker verdict code classes overlap")
        if checker_contract.get("unknown_exit_code_verdict") != "infrastructure-error":
            check.fail("unknown checker exit codes must be infrastructure errors")
    report.facts["regression_artifact_binding_sha256"] = bindings.get(
        "files_binding_sha256"
    )
    report.facts["canonical_sample_manifest_sha256"] = (
        manifest.get("sha256") if isinstance(manifest, dict) else None
    )
    return set(paths), sample_ids


def _lower_sha256(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def validate_strengthened_regression_evidence(
    report: Report,
    check: Check,
    receipt: dict[str, Any],
    plan: dict[str, Any],
    *,
    bound_paths: set[str],
    wrong_records: list[Any],
    wrong_matrix: list[dict[str, Any]],
) -> None:
    """Bind survivability and accepted-route evidence to the canonical plan.

    The regression executor already enforces these conditions while it runs.
    Completion independently checks the resulting receipt so a weakened or
    hand-edited evidence file cannot satisfy the final gate.
    """

    def successful_execution(raw: Any) -> bool:
        return (
            isinstance(raw, dict)
            and raw.get("returncode") == 0
            and raw.get("timed_out") is False
            and raw.get("launch_error") is None
        )

    plan_routes_raw = plan.get("wrong_routes")
    plan_routes = plan_routes_raw if isinstance(plan_routes_raw, list) else []
    plan_routes_by_id = {
        item.get("route_id"): item
        for item in plan_routes
        if isinstance(item, dict) and isinstance(item.get("route_id"), str)
    }
    records_by_id = {
        item.get("route_id"): item
        for item in wrong_records
        if isinstance(item, dict) and isinstance(item.get("route_id"), str)
    }
    expected_route_ids = [item.get("route_id") for item in wrong_matrix]
    if [item.get("route_id") for item in plan_routes if isinstance(item, dict)] != (
        expected_route_ids
    ):
        check.fail(
            "canonical regression plan wrong-route order differs from the qualified audit"
        )

    survivability_count = 0
    for route_id in expected_route_ids:
        plan_route = plan_routes_by_id.get(route_id)
        record = records_by_id.get(route_id)
        if not isinstance(plan_route, dict) or not isinstance(record, dict):
            check.fail(f"survivability evidence is unavailable for route {route_id}")
            continue
        specs = plan_route.get("survivability_inputs")
        results = record.get("survivability_results")
        if not isinstance(specs, list) or not isinstance(results, list):
            check.fail(f"route {route_id} has no structured survivability matrix")
            continue
        expected_pairs = [
            (item.get("kind"), item.get("input"))
            for item in specs
            if isinstance(item, dict)
        ]
        observed_pairs = [
            (item.get("kind"), item.get("input"))
            for item in results
            if isinstance(item, dict)
        ]
        if len(expected_pairs) != len(specs) or observed_pairs != expected_pairs:
            check.fail(
                f"route {route_id} survivability results differ from the canonical plan"
            )
            continue
        kinds = {kind for kind, _ in expected_pairs}
        if not {"small", "random", "structured"}.issubset(kinds):
            check.fail(
                f"route {route_id} lacks small/random/structured survivability evidence"
            )
        breaker_input = plan_route.get("breaker_input")
        for index, result in enumerate(results):
            label = f"route {route_id} survivability[{index}]"
            if not isinstance(result, dict):
                check.fail(f"{label} must be an object")
                continue
            relative = result.get("input")
            if (
                not isinstance(relative, str)
                or not relative.startswith("package/tests/")
                or not relative.endswith(".in")
                or relative == breaker_input
                or relative not in bound_paths
            ):
                check.fail(f"{label} is not an independent bound package test")
                continue
            try:
                _, path = safe_problem_path(
                    report.problem_dir,
                    relative,
                    label=f"{label}.input",
                    require_exists=True,
                )
            except ContractError as exc:
                check.fail(str(exc))
                continue
            if result.get("input_sha256") != sha256_file(path):
                check.fail(f"{label} input hash is stale")
            if result.get("status") != "passed" or result.get(
                "observed_verdict"
            ) != "AC":
                check.fail(f"{label} did not independently receive AC")
            if not successful_execution(result.get("validator")) or not (
                successful_execution(result.get("std"))
            ):
                check.fail(f"{label} lacks validator/std execution evidence")
            wrong = result.get("wrong")
            if not isinstance(wrong, dict) or not successful_execution(
                wrong.get("execution")
            ):
                check.fail(f"{label} lacks wrong-program execution evidence")
            survivability_count += 1

    alternatives_raw = plan.get("accepted_alternatives", [])
    alternatives = alternatives_raw if isinstance(alternatives_raw, list) else []
    checker_present = "package/checker.cpp" in bound_paths
    waiver = plan.get("accepted_alternative_waiver")
    if alternatives:
        expected_policy = {
            "strategy": "programs",
            "program_count": len(alternatives),
            "waiver": None,
        }
    elif checker_present:
        expected_policy = {
            "strategy": "no-known-alternative",
            "program_count": 0,
            "waiver": waiver,
        }
    else:
        expected_policy = {
            "strategy": "not-required",
            "program_count": 0,
            "waiver": None,
        }
    policy = receipt.get("accepted_alternative_policy")
    if policy != expected_policy:
        check.fail(
            "machine regression accepted-alternative policy differs from the canonical plan"
        )
    if checker_present and not alternatives:
        if not isinstance(waiver, dict) or waiver.get("status") != (
            "no-known-alternative"
        ) or any(
            not isinstance(waiver.get(key), str) or not waiver[key].strip()
            for key in ("basis", "search_scope")
        ):
            check.fail(
                "custom-checker plan has neither accepted programs nor a concrete waiver"
            )

    expected_bindings: list[dict[str, Any]] = []
    try:
        std_normalized_source_sha256 = regression_gate.cpp_normalized_source_sha256(
            report.problem_dir / "package/std.cpp"
        )
    except Exception as exc:
        check.fail(f"cannot normalize package/std.cpp for clone detection: {exc}")
        std_normalized_source_sha256 = None
    seen_normalized_source_hashes: set[str] = set()
    for index, alternative in enumerate(alternatives):
        if not isinstance(alternative, dict):
            check.fail(f"accepted_alternatives[{index}] must be an object")
            continue
        source = alternative.get("source")
        alternative_id = alternative.get("alternative_id")
        independence_basis = alternative.get("independence_basis")
        if not isinstance(independence_basis, str) or not non_placeholder(
            independence_basis
        ):
            check.fail(
                f"accepted alternative {alternative_id!r} lacks a concrete independence basis"
            )
        try:
            relative, path = safe_problem_path(
                report.problem_dir,
                source,
                label=f"accepted_alternatives[{index}].source",
                require_exists=True,
            )
        except ContractError as exc:
            check.fail(str(exc))
            continue
        if (
            not isinstance(alternative_id, str)
            or not alternative_id
            or not relative.startswith("audit/private/accepted-solutions/")
            or relative not in bound_paths
        ):
            check.fail(f"accepted alternative {alternative_id!r} is outside its boundary")
            continue
        try:
            normalized_source_sha256 = (
                regression_gate.cpp_normalized_source_sha256(path)
            )
        except Exception as exc:
            check.fail(f"cannot normalize accepted alternative {alternative_id}: {exc}")
            continue
        if normalized_source_sha256 == std_normalized_source_sha256:
            check.fail(
                f"accepted alternative {alternative_id} is a normalized "
                "preprocessing-token std clone"
            )
        if normalized_source_sha256 in seen_normalized_source_hashes:
            check.fail(
                f"accepted alternative {alternative_id} duplicates another normalized source"
            )
        seen_normalized_source_hashes.add(normalized_source_sha256)
        expected_bindings.append(
            {
                "alternative_id": alternative_id,
                "source": relative,
                "source_sha256": sha256_file(path),
                "normalized_source_sha256": normalized_source_sha256,
                "independence_basis": independence_basis,
            }
        )
    if receipt.get("accepted_alternative_bindings") != expected_bindings:
        check.fail("machine regression accepted-alternative bindings are stale")

    accepted_records = receipt.get("accepted_alternatives")
    if not isinstance(accepted_records, list):
        check.fail("machine regression accepted-alternative matrix is missing")
        accepted_records = []
    if [
        item.get("alternative_id") if isinstance(item, dict) else None
        for item in accepted_records
    ] != [item.get("alternative_id") for item in expected_bindings]:
        check.fail("machine regression accepted-alternative order differs from the plan")
    release_plan_raw = plan.get("release_tests")
    release_plan = release_plan_raw if isinstance(release_plan_raw, list) else []
    accepted_count = 0
    diversity_witnesses: list[dict[str, Any]] = []
    for binding, record in zip(expected_bindings, accepted_records):
        alternative_id = binding["alternative_id"]
        if not isinstance(record, dict):
            check.fail(f"accepted alternative {alternative_id} receipt is invalid")
            continue
        if any(record.get(key) != value for key, value in binding.items()):
            check.fail(f"accepted alternative {alternative_id} source binding is stale")
        if (
            record.get("compile_status") != "passed"
            or record.get("status") != "passed"
            or record.get("errors") != []
        ):
            check.fail(f"accepted alternative {alternative_id} did not pass")
        results = record.get("release_results")
        if not isinstance(results, list) or len(results) != len(release_plan):
            check.fail(
                f"accepted alternative {alternative_id} did not run every release test"
            )
            continue
        record_non_jury_ids: list[str] = []
        for release, result in zip(release_plan, results):
            if not isinstance(release, dict) or not isinstance(result, dict):
                check.fail(f"accepted alternative {alternative_id} has invalid results")
                continue
            expected_release = {
                "test_id": release.get("test_id"),
                "input": release.get("input"),
                "answer": release.get("answer"),
            }
            if any(result.get(key) != value for key, value in expected_release.items()):
                check.fail(
                    f"accepted alternative {alternative_id} release binding differs"
                )
                continue
            for field in ("input", "answer"):
                relative = result.get(field)
                if not isinstance(relative, str) or relative not in bound_paths:
                    check.fail(
                        f"accepted alternative {alternative_id} has unbound {field}"
                    )
                    continue
                try:
                    _, path = safe_problem_path(
                        report.problem_dir,
                        relative,
                        label=f"accepted alternative {alternative_id} {field}",
                        require_exists=True,
                    )
                except ContractError as exc:
                    check.fail(str(exc))
                    continue
                if result.get(f"{field}_sha256") != sha256_file(path):
                    check.fail(
                        f"accepted alternative {alternative_id} {field} hash is stale"
                    )
                if field == "answer":
                    expected_answer_token_sha256 = regression_gate.token_sha256(
                        path.read_bytes()
                    )
                    if result.get("answer_token_sha256") != (
                        expected_answer_token_sha256
                    ):
                        check.fail(
                            f"accepted alternative {alternative_id} answer token hash is stale"
                        )
            execution = result.get("execution")
            judge = result.get("judge")
            candidate_token_sha256 = result.get("candidate_token_sha256")
            answer_token_sha256 = result.get("answer_token_sha256")
            non_jury_output = (
                _lower_sha256(candidate_token_sha256)
                and _lower_sha256(answer_token_sha256)
                and candidate_token_sha256 != answer_token_sha256
            )
            if result.get("non_jury_output") is not non_jury_output:
                check.fail(
                    f"accepted alternative {alternative_id} output-diversity flag is stale"
                )
            if (
                result.get("status") != "passed"
                or not successful_execution(execution)
                or not isinstance(judge, dict)
                or judge.get("verdict") != "accepted"
            ):
                check.fail(
                    f"accepted alternative {alternative_id} failed a release test"
                )
            elif non_jury_output:
                test_id = str(result.get("test_id"))
                record_non_jury_ids.append(test_id)
                diversity_witnesses.append(
                    {
                        "alternative_id": alternative_id,
                        "test_id": test_id,
                        "candidate_token_sha256": candidate_token_sha256,
                        "answer_token_sha256": answer_token_sha256,
                    }
                )
        if record.get("non_jury_accepted_test_ids") != record_non_jury_ids:
            check.fail(
                f"accepted alternative {alternative_id} non-jury witness list is stale"
            )
        accepted_count += 1

    if checker_present and alternatives:
        expected_diversity_status = "passed" if diversity_witnesses else "failed"
        diversity_required = True
    elif checker_present:
        expected_diversity_status = "waived"
        diversity_required = False
    else:
        expected_diversity_status = "not-required"
        diversity_required = False
    expected_diversity = {
        "required": diversity_required,
        "status": expected_diversity_status,
        "witnesses": diversity_witnesses,
    }
    if receipt.get("accepted_alternative_output_diversity") != expected_diversity:
        check.fail("machine regression accepted-output diversity evidence is stale")
    if diversity_required and not diversity_witnesses:
        check.fail(
            "custom checker has no accepted alternative output distinct from the jury tokens"
        )

    facts = receipt.get("facts")
    if isinstance(facts, dict):
        if facts.get("survivability_inputs_checked") != survivability_count:
            check.fail("machine regression survivability fact count is inconsistent")
        if facts.get("accepted_alternatives_checked") != accepted_count:
            check.fail("machine regression accepted-alternative fact count is inconsistent")
        if facts.get("accepted_non_jury_outputs_checked") != len(
            diversity_witnesses
        ):
            check.fail("machine regression non-jury-output fact count is inconsistent")
        if facts.get("accepted_alternative_strategy") != expected_policy["strategy"]:
            check.fail("machine regression accepted-alternative strategy fact is stale")
    report.facts["strengthened_regression"] = {
        "survivability_inputs_checked": survivability_count,
        "accepted_alternatives_checked": accepted_count,
        "accepted_non_jury_outputs_checked": len(diversity_witnesses),
        "accepted_alternative_strategy": expected_policy["strategy"],
    }


@dataclass(frozen=True)
class CanonicalResourcePolicy:
    statement_resources: StatementResources
    policy_sha256: str

    @property
    def time_limit_ms(self) -> int:
        return self.statement_resources.time_limit_ms

    @property
    def memory_limit_mib(self) -> int:
        return self.statement_resources.memory_limit_mib


def validate_machine_resource_policy(
    report: Report,
    check: Check,
    receipt: dict[str, Any],
    plan_path: Path,
) -> CanonicalResourcePolicy | None:
    """Bind machine limits to the current statement, plan, and receipt."""

    try:
        statement_resources = load_statement_resources(report.problem_dir)
    except StatementResourceError as exc:
        check.fail(f"current statement resource policy is invalid: {exc}")
        return None
    report.track(report.problem_dir / statement_resources.statement_path)

    raw = receipt.get("resource_policy")
    required_keys = {
        "schema_version",
        "statement_resources",
        "design_basis",
        "policy_sha256",
    }
    if not isinstance(raw, dict) or set(raw) != required_keys:
        check.fail(
            "machine regression resource_policy must contain exactly "
            "schema_version, statement_resources, design_basis, and policy_sha256"
        )
        return None
    if raw.get("schema_version") != RESOURCE_POLICY_SCHEMA_VERSION:
        check.fail("machine regression resource_policy.schema_version must be integer 1")
    if raw.get("statement_resources") != statement_resources.as_dict():
        check.fail(
            "machine regression resource_policy does not bind the current statement limits"
        )

    design = raw.get("design_basis")
    canonical_design: dict[str, str] | None = None
    if not isinstance(design, dict) or set(design) != set(
        RESOURCE_POLICY_DESIGN_FIELDS
    ):
        check.fail(
            "machine regression resource_policy.design_basis has the wrong fields"
        )
    elif any(
        not isinstance(design.get(key), str)
        or not design[key].strip()
        or design[key] != design[key].strip()
        for key in RESOURCE_POLICY_DESIGN_FIELDS
    ):
        check.fail(
            "machine regression resource_policy.design_basis must contain canonical "
            "non-empty strings"
        )
    else:
        canonical_design = {
            key: design[key] for key in RESOURCE_POLICY_DESIGN_FIELDS
        }

    expected_policy_sha256: str | None = None
    if canonical_design is not None:
        recorded_policy_sha256 = raw.get("policy_sha256")
        recorded_policy_payload = {
            "schema_version": raw.get("schema_version"),
            "statement_resources": raw.get("statement_resources"),
            "design_basis": canonical_design,
        }
        if recorded_policy_sha256 != canonical_json_sha256(
            recorded_policy_payload
        ):
            check.fail("machine regression resource_policy self-digest is invalid")
        expected_policy_sha256 = canonical_json_sha256(
            {
                "schema_version": RESOURCE_POLICY_SCHEMA_VERSION,
                "statement_resources": statement_resources.as_dict(),
                "design_basis": canonical_design,
            }
        )
        if recorded_policy_sha256 != expected_policy_sha256:
            check.fail(
                "machine regression resource_policy digest does not bind the "
                "current statement"
            )

    try:
        plan = load_json(plan_path)
    except ContractError as exc:
        check.fail(str(exc))
    else:
        if (
            not isinstance(plan, dict)
            or plan.get("schema_version") != REGRESSION_PLAN_SCHEMA_VERSION
        ):
            check.fail(
                "machine regression canonical plan must use schema_version "
                f"{REGRESSION_PLAN_SCHEMA_VERSION}"
            )
        if not isinstance(plan, dict) or plan.get("resource_policy") != raw:
            check.fail(
                "machine regression resource_policy differs from the canonical plan"
            )
        receipt_plan = receipt.get("plan")
        if not isinstance(receipt_plan, dict) or receipt_plan.get(
            "canonical_sha256"
        ) != canonical_json_sha256(plan):
            check.fail("machine regression canonical plan digest is missing or stale")

    configuration = receipt.get("configuration")
    if not isinstance(configuration, dict) or configuration.get(
        "resource_policy_sha256"
    ) != expected_policy_sha256:
        check.fail(
            "machine regression configuration is not bound to resource_policy"
        )
    if expected_policy_sha256 is None:
        return None
    report.facts["resource_policy_sha256"] = expected_policy_sha256
    report.facts["time_limit_ms"] = statement_resources.time_limit_ms
    report.facts["memory_limit_mib"] = statement_resources.memory_limit_mib
    return CanonicalResourcePolicy(statement_resources, expected_policy_sha256)


def validate_lightcp_compilation_evidence(
    check: Check,
    record: Any,
    *,
    problem_dir: Path,
    source: Path,
    role: str,
    requested_compile_timeout_seconds: float = 120.0,
    canonical_time_limit_ms: int | None = None,
    canonical_memory_limit_mib: int | None = None,
    canonical_wall_time_multiplier: int | float,
) -> bool:
    """Require compile limits and source/context hashes echoed by the service."""

    issue_count = len(check.issues)
    del requested_compile_timeout_seconds  # Compilation has its own service profile.
    if (canonical_time_limit_ms is None) != (canonical_memory_limit_mib is None):
        check.fail(f"{role} canonical runtime limits must be supplied together")
        return False
    if canonical_time_limit_ms is None or canonical_memory_limit_mib is None:
        try:
            statement_resources = load_statement_resources(problem_dir)
        except StatementResourceError as exc:
            check.fail(f"cannot load canonical runtime limits for {role}: {exc}")
            return False
        canonical_time_limit_ms = statement_resources.time_limit_ms
        canonical_memory_limit_mib = statement_resources.memory_limit_mib
    if type(canonical_time_limit_ms) is not int or canonical_time_limit_ms <= 0:
        check.fail(f"{role} canonical time limit is invalid")
        return False
    if type(canonical_memory_limit_mib) is not int or canonical_memory_limit_mib <= 0:
        check.fail(f"{role} canonical memory limit is invalid")
        return False
    if (
        not isinstance(canonical_wall_time_multiplier, (int, float))
        or isinstance(canonical_wall_time_multiplier, bool)
        or canonical_wall_time_multiplier <= 0
    ):
        check.fail(f"{role} attested wall-time multiplier is invalid")
        return False
    if not isinstance(record, dict):
        check.fail(f"{role} compilation record is missing")
        return False
    evidence = record.get("compilation_evidence")
    if not isinstance(evidence, dict):
        check.fail(f"{role} has no LightCPVerifier compilation evidence")
        return False
    core = dict(evidence)
    digest = core.pop("evidence_sha256", None)
    if digest != canonical_sha256(core):
        check.fail(f"{role} compilation evidence hash is invalid")
    try:
        relative = source.relative_to(problem_dir).as_posix()
        source_digest = sha256_file(source)
        context_digest = compile_context_sha256()
    except Exception as exc:
        check.fail(f"cannot bind {role} compilation context: {exc}")
        return False
    expected_scalars = {
        "schema_version": 1,
        "kind": "cpideas.dataset_compilation",
        "dataset_api_revision": LIGHTCP_DATASET_API_REVISION,
        "source_name": relative,
        "source_sha256": source_digest,
        "compile_context_policy_revision": COMPILE_CONTEXT_POLICY_REVISION,
        "compile_copy_in_files_sha256": context_digest,
        "status": "COMPILED",
        "ok": True,
    }
    for key, expected in expected_scalars.items():
        if evidence.get(key) != expected:
            check.fail(f"{role} compilation evidence.{key} must be {expected!r}")
    if not isinstance(evidence.get("cached"), bool):
        check.fail(f"{role} compilation evidence.cached must be boolean")
    if not isinstance(evidence.get("time_ms"), int) or evidence["time_ms"] < 0:
        check.fail(f"{role} compilation evidence.time_ms is invalid")
    expected_runtime_profile = {
        "requested_time_limit_ms": canonical_time_limit_ms,
        "effective_time_limit_ms": canonical_time_limit_ms,
        "effective_wall_time_limit_ms": round(
            canonical_time_limit_ms * canonical_wall_time_multiplier
        ),
        "requested_memory_limit_mb": canonical_memory_limit_mib,
        "effective_memory_limit_mb": canonical_memory_limit_mib,
        "requested_max_output_bytes": LIGHTCP_MAX_OUTPUT_BYTES,
        "effective_max_output_bytes": LIGHTCP_MAX_OUTPUT_BYTES,
    }
    if evidence.get("runtime_profile_for_subsequent_execution") != (
        expected_runtime_profile
    ):
        check.fail(f"{role} compilation runtime profile does not match ver3")
    if evidence.get("compiler_limits") != {
        "cpu_time_ms": 10000,
        "memory_mb": 512,
        "process_limit": 50,
    }:
        check.fail(f"{role} compilation limits do not match LightCPVerifier")
    return len(check.issues) == issue_count


def validate_execution_backend_evidence(
    check: Check,
    receipt: dict[str, Any],
    backend_configuration: dict[str, Any],
    *,
    canonical_time_limit_ms: int,
    canonical_memory_limit_mib: int,
) -> None:
    """Validate hash-bound, per-invocation sandbox facts for a passed run."""

    if type(canonical_time_limit_ms) is not int or canonical_time_limit_ms <= 0:
        check.fail("canonical statement time limit is invalid")
        return
    if type(canonical_memory_limit_mib) is not int or canonical_memory_limit_mib <= 0:
        check.fail("canonical statement memory limit is invalid")
        return
    evidence = receipt.get("execution_backend_evidence")
    if not isinstance(evidence, dict):
        check.fail("machine regression execution backend evidence is missing")
        return
    expected_metadata = {
        "schema_version": BACKEND_EVIDENCE_SCHEMA_VERSION,
        "kind": "icpc-light.program-dataset-execution-evidence",
        "backend": "lightcpverifier",
        "sandboxed": True,
        "testing_only": False,
        "dataset_api_revision": LIGHTCP_DATASET_API_REVISION,
    }
    for key, expected in expected_metadata.items():
        if evidence.get(key) != expected:
            check.fail(f"execution backend evidence.{key} must be {expected!r}")

    adapter = Path(__file__).resolve().with_name("regression_backend.py")
    expected_adapter_sha256 = (
        sha256_file(adapter)
        if adapter.is_file() and not adapter.is_symlink()
        else None
    )
    if (
        backend_configuration.get("adapter_sha256") != expected_adapter_sha256
        or evidence.get("adapter_sha256") != expected_adapter_sha256
    ):
        check.fail("regression backend adapter hash is missing or stale")

    if backend_configuration.get("dataset_api_revision") != LIGHTCP_DATASET_API_REVISION:
        check.fail("machine regression uses an unsupported CPIdeas dataset API")
    if backend_configuration.get("execution_evidence_schema_version") != (
        BACKEND_EVIDENCE_SCHEMA_VERSION
    ):
        check.fail("machine regression backend evidence schema is unsupported")

    service_identity = backend_configuration.get("service_identity")
    if not isinstance(service_identity, dict):
        check.fail("machine regression has no attested LightCPVerifier identity")
    else:
        if evidence.get("service_identity") != service_identity:
            check.fail("execution evidence service identity does not match configuration")
        if service_identity.get("apiRevision") != LIGHTCP_API_REVISION:
            check.fail("LightCPVerifier API revision does not match ver3")
        for key in ("buildId", "imageId"):
            value = service_identity.get(key)
            if not (
                isinstance(value, str)
                and re.fullmatch(r"sha256:[0-9a-f]{64}", value) is not None
            ):
                check.fail(f"LightCPVerifier service {key} is not attested")

    recorded_modules = backend_configuration.get("client_module_sha256")
    if not isinstance(recorded_modules, dict):
        check.fail("machine regression has no CPIdeas client module bindings")
    else:
        if evidence.get("client_module_sha256") != recorded_modules:
            check.fail("execution evidence CPIdeas module bindings do not match config")
        try:
            current_modules = cpideas_module_bindings()
        except Exception as exc:
            check.fail(f"cannot verify CPIdeas client module bindings: {exc}")
        else:
            if recorded_modules != current_modules:
                check.fail("CPIdeas client modules changed after machine regression")

    invocations = evidence.get("invocations")
    if not isinstance(invocations, list) or not invocations:
        check.fail("production execution evidence has no dataset invocations")
        return
    if evidence.get("invocation_count") != len(invocations):
        check.fail("execution evidence invocation_count does not match its array")
    if evidence.get("invocations_sha256") != canonical_sha256(invocations):
        check.fail("execution evidence invocation array hash is invalid")

    compilation = receipt.get("compilation")
    compilation_items = compilation if isinstance(compilation, list) else []
    compiled_by_role = {
        item.get("role"): item
        for item in compilation_items
        if isinstance(item, dict) and isinstance(item.get("role"), str)
    }
    observed_roles: set[str] = set()
    configured_batch_size = backend_configuration.get("dataset_batch_size")
    configured_max_request_bytes = backend_configuration.get("max_request_bytes")
    configured_output_bytes = backend_configuration.get("max_output_bytes_per_stream")
    configured_time_seconds = backend_configuration.get(
        "sandbox_effective_time_limit_seconds"
    )
    requested_time_seconds = backend_configuration.get(
        "requested_program_timeout_seconds"
    )
    effective_time_seconds = backend_configuration.get(
        "effective_program_timeout_seconds"
    )
    verdict_time_seconds = backend_configuration.get("verdict_time_limit_seconds")
    requested_memory_mb = backend_configuration.get("requested_memory_limit_mb")
    configured_memory_mb = backend_configuration.get("effective_memory_limit_mb")
    numeric_configuration = {
        "dataset_batch_size": configured_batch_size,
        "max_request_bytes": configured_max_request_bytes,
        "max_output_bytes_per_stream": configured_output_bytes,
        "requested_program_timeout_seconds": requested_time_seconds,
        "effective_program_timeout_seconds": effective_time_seconds,
        "sandbox_effective_time_limit_seconds": configured_time_seconds,
        "verdict_time_limit_seconds": verdict_time_seconds,
        "requested_memory_limit_mb": requested_memory_mb,
        "effective_memory_limit_mb": configured_memory_mb,
    }
    if any(
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or value <= 0
        for value in numeric_configuration.values()
    ):
        check.fail("machine regression backend numeric configuration is invalid")
        return
    expected_time_seconds = canonical_time_limit_ms / 1000
    expected_resource_configuration = {
        "requested_program_timeout_seconds": expected_time_seconds,
        "effective_program_timeout_seconds": expected_time_seconds,
        "sandbox_effective_time_limit_seconds": expected_time_seconds,
        "verdict_time_limit_seconds": expected_time_seconds,
        "requested_memory_limit_mb": canonical_memory_limit_mib,
        "effective_memory_limit_mb": canonical_memory_limit_mib,
    }
    for key, expected in expected_resource_configuration.items():
        if backend_configuration.get(key) != expected:
            check.fail(
                f"machine regression backend {key} differs from the canonical "
                "statement resource policy"
            )
    configured_time_ms = canonical_time_limit_ms
    configured_memory_mb = canonical_memory_limit_mib
    policy = (
        service_identity.get("executionPolicy")
        if isinstance(service_identity, dict)
        else None
    )
    runtime_policy = policy.get("runtime") if isinstance(policy, dict) else None
    batch_policy = policy.get("batch") if isinstance(policy, dict) else None
    compilation_policy = (
        policy.get("compilation") if isinstance(policy, dict) else None
    )
    wall_multiplier = (
        runtime_policy.get("wallTimeMultiplier")
        if isinstance(runtime_policy, dict)
        else None
    )
    policy_batch_budget = (
        batch_policy.get("maxCapturedOutputBytes")
        if isinstance(batch_policy, dict)
        else None
    )
    if not isinstance(wall_multiplier, (int, float)) or wall_multiplier <= 0:
        check.fail("LightCPVerifier runtime wall-time policy is invalid")
    if type(policy_batch_budget) is not int or policy_batch_budget <= 0:
        check.fail("LightCPVerifier batch output policy is invalid")
    expected_runtime_policy = {
        "minimumCpuTimeMs": 100,
        "maximumCpuTimeMs": 30000,
        "wallTimeMultiplier": 2,
        "minimumMemoryMb": 16,
        "maximumMemoryMb": 2048,
        "maximumOutputBytes": 16 * 1024 * 1024,
        "processLimit": 128,
        "addressSpaceLimit": True,
    }
    if not isinstance(runtime_policy, dict) or any(
        runtime_policy.get(key) != expected
        for key, expected in expected_runtime_policy.items()
    ):
        check.fail("LightCPVerifier runtime policy does not match ver3")
    elif not (
        runtime_policy["minimumCpuTimeMs"]
        <= canonical_time_limit_ms
        <= runtime_policy["maximumCpuTimeMs"]
        and runtime_policy["minimumMemoryMb"]
        <= canonical_memory_limit_mib
        <= runtime_policy["maximumMemoryMb"]
    ):
        check.fail("canonical statement limits are outside the attested runtime policy")
    if not isinstance(batch_policy, dict) or (
        batch_policy.get("maxTests") != configured_batch_size
        or batch_policy.get("maxCapturedOutputBytes") != policy_batch_budget
    ):
        check.fail("LightCPVerifier batch policy does not match ver3")
    cpp_compile_policy = (
        compilation_policy.get("cpp")
        if isinstance(compilation_policy, dict)
        else None
    )
    if cpp_compile_policy != {
        "cpuTimeMs": 10000,
        "memoryMb": 512,
        "processLimit": 50,
    }:
        check.fail("LightCPVerifier C++ compilation policy does not match ver3")

    forbidden_verdicts = {
        "INFRA",
        "VALIDATOR_ERROR",
        "INVALID_TEST_DATA",
        "NOT_EXECUTED",
    }
    for index, invocation in enumerate(invocations):
        label = f"execution invocation[{index}]"
        if not isinstance(invocation, dict):
            check.fail(f"{label} must be an object")
            continue
        if invocation.get("index") != index:
            check.fail(f"{label}.index is not sequential")
        core = dict(invocation)
        recorded_invocation_hash = core.pop("evidence_sha256", None)
        if recorded_invocation_hash != canonical_sha256(core):
            check.fail(f"{label} evidence hash is invalid")
        role = invocation.get("role")
        source = invocation.get("source")
        source_sha256 = invocation.get("source_sha256")
        if not isinstance(role, str) or not role:
            check.fail(f"{label} has no role")
            continue
        observed_roles.add(role)
        compiled = compiled_by_role.get(role)
        if not isinstance(compiled, dict) or compiled.get("status") != "passed":
            check.fail(f"{label} role {role} has no passed compilation evidence")
        elif (
            compiled.get("source") != source
            or compiled.get("source_sha256") != source_sha256
        ):
            check.fail(f"{label} source binding differs from compilation evidence")
        if not _lower_sha256(source_sha256):
            check.fail(f"{label}.source_sha256 is invalid")
        if not _lower_sha256(invocation.get("requested_case_ids_sha256")):
            check.fail(f"{label}.requested_case_ids_sha256 is invalid")
        if not _lower_sha256(invocation.get("program_results_sha256")):
            check.fail(f"{label}.program_results_sha256 is invalid")

        requested_count = invocation.get("requested_case_count")
        if type(requested_count) is not int or requested_count <= 0:
            check.fail(f"{label}.requested_case_count must be positive")
            continue
        if invocation.get("status") != "completed":
            check.fail(f"{label} did not complete")
        if invocation.get("evaluation_complete") is not True:
            check.fail(f"{label} is not evaluation-complete")

        evaluation = invocation.get("evaluation")
        if not isinstance(evaluation, dict):
            check.fail(f"{label}.evaluation must be an object")
            continue
        expected_evaluation = {
            "schema_version": 1,
            "kind": "cpideas.program_dataset_evaluation",
            "status": "completed",
            "evaluation_complete": True,
            "error": None,
            "comparison": "none",
            "validator": None,
        }
        for key, expected in expected_evaluation.items():
            if evaluation.get(key) != expected:
                check.fail(f"{label}.evaluation.{key} must be {expected!r}")
        program = evaluation.get("program")
        compiled_evidence = (
            compiled.get("compilation_evidence")
            if isinstance(compiled, dict)
            else None
        )
        if not isinstance(program, dict) or (
            program.get("source_name") != source
            or program.get("source_sha256") != source_sha256
            or not isinstance(compiled_evidence, dict)
            or program.get("compile_files_sha256")
            != compiled_evidence.get("compile_copy_in_files_sha256")
        ):
            check.fail(f"{label}.evaluation program binding is stale")
        evaluation_compile = evaluation.get("compilation")
        if not isinstance(evaluation_compile, dict) or (
            evaluation_compile.get("status") != "COMPILED"
            or evaluation_compile.get("ok") is not True
        ):
            check.fail(f"{label}.evaluation compilation did not pass")

        case_binding = evaluation.get("case_results_binding")
        summary = evaluation.get("summary")
        counts = summary.get("verdict_counts") if isinstance(summary, dict) else None
        expected_evaluation_ok: bool | None = None
        if not isinstance(case_binding, dict) or (
            case_binding.get("count") != requested_count
            or not _lower_sha256(case_binding.get("sha256"))
        ):
            check.fail(f"{label} case-result binding is invalid")
        if not isinstance(summary, dict) or summary.get("total") != requested_count:
            check.fail(f"{label} summary total does not match request")
        if not isinstance(counts, dict) or any(
            not isinstance(key, str)
            or type(value) is not int
            or value <= 0
            for key, value in (counts.items() if isinstance(counts, dict) else [])
        ):
            check.fail(f"{label} verdict counts are invalid")
        else:
            if sum(counts.values()) != requested_count:
                check.fail(f"{label} verdict counts do not cover every case")
            unexpected = forbidden_verdicts & set(counts)
            if unexpected:
                check.fail(f"{label} contains untrustworthy verdicts {sorted(unexpected)}")
            allowed_verdicts = {"EXECUTED", "TLE", "MLE", "OLE", "RE"}
            unsupported = set(counts) - allowed_verdicts
            if unsupported:
                check.fail(f"{label} contains unsupported verdicts {sorted(unsupported)}")
            expected_evaluation_ok = counts == {"EXECUTED": requested_count}
            if evaluation.get("ok") is not expected_evaluation_ok:
                check.fail(
                    f"{label}.evaluation.ok is inconsistent with verdict counts"
                )

        evaluation_configuration = evaluation.get("configuration")
        chunks = evaluation.get("chunks")
        if not isinstance(evaluation_configuration, dict) or not isinstance(chunks, list):
            check.fail(f"{label} has no evaluation configuration/chunks")
            continue
        expected_configuration = {
            "requested_time_limit_ms": canonical_time_limit_ms,
            "effective_time_limit_ms": canonical_time_limit_ms,
            "requested_memory_limit_mb": canonical_memory_limit_mib,
            "effective_memory_limit_mb": canonical_memory_limit_mib,
            "requested_max_output_bytes": configured_output_bytes,
            "effective_max_output_bytes": configured_output_bytes,
            "max_batch_output_bytes": policy_batch_budget,
            "validator_limits": None,
            "chunk_count": len(chunks),
            "batch_size": configured_batch_size,
            "max_request_bytes": configured_max_request_bytes,
        }
        for key, expected in expected_configuration.items():
            if evaluation_configuration.get(key) != expected:
                check.fail(
                    f"{label}.evaluation.configuration.{key} must be {expected!r}"
                )
        if isinstance(evaluation_compile, dict):
            expected_evaluation_compile_profile = {
                "requested_time_limit_ms": canonical_time_limit_ms,
                "effective_time_limit_ms": canonical_time_limit_ms,
                "effective_wall_time_limit_ms": (
                    round(canonical_time_limit_ms * wall_multiplier)
                    if isinstance(wall_multiplier, (int, float))
                    else None
                ),
                "requested_memory_limit_mb": canonical_memory_limit_mib,
                "effective_memory_limit_mb": canonical_memory_limit_mib,
                "requested_max_output_bytes": configured_output_bytes,
                "effective_max_output_bytes": configured_output_bytes,
            }
            if evaluation_compile.get(
                "runtime_profile_for_subsequent_execution"
            ) != expected_evaluation_compile_profile:
                check.fail(f"{label}.evaluation compilation runtime profile is invalid")
            if evaluation_compile.get("compiler_limits") != {
                "cpu_time_ms": 10000,
                "memory_mb": 512,
                "process_limit": 50,
            }:
                check.fail(f"{label}.evaluation compilation limits are invalid")

        cursor = 0
        for chunk_index, chunk in enumerate(chunks):
            chunk_label = f"{label}.chunk[{chunk_index}]"
            if not isinstance(chunk, dict):
                check.fail(f"{chunk_label} must be an object")
                continue
            start = chunk.get("start")
            stop = chunk.get("stop")
            if (
                chunk.get("index") != chunk_index
                or start != cursor
                or type(stop) is not int
                or type(start) is not int
                or stop <= start
                or stop - start > configured_batch_size
                or chunk.get("total") != stop - start
            ):
                check.fail(f"{chunk_label} range/total is invalid")
                continue
            cursor = stop
            estimate = chunk.get("request_bytes_estimate")
            if type(estimate) is not int or not 0 <= estimate <= configured_max_request_bytes:
                check.fail(f"{chunk_label} request-size evidence is invalid")
            if (
                chunk.get("status") != "completed"
                or type(chunk.get("ok")) is not bool
                or (
                    expected_evaluation_ok is True
                    and chunk.get("ok") is not True
                )
                or chunk.get("output_truncated") is not False
            ):
                check.fail(f"{chunk_label} is incomplete or truncated")
            expected_chunk_validation_counts = {
                "valid": stop - start,
                "invalid": 0,
                "validator_errors": 0,
            }
            for key, expected in expected_chunk_validation_counts.items():
                if chunk.get(key) != expected or type(chunk.get(key)) is not int:
                    check.fail(f"{chunk_label}.{key} must be {expected}")
            captured = chunk.get("captured_output_bytes")
            budget = chunk.get("max_batch_output_bytes")
            if (
                type(captured) is not int
                or type(budget) is not int
                or not 0 <= captured <= budget
                or budget != policy_batch_budget
            ):
                check.fail(f"{chunk_label} output-budget evidence is invalid")
            expected_chunk_limits = {
                "effective_time_limit_ms": configured_time_ms,
                "effective_wall_time_limit_ms": (
                    round(configured_time_ms * wall_multiplier)
                    if isinstance(wall_multiplier, (int, float))
                    else None
                ),
                "effective_memory_limit_mb": configured_memory_mb,
                "effective_max_output_bytes": configured_output_bytes,
                "effective_validator_time_limit_ms": None,
                "effective_validator_wall_time_limit_ms": None,
                "effective_validator_memory_limit_mb": None,
                "effective_validator_max_output_bytes": None,
            }
            for key, expected in expected_chunk_limits.items():
                if chunk.get(key) != expected:
                    check.fail(f"{chunk_label}.{key} must be {expected!r}")
        if cursor != requested_count:
            check.fail(f"{label} chunks do not cover every requested case")
        if (
            expected_evaluation_ok is False
            and chunks
            and all(
                isinstance(chunk, dict) and chunk.get("ok") is True
                for chunk in chunks
            )
        ):
            check.fail(f"{label} chunk ok flags contradict failed case verdicts")

    required_roles = {"generator", "validator", "std", "brute"}
    if not required_roles.issubset(observed_roles):
        check.fail(
            "execution evidence is missing required roles: "
            f"{sorted(required_roles - observed_roles)}"
        )
    compiled_roles = set(compiled_by_role)
    if not observed_roles.issubset(compiled_roles):
        check.fail("execution evidence contains an uncompiled program role")
    if not check.issues:
        check.add(
            f"validated {len(invocations)} hash-bound Program x Dataset invocation(s)"
        )


def check_machine_regression(
    report: Report, wrong_sources: list[tuple[str, Path]]
) -> dict[str, Any] | None:
    check = report.new_check("machine-regression")
    receipt_path = report.problem_dir / REGRESSION_MACHINE_REL
    if report.issues:
        check.fail(
            "machine regression not launched because an earlier structural or "
            "handoff gate failed"
        )
        return None
    executor = Path(__file__).resolve().with_name(REGRESSION_EXECUTOR_NAME)
    if executor.is_symlink() or not executor.is_file():
        check.fail(f"missing canonical regression executor: {executor}")
        return None
    command = [
        sys.executable,
        str(executor),
        "--problem-dir",
        str(report.problem_dir),
        "--plan",
        "audit/regression-plan.json",
        "--receipt-out",
        REGRESSION_MACHINE_REL,
        "--json",
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=report.problem_dir,
            capture_output=True,
            text=True,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        check.fail(f"could not execute canonical machine regression: {exc}")
        return None
    if completed.returncode != 0:
        diagnostic = (completed.stderr or completed.stdout).strip()
        if len(diagnostic) > 6000:
            diagnostic = diagnostic[:6000] + "\n... output truncated ..."
        check.fail(
            f"canonical machine regression exited {completed.returncode}"
            + (f":\n{diagnostic}" if diagnostic else "")
        )
    if not require_file(
        report, check, receipt_path, "audit/regression-machine.json"
    ):
        return None
    try:
        receipt = load_json(receipt_path)
    except ContractError as exc:
        check.fail(str(exc))
        return None
    if not isinstance(receipt, dict):
        check.fail("machine regression receipt must be a JSON object")
        return None
    expected = {
        "schema_version": 1,
        "gate": "icpc-light-regression-machine",
        "execution_mode": "production",
        "production": True,
        "status": "passed",
        "problem_dir": ".",
        "receipt_path": REGRESSION_MACHINE_REL,
    }
    for key, value in expected.items():
        if receipt.get(key) != value:
            check.fail(f"machine regression receipt.{key} must be {value!r}")
    if receipt.get("errors") != []:
        check.fail("machine regression receipt.errors must be an empty list")
    plan_path = report.problem_dir / "audit/regression-plan.json"
    resource_policy = validate_machine_resource_policy(
        report, check, receipt, plan_path
    )
    try:
        canonical_plan = load_json(plan_path)
    except ContractError as exc:
        check.fail(str(exc))
        canonical_plan = {}
    if not isinstance(canonical_plan, dict):
        check.fail("canonical regression plan must be a JSON object")
        canonical_plan = {}
    bound_paths, canonical_sample_ids = validate_regression_artifact_bindings(
        report, check, receipt
    )
    attested_wall_time_multiplier: int | float | None = None
    configuration = receipt.get("configuration")
    if not isinstance(configuration, dict):
        check.fail("machine regression receipt.configuration must be an object")
    else:
        if configuration.get("random_minimum") != 5000:
            check.fail("machine regression production random minimum must be 5000")
        overrides = configuration.get("testing_overrides")
        if not isinstance(overrides, dict) or any(
            value is not None for value in overrides.values()
        ):
            check.fail("machine regression production receipt contains testing overrides")
        if resource_policy is not None:
            expected_time_seconds = resource_policy.time_limit_ms / 1000
            expected_configuration = {
                "program_timeout_seconds": expected_time_seconds,
                "requested_program_timeout_seconds": expected_time_seconds,
                "requested_memory_limit_mb": resource_policy.memory_limit_mib,
                "resource_policy_sha256": resource_policy.policy_sha256,
                "verdict_time_limit_seconds": expected_time_seconds,
                "sandbox_effective_time_limit_seconds": expected_time_seconds,
                "sandbox_effective_memory_limit_mb": (
                    resource_policy.memory_limit_mib
                ),
            }
            for key, expected_value in expected_configuration.items():
                if configuration.get(key) != expected_value:
                    check.fail(
                        f"machine regression configuration.{key} differs from "
                        "the canonical resource policy"
                    )
        backend = configuration.get("execution_backend")
        if not isinstance(backend, dict):
            check.fail("machine regression execution backend evidence is missing")
        elif resource_policy is not None:
            service_identity = backend.get("service_identity")
            execution_policy = (
                service_identity.get("executionPolicy")
                if isinstance(service_identity, dict)
                else None
            )
            runtime_policy = (
                execution_policy.get("runtime")
                if isinstance(execution_policy, dict)
                else None
            )
            if isinstance(runtime_policy, dict):
                attested_wall_time_multiplier = runtime_policy.get(
                    "wallTimeMultiplier"
                )
            expected_time_seconds = resource_policy.time_limit_ms / 1000
            expected_backend = {
                "name": "lightcpverifier",
                "sandboxed": True,
                "testing_only": False,
                "requested_program_timeout_seconds": expected_time_seconds,
                "effective_program_timeout_seconds": expected_time_seconds,
                "verdict_time_limit_seconds": expected_time_seconds,
                "sandbox_effective_time_limit_seconds": expected_time_seconds,
                "timeout_classification": "sandbox-enforced",
                "requested_compile_timeout_seconds": 120.0,
                "effective_compile_timeout_seconds": None,
                "requested_memory_limit_mb": resource_policy.memory_limit_mib,
                "effective_memory_limit_mb": resource_policy.memory_limit_mib,
                "dataset_batch_size": 128,
                "max_request_bytes": 60 * 1024 * 1024,
                "max_output_bytes_per_stream": 16 * 1024 * 1024,
                "compile_context_policy_revision": COMPILE_CONTEXT_POLICY_REVISION,
                "cpp_compiler_profile": LIGHTCP_CPP_PROFILE,
                "dataset_api_revision": LIGHTCP_DATASET_API_REVISION,
                "execution_evidence_schema_version": (
                    BACKEND_EVIDENCE_SCHEMA_VERSION
                ),
            }
            for key, value in expected_backend.items():
                if backend.get(key) != value:
                    check.fail(
                        f"machine regression execution_backend.{key} must be {value!r}"
                    )
            for key, nested_key in (
                ("verdict_time_limit_seconds", "verdict_time_limit_seconds"),
                (
                    "sandbox_effective_time_limit_seconds",
                    "sandbox_effective_time_limit_seconds",
                ),
                ("sandbox_effective_memory_limit_mb", "effective_memory_limit_mb"),
            ):
                if configuration.get(key) != backend.get(nested_key):
                    check.fail(
                        f"machine regression configuration.{key} differs from backend"
                    )
            validate_execution_backend_evidence(
                check,
                receipt,
                backend,
                canonical_time_limit_ms=resource_policy.time_limit_ms,
                canonical_memory_limit_mib=resource_policy.memory_limit_mib,
            )
    receipt_plan = receipt.get("plan")
    if not isinstance(receipt_plan, dict):
        check.fail("machine regression receipt.plan must be an object")
    else:
        if receipt_plan.get("path") != "audit/regression-plan.json":
            check.fail("machine regression receipt binds the wrong plan path")
        expected_hash = sha256_file(plan_path) if plan_path.is_file() else None
        if receipt_plan.get("sha256") != expected_hash:
            check.fail("machine regression receipt plan hash is stale")
    privacy = receipt.get("privacy_scan")
    if not isinstance(privacy, dict) or privacy.get("status") != "passed":
        check.fail("machine regression package privacy scan did not pass")
    elif (
        privacy.get("scan_scope")
        != "package-boundary-and-common-secret-leak-v1"
        or privacy.get("forbidden_entries") != []
        or privacy.get("content_findings") != []
    ):
        check.fail("machine regression package privacy/leak scan is incomplete")
    differential = receipt.get("differential")
    if not isinstance(differential, dict) or differential.get("status") != "passed":
        check.fail("machine regression differential matrix did not pass")
    else:
        generator = differential.get("generator")
        generator_source = generator.get("source") if isinstance(generator, dict) else None
        if (
            not isinstance(generator_source, str)
            or not generator_source.startswith("package/generators/")
            or generator_source not in bound_paths
        ):
            check.fail("machine regression generator is outside bound package/generators")
    for required_path in (
        "package/std.cpp",
        "package/brute.cpp",
        "package/validator.cpp",
        CANONICAL_SAMPLE_MANIFEST,
    ):
        if required_path not in bound_paths:
            check.fail(f"machine regression artifact bindings omit {required_path}")
    releases = receipt.get("release_tests")
    if not isinstance(releases, list) or not releases or any(
        not isinstance(item, dict) or item.get("status") != "passed"
        for item in releases
    ):
        check.fail("machine regression release-test matrix is missing or failed")
    elif any(
        not str(item.get("input", "")).startswith("package/tests/")
        or not str(item.get("answer", "")).startswith("package/tests/")
        or item.get("input") not in bound_paths
        or item.get("answer") not in bound_paths
        for item in releases
    ):
        check.fail("machine regression release inputs/answers are outside bound package/tests")
    wrong_records = receipt.get("wrong_routes")
    if not isinstance(wrong_records, list) or any(
        not isinstance(item, dict) or item.get("status") != "passed"
        for item in wrong_records
    ):
        check.fail("machine regression wrong-route matrix is missing or failed")

    matrix = report.facts.get("wrong_route_matrix")
    if not isinstance(matrix, list):
        check.fail("qualified wrong-route audit matrix is unavailable")
        matrix = []
    expected_bindings = [
        {
            "route_id": item.get("route_id"),
            "source": item.get("source"),
            "source_sha256": item.get("source_sha256"),
        }
        for item in matrix
    ]
    bindings = receipt.get("qualified_wrong_route_bindings")
    if bindings != expected_bindings:
        check.fail(
            "machine regression qualified wrong-route IDs/sources/hashes do not "
            "match audit/wrong-solutions.md"
        )
    records_by_id = {
        item.get("route_id"): item
        for item in wrong_records
        if isinstance(item, dict) and isinstance(item.get("route_id"), str)
    } if isinstance(wrong_records, list) else {}
    for item in matrix:
        route_id = item.get("route_id")
        record = records_by_id.get(route_id)
        if not isinstance(record, dict):
            check.fail(f"machine regression omitted qualified route {route_id}")
            continue
        if (
            record.get("source") != item.get("source")
            or record.get("source_sha256") != item.get("source_sha256")
            or record.get("expected_verdict") != item.get("expected_verdict")
        ):
            check.fail(f"machine regression route binding differs for {route_id}")
        samples = record.get("sample_results")
        if not isinstance(samples, list) or [
            sample.get("sample_id") if isinstance(sample, dict) else None
            for sample in samples
        ] != canonical_sample_ids:
            check.fail(
                f"machine regression route {route_id} did not run every canonical sample"
            )
        elif any(
            sample.get("status") != "passed"
            or not str(sample.get("input", "")).startswith("package/samples/")
            or not str(sample.get("answer", "")).startswith("package/samples/")
            or sample.get("input") not in bound_paths
            or sample.get("answer") not in bound_paths
            for sample in samples
        ):
            check.fail(f"machine regression route {route_id} sample matrix is invalid")
        ordinary = record.get("ordinary_result")
        if (
            not isinstance(ordinary, dict)
            or ordinary.get("status") != "passed"
            or not str(ordinary.get("input", "")).startswith("package/tests/ordinary/")
            or ordinary.get("input") not in bound_paths
        ):
            check.fail(f"machine regression route {route_id} ordinary input is invalid")
        breaker = record.get("breaker_result")
        if not isinstance(breaker, dict):
            check.fail(f"machine regression route {route_id} has no breaker result")
        elif (
            breaker.get("input") != item.get("breaker_input")
            or breaker.get("observed_verdict") != item.get("observed_verdict")
            or breaker.get("status") != "passed"
            or not str(breaker.get("input", "")).startswith("package/tests/breakers/")
            or breaker.get("input") not in bound_paths
        ):
            check.fail(
                f"machine regression breaker path/verdict differs for {route_id}"
            )
    validate_strengthened_regression_evidence(
        report,
        check,
        receipt,
        canonical_plan,
        bound_paths=bound_paths,
        wrong_records=wrong_records if isinstance(wrong_records, list) else [],
        wrong_matrix=matrix,
    )
    facts = receipt.get("facts")
    if not isinstance(facts, dict):
        check.fail("machine regression receipt.facts must be an object")
        facts = {}
    else:
        required_fact_keys = {
            "differential_mode",
            "differential_cases_requested",
            "differential_cases_completed",
            "differential_consecutive_seeds",
            "generated_inputs_validated",
            "release_tests_checked",
            "wrong_routes_checked",
            "survivability_inputs_checked",
            "accepted_alternatives_checked",
            "accepted_non_jury_outputs_checked",
            "accepted_alternative_strategy",
            "canonical_samples_checked_per_wrong_route",
            "sample_manifest_sha256",
            "qualified_wrong_route_ids",
            "required_limit_tags",
            "covered_limit_tags",
            "limit_coverage_status",
        }
        missing = required_fact_keys - set(facts)
        if missing:
            check.fail(f"machine regression facts are missing: {sorted(missing)}")
        if facts.get("limit_coverage_status") != "passed":
            check.fail("machine regression important-limit coverage did not pass")
        if not (
            isinstance(facts.get("required_limit_tags"), list)
            and isinstance(facts.get("covered_limit_tags"), list)
            and sorted(facts["required_limit_tags"])
            == sorted(facts["covered_limit_tags"])
        ):
            check.fail("machine regression required and covered limit tags differ")
        if facts.get("differential_cases_requested") != facts.get(
            "differential_cases_completed"
        ):
            check.fail("machine regression did not complete every requested case")
        if facts.get("qualified_wrong_route_ids") != [
            item.get("route_id") for item in matrix
        ]:
            check.fail("machine regression wrong-route fact order differs from audit")
        if facts.get("canonical_samples_checked_per_wrong_route") != len(
            canonical_sample_ids
        ):
            check.fail("machine regression canonical sample count fact is inconsistent")
        if facts.get("sample_manifest_sha256") != report.facts.get(
            "canonical_sample_manifest_sha256"
        ):
            check.fail("machine regression canonical sample manifest fact is stale")
    compilation = receipt.get("compilation")
    if not isinstance(compilation, list) or not compilation:
        check.fail("machine regression compilation records are missing")
    else:
        for index, item in enumerate(compilation):
            if not isinstance(item, dict) or item.get("status") != "passed":
                check.fail(f"machine regression compilation[{index}] did not pass")
                continue
            try:
                relative, source = safe_problem_path(
                    report.problem_dir,
                    item.get("source"),
                    label=f"machine regression compilation[{index}].source",
                    require_exists=True,
                )
            except ContractError as exc:
                check.fail(str(exc))
                continue
            if item.get("source_sha256") != sha256_file(source):
                check.fail(f"machine regression compiled source changed: {relative}")
            validate_lightcp_compilation_evidence(
                check,
                item,
                problem_dir=report.problem_dir,
                source=source,
                role=str(item.get("role") or f"compilation[{index}]"),
                requested_compile_timeout_seconds=120.0,
                canonical_time_limit_ms=(
                    resource_policy.time_limit_ms
                    if resource_policy is not None
                    else None
                ),
                canonical_memory_limit_mib=(
                    resource_policy.memory_limit_mib
                    if resource_policy is not None
                    else None
                ),
                canonical_wall_time_multiplier=attested_wall_time_multiplier,
            )
    if not check.issues:
        machine_facts = {
            "differential_mode": facts.get("differential_mode"),
            "differential_cases_completed": facts.get(
                "differential_cases_completed"
            ),
            "differential_consecutive_seeds": facts.get(
                "differential_consecutive_seeds"
            ),
            "generated_inputs_validated": facts.get("generated_inputs_validated"),
            "release_tests_checked": facts.get("release_tests_checked"),
            "wrong_routes_checked": facts.get("wrong_routes_checked"),
            "survivability_inputs_checked": facts.get(
                "survivability_inputs_checked"
            ),
            "accepted_alternatives_checked": facts.get(
                "accepted_alternatives_checked"
            ),
            "accepted_non_jury_outputs_checked": facts.get(
                "accepted_non_jury_outputs_checked"
            ),
            "accepted_alternative_strategy": facts.get(
                "accepted_alternative_strategy"
            ),
        }
        report.facts["machine_regression"] = machine_facts
        report.facts["regression_machine_sha256"] = sha256_file(receipt_path)
        report.facts["regression_executor_sha256"] = sha256_file(executor)
        report.facts["execution_backend_evidence_sha256"] = canonical_json_sha256(
            receipt.get("execution_backend_evidence")
        )
        check.add(
            f"executed {facts.get('differential_cases_completed')} differential "
            f"cases, {facts.get('release_tests_checked')} release tests, and "
            f"{facts.get('wrong_routes_checked')} qualified wrong routes with "
            f"{facts.get('survivability_inputs_checked')} survivability checks"
        )
    return receipt if not check.issues else None


def check_lightcp_compile_only(
    check: Check,
    *,
    problem_dir: Path,
    source: Path,
    role: str,
    lightcpverifier_url: str,
) -> None:
    """Compile one hash-bound source in LightCPVerifier, with no host fallback."""

    if source.is_symlink() or not source.is_file():
        check.fail(f"{role} source is unavailable or unsafe")
        return
    if source.suffix.lower() not in CPP_SUFFIXES:
        check.fail(f"{role} source is not C/C++: {source}")
        return
    try:
        relative = source.relative_to(problem_dir).as_posix()
    except ValueError:
        check.fail(f"{role} source escapes the problem directory: {source}")
        return
    try:
        statement_resources = load_statement_resources(problem_dir)
        backend = create_backend(
            "lightcpverifier",
            test_mode=False,
            lightcpverifier_url=lightcpverifier_url,
            program_time_limit_ms=statement_resources.time_limit_ms,
            memory_limit_mb=statement_resources.memory_limit_mib,
        )
        backend_configuration = backend.configuration(
            requested_program_timeout_seconds=(
                statement_resources.time_limit_ms / 1000
            ),
            requested_compile_timeout_seconds=120.0,
            requested_memory_limit_mb=statement_resources.memory_limit_mib,
        )
        service_identity = backend_configuration.get("service_identity")
        execution_policy = (
            service_identity.get("executionPolicy")
            if isinstance(service_identity, dict)
            else None
        )
        runtime_policy = (
            execution_policy.get("runtime")
            if isinstance(execution_policy, dict)
            else None
        )
        attested_wall_time_multiplier = (
            runtime_policy.get("wallTimeMultiplier")
            if isinstance(runtime_policy, dict)
            else None
        )
        with tempfile.TemporaryDirectory(prefix="icpc-light-compile-only-") as raw:
            programs, records, errors = backend.compile_sources(
                [BackendSource(role, relative, source)],
                problem_dir=problem_dir,
                build_dir=Path(raw),
                timeout=120,
            )
    except Exception as exc:
        check.fail(f"LightCPVerifier compile-only unavailable: {exc}")
        return
    if errors or role not in programs or len(records) != 1:
        diagnostic = ""
        if records and isinstance(records[0].get("result"), dict):
            result = records[0]["result"]
            diagnostic = str(
                result.get("stderr_preview") or result.get("launch_error") or ""
            ).strip()
        check.fail(
            f"LightCPVerifier compilation failed for {relative}"
            + (f": {diagnostic}" if diagnostic else "")
        )
        return
    record = records[0]
    if (
        record.get("status") != "passed"
        or record.get("source") != relative
        or record.get("source_sha256") != sha256_file(source)
    ):
        check.fail(f"LightCPVerifier returned stale compile evidence for {relative}")
        return
    if not validate_lightcp_compilation_evidence(
        check,
        record,
        problem_dir=problem_dir,
        source=source,
        role=role,
        requested_compile_timeout_seconds=120.0,
        canonical_time_limit_ms=statement_resources.time_limit_ms,
        canonical_memory_limit_mib=statement_resources.memory_limit_mib,
        canonical_wall_time_multiplier=attested_wall_time_multiplier,
    ):
        return
    check.add(
        f"LightCPVerifier compiled {relative} at source sha256 {record['source_sha256']}"
    )


def check_compilation(
    report: Report,
    std: Path | None,
    oracle: Path | None,
    checker_source: Path | None,
    validator_sources: list[Path],
    wrong_sources: list[tuple[str, Path]],
    machine_receipt: dict[str, Any] | None,
) -> None:
    check = report.new_check("sandbox-compilation-evidence")
    if report.skip_compile:
        check.fail(
            "--skip-compile cannot certify completion; ver3 requires sandbox "
            "compilation evidence from machine regression"
        )
        return
    if not isinstance(machine_receipt, dict):
        check.fail("passed machine regression receipt is unavailable")
        return
    configuration = machine_receipt.get("configuration")
    backend = (
        configuration.get("execution_backend")
        if isinstance(configuration, dict)
        else None
    )
    if not isinstance(backend, dict) or any(
        backend.get(key) != value
        for key, value in {
            "name": "lightcpverifier",
            "sandboxed": True,
            "testing_only": False,
        }.items()
    ):
        check.fail(
            "compilation evidence is not from the production sandboxed "
            "LightCPVerifier backend"
        )
        return
    if len(validator_sources) != 1:
        check.fail("ver3 regression evidence requires exactly one package validator")
        return
    sources: list[tuple[str, Path | None]] = [
        ("std", std),
        ("brute", oracle),
        ("validator", validator_sources[0]),
    ]
    if checker_source is not None:
        sources.append(("checker", checker_source))
    sources.extend((f"wrong:{route_id}", source) for route_id, source in wrong_sources)
    accepted_bindings = machine_receipt.get("accepted_alternative_bindings")
    if accepted_bindings is None:
        accepted_bindings = []
    elif not isinstance(accepted_bindings, list):
        check.fail("machine regression accepted-alternative bindings are missing")
        accepted_bindings = []
    for index, binding in enumerate(accepted_bindings):
        if not isinstance(binding, dict):
            check.fail(f"accepted_alternative_bindings[{index}] must be an object")
            continue
        alternative_id = binding.get("alternative_id")
        try:
            _, source = safe_problem_path(
                report.problem_dir,
                binding.get("source"),
                label=f"accepted_alternative_bindings[{index}].source",
                require_exists=True,
            )
        except ContractError as exc:
            check.fail(str(exc))
            continue
        if not isinstance(alternative_id, str) or not alternative_id:
            check.fail(
                f"accepted_alternative_bindings[{index}].alternative_id is invalid"
            )
            continue
        sources.append((f"accepted:{alternative_id}", source))
    compilation = machine_receipt.get("compilation")
    if not isinstance(compilation, list) or not compilation:
        check.fail("machine regression compilation evidence is missing")
        return
    by_role: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(compilation):
        if not isinstance(item, dict) or not isinstance(item.get("role"), str):
            check.fail(f"compilation[{index}] has no program role")
            continue
        role = item["role"]
        if role in by_role:
            check.fail(f"machine regression duplicates compilation role {role}")
            continue
        by_role[role] = item
    for role, source in sources:
        if source is None or not source.is_file() or source.is_symlink():
            check.fail(f"{role} source is unavailable or unsafe")
            continue
        if source.suffix.lower() not in CPP_SUFFIXES:
            check.fail(f"{role} source is not C/C++: {source}")
            continue
        try:
            relative = source.relative_to(report.problem_dir).as_posix()
        except ValueError:
            check.fail(f"{role} source escapes the problem directory: {source}")
            continue
        evidence = by_role.get(role)
        if not isinstance(evidence, dict):
            check.fail(f"machine regression did not compile role {role}")
            continue
        if evidence.get("status") != "passed":
            check.fail(f"machine regression compilation failed for role {role}")
        if evidence.get("source") != relative:
            check.fail(
                f"machine regression role {role} binds {evidence.get('source')!r}, "
                f"expected {relative!r}"
            )
        digest = sha256_file(source)
        if evidence.get("source_sha256") != digest:
            check.fail(f"machine regression compilation hash is stale for role {role}")
        result = evidence.get("result")
        if (
            not isinstance(result, dict)
            or result.get("returncode") != 0
            or result.get("timed_out") is not False
            or result.get("launch_error") is not None
        ):
            check.fail(f"machine regression has invalid compile result for role {role}")
    if not check.issues:
        check.add(
            f"reused {len(sources)} hash-bound LightCPVerifier compilation record(s)"
        )


def finalize_facts(
    report: Report,
    grade: dict[str, Any] | None,
    verified_claims: list[dict[str, str]],
) -> None:
    if grade is not None:
        for key in (
            "preclassification",
            "data_buildability",
            "workflow_profile",
            "scam_status",
            "wrong_solution_min",
            "wrong_solution_max",
            "adversarial_round_min",
            "adversarial_round_max",
        ):
            report.facts[key] = grade.get(key)
    report.facts["verified_full_solutions"] = len(verified_claims)


def receipt_payload(report: Report) -> dict[str, Any]:
    inputs: list[dict[str, Any]] = []
    for relative in sorted(report.tracked, key=lambda path: path.as_posix()):
        path = report.problem_dir / relative
        inputs.append(
            {
                "path": relative.as_posix(),
                "sha256": sha256_file(path),
                "size": path.stat().st_size,
            }
        )
    watched = [
        {"path": root, "files": files}
        for root, files in sorted(report.watched_trees.items())
    ]
    return {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "gate": GATE_NAME,
        "verdict": "passed",
        "generated_at_utc": utc_now(),
        "problem_dir": ".",
        "completion_verifier": {
            "name": Path(__file__).name,
            "sha256": sha256_file(Path(__file__).resolve()),
        },
        "blind_gate": report.blind_gate,
        "facts": report.facts,
        "inputs": inputs,
        "watched_trees": watched,
        "checks": [check.as_dict() for check in report.checks],
    }


def emit(report: Report, payload: dict[str, Any] | None, as_json: bool) -> None:
    if as_json:
        output = {
            "schema_version": 1,
            "gate": GATE_NAME,
            "status": "pass" if report.passed else "fail",
            "problem_dir": str(report.problem_dir),
            "receipt": report.receipt_rel if report.passed else None,
            "checks": [check.as_dict() for check in report.checks],
            "issues": report.issues,
        }
        if payload is not None:
            output["receipt_sha256"] = sha256_file(report.problem_dir / report.receipt_rel)
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return
    stream = sys.stdout if report.passed else sys.stderr
    print(
        "ICPC Light pre-readiness completion gate: "
        + ("PASS" if report.passed else f"FAIL ({len(report.issues)} issue(s))"),
        file=stream,
    )
    print(f"problem_dir: {report.problem_dir}", file=stream)
    if report.passed:
        print(f"receipt: {report.receipt_rel}", file=stream)
    for check in report.checks:
        print(f"[{check.status.upper()}] {check.check_id}", file=stream)
        for evidence in check.evidence:
            print(f"  evidence: {evidence}", file=stream)
        for issue in check.issues:
            print(f"  - {issue}", file=stream)


def main() -> int:
    args = parse_args()
    report = Report(args.problem_dir, args.receipt_rel, args.skip_compile)
    receipt_check = report.new_check("receipt-output")
    try:
        invalidate_receipt(args.receipt_path)
        invalidate_receipt(args.problem_dir / REGRESSION_MACHINE_REL)
        receipt_check.add(f"invalidated prior receipt before verification: {args.receipt_rel}")
        receipt_check.add(
            f"invalidated prior machine regression before replay: {REGRESSION_MACHINE_REL}"
        )
    except (OSError, ContractError) as exc:
        receipt_check.fail(str(exc))

    check_blind_stage(report)
    check_required_artifacts(report)
    check_stage_execution_receipts(report)
    check_run_state_policy(report)
    grade = check_grade(report)
    verified_claims = check_verified_claims(report)
    selected_route = check_selected_standard_route(report, grade, verified_claims)
    materialization_mode = check_solution_draft_and_materialization(
        report, verified_claims, selected_route
    )
    std = check_solution_provenance(
        report, materialization_mode, selected_route
    )
    wrong_sources = check_wrong_solutions(report, grade)
    check_adversarial_rounds(report, grade)
    check_test_manifest(report)
    check_coverage_matrix(report)
    package_std, oracle, checker_source, validator_sources = check_package(report)
    machine_receipt = check_machine_regression(report, wrong_sources)
    check_regression(report)
    if std is None:
        std = package_std
    check_compilation(
        report,
        std,
        oracle,
        checker_source,
        validator_sources,
        wrong_sources,
        machine_receipt,
    )
    finalize_facts(report, grade, verified_claims)

    payload: dict[str, Any] | None = None
    if report.passed:
        try:
            payload = receipt_payload(report)
            atomic_write_json(args.receipt_path, payload)
        except (OSError, ContractError) as exc:
            receipt_check.fail(f"could not write completion receipt: {exc}")
            payload = None
            try:
                invalidate_receipt(args.receipt_path)
            except (OSError, ContractError):
                pass
    emit(report, payload, args.json)
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
