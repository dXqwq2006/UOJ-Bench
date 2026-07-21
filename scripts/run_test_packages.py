"""Generate and judge statement-only ordered test packages."""

from __future__ import annotations

from pathlib import Path
import argparse
import json
import tempfile

from utils.test_package_benchmark import (
    bind_package_contract,
    package_metrics,
    package_progress,
    run_solver_packages,
    sync_generation_package,
    sync_jury_executions,
)
from utils.testcase_eval_benchmark import RunStore


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--phase",
        choices=("generate", "sync-native", "judge", "stats"),
        required=True,
    )
    parser.add_argument("--result-dir", type=Path, required=True)
    parser.add_argument("--benchmark", choices=("testcase-eval", "codecontests-plus"))
    parser.add_argument("--policy", default="icpc_light_v33_bridge")
    parser.add_argument("--model", default="gpt-5.6-sol")
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--native-calls", type=int, default=20)
    parser.add_argument("--retry-errors", action="store_true")
    parser.add_argument("--lightcp-url", default="http://127.0.0.1:8082")
    return parser.parse_args()


def _benchmark(store: RunStore, requested: str | None) -> str:
    manifest = store.manifest()
    detected = (
        "codecontests-plus"
        if str(manifest.get("benchmark", "")).startswith("codecontests-plus")
        else "testcase-eval"
        if "testcase_eval_upstream_commit" in manifest
        else None
    )
    if detected is None:
        raise RuntimeError("result database is not a prepared TCE or CC+ run")
    if requested is not None and requested != detected:
        raise ValueError(f"requested {requested}, database contains {detected}")
    return detected


def _write_summary(path: Path, summary: dict[str, object]) -> None:
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as stream:
        json.dump(summary, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
        temporary = Path(stream.name)
    temporary.replace(path)


def main() -> None:
    args = _arguments()
    result_dir = args.result_dir.resolve()
    database = result_dir / "results.sqlite3"
    if not database.is_file():
        raise FileNotFoundError(
            f"{database} is missing; prepare the dataset with its native runner first"
        )

    with RunStore(database) as store:
        benchmark = _benchmark(store, args.benchmark)
        if args.phase == "generate":
            result = run_solver_packages(
                store,
                policy=args.policy,
                model=args.model,
                dataset=benchmark,
                fidelity="adapted",
                call_contract="full ICPC Light v3.3 package workflow",
                workers=args.workers,
                retry_errors=args.retry_errors,
                deltas=(
                    "upstream release requires ultra; published benchmark port is xhigh",
                    "final release_tests capped at 50",
                    "benchmark jury remains hidden from the package workspace",
                ),
            )
        elif args.phase == "sync-native":
            fidelity = (
                "native"
                if benchmark == "testcase-eval"
                and args.policy == "testcase_eval_task1_cot"
                else "adapted"
            )
            bind_package_contract(
                store,
                policy=args.policy,
                dataset=benchmark,
                fidelity=fidelity,
                call_contract=f"{args.native_calls} independent one-test calls",
                max_tests=args.native_calls,
                deltas=(
                    ()
                    if fidelity == "native"
                    else ("method applied to a non-native benchmark dataset",)
                ),
            )
            result = sync_generation_package(
                store,
                policy=args.policy,
                fidelity=fidelity,
                expected_calls=args.native_calls,
            )
        elif args.phase == "judge":
            if benchmark == "testcase-eval":
                from utils.testcase_eval_lightcp import run_judge

                detail = run_judge(
                    database, base_url=args.lightcp_url, workers=args.workers
                )
            else:
                from utils.codecontests_plus import run_judge

                detail = run_judge(
                    store, base_url=args.lightcp_url, workers=args.workers
                )
            result = {
                "judge": detail,
                "jury_executions": sync_jury_executions(store, args.policy),
            }
        else:
            sync_jury_executions(store, args.policy)
            if benchmark == "testcase-eval":
                from utils.testcase_eval_benchmark import score
            else:
                from utils.codecontests_plus import score
            summary = score(store)
            summary["test_package"] = {
                "progress": package_progress(store, args.policy),
                "metrics": package_metrics(
                    store, dataset=benchmark, policy=args.policy
                ),
            }
            _write_summary(result_dir / "summary.json", summary)
            result = summary["test_package"]
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
