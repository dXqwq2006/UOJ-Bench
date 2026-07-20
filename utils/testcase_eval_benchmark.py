"""Offline TestCase-Eval data, generation, persistence, and scoring."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext
from itertools import groupby
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
import json
import os
import sqlite3
import tempfile
import time

from solution import load_solver
from solution.api import (
    FaultCoverageInput,
    FaultExposureInput,
    TestCaseFormat,
    solver_capabilities,
)


UPSTREAM_COMMIT = "45275c6f838566e6e148a9eca18edc00be08a305"
DATASETS = {
    "problem": (
        "TestCase-Eval/problem",
        "b5cc0cc4589f5e38c1b010c24a4c5f513009278e",
    ),
    "submission_all": (
        "TestCase-Eval/submission_all",
        "7de1bb5d7b3143418147a84d34594be162ef7821",
    ),
    "submission_lite": (
        "TestCase-Eval/submission_lite",
        "96affb6b416002ed36bab881834e38a8c07b0647",
    ),
    "task1_prompt": (
        "Raywithyou/TestCase-Eval-Task1",
        "bd8b0e2e26e1e52225ca41537eaff592142cbc85",
    ),
    "task2_prompt": (
        "Raywithyou/TestCase-Eval-Task2",
        "ad6c3af216b088652b6f05d7df331b3858bf916d",
    ),
}
PAPER_GENERATIONS = {1: 20, 2: 1}
PAPER_TEMPERATURE = 1.0
PAPER_REASONING_MAX_OUTPUT_TOKENS = 18_000
_KILL_RESULTS = {"runtime_error", "time_limit_exceeded", "memory_limit_exceeded"}
_BOOL_TOKENS = {"yes", "no", "true", "false"}


@dataclass(frozen=True)
class GenerationJob:
    policy: str
    task: int
    problem_id: str
    problem_statement: str
    submission_id: str
    submission_code: str
    submission_language: str
    generation_id: int
    metadata: Mapping[str, Any]


class RunStore:
    """SQLite-backed run state with atomic, idempotent records."""

    def __init__(self, path: str | os.PathLike[str]):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, timeout=60)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=NORMAL")
        self._create_schema()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "RunStore":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    def _create_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS manifest (
                key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS problems (
                problem_id TEXT PRIMARY KEY,
                statement TEXT NOT NULL,
                metadata_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS submissions (
                dataset_name TEXT NOT NULL,
                submission_id TEXT NOT NULL,
                problem_id TEXT NOT NULL,
                submission_type TEXT NOT NULL,
                verdict TEXT NOT NULL,
                language TEXT NOT NULL,
                difficulty TEXT NOT NULL,
                source TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                PRIMARY KEY (dataset_name, submission_id)
            );
            CREATE INDEX IF NOT EXISTS submissions_problem
                ON submissions(dataset_name, problem_id, submission_type);
            CREATE TABLE IF NOT EXISTS generations (
                policy TEXT NOT NULL,
                task INTEGER NOT NULL,
                problem_id TEXT NOT NULL,
                submission_id TEXT NOT NULL,
                generation_id INTEGER NOT NULL,
                prompt TEXT NOT NULL,
                raw_text TEXT NOT NULL,
                candidate TEXT NOT NULL,
                candidate_format TEXT NOT NULL,
                message_json TEXT NOT NULL,
                usage_json TEXT NOT NULL,
                status TEXT NOT NULL,
                error TEXT NOT NULL,
                created_at REAL NOT NULL,
                PRIMARY KEY (
                    policy, task, problem_id, submission_id, generation_id
                )
            );
            CREATE TABLE IF NOT EXISTS materializations (
                policy TEXT NOT NULL,
                task INTEGER NOT NULL,
                problem_id TEXT NOT NULL,
                submission_id TEXT NOT NULL,
                generation_id INTEGER NOT NULL,
                test_input TEXT NOT NULL,
                status TEXT NOT NULL,
                error TEXT NOT NULL,
                created_at REAL NOT NULL,
                PRIMARY KEY (
                    policy, task, problem_id, submission_id, generation_id
                )
            );
            CREATE TABLE IF NOT EXISTS executions (
                policy TEXT NOT NULL,
                task INTEGER NOT NULL,
                problem_id TEXT NOT NULL,
                submission_id TEXT NOT NULL,
                generation_id INTEGER NOT NULL,
                checked_submission_id TEXT NOT NULL,
                checked_submission_type TEXT NOT NULL,
                checked_submission_verdict TEXT NOT NULL,
                checked_submission_language TEXT NOT NULL,
                checked_submission_difficulty TEXT NOT NULL,
                result TEXT NOT NULL,
                output TEXT NOT NULL,
                error TEXT NOT NULL,
                elapsed REAL NOT NULL,
                memory_kb INTEGER NOT NULL,
                created_at REAL NOT NULL,
                PRIMARY KEY (
                    policy, task, problem_id, submission_id, generation_id,
                    checked_submission_id
                )
            );
            CREATE INDEX IF NOT EXISTS executions_score
                ON executions(policy, task, problem_id, submission_id, generation_id);
            """
        )
        self.connection.commit()

    def bind_manifest(self, values: Mapping[str, Any]) -> None:
        for key, value in values.items():
            encoded = _json(value)
            row = self.connection.execute(
                "SELECT value_json FROM manifest WHERE key = ?", (key,)
            ).fetchone()
            if row is not None and row["value_json"] != encoded:
                raise ValueError(
                    f"result database is already bound to a different {key}: "
                    f"{row['value_json']} != {encoded}"
                )
            self.connection.execute(
                "INSERT OR IGNORE INTO manifest(key, value_json) VALUES (?, ?)",
                (key, encoded),
            )
        self.connection.commit()

    def manifest(self) -> dict[str, Any]:
        return {
            row["key"]: json.loads(row["value_json"])
            for row in self.connection.execute(
                "SELECT key, value_json FROM manifest ORDER BY key"
            )
        }

    def save_generation(self, record: Mapping[str, Any]) -> None:
        self.connection.execute(
            """
            INSERT OR REPLACE INTO generations (
                policy, task, problem_id, submission_id, generation_id,
                prompt, raw_text, candidate, candidate_format, message_json,
                usage_json, status, error, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["policy"],
                record["task"],
                record["problem_id"],
                record["submission_id"],
                record["generation_id"],
                record["prompt"],
                record["raw_text"],
                record["candidate"],
                record["candidate_format"],
                _json(record["message"]),
                _json(record["usage"]),
                record["status"],
                record["error"],
                time.time(),
            ),
        )
        self.connection.commit()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _load_dataset(key: str, cache_dir: str | None) -> Any:
    from datasets import load_dataset

    name, revision = DATASETS[key]
    return load_dataset(
        name,
        split="train",
        revision=revision,
        cache_dir=cache_dir,
    )


def _problem_id(record: Mapping[str, Any]) -> str:
    extra = record.get("extra_info")
    if isinstance(extra, Mapping) and extra.get("problem_id") is not None:
        return str(extra["problem_id"])
    for key in ("problem_id", "id"):
        if record.get(key) is not None:
            return str(record[key])
    raise KeyError("record has no problem_id")


def _submission_id(record: Mapping[str, Any]) -> str:
    for key in ("id", "submission_id"):
        if record.get(key) is not None:
            return str(record[key])
    extra = record.get("extra_info")
    if isinstance(extra, Mapping) and extra.get("submission_id") is not None:
        return str(extra["submission_id"])
    raise KeyError("record has no submission id")


def _prompt_text(prompt: Any) -> str:
    if isinstance(prompt, str):
        return prompt
    if isinstance(prompt, Sequence) and not isinstance(prompt, (bytes, bytearray)):
        if len(prompt) == 1 and isinstance(prompt[0], Mapping):
            content = prompt[0].get("content")
            if isinstance(content, str):
                return content
    raise TypeError(f"unsupported official prompt shape: {type(prompt).__name__}")


def _problem_statement(record: Mapping[str, Any]) -> str:
    for key in ("prompt", "problem_description", "statement"):
        value = record.get(key)
        if isinstance(value, str):
            return value
    raise KeyError("problem record has no statement")


def _metadata_without_source(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in record.items()
        if key not in {"source", "prompt"} and _json_size(value) <= 16_384
    }


def _json_size(value: Any) -> int:
    try:
        return len(_json(value))
    except (TypeError, ValueError):
        return 1_000_000_000


def prepare_dataset(
    store: RunStore,
    *,
    cache_dir: str | None = None,
    smoke_problems: int | None = None,
    problem_ids: Sequence[str] = (),
    verify_prompts: bool = True,
) -> dict[str, Any]:
    """Load pinned snapshots, validate policy prompts, and stage selected rows."""
    problem_data = _load_dataset("problem", cache_dir)
    problems = {_problem_id(row): dict(row) for row in problem_data}
    if problem_ids:
        selected = sorted({str(value) for value in problem_ids})
        missing = sorted(set(selected) - set(problems))
        if missing:
            raise ValueError(f"unknown problem ids: {', '.join(missing)}")
    else:
        selected = sorted(problems)
        if smoke_problems is not None:
            if smoke_problems < 1:
                raise ValueError("smoke_problems must be positive")
            selected = selected[:smoke_problems]
    selected_set = set(selected)

    all_rows = [
        dict(row)
        for row in _load_dataset("submission_all", cache_dir)
        if _problem_id(row) in selected_set
    ]
    lite_rows = [
        dict(row)
        for row in _load_dataset("submission_lite", cache_dir)
        if _problem_id(row) in selected_set
    ]

    store.bind_manifest(
        {
            "testcase_eval_upstream_commit": UPSTREAM_COMMIT,
            "dataset_revisions": {
                key: {"name": value[0], "revision": value[1]}
                for key, value in DATASETS.items()
            },
            "selected_problem_ids": selected,
            "paper_generations": PAPER_GENERATIONS,
        }
    )
    store.connection.executemany(
        """
        INSERT OR REPLACE INTO problems(problem_id, statement, metadata_json)
        VALUES (?, ?, ?)
        """,
        (
            (
                problem_id,
                _problem_statement(problems[problem_id]),
                _json(_metadata_without_source(problems[problem_id])),
            )
            for problem_id in selected
        ),
    )

    staged = {"submission_all": all_rows, "submission_lite": lite_rows}
    for dataset_name, rows in staged.items():
        store.connection.executemany(
            """
            INSERT OR REPLACE INTO submissions (
                dataset_name, submission_id, problem_id, submission_type,
                verdict, language, difficulty, source, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    dataset_name,
                    _submission_id(row),
                    _problem_id(row),
                    str(row.get("type", "")),
                    str(row.get("verdict", "")),
                    str(row.get("language", "")),
                    str(row.get("difficulty", "")),
                    str(row.get("source", "")),
                    _json(_metadata_without_source(row)),
                )
                for row in rows
            ),
        )
    store.connection.commit()

    prompt_checks = {1: 0, 2: 0}
    if verify_prompts:
        prompt_checks = _verify_official_prompts(
            problems, lite_rows, selected_set, cache_dir
        )

    summary = {
        "problems": len(selected),
        "submission_all": len(all_rows),
        "submission_lite": len(lite_rows),
        "wrong_all": sum(row.get("type") == "wrong_submission" for row in all_rows),
        "wrong_lite": sum(row.get("type") == "wrong_submission" for row in lite_rows),
        "right_all": sum(row.get("type") == "right_submission" for row in all_rows),
        "right_lite": sum(row.get("type") == "right_submission" for row in lite_rows),
        "verified_task1_prompts": prompt_checks[1],
        "verified_task2_prompts": prompt_checks[2],
        "problem_ids": selected,
    }
    store.bind_manifest({"prepared_counts": summary})
    return summary


def _verify_official_prompts(
    problems: Mapping[str, Mapping[str, Any]],
    lite_rows: Sequence[Mapping[str, Any]],
    selected: set[str],
    cache_dir: str | None,
) -> dict[int, int]:
    from solution.testcase_eval import prompts

    expected_task1 = {
        _problem_id(row): _prompt_text(row["prompt"])
        for row in _load_dataset("task1_prompt", cache_dir)
        if _problem_id(row) in selected
    }
    lite_by_id = {_submission_id(row): row for row in lite_rows}
    expected_task2 = {
        (_problem_id(row), _submission_id(row)): _prompt_text(row["prompt"])
        for row in _load_dataset("task2_prompt", cache_dir)
        if _problem_id(row) in selected
    }

    checked1 = 0
    for problem_id in sorted(selected):
        actual = prompts.fault_coverage(
            FaultCoverageInput(
                problem_id,
                _problem_statement(problems[problem_id]),
            )
        )
        if expected_task1.get(problem_id) != actual:
            raise ValueError(f"Task 1 prompt differs from pinned dataset for {problem_id}")
        checked1 += 1

    checked2 = 0
    for key, expected in expected_task2.items():
        problem_id, submission_id = key
        submission = lite_by_id.get(submission_id)
        if submission is None:
            raise ValueError(f"Task 2 prompt references missing submission {submission_id}")
        actual = prompts.fault_exposure(
            FaultExposureInput(
                problem_id,
                _problem_statement(problems[problem_id]),
                int(submission_id) if submission_id.isdigit() else submission_id,
                str(submission["source"]),
                str(submission.get("language", "")),
            )
        )
        if expected != actual:
            raise ValueError(
                f"Task 2 prompt differs from pinned dataset for "
                f"{problem_id}/{submission_id}"
            )
        checked2 += 1
    return {1: checked1, 2: checked2}


def _existing_generation(
    store: RunStore, job: GenerationJob
) -> sqlite3.Row | None:
    return store.connection.execute(
        """
        SELECT status FROM generations
        WHERE policy = ? AND task = ? AND problem_id = ?
          AND submission_id = ? AND generation_id = ?
        """,
        (
            job.policy,
            job.task,
            job.problem_id,
            job.submission_id,
            job.generation_id,
        ),
    ).fetchone()


def generation_jobs(
    store: RunStore,
    *,
    model: str,
    policies: Sequence[str],
    tasks: Sequence[int],
    task1_generations: int = PAPER_GENERATIONS[1],
    retry_errors: bool = False,
) -> list[GenerationJob]:
    if set(tasks) - {1, 2}:
        raise ValueError("tasks must contain only 1 or 2")

    capabilities = {
        policy: solver_capabilities(load_solver(policy, model))
        for policy in policies
    }
    capability_names = {1: "fault_coverage", 2: "fault_exposure"}
    supported_tasks = {
        policy: {
            task
            for task, capability in capability_names.items()
            if getattr(policy_capabilities, capability)
        }
        for policy, policy_capabilities in capabilities.items()
    }
    for task in tasks:
        if not any(task in supported for supported in supported_tasks.values()):
            raise ValueError(f"no selected policy supports TestCase-Eval Task {task}")

    problems = list(
        store.connection.execute(
            "SELECT problem_id, statement, metadata_json FROM problems ORDER BY problem_id"
        )
    )
    jobs: list[GenerationJob] = []
    if 1 in tasks:
        for policy in policies:
            if 1 not in supported_tasks[policy]:
                continue
            for problem in problems:
                for generation_id in range(task1_generations):
                    jobs.append(
                        GenerationJob(
                            policy,
                            1,
                            problem["problem_id"],
                            problem["statement"],
                            "",
                            "",
                            "",
                            generation_id,
                            json.loads(problem["metadata_json"]),
                        )
                    )

    if 2 in tasks:
        submissions = list(
            store.connection.execute(
                """
                SELECT s.*, p.statement, p.metadata_json AS problem_metadata_json
                FROM submissions AS s
                JOIN problems AS p USING(problem_id)
                WHERE s.dataset_name = 'submission_lite'
                  AND s.submission_type = 'wrong_submission'
                ORDER BY s.problem_id, s.submission_id
                """
            )
        )
        for policy in policies:
            if 2 not in supported_tasks[policy]:
                continue
            for submission in submissions:
                jobs.append(
                    GenerationJob(
                        policy,
                        2,
                        submission["problem_id"],
                        submission["statement"],
                        submission["submission_id"],
                        submission["source"],
                        submission["language"],
                        0,
                        json.loads(submission["problem_metadata_json"]),
                    )
                )

    selected: list[GenerationJob] = []
    for job in jobs:
        existing = _existing_generation(store, job)
        if existing is None:
            selected.append(job)
        elif retry_errors and existing["status"] == "request_error":
            selected.append(job)
    return selected


def _generate_one(job: GenerationJob, model: str) -> dict[str, Any]:
    prompt = ""
    try:
        solver = load_solver(job.policy, model)
        if job.task == 1:
            session = solver.start_fault_coverage(
                FaultCoverageInput(
                    job.problem_id,
                    job.problem_statement,
                    job.metadata,
                )
            )
        else:
            submission_id: Any = job.submission_id
            if job.submission_id.isdigit():
                submission_id = int(job.submission_id)
            session = solver.start_fault_exposure(
                FaultExposureInput(
                    job.problem_id,
                    job.problem_statement,
                    submission_id,
                    job.submission_code,
                    job.submission_language,
                    job.metadata,
                )
            )
        prompt = session.initial_request
        turn = session.next()
        candidate = turn.candidate
        return {
            "policy": job.policy,
            "task": job.task,
            "problem_id": job.problem_id,
            "submission_id": job.submission_id,
            "generation_id": job.generation_id,
            "prompt": prompt,
            "raw_text": turn.raw_text,
            "candidate": candidate.content if candidate is not None else "ERROR",
            "candidate_format": (
                candidate.format.value
                if candidate is not None
                else TestCaseFormat.RAW_INPUT.value
            ),
            "message": turn.message,
            "usage": turn.usage,
            "status": "complete",
            "error": turn.error or "",
        }
    except Exception as exc:
        return {
            "policy": job.policy,
            "task": job.task,
            "problem_id": job.problem_id,
            "submission_id": job.submission_id,
            "generation_id": job.generation_id,
            "prompt": prompt,
            "raw_text": "",
            "candidate": "",
            "candidate_format": TestCaseFormat.RAW_INPUT.value,
            "message": {},
            "usage": {},
            "status": "request_error",
            "error": f"{type(exc).__name__}: {exc}",
        }


def generate(
    store: RunStore,
    *,
    model: str,
    policies: Sequence[str],
    tasks: Sequence[int],
    workers: int,
    task1_generations: int = PAPER_GENERATIONS[1],
    retry_errors: bool = False,
) -> dict[str, int]:
    if workers < 1:
        raise ValueError("workers must be positive")
    store.bind_manifest(
        {
            "model": model,
            "policies": sorted(set(policies)),
            "tasks": sorted(set(tasks)),
            "task1_generations": task1_generations,
            "model_request": effective_model_request(),
        }
    )
    jobs = generation_jobs(
        store,
        model=model,
        policies=policies,
        tasks=tasks,
        task1_generations=task1_generations,
        retry_errors=retry_errors,
    )
    counts = {"scheduled": len(jobs), "complete": 0, "request_error": 0}
    if not jobs:
        return counts

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_generate_one, job, model): job for job in jobs}
        for completed, future in enumerate(as_completed(futures), 1):
            record = future.result()
            store.save_generation(record)
            counts[record["status"]] += 1
            if completed % 10 == 0 or completed == len(futures):
                print(
                    f"generation {completed}/{len(futures)} "
                    f"complete={counts['complete']} errors={counts['request_error']}",
                    flush=True,
                )
    return counts


def effective_model_request() -> dict[str, Any]:
    keys = (
        "TATU_BASE_URL",
        "TATU_OPENAI_TRANSPORT",
        "TATU_DEPLOYER",
        "TATU_REASONING_EFFORT",
        "TATU_MAX_OUTPUT_TOKENS",
        "TATU_TEMPERATURE",
        "TESTCASE_EVAL_EXTRACTOR_BASE_URL",
        "TESTCASE_EVAL_EXTRACTOR_MODEL",
    )
    return {key: os.environ.get(key) for key in keys}


def require_paper_generation_settings(model: str) -> None:
    try:
        temperature = float(os.environ.get("TATU_TEMPERATURE", ""))
    except ValueError as exc:
        raise ValueError("paper mode requires TATU_TEMPERATURE=1.0") from exc
    if temperature != PAPER_TEMPERATURE:
        raise ValueError("paper mode requires TATU_TEMPERATURE=1.0")
    if os.environ.get("TATU_MAX_OUTPUT_TOKENS") != str(
        PAPER_REASONING_MAX_OUTPUT_TOKENS
    ):
        raise ValueError("paper mode requires 18000 output tokens")
    if model == "gpt-5.6-sol":
        if os.environ.get("TATU_REASONING_EFFORT") not in {"xhigh", "max"}:
            raise ValueError("gpt-5.6-sol paper run requires reasoning effort xhigh")
        if os.environ.get("TATU_OPENAI_TRANSPORT") != "responses":
            raise ValueError("gpt-5.6-sol paper run requires the Responses transport")


def validate_outputs(output1: str, output2: str) -> bool:
    """Match TestCase-Eval's line/token comparator at 1e-12 precision."""
    lines1 = output1.strip().split("\n")
    lines2 = output2.strip().split("\n")
    if len(lines1) == len(lines2) and all(
        first.strip() == second.strip()
        for first, second in zip(lines1, lines2)
    ):
        return True

    tokens1 = output1.strip().split()
    tokens2 = output2.strip().split()
    if len(tokens1) != len(tokens2):
        return False
    with localcontext() as context:
        context.prec = 40
        precision = Decimal("1e-12")
        for first, second in zip(tokens1, tokens2):
            try:
                number1 = Decimal(first)
                number2 = Decimal(second)
                if number1.is_nan() or number2.is_nan():
                    continue
                if number1.is_infinite() or number2.is_infinite():
                    if number1 != number2:
                        return False
                elif abs(number1 - number2) > precision:
                    return False
                continue
            except InvalidOperation:
                pass
            normalized1 = first.lower() if first.lower() in _BOOL_TOKENS else first
            normalized2 = second.lower() if second.lower() in _BOOL_TOKENS else second
            if normalized1 != normalized2:
                return False
    return True


def _oracle(rows: Sequence[sqlite3.Row]) -> str | None:
    outputs = [
        row["output"]
        for row in rows
        if row["checked_submission_type"] == "right_submission"
        and row["result"] == "success_run"
    ]
    if len(outputs) == 1:
        return outputs[0]
    for index, first in enumerate(outputs):
        for second in outputs[index + 1 :]:
            if validate_outputs(first, second):
                return first
    return None


def _killed(row: sqlite3.Row, oracle: str | None) -> bool:
    if oracle is None:
        return False
    if row["result"] in _KILL_RESULTS:
        return True
    return row["result"] == "success_run" and not validate_outputs(
        row["output"], oracle
    )


def _usage_numbers(value: Any) -> tuple[int, int, int]:
    if not isinstance(value, Mapping):
        return (0, 0, 0)
    if any(
        key in value
        for key in ("prompt_tokens", "completion_tokens", "input_tokens", "output_tokens")
    ):
        prompt = int(value.get("prompt_tokens", value.get("input_tokens", 0)) or 0)
        completion = int(
            value.get("completion_tokens", value.get("output_tokens", 0)) or 0
        )
        total = int(value.get("total_tokens", prompt + completion) or 0)
        return (prompt, completion, total)
    prompt = completion = total = 0
    for item in value.values():
        child = _usage_numbers(item)
        prompt += child[0]
        completion += child[1]
        total += child[2]
    return (prompt, completion, total)


def score(store: RunStore) -> dict[str, Any]:
    generation_rows = list(
        store.connection.execute(
            """
            SELECT policy, task, status, usage_json
            FROM generations ORDER BY policy, task
            """
        )
    )
    generation_counts: dict[str, dict[str, dict[str, int]]] = {}
    usage: dict[str, dict[str, dict[str, int]]] = {}
    for row in generation_rows:
        policy = row["policy"]
        task = str(row["task"])
        counter = generation_counts.setdefault(policy, {}).setdefault(task, {})
        counter[row["status"]] = counter.get(row["status"], 0) + 1
        totals = usage.setdefault(policy, {}).setdefault(
            task,
            {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        )
        prompt, completion, total = _usage_numbers(json.loads(row["usage_json"]))
        totals["prompt_tokens"] += prompt
        totals["completion_tokens"] += completion
        totals["total_tokens"] += total

    rows = store.connection.execute(
        """
        SELECT * FROM executions
        ORDER BY policy, task, problem_id, submission_id, generation_id,
                 checked_submission_type, checked_submission_id
        """
    )
    key = lambda row: (
        row["policy"],
        row["task"],
        row["problem_id"],
        row["submission_id"],
        row["generation_id"],
    )
    task1_kills: dict[str, dict[tuple[str, str], int]] = {}
    task1_denominators: dict[str, set[tuple[str, str]]] = {}
    task2_totals: dict[str, int] = {}
    task2_kills: dict[str, int] = {}
    task2_breakdown: dict[str, dict[str, dict[str, int]]] = {}
    oracle_failures: dict[str, dict[str, int]] = {}

    for group_key, group in groupby(rows, key=key):
        policy, task, problem_id, _submission_id_value, generation_id = group_key
        execution_rows = list(group)
        oracle = _oracle(execution_rows)
        if oracle is None:
            failures = oracle_failures.setdefault(policy, {"1": 0, "2": 0})
            failures[str(task)] += 1
        wrong_rows = [
            row
            for row in execution_rows
            if row["checked_submission_type"] == "wrong_submission"
        ]
        if task == 1:
            denominator = task1_denominators.setdefault(policy, set())
            kills = task1_kills.setdefault(policy, {})
            for row in wrong_rows:
                submission_key = (problem_id, row["checked_submission_id"])
                denominator.add(submission_key)
                if _killed(row, oracle):
                    kills[submission_key] = min(
                        generation_id,
                        kills.get(submission_key, generation_id),
                    )
        else:
            target = next(
                (
                    row
                    for row in wrong_rows
                    if row["checked_submission_id"] == row["submission_id"]
                ),
                None,
            )
            if target is None:
                continue
            task2_totals[policy] = task2_totals.get(policy, 0) + 1
            killed = _killed(target, oracle)
            task2_kills[policy] = task2_kills.get(policy, 0) + int(killed)
            for dimension, value in (
                ("difficulty", target["checked_submission_difficulty"] or "unknown"),
                ("language", target["checked_submission_language"] or "unknown"),
                ("verdict", target["checked_submission_verdict"] or "unknown"),
            ):
                bucket = task2_breakdown.setdefault(policy, {}).setdefault(
                    f"{dimension}:{value}", {"killed": 0, "total": 0}
                )
                bucket["total"] += 1
                bucket["killed"] += int(killed)

    policies = sorted(
        set(generation_counts)
        | set(task1_denominators)
        | set(task2_totals)
    )
    results: dict[str, Any] = {}
    for policy in policies:
        result: dict[str, Any] = {}
        denominator = len(task1_denominators.get(policy, set()))
        if denominator:
            coverage = {}
            for count in (1, 5, 10, 20):
                killed = sum(
                    generation_id < count
                    for generation_id in task1_kills.get(policy, {}).values()
                )
                coverage[f"cov@{count}"] = {
                    "killed": killed,
                    "total": denominator,
                    "ratio": killed / denominator,
                }
            result["task1"] = coverage
        total = task2_totals.get(policy, 0)
        if total:
            killed = task2_kills.get(policy, 0)
            result["task2"] = {
                "killed": killed,
                "total": total,
                "ratio": killed / total,
                "breakdown": task2_breakdown.get(policy, {}),
            }
        results[policy] = result

    expected = _expected_counts(store)
    actual_executions = store.connection.execute(
        "SELECT COUNT(*) FROM executions"
    ).fetchone()[0]
    request_errors = sum(
        statuses.get("request_error", 0)
        for tasks in generation_counts.values()
        for statuses in tasks.values()
    )
    summary = {
        "manifest": store.manifest(),
        "generation_counts": generation_counts,
        "usage": usage,
        "oracle_failures": oracle_failures,
        "expected": expected,
        "actual_executions": actual_executions,
        "complete": (
            request_errors == 0
            and expected["generations"] == len(generation_rows)
            and expected["executions"] == actual_executions
        ),
        "policies": results,
    }
    return summary


def _expected_counts(store: RunStore) -> dict[str, int]:
    manifest = store.manifest()
    policies = manifest.get("policies", [])
    tasks = manifest.get("tasks", [])
    task1_generations = int(
        manifest.get("task1_generations", PAPER_GENERATIONS[1])
    )
    problem_count = store.connection.execute(
        "SELECT COUNT(*) FROM problems"
    ).fetchone()[0]
    wrong_lite = store.connection.execute(
        """
        SELECT COUNT(*) FROM submissions
        WHERE dataset_name = 'submission_lite'
          AND submission_type = 'wrong_submission'
        """
    ).fetchone()[0]
    all_count = store.connection.execute(
        "SELECT COUNT(*) FROM submissions WHERE dataset_name = 'submission_all'"
    ).fetchone()[0]
    rights_lite_by_problem = {
        row["problem_id"]: row["count"]
        for row in store.connection.execute(
            """
            SELECT problem_id, COUNT(*) AS count
            FROM submissions
            WHERE dataset_name = 'submission_lite'
              AND submission_type = 'right_submission'
            GROUP BY problem_id
            """
        )
    }
    wrong_lite_by_problem = {
        row["problem_id"]: row["count"]
        for row in store.connection.execute(
            """
            SELECT problem_id, COUNT(*) AS count
            FROM submissions
            WHERE dataset_name = 'submission_lite'
              AND submission_type = 'wrong_submission'
            GROUP BY problem_id
            """
        )
    }

    generations = 0
    executions = 0
    if 1 in tasks and "testcase_eval" in policies:
        generations += problem_count * task1_generations
        executions += all_count * task1_generations
    if 2 in tasks:
        for _policy in policies:
            generations += wrong_lite
            executions += sum(
                wrong_count * (1 + rights_lite_by_problem.get(problem_id, 0))
                for problem_id, wrong_count in wrong_lite_by_problem.items()
            )
    return {"generations": generations, "executions": executions}


def write_summary(store: RunStore, path: str | os.PathLike[str]) -> dict[str, Any]:
    summary = score(store)
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=destination.parent,
        delete=False,
    ) as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(destination)
    return summary
