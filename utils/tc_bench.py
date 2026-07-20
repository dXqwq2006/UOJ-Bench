"""Pinned public-snapshot adaptation of TC-Bench."""

from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
import json
import re
import sqlite3
import tempfile
import time

from utils.fault_coverage_benchmark import (
    GenerationJob,
    ProblemSpec,
    ProgramSpec,
    run_generation_jobs,
)
from utils.testcase_eval_benchmark import (
    RunStore,
    _existing_generation,
    _json,
    _usage_numbers,
    effective_model_request,
)
from utils.testcase_eval_executor import materialize_generations
from utils.testcase_eval_lightcp import _request_json


SOURCE_COMMIT = "89883430c3503f206def8c5f92d6b55774ba0472"
DATASET_NAME = "Luoberta/TC-Bench"
DATASET_REVISION = "f4d482da2d015b6342a12b9891149dcb00566c92"
PROFILE = "tc-bench"
DATASET_KEY = "tc_bench"
DEFAULT_POLICY = "testcase_eval_task1_cot"
EXPECTED_STATS = {
    "problems": 877,
    "rank_sum": 9347,
    "correct_programs": 6991,
    "wrong_programs": 9347,
    "cpp_programs": 16297,
    "c_programs": 41,
    "null_problem_ids": 1,
}
COMPILER_PROFILES = {
    "cpp": ("cpp-gnu++17", "cpp-gnu++20", "cpp-gnu++23"),
    "c": ("c-gnu11", "c-gnu17"),
}
_KILL_RESULTS = {
    "runtime_error",
    "time_limit_exceeded",
    "memory_limit_exceeded",
}
_DECIMAL = re.compile(r"^-?\d+\.\d+$")


def _load_dataset(cache_dir: str | None) -> Any:
    from datasets import load_dataset

    return load_dataset(
        DATASET_NAME,
        split="test",
        revision=DATASET_REVISION,
        cache_dir=cache_dir,
    )


def _nested_records(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, Mapping):
        keys = list(value)
        return [dict(zip(keys, items)) for items in zip(*(value[key] for key in keys))]
    return [dict(item) for item in value]


def _problem_spec(index: int, row: Mapping[str, Any]) -> ProblemSpec:
    rank = int(row["rank"])
    display_id = row.get("problem_id")
    return ProblemSpec(
        key=f"tc:{index:04d}",
        display_id=None if display_id is None else str(display_id),
        statement=str(row["description"]),
        budget=5 * rank,
        time_limit_ms=int(row["time_limit"]),
        memory_limit_mb=int(row["memory_limit"]),
        metadata={
            "row_index": index,
            "rank": rank,
            "display_problem_id": display_id,
            "time_limit_ms": int(row["time_limit"]),
            "memory_limit_mb": int(row["memory_limit"]),
            "sample_input": str(row.get("sample_input") or ""),
            "sample_output": str(row.get("sample_output") or ""),
        },
    )


def _program_specs(
    problem: ProblemSpec,
    row: Mapping[str, Any],
) -> Iterable[ProgramSpec]:
    for role, field, prefix in (
        ("right_submission", "solutions", "r"),
        ("wrong_submission", "wrong_solutions", "w"),
    ):
        for index, item in enumerate(_nested_records(row[field])):
            metadata = {"program_index": index}
            if role == "wrong_submission":
                metadata["output_str"] = str(item.get("output_str") or "")
            yield ProgramSpec(
                key=f"{problem.key}:{prefix}:{index:03d}",
                problem_key=problem.key,
                role=role,
                language=str(item["lang"]),
                source=str(item["code"]),
                metadata=metadata,
            )


def _snapshot_stats(rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    stats = {
        "problems": len(rows),
        "rank_sum": 0,
        "correct_programs": 0,
        "wrong_programs": 0,
        "cpp_programs": 0,
        "c_programs": 0,
        "null_problem_ids": 0,
    }
    for row in rows:
        rights = _nested_records(row["solutions"])
        wrongs = _nested_records(row["wrong_solutions"])
        rank = int(row["rank"])
        if rank != len(wrongs):
            raise ValueError("TC-Bench rank differs from wrong solution count")
        stats["rank_sum"] += rank
        stats["correct_programs"] += len(rights)
        stats["wrong_programs"] += len(wrongs)
        stats["null_problem_ids"] += row.get("problem_id") is None
        for program in (*rights, *wrongs):
            language = str(program["lang"])
            key = f"{language}_programs"
            if key not in stats:
                raise ValueError(f"unsupported TC-Bench language: {language}")
            stats[key] += 1
    return stats


def _create_tc_schema(store: RunStore) -> None:
    store.connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS tc_program_audits (
            submission_id TEXT PRIMARY KEY,
            compiler_profile TEXT NOT NULL,
            status TEXT NOT NULL,
            error TEXT NOT NULL,
            judge_backend TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS tc_oracles (
            policy TEXT NOT NULL,
            problem_id TEXT NOT NULL,
            generation_id INTEGER NOT NULL,
            valid INTEGER NOT NULL,
            output TEXT NOT NULL,
            error TEXT NOT NULL,
            created_at REAL NOT NULL,
            PRIMARY KEY (policy, problem_id, generation_id)
        );
        """
    )
    store.connection.commit()


def prepare_dataset(
    store: RunStore,
    *,
    cache_dir: str | None = None,
    row_indices: Sequence[int] = (),
    validate_snapshot: bool = True,
) -> dict[str, Any]:
    rows = [dict(row) for row in _load_dataset(cache_dir)]
    stats = _snapshot_stats(rows)
    if validate_snapshot and stats != EXPECTED_STATS:
        raise ValueError(f"TC-Bench public snapshot changed: {stats} != {EXPECTED_STATS}")
    selected = sorted(set(row_indices)) if row_indices else list(range(len(rows)))
    if any(index < 0 or index >= len(rows) for index in selected):
        raise ValueError("TC-Bench row index is out of range")

    _create_tc_schema(store)
    problems = [_problem_spec(index, rows[index]) for index in selected]
    programs = [
        program
        for problem, index in zip(problems, selected)
        for program in _program_specs(problem, rows[index])
    ]
    store.bind_manifest(
        {
            "benchmark": "tc-bench-public-snapshot-adapted",
            "tc_bench_source_commit": SOURCE_COMMIT,
            "dataset_revisions": {
                "tc_bench": {
                    "name": DATASET_NAME,
                    "revision": DATASET_REVISION,
                }
            },
            "tc_bench_snapshot_stats": stats,
            "selected_row_indices": selected,
            "tc_budget_multiplier": 5,
            "tc_comparator": "public-evaluator-whitespace-decimal-1e-6",
        }
    )
    store.connection.executemany(
        "INSERT OR REPLACE INTO problems VALUES (?, ?, ?)",
        (
            (problem.key, problem.statement, _json(dict(problem.metadata)))
            for problem in problems
        ),
    )
    store.connection.executemany(
        """
        INSERT OR REPLACE INTO submissions (
            dataset_name, submission_id, problem_id, submission_type,
            verdict, language, difficulty, source, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            (
                DATASET_KEY,
                program.key,
                program.problem_key,
                program.role,
                "",
                program.language,
                "",
                program.source,
                _json(dict(program.metadata)),
            )
            for program in programs
        ),
    )
    store.connection.commit()
    summary = {
        "problems": len(problems),
        "programs": len(programs),
        "correct_programs": sum(p.role == "right_submission" for p in programs),
        "wrong_programs": sum(p.role == "wrong_submission" for p in programs),
        "generations": sum(p.budget for p in problems),
        "problem_keys": [p.key for p in problems],
    }
    store.bind_manifest({"prepared_counts": summary})
    return summary


def generation_jobs(
    store: RunStore,
    *,
    policy: str,
    max_generations_per_problem: int | None = None,
    retry_errors: bool = False,
) -> list[GenerationJob]:
    jobs = []
    for row in store.connection.execute(
        "SELECT problem_id, statement, metadata_json FROM problems ORDER BY problem_id"
    ):
        metadata = json.loads(row["metadata_json"])
        budget = 5 * int(metadata["rank"])
        if max_generations_per_problem is not None:
            if max_generations_per_problem < 1:
                raise ValueError("max_generations_per_problem must be positive")
            budget = min(budget, max_generations_per_problem)
        for generation_id in range(budget):
            job = GenerationJob(
                policy,
                1,
                row["problem_id"],
                row["statement"],
                "",
                "",
                "",
                generation_id,
                metadata,
            )
            existing = _existing_generation(store, job)
            if existing is None or (
                retry_errors and existing["status"] == "request_error"
            ):
                jobs.append(job)
    return jobs


def generate(
    store: RunStore,
    *,
    model: str,
    policy: str,
    workers: int,
    max_generations_per_problem: int | None = None,
    retry_errors: bool = False,
) -> dict[str, int]:
    require_compile_audit(store)
    store.bind_manifest(
        {
            "model": model,
            "policies": [policy],
            "tasks": [1],
            "tc_max_generations_per_problem": max_generations_per_problem,
            "model_request": effective_model_request(),
        }
    )
    jobs = generation_jobs(
        store,
        policy=policy,
        max_generations_per_problem=max_generations_per_problem,
        retry_errors=retry_errors,
    )
    return run_generation_jobs(store, jobs, model=model, workers=workers)


def normalize_output(value: str) -> str:
    return " ".join(line.strip() for line in value.splitlines() if line.strip())


def outputs_equal(first: str, second: str) -> bool:
    left = normalize_output(first)
    right = normalize_output(second)
    if _DECIMAL.fullmatch(left) and _DECIMAL.fullmatch(right):
        return abs(float(left) - float(right)) <= 1e-6
    return left == right


def _backend_identity(health: Mapping[str, Any]) -> str:
    profiles = health.get("profiles")
    profile = profiles.get(PROFILE) if isinstance(profiles, Mapping) else None
    fingerprint = profile.get("fingerprint") if isinstance(profile, Mapping) else None
    if not isinstance(fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        raise RuntimeError("LightCP TC-Bench evaluator fingerprint is unavailable")
    return f"lightcp:{PROFILE}:{fingerprint}"


def preflight(base_url: str) -> dict[str, Any]:
    health = _request_json(base_url, "/health")
    if health.get("ok") is not True:
        raise RuntimeError(f"LightCP health check failed: {health}")
    _backend_identity(health)
    for language, code, source_name in (
        ("cpp-gnu++17", "#include <iostream>\nint main(){int x;std::cin>>x;std::cout<<x+1<<'\\n';}\n", "main.cpp"),
        ("c-gnu11", "#include <stdio.h>\nint main(){int x;scanf(\"%d\",&x);printf(\"%d\\n\",x+1);}\n", "main.c"),
    ):
        probe = _request_json(
            base_url,
            "/custom-test/batch",
            {
                "profile": PROFILE,
                "lang": language,
                "code": code,
                "sourceName": source_name,
                "tests": [
                    {
                        "id": "probe",
                        "stdin": "1\n",
                        "timeLimitMs": 4000,
                        "memoryLimitMb": 1280,
                    }
                ],
            },
        )
        results = probe.get("results")
        if (
            not isinstance(results, list)
            or len(results) != 1
            or results[0].get("status") != "exited"
            or results[0].get("stdout") != "2\n"
        ):
            raise RuntimeError(f"LightCP TC-Bench profile probe failed: {probe}")
    return health


def _source_name(language: str) -> str:
    return "main.cpp" if language == "cpp" else "main.c"


def _audit_one(
    base_url: str,
    backend: str,
    row: Mapping[str, Any],
) -> tuple[Any, ...]:
    language = str(row["language"])
    profiles = COMPILER_PROFILES.get(language)
    if profiles is None:
        return (
            row["submission_id"],
            "",
            "compile_error",
            f"unsupported language: {language}",
            backend,
            time.time(),
        )
    errors = []
    for compiler_profile in profiles:
        result = _request_json(
            base_url,
            "/custom-test",
            {
                "profile": PROFILE,
                "lang": compiler_profile,
                "code": row["source"],
                "sourceName": _source_name(language),
                "compileOnly": True,
            },
        )
        if result.get("status") == "compiled":
            return (
                row["submission_id"],
                compiler_profile,
                "complete",
                "",
                backend,
                time.time(),
            )
        errors.append(f"{compiler_profile}: {result.get('stderr') or result.get('message') or result}")
    return (
        row["submission_id"],
        "",
        "compile_error",
        "\n".join(errors),
        backend,
        time.time(),
    )


def audit_programs(
    store: RunStore,
    *,
    base_url: str,
    workers: int,
) -> dict[str, Any]:
    if workers < 1:
        raise ValueError("workers must be positive")
    _create_tc_schema(store)
    health = preflight(base_url)
    backend = _backend_identity(health)
    store.bind_manifest({"judge_backend": backend})
    rows = list(
        store.connection.execute(
            """
            SELECT s.submission_id, s.language, s.source
            FROM submissions AS s
            LEFT JOIN tc_program_audits AS a USING(submission_id)
            WHERE s.dataset_name = ? AND a.submission_id IS NULL
            ORDER BY s.submission_id
            """,
            (DATASET_KEY,),
        )
    )
    counts = {"scheduled": len(rows), "complete": 0, "compile_error": 0}
    statement = "INSERT OR REPLACE INTO tc_program_audits VALUES (?, ?, ?, ?, ?, ?)"
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_audit_one, base_url, backend, row) for row in rows]
        batch = []
        for completed, future in enumerate(futures, 1):
            record = future.result()
            counts[record[2]] += 1
            batch.append(record)
            if len(batch) >= 100:
                store.connection.executemany(statement, batch)
                store.connection.commit()
                batch.clear()
            if completed % 100 == 0 or completed == len(futures):
                print(
                    f"compile audit {completed}/{len(futures)} "
                    f"complete={counts['complete']} errors={counts['compile_error']}",
                    flush=True,
                )
        if batch:
            store.connection.executemany(statement, batch)
            store.connection.commit()
    return {"backend": backend, "service": health, **counts}


def require_compile_audit(store: RunStore) -> None:
    total = store.connection.execute(
        "SELECT COUNT(*) FROM submissions WHERE dataset_name = ?",
        (DATASET_KEY,),
    ).fetchone()[0]
    audited = store.connection.execute(
        "SELECT COUNT(*) FROM tc_program_audits WHERE status = 'complete'"
    ).fetchone()[0]
    errors = store.connection.execute(
        "SELECT COUNT(*) FROM tc_program_audits WHERE status != 'complete'"
    ).fetchone()[0]
    if audited != total or errors:
        raise RuntimeError(
            f"TC-Bench compile audit incomplete: {audited}/{total} passed, {errors} failed"
        )


def _normalize_result(value: Mapping[str, Any]) -> str:
    status = str(value.get("status", ""))
    if status == "exited":
        return "success_run"
    lowered = status.lower()
    if "time limit" in lowered:
        return "time_limit_exceeded"
    if "memory limit" in lowered or value.get("exitStatus") in {-9, 137}:
        return "memory_limit_exceeded"
    if status == "compile_error":
        return "compilation_error"
    return "runtime_error"


def _limits(metadata: Mapping[str, Any]) -> tuple[int, int]:
    time_limit_ms = (int(metadata["time_limit_ms"]) // 1000 + 3) * 1000
    memory_limit_mb = int(metadata["memory_limit_mb"]) * 5
    return time_limit_ms, memory_limit_mb


def _execute_program(
    base_url: str,
    policy: str,
    program: Mapping[str, Any],
    tests: Sequence[Mapping[str, Any]],
) -> list[tuple[Any, ...]]:
    if not tests:
        return []
    metadata = json.loads(program["problem_metadata_json"])
    time_limit_ms, memory_limit_mb = _limits(metadata)
    payload = {
        "profile": PROFILE,
        "lang": program["compiler_profile"],
        "code": program["source"],
        "sourceName": _source_name(program["language"]),
        "tests": [
            {
                "id": str(test["generation_id"]),
                "stdin": test["test_input"] + (
                    "\n" if test["test_input"] and not test["test_input"].endswith("\n") else ""
                ),
                "timeLimitMs": time_limit_ms,
                "memoryLimitMb": memory_limit_mb,
            }
            for test in tests
        ],
    }
    response = _request_json(base_url, "/custom-test/batch", payload)
    values = response.get("results")
    if not isinstance(values, list):
        raise RuntimeError(f"LightCP batch returned no results: {response}")
    by_id = {str(value.get("id")): value for value in values}
    records = []
    for test in tests:
        value = by_id.get(str(test["generation_id"]))
        if value is None:
            raise RuntimeError("LightCP batch omitted a requested test id")
        result = _normalize_result(value)
        if result == "compilation_error":
            raise RuntimeError("audited TC-Bench program returned a compile error")
        elapsed = float(value.get("timeNs") or 0) / 1_000_000_000
        records.append(
            (
                policy,
                1,
                program["problem_id"],
                "",
                test["generation_id"],
                program["submission_id"],
                program["submission_type"],
                "",
                program["language"],
                "",
                result,
                str(value.get("stdout") or ""),
                str(value.get("stderr") or value.get("signal") or ""),
                elapsed,
                int(value.get("memoryBytes") or 0) // 1024,
                time.time(),
            )
        )
    return records


def execute_pending(
    store: RunStore,
    *,
    base_url: str,
    workers: int,
) -> dict[str, int]:
    manifest = store.manifest()
    policies = manifest.get("policies", [])
    if len(policies) != 1:
        raise RuntimeError("TC-Bench result directory must contain exactly one policy")
    policy = str(policies[0])
    candidates: dict[str, list[dict[str, Any]]] = {}
    for row in store.connection.execute(
        """
        SELECT problem_id, generation_id, test_input
        FROM materializations
        WHERE policy = ? AND task = 1 AND status = 'complete'
        ORDER BY problem_id, generation_id
        """,
        (policy,),
    ):
        candidates.setdefault(row["problem_id"], []).append(dict(row))
    programs = list(
        store.connection.execute(
            """
            SELECT s.*, p.metadata_json AS problem_metadata_json,
                   a.compiler_profile
            FROM submissions AS s
            JOIN problems AS p USING(problem_id)
            JOIN tc_program_audits AS a USING(submission_id)
            WHERE s.dataset_name = ? AND a.status = 'complete'
            ORDER BY s.submission_id
            """,
            (DATASET_KEY,),
        )
    )
    pending_by_program = []
    for program in programs:
        existing = {
            row["generation_id"]
            for row in store.connection.execute(
                """
                SELECT generation_id FROM executions
                WHERE policy = ? AND task = 1 AND problem_id = ?
                  AND checked_submission_id = ?
                """,
                (policy, program["problem_id"], program["submission_id"]),
            )
        }
        tests = [
            test
            for test in candidates.get(program["problem_id"], [])
            if test["generation_id"] not in existing
        ]
        if tests:
            pending_by_program.append((dict(program), tests))

    statement = """
        INSERT OR REPLACE INTO executions (
            policy, task, problem_id, submission_id, generation_id,
            checked_submission_id, checked_submission_type,
            checked_submission_verdict, checked_submission_language,
            checked_submission_difficulty, result, output, error, elapsed,
            memory_kb, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    counts = {"program_batches": len(pending_by_program), "executions": 0}
    iterator = iter(pending_by_program)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending = set()

        def submit_next() -> bool:
            try:
                program, tests = next(iterator)
            except StopIteration:
                return False
            pending.add(
                pool.submit(_execute_program, base_url, policy, program, tests)
            )
            return True

        for _ in range(workers * 2):
            if not submit_next():
                break
        completed_batches = 0
        batch = []
        while pending:
            completed, pending = wait(pending, return_when=FIRST_COMPLETED)
            for future in completed:
                records = future.result()
                completed_batches += 1
                counts["executions"] += len(records)
                for record in records:
                    counts[record[10]] = counts.get(record[10], 0) + 1
                batch.extend(records)
                if len(batch) >= 1000:
                    store.connection.executemany(statement, batch)
                    store.connection.commit()
                    batch.clear()
                submit_next()
                if completed_batches % 100 == 0:
                    print(
                        f"execute {completed_batches}/{len(pending_by_program)} "
                        f"program batches, {counts['executions']} runs",
                        flush=True,
                    )
        if batch:
            store.connection.executemany(statement, batch)
            store.connection.commit()
    return counts


def _candidate_oracle(
    rows: Sequence[sqlite3.Row],
    expected_rights: int,
) -> tuple[bool, str, str]:
    rights = [row for row in rows if row["checked_submission_type"] == "right_submission"]
    if len(rights) != expected_rights:
        return False, "", "missing correct-program executions"
    if any(row["result"] != "success_run" for row in rights):
        return False, "", "a correct program did not exit successfully"
    if not rights:
        return False, "", "no correct programs"
    oracle = rights[0]["output"]
    if any(not outputs_equal(oracle, row["output"]) for row in rights[1:]):
        return False, "", "correct programs disagree"
    return True, oracle, ""


def refresh_oracles(store: RunStore) -> dict[str, int]:
    manifest = store.manifest()
    policies = manifest.get("policies", [])
    if len(policies) != 1:
        raise RuntimeError("TC-Bench result directory must contain exactly one policy")
    policy = str(policies[0])
    right_counts = {
        row["problem_id"]: row["count"]
        for row in store.connection.execute(
            """
            SELECT problem_id, COUNT(*) AS count
            FROM submissions
            WHERE dataset_name = ? AND submission_type = 'right_submission'
            GROUP BY problem_id
            """,
            (DATASET_KEY,),
        )
    }
    records = []
    for candidate in store.connection.execute(
        """
        SELECT m.problem_id, m.generation_id, m.status
        FROM materializations AS m
        WHERE m.policy = ? AND m.task = 1
        ORDER BY m.problem_id, m.generation_id
        """,
        (policy,),
    ):
        if candidate["status"] != "complete":
            valid, output, error = False, "", "invalid generated input"
        else:
            rows = list(
                store.connection.execute(
                    """
                    SELECT * FROM executions
                    WHERE policy = ? AND task = 1 AND problem_id = ?
                      AND generation_id = ?
                    ORDER BY checked_submission_id
                    """,
                    (policy, candidate["problem_id"], candidate["generation_id"]),
                )
            )
            valid, output, error = _candidate_oracle(
                rows,
                right_counts.get(candidate["problem_id"], 0),
            )
        records.append(
            (
                policy,
                candidate["problem_id"],
                candidate["generation_id"],
                int(valid),
                output,
                error,
                time.time(),
            )
        )
    store.connection.executemany(
        "INSERT OR REPLACE INTO tc_oracles VALUES (?, ?, ?, ?, ?, ?, ?)",
        records,
    )
    store.connection.commit()
    return {
        "candidates": len(records),
        "valid": sum(record[3] for record in records),
        "invalid": sum(not record[3] for record in records),
    }


def run_judge(
    store: RunStore,
    *,
    base_url: str,
    workers: int,
) -> dict[str, Any]:
    health = preflight(base_url)
    backend = _backend_identity(health)
    store.bind_manifest({"judge_backend": backend})
    require_compile_audit(store)
    materialization = materialize_generations(store.path, workers)
    execution = execute_pending(store, base_url=base_url, workers=workers)
    oracles = refresh_oracles(store)
    return {
        "backend": backend,
        "service": health,
        "materialization": materialization,
        "execution": execution,
        "oracles": oracles,
    }


def _killed(row: sqlite3.Row, oracle: str) -> bool:
    if row["result"] in _KILL_RESULTS:
        return True
    return row["result"] == "success_run" and not outputs_equal(
        row["output"], oracle
    )


def _budget(metadata: Mapping[str, Any], manifest: Mapping[str, Any]) -> int:
    budget = 5 * int(metadata["rank"])
    cap = manifest.get("tc_max_generations_per_problem")
    return min(budget, int(cap)) if cap is not None else budget


def score(store: RunStore) -> dict[str, Any]:
    manifest = store.manifest()
    policies = manifest.get("policies", [])
    if len(policies) != 1:
        raise RuntimeError("TC-Bench result directory must contain exactly one policy")
    policy = str(policies[0])
    problems = list(
        store.connection.execute(
            "SELECT problem_id, metadata_json FROM problems ORDER BY problem_id"
        )
    )
    per_problem = {}
    prefix_totals = {
        multiplier: {"pass_rate": 0.0, "hack_rate": 0.0}
        for multiplier in range(1, 6)
    }
    for problem in problems:
        metadata = json.loads(problem["metadata_json"])
        rank = int(metadata["rank"])
        budget = _budget(metadata, manifest)
        wrong_ids = {
            row["submission_id"]
            for row in store.connection.execute(
                """
                SELECT submission_id FROM submissions
                WHERE dataset_name = ? AND problem_id = ?
                  AND submission_type = 'wrong_submission'
                """,
                (DATASET_KEY, problem["problem_id"]),
            )
        }
        oracles = {
            row["generation_id"]: row
            for row in store.connection.execute(
                """
                SELECT * FROM tc_oracles
                WHERE policy = ? AND problem_id = ?
                ORDER BY generation_id
                """,
                (policy, problem["problem_id"]),
            )
        }
        results = {}
        for multiplier in range(1, 6):
            prefix = min(multiplier * rank, budget)
            valid_ids = {
                generation_id
                for generation_id, oracle in oracles.items()
                if generation_id < prefix and oracle["valid"]
            }
            hacked = set()
            for generation_id in valid_ids:
                oracle = oracles[generation_id]["output"]
                for row in store.connection.execute(
                    """
                    SELECT checked_submission_id, result, output
                    FROM executions
                    WHERE policy = ? AND task = 1 AND problem_id = ?
                      AND generation_id = ?
                      AND checked_submission_type = 'wrong_submission'
                    """,
                    (policy, problem["problem_id"], generation_id),
                ):
                    if _killed(row, oracle):
                        hacked.add(row["checked_submission_id"])
            value = {
                "tests": prefix,
                "valid": len(valid_ids),
                "pass_rate": len(valid_ids) / prefix if prefix else 0.0,
                "hacked": len(hacked),
                "wrong_programs": len(wrong_ids),
                "hack_rate": len(hacked) / len(wrong_ids) if wrong_ids else 0.0,
            }
            results[f"{multiplier}xrank"] = value
            prefix_totals[multiplier]["pass_rate"] += value["pass_rate"]
            prefix_totals[multiplier]["hack_rate"] += value["hack_rate"]
        per_problem[problem["problem_id"]] = results

    problem_count = len(problems)
    macro = {
        f"{multiplier}xrank": {
            key: total / problem_count if problem_count else 0.0
            for key, total in values.items()
        }
        for multiplier, values in prefix_totals.items()
    }
    generation_rows = list(
        store.connection.execute(
            "SELECT status, usage_json FROM generations WHERE policy = ? AND task = 1",
            (policy,),
        )
    )
    expected_generations = sum(
        _budget(json.loads(problem["metadata_json"]), manifest)
        for problem in problems
    )
    expected_executions = store.connection.execute(
        """
        SELECT COALESCE(SUM(programs.count), 0)
        FROM tc_oracles AS o
        JOIN (
            SELECT problem_id, COUNT(*) AS count
            FROM submissions WHERE dataset_name = ? GROUP BY problem_id
        ) AS programs USING(problem_id)
        WHERE o.policy = ? AND o.valid = 1
        """,
        (DATASET_KEY, policy),
    ).fetchone()[0]
    actual_executions = store.connection.execute(
        "SELECT COUNT(*) FROM executions WHERE policy = ? AND task = 1",
        (policy,),
    ).fetchone()[0]
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    for row in generation_rows:
        prompt, completion, total = _usage_numbers(json.loads(row["usage_json"]))
        usage["prompt_tokens"] += prompt
        usage["completion_tokens"] += completion
        usage["total_tokens"] += total
    complete = (
        len(generation_rows) == expected_generations
        and not any(row["status"] == "request_error" for row in generation_rows)
        and store.connection.execute(
            "SELECT COUNT(*) FROM tc_oracles WHERE policy = ?", (policy,)
        ).fetchone()[0] == expected_generations
        and actual_executions == expected_executions
    )
    return {
        "manifest": manifest,
        "complete": complete,
        "expected_generations": expected_generations,
        "actual_generations": len(generation_rows),
        "expected_executions": expected_executions,
        "actual_executions": actual_executions,
        "usage": usage,
        "macro": macro,
        "problems": per_problem,
    }


def export_jsonl(store: RunStore, directory: str | Path) -> dict[str, int]:
    manifest = store.manifest()
    policies = manifest.get("policies", [])
    if len(policies) != 1:
        raise RuntimeError("TC-Bench result directory must contain exactly one policy")
    policy = str(policies[0])
    destination = Path(directory)
    destination.mkdir(parents=True, exist_ok=True)
    counts = {}
    for problem in store.connection.execute(
        "SELECT problem_id FROM problems ORDER BY problem_id"
    ):
        rows = list(
            store.connection.execute(
                """
                SELECT m.test_input, o.output
                FROM tc_oracles AS o
                JOIN materializations AS m
                  ON m.policy = o.policy
                 AND m.problem_id = o.problem_id
                 AND m.generation_id = o.generation_id
                 AND m.task = 1
                WHERE o.policy = ? AND o.problem_id = ? AND o.valid = 1
                ORDER BY o.generation_id
                """,
                (policy, problem["problem_id"]),
            )
        )
        path = destination / f"tests-{problem['problem_id'].replace(':', '-')}.jsonl"
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=destination, delete=False
        ) as handle:
            for row in rows:
                handle.write(
                    json.dumps(
                        {"input": row["test_input"], "output": row["output"]},
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                    + "\n"
                )
            temporary = Path(handle.name)
        temporary.replace(path)
        counts[problem["problem_id"]] = len(rows)
    return counts


def write_summary(store: RunStore, path: str | Path) -> dict[str, Any]:
    summary = score(store)
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=destination.parent, delete=False
    ) as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(destination)
    return summary
