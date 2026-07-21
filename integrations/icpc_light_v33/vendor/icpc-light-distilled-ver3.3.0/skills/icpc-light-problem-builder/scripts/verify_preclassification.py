#!/usr/bin/env python3
"""Verify a continuing, escalation, or terminal preclassification transition."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import verify_completion as completion


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify the blind prerequisite, production grader receipt, selected route, and schema-v2 transition."
    )
    parser.add_argument("--problem-dir", type=Path, required=True)
    parser.add_argument(
        "--require-continuing",
        action="store_true",
        help=(
            "Require a non-provisional continuing transition before a downstream "
            "solution stage may launch."
        ),
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if not args.problem_dir.is_dir() or args.problem_dir.is_symlink():
        parser.error("--problem-dir must be an existing non-symlink directory")
    args.problem_dir = args.problem_dir.resolve()
    return args


def build_report(
    problem_dir: Path, *, require_continuing: bool
) -> tuple[completion.Report, dict[str, Any] | None]:
    report = completion.Report(problem_dir, "", False)
    completion.check_blind_stage(report)
    completion.check_stage_execution_receipts(report, ("preclassification",))
    completion.check_run_state_policy(report)
    grade = completion.check_grade(
        report, require_continuing=require_continuing
    )
    verified = completion.check_verified_claims(report)
    completion.check_selected_standard_route(report, grade, verified)
    return report, grade


def main() -> int:
    args = parse_args()
    report, grade = build_report(
        args.problem_dir, require_continuing=args.require_continuing
    )
    payload = {
        "schema_version": 1,
        "gate": "icpc-light-preclassification-handoff",
        "status": "pass" if report.passed else "fail",
        "problem_dir": str(args.problem_dir),
        "preclassification": grade.get("preclassification") if grade else None,
        "workflow_profile": grade.get("workflow_profile") if grade else None,
        "scam_status": grade.get("scam_status") if grade else None,
        "decision": grade.get("decision") if grade else None,
        "provisional": grade.get("provisional") if grade else None,
        "stop_reason": grade.get("stop_reason") if grade else None,
        "require_continuing": args.require_continuing,
        "issues": report.issues,
        "checks": [check.as_dict() for check in report.checks],
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        stream = sys.stdout if report.passed else sys.stderr
        print(
            "ICPC Light preclassification handoff: "
            + ("PASS" if report.passed else f"FAIL ({len(report.issues)} issue(s))"),
            file=stream,
        )
        for issue in report.issues:
            print(f"- {issue}", file=stream)
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
