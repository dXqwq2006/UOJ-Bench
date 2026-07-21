#!/usr/bin/env python3
"""Block concrete-source review until std materialization is real and compilable."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import verify_completion as completion
import verify_solution_draft_handoff as draft_gate


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Verify reviewed draft provenance, exact-copy/adapted materialization, "
            "current package/std.cpp hash, and compilation before source review."
        )
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


def build_report(
    problem_dir: Path,
    lightcpverifier_url: str = "http://127.0.0.1:8081",
) -> completion.Report:
    report = completion.Report(problem_dir, "", False)
    completion.check_blind_stage(report)
    completion.check_stage_execution_receipts(
        report,
        ("preclassification", "solution-draft", "std-materialization"),
    )
    completion.check_run_state_policy(report)
    grade = completion.check_grade(report)
    verified = completion.check_verified_claims(report)
    selected = completion.check_selected_standard_route(report, grade, verified)
    draft_gate.check_draft_semantics(report, verified, selected)
    mode = completion.check_solution_draft_and_materialization(
        report, verified, selected
    )

    detail_check = report.new_check("materialization-semantic-detail")
    if mode == "adapted":
        material_path = problem_dir / "audit/std-materialization.md"
        try:
            text = material_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            detail_check.fail(f"cannot read materialization detail: {exc}")
        else:
            delta = draft_gate._section_body(text, "Semantic Deltas")
            if not draft_gate._substantive(delta):
                detail_check.fail(
                    "adapted std requires a substantive '## Semantic Deltas' section"
                )
            else:
                detail_check.add("adapted-source semantic deltas are concrete")
    elif mode == "exact-copy":
        detail_check.add("exact-copy hash equality requires no semantic delta")
    else:
        detail_check.fail("materialization mode/provenance did not pass")

    compile_check = report.new_check("materialized-std-compilation")
    std = problem_dir / "package/std.cpp"
    if mode is None:
        compile_check.fail("materialization provenance gate did not pass")
    elif not std.is_file() or std.is_symlink():
        compile_check.fail("package/std.cpp is unavailable or unsafe")
    else:
        completion.check_lightcp_compile_only(
            compile_check,
            problem_dir=problem_dir,
            source=std,
            role="std",
            lightcpverifier_url=lightcpverifier_url,
        )
    return report


def main() -> int:
    args = parse_args()
    report = build_report(args.problem_dir, args.lightcpverifier_url)
    payload = {
        "schema_version": 1,
        "gate": "icpc-light-std-materialization-handoff",
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
            "ICPC Light std materialization handoff: "
            + ("PASS" if report.passed else f"FAIL ({len(report.issues)} issue(s))"),
            file=stream,
        )
        for issue in report.issues:
            print(f"- {issue}", file=stream)
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
