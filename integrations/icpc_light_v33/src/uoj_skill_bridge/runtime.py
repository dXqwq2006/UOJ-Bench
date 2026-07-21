"""Public-only UOJ task materializer and artifact exporter.

The UOJ process passes one typed request.  This module creates a fresh job
workspace, copies the frozen skill bundle, launches one configured agent
command, validates the exported artifact, and writes a hash-bound receipt.
It deliberately has no UOJ client and no model-provider implementation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import ast
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import signal
import stat
import subprocess
import time
from typing import Any, Mapping, Sequence


MODEL = "gpt-5.6-sol"
REASONING_EFFORT = "xhigh"
CONFIG_ENV = "ICPC_LIGHT_UOJ_BRIDGE_CONFIG"
MAX_REQUEST_BYTES = 8 * 1024 * 1024
MAX_CONFIG_BYTES = 1024 * 1024
MAX_AGENT_RESULT_BYTES = 4 * 1024 * 1024
MAX_AGENT_LOG_BYTES = 16 * 1024 * 1024
DEFAULT_MAX_CANDIDATE_BYTES = 4 * 1024 * 1024
MAX_SKILL_FILES = 10_000
MAX_SKILL_BYTES = 128 * 1024 * 1024
MAX_WORKSPACE_BYTES = 512 * 1024 * 1024
PUBLIC_METADATA_FIELDS = frozenset(
    {
        "difficulty",
        "difficulty-source",
        "display_problem_id",
        "hack_id",
        "hackable",
        "language",
        "memory_limit_mb",
        "problem_id",
        "submission_id",
        "title_en",
        "title_zh",
        "time_limit_ms",
        "wrong_id",
    }
)


class BridgeContractError(RuntimeError):
    """The request, configuration, agent run, or artifact is invalid."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_bounded_regular_file(path: Path, *, label: str, maximum: int) -> bytes:
    """Read one stable regular file without following links or over-allocating."""

    try:
        before = path.lstat()
    except OSError as exc:
        raise BridgeContractError(f"{label} is not readable") from exc
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise BridgeContractError(f"{label} must be a regular non-linked file")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise BridgeContractError(f"{label} is not a safe regular file") from exc
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
            or opened.st_size != before.st_size
            or opened.st_mtime_ns != before.st_mtime_ns
            or opened.st_ctime_ns != before.st_ctime_ns
        ):
            raise BridgeContractError(f"{label} changed before it was opened")
        if opened.st_size > maximum:
            raise BridgeContractError(f"{label} exceeds {maximum} bytes")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, maximum + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > maximum:
                raise BridgeContractError(f"{label} exceeds {maximum} bytes")
        after = os.fstat(descriptor)
        if (
            total != opened.st_size
            or after.st_size != opened.st_size
            or after.st_mtime_ns != opened.st_mtime_ns
            or after.st_ctime_ns != opened.st_ctime_ns
        ):
            raise BridgeContractError(f"{label} changed while it was read")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _load_json_file(path: Path, *, label: str, maximum: int) -> Any:
    data = _read_bounded_regular_file(path, label=label, maximum=maximum)
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise BridgeContractError(f"{label} is not one UTF-8 JSON value") from exc


def _write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    with temporary.open("xb") as stream:
        os.chmod(temporary, 0o600)
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _write_json(path: Path, value: Any) -> None:
    _write_bytes(path, _canonical_bytes(value) + b"\n")


def _absolute_directory(value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise BridgeContractError(f"{label} must be a non-empty absolute path")
    path = Path(value)
    if not path.is_absolute() or path.is_symlink() or not path.is_dir():
        raise BridgeContractError(f"{label} must be an absolute non-symlink directory")
    return path.resolve(strict=True)


def _absolute_file(value: Any, label: str, *, executable: bool = False) -> Path:
    if not isinstance(value, str) or not value:
        raise BridgeContractError(f"{label} must be a non-empty absolute path")
    path = Path(value)
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise BridgeContractError(f"{label} must be an absolute regular file")
    resolved = path.resolve(strict=True)
    if executable and not os.access(resolved, os.X_OK):
        raise BridgeContractError(f"{label} is not executable")
    return resolved


def _tree_sha256(root: Path, *, allow_empty: bool = False) -> tuple[str, int, int]:
    if root.is_symlink() or not root.is_dir():
        raise BridgeContractError("hashed tree root must be a non-symlink directory")
    digest = hashlib.sha256()
    paths = sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix())
    files = 0
    total = 0
    for path in paths:
        metadata = path.lstat()
        relative = path.relative_to(root).as_posix()
        if stat.S_ISLNK(metadata.st_mode):
            raise BridgeContractError(f"skill tree contains a symlink: {relative}")
        if stat.S_ISDIR(metadata.st_mode):
            continue
        if not stat.S_ISREG(metadata.st_mode):
            raise BridgeContractError(f"skill tree contains a non-regular file: {relative}")
        if metadata.st_nlink != 1:
            raise BridgeContractError(f"skill tree contains a hard-linked file: {relative}")
        files += 1
        total += metadata.st_size
        if files > MAX_SKILL_FILES or total > MAX_SKILL_BYTES:
            raise BridgeContractError("skill tree exceeds the bridge copy budget")
        name = relative.encode("utf-8")
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(path, flags)
        except OSError as exc:
            raise BridgeContractError(
                f"skill tree file is not safely readable: {relative}"
            ) from exc
        try:
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_nlink != 1
                or (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino)
                or opened.st_size != metadata.st_size
                or opened.st_mtime_ns != metadata.st_mtime_ns
                or opened.st_ctime_ns != metadata.st_ctime_ns
            ):
                raise BridgeContractError(f"skill tree file changed: {relative}")
            digest.update(len(name).to_bytes(8, "big"))
            digest.update(name)
            digest.update(opened.st_size.to_bytes(8, "big"))
            read_bytes = 0
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                read_bytes += len(chunk)
                digest.update(chunk)
            after = os.fstat(descriptor)
            if (
                read_bytes != opened.st_size
                or after.st_size != opened.st_size
                or after.st_mtime_ns != opened.st_mtime_ns
                or after.st_ctime_ns != opened.st_ctime_ns
            ):
                raise BridgeContractError(
                    f"skill tree file changed while hashing: {relative}"
                )
        finally:
            os.close(descriptor)
    if files == 0 and not allow_empty:
        raise BridgeContractError("skill tree is empty")
    return digest.hexdigest(), files, total


def _validate_request(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise BridgeContractError("request must be a JSON object")
    required = {"schema_version", "task", "model", "reasoning_effort", "input"}
    if set(value) != required:
        raise BridgeContractError("request fields do not match schema v1")
    if value.get("schema_version") != 1:
        raise BridgeContractError("request schema_version must be 1")
    task = value.get("task")
    if task not in {
        "generation",
        "hacking",
        "fault_coverage",
        "fault_exposure",
        "test_package",
    }:
        raise BridgeContractError("request task is unsupported")
    if value.get("model") != MODEL or value.get("reasoning_effort") != REASONING_EFFORT:
        raise BridgeContractError("request changed the frozen model or reasoning effort")
    task_input = value.get("input")
    if not isinstance(task_input, Mapping):
        raise BridgeContractError("request input must be an object")
    allowed = {
        "problem_id",
        "problem_statement",
        "language",
        "submission_id",
        "submission_code",
        "submission_language",
        "chinese",
        "metadata",
    }
    if not set(task_input).issubset(allowed):
        raise BridgeContractError("request input contains an unsupported field")
    for name in ("problem_id", "problem_statement", "metadata"):
        if name not in task_input:
            raise BridgeContractError(f"request input is missing {name}")
    if not isinstance(task_input["problem_id"], (str, int)) or isinstance(
        task_input["problem_id"], bool
    ):
        raise BridgeContractError("problem_id must be a string or integer")
    statement = task_input["problem_statement"]
    if not isinstance(statement, str) or not statement.strip():
        raise BridgeContractError("problem_statement must be non-empty")
    if len(statement.encode("utf-8")) > MAX_REQUEST_BYTES:
        raise BridgeContractError("problem_statement is too large")
    metadata = task_input["metadata"]
    if not isinstance(metadata, Mapping):
        raise BridgeContractError("metadata must be an object")
    scalar = (str, int, float, bool, type(None))
    if any(not isinstance(key, str) or not isinstance(item, scalar) for key, item in metadata.items()):
        raise BridgeContractError("metadata must contain only scalar public values")
    if not set(metadata).issubset(PUBLIC_METADATA_FIELDS):
        raise BridgeContractError("metadata contains a non-public field")
    if "chinese" in task_input and not isinstance(task_input["chinese"], bool):
        raise BridgeContractError("chinese must be boolean")
    for name in ("language", "submission_language"):
        if name in task_input and (
            not isinstance(task_input[name], str) or not task_input[name].strip()
        ):
            raise BridgeContractError(f"{name} must be a non-empty string")
    if task == "hacking":
        for name in ("submission_code", "submission_language", "chinese"):
            item = task_input.get(name)
            if name == "chinese":
                valid = isinstance(item, bool)
            else:
                valid = isinstance(item, str) and bool(item.strip())
            if not valid:
                raise BridgeContractError(f"hacking input requires non-empty {name}")
        if len(task_input["submission_code"].encode("utf-8")) > MAX_REQUEST_BYTES:
            raise BridgeContractError("submission_code is too large")
        if "submission_id" in task_input:
            raise BridgeContractError("hacking input contains a fault-exposure-only field")
    elif task == "fault_exposure":
        for name in ("submission_id", "submission_code", "submission_language"):
            item = task_input.get(name)
            if name == "submission_id":
                valid = isinstance(item, (str, int)) and not isinstance(item, bool)
            else:
                valid = isinstance(item, str) and bool(item.strip())
            if not valid:
                raise BridgeContractError(f"fault exposure input requires non-empty {name}")
        if len(task_input["submission_code"].encode("utf-8")) > MAX_REQUEST_BYTES:
            raise BridgeContractError("submission_code is too large")
        if "language" in task_input or "chinese" in task_input:
            raise BridgeContractError(
                "fault exposure input contains a UOJ-only field"
            )
    elif task == "generation":
        for name in ("language", "chinese"):
            if name not in task_input:
                raise BridgeContractError(f"generation input requires {name}")
        if any(
            name in task_input
            for name in ("submission_id", "submission_code", "submission_language")
        ):
            raise BridgeContractError("generation input contains a target-submission field")
    else:
        if any(
            name in task_input
            for name in (
                "language",
                "chinese",
                "submission_id",
                "submission_code",
                "submission_language",
            )
        ):
            raise BridgeContractError(
                "fault coverage input contains a solver or target-submission field"
            )
    return json.loads(_canonical_bytes(dict(value)).decode("utf-8"))


@dataclass(frozen=True)
class BridgeConfig:
    profile: str
    workspace_root: Path
    skill_bundle_root: Path
    expected_skill_bundle_sha256: str
    agent_command: tuple[str, ...]
    timeout_seconds: float
    max_candidate_bytes: int
    retain_workspaces: bool
    canonical_sha256: str
    source_path: Path

    @classmethod
    def load(cls) -> "BridgeConfig":
        raw_path = os.environ.get(CONFIG_ENV)
        path = _absolute_file(raw_path, CONFIG_ENV)
        metadata = path.stat()
        if hasattr(os, "geteuid") and metadata.st_uid != os.geteuid():
            raise BridgeContractError("bridge config is not owned by the bridge user")
        if stat.S_IMODE(metadata.st_mode) & 0o022:
            raise BridgeContractError("bridge config is group/world writable")
        value = _load_json_file(path, label="bridge config", maximum=MAX_CONFIG_BYTES)
        if not isinstance(value, Mapping):
            raise BridgeContractError("bridge config must be an object")
        required = {
            "schema_version",
            "profile",
            "workspace_root",
            "workspace_device",
            "skill_bundle_root",
            "skill_bundle_device",
            "expected_skill_bundle_sha256",
            "agent_command",
            "timeout_seconds",
            "max_candidate_bytes",
            "retain_workspaces",
        }
        if set(value) != required or value.get("schema_version") != 1:
            raise BridgeContractError("bridge config fields do not match schema v1")
        profile = value.get("profile")
        if not isinstance(profile, str) or not profile or len(profile) > 128:
            raise BridgeContractError("bridge profile is invalid")
        workspace_root = _absolute_directory(value.get("workspace_root"), "workspace_root")
        root_metadata = workspace_root.stat()
        if hasattr(os, "geteuid") and root_metadata.st_uid != os.geteuid():
            raise BridgeContractError("workspace_root is not owned by the bridge user")
        if stat.S_IMODE(root_metadata.st_mode) & 0o077:
            raise BridgeContractError("workspace_root must not be accessible by group/other")
        workspace_device = value.get("workspace_device")
        if (
            isinstance(workspace_device, bool)
            or not isinstance(workspace_device, int)
            or workspace_device != root_metadata.st_dev
        ):
            raise BridgeContractError("workspace_root device differs from frozen config")
        skill_bundle_root = _absolute_directory(
            value.get("skill_bundle_root"), "skill_bundle_root"
        )
        skill_bundle_device = value.get("skill_bundle_device")
        if (
            isinstance(skill_bundle_device, bool)
            or not isinstance(skill_bundle_device, int)
            or skill_bundle_device != skill_bundle_root.stat().st_dev
        ):
            raise BridgeContractError("skill_bundle_root device differs from frozen config")
        expected_skill_bundle_sha256 = value.get("expected_skill_bundle_sha256")
        if (
            not isinstance(expected_skill_bundle_sha256, str)
            or len(expected_skill_bundle_sha256) != 64
            or any(
                character not in "0123456789abcdef"
                for character in expected_skill_bundle_sha256
            )
        ):
            raise BridgeContractError(
                "expected_skill_bundle_sha256 must be 64 lowercase hex characters"
            )
        skill_file = skill_bundle_root / "skills" / "icpc-light-problem-builder" / "SKILL.md"
        if skill_file.is_symlink() or not skill_file.is_file():
            raise BridgeContractError("skill bundle has no regular ICPC Light SKILL.md")
        command = value.get("agent_command")
        if (
            not isinstance(command, list)
            or not command
            or any(not isinstance(item, str) or not item for item in command)
        ):
            raise BridgeContractError("agent_command must be a non-empty string array")
        secret_markers = ("api_key", "api-key", "token", "secret", "authorization", "bearer")
        if any(any(marker in item.casefold() for marker in secret_markers) for item in command):
            raise BridgeContractError("agent_command must not contain credential-like arguments")
        executable = _absolute_file(command[0], "agent_command[0]", executable=True)
        normalized_command = (str(executable), *tuple(command[1:]))
        timeout = value.get("timeout_seconds")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 1 <= timeout <= 21600:
            raise BridgeContractError("timeout_seconds must be in [1, 21600]")
        maximum = value.get("max_candidate_bytes")
        if isinstance(maximum, bool) or not isinstance(maximum, int) or not 1 <= maximum <= DEFAULT_MAX_CANDIDATE_BYTES:
            raise BridgeContractError(
                f"max_candidate_bytes must be in [1, {DEFAULT_MAX_CANDIDATE_BYTES}]"
            )
        retain = value.get("retain_workspaces")
        if retain is not True:
            raise BridgeContractError("retain_workspaces must be true for durable receipts")
        return cls(
            profile=profile,
            workspace_root=workspace_root,
            skill_bundle_root=skill_bundle_root,
            expected_skill_bundle_sha256=expected_skill_bundle_sha256,
            agent_command=normalized_command,
            timeout_seconds=float(timeout),
            max_candidate_bytes=maximum,
            retain_workspaces=retain,
            canonical_sha256=_sha256_bytes(_canonical_bytes(value)),
            source_path=path,
        )


def _job_workspace(root: Path, request_sha256: str) -> Path:
    name = (
        f"job-{request_sha256[:12]}-{time.time_ns()}-{os.getpid()}-"
        f"{secrets.token_hex(4)}"
    )
    workspace = root / name
    workspace.mkdir(mode=0o700)
    for child in ("surface", "output", "control", "scratch", "home"):
        (workspace / child).mkdir(mode=0o700)
    return workspace


def _materialize_surface(workspace: Path, request: Mapping[str, Any]) -> str:
    surface = workspace / "surface"
    task_input = request["input"]
    _write_json(surface / "task.json", request)
    statement = str(task_input["problem_statement"]).rstrip() + "\n"
    _write_bytes(surface / "statement.md", statement.encode("utf-8"))
    if request["task"] in {"hacking", "fault_exposure"}:
        wrong = str(task_input["submission_code"]).rstrip() + "\n"
        _write_bytes(surface / "wrong-source.txt", wrong.encode("utf-8"))
    return _tree_sha256(surface)[0]


def _copy_skills(config: BridgeConfig, workspace: Path) -> tuple[str, str, int, int]:
    bundle_sha, _, _ = _tree_sha256(config.skill_bundle_root)
    if bundle_sha != config.expected_skill_bundle_sha256:
        raise BridgeContractError("skill bundle differs from the frozen expected SHA-256")
    source = config.skill_bundle_root / "skills"
    destination = workspace / "skills"
    expected_skills = _tree_sha256(source)
    shutil.copytree(source, destination, copy_function=shutil.copy2)
    actual_skills = _tree_sha256(destination)
    if actual_skills != expected_skills:
        raise BridgeContractError("copied skill tree hash does not match its source")
    if _tree_sha256(config.skill_bundle_root)[0] != bundle_sha:
        raise BridgeContractError("skill bundle changed while it was being copied")
    return bundle_sha, actual_skills[0], actual_skills[1], actual_skills[2]


def _agent_environment(workspace: Path) -> dict[str, str]:
    environment = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(workspace / "home"),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    for name in ("LANG", "LC_ALL", "TZ"):
        value = os.environ.get(name)
        if value:
            environment[name] = value
    return environment


def _command_identity(command: Sequence[str]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, item in enumerate(command):
        record: dict[str, Any] = {"index": index, "value": item}
        path = Path(item)
        if path.is_absolute() and path.is_file() and not path.is_symlink():
            record["sha256"] = _sha256_file(path)
        result.append(record)
    return result


def _stop_agent(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
    else:
        process.terminate()
    try:
        process.wait(timeout=2)
        return
    except subprocess.TimeoutExpired:
        pass
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
    else:
        process.kill()
    process.wait(timeout=2)


def _kill_orphaned_agent_group(process_group: int) -> None:
    if os.name != "posix":
        return
    try:
        os.killpg(process_group, 0)
    except ProcessLookupError:
        return
    # The direct agent has already exited.  Anything left in its private group
    # is an orphaned child and must not survive the job boundary.
    os.killpg(process_group, signal.SIGKILL)


def _workspace_size(root: Path) -> int:
    total = 0
    for path in root.rglob("*"):
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            total += metadata.st_size
    return total


def _agent_preexec() -> None:
    # The production resource owner is the zero-mount container/cgroup
    # scheduler.  This additional per-file ceiling prevents a compatibility
    # agent from filling the system disk through one stdout/stderr or artifact.
    import resource

    _, hard = resource.getrlimit(resource.RLIMIT_FSIZE)
    limit = MAX_AGENT_LOG_BYTES
    if hard != resource.RLIM_INFINITY:
        limit = min(limit, hard)
    resource.setrlimit(resource.RLIMIT_FSIZE, (limit, limit))


def _run_agent(
    config: BridgeConfig, workspace: Path, task: str
) -> tuple[dict[str, Any], float, dict[str, Any]]:
    command = [*config.agent_command, "--task", task, "--workspace", str(workspace)]
    started = time.monotonic()
    stdout_path = workspace / "control" / "agent-stdout.log"
    stderr_path = workspace / "control" / "agent-stderr.log"
    try:
        with stdout_path.open("xb") as stdout, stderr_path.open("xb") as stderr:
            process = subprocess.Popen(
                command,
                cwd=workspace,
                env=_agent_environment(workspace),
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                start_new_session=(os.name == "posix"),
                preexec_fn=_agent_preexec if os.name == "posix" else None,
            )
            try:
                deadline = time.monotonic() + config.timeout_seconds
                while process.poll() is None:
                    if time.monotonic() >= deadline:
                        raise BridgeContractError(
                            f"agent exceeded {config.timeout_seconds:g} seconds"
                        )
                    if (
                        stdout_path.stat().st_size > MAX_AGENT_LOG_BYTES
                        or stderr_path.stat().st_size > MAX_AGENT_LOG_BYTES
                    ):
                        raise BridgeContractError("agent log exceeded 16 MiB")
                    if _workspace_size(workspace) > MAX_WORKSPACE_BYTES:
                        raise BridgeContractError("agent workspace exceeded 512 MiB")
                    time.sleep(0.05)
                returncode = int(process.returncode or 0)
            except BaseException:
                _stop_agent(process)
                raise
            finally:
                if process.poll() is not None:
                    _kill_orphaned_agent_group(process.pid)
    except BridgeContractError:
        raise
    except OSError as exc:
        raise BridgeContractError(
            f"agent execution failed: {type(exc).__name__}: {exc}"
        ) from exc
    duration = time.monotonic() - started
    stdout_data = _read_bounded_regular_file(
        stdout_path, label="agent stdout", maximum=MAX_AGENT_LOG_BYTES
    )
    stderr_data = _read_bounded_regular_file(
        stderr_path, label="agent stderr", maximum=MAX_AGENT_LOG_BYTES
    )
    logs = {
        "stdout_sha256": _sha256_bytes(stdout_data),
        "stdout_bytes": len(stdout_data),
        "stderr_sha256": _sha256_bytes(stderr_data),
        "stderr_bytes": len(stderr_data),
    }
    result_path = workspace / "control" / "agent-result.json"
    control_root = workspace / "control"
    if control_root.is_symlink() or not control_root.is_dir():
        raise BridgeContractError("agent replaced the control directory")
    result: dict[str, Any] = {}
    if result_path.is_file() and not result_path.is_symlink():
        loaded = _load_json_file(
            result_path, label="agent result", maximum=MAX_AGENT_RESULT_BYTES
        )
        if isinstance(loaded, Mapping):
            result = dict(loaded)
    if returncode != 0:
        raise BridgeContractError(
            f"agent exited {returncode}; inspect the retained hashed stderr log"
        )
    if result.get("schema_version") != 1 or result.get("status") != "completed":
        raise BridgeContractError("agent did not publish a completed schema-v1 result")
    if result.get("task") != task:
        raise BridgeContractError("agent result task does not match the request")
    transcript = result.get("transcript", [])
    usage = result.get("usage", {})
    if not isinstance(transcript, list) or not isinstance(usage, Mapping):
        raise BridgeContractError("agent result transcript/usage is invalid")
    canonical_result = _canonical_bytes(result)
    logs["result_canonical_sha256"] = _sha256_bytes(canonical_result)
    logs["result_canonical_bytes"] = len(canonical_result)
    return result, duration, logs


def _regular_candidate(path: Path, maximum: int, label: str) -> str:
    data = _read_bounded_regular_file(path, label=label, maximum=maximum)
    if not data:
        raise BridgeContractError(f"{label} is empty or exceeds {maximum} bytes")
    try:
        text = data.decode("utf-8")
    except UnicodeError as exc:
        raise BridgeContractError(f"{label} is not UTF-8") from exc
    if not text.strip():
        raise BridgeContractError(f"{label} contains no non-whitespace text")
    return text


def _test_package_candidate(workspace: Path, maximum: int) -> dict[str, Any]:
    audit_root = workspace / "audit"
    package_root = workspace / "package"
    tests_root = package_root / "tests"
    for path, label in (
        (audit_root, "audit"),
        (package_root, "package"),
        (tests_root, "package/tests"),
    ):
        metadata = path.lstat()
        if path.is_symlink() or not stat.S_ISDIR(metadata.st_mode):
            raise BridgeContractError(f"{label} is not a safe directory")
    readiness = _regular_candidate(
        workspace / "audit" / "readiness.md", maximum, "audit/readiness.md"
    )
    if re.search(r"(?m)^verdict:\s*go\s*$", readiness) is None:
        raise BridgeContractError("ICPC Light readiness verdict is not go")
    plan_path = workspace / "audit" / "regression-plan.json"
    plan = _load_json_file(
        plan_path, label="audit/regression-plan.json", maximum=maximum
    )
    release = plan.get("release_tests") if isinstance(plan, Mapping) else None
    if not isinstance(release, list) or not 1 <= len(release) <= 50:
        raise BridgeContractError("release_tests must contain 1 to 50 items")

    actual = set()
    for path in tests_root.rglob("*"):
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise BridgeContractError("package/tests contains a symlink")
        if stat.S_ISREG(metadata.st_mode) and path.suffix == ".in":
            actual.add(path.relative_to(workspace).as_posix())
        elif not stat.S_ISREG(metadata.st_mode) and not stat.S_ISDIR(metadata.st_mode):
            raise BridgeContractError("package/tests contains an unsafe artifact")

    tests = []
    declared = []
    for index, item in enumerate(release):
        raw = item.get("input") if isinstance(item, Mapping) else None
        if not isinstance(raw, str):
            raise BridgeContractError(f"release_tests[{index}].input is invalid")
        relative = PurePosixPath(raw)
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or relative.parts[:2] != ("package", "tests")
            or relative.suffix != ".in"
        ):
            raise BridgeContractError(f"release_tests[{index}] escapes package/tests")
        normalized = relative.as_posix()
        if normalized in declared:
            raise BridgeContractError("release_tests contains a duplicate input")
        declared.append(normalized)
        tests.append(
            {
                "path": normalized,
                "content": _regular_candidate(
                    workspace.joinpath(*relative.parts),
                    maximum,
                    normalized,
                ),
            }
        )
    if set(declared) != actual:
        raise BridgeContractError(
            "release_tests must enumerate every package/tests .in exactly once"
        )
    candidate = {
        "kind": "test_package",
        "tests": tests,
        "artifact": {
            "readiness": "go",
            "readiness_sha256": _sha256_file(workspace / "audit" / "readiness.md"),
            "regression_plan_sha256": _sha256_file(plan_path),
        },
    }
    if len(_canonical_bytes(candidate)) > maximum:
        raise BridgeContractError(f"test package exceeds {maximum} bytes")
    return candidate


def _candidate(workspace: Path, task: str, maximum: int) -> dict[str, Any]:
    output = workspace / "output"
    if output.is_symlink() or not output.is_dir():
        raise BridgeContractError("agent replaced the output directory")
    files = []
    for path in sorted(output.rglob("*")):
        metadata = path.lstat()
        relative = path.relative_to(output).as_posix()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            if not stat.S_ISDIR(metadata.st_mode):
                raise BridgeContractError(f"output contains an unsafe file: {relative}")
            continue
        files.append(relative)
    if task == "generation":
        if files != ["main.cpp"]:
            raise BridgeContractError("generation must export exactly output/main.cpp")
        return {
            "kind": "solution",
            "content": _regular_candidate(output / "main.cpp", maximum, "main.cpp"),
        }
    if task == "test_package":
        if files:
            raise BridgeContractError("test_package must keep output/ empty")
        return _test_package_candidate(workspace, maximum)
    choices = [name for name in ("candidate.in", "generator.py") if name in files]
    if len(choices) != 1 or files != choices:
        raise BridgeContractError(
            f"{task} must export exactly one of output/candidate.in or output/generator.py"
        )
    selected = choices[0]
    content = _regular_candidate(output / selected, maximum, selected)
    if selected == "generator.py":
        try:
            ast.parse(content, filename="generator.py")
        except SyntaxError as exc:
            raise BridgeContractError("generator.py is not valid Python syntax") from exc
    return {
        "kind": "hack" if task == "hacking" else "test_case",
        "format": "raw_input" if selected == "candidate.in" else "python_generator",
        "content": content,
    }


def execute_request(request_value: Any) -> dict[str, Any]:
    request = _validate_request(request_value)
    config = BridgeConfig.load()
    request_sha = _sha256_bytes(_canonical_bytes(request))
    workspace = _job_workspace(config.workspace_root, request_sha)
    started_at = _now()
    try:
        surface_sha = _materialize_surface(workspace, request)
        bundle_sha, copied_skills_sha, skill_files, skill_bytes = _copy_skills(
            config, workspace
        )
        agent_command_identity = _command_identity(config.agent_command)
        result, duration, agent_logs = _run_agent(config, workspace, request["task"])
        if _command_identity(config.agent_command) != agent_command_identity:
            raise BridgeContractError("agent command changed while the job was running")
        if _tree_sha256(workspace / "surface")[0] != surface_sha:
            raise BridgeContractError("agent modified the immutable public task surface")
        if _tree_sha256(workspace / "skills")[0] != copied_skills_sha:
            raise BridgeContractError("agent modified the copied skill bundle")
        candidate = _candidate(workspace, request["task"], config.max_candidate_bytes)
        candidate_bytes = (
            _canonical_bytes(candidate)
            if request["task"] == "test_package"
            else candidate["content"].encode("utf-8")
        )
        candidate_sha = _sha256_bytes(candidate_bytes)
        identity = {
            "schema_version": 1,
            "profile": config.profile,
            "model": MODEL,
            "reasoning_effort": REASONING_EFFORT,
            "request_sha256": request_sha,
            "surface_sha256": surface_sha,
            "skill_bundle_sha256": bundle_sha,
            "expected_skill_bundle_sha256": config.expected_skill_bundle_sha256,
            "copied_skills_sha256": copied_skills_sha,
            "skill_files": skill_files,
            "skill_bytes": skill_bytes,
            "bridge_config_sha256": config.canonical_sha256,
            "agent_command": agent_command_identity,
            "candidate_sha256": candidate_sha,
        }
        signature_value = {
            key: identity[key]
            for key in (
                "profile",
                "model",
                "reasoning_effort",
                "skill_bundle_sha256",
                "expected_skill_bundle_sha256",
                "copied_skills_sha256",
                "bridge_config_sha256",
                "agent_command",
            )
        }
        identity["pipeline_signature_sha256"] = _sha256_bytes(
            _canonical_bytes(signature_value)
        )
        receipt = {
            "schema_version": 1,
            "status": "completed",
            "task": request["task"],
            "problem_id": request["input"]["problem_id"],
            "started_at": started_at,
            "finished_at": _now(),
            "duration_seconds": round(duration, 3),
            "pipeline_identity": identity,
            "usage": dict(result.get("usage", {})),
            "agent_logs": agent_logs,
            "artifact": {
                "kind": candidate["kind"],
                "format": candidate.get("format"),
                "sha256": candidate_sha,
                "bytes": len(candidate_bytes),
                "test_count": len(candidate.get("tests", [])),
            },
        }
        receipt_sha = _sha256_bytes(_canonical_bytes(receipt))
        receipt["canonical_sha256"] = receipt_sha
        _write_json(workspace / "control" / "receipt.json", receipt)
        response = {
            "schema_version": 1,
            "status": "completed",
            "candidate": candidate,
            "raw_text": str(result.get("raw_text", "")),
            "message": {
                "role": "assistant",
                "content": str(result.get("final_message", "pipeline artifact exported")),
                "pipeline_identity": identity,
                "receipt_sha256": receipt_sha,
            },
            "transcript": result.get("transcript", []),
            "usage": dict(result.get("usage", {})),
            "pipeline_identity": identity,
        }
        return response
    finally:
        if not config.retain_workspaces:
            shutil.rmtree(workspace, ignore_errors=True)


def _read_request() -> Any:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = os.read(0, min(65536, MAX_REQUEST_BYTES + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > MAX_REQUEST_BYTES:
            raise BridgeContractError("request exceeds 8 MiB")
    data = b"".join(chunks)
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise BridgeContractError("stdin is not one UTF-8 JSON request") from exc


def _write_stdout(data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(1, view)
        if written <= 0:
            raise BridgeContractError("stdout closed while writing the bridge response")
        view = view[written:]


def main() -> int:
    def terminate(signum: int, _frame: Any) -> None:
        raise BridgeContractError(f"bridge received termination signal {signum}")

    previous_handlers: dict[int, Any] = {}
    for signum in (signal.SIGTERM, signal.SIGINT):
        previous_handlers[signum] = signal.getsignal(signum)
        signal.signal(signum, terminate)
    try:
        try:
            response = execute_request(_read_request())
        except Exception as exc:
            response = {
                "schema_version": 1,
                "status": "retryable_error",
                "candidate": None,
                "error": {"type": type(exc).__name__, "message": str(exc)},
                "transcript": [],
                "usage": {},
            }
        _write_stdout(_canonical_bytes(response) + b"\n")
        return 0
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)


if __name__ == "__main__":
    raise SystemExit(main())
