"""Container-only compiler and executor for TestCase-Eval."""

from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time


TIME_LIMIT_SECONDS = 3.0
MEMORY_LIMIT_MB = 512
OUTPUT_LIMIT_BYTES = 16 * 1024 * 1024
GENERATOR_TIMEOUT_SECONDS = 6.0


@dataclass(frozen=True)
class Language:
    extension: str
    executable: str
    timeout_factor: float = 1.0
    cpp_standard: str = ""
    java: bool = False


LANGUAGES = {
    "Python 2": Language(".py", "python2", 2.0),
    "Python 3": Language(".py", "python3", 2.0),
    "PyPy 2": Language(".py", "pypy2", 2.0),
    "PyPy 3": Language(".py", "pypy3", 2.0),
    "PyPy 3-64": Language(".py", "pypy3", 2.0),
    "C++14 (GCC 6-32)": Language(".cpp", "", cpp_standard="c++14"),
    "C++17 (GCC 7-32)": Language(".cpp", "", cpp_standard="c++17"),
    "C++20 (GCC 13-64)": Language(".cpp", "", cpp_standard="c++20"),
    "C++23 (GCC 14-64, winlibs)": Language(".cpp", "", cpp_standard="c++23"),
    "C++23 (GCC 14-64, msys2)": Language(".cpp", "", cpp_standard="c++23"),
    "Java 8": Language(".java", "java", 1.5, java=True),
    "Java 21": Language(".java", "java", 1.5, java=True),
}


@dataclass(frozen=True)
class Program:
    cache_key: str
    language: str
    directory: str
    command: tuple[str, ...]
    compile_result: str
    compile_error: str


def _connect(path: str | os.PathLike[str]) -> sqlite3.Connection:
    connection = sqlite3.connect(Path(path).resolve(), timeout=60)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    return connection


def bind_judge_backend(
    database: str | os.PathLike[str],
    backend: str,
) -> None:
    connection = _connect(database)
    encoded = json.dumps(backend)
    row = connection.execute(
        "SELECT value_json FROM manifest WHERE key = 'judge_backend'"
    ).fetchone()
    if row is not None:
        if row["value_json"] != encoded:
            connection.close()
            raise RuntimeError(
                "result database is already bound to judge backend "
                f"{row['value_json']}, not {encoded}"
            )
    else:
        execution_count = connection.execute(
            "SELECT COUNT(*) AS count FROM executions"
        ).fetchone()["count"]
        if execution_count:
            connection.close()
            raise RuntimeError(
                "result database already has unbound execution rows; "
                "use a fresh result directory for a fingerprinted judge backend"
            )
        connection.execute(
            "INSERT INTO manifest(key, value_json) VALUES ('judge_backend', ?)",
            (encoded,),
        )
        connection.commit()
    connection.close()


def _java_source(code: str, class_name: str) -> str:
    main_pattern = r"public\s+class\s+(\w+).*?public\s+static\s+void\s+main"
    match = re.search(main_pattern, code, re.DOTALL)
    if match:
        original = match.group(1)
        code = re.sub(
            rf"public\s+class\s+{re.escape(original)}",
            f"public class {class_name}",
            code,
        )
        return re.sub(
            rf"\b{re.escape(original)}\s*\(",
            f"{class_name}(",
            code,
        )
    replaced = re.sub(
        r"public\s+class\s+(\w+)",
        f"public class {class_name}",
        code,
    )
    if replaced != code:
        return replaced
    return re.sub(
        r"(?<!public\s)class\s+(\w+)",
        f"class {class_name}",
        code,
        count=1,
    )


def _program_key(language: str, source: str) -> str:
    return hashlib.sha256(
        (language + "\0" + source).encode("utf-8", errors="replace")
    ).hexdigest()


def _load_program(directory: Path) -> Program | None:
    metadata = directory / "program.json"
    if not metadata.exists():
        return None
    try:
        value = json.loads(metadata.read_text(encoding="utf-8"))
        return Program(
            value["cache_key"],
            value["language"],
            str(directory),
            tuple(value["command"]),
            value["compile_result"],
            value["compile_error"],
        )
    except (KeyError, OSError, TypeError, ValueError):
        return None


def compile_program(cache_root: Path, language: str, source: str) -> Program:
    key = _program_key(language, source)
    destination = cache_root / key
    cached = _load_program(destination)
    if cached is not None:
        return cached

    specification = LANGUAGES.get(language)
    if specification is None:
        return Program(
            key,
            language,
            str(destination),
            (),
            "compilation_error",
            f"Unsupported language: {language}",
        )

    cache_root.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{key[:12]}-", dir=cache_root))
    compile_result = "success"
    compile_error = ""
    command: Sequence[str]
    try:
        if specification.java:
            class_name = "Tmp" + key[:16]
            source_path = temporary / f"{class_name}.java"
            source_path.write_text(
                _java_source(source, class_name),
                encoding="utf-8",
            )
            result = subprocess.run(
                ["javac", "-encoding", "UTF-8", str(source_path)],
                cwd=temporary,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=30,
                check=False,
            )
            command = (
                "java",
                "-XX:+UseSerialGC",
                "-XX:TieredStopAtLevel=1",
                "-XX:NewRatio=5",
                "-Xms8M",
                "-Xss64M",
                "-DONLINE_JUDGE=true",
                f"-Xmx{MEMORY_LIMIT_MB}M",
                "-cp",
                str(destination),
                class_name,
            )
        elif specification.cpp_standard:
            source_path = temporary / "main.cpp"
            executable = temporary / "main"
            source_path.write_text(source, encoding="utf-8")
            result = subprocess.run(
                [
                    "g++-14",
                    f"-std={specification.cpp_standard}",
                    "-O2",
                    "-static",
                    "-DONLINE_JUDGE",
                    "-static-libstdc++",
                    "-static-libgcc",
                    str(source_path),
                    "-o",
                    str(executable),
                    "-lstdc++exp",
                ],
                cwd=temporary,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=30,
                check=False,
            )
            command = (str(destination / "main"),)
        else:
            source_path = temporary / f"main{specification.extension}"
            source_path.write_text(source, encoding="utf-8")
            result = None
            command = (specification.executable, "-u", str(destination / source_path.name))

        if result is not None and result.returncode != 0:
            compile_result = "compilation_error"
            compile_error = result.stderr.decode("utf-8", errors="replace")[
                :OUTPUT_LIMIT_BYTES
            ]
            command = ()

        metadata = {
            "cache_key": key,
            "language": language,
            "command": list(command),
            "compile_result": compile_result,
            "compile_error": compile_error,
        }
        (temporary / "program.json").write_text(
            json.dumps(metadata, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        try:
            temporary.replace(destination)
        except FileExistsError:
            shutil.rmtree(temporary, ignore_errors=True)
        program = _load_program(destination)
        if program is None:
            raise RuntimeError(f"invalid compile cache entry {destination}")
        return program
    except subprocess.TimeoutExpired:
        compile_result = "compilation_error"
        compile_error = "Compilation timeout"
    except Exception as exc:
        compile_result = "system_error"
        compile_error = f"{type(exc).__name__}: {exc}"

    metadata = {
        "cache_key": key,
        "language": language,
        "command": [],
        "compile_result": compile_result,
        "compile_error": compile_error,
    }
    (temporary / "program.json").write_text(
        json.dumps(metadata, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    try:
        temporary.replace(destination)
    except FileExistsError:
        shutil.rmtree(temporary, ignore_errors=True)
    return _load_program(destination) or Program(
        key, language, str(destination), (), compile_result, compile_error
    )


def _limited_command(command: Sequence[str], *, java: bool) -> list[str]:
    limits = ["prlimit", f"--fsize={OUTPUT_LIMIT_BYTES}"]
    if not java:
        limits.append(f"--as={MEMORY_LIMIT_MB * 1024 * 1024}")
    return [*limits, "--", *command]



def run_process(
    command: Sequence[str],
    input_data: str,
    *,
    timeout: float,
    java: bool = False,
) -> dict[str, Any]:
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="testcase-eval-run-") as directory:
        work = Path(directory)
        environment = {
            "HOME": str(work),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PATH": os.environ.get("PATH", ""),
        }
        process = subprocess.Popen(
            _limited_command(command, java=java),
            cwd=work,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            start_new_session=True,
        )
        benchmark_input = input_data
        if benchmark_input and not benchmark_input.endswith("\n"):
            benchmark_input += "\n"
        try:
            stdout, stderr = process.communicate(
                input=benchmark_input.encode("utf-8", errors="replace"),
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, 9)
            except ProcessLookupError:
                pass
            process.communicate()
            return {
                "result": "time_limit_exceeded",
                "output": "",
                "error": "Execution timeout",
                "elapsed": timeout,
                "memory_kb": 0,
            }

        elapsed = time.monotonic() - started
        output = stdout[:OUTPUT_LIMIT_BYTES].decode("utf-8", errors="replace")
        error = stderr[:OUTPUT_LIMIT_BYTES].decode("utf-8", errors="replace")
        if process.returncode == 0:
            result = "success_run"
        elif process.returncode in {-9, 137} or any(
            marker in error.lower()
            for marker in (
                "bad_alloc",
                "out of memory",
                "memory allocation",
                "cannot allocate",
                "memoryerror",
            )
        ):
            result = "memory_limit_exceeded"
        else:
            result = "runtime_error"
        return {
            "result": result,
            "output": output,
            "error": error,
            "elapsed": elapsed,
            "memory_kb": 0,
        }


def _materialize_one(row: sqlite3.Row) -> tuple[Any, ...]:
    candidate = row["candidate"]
    status = "complete"
    error = row["error"]
    test_input = candidate
    if not candidate.strip() or candidate.strip() == "ERROR":
        status = "invalid_input"
        test_input = "ERROR"
    elif row["candidate_format"] == "python_generator":
        with tempfile.TemporaryDirectory(prefix="testcase-eval-generator-") as directory:
            source = Path(directory) / "generator.py"
            source.write_text(candidate, encoding="utf-8")
            result = run_process(
                ["python3", "-I", str(source)],
                "",
                timeout=GENERATOR_TIMEOUT_SECONDS,
            )
        if result["result"] == "success_run" and result["output"].strip():
            test_input = result["output"]
        else:
            status = "invalid_input"
            test_input = "ERROR"
            error = result["error"] or result["result"]
    return (
        row["policy"],
        row["task"],
        row["problem_id"],
        row["submission_id"],
        row["generation_id"],
        test_input,
        status,
        error,
        time.time(),
    )


def materialize_generations(
    database: str | os.PathLike[str], workers: int
) -> dict[str, int]:
    connection = _connect(database)
    rows = list(
        connection.execute(
            """
            SELECT g.*
            FROM generations AS g
            LEFT JOIN materializations AS m
              ON m.policy = g.policy
             AND m.task = g.task
             AND m.problem_id = g.problem_id
             AND m.submission_id = g.submission_id
             AND m.generation_id = g.generation_id
            WHERE g.status = 'complete' AND m.policy IS NULL
            ORDER BY g.policy, g.task, g.problem_id, g.submission_id,
                     g.generation_id
            """
        )
    )
    counts = {"scheduled": len(rows), "complete": 0, "invalid_input": 0}
    statement = """
        INSERT OR REPLACE INTO materializations (
            policy, task, problem_id, submission_id, generation_id,
            test_input, status, error, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_materialize_one, row) for row in rows]
        batch = []
        for future in as_completed(futures):
            record = future.result()
            counts[record[6]] += 1
            batch.append(record)
            if len(batch) >= 100:
                connection.executemany(statement, batch)
                connection.commit()
                batch.clear()
        if batch:
            connection.executemany(statement, batch)
            connection.commit()
    connection.close()
    return counts


def _submission_rows(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(
        connection.execute(
            """
            SELECT * FROM submissions
            WHERE dataset_name IN ('submission_all', 'submission_lite')
            ORDER BY dataset_name, submission_id
            """
        )
    )


def prepare_programs(
    database: str | os.PathLike[str],
    cache_root: Path,
    workers: int,
) -> tuple[dict[tuple[str, str], Program], dict[str, int]]:
    connection = _connect(database)
    rows = _submission_rows(connection)
    connection.close()
    unique: dict[str, tuple[str, str]] = {}
    row_keys: dict[tuple[str, str], str] = {}
    for row in rows:
        cache_key = _program_key(row["language"], row["source"])
        unique.setdefault(cache_key, (row["language"], row["source"]))
        row_keys[(row["dataset_name"], row["submission_id"])] = cache_key

    compiled: dict[str, Program] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(compile_program, cache_root, language, source): key
            for key, (language, source) in unique.items()
        }
        for completed, future in enumerate(as_completed(futures), 1):
            compiled[futures[future]] = future.result()
            if completed % 100 == 0 or completed == len(futures):
                print(f"compile {completed}/{len(futures)}", flush=True)
    programs = {
        row_key: compiled[cache_key]
        for row_key, cache_key in row_keys.items()
    }
    counts: dict[str, int] = {}
    for program in compiled.values():
        counts[program.compile_result] = counts.get(program.compile_result, 0) + 1
    return programs, counts


def _execution_rows(
    connection: sqlite3.Connection,
) -> Iterator[sqlite3.Row]:
    common = """
        SELECT
            g.policy, g.task, g.problem_id, g.submission_id, g.generation_id,
            m.test_input, m.status AS materialization_status,
            s.dataset_name, s.submission_id AS checked_submission_id,
            s.submission_type AS checked_submission_type,
            s.verdict AS checked_submission_verdict,
            s.language AS checked_submission_language,
            s.difficulty AS checked_submission_difficulty
        FROM generations AS g
        JOIN materializations AS m
          ON m.policy = g.policy
         AND m.task = g.task
         AND m.problem_id = g.problem_id
         AND m.submission_id = g.submission_id
         AND m.generation_id = g.generation_id
        JOIN submissions AS s
          ON s.problem_id = g.problem_id
        WHERE g.status = 'complete'
          AND {task_filter}
          AND NOT EXISTS (
              SELECT 1 FROM executions AS e
              WHERE e.policy = g.policy
                AND e.task = g.task
                AND e.problem_id = g.problem_id
                AND e.submission_id = g.submission_id
                AND e.generation_id = g.generation_id
                AND e.checked_submission_id = s.submission_id
          )
        ORDER BY g.policy, g.problem_id, g.submission_id, g.generation_id,
                 s.submission_id
    """
    task1 = common.format(
        task_filter="g.task = 1 AND s.dataset_name = 'submission_all'"
    )
    task2 = common.format(
        task_filter=(
            "g.task = 2 AND s.dataset_name = 'submission_lite' "
            "AND (s.submission_type = 'right_submission' "
            "OR s.submission_id = g.submission_id)"
        )
    )
    yield from connection.execute(task1)
    yield from connection.execute(task2)


def _execute_one(
    row: sqlite3.Row,
    programs: Mapping[tuple[str, str], Program],
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
        if program.compile_result != "success":
            result = {
                "result": program.compile_result,
                "output": "",
                "error": program.compile_error,
                "elapsed": 0.0,
                "memory_kb": 0,
            }
        else:
            language = LANGUAGES[row["checked_submission_language"]]
            result = run_process(
                program.command,
                row["test_input"],
                timeout=TIME_LIMIT_SECONDS * language.timeout_factor,
                java=language.java,
            )
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
        result["output"],
        result["error"],
        result["elapsed"],
        result["memory_kb"],
        time.time(),
    )


def _bounded_execute(
    rows: Iterable[sqlite3.Row],
    programs: Mapping[tuple[str, str], Program],
    workers: int,
) -> Iterator[tuple[Any, ...]]:
    iterator = iter(rows)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending = set()
        for _ in range(workers * 4):
            try:
                pending.add(pool.submit(_execute_one, next(iterator), programs))
            except StopIteration:
                break
        while pending:
            completed, pending = wait(pending, return_when=FIRST_COMPLETED)
            for future in completed:
                yield future.result()
                try:
                    pending.add(
                        pool.submit(_execute_one, next(iterator), programs)
                    )
                except StopIteration:
                    pass


def execute_pending(
    database: str | os.PathLike[str],
    programs: Mapping[tuple[str, str], Program],
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
    database: str | os.PathLike[str],
    *,
    cache_dir: str | os.PathLike[str],
    workers: int,
    backend: str,
) -> dict[str, Any]:
    if workers < 1:
        raise ValueError("workers must be positive")
    if not backend:
        raise ValueError("backend identity is required")
    bind_judge_backend(database, backend)
    materialization = materialize_generations(database, workers)
    programs, compilation = prepare_programs(
        database,
        Path(cache_dir).resolve(),
        workers,
    )
    execution = execute_pending(database, programs, workers)
    return {
        "backend": backend,
        "materialization": materialization,
        "compilation": compilation,
        "execution": execution,
    }
