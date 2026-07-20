"""LightCPVerifier execution backend for the pinned TestCase-Eval benchmark."""

from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping
import json
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.request

from utils.testcase_eval_benchmark import encode_execution_output
from utils.testcase_eval_executor import (
    LANGUAGES,
    OUTPUT_LIMIT_BYTES,
    _connect,
    _execution_rows,
    _java_source,
    _program_key,
    _submission_rows,
    bind_judge_backend,
    materialize_generations,
)


PROFILE = "testcase-eval"
REQUEST_TIMEOUT_SECONDS = 300
_MEMORY_ERROR_MARKERS = (
    "bad_alloc",
    "out of memory",
    "memory allocation",
    "cannot allocate",
    "memoryerror",
)


def _request_json(
    base_url: str,
    path: str,
    payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}{path}"
    data = None
    headers: dict[str, str] = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(
            request,
            timeout=REQUEST_TIMEOUT_SECONDS,
        ) as response:
            value = json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"LightCP HTTP {exc.code}: {detail}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"LightCP returned {type(value).__name__}, expected object")
    return value


def _backend_identity(health: Mapping[str, Any]) -> str:
    profiles = health.get("profiles")
    profile = profiles.get(PROFILE) if isinstance(profiles, Mapping) else None
    fingerprint = profile.get("fingerprint") if isinstance(profile, Mapping) else None
    if not isinstance(fingerprint, str) or re.fullmatch(r"[0-9a-f]{64}", fingerprint) is None:
        raise RuntimeError("LightCP TestCase-Eval evaluator fingerprint is unavailable")
    return f"lightcp:{PROFILE}:{fingerprint}"


def preflight(base_url: str) -> dict[str, Any]:
    health = _request_json(base_url, "/health")
    if health.get("ok") is not True:
        raise RuntimeError(f"LightCP health check failed: {health}")
    _backend_identity(health)
    probe = _request_json(
        base_url,
        "/custom-test",
        {
            "profile": PROFILE,
            "lang": "C++17 (GCC 7-32)",
            "code": (
                "#include <iostream>\n"
                "int main(){long long x;std::cin>>x;std::cout<<x+1<<'\\n';}\n"
            ),
            "sourceName": "main.cpp",
            "stdin": "1\n",
        },
    )
    if probe.get("status") != "exited" or probe.get("stdout") != "2\n":
        raise RuntimeError(f"LightCP TestCase-Eval profile probe failed: {probe}")
    return health


def _program_request(language: str, source: str) -> dict[str, str]:
    if language not in LANGUAGES:
        raise ValueError(f"unsupported TestCase-Eval language: {language}")
    request = {"profile": PROFILE, "lang": language, "code": source}
    if language.startswith("Java"):
        cache_key = _program_key(language, source)
        class_name = "Tmp" + cache_key[:16]
        request["code"] = _java_source(source, class_name)
        request["sourceName"] = f"{class_name}.java"
    elif language.startswith("C++"):
        request["sourceName"] = "main.cpp"
    else:
        request["sourceName"] = "main.py"
    return request


def _load_programs(
    database: str | Path,
) -> tuple[dict[tuple[str, str], dict[str, Any]], int]:
    connection = _connect(database)
    rows = _submission_rows(connection)
    connection.close()
    unique: dict[str, dict[str, Any]] = {}
    programs: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        cache_key = _program_key(row["language"], row["source"])
        program = unique.setdefault(
            cache_key,
            {
                "cache_key": cache_key,
                "request": _program_request(row["language"], row["source"]),
            },
        )
        programs[(row["dataset_name"], row["submission_id"])] = program
    return programs, len(unique)


def _normalize_result(value: Mapping[str, Any]) -> str:
    status = str(value.get("status", ""))
    if status == "exited":
        return "success_run"
    if status == "compile_error":
        return "compilation_error"
    lowered = status.lower()
    error = str(value.get("stderr") or "").lower()
    signal = str(value.get("signal") or "").lower()
    if "time limit" in lowered:
        return "time_limit_exceeded"
    if (
        "memory limit" in lowered
        or value.get("exitStatus") in {-9, 137}
        or "killed" in signal
        or any(marker in error for marker in _MEMORY_ERROR_MARKERS)
    ):
        return "memory_limit_exceeded"
    return "runtime_error"


def _benchmark_stdin(value: str) -> str:
    return value + ("\n" if value and not value.endswith("\n") else "")


def _execute_one(
    row: sqlite3.Row,
    programs: Mapping[tuple[str, str], dict[str, Any]],
    base_url: str,
    compile_errors: dict[str, str],
    compile_errors_lock: threading.Lock,
) -> tuple[Any, ...]:
    if row["materialization_status"] != "complete":
        result = {
            "result": "invalid_input",
            "output": "",
            "error": "INVALID_INPUT",
            "elapsed": 0.0,
            "memory_kb": 0,
        }
    else:
        program = programs[(row["dataset_name"], row["checked_submission_id"])]
        with compile_errors_lock:
            compile_error = compile_errors.get(program["cache_key"])
        if compile_error is not None:
            result = {
                "result": "compilation_error",
                "output": "",
                "error": compile_error,
                "elapsed": 0.0,
                "memory_kb": 0,
            }
        else:
            request = {
                **program["request"],
                "stdin": _benchmark_stdin(row["test_input"]),
            }
            try:
                response = _request_json(base_url, "/custom-test", request)
                status = _normalize_result(response)
                error = str(
                    response.get("stderr")
                    or response.get("message")
                    or response.get("signal")
                    or ""
                )[:OUTPUT_LIMIT_BYTES]
                if status == "compilation_error":
                    with compile_errors_lock:
                        compile_errors[program["cache_key"]] = error
                elapsed = float(
                    response.get("timeNs")
                    or int(response.get("timeMs") or 0) * 1_000_000
                ) / 1_000_000_000
                result = {
                    "result": status,
                    "output": str(response.get("stdout") or "")[:OUTPUT_LIMIT_BYTES],
                    "error": error,
                    "elapsed": 0.0 if status == "compilation_error" else elapsed,
                    "memory_kb": int(response.get("memoryBytes") or 0) // 1024,
                }
            except Exception as exc:
                raise RuntimeError(
                    f"LightCP execution request failed: {type(exc).__name__}: {exc}"
                ) from exc
    return (
        row["policy"],
        row["task"],
        row["problem_id"],
        row["submission_id"],
        row["generation_id"],
        row["checked_submission_id"],
        row["checked_submission_type"],
        row["checked_submission_verdict"],
        row["checked_submission_language"],
        row["checked_submission_difficulty"],
        result["result"],
        encode_execution_output(result["output"]),
        result["error"],
        result["elapsed"],
        result["memory_kb"],
        time.time(),
    )


def _bounded_execute(
    rows: Iterable[sqlite3.Row],
    programs: Mapping[tuple[str, str], dict[str, Any]],
    base_url: str,
    workers: int,
) -> Iterator[tuple[Any, ...]]:
    iterator = iter(rows)
    compile_errors: dict[str, str] = {}
    compile_errors_lock = threading.Lock()

    def submit(pool: ThreadPoolExecutor, row: sqlite3.Row):
        return pool.submit(
            _execute_one,
            row,
            programs,
            base_url,
            compile_errors,
            compile_errors_lock,
        )

    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending = set()
        for _ in range(workers * 4):
            try:
                pending.add(submit(pool, next(iterator)))
            except StopIteration:
                break
        while pending:
            completed, pending = wait(pending, return_when=FIRST_COMPLETED)
            for future in completed:
                yield future.result()
                try:
                    pending.add(submit(pool, next(iterator)))
                except StopIteration:
                    pass


def execute_pending(
    database: str | Path,
    programs: Mapping[tuple[str, str], dict[str, Any]],
    base_url: str,
    workers: int,
) -> dict[str, int]:
    read_connection = _connect(database)
    write_connection = _connect(database)
    statement = """
        INSERT OR REPLACE INTO executions (
            policy, task, problem_id, submission_id, generation_id,
            checked_submission_id, checked_submission_type,
            checked_submission_verdict, checked_submission_language,
            checked_submission_difficulty, result, output, error, elapsed,
            memory_kb, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    counts: dict[str, int] = {"scheduled": 0}
    batch = []
    started = time.monotonic()
    for record in _bounded_execute(
        _execution_rows(read_connection),
        programs,
        base_url,
        workers,
    ):
        counts["scheduled"] += 1
        counts[record[10]] = counts.get(record[10], 0) + 1
        batch.append(record)
        if len(batch) >= 100:
            write_connection.executemany(statement, batch)
            write_connection.commit()
            batch.clear()
        if counts["scheduled"] % 1000 == 0:
            elapsed = max(time.monotonic() - started, 0.001)
            print(
                f"execute {counts['scheduled']} "
                f"({counts['scheduled'] / elapsed:.1f}/s)",
                flush=True,
            )
    if batch:
        write_connection.executemany(statement, batch)
        write_connection.commit()
    read_connection.close()
    write_connection.close()
    return counts


def run_judge(
    database: str | Path,
    *,
    base_url: str,
    workers: int,
) -> dict[str, Any]:
    if workers < 1:
        raise ValueError("workers must be positive")
    health = preflight(base_url)
    backend = _backend_identity(health)
    bind_judge_backend(database, backend)
    materialization = materialize_generations(database, workers)
    programs, unique_programs = _load_programs(database)
    execution = execute_pending(database, programs, base_url, workers)
    return {
        "backend": backend,
        "service": health,
        "materialization": materialization,
        "compilation": {"deferred_programs": unique_programs},
        "execution": execution,
    }
