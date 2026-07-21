"""CodeContests+ Verified fault-coverage benchmark adapter."""

from __future__ import annotations

from bisect import insort
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
import hashlib
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
from utils.testcase_eval_executor import evaluation_owner, materialize_generations
from utils.testcase_eval_lightcp import _request_json


DATASET_NAME = "ByteDance-Seed/Code-Contests-Plus"
DATASET_CONFIG = "1x"
DATASET_REVISION = "96c850540fade31d384a25766461e0da6b08f5fc"
VERIFIED_THRESHOLD = 0.9
GENERATIONS_PER_PROBLEM = 20
PROGRAMS_PER_ROLE = 100
PROFILE = "codecontests-plus"
DATASET_KEY = "codecontests_plus_verified"
DEFAULT_POLICY = "testcase_eval_task1_cot"
DEFAULT_PROBLEM_SAMPLE_SEED = "codecontests-plus-verified-v1"
COMPILER_PROFILES = {
    "CPP": ("cpp-gnu++17",),
    "PY2": ("python2",),
    "PY3": ("python3",),
    "JAVA": ("java21",),
}


def _load_dataset(
    cache_dir: str | None,
    dataset_parquets: Sequence[str | Path] = (),
) -> Any:
    if dataset_parquets:
        paths = [str(Path(path).resolve()) for path in dataset_parquets]
        from datasets import load_dataset

        return load_dataset(
            "parquet",
            data_files=paths,
            split="train",
            cache_dir=cache_dir,
        )
    from datasets import load_dataset

    return load_dataset(
        DATASET_NAME,
        DATASET_CONFIG,
        split="train",
        revision=DATASET_REVISION,
        cache_dir=cache_dir,
    )


def _nested_records(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, Mapping):
        keys = list(value)
        return [dict(zip(keys, items)) for items in zip(*(value[key] for key in keys))]
    return [dict(item) for item in value]


def _verified(row: Mapping[str, Any]) -> bool:
    return (
        float(row.get("true_positive_rate") or 0) >= VERIFIED_THRESHOLD
        and float(row.get("true_negative_rate") or 0) >= VERIFIED_THRESHOLD
    )


def _problem_key(row: Mapping[str, Any]) -> str:
    return f"ccp:{row['source']}:{row['id']}"


def _problem_sample_rank(row: Mapping[str, Any], seed: str) -> tuple[bytes, str]:
    key = _problem_key(row)
    digest = hashlib.sha256(f"{seed}\0{key}".encode("utf-8")).digest()
    return digest, key


def _problem_spec(row_index: int, row: Mapping[str, Any]) -> ProblemSpec:
    display_id = f"{row['source']}:{row['id']}"
    title = str(row.get("title") or "").strip()
    description = str(row["description"])
    return ProblemSpec(
        key=_problem_key(row),
        display_id=display_id,
        statement=f"{title}\n\n{description}" if title else description,
        budget=GENERATIONS_PER_PROBLEM,
        time_limit_ms=int(row["time_limit"]),
        memory_limit_mb=int(row["memory_limit"]),
        metadata={
            "row_index": row_index,
            "display_problem_id": display_id,
            "source": str(row["source"]),
            "source_problem_id": str(row["id"]),
            "time_limit_ms": int(row["time_limit"]),
            "memory_limit_mb": int(row["memory_limit"]),
            "published_true_positive_rate": float(row["true_positive_rate"]),
            "published_true_negative_rate": float(row["true_negative_rate"]),
        },
    )


def _selected_records(
    row: Mapping[str, Any],
    field: str,
) -> list[tuple[int, dict[str, Any]]]:
    supported = [
        (index, item)
        for index, item in enumerate(_nested_records(row[field]))
        if str(item["language"]).upper() in COMPILER_PROFILES
    ]
    supported.sort(
        key=lambda value: hashlib.sha256(
            f"{value[0]}\0{value[1]['language']}\0{value[1]['code']}".encode()
        ).digest()
    )
    return supported[:PROGRAMS_PER_ROLE]


def _program_specs(
    problem: ProblemSpec,
    row: Mapping[str, Any],
) -> Iterable[ProgramSpec]:
    for role, field, prefix in (
        ("right_submission", "correct_submissions", "r"),
        ("wrong_submission", "incorrect_submissions", "w"),
    ):
        for selected_index, (source_index, item) in enumerate(
            _selected_records(row, field)
        ):
            yield ProgramSpec(
                key=f"{problem.key}:{prefix}:{selected_index:03d}",
                problem_key=problem.key,
                role=role,
                language=str(item["language"]).upper(),
                source=str(item["code"]),
                metadata={"source_program_index": source_index},
            )


def _snapshot_stats(rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    stats = {
        "problems": len(rows),
        "available_correct_programs": 0,
        "available_wrong_programs": 0,
        "correct_programs": 0,
        "wrong_programs": 0,
        "ignored_unknown_language_programs": 0,
    }
    for row in rows:
        if not _verified(row):
            raise ValueError("unverified CodeContests+ row selected")
        available_rights = _nested_records(row["correct_submissions"])
        available_wrongs = _nested_records(row["incorrect_submissions"])
        rights = [item for _, item in _selected_records(row, "correct_submissions")]
        wrongs = [item for _, item in _selected_records(row, "incorrect_submissions")]
        if not rights or not wrongs:
            raise ValueError(
                "CodeContests+ row must contain supported correct and incorrect submissions"
            )
        if not str(row.get("validator") or "").strip():
            raise ValueError("CodeContests+ row has no input validator")
        if not str(row.get("checker") or "").strip():
            raise ValueError("CodeContests+ row has no output checker")
        stats["available_correct_programs"] += len(available_rights)
        stats["available_wrong_programs"] += len(available_wrongs)
        stats["correct_programs"] += len(rights)
        stats["wrong_programs"] += len(wrongs)
        stats["ignored_unknown_language_programs"] += sum(
            str(program["language"]).upper() not in COMPILER_PROFILES
            for program in (*available_rights, *available_wrongs)
        )
        for program in (*rights, *wrongs):
            language = str(program["language"]).upper()
            key = f"{language.lower()}_programs"
            stats[key] = stats.get(key, 0) + 1
    return stats


def _create_ccplus_schema(store: RunStore) -> None:
    store.connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS ccplus_problem_assets (
            problem_id TEXT PRIMARY KEY,
            validator TEXT NOT NULL,
            checker TEXT NOT NULL,
            reference_submission_id TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS ccplus_program_audits (
            submission_id TEXT PRIMARY KEY,
            compiler_profile TEXT NOT NULL,
            status TEXT NOT NULL,
            error TEXT NOT NULL,
            judge_backend TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS ccplus_candidates (
            policy TEXT NOT NULL,
            problem_id TEXT NOT NULL,
            generation_id INTEGER NOT NULL,
            valid INTEGER NOT NULL,
            status TEXT NOT NULL,
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
    dataset_parquets: Sequence[str | Path] = (),
    problem_ids: Sequence[str] = (),
    smoke_problems: int | None = None,
    sample_problems: int | None = None,
    sample_seed: str = DEFAULT_PROBLEM_SAMPLE_SEED,
) -> dict[str, Any]:
    if smoke_problems is not None and smoke_problems < 1:
        raise ValueError("smoke_problems must be positive")
    if sample_problems is not None and sample_problems < 1:
        raise ValueError("sample_problems must be positive")
    if sample_problems is not None and (smoke_problems is not None or problem_ids):
        raise ValueError(
            "sample_problems cannot be combined with smoke_problems or problem_ids"
        )
    if sample_problems is not None and not sample_seed:
        raise ValueError("sample_seed must not be empty")
    wanted = {str(value) for value in problem_ids}
    selected = []
    sampled = []
    verified_population = 0
    for row_index, value in enumerate(_load_dataset(cache_dir, dataset_parquets)):
        row = dict(value)
        if not _verified(row):
            continue
        display_id = f"{row['source']}:{row['id']}"
        if wanted and display_id not in wanted and _problem_key(row) not in wanted:
            continue
        verified_population += 1
        if sample_problems is not None:
            ranked_row = (_problem_sample_rank(row, sample_seed), row_index, row)
            if len(sampled) < sample_problems or ranked_row[0] < sampled[-1][0]:
                insort(sampled, ranked_row)
                if len(sampled) > sample_problems:
                    sampled.pop()
            continue
        selected.append((row_index, row))
        if smoke_problems is not None and not wanted and len(selected) >= smoke_problems:
            break
    if sample_problems is not None:
        if sample_problems > verified_population:
            raise ValueError(
                f"sample_problems={sample_problems} exceeds the Verified population "
                f"of {verified_population}"
            )
        selected = [(row_index, row) for _rank, row_index, row in sampled]
    row_indices = [row_index for row_index, _row in selected]
    rows = [row for _row_index, row in selected]
    if wanted:
        found = {f"{row['source']}:{row['id']}" for row in rows} | {
            _problem_key(row) for row in rows
        }
        missing = sorted(wanted - found)
        if missing:
            raise ValueError(f"unknown verified problem ids: {', '.join(missing)}")
    stats = _snapshot_stats(rows)
    _create_ccplus_schema(store)
    problems = [_problem_spec(index, row) for index, row in zip(row_indices, rows)]
    programs = [
        program
        for problem, row in zip(problems, rows)
        for program in _program_specs(problem, row)
    ]
    local_artifacts = []
    for value in dataset_parquets:
        path = Path(value).resolve()
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        local_artifacts.append({"path": str(path), "sha256": digest})
    store.bind_manifest(
        {
            "benchmark": "codecontests-plus-verified-adapted",
            "dataset_revisions": {
                DATASET_KEY: {
                    "name": DATASET_NAME,
                    "config": DATASET_CONFIG,
                    "revision": DATASET_REVISION,
                    "local_artifacts": local_artifacts,
                }
            },
            "verified_thresholds": {
                "true_positive_rate": VERIFIED_THRESHOLD,
                "true_negative_rate": VERIFIED_THRESHOLD,
            },
            "ccplus_snapshot_stats": stats,
            "selected_row_indices": row_indices,
            "generations_per_problem": GENERATIONS_PER_PROBLEM,
            "program_sampling": {
                "method": "sha256-order",
                "per_role_limit": PROGRAMS_PER_ROLE,
                "supported_dataset_languages": sorted(COMPILER_PROFILES),
                "unknown_language_policy": "exclude",
            },
            "output_judging": "published-checker",
        }
    )
    if sample_problems is not None:
        store.bind_manifest(
            {
                "problem_sampling": {
                    "method": "sha256-minhash",
                    "seed": sample_seed,
                    "verified_population": verified_population,
                    "sample_size": len(rows),
                    "selected_problem_keys": [_problem_key(row) for row in rows],
                }
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
        "INSERT OR REPLACE INTO ccplus_problem_assets VALUES (?, ?, ?, ?)",
        (
            (
                problem.key,
                str(row["validator"]),
                str(row["checker"]),
                f"{problem.key}:r:000",
            )
            for problem, row in zip(problems, rows)
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
        "generations": GENERATIONS_PER_PROBLEM * len(problems),
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
        budget = GENERATIONS_PER_PROBLEM
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
            "ccplus_max_generations_per_problem": max_generations_per_problem,
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


def _backend_identity(health: Mapping[str, Any]) -> str:
    profiles = health.get("profiles")
    profile = profiles.get(PROFILE) if isinstance(profiles, Mapping) else None
    fingerprint = profile.get("fingerprint") if isinstance(profile, Mapping) else None
    if not isinstance(fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        raise RuntimeError("LightCP CodeContests+ evaluator fingerprint is unavailable")
    return f"lightcp:{PROFILE}:{fingerprint}"


def preflight(base_url: str) -> dict[str, Any]:
    health = _request_json(base_url, "/health")
    if health.get("ok") is not True:
        raise RuntimeError(f"LightCP health check failed: {health}")
    _backend_identity(health)
    for language, code, source_name in (
        ("cpp-gnu++17", "#include <iostream>\nint main(){int x;std::cin>>x;std::cout<<x+1<<'\\n';}\n", "main.cpp"),
        ("python2", "print int(raw_input()) + 1\n", "main.py"),
        ("python3", "print(int(input()) + 1)\n", "main.py"),
        ("java21", "import java.util.*; class Main { public static void main(String[] a) { Scanner s=new Scanner(System.in); System.out.println(s.nextInt()+1); }}\n", "Main.java"),
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
            raise RuntimeError(f"LightCP CodeContests+ profile probe failed: {probe}")

    validator = _request_json(
        base_url,
        "/custom-test/batch",
        {
            "profile": PROFILE,
            "lang": "cpp-gnu++17",
            "code": '#include "testlib.h"\nint main(int c,char**v){registerValidation(c,v);inf.readInt(1,1);inf.readEoln();inf.readEof();}\n',
            "sourceName": "main.cpp",
            "tests": [
                {"id": "valid", "stdin": "1\n", "timeLimitMs": 2000, "memoryLimitMb": 256},
                {"id": "invalid", "stdin": "2\n", "timeLimitMs": 2000, "memoryLimitMb": 256},
            ],
        },
    )
    values = {str(value.get("id")): value for value in validator.get("results", [])}
    if values.get("valid", {}).get("status") != "exited" or values.get("invalid", {}).get("status") == "exited":
        raise RuntimeError(f"LightCP CodeContests+ testlib probe failed: {validator}")
    return health


def _source_name(language: str) -> str:
    return {
        "CPP": "main.cpp",
        "PY2": "main.py",
        "PY3": "main.py",
        "JAVA": "Main.java",
    }[language]


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
    _create_ccplus_schema(store)
    health = preflight(base_url)
    backend = _backend_identity(health)
    store.bind_manifest(
        {"judge_backend": backend, "evaluation_owner": evaluation_owner()}
    )
    rows = list(
        store.connection.execute(
            """
            SELECT s.submission_id, s.language, s.source
            FROM submissions AS s
            LEFT JOIN ccplus_program_audits AS a USING(submission_id)
            WHERE s.dataset_name = ? AND a.submission_id IS NULL
            ORDER BY s.submission_id
            """,
            (DATASET_KEY,),
        )
    )
    counts = {"scheduled": len(rows), "complete": 0, "compile_error": 0}
    statement = "INSERT OR REPLACE INTO ccplus_program_audits VALUES (?, ?, ?, ?, ?, ?)"
    iterator = iter(rows)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending = set()

        def submit_next() -> bool:
            try:
                row = next(iterator)
            except StopIteration:
                return False
            pending.add(pool.submit(_audit_one, base_url, backend, row))
            return True

        for _ in range(workers * 2):
            if not submit_next():
                break

        batch = []
        completed_count = 0
        while pending:
            completed, pending = wait(pending, return_when=FIRST_COMPLETED)
            for future in completed:
                record = future.result()
                completed_count += 1
                counts[record[2]] += 1
                batch.append(record)
                if len(batch) >= 100:
                    store.connection.executemany(statement, batch)
                    store.connection.commit()
                    batch.clear()
                submit_next()
                if completed_count % 100 == 0 or completed_count == len(rows):
                    print(
                        f"compile audit {completed_count}/{len(rows)} "
                        f"complete={counts['complete']} "
                        f"errors={counts['compile_error']}",
                        flush=True,
                    )
        if batch:
            store.connection.executemany(statement, batch)
            store.connection.commit()
    previous = store.manifest().get("ccplus_compile_audit")
    if previous is None:
        store.bind_manifest({"ccplus_compile_audit": counts})
    else:
        counts = previous
    return {"backend": backend, "service": health, **counts}


def require_compile_audit(store: RunStore) -> None:
    total = store.connection.execute(
        "SELECT COUNT(*) FROM submissions WHERE dataset_name = ?",
        (DATASET_KEY,),
    ).fetchone()[0]
    audited = store.connection.execute(
        "SELECT COUNT(*) FROM ccplus_program_audits"
    ).fetchone()[0]
    if audited != total:
        raise RuntimeError(
            f"CodeContests+ compile audit incomplete: {audited}/{total} programs audited"
        )
    missing_references = store.connection.execute(
        """
        SELECT COUNT(*) FROM problems AS p
        WHERE NOT EXISTS (
            SELECT 1 FROM submissions AS s
            JOIN ccplus_program_audits AS a USING(submission_id)
            WHERE s.problem_id = p.problem_id
              AND s.submission_type = 'right_submission'
              AND a.status = 'complete'
        )
        """
    ).fetchone()[0]
    if missing_references:
        raise RuntimeError(
            f"CodeContests+ compile audit left {missing_references} problems without an oracle"
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
    return max(100, int(metadata["time_limit_ms"])), max(
        16, int(metadata["memory_limit_mb"])
    )


def _batch_results(base_url: str, payload: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    response = _request_json(base_url, "/custom-test/batch", payload)
    values = response.get("results")
    if not isinstance(values, list):
        raise RuntimeError(f"LightCP batch returned no results: {response}")
    return {str(value.get("id")): value for value in values}


def _validate_problem(
    base_url: str,
    policy: str,
    problem: Mapping[str, Any],
    tests: Sequence[Mapping[str, Any]],
) -> list[tuple[Any, ...]]:
    metadata = json.loads(problem["metadata_json"])
    time_limit_ms, memory_limit_mb = _limits(metadata)
    values = _batch_results(
        base_url,
        {
            "profile": PROFILE,
            "lang": "cpp-gnu++17",
            "code": problem["validator"],
            "sourceName": "main.cpp",
            "tests": [
                {
                    "id": str(test["generation_id"]),
                    "stdin": test["test_input"],
                    "timeLimitMs": min(5000, time_limit_ms),
                    "memoryLimitMb": memory_limit_mb,
                }
                for test in tests
            ],
        },
    )
    records = []
    for test in tests:
        value = values.get(str(test["generation_id"]))
        if value is None:
            raise RuntimeError("LightCP validator batch omitted a requested test id")
        if value.get("status") == "compile_error":
            raise RuntimeError(f"CodeContests+ validator did not compile: {value}")
        valid = value.get("status") == "exited"
        records.append(
            (
                policy,
                problem["problem_id"],
                test["generation_id"],
                int(valid),
                "validator_accepted" if valid else "validator_rejected",
                "",
                "" if valid else str(value.get("stderr") or value.get("signal") or "rejected"),
                time.time(),
            )
        )
    return records


def validate_candidates(
    store: RunStore,
    *,
    base_url: str,
    workers: int,
) -> dict[str, int]:
    manifest = store.manifest()
    policies = manifest.get("policies", [])
    if len(policies) != 1:
        raise RuntimeError("CodeContests+ result directory must contain exactly one policy")
    policy = str(policies[0])
    statement = "INSERT OR REPLACE INTO ccplus_candidates VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
    invalid_records = []
    pending = []
    for problem in store.connection.execute(
        """
        SELECT p.problem_id, p.metadata_json, a.validator
        FROM problems AS p JOIN ccplus_problem_assets AS a USING(problem_id)
        ORDER BY p.problem_id
        """
    ):
        existing = {
            row["generation_id"]
            for row in store.connection.execute(
                "SELECT generation_id FROM ccplus_candidates WHERE policy = ? AND problem_id = ?",
                (policy, problem["problem_id"]),
            )
        }
        tests = []
        for row in store.connection.execute(
            """
            SELECT generation_id, test_input, status, error FROM materializations
            WHERE policy = ? AND task = 1 AND problem_id = ? ORDER BY generation_id
            """,
            (policy, problem["problem_id"]),
        ):
            if row["generation_id"] in existing:
                continue
            if row["status"] != "complete":
                invalid_records.append(
                    (
                        policy,
                        problem["problem_id"],
                        row["generation_id"],
                        0,
                        "materialization_error",
                        "",
                        row["error"] or "invalid generated output",
                        time.time(),
                    )
                )
            else:
                tests.append(dict(row))
        if tests:
            pending.append((dict(problem), tests))
    if invalid_records:
        store.connection.executemany(statement, invalid_records)
        store.connection.commit()

    counts = {"candidates": len(invalid_records), "valid": 0, "invalid": len(invalid_records)}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(_validate_problem, base_url, policy, problem, tests)
            for problem, tests in pending
        ]
        for future in futures:
            records = future.result()
            store.connection.executemany(statement, records)
            store.connection.commit()
            counts["candidates"] += len(records)
            counts["valid"] += sum(record[3] for record in records)
            counts["invalid"] += sum(not record[3] for record in records)
    return counts


def _run_program(
    base_url: str,
    program: Mapping[str, Any],
    tests: Sequence[Mapping[str, Any]],
) -> dict[str, Mapping[str, Any]]:
    metadata = json.loads(program["problem_metadata_json"])
    time_limit_ms, memory_limit_mb = _limits(metadata)
    return _batch_results(
        base_url,
        {
            "profile": PROFILE,
            "lang": program["compiler_profile"],
            "code": program["source"],
            "sourceName": _source_name(program["language"]),
            "tests": [
                {
                    "id": str(test["generation_id"]),
                    "stdin": test["test_input"],
                    "timeLimitMs": time_limit_ms,
                    "memoryLimitMb": memory_limit_mb,
                }
                for test in tests
            ],
        },
    )


def populate_oracles(
    store: RunStore,
    *,
    base_url: str,
    workers: int,
) -> dict[str, int]:
    manifest = store.manifest()
    policy = str(manifest["policies"][0])
    pending = []
    for program in store.connection.execute(
        """
        WITH reference_programs AS (
            SELECT s.problem_id, MIN(s.submission_id) AS submission_id
            FROM submissions AS s
            JOIN ccplus_program_audits AS a USING(submission_id)
            WHERE s.dataset_name = ?
              AND s.submission_type = 'right_submission'
              AND a.status = 'complete'
            GROUP BY s.problem_id
        )
        SELECT s.*, p.metadata_json AS problem_metadata_json, a.compiler_profile
        FROM reference_programs AS r
        JOIN submissions AS s
          ON s.dataset_name = ? AND s.submission_id = r.submission_id
        JOIN problems AS p USING(problem_id)
        JOIN ccplus_program_audits AS a USING(submission_id)
        ORDER BY s.problem_id
        """,
        (DATASET_KEY, DATASET_KEY),
    ):
        tests = list(
            store.connection.execute(
                """
                SELECT c.generation_id, m.test_input
                FROM ccplus_candidates AS c JOIN materializations AS m
                  ON m.policy = c.policy AND m.task = 1
                 AND m.problem_id = c.problem_id AND m.generation_id = c.generation_id
                WHERE c.policy = ? AND c.problem_id = ?
                  AND c.status = 'validator_accepted'
                ORDER BY c.generation_id
                """,
                (policy, program["problem_id"]),
            )
        )
        if tests:
            pending.append((dict(program), [dict(test) for test in tests]))

    counts = {"scheduled": sum(len(tests) for _, tests in pending), "complete": 0, "oracle_error": 0}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_run_program, base_url, program, tests): (program, tests)
            for program, tests in pending
        }
        for future, (program, tests) in futures.items():
            values = future.result()
            for test in tests:
                value = values.get(str(test["generation_id"]))
                if value is None:
                    raise RuntimeError("LightCP oracle batch omitted a requested test id")
                success = _normalize_result(value) == "success_run"
                status = "complete" if success else "oracle_error"
                store.connection.execute(
                    """
                    UPDATE ccplus_candidates SET valid = ?, status = ?, output = ?, error = ?, created_at = ?
                    WHERE policy = ? AND problem_id = ? AND generation_id = ?
                    """,
                    (
                        int(success),
                        status,
                        str(value.get("stdout") or "") if success else "",
                        "" if success else str(value.get("stderr") or value.get("signal") or "oracle failed"),
                        time.time(),
                        policy,
                        program["problem_id"],
                        test["generation_id"],
                    ),
                )
                counts[status] += 1
            store.connection.commit()
    return counts


def _execute_program(
    base_url: str,
    policy: str,
    program: Mapping[str, Any],
    tests: Sequence[Mapping[str, Any]],
) -> list[tuple[Any, ...]]:
    if not tests:
        return []
    by_id = _run_program(base_url, program, tests)
    checker_tests = []
    for test in tests:
        value = by_id.get(str(test["generation_id"]))
        if value is None:
            raise RuntimeError("LightCP batch omitted a requested test id")
        if _normalize_result(value) == "success_run":
            checker_tests.append(
                {
                    "id": str(test["generation_id"]),
                    "stdin": "",
                    "argv": ["input.txt", "output.txt", "answer.txt"],
                    "copyInFiles": {
                        "input.txt": test["test_input"],
                        "output.txt": str(value.get("stdout") or ""),
                        "answer.txt": test["oracle_output"],
                    },
                    "timeLimitMs": 5000,
                    "memoryLimitMb": 512,
                }
            )
    checker_values = (
        _batch_results(
            base_url,
            {
                "profile": PROFILE,
                "lang": "cpp-gnu++17",
                "code": program["checker"],
                "sourceName": "main.cpp",
                "tests": checker_tests,
            },
        )
        if checker_tests
        else {}
    )
    records = []
    for test in tests:
        value = by_id.get(str(test["generation_id"]))
        if value is None:
            raise RuntimeError("LightCP batch omitted a requested test id")
        result = _normalize_result(value)
        if result == "compilation_error":
            raise RuntimeError("audited CodeContests+ program returned a compile error")
        if result == "success_run":
            checked = checker_values.get(str(test["generation_id"]))
            if checked is None:
                raise RuntimeError("LightCP checker batch omitted a requested test id")
            exit_status = checked.get("exitStatus")
            if checked.get("status") == "exited":
                result = "accepted"
            elif exit_status in {1, 2}:
                result = "wrong_answer"
            else:
                raise RuntimeError(f"CodeContests+ checker failed: {checked}")
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
        raise RuntimeError("CodeContests+ result directory must contain exactly one policy")
    policy = str(policies[0])
    candidates: dict[str, list[dict[str, Any]]] = {}
    for row in store.connection.execute(
        """
        SELECT c.problem_id, c.generation_id, m.test_input, c.output AS oracle_output
        FROM ccplus_candidates AS c JOIN materializations AS m
          ON m.policy = c.policy AND m.task = 1
         AND m.problem_id = c.problem_id AND m.generation_id = c.generation_id
        WHERE c.policy = ? AND c.status = 'complete'
        ORDER BY c.problem_id, c.generation_id
        """,
        (policy,),
    ):
        candidates.setdefault(row["problem_id"], []).append(dict(row))
    programs = list(
        store.connection.execute(
            """
            SELECT s.*, p.metadata_json AS problem_metadata_json,
                   a.compiler_profile, asset.checker
            FROM submissions AS s
            JOIN problems AS p USING(problem_id)
            JOIN ccplus_program_audits AS a USING(submission_id)
            JOIN ccplus_problem_assets AS asset USING(problem_id)
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


def run_judge(
    store: RunStore,
    *,
    base_url: str,
    workers: int,
) -> dict[str, Any]:
    health = preflight(base_url)
    backend = _backend_identity(health)
    store.bind_manifest(
        {"judge_backend": backend, "evaluation_owner": evaluation_owner()}
    )
    require_compile_audit(store)
    materialization = materialize_generations(store.path, workers)
    validation = validate_candidates(store, base_url=base_url, workers=workers)
    oracles = populate_oracles(store, base_url=base_url, workers=workers)
    execution = execute_pending(store, base_url=base_url, workers=workers)
    return {
        "backend": backend,
        "service": health,
        "materialization": materialization,
        "validation": validation,
        "execution": execution,
        "oracles": oracles,
    }


def _accepted(row: sqlite3.Row) -> bool:
    return row["result"] == "accepted"


def _budget(metadata: Mapping[str, Any], manifest: Mapping[str, Any]) -> int:
    budget = GENERATIONS_PER_PROBLEM
    cap = manifest.get("ccplus_max_generations_per_problem")
    return min(budget, int(cap)) if cap is not None else budget


def score(store: RunStore) -> dict[str, Any]:
    manifest = store.manifest()
    policies = manifest.get("policies", [])
    if len(policies) != 1:
        raise RuntimeError("CodeContests+ result directory must contain exactly one policy")
    policy = str(policies[0])
    problems = list(
        store.connection.execute(
            "SELECT problem_id, metadata_json FROM problems ORDER BY problem_id"
        )
    )
    per_problem = {}
    macro_totals = {
        "valid_rate": 0.0,
        "true_positive_rate": 0.0,
        "true_negative_rate": 0.0,
    }
    for problem in problems:
        metadata = json.loads(problem["metadata_json"])
        budget = _budget(metadata, manifest)
        programs = {
            row["submission_id"]: row["submission_type"]
            for row in store.connection.execute(
                """
                SELECT s.submission_id, s.submission_type FROM submissions AS s
                JOIN ccplus_program_audits AS a USING(submission_id)
                WHERE s.dataset_name = ? AND s.problem_id = ? AND a.status = 'complete'
                """,
                (DATASET_KEY, problem["problem_id"]),
            )
        }
        candidates = list(
            store.connection.execute(
                """
                SELECT generation_id, valid, status FROM ccplus_candidates
                WHERE policy = ? AND problem_id = ? ORDER BY generation_id
                """,
                (policy, problem["problem_id"]),
            )
        )
        valid_ids = {
            row["generation_id"]
            for row in candidates
            if row["valid"] and row["status"] == "complete"
        }
        execution_rows = list(
            store.connection.execute(
                """
                SELECT generation_id, checked_submission_id, result FROM executions
                WHERE policy = ? AND task = 1 AND problem_id = ?
                """,
                (policy, problem["problem_id"]),
            )
        )
        by_program: dict[str, dict[int, sqlite3.Row]] = {}
        for row in execution_rows:
            by_program.setdefault(row["checked_submission_id"], {})[
                row["generation_id"]
            ] = row

        correct_ids = {
            submission_id
            for submission_id, role in programs.items()
            if role == "right_submission"
        }
        wrong_ids = set(programs) - correct_ids

        def accepts_suite(submission_id: str) -> bool:
            results = by_program.get(submission_id, {})
            return bool(valid_ids) and all(
                generation_id in results and _accepted(results[generation_id])
                for generation_id in valid_ids
            )

        correct_accepted = sum(accepts_suite(value) for value in correct_ids)
        def rejects_suite(submission_id: str) -> bool:
            results = by_program.get(submission_id, {})
            return bool(valid_ids) and all(
                generation_id in results for generation_id in valid_ids
            ) and any(
                not _accepted(results[generation_id])
                for generation_id in valid_ids
            )

        wrong_rejected = sum(rejects_suite(value) for value in wrong_ids)
        value = {
            "tests": budget,
            "validated": len(candidates),
            "valid": len(valid_ids),
            "valid_rate": len(valid_ids) / budget if budget else 0.0,
            "correct_programs": len(correct_ids),
            "correct_accepted": correct_accepted,
            "true_positive_rate": (
                correct_accepted / len(correct_ids) if correct_ids else 0.0
            ),
            "wrong_programs": len(wrong_ids),
            "wrong_rejected": wrong_rejected,
            "true_negative_rate": (
                wrong_rejected / len(wrong_ids) if wrong_ids else 0.0
            ),
            "published_true_positive_rate": metadata[
                "published_true_positive_rate"
            ],
            "published_true_negative_rate": metadata[
                "published_true_negative_rate"
            ],
        }
        per_problem[problem["problem_id"]] = value
        for key in macro_totals:
            macro_totals[key] += value[key]

    problem_count = len(problems)
    macro = {
        key: total / problem_count if problem_count else 0.0
        for key, total in macro_totals.items()
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
    candidate_count = store.connection.execute(
        "SELECT COUNT(*) FROM ccplus_candidates WHERE policy = ?", (policy,)
    ).fetchone()[0]
    pending_candidates = store.connection.execute(
        "SELECT COUNT(*) FROM ccplus_candidates WHERE policy = ? AND status = 'validator_accepted'",
        (policy,),
    ).fetchone()[0]
    expected_executions = store.connection.execute(
        """
        SELECT COALESCE(SUM(programs.count), 0)
        FROM ccplus_candidates AS c
        JOIN (
            SELECT s.problem_id, COUNT(*) AS count
            FROM submissions AS s JOIN ccplus_program_audits AS a USING(submission_id)
            WHERE s.dataset_name = ? AND a.status = 'complete'
            GROUP BY s.problem_id
        ) AS programs USING(problem_id)
        WHERE c.policy = ? AND c.valid = 1 AND c.status = 'complete'
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
        and candidate_count == expected_generations
        and pending_candidates == 0
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
        raise RuntimeError("CodeContests+ result directory must contain exactly one policy")
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
                FROM ccplus_candidates AS o
                JOIN materializations AS m
                  ON m.policy = o.policy
                 AND m.problem_id = o.problem_id
                 AND m.generation_id = o.generation_id
                 AND m.task = 1
                WHERE o.policy = ? AND o.problem_id = ?
                  AND o.valid = 1 AND o.status = 'complete'
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
