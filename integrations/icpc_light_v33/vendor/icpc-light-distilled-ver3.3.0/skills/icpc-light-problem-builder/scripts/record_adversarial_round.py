#!/usr/bin/env python3
"""Execute and hash-bind one ICPC Light adversarial round.

The plan names concrete wrong-route sources and breaker tests.  This command
compiles every route and runs it itself (never through a shell), derives the
actual AC/WA/TLE/RE verdict, links the prior round receipt, and atomically
creates an append-only receipt.  Caller-authored Markdown verdicts are not an
input to this program.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import signal
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from string import Formatter
from typing import Any

from regression_backend import (
    BackendError,
    BackendSource,
    DatasetInvocation,
    ProgramResult,
    create_backend,
)
from statement_resources import (
    StatementResourceError,
    StatementResources,
    load_statement_resources,
)


GENERATOR = "icpc-light-adversarial-round-recorder"
SCHEMA_VERSION = 1
DEFAULT_RECEIPT_ROOT = PurePosixPath("audit/adversarial-round-receipts")
ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
PLACEHOLDERS = {"", "-", "none", "n/a", "na", "tbd", "todo", "pending"}
CHECKER_FIELDS = {"input", "actual", "answer", "problem_dir"}
MAX_PREVIEW_BYTES = 4096


class ContractError(ValueError):
    """Raised when a plan or artifact violates the round contract."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_digest(value: Any) -> str:
    data = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return sha256_bytes(data)


def non_placeholder(value: Any) -> bool:
    return isinstance(value, str) and value.strip().lower() not in PLACEHOLDERS


def safe_relative(raw: Any, *, label: str) -> PurePosixPath:
    if not isinstance(raw, str) or not raw.strip() or "\\" in raw:
        raise ContractError(f"{label} must be a non-empty normalized POSIX path")
    path = PurePosixPath(raw)
    if path.is_absolute() or path == PurePosixPath(".") or ".." in path.parts:
        raise ContractError(f"{label} must stay below the problem directory")
    if any(part.startswith(".") for part in path.parts):
        raise ContractError(f"{label} must not contain hidden path components")
    if path.as_posix() != raw:
        raise ContractError(f"{label} must use normalized POSIX syntax")
    return path


def problem_path(
    root: Path, relative: PurePosixPath, *, label: str, require_file: bool = True
) -> Path:
    candidate = root.joinpath(*relative.parts)
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise ContractError(f"{label} traverses symbolic link: {current}")
        if not current.exists():
            break
    try:
        candidate.resolve(strict=False).relative_to(root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ContractError(f"{label} resolves outside the problem directory") from exc
    if require_file:
        if not candidate.is_file() or candidate.is_symlink():
            raise ContractError(f"{label} is not a regular file: {candidate}")
        if candidate.stat().st_size == 0:
            raise ContractError(f"{label} is empty: {candidate}")
    return candidate


def read_json(path: Path, *, label: str) -> Any:
    if not path.is_file() or path.is_symlink():
        raise ContractError(f"{label} is not a regular file: {path}")
    try:
        with path.open("r", encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContractError(f"cannot read {label} as UTF-8 JSON: {exc}") from exc


def file_binding(relative: PurePosixPath, path: Path) -> dict[str, Any]:
    return {
        "path": relative.as_posix(),
        "size": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def stream_binding(path: Path) -> dict[str, Any]:
    size = path.stat().st_size
    with path.open("rb") as stream:
        preview = stream.read(MAX_PREVIEW_BYTES)
    return {
        "size": size,
        "sha256": sha256_file(path),
        "preview_utf8": preview.decode("utf-8", errors="replace"),
        "preview_truncated": size > len(preview),
    }


def stream_binding_bytes(data: bytes) -> dict[str, Any]:
    preview = data[:MAX_PREVIEW_BYTES]
    return {
        "size": len(data),
        "sha256": sha256_bytes(data),
        "preview_utf8": preview.decode("utf-8", errors="replace"),
        "preview_truncated": len(data) > len(preview),
    }


def positive_seconds(raw: Any, *, label: str, default: float | None = None) -> float:
    if raw is None and default is not None:
        return default
    if (
        not isinstance(raw, (int, float))
        or isinstance(raw, bool)
        or not math.isfinite(float(raw))
        or float(raw) <= 0
    ):
        raise ContractError(f"{label} must be a finite positive number")
    return float(raw)


def valid_id(raw: Any, *, label: str) -> str:
    if not isinstance(raw, str) or ID_RE.fullmatch(raw) is None:
        raise ContractError(f"{label} must match {ID_RE.pattern!r}")
    return raw


def execute_legacy_checker_to_files(
    command: list[str],
    *,
    cwd: Path,
    stdin_path: Path | None,
    stdout_path: Path,
    stderr_path: Path,
    timeout: float,
) -> dict[str, Any]:
    """Run a legacy checker only after explicit local test-mode gating."""

    started = utc_now()
    timed_out = False
    spawn_error: str | None = None
    exit_code: int | None = None
    try:
        with (
            (stdin_path.open("rb") if stdin_path is not None else open(os.devnull, "rb")) as stdin,
            stdout_path.open("xb") as stdout,
            stderr_path.open("xb") as stderr,
        ):
            process = subprocess.Popen(
                command,
                cwd=cwd,
                stdin=stdin,
                stdout=stdout,
                stderr=stderr,
                shell=False,
                start_new_session=True,
            )
            try:
                exit_code = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    exit_code = process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    exit_code = process.wait()
    except (OSError, ValueError) as exc:
        spawn_error = f"{type(exc).__name__}: {exc}"
        stdout_path.touch(exist_ok=True)
        stderr_path.touch(exist_ok=True)
    return {
        "command": command,
        "started_at_utc": started,
        "finished_at_utc": utc_now(),
        "timeout_seconds": timeout,
        "timed_out": timed_out,
        "exit_code": exit_code,
        "spawn_error": spawn_error,
        "stdout": stream_binding(stdout_path),
        "stderr": stream_binding(stderr_path),
    }


def backend_execution_record(
    result: ProgramResult,
    *,
    role: str,
    timeout: float,
    backend_name: str,
) -> dict[str, Any]:
    return {
        "command": [f"$BACKEND_PROGRAM/{role}"],
        "started_at_utc": None,
        "finished_at_utc": None,
        "timeout_seconds": timeout,
        "timed_out": result.timed_out,
        "exit_code": result.returncode,
        "spawn_error": result.launch_error,
        "stdout": stream_binding_bytes(result.stdout),
        "stderr": stream_binding_bytes(result.stderr),
        "execution_backend": backend_name,
        "duration_seconds": round(result.duration_seconds, 6),
        "memory_bytes": result.memory_bytes,
        "sandbox_verdict": result.sandbox_verdict,
        "sandbox_status": result.sandbox_status,
    }


def backend_compile_record(
    record: dict[str, Any],
    *,
    timeout: float,
    backend_name: str,
) -> dict[str, Any]:
    result = record.get("result")
    if not isinstance(result, dict):
        raise ContractError("backend compilation result is malformed")
    stderr = str(result.get("stderr_preview", "")).encode("utf-8")
    return {
        "command": record.get("command") or ["$BACKEND_COMPILE"],
        "started_at_utc": None,
        "finished_at_utc": None,
        "timeout_seconds": timeout,
        "timed_out": bool(result.get("timed_out", False)),
        "exit_code": result.get("returncode"),
        "spawn_error": result.get("launch_error"),
        "stdout": stream_binding_bytes(b""),
        "stderr": stream_binding_bytes(stderr),
        "execution_backend": backend_name,
        "source": record.get("source"),
        "source_sha256": record.get("source_sha256"),
        "compilation_evidence": record.get("compilation_evidence"),
    }


def program_failure_verdict(result: ProgramResult) -> str | None:
    """Preserve one trustworthy sandbox/runtime failure class."""

    if result.sandbox_verdict in {"TLE", "MLE", "OLE", "RE"}:
        return result.sandbox_verdict
    if result.timed_out:
        return "TLE"
    if result.launch_error is None and result.returncode not in {None, 0}:
        return "RE"
    return None


def render_checker_command(
    raw: Any,
    *,
    input_path: Path,
    actual_path: Path,
    answer_path: Path,
    problem_root: Path,
) -> list[str]:
    if not isinstance(raw, list) or not raw:
        raise ContractError("checker_command must be a non-empty argv list")
    fields = {
        "input": str(input_path),
        "actual": str(actual_path),
        "answer": str(answer_path),
        "problem_dir": str(problem_root),
    }
    command: list[str] = []
    for index, token in enumerate(raw):
        if not isinstance(token, str) or not token:
            raise ContractError(f"checker_command[{index}] must be a non-empty string")
        names = {
            name
            for _, name, _, _ in Formatter().parse(token)
            if name is not None
        }
        unknown = names - CHECKER_FIELDS
        if unknown:
            raise ContractError(f"checker_command uses unknown fields: {sorted(unknown)}")
        command.append(token.format(**fields))
    return command


def write_json_atomic_exclusive(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or path.exists():
        raise ContractError(f"refusing to overwrite receipt: {path}")
    data = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
    except FileExistsError as exc:
        raise ContractError(f"refusing to overwrite receipt: {path}") from exc
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def parse_plan(
    raw: Any, *, problem_root: Path, plan_rel: PurePosixPath
) -> dict[str, Any]:
    if not isinstance(raw, dict) or raw.get("schema_version") != SCHEMA_VERSION:
        raise ContractError("round plan must be an object with schema_version 1")
    round_number = raw.get("round")
    if not isinstance(round_number, int) or isinstance(round_number, bool) or round_number < 1:
        raise ContractError("round must be a positive integer")
    trigger = raw.get("trigger")
    delta = raw.get("delta")
    if round_number == 1 and trigger != "initial-matrix":
        raise ContractError("round 1 trigger must be exactly 'initial-matrix'")
    if not non_placeholder(trigger):
        raise ContractError("trigger must be concrete")
    if not non_placeholder(delta):
        raise ContractError("delta must be concrete")
    expected_plan = PurePosixPath("audit/adversarial-round-plans") / f"round-{round_number:02d}.json"
    if plan_rel != expected_plan:
        raise ContractError(f"round {round_number} plan must be {expected_plan.as_posix()}")

    previous_raw = raw.get("previous_receipt")
    expected_previous = (
        None
        if round_number == 1
        else (DEFAULT_RECEIPT_ROOT / f"round-{round_number - 1:02d}.json").as_posix()
    )
    if previous_raw != expected_previous:
        raise ContractError(f"previous_receipt must be {expected_previous!r}")

    try:
        statement_resources = load_statement_resources(problem_root)
    except StatementResourceError as exc:
        raise ContractError(f"statement resource policy is invalid: {exc}") from exc
    timeout = statement_resources.time_limit_ms / 1000
    if "timeout_seconds" in raw:
        planned_timeout = positive_seconds(
            raw.get("timeout_seconds"), label="timeout_seconds"
        )
        if planned_timeout != timeout:
            raise ContractError(
                "timeout_seconds must equal the current statement time limit "
                f"({timeout:g} seconds)"
            )
    planned_memory = raw.get("memory_limit_mb")
    if "memory_limit_mb" in raw and (
        type(planned_memory) is not int
        or planned_memory != statement_resources.memory_limit_mib
    ):
        raise ContractError(
            "memory_limit_mb must equal the current statement memory limit "
            f"({statement_resources.memory_limit_mib} MiB)"
        )
    compile_timeout = positive_seconds(
        raw.get("compile_timeout_seconds"),
        label="compile_timeout_seconds",
        default=60.0,
    )
    tests_raw = raw.get("tests")
    routes_raw = raw.get("routes")
    if not isinstance(tests_raw, list) or not tests_raw:
        raise ContractError("tests must be a non-empty list")
    if not isinstance(routes_raw, list) or not routes_raw:
        raise ContractError("routes must be a non-empty list")

    tests: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(tests_raw):
        label = f"tests[{index}]"
        if not isinstance(item, dict):
            raise ContractError(f"{label} must be an object")
        test_id = valid_id(item.get("test_id"), label=f"{label}.test_id")
        if test_id in tests:
            raise ContractError(f"duplicate test_id: {test_id}")
        input_rel = safe_relative(item.get("input_path"), label=f"{label}.input_path")
        answer_rel = safe_relative(item.get("answer_path"), label=f"{label}.answer_path")
        for field, relative in (("input_path", input_rel), ("answer_path", answer_rel)):
            if relative.parts[:2] != ("package", "tests"):
                raise ContractError(f"{label}.{field} must be below package/tests/")
        input_path = problem_path(problem_root, input_rel, label=f"{label} input")
        answer_path = problem_path(problem_root, answer_rel, label=f"{label} answer")
        comparison = item.get("comparison", "tokens")
        if comparison not in {"tokens", "exact", "checker"}:
            raise ContractError(f"{label}.comparison must be tokens, exact, or checker")
        checker_command = item.get("checker_command")
        if comparison == "checker" and checker_command is not None and not isinstance(
            checker_command, list
        ):
            raise ContractError(
                f"{label}.checker_command must be an argv array when present"
            )
        if comparison != "checker" and checker_command is not None:
            raise ContractError(f"{label}.checker_command is only valid in checker mode")
        checker_wa_exit_codes = item.get("checker_wa_exit_codes", [1, 2])
        if (
            not isinstance(checker_wa_exit_codes, list)
            or not checker_wa_exit_codes
            or any(
                not isinstance(code, int) or isinstance(code, bool) or code <= 0
                for code in checker_wa_exit_codes
            )
            or len(set(checker_wa_exit_codes)) != len(checker_wa_exit_codes)
        ):
            raise ContractError(
                f"{label}.checker_wa_exit_codes must be distinct positive integers"
            )
        if comparison != "checker" and "checker_wa_exit_codes" in item:
            raise ContractError(
                f"{label}.checker_wa_exit_codes is only valid in checker mode"
            )
        tests[test_id] = {
            "test_id": test_id,
            "input_rel": input_rel,
            "input_path": input_path,
            "answer_rel": answer_rel,
            "answer_path": answer_path,
            "comparison": comparison,
            "checker_command": checker_command,
            "checker_wa_exit_codes": checker_wa_exit_codes,
        }

    checker_tests = [
        test for test in tests.values() if test["comparison"] == "checker"
    ]
    checker_source_raw = raw.get("checker_source")
    checker_source: dict[str, Any] | None = None
    if checker_tests:
        if checker_source_raw is not None:
            checker_rel = safe_relative(
                checker_source_raw, label="checker_source"
            )
            if checker_rel != PurePosixPath("package/checker.cpp"):
                raise ContractError(
                    "checker_source must be exactly package/checker.cpp"
                )
            checker_path = problem_path(
                problem_root, checker_rel, label="checker source"
            )
            if checker_path.suffix.lower() not in {".cc", ".cpp", ".cxx"}:
                raise ContractError("checker_source must be C++")
            if any(test["checker_command"] is not None for test in checker_tests):
                raise ContractError(
                    "checker_command cannot be combined with checker_source"
                )
            checker_source = {
                "rel": checker_rel,
                "path": checker_path,
            }
        elif any(test["checker_command"] is None for test in checker_tests):
            raise ContractError(
                "checker mode requires checker_source or a legacy checker_command "
                "for every checker test"
            )
    elif checker_source_raw is not None:
        raise ContractError("checker_source is only valid when a test uses checker mode")

    routes: list[dict[str, Any]] = []
    seen_routes: set[str] = set()
    used_tests: set[str] = set()
    for index, item in enumerate(routes_raw):
        label = f"routes[{index}]"
        if not isinstance(item, dict):
            raise ContractError(f"{label} must be an object")
        route_id = valid_id(item.get("route_id"), label=f"{label}.route_id")
        if route_id in seen_routes:
            raise ContractError(f"duplicate route_id: {route_id}")
        seen_routes.add(route_id)
        source_rel = safe_relative(item.get("source_path"), label=f"{label}.source_path")
        if not source_rel.as_posix().startswith("audit/private/wrong-solutions/"):
            raise ContractError(f"{label}.source_path must be in private wrong-solutions")
        source_path = problem_path(problem_root, source_rel, label=f"{label} source")
        test_id = valid_id(item.get("breaker_test_id"), label=f"{label}.breaker_test_id")
        if test_id not in tests:
            raise ContractError(f"{label}.breaker_test_id is not declared in tests")
        used_tests.add(test_id)
        routes.append(
            {
                "route_id": route_id,
                "source_rel": source_rel,
                "source_path": source_path,
                "breaker_test_id": test_id,
            }
        )
    if used_tests != set(tests):
        raise ContractError("every declared test must be used by at least one route")
    return {
        "round": round_number,
        "trigger": trigger,
        "delta": delta.strip(),
        "previous_receipt": previous_raw,
        "timeout": timeout,
        "memory_limit_mb": statement_resources.memory_limit_mib,
        "statement_resources": statement_resources,
        "compile_timeout": compile_timeout,
        "tests": tests,
        "routes": routes,
        "checker_source": checker_source,
    }


def load_previous(
    problem_root: Path, parsed: dict[str, Any]
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    previous_rel_raw = parsed["previous_receipt"]
    if previous_rel_raw is None:
        return None, None
    previous_rel = safe_relative(previous_rel_raw, label="previous_receipt")
    previous_path = problem_path(problem_root, previous_rel, label="previous receipt")
    previous = read_json(previous_path, label="previous receipt")
    if (
        not isinstance(previous, dict)
        or previous.get("schema_version") != SCHEMA_VERSION
        or previous.get("generator") != GENERATOR
        or previous.get("round") != parsed["round"] - 1
        or previous.get("status") != "passed"
    ):
        raise ContractError("previous receipt is not the passed immediately prior round")
    evidence = previous.get("evidence_sha256")
    previous_core = dict(previous)
    previous_core.pop("evidence_sha256", None)
    if evidence != canonical_digest(previous_core):
        raise ContractError("previous receipt evidence digest is invalid")
    return previous, file_binding(previous_rel, previous_path)


def execute_round(
    *,
    problem_root: Path,
    plan_rel: PurePosixPath,
    plan_path: Path,
    parsed: dict[str, Any],
    execution_backend: str,
    test_mode: bool,
    lightcpverifier_url: str,
) -> dict[str, Any]:
    try:
        current_resources = load_statement_resources(problem_root)
    except StatementResourceError as exc:
        raise ContractError(f"statement resource policy is invalid: {exc}") from exc
    parsed_resources = parsed.get("statement_resources")
    if not isinstance(parsed_resources, StatementResources) or (
        parsed_resources.as_dict() != current_resources.as_dict()
    ):
        raise ContractError("statement resource policy changed after plan parsing")
    previous, previous_binding = load_previous(problem_root, parsed)
    current_resource_record = current_resources.as_dict()
    if previous is not None and (
        previous.get("statement_resources") != current_resource_record
        or previous.get("statement_resources_sha256")
        != current_resources.canonical_sha256()
    ):
        raise ContractError(
            "previous round is bound to a different statement resource policy"
        )
    if previous is not None and not test_mode:
        prior_backend = previous.get("execution_backend")
        if (
            previous.get("production") is not True
            or previous.get("execution_mode") != "production"
            or not isinstance(prior_backend, dict)
            or prior_backend.get("name") != "lightcpverifier"
            or prior_backend.get("sandboxed") is not True
            or prior_backend.get("testing_only") is not False
        ):
            raise ContractError(
                "production round cannot extend non-production backend evidence"
            )
    if previous is not None:
        prior_survivors = set(previous.get("survivors", []))
        active = {route["route_id"] for route in parsed["routes"]}
        missing = sorted(prior_survivors - active)
        if missing:
            raise ContractError(
                f"round must carry every prior survivor forward; missing {missing}"
            )
        prior_routes = {
            item.get("route_id"): item
            for item in previous.get("routes", [])
            if isinstance(item, dict)
        }
        for route in parsed["routes"]:
            route_id = route["route_id"]
            if route_id not in prior_survivors:
                continue
            prior_source = prior_routes.get(route_id, {}).get("source")
            if (
                not isinstance(prior_source, dict)
                or prior_source.get("path") != route["source_rel"].as_posix()
                or prior_source.get("sha256") != sha256_file(route["source_path"])
            ):
                raise ContractError(
                    f"carried survivor {route_id} changed source path or content"
                )

    if (
        any(test["comparison"] == "checker" for test in parsed["tests"].values())
        and parsed["checker_source"] is None
        and not (execution_backend == "local" and test_mode)
    ):
        raise ContractError(
            "production checker mode requires hash-bound checker_source; legacy "
            "checker_command is allowed only with --test-mode "
            "--execution-backend local"
        )
    try:
        backend = create_backend(
            execution_backend,
            test_mode=test_mode,
            lightcpverifier_url=lightcpverifier_url,
            program_time_limit_ms=current_resources.time_limit_ms,
            memory_limit_mb=current_resources.memory_limit_mib,
        )
    except BackendError as exc:
        raise ContractError(f"execution backend unavailable: {exc}") from exc
    backend_configuration = backend.configuration(
        requested_program_timeout_seconds=parsed["timeout"],
        requested_compile_timeout_seconds=parsed["compile_timeout"],
        requested_memory_limit_mb=current_resources.memory_limit_mib,
    )
    tests_receipt = [
        {
            "test_id": test["test_id"],
            "input": file_binding(test["input_rel"], test["input_path"]),
            "answer": file_binding(test["answer_rel"], test["answer_path"]),
            "comparison": test["comparison"],
            "checker_command_template": test["checker_command"],
            "checker_wa_exit_codes": test["checker_wa_exit_codes"],
        }
        for test in parsed["tests"].values()
    ]
    routes_receipt: list[dict[str, Any]] = []
    killed: list[str] = []
    survivors: list[str] = []
    with tempfile.TemporaryDirectory(prefix="icpc-light-adversarial-round-") as tmp:
        temporary = Path(tmp)
        backend_sources = [
            BackendSource(
                role=f"wrong:{route['route_id']}",
                rel=route["source_rel"].as_posix(),
                path=route["source_path"],
            )
            for route in parsed["routes"]
        ]
        if parsed["checker_source"] is not None:
            backend_sources.append(
                BackendSource(
                    role="checker",
                    rel=parsed["checker_source"]["rel"].as_posix(),
                    path=parsed["checker_source"]["path"],
                )
            )
        try:
            programs, compilation, compile_errors = backend.compile_sources(
                backend_sources,
                problem_dir=problem_root,
                build_dir=temporary,
                timeout=parsed["compile_timeout"],
            )
        except (BackendError, OSError) as exc:
            raise ContractError(f"backend compilation failed: {exc}") from exc
        if compile_errors:
            raise ContractError(
                "round program compilation failed: " + "; ".join(compile_errors)
            )
        compilation_by_role = {
            item.get("role"): item
            for item in compilation
            if isinstance(item, dict) and isinstance(item.get("role"), str)
        }
        checker_program = programs.get("checker")
        checker_source_binding: dict[str, Any] | None = None
        checker_compile_record: dict[str, Any] | None = None
        if parsed["checker_source"] is not None:
            raw_checker_compile = compilation_by_role.get("checker")
            if checker_program is None or not isinstance(raw_checker_compile, dict):
                raise ContractError("checker has no compile evidence")
            checker_compile_record = backend_compile_record(
                raw_checker_compile,
                timeout=parsed["compile_timeout"],
                backend_name=backend.name,
            )
            if (
                checker_compile_record["spawn_error"] is not None
                or checker_compile_record["timed_out"]
                or checker_compile_record["exit_code"] != 0
            ):
                raise ContractError("checker did not compile successfully")
            checker_source_binding = file_binding(
                parsed["checker_source"]["rel"],
                parsed["checker_source"]["path"],
            )
        for ordinal, route in enumerate(parsed["routes"], start=1):
            route_id = route["route_id"]
            role = f"wrong:{route_id}"
            program = programs.get(role)
            raw_compile = compilation_by_role.get(role)
            if program is None or not isinstance(raw_compile, dict):
                raise ContractError(f"route {route_id} has no compile evidence")
            compile_record = backend_compile_record(
                raw_compile,
                timeout=parsed["compile_timeout"],
                backend_name=backend.name,
            )
            if (
                compile_record["spawn_error"] is not None
                or compile_record["timed_out"]
                or compile_record["exit_code"] != 0
            ):
                raise ContractError(f"route {route_id} did not compile successfully")

            test = parsed["tests"][route["breaker_test_id"]]
            try:
                observed = backend.run_dataset(
                    program,
                    [
                        DatasetInvocation(
                            stdin=test["input_path"].read_bytes(),
                            case_id=route_id,
                        )
                    ],
                    problem_dir=problem_root,
                    timeout=parsed["timeout"],
                )
            except (BackendError, OSError) as exc:
                raise ContractError(
                    f"route {route_id} backend execution failed: {exc}"
                ) from exc
            if len(observed) != 1:
                raise ContractError(f"route {route_id} returned no ordered result")
            run_result = observed[0]
            run_record = backend_execution_record(
                run_result,
                role=role,
                timeout=parsed["timeout"],
                backend_name=backend.name,
            )
            checker_record: dict[str, Any] | None = None
            failure_verdict = program_failure_verdict(run_result)
            if failure_verdict is not None:
                verdict = failure_verdict
                comparison_evidence = None
            elif run_record["spawn_error"] is not None:
                raise ContractError(f"route {route_id} failed to launch")
            elif run_record["exit_code"] != 0:
                verdict = "RE"
                comparison_evidence = None
            elif test["comparison"] == "tokens":
                answer = test["answer_path"].read_bytes()
                actual_tokens = b"\0".join(run_result.stdout.split())
                answer_tokens = b"\0".join(answer.split())
                verdict = "AC" if actual_tokens == answer_tokens else "WA"
                comparison_evidence = {
                    "mode": "tokens",
                    "actual_normalized_sha256": sha256_bytes(actual_tokens),
                    "answer_normalized_sha256": sha256_bytes(answer_tokens),
                }
            elif test["comparison"] == "exact":
                answer = test["answer_path"].read_bytes()
                verdict = "AC" if run_result.stdout == answer else "WA"
                comparison_evidence = {
                    "mode": "exact",
                    "actual_sha256": run_record["stdout"]["sha256"],
                    "answer_sha256": sha256_file(test["answer_path"]),
                }
            else:
                if checker_program is not None:
                    try:
                        checker_results = backend.run_dataset(
                            checker_program,
                            [
                                DatasetInvocation(
                                    argv=(
                                        "input.txt",
                                        "candidate.txt",
                                        "answer.txt",
                                    ),
                                    copy_in_files={
                                        "input.txt": test["input_path"].read_bytes(),
                                        "candidate.txt": run_result.stdout,
                                        "answer.txt": test["answer_path"].read_bytes(),
                                    },
                                    case_id=f"checker:{route_id}",
                                )
                            ],
                            problem_dir=problem_root,
                            timeout=parsed["timeout"],
                        )
                    except (BackendError, OSError) as exc:
                        raise ContractError(
                            f"checker backend execution failed for route {route_id}: {exc}"
                        ) from exc
                    if len(checker_results) != 1:
                        raise ContractError(
                            f"checker returned no ordered result for route {route_id}"
                        )
                    checker_result = checker_results[0]
                    checker_record = backend_execution_record(
                        checker_result,
                        role="checker",
                        timeout=parsed["timeout"],
                        backend_name=backend.name,
                    )
                    checker_record["command"] = [
                        "$BACKEND_PROGRAM/checker",
                        "$INPUT",
                        "$CANDIDATE_OUTPUT",
                        "$ANSWER_OUTPUT",
                    ]
                    if (
                        checker_record["spawn_error"] is not None
                        or checker_record["timed_out"]
                        or checker_record.get("sandbox_verdict") in {"TLE", "MLE", "OLE"}
                    ):
                        raise ContractError(
                            f"checker failed to execute for route {route_id}"
                        )
                else:
                    run_stdout = temporary / f"run-{ordinal}.stdout"
                    run_stdout.write_bytes(run_result.stdout)
                    checker_stdout = temporary / f"checker-{ordinal}.stdout"
                    checker_stderr = temporary / f"checker-{ordinal}.stderr"
                    checker_command = render_checker_command(
                        test["checker_command"],
                        input_path=test["input_path"],
                        actual_path=run_stdout,
                        answer_path=test["answer_path"],
                        problem_root=problem_root,
                    )
                    checker_record = execute_legacy_checker_to_files(
                        checker_command,
                        cwd=problem_root,
                        stdin_path=None,
                        stdout_path=checker_stdout,
                        stderr_path=checker_stderr,
                        timeout=parsed["timeout"],
                    )
                    if (
                        checker_record["spawn_error"] is not None
                        or checker_record["timed_out"]
                    ):
                        raise ContractError(
                            f"checker failed to execute for route {route_id}"
                        )
                checker_exit = checker_record["exit_code"]
                checker_sandbox_verdict = checker_record.get("sandbox_verdict")
                if checker_program is not None and backend.name == "lightcpverifier":
                    if checker_exit == 0 and checker_sandbox_verdict != "EXECUTED":
                        raise ContractError(
                            f"checker exit 0 conflicts with sandbox verdict "
                            f"{checker_sandbox_verdict!r} for route {route_id}"
                        )
                    if checker_exit != 0 and checker_sandbox_verdict not in {
                        "EXECUTED",
                        "RE",
                    }:
                        raise ContractError(
                            f"checker rejection conflicts with sandbox verdict "
                            f"{checker_sandbox_verdict!r} for route {route_id}"
                        )
                if checker_exit == 0:
                    verdict = "AC"
                elif checker_exit in test["checker_wa_exit_codes"]:
                    verdict = "WA"
                else:
                    raise ContractError(
                        f"checker returned unassigned infrastructure code {checker_exit} "
                        f"for route {route_id}"
                    )
                comparison_evidence = {
                    "mode": "checker",
                    "checker_exit_code": checker_exit,
                    "checker_source_sha256": (
                        checker_source_binding["sha256"]
                        if checker_source_binding is not None
                        else None
                    ),
                }

            outcome = "survived" if verdict == "AC" else "killed"
            (survivors if verdict == "AC" else killed).append(route_id)
            routes_receipt.append(
                {
                    "route_id": route_id,
                    "source": file_binding(route["source_rel"], route["source_path"]),
                    "breaker_test_id": route["breaker_test_id"],
                    "compile": compile_record,
                    "execution": run_record,
                    "checker": checker_record,
                    "comparison_evidence": comparison_evidence,
                    "verdict": verdict,
                    "outcome": outcome,
                }
            )

    receipt: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generator": GENERATOR,
        "recorder_sha256": sha256_file(Path(__file__).resolve()),
        "status": "passed",
        "round": parsed["round"],
        "trigger": parsed["trigger"],
        "delta": parsed["delta"],
        "created_at_utc": utc_now(),
        "execution_mode": "test" if test_mode else "production",
        "production": not test_mode,
        "statement_resources": current_resource_record,
        "statement_resources_sha256": current_resources.canonical_sha256(),
        "execution_backend": backend_configuration,
        "execution_backend_evidence": backend.execution_evidence(),
        "plan": file_binding(plan_rel, plan_path),
        "previous_receipt": previous_binding,
        "compiler": backend.name,
        "checker_source": checker_source_binding,
        "checker_compile": checker_compile_record,
        "tests": tests_receipt,
        "routes": routes_receipt,
        "killed": killed,
        "survivors": survivors,
    }
    receipt["evidence_sha256"] = canonical_digest(receipt)
    return receipt


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--problem-dir", type=Path, required=True)
    parser.add_argument("--plan", required=True, help="problem-relative round plan JSON")
    parser.add_argument(
        "--execution-backend",
        choices=("lightcpverifier", "local"),
        default="lightcpverifier",
    )
    parser.add_argument("--test-mode", action="store_true")
    parser.add_argument(
        "--lightcpverifier-url",
        default=os.environ.get(
            "ICPC_LIGHT_LIGHTCPVERIFIER_URL", "http://127.0.0.1:8081"
        ),
    )
    args = parser.parse_args()
    if not args.problem_dir.is_dir() or args.problem_dir.is_symlink():
        parser.error("--problem-dir must be an existing non-symlink directory")
    if args.execution_backend == "local" and not args.test_mode:
        parser.error("--execution-backend local requires --test-mode")
    args.problem_dir = args.problem_dir.resolve()
    try:
        args.plan_rel = safe_relative(args.plan, label="--plan")
    except ContractError as exc:
        parser.error(str(exc))
    return args


def main() -> int:
    args = parse_args()
    try:
        plan_path = problem_path(
            args.problem_dir, args.plan_rel, label="round plan"
        )
        raw = read_json(plan_path, label="round plan")
        parsed = parse_plan(raw, problem_root=args.problem_dir, plan_rel=args.plan_rel)
        receipt = execute_round(
            problem_root=args.problem_dir,
            plan_rel=args.plan_rel,
            plan_path=plan_path,
            parsed=parsed,
            execution_backend=args.execution_backend,
            test_mode=args.test_mode,
            lightcpverifier_url=args.lightcpverifier_url,
        )
        output_rel = DEFAULT_RECEIPT_ROOT / f"round-{parsed['round']:02d}.json"
        output_path = problem_path(
            args.problem_dir,
            output_rel,
            label="round receipt output",
            require_file=False,
        )
        write_json_atomic_exclusive(output_path, receipt)
    except ContractError as exc:
        print(f"record_adversarial_round.py: FAIL: {exc}", file=sys.stderr)
        return 1
    print(f"Adversarial round {parsed['round']}: PASS")
    print(f"receipt: {output_rel.as_posix()}")
    print(f"killed: {','.join(receipt['killed']) or 'none'}")
    print(f"survivors: {','.join(receipt['survivors']) or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
