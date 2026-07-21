"""Ordered test-package persistence on top of the existing hidden juries."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, is_dataclass
from hashlib import sha256
from typing import Any, Mapping, Sequence
import json
import time


SCHEMA_VERSION = 1
MAX_PACKAGE_TESTS = 50
TERMINAL_STATUSES = frozenset(
    {"complete", "request_error", "parse_error", "over_limit", "no_valid_tests"}
)


def create_package_schema(store: Any) -> None:
    store.connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS package_runs (
            policy TEXT NOT NULL,
            problem_id TEXT NOT NULL,
            status TEXT NOT NULL,
            fidelity TEXT NOT NULL,
            declared_test_count INTEGER NOT NULL,
            artifact_json TEXT NOT NULL,
            usage_json TEXT NOT NULL,
            error TEXT NOT NULL,
            created_at REAL NOT NULL,
            PRIMARY KEY (policy, problem_id)
        );
        CREATE TABLE IF NOT EXISTS package_calls (
            policy TEXT NOT NULL,
            problem_id TEXT NOT NULL,
            call_id INTEGER NOT NULL,
            stage TEXT NOT NULL,
            prompt TEXT NOT NULL,
            raw_text TEXT NOT NULL,
            message_json TEXT NOT NULL,
            usage_json TEXT NOT NULL,
            status TEXT NOT NULL,
            error TEXT NOT NULL,
            created_at REAL NOT NULL,
            PRIMARY KEY (policy, problem_id, call_id)
        );
        CREATE TABLE IF NOT EXISTS package_tests (
            policy TEXT NOT NULL,
            problem_id TEXT NOT NULL,
            test_index INTEGER NOT NULL,
            content TEXT NOT NULL,
            candidate_format TEXT NOT NULL,
            method TEXT NOT NULL,
            source_path TEXT NOT NULL,
            status TEXT NOT NULL,
            error TEXT NOT NULL,
            content_sha256 TEXT NOT NULL,
            created_at REAL NOT NULL,
            PRIMARY KEY (policy, problem_id, test_index)
        );
        CREATE TABLE IF NOT EXISTS jury_executions (
            policy TEXT NOT NULL,
            problem_id TEXT NOT NULL,
            test_index INTEGER NOT NULL,
            checked_program_id TEXT NOT NULL,
            checked_program_role TEXT NOT NULL,
            result TEXT NOT NULL,
            error TEXT NOT NULL,
            created_at REAL NOT NULL,
            PRIMARY KEY (
                policy, problem_id, test_index, checked_program_id
            )
        );
        """
    )
    store.connection.commit()


def bind_package_contract(
    store: Any,
    *,
    policy: str,
    dataset: str,
    fidelity: str,
    call_contract: str,
    deltas: Sequence[str] = (),
    max_tests: int = MAX_PACKAGE_TESTS,
) -> None:
    if fidelity not in {"native", "adapted", "unsupported"}:
        raise ValueError(f"unsupported fidelity: {fidelity}")
    if max_tests < 1 or max_tests > MAX_PACKAGE_TESTS:
        raise ValueError(f"max_tests must be in [1, {MAX_PACKAGE_TESTS}]")
    create_package_schema(store)
    store.bind_manifest(
        {
            "test_package_contract": {
                "schema_version": SCHEMA_VERSION,
                "dataset": dataset,
                "policy": policy,
                "fidelity": fidelity,
                "competitor_input": "statement_only",
                "competitor_output": "ordered_test_package",
                "call_contract": call_contract,
                "max_final_tests": max_tests,
                "overflow": "reject_package",
                "score": "whole_package_union_coverage",
                "jury_assets": "hidden",
                "deltas": list(deltas),
            }
        }
    )


def save_package_call(
    store: Any,
    *,
    policy: str,
    problem_id: str,
    call_id: int,
    stage: str,
    prompt: Any,
    raw_text: str,
    message: Any,
    usage: Any,
    status: str,
    error: str = "",
) -> None:
    create_package_schema(store)
    store.connection.execute(
        "INSERT OR REPLACE INTO package_calls VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            policy,
            problem_id,
            call_id,
            stage,
            _text(prompt),
            raw_text,
            _json(message),
            _json(usage),
            status,
            error,
            time.time(),
        ),
    )
    store.connection.commit()


def publish_package(
    store: Any,
    *,
    policy: str,
    problem_id: str,
    tests: Sequence[Any],
    fidelity: str,
    status: str = "complete",
    declared_test_count: int | None = None,
    artifact: Mapping[str, Any] | None = None,
    usage: Mapping[str, Any] | None = None,
    error: str = "",
    max_tests: int = MAX_PACKAGE_TESTS,
) -> str:
    """Persist one package and mirror its valid tests to the existing jury."""
    create_package_schema(store)
    if status not in TERMINAL_STATUSES:
        raise ValueError(f"package status is not terminal: {status}")
    normalized = [_test_record(value, index) for index, value in enumerate(tests)]
    declared = len(normalized) if declared_test_count is None else declared_test_count
    if declared < len(normalized) or declared < 0:
        raise ValueError("declared_test_count is smaller than the stored package")
    if status == "complete" and declared > max_tests:
        status = "over_limit"
        error = error or f"package declares {declared} tests; limit is {max_tests}"
        normalized = []
    elif status == "complete" and not normalized:
        status = "no_valid_tests"
        error = error or "package contains no scoreable test"

    downstream = store.connection.execute(
        """
        SELECT
          (SELECT COUNT(*) FROM materializations WHERE policy = ? AND problem_id = ?)
          + (SELECT COUNT(*) FROM executions WHERE policy = ? AND problem_id = ?)
        """,
        (policy, problem_id, policy, problem_id),
    ).fetchone()[0]
    if downstream:
        raise RuntimeError(f"cannot replace judged package {policy}/{problem_id}")

    connection = store.connection
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute(
            "DELETE FROM generations WHERE policy = ? AND task = 1 AND problem_id = ?",
            (policy, problem_id),
        )
        connection.execute(
            "DELETE FROM package_tests WHERE policy = ? AND problem_id = ?",
            (policy, problem_id),
        )
        connection.execute(
            "INSERT OR REPLACE INTO package_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                policy,
                problem_id,
                status,
                fidelity,
                declared,
                _json(artifact or {}),
                _json(usage or {}),
                error,
                time.time(),
            ),
        )
        if status == "complete":
            now = time.time()
            connection.executemany(
                "INSERT INTO package_tests VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    (
                        policy,
                        problem_id,
                        item["test_index"],
                        item["content"],
                        item["candidate_format"],
                        item["method"],
                        item["source_path"],
                        item["status"],
                        item["error"],
                        sha256(item["content"].encode("utf-8")).hexdigest(),
                        now,
                    )
                    for item in normalized
                ),
            )
            connection.executemany(
                """
                INSERT INTO generations (
                    policy, task, problem_id, submission_id, generation_id,
                    prompt, raw_text, candidate, candidate_format, message_json,
                    usage_json, status, error, created_at
                ) VALUES (?, 1, ?, '', ?, ?, '', ?, ?, ?, '{}', ?, ?, ?)
                """,
                (
                    (
                        policy,
                        problem_id,
                        item["test_index"],
                        "test-package artifact; see package_calls",
                        item["content"],
                        item["candidate_format"],
                        _json(
                            {
                                "method": item["method"],
                                "source_path": item["source_path"],
                            }
                        ),
                        item["status"],
                        item["error"],
                        now,
                    )
                    for item in normalized
                ),
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return status


def sync_generation_package(
    store: Any,
    *,
    policy: str,
    fidelity: str,
    expected_calls: int,
) -> dict[str, int]:
    """Aggregate an independent-call policy without changing prompt bytes."""
    create_package_schema(store)
    counts = {"packages": 0, "calls": 0, "tests": 0, "incomplete": 0}
    problems = store.connection.execute(
        "SELECT problem_id FROM problems ORDER BY problem_id"
    )
    for problem in problems:
        problem_id = problem["problem_id"]
        rows = list(
            store.connection.execute(
                """
                SELECT * FROM generations
                WHERE policy = ? AND task = 1 AND problem_id = ?
                ORDER BY generation_id
                """,
                (policy, problem_id),
            )
        )
        if not rows:
            continue
        connection = store.connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute(
                "DELETE FROM package_calls WHERE policy = ? AND problem_id = ?",
                (policy, problem_id),
            )
            connection.execute(
                "DELETE FROM package_tests WHERE policy = ? AND problem_id = ?",
                (policy, problem_id),
            )
            now = time.time()
            for row in rows:
                connection.execute(
                    "INSERT INTO package_calls VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        policy,
                        problem_id,
                        row["generation_id"],
                        "native_generation",
                        row["prompt"],
                        row["raw_text"],
                        row["message_json"],
                        row["usage_json"],
                        row["status"],
                        row["error"],
                        now,
                    ),
                )
                valid = row["status"] == "complete" and row["candidate"] != "ERROR"
                content = row["candidate"] if valid else ""
                connection.execute(
                    "INSERT INTO package_tests VALUES (?, ?, ?, ?, ?, '', '', ?, ?, ?, ?)",
                    (
                        policy,
                        problem_id,
                        row["generation_id"],
                        content,
                        row["candidate_format"],
                        "complete" if valid else "parse_error",
                        "" if valid else row["error"] or "invalid generation slot",
                        sha256(content.encode("utf-8")).hexdigest(),
                        now,
                    ),
                )
            terminal = len(rows) == expected_calls
            connection.execute(
                "INSERT OR REPLACE INTO package_runs VALUES (?, ?, ?, ?, ?, '{}', '{}', ?, ?)",
                (
                    policy,
                    problem_id,
                    "complete" if terminal else "request_error",
                    fidelity,
                    expected_calls,
                    "" if terminal else f"{len(rows)}/{expected_calls} calls recorded",
                    now,
                ),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        counts["packages"] += 1
        counts["calls"] += len(rows)
        counts["tests"] += sum(row["candidate"] != "ERROR" for row in rows)
        counts["incomplete"] += int(not terminal)
    return counts


def run_solver_packages(
    store: Any,
    *,
    policy: str,
    model: str,
    dataset: str,
    fidelity: str,
    call_contract: str,
    workers: int,
    retry_errors: bool = False,
    deltas: Sequence[str] = (),
) -> dict[str, int]:
    """Run one complex package-producing solver invocation per problem."""
    if workers < 1:
        raise ValueError("workers must be positive")
    from solution import load_solver
    from solution.api import (
        TestPackageCandidate,
        TestPackageInput,
        require_solver_support,
    )

    bind_package_contract(
        store,
        policy=policy,
        dataset=dataset,
        fidelity=fidelity,
        call_contract=call_contract,
        deltas=deltas,
    )
    store.bind_manifest({"model": model, "policies": [policy], "tasks": [1]})
    jobs = []
    for row in store.connection.execute(
        "SELECT problem_id, statement, metadata_json FROM problems ORDER BY problem_id"
    ):
        existing = store.connection.execute(
            "SELECT status FROM package_runs WHERE policy = ? AND problem_id = ?",
            (policy, row["problem_id"]),
        ).fetchone()
        if existing is None or (retry_errors and existing["status"] != "complete"):
            problem_metadata = json.loads(row["metadata_json"])
            public_metadata = {"benchmark": dataset}
            for key in ("time_limit_ms", "memory_limit_mb"):
                value = problem_metadata.get(key)
                if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                    public_metadata[key] = value
            jobs.append((row["problem_id"], row["statement"], public_metadata))

    def generate(
        problem_id: str, statement: str, metadata: Mapping[str, Any]
    ) -> dict[str, Any]:
        prompt: Any = ""
        try:
            solver = load_solver(policy, model)
            require_solver_support(solver, "test_package")
            session = solver.start_test_package(
                TestPackageInput(problem_id, statement, metadata)
            )
            prompt = session.initial_request
            turn = session.next()
            if not isinstance(turn.candidate, TestPackageCandidate):
                return {
                    "problem_id": problem_id,
                    "prompt": prompt,
                    "raw_text": turn.raw_text,
                    "message": turn.message,
                    "usage": turn.usage,
                    "tests": (),
                    "artifact": {},
                    "status": "parse_error",
                    "error": turn.error or "solver returned no test package",
                }
            paths = turn.candidate.artifact.get("release_test_paths", ())
            if (
                not isinstance(paths, (list, tuple))
                or len(paths) != len(turn.candidate.tests)
            ):
                paths = ("",) * len(turn.candidate.tests)
            return {
                "problem_id": problem_id,
                "prompt": prompt,
                "raw_text": turn.raw_text,
                "message": turn.message,
                "usage": turn.usage,
                "tests": tuple(
                    {
                        "content": test.content,
                        "candidate_format": test.format.value,
                        "source_path": paths[index],
                    }
                    for index, test in enumerate(turn.candidate.tests)
                ),
                "artifact": dict(turn.candidate.artifact),
                "status": "complete",
                "error": turn.error or "",
            }
        except Exception as exc:
            return {
                "problem_id": problem_id,
                "prompt": prompt,
                "raw_text": "",
                "message": {},
                "usage": {},
                "tests": (),
                "artifact": {},
                "status": "request_error",
                "error": f"{type(exc).__name__}: {exc}",
            }

    counts = {
        "scheduled": len(jobs),
        "complete": 0,
        "request_error": 0,
        "parse_error": 0,
        "over_limit": 0,
        "no_valid_tests": 0,
    }
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(generate, problem_id, statement, metadata)
            for problem_id, statement, metadata in jobs
        ]
        for future in as_completed(futures):
            record = future.result()
            save_package_call(
                store,
                policy=policy,
                problem_id=record["problem_id"],
                call_id=0,
                stage="package_pipeline",
                prompt=record["prompt"],
                raw_text=record["raw_text"],
                message=record["message"],
                usage=record["usage"],
                status=record["status"],
                error=record["error"],
            )
            status = publish_package(
                store,
                policy=policy,
                problem_id=record["problem_id"],
                tests=record["tests"],
                fidelity=fidelity,
                status=record["status"],
                declared_test_count=len(record["tests"]),
                artifact=record["artifact"],
                usage=record["usage"],
                error=record["error"],
            )
            counts[status] += 1
    return counts



def sync_jury_executions(store: Any, policy: str) -> int:
    create_package_schema(store)
    store.connection.execute(
        """
        INSERT OR REPLACE INTO jury_executions
        SELECT policy, problem_id, generation_id, checked_submission_id,
               checked_submission_type, result, error, created_at
        FROM executions
        WHERE policy = ? AND task = 1
        """,
        (policy,),
    )
    store.connection.commit()
    return store.connection.execute(
        "SELECT COUNT(*) FROM jury_executions WHERE policy = ?", (policy,)
    ).fetchone()[0]


def package_metrics(store: Any, *, dataset: str, policy: str) -> dict[str, Any]:
    """Compute ordered coverage curves and whole-package union coverage."""
    declared = store.connection.execute(
        "SELECT COALESCE(SUM(declared_test_count), 0) FROM package_runs WHERE policy = ?",
        (policy,),
    ).fetchone()[0]
    stored = store.connection.execute(
        "SELECT COUNT(*) FROM package_tests WHERE policy = ?",
        (policy,),
    ).fetchone()[0]
    if dataset == "testcase-eval":
        from itertools import groupby
        from utils.testcase_eval_benchmark import _killed, _oracle

        denominator = {
            (row["problem_id"], row["submission_id"])
            for row in store.connection.execute(
                """
                SELECT problem_id, submission_id FROM submissions
                WHERE dataset_name = 'submission_all'
                  AND submission_type = 'wrong_submission'
                """
            )
        }
        rows = store.connection.execute(
            """
            SELECT * FROM executions
            WHERE policy = ? AND task = 1
            ORDER BY problem_id, generation_id, checked_submission_type,
                     checked_submission_id
            """,
            (policy,),
        )
        first_kill = {}
        valid_tests = 0
        for (_problem_id, _index), group in groupby(
            rows, key=lambda row: (row["problem_id"], row["generation_id"])
        ):
            execution_rows = list(group)
            oracle = _oracle(execution_rows)
            valid_tests += int(oracle is not None)
            for row in execution_rows:
                if (
                    row["checked_submission_type"] == "wrong_submission"
                    and _killed(row, oracle)
                ):
                    key = (row["problem_id"], row["checked_submission_id"])
                    first_kill[key] = min(
                        row["generation_id"],
                        first_kill.get(key, row["generation_id"]),
                    )
        curves = _coverage_curves(first_kill, denominator)
        return {
            "valid_tests": valid_tests,
            "declared_tests": declared,
            "stored_tests": stored,
            "valid_rate": valid_tests / declared if declared else 0.0,
            "coverage": curves,
            "union_coverage": curves["cov@50"],
        }

    if dataset != "codecontests-plus":
        raise ValueError(f"unsupported package dataset: {dataset}")
    programs = {
        (row["problem_id"], row["submission_id"]): row["submission_type"]
        for row in store.connection.execute(
            """
            SELECT s.problem_id, s.submission_id, s.submission_type
            FROM submissions AS s
            JOIN ccplus_program_audits AS a USING(submission_id)
            WHERE a.status = 'complete'
            """
        )
    }
    valid_tests = {
        (row["problem_id"], row["generation_id"])
        for row in store.connection.execute(
            """
            SELECT problem_id, generation_id FROM ccplus_candidates
            WHERE policy = ? AND valid = 1 AND status = 'complete'
            """,
            (policy,),
        )
    }
    executions = {}
    first_kill = {}
    for row in store.connection.execute(
        """
        SELECT problem_id, generation_id, checked_submission_id, result
        FROM executions WHERE policy = ? AND task = 1
        """,
        (policy,),
    ):
        key = (row["problem_id"], row["checked_submission_id"])
        executions.setdefault(key, {})[row["generation_id"]] = row["result"]
        if (
            programs.get(key) == "wrong_submission"
            and (row["problem_id"], row["generation_id"]) in valid_tests
            and row["result"] != "accepted"
        ):
            first_kill[key] = min(
                row["generation_id"],
                first_kill.get(key, row["generation_id"]),
            )
    wrong = {key for key, role in programs.items() if role == "wrong_submission"}
    correct = {key for key, role in programs.items() if role == "right_submission"}
    correct_preserved = 0
    for key in correct:
        indices = {
            index for problem_id, index in valid_tests if problem_id == key[0]
        }
        results = executions.get(key, {})
        correct_preserved += int(
            bool(indices)
            and all(results.get(index) == "accepted" for index in indices)
        )
    curves = _coverage_curves(first_kill, wrong)
    return {
        "valid_tests": len(valid_tests),
        "declared_tests": declared,
        "stored_tests": stored,
        "valid_rate": len(valid_tests) / declared if declared else 0.0,
        "correct_programs": len(correct),
        "correct_preserved": correct_preserved,
        "correct_preservation_rate": (
            correct_preserved / len(correct) if correct else 0.0
        ),
        "coverage": curves,
        "union_coverage": curves["cov@50"],
    }


def _coverage_curves(
    first_kill: Mapping[tuple[str, str], int],
    denominator: set[tuple[str, str]],
) -> dict[str, dict[str, float | int]]:
    result = {}
    total = len(denominator)
    for count in (1, 5, 10, 20, 50):
        killed = sum(
            key in denominator and index < count
            for key, index in first_kill.items()
        )
        result[f"cov@{count}"] = {
            "killed": killed,
            "total": total,
            "ratio": killed / total if total else 0.0,
        }
    return result



def package_progress(store: Any, policy: str) -> dict[str, Any]:
    create_package_schema(store)
    status = {
        row[0]: row[1]
        for row in store.connection.execute(
            "SELECT status, COUNT(*) FROM package_runs WHERE policy = ? GROUP BY status",
            (policy,),
        )
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "policy": policy,
        "packages": sum(status.values()),
        "package_status": status,
        "calls": store.connection.execute(
            "SELECT COUNT(*) FROM package_calls WHERE policy = ?", (policy,)
        ).fetchone()[0],
        "tests": store.connection.execute(
            "SELECT COUNT(*) FROM package_tests WHERE policy = ?", (policy,)
        ).fetchone()[0],
        "jury_executions": store.connection.execute(
            "SELECT COUNT(*) FROM jury_executions WHERE policy = ?", (policy,)
        ).fetchone()[0],
    }


def _test_record(value: Any, index: int) -> dict[str, Any]:
    if is_dataclass(value):
        value = asdict(value)
    if isinstance(value, str):
        value = {"content": value}
    if not isinstance(value, Mapping):
        raise TypeError("package tests must be strings, mappings, or dataclasses")
    content = value.get("content", value.get("input"))
    if not isinstance(content, str) or not content:
        raise ValueError(f"package test {index} is empty")
    return {
        "test_index": index,
        "content": content,
        "candidate_format": str(value.get("candidate_format", value.get("format", "raw_input"))),
        "method": str(value.get("method", "")),
        "source_path": str(value.get("source_path", value.get("path", ""))),
        "status": str(value.get("status", "complete")),
        "error": str(value.get("error", "")),
    }


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _text(value: Any) -> str:
    return value if isinstance(value, str) else _json(value)
