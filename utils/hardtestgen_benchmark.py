"""Checkpointed HardTestGen adapter for prepared TCE and CC+ databases."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from typing import Any
import json
import time

from solution.hardtestgen import UPSTREAM_COMMIT
from solution.hardtestgen.api import (
    HardTestGenInput,
    KitStage,
    OracleProgram,
    SuiteResult,
    TestCase,
    TestCaseKit,
)
from solution.hardtestgen.pipeline import HardTestGenPipeline, project_test_cases
from utils.testcase_eval_benchmark import RunStore, _json, _usage_numbers, effective_model_request


POLICY = "hardtestgen"
DEFAULT_PROJECTION_BUDGET = 20


def _create_schema(store: RunStore) -> None:
    store.connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS hardtestgen_kits (
            problem_id TEXT PRIMARY KEY,
            model TEXT NOT NULL,
            kit_json TEXT NOT NULL,
            status TEXT NOT NULL,
            error TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS hardtestgen_calls (
            problem_id TEXT NOT NULL,
            stage TEXT NOT NULL,
            model TEXT NOT NULL,
            stage_json TEXT NOT NULL,
            status TEXT NOT NULL,
            error TEXT NOT NULL,
            created_at REAL NOT NULL,
            PRIMARY KEY (problem_id, stage)
        );
        CREATE TABLE IF NOT EXISTS hardtestgen_suites (
            problem_id TEXT PRIMARY KEY,
            suite_json TEXT NOT NULL,
            status TEXT NOT NULL,
            error TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        """
    )
    store.connection.commit()


def benchmark_kind(store: RunStore) -> str:
    manifest = store.manifest()
    benchmark = str(manifest.get("benchmark") or "")
    if benchmark.startswith("codecontests-plus"):
        return "codecontests-plus"
    if "testcase_eval_upstream_commit" in manifest:
        return "testcase-eval"
    raise RuntimeError("database is not a prepared TestCase-Eval or CodeContests+ run")


def _supported_language(benchmark: str, language: str) -> bool:
    compact = language.lower().replace(" ", "")
    if benchmark == "codecontests-plus":
        return language.upper() in {"CPP", "PY3"}
    return language.startswith("C++") or compact in {
        "python3", "pypy3", "pypy3-64"
    }


def problem_inputs(store: RunStore) -> list[HardTestGenInput]:
    benchmark = benchmark_kind(store)
    dataset_name = (
        "codecontests_plus_verified" if benchmark == "codecontests-plus"
        else "submission_all"
    )
    tasks = []
    for problem in store.connection.execute(
        "SELECT problem_id, statement, metadata_json FROM problems ORDER BY problem_id"
    ):
        oracles = []
        rows = store.connection.execute(
            """
            SELECT submission_id, language, source
            FROM submissions
            WHERE dataset_name = ? AND problem_id = ?
              AND submission_type = 'right_submission'
            ORDER BY submission_id
            """,
            (dataset_name, problem["problem_id"]),
        )
        for row in rows:
            if not _supported_language(benchmark, row["language"]):
                continue
            oracles.append(
                OracleProgram(row["submission_id"], row["language"], row["source"])
            )
            if len(oracles) == 5:
                break
        tasks.append(
            HardTestGenInput(
                problem["problem_id"],
                problem["statement"],
                tuple(oracles),
                json.loads(problem["metadata_json"]),
            )
        )
    return tasks


def _kit_from_json(value: str) -> TestCaseKit:
    data = json.loads(value)
    return TestCaseKit(
        input_validator=data["input_validator"],
        output_judging_function=data.get("output_judging_function"),
        llm_inputs=tuple(data["llm_inputs"]),
        regular_generator=data.get("regular_generator"),
        regular_functions=tuple(data["regular_functions"]),
        hack_generator=data.get("hack_generator"),
        hack_functions=tuple(data["hack_functions"]),
        prompts=data["prompts"],
        responses=data["responses"],
        messages=data["messages"],
        usage=data["usage"],
    )


def _stage_from_json(value: str) -> KitStage:
    data = json.loads(value)
    return KitStage(
        data["stage"],
        data["prompt"],
        data["raw_text"],
        data["message"],
        data["usage"],
        data["parsed"],
    )


def _call_stage(task, model, pipeline_factory, first=None):
    try:
        pipeline = pipeline_factory(model)
        stage = (
            pipeline.generate_iv_and_ojf(task)
            if first is None
            else pipeline.generate_input_generation(task, first)
        )
    except Exception as exc:
        return task, None, "request_error", f"{type(exc).__name__}: {exc}"
    valid = (
        isinstance(stage.parsed.get("input_validator"), str)
        and bool(stage.parsed["input_validator"].strip())
        if stage.stage == "iv_and_ojf"
        else bool(stage.parsed)
    )
    return (
        task,
        stage,
        "complete" if valid else "response_error",
        "" if valid else f"{stage.stage} response has no valid result",
    )


def _save_stage(store, task, model, stage_name, result) -> str:
    _task, stage, status, error = result
    store.connection.execute(
        "INSERT OR REPLACE INTO hardtestgen_calls VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            task.problem_id,
            stage_name,
            model,
            _json(asdict(stage)) if stage is not None else "{}",
            status,
            error,
            time.time(),
        ),
    )
    store.connection.commit()
    return status


def generate_kits(
    store: RunStore,
    *,
    model: str,
    workers: int,
    projection_budget: int = DEFAULT_PROJECTION_BUDGET,
    retry_errors: bool = False,
    pipeline_factory=HardTestGenPipeline,
) -> dict[str, int]:
    if workers < 1:
        raise ValueError("workers must be positive")
    if projection_budget < 1:
        raise ValueError("projection_budget must be positive")
    _create_schema(store)
    store.bind_manifest(
        {
            "model": model,
            "policies": [POLICY],
            "tasks": [1],
            "task1_generations": projection_budget,
            "hardtestgen": {
                "upstream_commit": UPSTREAM_COMMIT,
                "llm_calls_per_problem": 2,
                "max_oracles": 5,
                "min_output_agreements": 2,
                "projection": "category-round-robin",
                "projection_budget": projection_budget,
                "stable_deduplication": True,
            },
            "model_request": effective_model_request(),
        }
    )
    tasks = problem_inputs(store)
    pending_first = []
    for task in tasks:
        if not task.oracle_programs:
            continue
        row = store.connection.execute(
            "SELECT status FROM hardtestgen_calls WHERE problem_id = ? AND stage = ?",
            (task.problem_id, "iv_and_ojf"),
        ).fetchone()
        if row is None or (retry_errors and row["status"] != "complete"):
            pending_first.append(task)

    counts = {
        "iv_scheduled": len(pending_first),
        "input_generation_scheduled": 0,
        "complete": 0,
        "request_error": 0,
        "response_error": 0,
        "no_oracle": 0,
    }
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_call_stage, task, model, pipeline_factory): task
            for task in pending_first
        }
        for future in as_completed(futures):
            task = futures[future]
            _save_stage(store, task, model, "iv_and_ojf", future.result())

    pending_second = []
    for task in tasks:
        first_row = store.connection.execute(
            "SELECT * FROM hardtestgen_calls WHERE problem_id = ? AND stage = ?",
            (task.problem_id, "iv_and_ojf"),
        ).fetchone()
        if first_row is None or first_row["status"] != "complete":
            continue
        second_row = store.connection.execute(
            "SELECT status FROM hardtestgen_calls WHERE problem_id = ? AND stage = ?",
            (task.problem_id, "input_generation"),
        ).fetchone()
        if second_row is None or (retry_errors and second_row["status"] != "complete"):
            pending_second.append((task, _stage_from_json(first_row["stage_json"])))
    counts["input_generation_scheduled"] = len(pending_second)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_call_stage, task, model, pipeline_factory, first): task
            for task, first in pending_second
        }
        for future in as_completed(futures):
            task = futures[future]
            _save_stage(store, task, model, "input_generation", future.result())

    for task in tasks:
        status = "complete"
        error = ""
        kit = None
        if not task.oracle_programs:
            status = "no_oracle"
            error = "no supported correct program"
        else:
            stages = {
                row["stage"]: row
                for row in store.connection.execute(
                    "SELECT * FROM hardtestgen_calls WHERE problem_id = ?",
                    (task.problem_id,),
                )
            }
            first = stages.get("iv_and_ojf")
            second = stages.get("input_generation")
            failed = next(
                (row for row in (first, second) if row is not None and row["status"] != "complete"),
                None,
            )
            if failed is not None:
                status = failed["status"]
                error = failed["error"]
            elif first is None or second is None:
                status = "request_error"
                error = "missing HardTestGen call checkpoint"
            else:
                try:
                    kit = pipeline_factory(model).assemble_kit(
                        _stage_from_json(first["stage_json"]),
                        _stage_from_json(second["stage_json"]),
                    )
                except Exception as exc:
                    status = "response_error"
                    error = f"{type(exc).__name__}: {exc}"
        store.connection.execute(
            "INSERT OR REPLACE INTO hardtestgen_kits VALUES (?, ?, ?, ?, ?, ?)",
            (
                task.problem_id,
                model,
                _json(asdict(kit)) if kit is not None else "{}",
                status,
                error,
                time.time(),
            ),
        )
        store.connection.commit()
        counts[status] = counts.get(status, 0) + 1
    return counts


def _generate_suite(
    task: HardTestGenInput,
    kit: TestCaseKit,
    executor,
) -> tuple[HardTestGenInput, SuiteResult]:
    try:
        result = HardTestGenPipeline("materialize-only").generate_suite(
            task, kit, executor
        )
    except Exception as exc:
        result = SuiteResult("execution_error", error=f"{type(exc).__name__}: {exc}")
    return task, result


def _publish_projection(
    store: RunStore,
    task: HardTestGenInput,
    result: SuiteResult,
    budget: int,
) -> None:
    downstream = store.connection.execute(
        """
        SELECT
          (SELECT COUNT(*) FROM materializations WHERE policy = ? AND problem_id = ?)
          + (SELECT COUNT(*) FROM executions WHERE policy = ? AND problem_id = ?)
        """,
        (POLICY, task.problem_id, POLICY, task.problem_id),
    ).fetchone()[0]
    existing = store.connection.execute(
        "SELECT COUNT(*) FROM generations WHERE policy = ? AND problem_id = ?",
        (POLICY, task.problem_id),
    ).fetchone()[0]
    if existing and downstream:
        raise RuntimeError(
            f"cannot replace {task.problem_id}: projected tests were already judged"
        )
    projected = project_test_cases(result.test_cases, budget) if result.test_cases else []
    store.connection.execute(
        "DELETE FROM generations WHERE policy = ? AND problem_id = ?",
        (POLICY, task.problem_id),
    )
    for generation_id in range(budget):
        test_case = projected[generation_id] if generation_id < len(projected) else None
        candidate = test_case.input if test_case is not None else "ERROR"
        error = "" if test_case is not None else result.error or result.status
        store.save_generation(
            {
                "policy": POLICY,
                "task": 1,
                "problem_id": task.problem_id,
                "submission_id": "",
                "generation_id": generation_id,
                "prompt": "HardTestGen suite projection; see hardtestgen_kits",
                "raw_text": "",
                "candidate": candidate,
                "candidate_format": "raw_input",
                "message": {
                    "suite_status": result.status,
                    "method": test_case.method if test_case else None,
                    "generator": test_case.generator if test_case else None,
                },
                "usage": {},
                "status": "complete",
                "error": error,
            }
        )


def generate_suites(
    store: RunStore,
    *,
    executor,
    workers: int,
    projection_budget: int = DEFAULT_PROJECTION_BUDGET,
    retry_errors: bool = False,
) -> dict[str, int]:
    if workers < 1:
        raise ValueError("workers must be positive")
    if projection_budget < 1:
        raise ValueError("projection_budget must be positive")
    _create_schema(store)
    tasks = {task.problem_id: task for task in problem_inputs(store)}
    kit_problem_ids = {
        row["problem_id"]
        for row in store.connection.execute("SELECT problem_id FROM hardtestgen_kits")
    }
    missing = sorted(set(tasks) - kit_problem_ids)
    if missing:
        raise RuntimeError(
            f"generate-kits is incomplete; {len(missing)} problems have no checkpoint"
        )
    pending = []
    for row in store.connection.execute(
        "SELECT * FROM hardtestgen_kits ORDER BY problem_id"
    ):
        task = tasks[row["problem_id"]]
        existing = store.connection.execute(
            "SELECT status FROM hardtestgen_suites WHERE problem_id = ?",
            (task.problem_id,),
        ).fetchone()
        if existing is not None and not (retry_errors and existing["status"] != "complete"):
            continue
        if row["status"] != "complete":
            result = SuiteResult("kit_" + row["status"], error=row["error"])
            pending.append((task, None, result))
        else:
            pending.append((task, _kit_from_json(row["kit_json"]), None))

    counts: dict[str, int] = {"scheduled": len(pending), "complete": 0}
    immediate = [(task, result) for task, _kit, result in pending if result is not None]
    runnable = [(task, kit) for task, kit, result in pending if result is None]
    completed = list(immediate)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(_generate_suite, task, kit, executor) for task, kit in runnable
        ]
        for future in as_completed(futures):
            completed.append(future.result())

    for task, result in completed:
        store.connection.execute(
            "INSERT OR REPLACE INTO hardtestgen_suites VALUES (?, ?, ?, ?, ?)",
            (
                task.problem_id,
                _json(asdict(result)),
                result.status,
                result.error,
                time.time(),
            ),
        )
        _publish_projection(store, task, result, projection_budget)
        store.connection.commit()
        counts[result.status] = counts.get(result.status, 0) + 1
    return counts


def pipeline_summary(store: RunStore) -> dict[str, Any]:
    _create_schema(store)
    kit_counts = dict(
        store.connection.execute(
            "SELECT status, COUNT(*) FROM hardtestgen_kits GROUP BY status"
        ).fetchall()
    )
    suite_counts = dict(
        store.connection.execute(
            "SELECT status, COUNT(*) FROM hardtestgen_suites GROUP BY status"
        ).fetchall()
    )
    methods: dict[str, int] = {}
    full_test_cases = 0
    generated_inputs = 0
    for row in store.connection.execute("SELECT suite_json FROM hardtestgen_suites"):
        suite = json.loads(row["suite_json"])
        full_test_cases += len(suite.get("test_cases", []))
        generated_inputs += len(suite.get("generated_inputs", []))
        for test_case in suite.get("test_cases", []):
            method = str(test_case.get("method") or "unknown")
            methods[method] = methods.get(method, 0) + 1
    prompt = completion = total = 0
    for row in store.connection.execute(
        "SELECT kit_json FROM hardtestgen_kits WHERE status = 'complete'"
    ):
        usage = json.loads(row["kit_json"]).get("usage", {})
        current = _usage_numbers(usage)
        prompt += current[0]
        completion += current[1]
        total += current[2]
    return {
        "benchmark": benchmark_kind(store),
        "kits": kit_counts,
        "suites": suite_counts,
        "full_test_cases": full_test_cases,
        "generated_inputs": generated_inputs,
        "methods": methods,
        "projected_generations": store.connection.execute(
            "SELECT COUNT(*) FROM generations WHERE policy = ?", (POLICY,)
        ).fetchone()[0],
        "usage": {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": total,
        },
    }
