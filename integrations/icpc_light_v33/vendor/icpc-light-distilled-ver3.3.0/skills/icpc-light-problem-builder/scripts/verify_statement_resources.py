#!/usr/bin/env python3
"""Fail closed unless statement.md declares supported explicit TL and ML."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from statement_resources import StatementResourceError, load_statement_resources


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--problem-dir", type=Path, required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if not args.problem_dir.is_dir() or args.problem_dir.is_symlink():
        parser.error("--problem-dir must be an existing non-symlink directory")
    args.problem_dir = args.problem_dir.resolve()
    return args


def main() -> int:
    args = parse_args()
    try:
        resources = load_statement_resources(args.problem_dir)
    except StatementResourceError as exc:
        if args.json:
            print(
                json.dumps(
                    {"schema_version": 1, "status": "failed", "error": str(exc)},
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            print(f"statement resource preflight: FAIL: {exc}", file=sys.stderr)
        return 1

    payload = {"status": "passed", **resources.as_dict()}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(
            "statement resource preflight: PASS "
            f"(time={resources.time_limit_ms}ms, "
            f"memory={resources.memory_limit_mib}MiB)"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
