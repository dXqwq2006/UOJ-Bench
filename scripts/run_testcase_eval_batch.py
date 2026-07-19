"""Run the pinned TestCase-Eval reproduction and UOJ-prompt control."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence
import argparse
import json
import os
import subprocess
import sys

from utils.testcase_eval_benchmark import (
    PAPER_GENERATIONS,
    RunStore,
    generate,
    prepare_dataset,
    require_paper_generation_settings,
    write_summary,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_IMAGE = "uoj-bench-testcase-eval:45275c6"


def _arguments(default_tasks: Sequence[int] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--phase",
        required=True,
        choices=("prepare", "preflight", "generate", "judge", "stats"),
    )
    parser.add_argument("--result-dir", type=Path, required=True)
    parser.add_argument("--model", default="gpt-5.6-sol")
    parser.add_argument(
        "--policy",
        action="append",
        choices=("testcase_eval", "prompt"),
        dest="policies",
    )
    parser.add_argument("--task", type=int, action="append", choices=(1, 2))
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument(
        "--task1-generations",
        type=int,
        default=PAPER_GENERATIONS[1],
    )
    parser.add_argument("--smoke-problems", type=int)
    parser.add_argument("--problem-id", action="append", default=[])
    parser.add_argument("--dataset-cache")
    parser.add_argument("--retry-errors", action="store_true")
    parser.add_argument("--paper", action="store_true")
    parser.add_argument("--no-verify-prompts", action="store_true")
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--inside-container", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    args.tasks = sorted(set(args.task or default_tasks or (1, 2)))
    args.policies = sorted(set(args.policies or ("testcase_eval",)))
    return args


def _print(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _preflight(model: str, paper: bool) -> dict[str, object]:
    if paper:
        require_paper_generation_settings(model)
    from solution.llm.call_llm import call_llm_details
    from solution.testcase_eval.solver import extract_test_input_llm

    raw_text, message, usage = call_llm_details(
        (
            "Return one valid test input containing the single integer 1. "
            "Use exactly one plaintext fenced block."
        ),
        model,
    )
    extracted, extractor_message, extractor_usage = extract_test_input_llm(
        "Test Input:\n1"
    )
    request_config = message.get("request_config", {})
    if paper and model == "gpt-5.6-sol":
        expected = {
            "transport": "responses",
            "max_output_tokens": 18_000,
            "reasoning_effort": "xhigh",
            "temperature": 1.0,
        }
        mismatches = {
            key: (request_config.get(key), value)
            for key, value in expected.items()
            if request_config.get(key) != value
        }
        if mismatches:
            raise RuntimeError(f"main-model preflight settings differ: {mismatches}")
    if extracted.strip() != "1":
        raise RuntimeError(f"extractor preflight returned {extracted!r}")
    return {
        "main": {
            "model": message.get("model"),
            "request_config": request_config,
            "usage": usage,
            "returned_text": bool(raw_text),
        },
        "extractor": {
            "model": extractor_message.get("model"),
            "request_config": extractor_message.get("request_config"),
            "usage": extractor_usage,
            "extracted": extracted,
        },
    }


def _run_docker(args: argparse.Namespace) -> None:
    dockerfile = ROOT / "docker" / "testcase_eval.Dockerfile"
    subprocess.run(
        [
            "docker",
            "build",
            "--file",
            str(dockerfile),
            "--tag",
            args.image,
            str(ROOT),
        ],
        check=True,
    )
    result_dir = args.result_dir.resolve()
    result_dir.mkdir(parents=True, exist_ok=True)
    command = [
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        "8192",
        "--tmpfs",
        "/tmp:rw,exec,size=32g",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "--volume",
        f"{ROOT}:/workspace:ro",
        "--volume",
        f"{result_dir}:/results",
        "--workdir",
        "/workspace",
        "--env",
        "PYTHONDONTWRITEBYTECODE=1",
        args.image,
        "python3",
        "-m",
        "scripts.run_testcase_eval_batch",
        "--phase",
        "judge",
        "--result-dir",
        "/results",
        "--workers",
        str(args.workers),
        "--inside-container",
    ]
    subprocess.run(command, check=True)


def main(default_tasks: Sequence[int] | None = None) -> None:
    args = _arguments(default_tasks)
    args.result_dir = args.result_dir.resolve()
    args.result_dir.mkdir(parents=True, exist_ok=True)
    database = args.result_dir / "results.sqlite3"

    if args.phase == "preflight":
        _print(_preflight(args.model, args.paper))
        return
    if args.phase == "judge":
        if not args.inside_container:
            _run_docker(args)
            return
        from utils.testcase_eval_executor import run_judge

        _print(
            run_judge(
                database,
                cache_dir=args.result_dir / "compile-cache",
                workers=args.workers,
            )
        )
        return

    with RunStore(database) as store:
        if args.phase == "prepare":
            _print(
                prepare_dataset(
                    store,
                    cache_dir=args.dataset_cache,
                    smoke_problems=args.smoke_problems,
                    problem_ids=args.problem_id,
                    verify_prompts=not args.no_verify_prompts,
                )
            )
        elif args.phase == "generate":
            if args.paper:
                require_paper_generation_settings(args.model)
            _print(
                generate(
                    store,
                    model=args.model,
                    policies=args.policies,
                    tasks=args.tasks,
                    workers=args.workers,
                    task1_generations=args.task1_generations,
                    retry_errors=args.retry_errors,
                )
            )
        else:
            summary = write_summary(store, args.result_dir / "summary.json")
            _print(
                {
                    "complete": summary["complete"],
                    "actual_executions": summary["actual_executions"],
                    "expected": summary["expected"],
                    "policies": summary["policies"],
                    "summary": str(args.result_dir / "summary.json"),
                }
            )


if __name__ == "__main__":
    main()
