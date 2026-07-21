#!/usr/bin/env python3
"""Refresh canonical completion evidence before readiness dispatch."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import verify_completion as completion
import verify_readiness as readiness


COMPLETION_REPLAY_TIMEOUT_SECONDS = 4 * 60 * 60
MAX_REPORTED_ISSUES = 8
MAX_REPORTED_ISSUE_CHARACTERS = 1200


def bounded_reported_issues(payload: Any) -> list[str]:
    """Retain useful verifier diagnostics without copying unbounded output."""

    if not isinstance(payload, dict) or not isinstance(payload.get("issues"), list):
        return []
    issues: list[str] = []
    for raw in payload["issues"]:
        if not isinstance(raw, str) or not raw.strip():
            continue
        issues.append(raw.strip()[:MAX_REPORTED_ISSUE_CHARACTERS])
        if len(issues) == MAX_REPORTED_ISSUES:
            break
    return issues


def replay_completion(problem_dir: Path) -> dict[str, Any]:
    """Run the canonical completion gate on the trusted handoff path.

    A structurally plausible JSON receipt is not evidence that the completion
    verifier ran.  The readiness transition therefore refreshes that receipt
    itself before allowing the readiness agent to start.
    """

    verifier = Path(completion.__file__).resolve()
    command = [
        sys.executable,
        str(verifier),
        "--problem-dir",
        str(problem_dir),
        "--json",
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=problem_dir,
            capture_output=True,
            text=True,
            timeout=COMPLETION_REPLAY_TIMEOUT_SECONDS,
            check=False,
        )
        timed_out = False
        stdout = completed.stdout
        stderr = completed.stderr
        exit_code: int | None = completed.returncode
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        exit_code = None
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        payload = None
    reported_issues = bounded_reported_issues(payload)
    return {
        "command": [sys.executable, verifier.name, "--problem-dir", ".", "--json"],
        "verifier_sha256": completion.sha256_file(verifier),
        "timeout_seconds": COMPLETION_REPLAY_TIMEOUT_SECONDS,
        "timed_out": timed_out,
        "exit_code": exit_code,
        "reported_status": (
            payload.get("status") if isinstance(payload, dict) else None
        ),
        "receipt_sha256": (
            payload.get("receipt_sha256") if isinstance(payload, dict) else None
        ),
        "reported_issues": reported_issues,
        "stdout_sha256": hashlib.sha256(stdout.encode()).hexdigest(),
        "stderr_sha256": hashlib.sha256(stderr.encode()).hexdigest(),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run canonical completion, refresh audit/completion-gate.json, and "
            "verify it before the independent readiness agent may start."
        )
    )
    parser.add_argument("--problem-dir", type=Path, required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if not args.problem_dir.is_dir() or args.problem_dir.is_symlink():
        parser.error("--problem-dir must be an existing non-symlink directory")
    args.problem_dir = args.problem_dir.resolve()
    return args


def main() -> int:
    args = parse_args()
    gate = readiness.Gate(args.problem_dir)
    replay_check = gate.new_check("canonical-completion-replay")
    replay = replay_completion(args.problem_dir)
    if (
        replay.get("timed_out") is not False
        or replay.get("exit_code") != 0
        or replay.get("reported_status") != "pass"
    ):
        reported_issues = replay.get("reported_issues")
        first_issue = (
            reported_issues[0]
            if isinstance(reported_issues, list) and reported_issues
            else None
        )
        replay_check.fail(
            "canonical completion replay did not pass; refusing readiness dispatch"
            + (f"; first verifier issue: {first_issue}" if first_issue else "")
        )
    else:
        replay_check.add("canonical completion verifier refreshed the receipt")
    relative = completion.DEFAULT_RECEIPT_REL
    receipt = readiness.check_receipt(
        gate, relative, args.problem_dir / relative
    )
    if isinstance(receipt, dict):
        receipt_path = args.problem_dir / relative
        try:
            receipt_sha256 = completion.sha256_file(receipt_path)
        except OSError as exc:
            replay_check.fail(f"cannot hash refreshed completion receipt: {exc}")
        else:
            if replay.get("receipt_sha256") != receipt_sha256:
                replay_check.fail(
                    "canonical replay output does not bind the refreshed receipt"
                )
    passed = gate.passed and receipt is not None
    payload = {
        "schema_version": 1,
        "gate": "icpc-light-completion-handoff",
        "status": "pass" if passed else "fail",
        "problem_dir": str(args.problem_dir),
        "completion_receipt": relative if passed else None,
        "completion_replay": replay,
        "issues": gate.issues,
        "checks": [check.as_dict() for check in gate.checks],
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        stream = sys.stdout if passed else sys.stderr
        print(
            "ICPC Light completion handoff: "
            + ("PASS" if passed else f"FAIL ({len(gate.issues)} issue(s))"),
            file=stream,
        )
        for issue in gate.issues:
            print(f"- {issue}", file=stream)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
