#!/usr/bin/env python3
"""Print a hash-bound private regression resource policy.

The command is intentionally read-only.  It derives TL/ML exclusively from the
current statement and accepts only the four human design explanations that the
build agent must provide.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from statement_resources import StatementResourceError, load_statement_resources


RESOURCE_POLICY_SCHEMA_VERSION = 1


def nonempty(value: str, label: str) -> str:
    normalized = value.strip()
    if not normalized or "\x00" in normalized:
        raise ValueError(f"{label} must be non-empty")
    return normalized


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def build_policy(args: argparse.Namespace) -> dict[str, Any]:
    resources = load_statement_resources(args.problem_dir)
    design_basis = {
        "intended_complexity": nonempty(
            args.intended_complexity, "--intended-complexity"
        ),
        "maximum_scale": nonempty(args.maximum_scale, "--maximum-scale"),
        "time_limit_rationale": nonempty(
            args.time_limit_rationale, "--time-limit-rationale"
        ),
        "memory_limit_rationale": nonempty(
            args.memory_limit_rationale, "--memory-limit-rationale"
        ),
    }
    payload = {
        "schema_version": RESOURCE_POLICY_SCHEMA_VERSION,
        "statement_resources": resources.as_dict(),
        "design_basis": design_basis,
    }
    return {
        **payload,
        "policy_sha256": hashlib.sha256(canonical_json_bytes(payload)).hexdigest(),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Print the private regression resource_policy JSON object."
    )
    parser.add_argument("--problem-dir", type=Path, required=True)
    parser.add_argument("--intended-complexity", required=True)
    parser.add_argument("--maximum-scale", required=True)
    parser.add_argument("--time-limit-rationale", required=True)
    parser.add_argument("--memory-limit-rationale", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        policy = build_policy(args)
    except (StatementResourceError, ValueError) as exc:
        print(f"resource policy: FAIL: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(policy, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
