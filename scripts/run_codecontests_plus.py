"""Run the pinned CodeContests+ Verified fault-coverage benchmark."""

from __future__ import annotations

from pathlib import Path
import argparse
import json
import os

from scripts.run_testcase_eval_batch import _preflight as model_preflight
from utils.codecontests_plus import (
    DEFAULT_POLICY,
    RunStore,
    audit_programs,
    export_jsonl,
    generate,
    prepare_dataset,
    run_judge,
    write_summary,
)
from utils.testcase_eval_benchmark import require_paper_generation_settings


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--phase",
        required=True,
        choices=("prepare", "preflight", "audit", "generate", "judge", "stats", "export"),
    )
    parser.add_argument("--result-dir", type=Path, required=True)
    parser.add_argument("--model", default="gpt-5.6-sol")
    parser.add_argument("--policy", default=DEFAULT_POLICY)
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--problem-id", action="append", default=[])
    parser.add_argument("--smoke-problems", type=int)
    parser.add_argument("--sample-problems", type=int)
    parser.add_argument("--sample-seed", default="codecontests-plus-verified-v1")
    parser.add_argument("--dataset-cache")
    parser.add_argument("--dataset-parquet", type=Path, action="append", default=[])
    parser.add_argument("--max-generations-per-problem", type=int)
    parser.add_argument("--retry-errors", action="store_true")
    parser.add_argument("--paper", action="store_true")
    parser.add_argument(
        "--lightcp-url",
        default=os.environ.get("CCPLUS_LIGHTCP_URL", "http://127.0.0.1:8082"),
    )
    return parser.parse_args()


def _print(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def main() -> None:
    args = _arguments()
    args.result_dir = args.result_dir.resolve()
    args.result_dir.mkdir(parents=True, exist_ok=True)
    database = args.result_dir / "results.sqlite3"

    if args.phase == "preflight":
        _print(model_preflight(args.model, args.paper, (args.policy,)))
        return

    with RunStore(database) as store:
        if args.phase == "prepare":
            _print(
                prepare_dataset(
                    store,
                    cache_dir=args.dataset_cache,
                    dataset_parquets=args.dataset_parquet,
                    problem_ids=args.problem_id,
                    smoke_problems=args.smoke_problems,
                    sample_problems=args.sample_problems,
                    sample_seed=args.sample_seed,
                )
            )
        elif args.phase == "audit":
            _print(
                audit_programs(
                    store,
                    base_url=args.lightcp_url,
                    workers=args.workers,
                )
            )
        elif args.phase == "generate":
            if args.paper:
                require_paper_generation_settings(args.model)
            _print(
                generate(
                    store,
                    model=args.model,
                    policy=args.policy,
                    workers=args.workers,
                    max_generations_per_problem=args.max_generations_per_problem,
                    retry_errors=args.retry_errors,
                )
            )
        elif args.phase == "judge":
            _print(
                run_judge(
                    store,
                    base_url=args.lightcp_url,
                    workers=args.workers,
                )
            )
        elif args.phase == "stats":
            summary = write_summary(store, args.result_dir / "summary.json")
            _print(
                {
                    "complete": summary["complete"],
                    "macro": summary["macro"],
                    "summary": str(args.result_dir / "summary.json"),
                }
            )
        else:
            _print(export_jsonl(store, args.result_dir / "tests"))


if __name__ == "__main__":
    main()
