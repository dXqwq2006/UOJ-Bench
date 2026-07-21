#!/usr/bin/env python3
"""Block std materialization until the reviewed route is semantically complete."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import verify_completion as completion


CONTRACT_SECTIONS = (
    "Input Contract",
    "Output Contract",
    "Bounds and Aggregates",
    "Complexity Target",
    "Critical Boundaries",
    "Checker Choice",
    "Ambiguity Resolutions",
)

DRAFT_SECTIONS = (
    "Algorithm",
    "Correctness Proof",
    "Complexity",
    "Boundary and Integer-Width Audit",
    "Route Comparison",
    "Standard Route Adoption",
    "Unresolved Claims",
    "Oracle Domain",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Verify a frozen contract and independently reviewed full-solution "
            "draft before package/std.cpp may be materialized."
        )
    )
    parser.add_argument("--problem-dir", type=Path, required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if not args.problem_dir.is_dir() or args.problem_dir.is_symlink():
        parser.error("--problem-dir must be an existing non-symlink directory")
    args.problem_dir = args.problem_dir.resolve()
    return args


def _section_body(text: str, title: str) -> str | None:
    match = re.search(
        rf"(?ims)^##\s+{re.escape(title)}\s*$\s*(.+?)(?=^##\s|\Z)", text
    )
    return match.group(1).strip() if match else None


def _substantive(value: str | None) -> bool:
    if value is None or not completion.non_placeholder(value):
        return False
    normalized = "".join(character for character in value if character.isalnum()).lower()
    return len(normalized) >= 24 and normalized not in {
        "seestatement",
        "sameasstatement",
        "tobedetermined",
    }


def _require_sections(
    check: completion.Check, path: Path, titles: tuple[str, ...], label: str
) -> None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        check.fail(f"cannot read {label}: {exc}")
        return
    for title in titles:
        body = _section_body(text, title)
        if not _substantive(body):
            check.fail(f"{label} requires a substantive '## {title}' section")


def check_draft_semantics(
    report: completion.Report,
    verified_claims: list[dict[str, str]],
    selected_route: Path | None,
) -> dict[str, Any] | None:
    check = report.new_check("solution-draft-semantic-handoff")
    contract_path = report.problem_dir / "audit/contract.md"
    draft_path = report.problem_dir / "audit/solution-review-draft.md"
    if not completion.require_file(
        report, check, contract_path, "frozen solution contract"
    ):
        return None
    if not completion.require_file(
        report, check, draft_path, "solution review draft"
    ):
        return None
    try:
        draft = completion.parse_front_matter(draft_path)
    except completion.ContractError as exc:
        check.fail(str(exc))
        return None
    completion.require_fields(
        check,
        draft,
        completion.DRAFT_REVIEW_REQUIRED_FIELDS,
        "solution draft",
    )
    expected = {
        "schema_version": 1,
        "agent_model": completion.REQUIRED_AGENT_MODEL,
        "agent_reasoning_effort": completion.REQUIRED_REASONING_EFFORT,
        "review_status": "passed",
    }
    for field, value in expected.items():
        if draft.get(field) != value:
            check.fail(f"solution draft.{field} must be {value!r}")
    source = {
        "source_path": draft.get("blind_source_path"),
        "source_sha256": draft.get("blind_source_sha256"),
    }
    if source not in verified_claims:
        check.fail("solution draft must bind one current active verified blind source")
    if selected_route is None:
        check.fail("selected standard route did not pass its provenance gate")
    _require_sections(check, contract_path, CONTRACT_SECTIONS, "audit/contract.md")
    _require_sections(
        check, draft_path, DRAFT_SECTIONS, "audit/solution-review-draft.md"
    )
    if not check.issues:
        check.add(
            "frozen contract and reviewed algorithm/proof/complexity/boundary "
            "evidence are complete and bind an active blind source"
        )
        return draft
    return None


def build_report(problem_dir: Path) -> completion.Report:
    report = completion.Report(problem_dir, "", False)
    completion.check_blind_stage(report)
    completion.check_stage_execution_receipts(
        report, ("preclassification", "solution-draft")
    )
    completion.check_run_state_policy(report)
    grade = completion.check_grade(report)
    verified = completion.check_verified_claims(report)
    selected = completion.check_selected_standard_route(report, grade, verified)
    check_draft_semantics(report, verified, selected)
    return report


def main() -> int:
    args = parse_args()
    report = build_report(args.problem_dir)
    payload = {
        "schema_version": 1,
        "gate": "icpc-light-solution-draft-handoff",
        "status": "pass" if report.passed else "fail",
        "problem_dir": str(args.problem_dir),
        "issues": report.issues,
        "checks": [check.as_dict() for check in report.checks],
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        stream = sys.stdout if report.passed else sys.stderr
        print(
            "ICPC Light solution draft handoff: "
            + ("PASS" if report.passed else f"FAIL ({len(report.issues)} issue(s))"),
            file=stream,
        )
        for issue in report.issues:
            print(f"- {issue}", file=stream)
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
