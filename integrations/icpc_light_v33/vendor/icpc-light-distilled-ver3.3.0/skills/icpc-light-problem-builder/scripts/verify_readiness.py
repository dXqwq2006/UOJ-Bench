#!/usr/bin/env python3
"""Verify the final ICPC Light readiness decision against a completion receipt.

This script is read-only.  It does not write or repair ``audit/readiness.md``.
It first proves that every file and watched tree bound by the pre-readiness
completion receipt is unchanged, then validates the schema-v2 ``go`` decision.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import verify_completion as completion


GATE_NAME = "icpc-light-final-readiness"
DEFAULT_RECEIPT_REL = completion.DEFAULT_RECEIPT_REL
READINESS_REL = "audit/readiness.md"

READINESS_REQUIRED_FIELDS = (
    "schema_version",
    "verdict",
    "agent_model",
    "agent_reasoning_effort",
    "model_policy_status",
    "preclassification",
    "workflow_profile",
    "scam_status",
    "blind_gate",
    "verified_full_solutions",
    "std_path",
    "std_sha256",
    "wrong_solutions_qualified",
    "wrong_solutions_required_min",
    "adversarial_rounds_completed",
    "adversarial_round_limit",
    "completion_gate",
    "machine_regression",
    "adversarial_round_chain",
    "stage_execution_receipts",
    "std_materialization_mode",
    "repair_used",
    "blockers",
    "evidence",
)


class Gate:
    def __init__(self, problem_dir: Path) -> None:
        self.problem_dir = problem_dir
        self.checks: list[completion.Check] = []

    def new_check(self, check_id: str) -> completion.Check:
        check = completion.Check(check_id)
        self.checks.append(check)
        return check

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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Verify that the ICPC Light completion receipt is current and that "
            "audit/readiness.md is a consistent schema-v2 go decision."
        )
    )
    parser.add_argument("--problem-dir", type=Path, required=True)
    parser.add_argument(
        "--receipt",
        default=DEFAULT_RECEIPT_REL,
        help=f"Problem-relative completion receipt (default: {DEFAULT_RECEIPT_REL}).",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if not args.problem_dir.exists() or not args.problem_dir.is_dir():
        parser.error(f"problem directory is not an existing directory: {args.problem_dir}")
    if args.problem_dir.is_symlink():
        parser.error("--problem-dir itself must not be a symbolic link")
    args.problem_dir = args.problem_dir.resolve()
    try:
        args.receipt_rel, args.receipt_path = completion.safe_problem_path(
            args.problem_dir,
            args.receipt,
            label="--receipt",
            require_exists=False,
        )
    except completion.ContractError as exc:
        parser.error(str(exc))
    return args


def current_tree_files(problem_dir: Path, root_raw: Any, check: completion.Check) -> list[str]:
    try:
        root_rel, root = completion.safe_problem_path(
            problem_dir,
            root_raw,
            label="completion receipt watched tree",
            require_exists=True,
        )
    except completion.ContractError as exc:
        check.fail(str(exc))
        return []
    if root.is_symlink() or not root.is_dir():
        check.fail(f"watched tree is not a regular directory: {root_rel}")
        return []
    files: list[str] = []
    for path in sorted(root.rglob("*")):
        relative_inside = path.relative_to(root)
        if any(part.startswith(".") for part in relative_inside.parts):
            relative = path.relative_to(problem_dir).as_posix()
            check.fail(f"watched tree now contains hidden entry: {relative}")
            continue
        relative = path.relative_to(problem_dir).as_posix()
        if path.is_symlink():
            check.fail(f"watched tree now contains symbolic link: {relative}")
        elif path.is_file():
            try:
                path.stat()
                files.append(relative)
            except OSError as exc:
                check.fail(f"cannot stat watched-tree file {relative}: {exc}")
        elif not path.is_dir():
            check.fail(f"watched tree contains unsupported entry: {relative}")
    return files


def check_receipt(
    gate: Gate, receipt_rel: str, receipt_path: Path
) -> dict[str, Any] | None:
    check = gate.new_check("completion-receipt")
    if receipt_path.is_symlink() or not receipt_path.is_file():
        check.fail(f"completion receipt is not a regular file: {receipt_path}")
        return None
    try:
        if receipt_path.stat().st_size == 0:
            check.fail(f"completion receipt is empty: {receipt_path}")
            return None
        receipt = completion.load_json(receipt_path)
    except (OSError, completion.ContractError) as exc:
        check.fail(str(exc))
        return None
    if not isinstance(receipt, dict):
        check.fail("completion receipt top level must be an object")
        return None
    if receipt.get("schema_version") != completion.RECEIPT_SCHEMA_VERSION:
        check.fail("completion receipt schema_version must be integer 1")
    if receipt.get("gate") != completion.GATE_NAME:
        check.fail(f"completion receipt gate must be {completion.GATE_NAME!r}")
    if receipt.get("verdict") != "passed":
        check.fail("completion receipt verdict must be 'passed'")
    if receipt.get("problem_dir") != ".":
        check.fail("completion receipt problem_dir must be portable value '.'")

    completion_verifier = receipt.get("completion_verifier")
    current_completion = Path(completion.__file__).resolve()
    if not isinstance(completion_verifier, dict) or completion_verifier.get(
        "name"
    ) != current_completion.name:
        check.fail("completion receipt verifier provenance is invalid")
    else:
        expected = completion_verifier.get("sha256")
        if not isinstance(expected, str) or completion.HASH_RE.fullmatch(expected) is None:
            check.fail("completion receipt verifier hash is invalid")
        elif completion.sha256_file(current_completion) != expected:
            check.fail("completion verifier changed; rerun completion gate")

    blind = receipt.get("blind_gate")
    if not isinstance(blind, dict) or blind.get("verdict") != "passed":
        check.fail("completion receipt must record a passed blind gate")
    else:
        verifier = Path(__file__).resolve().with_name(completion.BLIND_VERIFIER_NAME)
        expected_hash = blind.get("verifier_sha256")
        if (
            verifier.is_symlink()
            or not verifier.is_file()
            or not isinstance(expected_hash, str)
            or completion.HASH_RE.fullmatch(expected_hash) is None
        ):
            check.fail("completion receipt blind verifier provenance is invalid")
        else:
            try:
                actual = completion.sha256_file(verifier)
            except OSError as exc:
                check.fail(f"cannot hash current blind verifier: {exc}")
            else:
                if actual != expected_hash:
                    check.fail(
                        "blind verifier changed after completion; rerun completion gate"
                    )

    raw_inputs = receipt.get("inputs")
    seen_inputs: set[str] = set()
    if not isinstance(raw_inputs, list) or not raw_inputs:
        check.fail("completion receipt inputs must be a non-empty list")
    else:
        for index, entry in enumerate(raw_inputs):
            label = f"completion receipt inputs[{index}]"
            if not isinstance(entry, dict):
                check.fail(f"{label} must be an object")
                continue
            raw_path = entry.get("path")
            digest = entry.get("sha256")
            size = entry.get("size")
            try:
                relative, path = completion.safe_problem_path(
                    gate.problem_dir,
                    raw_path,
                    label=f"{label}.path",
                    require_exists=True,
                )
            except completion.ContractError as exc:
                check.fail(str(exc))
                continue
            if relative == receipt_rel:
                check.fail("completion receipt must not hash-bind itself")
            if relative in seen_inputs:
                check.fail(f"completion receipt input path is duplicated: {relative}")
            seen_inputs.add(relative)
            if path.is_symlink() or not path.is_file():
                check.fail(f"completion input is no longer a regular file: {relative}")
                continue
            if not isinstance(digest, str) or completion.HASH_RE.fullmatch(digest) is None:
                check.fail(f"{label}.sha256 must be 64 lowercase hex digits")
                continue
            if type(size) is not int or size < 0:
                check.fail(f"{label}.size must be a non-negative integer")
                continue
            try:
                actual_size = path.stat().st_size
                actual_digest = completion.sha256_file(path)
            except OSError as exc:
                check.fail(f"cannot verify completion input {relative}: {exc}")
                continue
            if actual_size != size:
                check.fail(f"completion input size changed: {relative}")
            if actual_digest != digest:
                check.fail(f"completion input hash changed: {relative}")

    watched = receipt.get("watched_trees")
    seen_roots: set[str] = set()
    if not isinstance(watched, list) or not watched:
        check.fail("completion receipt watched_trees must be a non-empty list")
    else:
        for index, entry in enumerate(watched):
            label = f"completion receipt watched_trees[{index}]"
            if not isinstance(entry, dict):
                check.fail(f"{label} must be an object")
                continue
            root = entry.get("path")
            expected = entry.get("files")
            if not isinstance(root, str) or not root:
                check.fail(f"{label}.path must be a non-empty string")
                continue
            if root in seen_roots:
                check.fail(f"watched tree is duplicated: {root}")
            seen_roots.add(root)
            if not isinstance(expected, list) or not all(
                isinstance(value, str) for value in expected
            ):
                check.fail(f"{label}.files must be a string list")
                continue
            if expected != sorted(set(expected)):
                check.fail(f"{label}.files must be sorted and unique")
            actual = current_tree_files(gate.problem_dir, root, check)
            if actual != expected:
                check.fail(f"watched tree changed after completion: {root}")

    facts = receipt.get("facts")
    if not isinstance(facts, dict):
        check.fail("completion receipt facts must be an object")
    required_facts = {
        "preclassification",
        "data_buildability",
        "workflow_profile",
        "scam_status",
        "wrong_solution_min",
        "wrong_solution_max",
        "wrong_solutions_qualified",
        "adversarial_round_min",
        "adversarial_round_max",
        "adversarial_rounds_completed",
        "verified_full_solutions",
        "std_path",
        "std_sha256",
        "agent_model",
        "agent_reasoning_effort",
        "model_policy_status",
        "blind_time_limit_seconds",
        "stage_execution_receipts",
        "stage_runner_sha256",
        "regression_machine_sha256",
        "regression_executor_sha256",
        "adversarial_round_chain_sha256",
        "adversarial_round_receipt_hashes",
        "adversarial_round_verifier_sha256",
        "adversarial_round_recorder_sha256",
        "preclassification_validator_sha256",
        "std_materialization_mode",
        "selected_standard_route_path",
        "selected_standard_route_sha256",
        "selected_standard_route_kind",
        "std_provenance_path",
        "std_provenance_sha256",
        "coverage_matrix",
    }
    if isinstance(facts, dict):
        missing = sorted(required_facts - set(facts))
        if missing:
            check.fail(f"completion receipt facts are missing: {', '.join(missing)}")
        stage_runner_path = Path(completion.stage_runner.__file__).resolve()
        if facts.get("stage_runner_sha256") != completion.sha256_file(stage_runner_path):
            check.fail("stage runner changed after completion; rerun completion gate")
        regression_executor = Path(__file__).resolve().with_name(
            completion.REGRESSION_EXECUTOR_NAME
        )
        if (
            regression_executor.is_symlink()
            or not regression_executor.is_file()
            or facts.get("regression_executor_sha256")
            != completion.sha256_file(regression_executor)
        ):
            check.fail("regression executor changed after completion; rerun completion gate")
        round_verifier = Path(completion.adversarial_chain.__file__).resolve()
        round_recorder = round_verifier.with_name("record_adversarial_round.py")
        if facts.get("adversarial_round_verifier_sha256") != completion.sha256_file(
            round_verifier
        ):
            check.fail(
                "adversarial-round verifier changed after completion; rerun completion gate"
            )
        if facts.get("adversarial_round_recorder_sha256") != completion.sha256_file(
            round_recorder
        ):
            check.fail(
                "adversarial-round recorder changed after completion; rerun completion gate"
            )
        preclassification_validator = completion.PRECLASSIFICATION_VALIDATOR
        if facts.get("preclassification_validator_sha256") != completion.sha256_file(
            preclassification_validator
        ):
            check.fail(
                "preclassification validator changed after completion; rerun completion gate"
            )
        if facts.get("selected_standard_route_path") != completion.SELECTED_STANDARD_ROUTE_REL:
            check.fail("completion receipt selected standard route path is invalid")
        if facts.get("selected_standard_route_kind") not in {
            "verified-blind",
            "verified-simpler",
        }:
            check.fail("completion receipt selected standard route kind is invalid")
        if (
            facts.get("std_provenance_path")
            != facts.get("selected_standard_route_path")
            or facts.get("std_provenance_sha256")
            != facts.get("selected_standard_route_sha256")
        ):
            check.fail(
                "completion receipt std provenance does not match the selected "
                "standard route"
            )
        coverage = facts.get("coverage_matrix")
        if not isinstance(coverage, dict) or any(
            type(coverage.get(key)) is not int or coverage.get(key, 0) < 1
            for key in (
                "families",
                "obligations",
                "route_axes",
                "scale_axes",
                "release_inputs",
            )
        ):
            check.fail("completion receipt has no valid compact coverage summary")
        elif coverage.get("route_axes") != len(completion.REQUIRED_ROUTE_AXES):
            check.fail("completion receipt route-risk axis count is incomplete")
        elif not isinstance(coverage.get("sha256"), str) or completion.HASH_RE.fullmatch(
            coverage["sha256"]
        ) is None:
            check.fail("completion receipt coverage-matrix hash is invalid")
    if not check.issues:
        check.add(
            f"receipt and {len(seen_inputs)} hash-bound inputs remain unchanged"
        )
    return receipt


def require_readiness_int(
    check: completion.Check, readiness: dict[str, Any], key: str
) -> int | None:
    value = readiness.get(key)
    if type(value) is not int:
        check.fail(f"readiness.{key} must be an integer")
        return None
    return value


def check_current_stage_file(
    gate: Gate,
    check: completion.Check,
    raw: Any,
    *,
    label: str,
    require_nonempty: bool,
) -> str | None:
    if not isinstance(raw, dict):
        check.fail(f"{label} must be a file receipt object")
        return None
    try:
        relative, path = completion.safe_problem_path(
            gate.problem_dir,
            raw.get("path"),
            label=f"{label}.path",
            require_exists=False,
        )
    except completion.ContractError as exc:
        check.fail(str(exc))
        return None
    current = completion.stage_runner.file_state(path, relative)
    if raw != current:
        check.fail(f"{label} no longer matches current file {relative}")
        return None
    if require_nonempty and current.get("status") != "present-nonempty":
        check.fail(f"{label} is not a non-empty regular file: {relative}")
        return None
    return relative


def check_readiness_execution_receipt(gate: Gate) -> None:
    check = gate.new_check("readiness-execution-receipt")
    relative = "audit/private/stage-executions/readiness/current.json"
    path = gate.problem_dir / relative
    if path.is_symlink() or not path.is_file():
        check.fail(f"missing readiness production execution receipt: {relative}")
        return
    try:
        receipt = completion.load_json(path)
    except completion.ContractError as exc:
        check.fail(str(exc))
        return
    if not isinstance(receipt, dict):
        check.fail("readiness execution receipt must be a JSON object")
        return
    try:
        validated_summary = completion.stage_runner.require_prior_stage_receipt(
            gate.problem_dir, "readiness"
        )
    except (OSError, ValueError, completion.stage_runner.ContractError) as exc:
        check.fail(f"readiness recursive production receipt validation failed: {exc}")
        return
    if validated_summary != {
        "stage": "readiness",
        "path": relative,
        "sha256": completion.sha256_file(path),
    }:
        check.fail("readiness recursive receipt summary is inconsistent")
    expected = {
        "schema_version": 1,
        "runner": "icpc-light-stage-agent-runner",
        "stage": "readiness",
        "execution_mode": "production-codex",
        "model": completion.REQUIRED_AGENT_MODEL,
        "reasoning_effort": completion.REQUIRED_REASONING_EFFORT,
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
    for key, value in expected.items():
        if receipt.get(key) != value:
            check.fail(f"readiness receipt.{key} must be {value!r}")
    if not completion.exact_stage_command(receipt.get("command"), gate.problem_dir):
        check.fail("readiness receipt command is not exact production Codex execution")
    started = completion.parse_utc(receipt.get("started_at_utc"), "readiness start")
    finished = completion.parse_utc(receipt.get("finished_at_utc"), "readiness finish")
    if started is None or finished is None or finished < started:
        check.fail("readiness receipt has invalid timestamps")
    completion_path = gate.problem_dir / completion.DEFAULT_RECEIPT_REL
    try:
        completion_data = completion.load_json(completion_path)
    except completion.ContractError as exc:
        check.fail(str(exc))
        completion_data = None
    if isinstance(completion_data, dict) and started is not None:
        completed_at = completion.parse_utc(
            completion_data.get("generated_at_utc"), "completion generated time"
        )
        if completed_at is None or started < completed_at:
            check.fail("readiness agent started before completion gate finished")

    prompt = check_current_stage_file(
        gate, check, receipt.get("prompt"), label="readiness prompt", require_nonempty=True
    )
    if prompt is not None and not prompt.startswith("audit/private/"):
        check.fail("readiness prompt must stay under audit/private/")
    check_current_stage_file(
        gate,
        check,
        receipt.get("stdout_log"),
        label="readiness stdout log",
        require_nonempty=True,
    )
    check_current_stage_file(
        gate,
        check,
        receipt.get("stderr_log"),
        label="readiness stderr log",
        require_nonempty=False,
    )
    input_paths: set[str] = set()
    raw_inputs = receipt.get("inputs")
    if not isinstance(raw_inputs, list):
        check.fail("readiness receipt.inputs must be a list")
        raw_inputs = []
    for index, item in enumerate(raw_inputs):
        found = check_current_stage_file(
            gate,
            check,
            item,
            label=f"readiness inputs[{index}]",
            require_nonempty=True,
        )
        if found:
            input_paths.add(found)
    required_inputs = set(completion.stage_runner.STAGES["readiness"].inputs)
    if required_inputs - input_paths:
        check.fail(
            "readiness receipt omits required inputs: "
            + ", ".join(sorted(required_inputs - input_paths))
        )
    output_paths: set[str] = set()
    raw_outputs = receipt.get("outputs")
    if not isinstance(raw_outputs, list):
        check.fail("readiness receipt.outputs must be a list")
        raw_outputs = []
    for index, item in enumerate(raw_outputs):
        found = check_current_stage_file(
            gate,
            check,
            item,
            label=f"readiness outputs[{index}]",
            require_nonempty=True,
        )
        if found:
            output_paths.add(found)
    required_outputs = set(completion.stage_runner.STAGES["readiness"].outputs)
    if required_outputs - output_paths:
        check.fail("readiness receipt does not bind audit/readiness.md")
    if not check.issues:
        check.add("readiness was produced by a current exact-model production run")


def check_readiness(
    gate: Gate, receipt: dict[str, Any] | None, receipt_rel: str
) -> None:
    check = gate.new_check("readiness")
    path = gate.problem_dir / READINESS_REL
    if path.is_symlink() or not path.is_file():
        check.fail(f"readiness report is not a regular file: {path}")
        return
    try:
        readiness = completion.parse_front_matter(path)
    except completion.ContractError as exc:
        check.fail(str(exc))
        return
    completion.require_fields(
        check, readiness, READINESS_REQUIRED_FIELDS, "readiness front matter"
    )
    if readiness.get("schema_version") != 2:
        check.fail("readiness.schema_version must be integer 2")
    if readiness.get("verdict") != "go":
        check.fail("readiness.verdict must be exactly 'go'")
    if readiness.get("blind_gate") != "passed":
        check.fail("readiness.blind_gate must be 'passed'")
    if readiness.get("completion_gate") != "passed":
        check.fail("readiness.completion_gate must be 'passed'")
    if readiness.get("machine_regression") != "passed":
        check.fail("readiness.machine_regression must be 'passed'")
    if readiness.get("adversarial_round_chain") != "passed":
        check.fail("readiness.adversarial_round_chain must be 'passed'")
    if readiness.get("stage_execution_receipts") != "passed":
        check.fail("readiness.stage_execution_receipts must be 'passed'")
    if readiness.get("agent_model") != completion.REQUIRED_AGENT_MODEL:
        check.fail(
            f"readiness.agent_model must be {completion.REQUIRED_AGENT_MODEL!r}"
        )
    if readiness.get("agent_reasoning_effort") != completion.REQUIRED_REASONING_EFFORT:
        check.fail(
            "readiness.agent_reasoning_effort must be "
            f"{completion.REQUIRED_REASONING_EFFORT!r}"
        )
    if readiness.get("model_policy_status") != "enforced":
        check.fail("readiness.model_policy_status must be 'enforced'")
    if type(readiness.get("repair_used")) is not bool:
        check.fail("readiness.repair_used must be a YAML boolean")
    blockers = readiness.get("blockers")
    if not isinstance(blockers, list):
        check.fail("readiness.blockers must be a YAML list")
    elif blockers:
        check.fail("readiness go decision must have an empty blockers list")

    evidence = readiness.get("evidence")
    evidence_paths: set[str] = set()
    if not isinstance(evidence, list) or not evidence:
        check.fail("readiness.evidence must be a non-empty YAML list")
    else:
        for index, raw in enumerate(evidence):
            try:
                relative, evidence_path = completion.safe_problem_path(
                    gate.problem_dir,
                    raw,
                    label=f"readiness.evidence[{index}]",
                    require_exists=True,
                )
            except completion.ContractError as exc:
                check.fail(str(exc))
                continue
            evidence_paths.add(relative)
            if evidence_path.is_symlink() or not evidence_path.is_file():
                check.fail(f"readiness evidence is not a regular file: {relative}")
            elif evidence_path.stat().st_size == 0:
                check.fail(f"readiness evidence is empty: {relative}")
    for required in (
        "audit/regression.md",
        "audit/regression-machine.json",
        receipt_rel,
    ):
        if required not in evidence_paths:
            check.fail(f"readiness.evidence must cite {required}")

    if receipt is None or not isinstance(receipt.get("facts"), dict):
        check.fail("cannot compare readiness without a valid completion receipt")
        return
    facts = receipt["facts"]
    for key in (
        "preclassification",
        "workflow_profile",
        "scam_status",
        "agent_model",
        "agent_reasoning_effort",
        "model_policy_status",
        "std_materialization_mode",
    ):
        if readiness.get(key) != facts.get(key):
            check.fail(
                f"readiness.{key} does not match completion receipt: "
                f"{readiness.get(key)!r} != {facts.get(key)!r}"
            )

    verified = require_readiness_int(check, readiness, "verified_full_solutions")
    qualified = require_readiness_int(check, readiness, "wrong_solutions_qualified")
    required_min = require_readiness_int(
        check, readiness, "wrong_solutions_required_min"
    )
    rounds = require_readiness_int(check, readiness, "adversarial_rounds_completed")
    round_limit = require_readiness_int(check, readiness, "adversarial_round_limit")
    comparisons = (
        ("verified_full_solutions", verified, facts.get("verified_full_solutions")),
        ("wrong_solutions_qualified", qualified, facts.get("wrong_solutions_qualified")),
        ("wrong_solutions_required_min", required_min, facts.get("wrong_solution_min")),
        ("adversarial_rounds_completed", rounds, facts.get("adversarial_rounds_completed")),
        ("adversarial_round_limit", round_limit, facts.get("adversarial_round_max")),
    )
    for key, actual, expected in comparisons:
        if actual is not None and actual != expected:
            check.fail(f"readiness.{key} does not match completion receipt")
    if verified is not None and verified < 1:
        check.fail("readiness must record at least one verified full solution")
    grade_max = facts.get("wrong_solution_max")
    if (
        qualified is not None
        and required_min is not None
        and type(grade_max) is int
        and not required_min <= qualified <= grade_max
    ):
        check.fail("readiness wrong-solution count is outside the graded quota")
    round_min = facts.get("adversarial_round_min")
    if (
        rounds is not None
        and round_limit is not None
        and type(round_min) is int
        and not round_min <= rounds <= round_limit
    ):
        check.fail("readiness completed rounds are outside the graded range")
    if rounds is not None:
        for number in range(1, rounds + 1):
            required_round_receipt = (
                f"audit/adversarial-round-receipts/round-{number:02d}.json"
            )
            if required_round_receipt not in evidence_paths:
                check.fail(
                    "readiness.evidence must cite " + required_round_receipt
                )

    std_path = readiness.get("std_path")
    std_hash = readiness.get("std_sha256")
    if std_path != facts.get("std_path"):
        check.fail("readiness.std_path does not match completion receipt")
    if std_hash != facts.get("std_sha256"):
        check.fail("readiness.std_sha256 does not match completion receipt")
    if not check.issues:
        check.add(
            f"schema-v2 go; std={std_path}; qualified wrongs={qualified}; rounds={rounds}"
        )


def emit(gate: Gate, as_json: bool) -> None:
    if as_json:
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "gate": GATE_NAME,
                    "status": "pass" if gate.passed else "fail",
                    "problem_dir": str(gate.problem_dir),
                    "checks": [check.as_dict() for check in gate.checks],
                    "issues": gate.issues,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    stream = sys.stdout if gate.passed else sys.stderr
    print(
        "ICPC Light final readiness gate: "
        + ("PASS" if gate.passed else f"FAIL ({len(gate.issues)} issue(s))"),
        file=stream,
    )
    print(f"problem_dir: {gate.problem_dir}", file=stream)
    for check in gate.checks:
        print(f"[{check.status.upper()}] {check.check_id}", file=stream)
        for evidence in check.evidence:
            print(f"  evidence: {evidence}", file=stream)
        for issue in check.issues:
            print(f"  - {issue}", file=stream)


def main() -> int:
    args = parse_args()
    gate = Gate(args.problem_dir)
    receipt = check_receipt(gate, args.receipt_rel, args.receipt_path)
    check_readiness_execution_receipt(gate)
    check_readiness(gate, receipt, args.receipt_rel)
    emit(gate, args.json)
    return 0 if gate.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
