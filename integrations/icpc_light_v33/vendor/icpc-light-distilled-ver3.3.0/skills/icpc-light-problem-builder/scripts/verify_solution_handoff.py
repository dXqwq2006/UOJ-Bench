#!/usr/bin/env python3
"""Block hardening until the concrete std passes its independent source gate.

This gate compiles and verifies source/proof provenance.  It deliberately does
not accept Markdown as sample or tiny-differential execution evidence; those
fields remain ``pending-machine-regression`` until build-hardening creates the
oracle, samples, generator, and canonical machine receipt.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import verify_completion as completion


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify ordered stage receipts, reviewed std provenance, and actual std compilation before hardening."
    )
    parser.add_argument("--problem-dir", type=Path, required=True)
    parser.add_argument(
        "--lightcpverifier-url",
        default=os.environ.get(
            "ICPC_LIGHT_LIGHTCPVERIFIER_URL", "http://127.0.0.1:8081"
        ),
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if not args.problem_dir.is_dir() or args.problem_dir.is_symlink():
        parser.error("--problem-dir must be an existing non-symlink directory")
    args.problem_dir = args.problem_dir.resolve()
    return args


def main() -> int:
    args = parse_args()
    report = completion.Report(args.problem_dir, "", False)
    completion.check_blind_stage(report)
    completion.check_stage_execution_receipts(
        report,
        (
            "preclassification",
            "solution-draft",
            "std-materialization",
            "solution-validation",
        ),
    )
    completion.check_run_state_policy(report)
    grade = completion.check_grade(report)
    files = report.new_check("solution-handoff-files")
    for relative in (
        "audit/contract.md",
        "audit/solution-review-draft.md",
        "audit/std-materialization.md",
        "audit/solution-review.md",
        "package/std.cpp",
    ):
        completion.require_file(
            report, files, args.problem_dir / relative, relative
        )
    verified = completion.check_verified_claims(report)
    selected = completion.check_selected_standard_route(report, grade, verified)
    mode = completion.check_solution_draft_and_materialization(
        report, verified, selected
    )
    std = completion.check_solution_provenance(report, mode, selected)

    compile_check = report.new_check("concrete-std-compilation")
    if std is None or not std.is_file():
        compile_check.fail("reviewed package/std.cpp is unavailable")
    else:
        completion.check_lightcp_compile_only(
            compile_check,
            problem_dir=args.problem_dir,
            source=std,
            role="std",
            lightcpverifier_url=args.lightcpverifier_url,
        )

    payload = {
        "schema_version": 1,
        "gate": "icpc-light-concrete-std-handoff",
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
            "ICPC Light concrete std handoff: "
            + ("PASS" if report.passed else f"FAIL ({len(report.issues)} issue(s))"),
            file=stream,
        )
        for issue in report.issues:
            print(f"- {issue}", file=stream)
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
