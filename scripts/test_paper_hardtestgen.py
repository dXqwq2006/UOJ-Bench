"""Run the HardTestGen paper pipeline on a prepared TCE or CC+ result DB."""

from __future__ import annotations

from pathlib import Path
import argparse
import json
import os

from solution.hardtestgen.lightcp import HardTestGenLightCP
from utils.hardtestgen_benchmark import (
    DEFAULT_PROJECTION_BUDGET,
    benchmark_kind,
    generate_kits,
    generate_suites,
    pipeline_summary,
)
from utils.testcase_eval_benchmark import RunStore


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--phase",
        required=True,
        choices=("preflight", "generate-kits", "generate-suites", "judge", "stats"),
    )
    parser.add_argument("--result-dir", type=Path, required=True)
    parser.add_argument("--model", default="gpt-5.6-sol")
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--projection-budget", type=int, default=DEFAULT_PROJECTION_BUDGET)
    parser.add_argument("--retry-errors", action="store_true")
    parser.add_argument("--github-settings", action="store_true")
    parser.add_argument(
        "--lightcp-url",
        default=os.environ.get("HARDTESTGEN_LIGHTCP_URL", "http://127.0.0.1:8082"),
    )
    return parser.parse_args()


def _print(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _require_github_settings() -> None:
    if os.environ.get("TATU_TEMPERATURE") != "0.1":
        raise ValueError("GitHub settings require TATU_TEMPERATURE=0.1")
    if os.environ.get("TATU_MAX_OUTPUT_TOKENS") != "5120":
        raise ValueError("GitHub settings require TATU_MAX_OUTPUT_TOKENS=5120")


def main() -> None:
    args = _arguments()
    args.result_dir = args.result_dir.resolve()
    database = args.result_dir / "results.sqlite3"
    if not database.is_file():
        raise FileNotFoundError(
            f"{database} is missing; prepare it with run_testcase_eval_batch or "
            "run_codecontests_plus first"
        )
    with RunStore(database) as store:
        benchmark = benchmark_kind(store)
        executor = HardTestGenLightCP(args.lightcp_url, benchmark)
        if args.phase == "preflight":
            health = executor.preflight()
            _print({"benchmark": benchmark, "profile": executor.profile, "ok": health["ok"]})
        elif args.phase == "generate-kits":
            if args.github_settings:
                _require_github_settings()
            _print(
                generate_kits(
                    store,
                    model=args.model,
                    workers=args.workers,
                    projection_budget=args.projection_budget,
                    retry_errors=args.retry_errors,
                )
            )
        elif args.phase == "generate-suites":
            _print(
                generate_suites(
                    store,
                    executor=executor,
                    workers=args.workers,
                    projection_budget=args.projection_budget,
                    retry_errors=args.retry_errors,
                )
            )
        elif args.phase == "judge":
            if benchmark == "testcase-eval":
                from utils.testcase_eval_lightcp import run_judge

                _print(run_judge(database, base_url=args.lightcp_url, workers=args.workers))
            else:
                from utils.codecontests_plus import run_judge

                _print(run_judge(store, base_url=args.lightcp_url, workers=args.workers))
        else:
            detail = pipeline_summary(store)
            if benchmark == "testcase-eval":
                from utils.testcase_eval_benchmark import write_summary
            else:
                from utils.codecontests_plus import write_summary

            score = write_summary(store, args.result_dir / "summary.json")
            _print({"pipeline": detail, "benchmark_complete": score["complete"]})


if __name__ == "__main__":
    main()
