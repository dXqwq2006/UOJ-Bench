#!/usr/bin/env python3
"""Test-only workers injected into the real v3.3 blind-sweep scripts."""

from __future__ import annotations

import argparse
import os
from pathlib import Path


GENERATION_SOURCE = r'''#include <bits/stdc++.h>
using namespace std;
int main() {
    ios::sync_with_stdio(false);
    cin.tie(nullptr);
    long long a, b;
    if (!(cin >> a >> b)) return 0;
    cout << a + b << '\n';
    return 0;
}
'''


def _no_secrets() -> None:
    for name in ("UOJ_API_KEY", "TATU_API_KEY", "OPENAI_API_KEY"):
        if name in os.environ:
            raise RuntimeError(f"secret-like environment crossed into fixture worker: {name}")


def _blind_lane(args: argparse.Namespace) -> None:
    if not (Path("public") / "statement.md").is_file():
        raise RuntimeError("blind lane did not receive statement.md")
    if args.kind == "neutral":
        Path("main.cpp").write_text(GENERATION_SOURCE, encoding="utf-8")
    Path("final-status.md").write_text(
        "\n".join(
            (
                f"lane_id: {args.lane_id}",
                "contamination_status: clean",
                f"kind: {args.kind}",
                "claimed_verdict: complete" if args.kind == "neutral" else "claimed_verdict: attacked",
                "evidence: deterministic test-only fixture",
                "",
            )
        ),
        encoding="utf-8",
    )


def _review(args: argparse.Namespace) -> None:
    candidate = Path("candidate") / "main.cpp"
    public = Path("public") / "statement.md"
    if not candidate.is_file() or not public.is_file():
        raise RuntimeError("review workspace is incomplete")
    Path("review-report.md").write_text(
        "\n".join(
            (
                f"review_id: {args.review_id}",
                f"reviewer_id: {args.reviewer_id}",
                f"attempt_id: {args.attempt_id}",
                f"source_sha256: {args.source_sha256}",
                "contamination_status: clean",
                "status: verified",
                "compilation: passed",
                "public_samples: not-available",
                "contract_review: passed",
                "proof_review: passed",
                "complexity_review: passed",
                "tiny_oracle: passed",
                "tiny_oracle_reason: deterministic sum fixture checked exhaustively",
                "commands_run: fixture compile and exhaustive integer-pair comparison",
                "",
            )
        ),
        encoding="utf-8",
    )


def _hack() -> None:
    public = Path("public")
    for name in ("task.json", "statement.md", "wrong-source.txt"):
        if not (public / name).is_file():
            raise RuntimeError(f"Hacking task slice is missing {name}")
    output = Path("output")
    output.mkdir()
    (output / "candidate.in").write_text("2 3\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("blind-lane", "review", "hack"), required=True)
    parser.add_argument("--kind", choices=("neutral", "deceptive"))
    parser.add_argument("--lane-id")
    parser.add_argument("--review-id")
    parser.add_argument("--reviewer-id")
    parser.add_argument("--attempt-id")
    parser.add_argument("--source-sha256")
    args = parser.parse_args()
    _no_secrets()
    if args.mode == "blind-lane":
        if not args.kind or not args.lane_id:
            parser.error("blind-lane requires --kind and --lane-id")
        _blind_lane(args)
    elif args.mode == "review":
        if not all(
            (args.review_id, args.reviewer_id, args.attempt_id, args.source_sha256)
        ):
            parser.error("review requires all review identity arguments")
        _review(args)
    else:
        _hack()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
