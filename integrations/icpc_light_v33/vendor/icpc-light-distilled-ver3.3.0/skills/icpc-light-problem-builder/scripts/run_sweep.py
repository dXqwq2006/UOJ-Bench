#!/usr/bin/env python3
"""Execute a build_sweep.py plan without judging the lanes' claims.

The runner stages only files named by an explicit public-material manifest,
starts every lane before waiting for any one lane, preserves raw process logs,
and enforces the planner's required-output contract.  It deliberately does not
decide whether a proposed algorithm, proof, or counterexample is correct.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import shlex
import shutil
import stat
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from string import Formatter
from typing import Any

from statement_resources import StatementResourceError, load_statement_resources


PLANNER_NAME = "icpc-light-public-blind-solve-sweep"
RUNNER_NAME = "icpc-light-public-blind-solve-runner"
DEFAULT_SOLVER_COMMAND = "codex"
PLAN_SCHEMA_VERSION = 2
PLAN_PHASES = {"initial", "replacement", "focused-neutral", "focused-deceptive"}
REQUIRED_MODEL = "gpt-5.6-sol"
REQUIRED_REASONING_EFFORT = "xhigh"
PRODUCTION_BLIND_TIME_LIMIT_SECONDS = 7200
PRODUCTION_CLOCK_NAME = "blind-time-budget.json"
TEST_CLOCK_NAME = "blind-time-budget-test.json"
PRIVATE_PUBLIC_ROOTS = {
    ".codex",
    ".git",
    "audit",
    "blind-solves",
    "data",
    "editorial",
    "materials",
    "package",
    "private",
    "solution",
    "solutions",
    "testdata",
    "tests",
    "tools",
}
PRIVATE_PUBLIC_STEMS = {
    "answer",
    "brute",
    "checker",
    "editorial",
    "gen",
    "generator",
    "oracle",
    "sol",
    "solution",
    "std",
    "validator",
}
PRIVATE_PUBLIC_NAMES = {"problem.conf"}
ALLOWED_TEMPLATE_FIELDS = {
    "kind",
    "lane_id",
    "model",
    "problem_dir",
    "prompt_file",
    "reasoning_effort",
    "workspace",
}


class ContractError(ValueError):
    """Raised when an input or planned path violates the runner contract."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_utc(raw: Any, *, label: str) -> datetime:
    if not isinstance(raw, str) or not raw.endswith("Z"):
        raise ContractError(f"{label} must be a canonical UTC timestamp ending in Z")
    try:
        value = datetime.fromisoformat(raw[:-1] + "+00:00")
    except ValueError as exc:
        raise ContractError(f"{label} is not a valid UTC timestamp") from exc
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ContractError(f"{label} must be UTC")
    return value.astimezone(timezone.utc)


def positive_seconds(raw: str) -> float:
    try:
        value = float(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if not value > 0 or not value < float("inf"):
        raise argparse.ArgumentTypeError("must be a finite positive number")
    return value


def safe_relative_path(raw: Any, *, label: str) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise ContractError(f"{label} must be a non-empty relative path")
    if "\\" in raw:
        raise ContractError(f"{label} must use '/' and must not contain backslashes")
    path = Path(raw)
    if path.is_absolute() or path == Path(".") or ".." in path.parts:
        raise ContractError(
            f"{label} must be a normalized problem-relative path without '..'"
        )
    if path.as_posix() != raw:
        raise ContractError(f"{label} must use normalized POSIX path syntax")
    return path


def below(root: Path, relative: Path, *, label: str) -> Path:
    candidate = root / relative
    try:
        candidate.resolve(strict=False).relative_to(root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ContractError(f"{label} resolves outside the problem directory") from exc
    return candidate


def reject_symlink_chain(
    root: Path, relative: Path, *, label: str, require_leaf: bool
) -> Path:
    candidate = below(root, relative, label=label)
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise ContractError(f"{label} traverses symbolic link: {current}")
        if not current.exists():
            break
    if require_leaf and not candidate.exists():
        raise ContractError(f"{label} does not exist: {candidate}")
    return candidate


def read_json_file(path: Path, *, label: str) -> Any:
    if not path.is_file():
        raise ContractError(f"{label} is not a regular file: {path}")
    try:
        with path.open("r", encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContractError(f"cannot read {label} as UTF-8 JSON: {path}: {exc}") from exc


def require_string(mapping: dict[str, Any], key: str, *, label: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{label}.{key} must be a non-empty string")
    return value


def require_path_list(
    mapping: dict[str, Any], key: str, *, label: str
) -> list[Path]:
    raw = mapping.get(key)
    if not isinstance(raw, list):
        raise ContractError(f"{label}.{key} must be a list")
    paths = [
        safe_relative_path(value, label=f"{label}.{key}[{index}]")
        for index, value in enumerate(raw)
    ]
    if len(set(paths)) != len(paths):
        raise ContractError(f"{label}.{key} contains duplicate paths")
    return paths


def validate_plan(
    raw: Any, *, plan_rel: Path
) -> tuple[dict[str, Any], Path, list[dict[str, Any]]]:
    if not isinstance(raw, dict):
        raise ContractError("plan must be a JSON object")
    if (
        raw.get("schema_version") != PLAN_SCHEMA_VERSION
        or raw.get("planner") != PLANNER_NAME
    ):
        raise ContractError("unsupported plan schema or planner")
    if raw.get("path_base") != "problem_dir":
        raise ContractError("plan.path_base must be 'problem_dir'")
    if raw.get("model") != REQUIRED_MODEL:
        raise ContractError(f"plan.model must be exactly {REQUIRED_MODEL!r}")
    if raw.get("reasoning_effort") != REQUIRED_REASONING_EFFORT:
        raise ContractError(
            "plan.reasoning_effort must be exactly "
            f"{REQUIRED_REASONING_EFFORT!r}"
        )

    workspace_root = safe_relative_path(
        raw.get("workspace_root_rel"), label="plan.workspace_root_rel"
    )
    if (
        len(workspace_root.parts) < 2
        or workspace_root.parts[0] != "blind-solves"
    ):
        raise ContractError("plan workspace must be a dedicated blind-solves/ namespace")
    if plan_rel.parent != workspace_root:
        raise ContractError("plan file must be directly inside its workspace root")

    phase = raw.get("phase")
    wave = raw.get("wave")
    if phase not in PLAN_PHASES:
        raise ContractError("plan.phase is unsupported")
    if not isinstance(wave, int) or isinstance(wave, bool) or wave < 1:
        raise ContractError("plan.wave must be a positive integer")
    if (phase == "initial") != (wave == 1):
        raise ContractError("only the initial phase may use wave 1")

    runs = raw.get("runs")
    if not isinstance(runs, list) or not runs:
        raise ContractError("plan.runs must be a non-empty list")
    seen_ids: set[str] = set()
    seen_paths: set[Path] = set()
    validated: list[dict[str, Any]] = []
    kind_ordinals = {"neutral": 0, "deceptive": 0}

    for index, value in enumerate(runs):
        label = f"plan.runs[{index}]"
        if not isinstance(value, dict):
            raise ContractError(f"{label} must be an object")
        lane_id = require_string(value, "id", label=label)
        if Path(lane_id).name != lane_id or lane_id in {".", ".."}:
            raise ContractError(f"{label}.id must be one safe path component")
        if lane_id in seen_ids:
            raise ContractError(f"duplicate lane id: {lane_id}")
        seen_ids.add(lane_id)

        kind = require_string(value, "kind", label=label)
        if kind not in {"neutral", "deceptive"}:
            raise ContractError(f"{label}.kind is unsupported: {kind}")
        if value.get("phase") != phase or value.get("wave") != wave:
            raise ContractError(f"{label} phase/wave does not match its plan")
        kind_ordinals[kind] += 1
        expected_id = (
            f"{kind}-{kind_ordinals[kind]:02d}"
            if phase == "initial"
            else f"{kind}-w{wave:02d}-{kind_ordinals[kind]:02d}"
        )
        if lane_id != expected_id:
            raise ContractError(
                f"{label}.id must be {expected_id!r} for this planner wave"
            )
        prompt = require_string(value, "prompt", label=label)
        model = require_string(value, "model", label=label)
        effort = require_string(value, "reasoning_effort", label=label)
        if model != REQUIRED_MODEL:
            raise ContractError(f"{label}.model must be exactly {REQUIRED_MODEL!r}")
        if effort != REQUIRED_REASONING_EFFORT:
            raise ContractError(
                f"{label}.reasoning_effort must be exactly "
                f"{REQUIRED_REASONING_EFFORT!r}"
            )

        run_root = workspace_root / lane_id
        expected = {
            "workspace_rel": run_root / "workspace",
            "working_directory_rel": run_root / "workspace",
            "public_materials_rel": run_root / "workspace" / "public",
            "prompt_file_rel": run_root / "prompt.txt",
            "launch_log_rel": run_root / "raw-trace" / "codex-exec.jsonl",
            "stderr_log_rel": run_root / "raw-trace" / "stderr.log",
        }
        paths: dict[str, Path] = {}
        for key, expected_path in expected.items():
            actual = safe_relative_path(value.get(key), label=f"{label}.{key}")
            if actual != expected_path:
                raise ContractError(
                    f"{label}.{key} does not match the build_sweep.py layout"
                )
            paths[key] = actual

        required = require_path_list(value, "required_outputs", label=label)
        optional = require_path_list(value, "optional_outputs", label=label)
        expected_required_names = (
            {"main.cpp", "final-status.md"}
            if kind == "neutral"
            else {"final-status.md"}
        )
        if {path.name for path in required} != expected_required_names:
            raise ContractError(f"{label}.required_outputs violates the lane contract")
        for output in required + optional:
            if output.parent != paths["workspace_rel"]:
                raise ContractError(f"{label} output must be directly inside its workspace")

        lane_paths = set(paths.values()) | set(required) | set(optional)
        overlap = lane_paths & seen_paths
        if overlap:
            raise ContractError(f"planned paths are shared across lanes: {sorted(overlap)!r}")
        seen_paths.update(lane_paths)
        validated.append(
            {
                "id": lane_id,
                "kind": kind,
                "phase": phase,
                "wave": wave,
                "prompt": prompt,
                "model": model,
                "reasoning_effort": effort,
                "run_root_rel": run_root,
                **paths,
                "required_outputs": required,
                "optional_outputs": optional,
            }
        )

    counts = raw.get("run_counts")
    expected_counts = {
        "neutral": sum(run["kind"] == "neutral" for run in validated),
        "deceptive": sum(run["kind"] == "deceptive" for run in validated),
        "total": len(validated),
    }
    if counts != expected_counts:
        raise ContractError("plan.run_counts does not match plan.runs")
    return raw, workspace_root, validated


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def regular_file_sha256(path: Path) -> str | None:
    if path.is_symlink() or not path.is_file():
        return None
    try:
        return sha256_file(path)
    except OSError:
        return None


def reject_private_public_path(relative: Path, *, label: str) -> None:
    """Reject paths that conventionally contain setter-only problem material."""
    lowered_parts = tuple(part.casefold() for part in relative.parts)
    if lowered_parts[0] in PRIVATE_PUBLIC_ROOTS:
        raise ContractError(
            f"{label} enters reserved private root {relative.parts[0]!r}; "
            "copy genuine contestant attachments under a dedicated public path"
        )
    name = lowered_parts[-1]
    if name in PRIVATE_PUBLIC_NAMES or Path(name).stem in PRIVATE_PUBLIC_STEMS:
        raise ContractError(f"{label} names conventional private artifact {relative.name!r}")


def validate_manifest(
    raw: Any, *, problem_root: Path
) -> tuple[list[tuple[Path, str]], dict[str, Any]]:
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise ContractError("public manifest must be a schema_version 1 JSON object")
    if set(raw) != {"schema_version", "files"}:
        raise ContractError("public manifest may contain only schema_version and files")
    files = raw.get("files")
    if not isinstance(files, list) or not files:
        raise ContractError("public manifest.files must be a non-empty list")
    public_files: list[tuple[Path, str]] = []
    digest_pattern = re.compile(r"[0-9a-f]{64}")
    for index, entry in enumerate(files):
        label = f"public manifest.files[{index}]"
        if not isinstance(entry, dict) or set(entry) != {"path", "sha256"}:
            raise ContractError(f"{label} must contain exactly path and sha256")
        relative = safe_relative_path(entry.get("path"), label=f"{label}.path")
        reject_private_public_path(relative, label=f"{label}.path")
        digest = entry.get("sha256")
        if not isinstance(digest, str) or digest_pattern.fullmatch(digest) is None:
            raise ContractError(f"{label}.sha256 must be 64 lowercase hexadecimal digits")
        public_files.append((relative, digest))
    paths = [relative for relative, _ in public_files]
    if len(set(paths)) != len(paths):
        raise ContractError("public manifest contains duplicate files")
    if Path("statement.md") not in paths:
        raise ContractError("public manifest must inventory problem-root statement.md")

    for relative, expected_digest in public_files:
        source = reject_symlink_chain(
            problem_root,
            relative,
            label=f"public file {relative.as_posix()}",
            require_leaf=True,
        )
        try:
            source_info = os.stat(source, follow_symlinks=False)
        except OSError as exc:
            raise ContractError(f"cannot stat public file {source}: {exc}") from exc
        if not stat.S_ISREG(source_info.st_mode):
            raise ContractError(f"public manifest entry is not a regular file: {source}")
        if source_info.st_size == 0:
            raise ContractError(f"public manifest entry is empty: {source}")
        try:
            actual_digest = sha256_file(source)
        except OSError as exc:
            raise ContractError(f"cannot hash public file {source}: {exc}") from exc
        if actual_digest != expected_digest:
            raise ContractError(
                f"public manifest digest mismatch for {relative.as_posix()}: "
                f"expected {expected_digest}, got {actual_digest}"
            )

    public_files.sort(key=lambda item: item[0].as_posix())
    normalized = {
        "schema_version": 1,
        "files": [
            {"path": relative.as_posix(), "sha256": digest}
            for relative, digest in public_files
        ],
    }
    return public_files, normalized


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Execute every lane in a build_sweep.py blind-solve plan."
    )
    parser.add_argument("--problem-dir", type=Path, required=True)
    parser.add_argument(
        "--plan",
        required=True,
        help="Problem-relative build_sweep.py JSON plan path.",
    )
    parser.add_argument(
        "--public-manifest",
        required=True,
        help=(
            "Problem-relative UTF-8 JSON file with "
            "{\"schema_version\": 1, \"files\": "
            "[{\"path\": \"statement.md\", \"sha256\": \"...\"}, ...]}."
        ),
    )
    parser.add_argument(
        "--solver-command",
        default=None,
        help=(
            "Optional shell-like command template, executed without a shell. The prompt "
            "is always provided on stdin and cwd is the lane workspace. Supported "
            "placeholders: {lane_id}, {kind}, {model}, {reasoning_effort}, "
            "{workspace}, {prompt_file}, {problem_dir}."
        ),
    )
    parser.add_argument(
        "--blind-time-limit-seconds",
        type=positive_seconds,
        default=None,
        help=(
            "Testing-only blind-stage time limit. This option is rejected unless "
            "--solver-command is also supplied; production is fixed at 7200 seconds."
        ),
    )
    args = parser.parse_args()

    if not args.problem_dir.exists() or not args.problem_dir.is_dir():
        parser.error(f"problem directory is not an existing directory: {args.problem_dir}")
    if args.problem_dir.is_symlink():
        parser.error("--problem-dir itself must not be a symbolic link")
    if args.blind_time_limit_seconds is not None and args.solver_command is None:
        parser.error(
            "--blind-time-limit-seconds is testing-only and requires --solver-command"
        )
    if args.solver_command is None:
        args.blind_time_limit_seconds = PRODUCTION_BLIND_TIME_LIMIT_SECONDS
    elif args.blind_time_limit_seconds is None:
        args.blind_time_limit_seconds = PRODUCTION_BLIND_TIME_LIMIT_SECONDS
    args.problem_dir = args.problem_dir.resolve()
    try:
        args.plan_rel = safe_relative_path(args.plan, label="--plan")
        args.manifest_rel = safe_relative_path(
            args.public_manifest, label="--public-manifest"
        )
    except ContractError as exc:
        parser.error(str(exc))
    return args


def default_command(run: dict[str, Any], workspace: Path) -> list[str]:
    return [
        DEFAULT_SOLVER_COMMAND,
        "exec",
        "--json",
        "--ephemeral",
        "--skip-git-repo-check",
        "--ignore-user-config",
        "--ignore-rules",
        "--model",
        run["model"],
        "-c",
        f'model_reasoning_effort="{run["reasoning_effort"]}"',
        "--sandbox",
        "workspace-write",
        "--cd",
        str(workspace),
        "-",
    ]


def template_command(
    template: str, *, run: dict[str, Any], problem_root: Path, prompt: Path, workspace: Path
) -> list[str]:
    try:
        tokens = shlex.split(template)
    except ValueError as exc:
        raise ContractError(f"invalid --solver-command quoting: {exc}") from exc
    if not tokens:
        raise ContractError("--solver-command must not be empty")
    fields = {
        "lane_id": run["id"],
        "kind": run["kind"],
        "model": run["model"],
        "reasoning_effort": run["reasoning_effort"],
        "workspace": str(workspace),
        "prompt_file": str(prompt),
        "problem_dir": str(problem_root),
    }
    rendered: list[str] = []
    for token in tokens:
        try:
            names = {
                field_name
                for _, field_name, _, _ in Formatter().parse(token)
                if field_name is not None
            }
        except ValueError as exc:
            raise ContractError(f"invalid --solver-command template: {exc}") from exc
        unsupported = names - ALLOWED_TEMPLATE_FIELDS
        if unsupported:
            raise ContractError(
                "unsupported --solver-command placeholder(s): "
                + ", ".join(sorted(unsupported))
            )
        rendered.append(token.format_map(fields))
    return rendered


def write_exclusive_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def stage_lane(
    *, problem_root: Path, run: dict[str, Any], public_files: list[tuple[Path, str]]
) -> None:
    run_root = below(problem_root, run["run_root_rel"], label="lane run root")
    workspace = below(problem_root, run["workspace_rel"], label="lane workspace")
    public_root = below(
        problem_root, run["public_materials_rel"], label="lane public directory"
    )
    prompt = below(problem_root, run["prompt_file_rel"], label="lane prompt")
    raw_trace = below(
        problem_root, run["launch_log_rel"], label="lane stdout log"
    ).parent

    run_root.mkdir(parents=True, exist_ok=False)
    workspace.mkdir()
    public_root.mkdir()
    raw_trace.mkdir()
    write_exclusive_text(prompt, run["prompt"] + "\n")

    for relative, expected_digest in public_files:
        source = reject_symlink_chain(
            problem_root,
            relative,
            label=f"public file {relative.as_posix()}",
            require_leaf=True,
        )
        if sha256_file(source) != expected_digest:
            raise ContractError(
                f"public source changed after manifest validation: {relative.as_posix()}"
            )
        destination = public_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() or destination.is_symlink():
            raise ContractError(f"refusing to overwrite staged file: {destination}")
        shutil.copy2(source, destination, follow_symlinks=False)
        if sha256_file(destination) != expected_digest:
            raise ContractError(
                f"staged public digest mismatch: {relative.as_posix()}"
            )


def output_status(problem_root: Path, relative: Path) -> dict[str, Any]:
    path = below(problem_root, relative, label="lane output")
    current = problem_root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            return {
                "path": relative.as_posix(),
                "status": "unsafe-symlink",
                "size": None,
                "sha256": None,
            }
        if not current.exists():
            return {
                "path": relative.as_posix(),
                "status": "missing",
                "size": None,
                "sha256": None,
            }
    try:
        info = os.stat(path, follow_symlinks=False)
    except OSError:
        return {
            "path": relative.as_posix(),
            "status": "unreadable",
            "size": None,
            "sha256": None,
        }
    digest: str | None = None
    if not stat.S_ISREG(info.st_mode):
        status = "not-regular"
    elif info.st_size == 0:
        status = "empty"
        try:
            digest = sha256_file(path)
        except OSError:
            status = "unreadable"
    else:
        status = "present-nonempty"
        try:
            digest = sha256_file(path)
        except OSError:
            status = "unreadable"
    return {
        "path": relative.as_posix(),
        "status": status,
        "size": info.st_size,
        "sha256": digest,
    }


def write_results_exclusive(path: Path, payload: dict[str, Any]) -> None:
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(path, flags, 0o644)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(rendered)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        try:
            os.unlink(path)
        except OSError:
            pass
        raise


def require_frozen_public_inventory(
    *,
    problem_root: Path,
    workspace_root_rel: Path,
    normalized_manifest: dict[str, Any],
) -> None:
    """Require every completed wave in one sweep namespace to use one inventory."""
    workspace_root = below(
        problem_root, workspace_root_rel, label="sweep workspace root"
    )
    for prior_results in sorted(workspace_root.glob("*-results.json")):
        if prior_results.is_symlink():
            raise ContractError(f"prior sweep results is a symbolic link: {prior_results}")
        data = read_json_file(prior_results, label="prior sweep results")
        if not isinstance(data, dict) or data.get("runner") != RUNNER_NAME:
            continue
        raw_manifest_rel = data.get("public_manifest_rel")
        prior_manifest_rel = safe_relative_path(
            raw_manifest_rel,
            label=f"{prior_results.name}.public_manifest_rel",
        )
        prior_manifest = reject_symlink_chain(
            problem_root,
            prior_manifest_rel,
            label=f"public manifest referenced by {prior_results.name}",
            require_leaf=True,
        )
        prior_inventory = read_json_file(
            prior_manifest,
            label=f"public manifest referenced by {prior_results.name}",
        )
        if prior_inventory != normalized_manifest:
            raise ContractError(
                "public inventory changed across blind-solve waves in "
                f"{workspace_root_rel.as_posix()}; use a new sweep namespace after "
                "contestant-visible material changes"
            )


def validate_blind_window(
    raw: dict[str, Any],
    *,
    label: str,
    expected_mode: str,
    expected_limit: float,
) -> tuple[datetime, datetime]:
    if raw.get("execution_mode") != expected_mode:
        raise ContractError(f"{label}.execution_mode is inconsistent")
    recorded_limit = raw.get("blind_time_limit_seconds")
    if (
        not isinstance(recorded_limit, (int, float))
        or isinstance(recorded_limit, bool)
        or float(recorded_limit) != float(expected_limit)
    ):
        raise ContractError(f"{label}.blind_time_limit_seconds is inconsistent")
    started = parse_utc(raw.get("blind_started_at_utc"), label=f"{label}.started")
    deadline = parse_utc(raw.get("blind_deadline_utc"), label=f"{label}.deadline")
    actual_limit = (deadline - started).total_seconds()
    if abs(actual_limit - float(expected_limit)) > 0.001:
        raise ContractError(f"{label} deadline does not match its time limit")
    return started, deadline


def discover_blind_window(
    *,
    problem_root: Path,
    workspace_root_rel: Path,
    execution_mode: str,
    time_limit_seconds: float,
) -> tuple[datetime | None, datetime | None, Path, Path]:
    workspace_root = reject_symlink_chain(
        problem_root,
        workspace_root_rel,
        label="blind workspace root",
        require_leaf=True,
    )
    clock_name = (
        PRODUCTION_CLOCK_NAME
        if execution_mode == "production-codex"
        else TEST_CLOCK_NAME
    )
    clock_rel = workspace_root_rel / clock_name
    clock_path = reject_symlink_chain(
        problem_root, clock_rel, label="blind time-budget clock", require_leaf=False
    )
    windows: list[tuple[datetime, datetime]] = []
    for prior_path in sorted(workspace_root.glob("*-results.json")):
        if prior_path.is_symlink():
            raise ContractError(f"prior sweep results is a symbolic link: {prior_path}")
        prior = read_json_file(prior_path, label="prior sweep results")
        if not isinstance(prior, dict) or prior.get("runner") != RUNNER_NAME:
            continue
        if prior.get("execution_mode") != execution_mode:
            continue
        if prior.get("blind_started_at_utc") is None:
            continue
        windows.append(
            validate_blind_window(
                prior,
                label=f"prior sweep results {prior_path.name}",
                expected_mode=execution_mode,
                expected_limit=time_limit_seconds,
            )
        )
    if clock_path.exists() or clock_path.is_symlink():
        if clock_path.is_symlink():
            raise ContractError("blind time-budget clock must not be a symbolic link")
        raw_clock = read_json_file(clock_path, label="blind time-budget clock")
        if not isinstance(raw_clock, dict) or raw_clock.get("schema_version") != 1:
            raise ContractError("blind time-budget clock has an unsupported schema")
        windows.append(
            validate_blind_window(
                raw_clock,
                label="blind time-budget clock",
                expected_mode=execution_mode,
                expected_limit=time_limit_seconds,
            )
        )
    if not windows:
        return None, None, clock_rel, clock_path
    started, deadline = windows[0]
    for other_started, other_deadline in windows[1:]:
        if other_started != started or other_deadline != deadline:
            raise ContractError("blind time budget changed across sweep waves")
    return started, deadline, clock_rel, clock_path


def establish_blind_window(
    *,
    execution_mode: str,
    time_limit_seconds: float,
    clock_rel: Path,
    clock_path: Path,
) -> tuple[datetime, datetime]:
    started = datetime.now(timezone.utc)
    deadline = started + timedelta(seconds=float(time_limit_seconds))
    payload = {
        "schema_version": 1,
        "execution_mode": execution_mode,
        "blind_started_at_utc": format_utc(started),
        "blind_deadline_utc": format_utc(deadline),
        "blind_time_limit_seconds": time_limit_seconds,
        "clock_rel": clock_rel.as_posix(),
    }
    try:
        write_results_exclusive(clock_path, payload)
        return started, deadline
    except FileExistsError:
        raw_clock = read_json_file(clock_path, label="concurrent blind time-budget clock")
        if not isinstance(raw_clock, dict) or raw_clock.get("schema_version") != 1:
            raise ContractError("concurrent blind time-budget clock is invalid")
        return validate_blind_window(
            raw_clock,
            label="concurrent blind time-budget clock",
            expected_mode=execution_mode,
            expected_limit=time_limit_seconds,
        )


def blind_timing_fields(
    *,
    started: datetime | None,
    deadline: datetime | None,
    time_limit_seconds: float,
    deadline_exceeded: bool,
    clock_rel: Path,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    elapsed = 0.0 if started is None else max(0.0, (now - started).total_seconds())
    return {
        "blind_started_at_utc": format_utc(started) if started is not None else None,
        "blind_deadline_utc": format_utc(deadline) if deadline is not None else None,
        "blind_elapsed_seconds": round(elapsed, 6),
        "blind_time_limit_seconds": time_limit_seconds,
        "blind_deadline_exceeded": deadline_exceeded,
        "blind_clock_rel": clock_rel.as_posix(),
    }


def unlaunched_run_receipts(
    *,
    problem_root: Path,
    runs: list[dict[str, Any]],
    stage_status: str,
    reason: str,
) -> list[dict[str, Any]]:
    receipts: list[dict[str, Any]] = []
    for run in runs:
        prompt_path = below(problem_root, run["prompt_file_rel"], label="lane prompt")
        receipts.append(
            {
                "id": run["id"],
                "attempt_id": run["workspace_rel"].as_posix(),
                "kind": run["kind"],
                "phase": run["phase"],
                "wave": run["wave"],
                "model": run["model"],
                "reasoning_effort": run["reasoning_effort"],
                "stage_status": stage_status,
                "success": False,
                "exit_code": None,
                "spawn_error": reason,
                "deadline_terminated": False,
                "termination_action": None,
                "command": [],
                "workspace_rel": run["workspace_rel"].as_posix(),
                "prompt_file_rel": run["prompt_file_rel"].as_posix(),
                "prompt_sha256": regular_file_sha256(prompt_path),
                "prompt_unchanged": None,
                "stdout_log_rel": run["launch_log_rel"].as_posix(),
                "stderr_log_rel": run["stderr_log_rel"].as_posix(),
                "stdout_log": output_status(problem_root, run["launch_log_rel"]),
                "stderr_log": output_status(problem_root, run["stderr_log_rel"]),
                "required_outputs": [
                    output_status(problem_root, relative)
                    for relative in run["required_outputs"]
                ],
                "optional_outputs": [
                    output_status(problem_root, relative)
                    for relative in run["optional_outputs"]
                ],
            }
        )
    return receipts


def stop_live_processes(
    processes: dict[str, subprocess.Popen[bytes]],
) -> tuple[set[str], set[str]]:
    """Terminate every live lane, then kill any process group that will not exit."""
    terminated: set[str] = set()
    killed: set[str] = set()
    for lane_id, process in processes.items():
        if process.poll() is None:
            terminated.add(lane_id)
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except (OSError, ProcessLookupError):
                try:
                    process.terminate()
                except OSError:
                    pass
    terminate_until = time.monotonic() + 10.0
    for lane_id in terminated:
        process = processes[lane_id]
        remaining = max(0.0, terminate_until - time.monotonic())
        try:
            process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            killed.add(lane_id)
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                try:
                    process.kill()
                except OSError:
                    pass
    for lane_id in terminated:
        process = processes[lane_id]
        if process.poll() is None:
            killed.add(lane_id)
            try:
                process.kill()
            except OSError:
                pass
        process.wait()
    return terminated, killed


def execute(args: argparse.Namespace) -> int:
    problem_root: Path = args.problem_dir
    try:
        load_statement_resources(problem_root)
    except StatementResourceError as exc:
        raise ContractError(f"statement resource preflight failed: {exc}") from exc
    execution_mode = (
        "test-override" if args.solver_command is not None else "production-codex"
    )
    plan_path = reject_symlink_chain(
        problem_root, args.plan_rel, label="plan", require_leaf=True
    )
    manifest_path = reject_symlink_chain(
        problem_root, args.manifest_rel, label="public manifest", require_leaf=True
    )
    plan_digest = sha256_file(plan_path)
    input_manifest_digest = sha256_file(manifest_path)
    raw_plan = read_json_file(plan_path, label="plan")
    _, workspace_root_rel, runs = validate_plan(raw_plan, plan_rel=args.plan_rel)
    public_files, normalized_manifest = validate_manifest(
        read_json_file(manifest_path, label="public manifest"),
        problem_root=problem_root,
    )

    results_rel = args.plan_rel.with_name(f"{args.plan_rel.stem}-results.json")
    results_path = reject_symlink_chain(
        problem_root, results_rel, label="results", require_leaf=False
    )
    if results_path.exists() or results_path.is_symlink():
        raise ContractError(
            f"refusing to overwrite prior sweep results: {results_path}; "
            "create a new plan/workspace for a new wave"
        )

    canonical_manifest_rel = args.plan_rel.with_name(
        f"{args.plan_rel.stem}-public-manifest.json"
    )
    canonical_manifest_path = reject_symlink_chain(
        problem_root,
        canonical_manifest_rel,
        label="canonical public manifest",
        require_leaf=False,
    )
    if canonical_manifest_path.exists() or canonical_manifest_path.is_symlink():
        if canonical_manifest_path.is_symlink():
            raise ContractError(
                f"canonical public manifest is a symbolic link: {canonical_manifest_path}"
            )
        existing_manifest = read_json_file(
            canonical_manifest_path, label="canonical public manifest"
        )
        if existing_manifest != normalized_manifest:
            raise ContractError(
                "refusing to overwrite a different canonical public manifest: "
                f"{canonical_manifest_path}"
            )

    require_frozen_public_inventory(
        problem_root=problem_root,
        workspace_root_rel=workspace_root_rel,
        normalized_manifest=normalized_manifest,
    )

    reject_symlink_chain(
        problem_root,
        workspace_root_rel,
        label="workspace root",
        require_leaf=True,
    )
    for run in runs:
        run_root = reject_symlink_chain(
            problem_root,
            run["run_root_rel"],
            label=f"lane {run['id']} run root",
            require_leaf=False,
        )
        if run_root.exists() or run_root.is_symlink():
            raise ContractError(
                f"refusing to overwrite existing attempt for lane {run['id']}: {run_root}"
            )

    if args.solver_command is not None:
        # Validate the template before creating any attempt directory.
        probe = runs[0]
        template_command(
            args.solver_command,
            run=probe,
            problem_root=problem_root,
            prompt=below(problem_root, probe["prompt_file_rel"], label="prompt"),
            workspace=below(problem_root, probe["workspace_rel"], label="workspace"),
        )

    if not canonical_manifest_path.exists():
        write_results_exclusive(canonical_manifest_path, normalized_manifest)
    canonical_manifest_digest = sha256_file(canonical_manifest_path)

    blind_started, blind_deadline, clock_rel, clock_path = discover_blind_window(
        problem_root=problem_root,
        workspace_root_rel=workspace_root_rel,
        execution_mode=execution_mode,
        time_limit_seconds=args.blind_time_limit_seconds,
    )

    started_at = utc_now()
    if blind_deadline is not None and datetime.now(timezone.utc) >= blind_deadline:
        reason = "blind-stage deadline expired before this wave could launch"
        expired_results = {
            "schema_version": 1,
            "runner": RUNNER_NAME,
            "execution_mode": execution_mode,
            "plan_rel": args.plan_rel.as_posix(),
            "plan_sha256": plan_digest,
            "plan_unchanged": regular_file_sha256(plan_path) == plan_digest,
            "input_public_manifest_rel": args.manifest_rel.as_posix(),
            "input_public_manifest_sha256": input_manifest_digest,
            "public_manifest_rel": canonical_manifest_rel.as_posix(),
            "public_manifest_sha256": canonical_manifest_digest,
            "public_manifest_unchanged": (
                regular_file_sha256(canonical_manifest_path)
                == canonical_manifest_digest
            ),
            "public_files": [
                {"path": path.as_posix(), "sha256": digest}
                for path, digest in public_files
            ],
            "started_at_utc": started_at,
            "finished_at_utc": utc_now(),
            "interrupted": False,
            "success": False,
            "staging_error": None,
            "deadline_failure_reason": reason,
            "isolation_mode": "trust-based-public-workspace",
            "filesystem_read_isolation": False,
            "semantic_verification": "not-performed",
            "semantic_review_artifact": "audit/blind-claim-reviews.json",
            "runs": unlaunched_run_receipts(
                problem_root=problem_root,
                runs=runs,
                stage_status="deadline-expired-before-staging",
                reason=reason,
            ),
            **blind_timing_fields(
                started=blind_started,
                deadline=blind_deadline,
                time_limit_seconds=args.blind_time_limit_seconds,
                deadline_exceeded=True,
                clock_rel=clock_rel,
            ),
        }
        write_results_exclusive(results_path, expired_results)
        print(f"Wrote expired sweep results: {results_rel.as_posix()}")
        print(reason + "; no new attempt was launched.", file=sys.stderr)
        return 1

    staged_ids: set[str] = set()
    staging_run_id: str | None = None
    try:
        for run in runs:
            staging_run_id = run["id"]
            stage_lane(problem_root=problem_root, run=run, public_files=public_files)
            staged_ids.add(run["id"])
    except (ContractError, OSError) as exc:
        staging_error = f"{type(exc).__name__}: {exc}"
        aborted_runs: list[dict[str, Any]] = []
        for run in runs:
            if run["id"] in staged_ids:
                stage_status = "staged-not-launched"
                error = "not launched because another lane failed staging"
            elif run["id"] == staging_run_id:
                stage_status = "staging-failed"
                error = staging_error
            else:
                stage_status = "not-staged"
                error = "not staged because an earlier lane failed staging"
            prompt_path = below(
                problem_root, run["prompt_file_rel"], label="lane prompt"
            )
            aborted_runs.append(
                {
                    "id": run["id"],
                    "attempt_id": run["workspace_rel"].as_posix(),
                    "kind": run["kind"],
                    "phase": run["phase"],
                    "wave": run["wave"],
                    "model": run["model"],
                    "reasoning_effort": run["reasoning_effort"],
                    "stage_status": stage_status,
                    "success": False,
                    "exit_code": None,
                    "spawn_error": error,
                    "deadline_terminated": False,
                    "termination_action": None,
                    "command": [],
                    "workspace_rel": run["workspace_rel"].as_posix(),
                    "prompt_file_rel": run["prompt_file_rel"].as_posix(),
                    "prompt_sha256": regular_file_sha256(prompt_path),
                    "prompt_unchanged": None,
                    "stdout_log_rel": run["launch_log_rel"].as_posix(),
                    "stderr_log_rel": run["stderr_log_rel"].as_posix(),
                    "stdout_log": output_status(problem_root, run["launch_log_rel"]),
                    "stderr_log": output_status(problem_root, run["stderr_log_rel"]),
                    "required_outputs": [
                        output_status(problem_root, relative)
                        for relative in run["required_outputs"]
                    ],
                    "optional_outputs": [
                        output_status(problem_root, relative)
                        for relative in run["optional_outputs"]
                    ],
                }
            )
        aborted_results = {
            "schema_version": 1,
            "runner": RUNNER_NAME,
            "execution_mode": execution_mode,
            "plan_rel": args.plan_rel.as_posix(),
            "plan_sha256": plan_digest,
            "plan_unchanged": regular_file_sha256(plan_path) == plan_digest,
            "input_public_manifest_rel": args.manifest_rel.as_posix(),
            "input_public_manifest_sha256": input_manifest_digest,
            "public_manifest_rel": canonical_manifest_rel.as_posix(),
            "public_manifest_sha256": canonical_manifest_digest,
            "public_manifest_unchanged": (
                regular_file_sha256(canonical_manifest_path)
                == canonical_manifest_digest
            ),
            "public_files": [
                {"path": path.as_posix(), "sha256": digest}
                for path, digest in public_files
            ],
            "started_at_utc": started_at,
            "finished_at_utc": utc_now(),
            "interrupted": False,
            "success": False,
            "staging_error": staging_error,
            "deadline_failure_reason": None,
            "isolation_mode": "trust-based-public-workspace",
            "filesystem_read_isolation": False,
            "semantic_verification": "not-performed",
            "semantic_review_artifact": "audit/blind-claim-reviews.json",
            "runs": aborted_runs,
            **blind_timing_fields(
                started=blind_started,
                deadline=blind_deadline,
                time_limit_seconds=args.blind_time_limit_seconds,
                deadline_exceeded=(
                    blind_deadline is not None
                    and datetime.now(timezone.utc) >= blind_deadline
                ),
                clock_rel=clock_rel,
            ),
        }
        write_results_exclusive(results_path, aborted_results)
        print(f"Wrote failed sweep results: {results_rel.as_posix()}")
        print(
            "Sweep staging failed; all partial lane workspaces were preserved.",
            file=sys.stderr,
        )
        return 1

    if blind_started is None or blind_deadline is None:
        blind_started, blind_deadline = establish_blind_window(
            execution_mode=execution_mode,
            time_limit_seconds=args.blind_time_limit_seconds,
            clock_rel=clock_rel,
            clock_path=clock_path,
        )

    if datetime.now(timezone.utc) >= blind_deadline:
        reason = "blind-stage deadline expired after staging and before lane launch"
        expired_results = {
            "schema_version": 1,
            "runner": RUNNER_NAME,
            "execution_mode": execution_mode,
            "plan_rel": args.plan_rel.as_posix(),
            "plan_sha256": plan_digest,
            "plan_unchanged": regular_file_sha256(plan_path) == plan_digest,
            "input_public_manifest_rel": args.manifest_rel.as_posix(),
            "input_public_manifest_sha256": input_manifest_digest,
            "public_manifest_rel": canonical_manifest_rel.as_posix(),
            "public_manifest_sha256": canonical_manifest_digest,
            "public_manifest_unchanged": (
                regular_file_sha256(canonical_manifest_path)
                == canonical_manifest_digest
            ),
            "public_files": [
                {"path": path.as_posix(), "sha256": digest}
                for path, digest in public_files
            ],
            "started_at_utc": started_at,
            "finished_at_utc": utc_now(),
            "interrupted": False,
            "success": False,
            "staging_error": None,
            "deadline_failure_reason": reason,
            "isolation_mode": "trust-based-public-workspace",
            "filesystem_read_isolation": False,
            "semantic_verification": "not-performed",
            "semantic_review_artifact": "audit/blind-claim-reviews.json",
            "runs": unlaunched_run_receipts(
                problem_root=problem_root,
                runs=runs,
                stage_status="staged-deadline-expired-before-launch",
                reason=reason,
            ),
            **blind_timing_fields(
                started=blind_started,
                deadline=blind_deadline,
                time_limit_seconds=args.blind_time_limit_seconds,
                deadline_exceeded=True,
                clock_rel=clock_rel,
            ),
        }
        write_results_exclusive(results_path, expired_results)
        print(f"Wrote expired sweep results: {results_rel.as_posix()}")
        print(reason + "; staged workspaces were preserved.", file=sys.stderr)
        return 1

    prompt_digests = {
        run["id"]: sha256_file(
            below(problem_root, run["prompt_file_rel"], label="lane prompt")
        )
        for run in runs
    }

    processes: dict[str, subprocess.Popen[bytes]] = {}
    spawn_errors: dict[str, str] = {}
    commands: dict[str, list[str]] = {}
    deadline_prevented_ids: set[str] = set()
    for run in runs:
        workspace = below(problem_root, run["workspace_rel"], label="workspace")
        prompt_path = below(problem_root, run["prompt_file_rel"], label="prompt")
        stdout_path = below(problem_root, run["launch_log_rel"], label="stdout log")
        stderr_path = below(problem_root, run["stderr_log_rel"], label="stderr log")
        command = (
            template_command(
                args.solver_command,
                run=run,
                problem_root=problem_root,
                prompt=prompt_path,
                workspace=workspace,
            )
            if args.solver_command is not None
            else default_command(run, workspace)
        )
        commands[run["id"]] = command
        if datetime.now(timezone.utc) >= blind_deadline:
            deadline_prevented_ids.add(run["id"])
            spawn_errors[run["id"]] = (
                "blind-stage deadline expired before this lane could launch"
            )
            continue
        try:
            with (
                prompt_path.open("rb") as stdin_stream,
                stdout_path.open("xb") as stdout_stream,
                stderr_path.open("xb") as stderr_stream,
            ):
                processes[run["id"]] = subprocess.Popen(
                    command,
                    cwd=workspace,
                    stdin=stdin_stream,
                    stdout=stdout_stream,
                    stderr=stderr_stream,
                    shell=False,
                    start_new_session=True,
                )
        except (OSError, ValueError) as exc:
            spawn_errors[run["id"]] = f"{type(exc).__name__}: {exc}"

    interrupted = False
    deadline_exceeded = bool(deadline_prevented_ids)
    deadline_terminated_ids: set[str] = set()
    deadline_killed_ids: set[str] = set()
    try:
        for process in processes.values():
            remaining = (blind_deadline - datetime.now(timezone.utc)).total_seconds()
            if remaining <= 0:
                deadline_exceeded = True
                break
            try:
                process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                deadline_exceeded = True
                break
        if deadline_exceeded:
            deadline_terminated_ids, deadline_killed_ids = stop_live_processes(
                processes
            )
    except KeyboardInterrupt:
        interrupted = True
        stop_live_processes(processes)

    if datetime.now(timezone.utc) >= blind_deadline:
        deadline_exceeded = True

    run_results: list[dict[str, Any]] = []
    for run in runs:
        process = processes.get(run["id"])
        prompt_path = below(
            problem_root, run["prompt_file_rel"], label="lane prompt"
        )
        prompt_unchanged = (
            regular_file_sha256(prompt_path) == prompt_digests[run["id"]]
        )
        required = [
            output_status(problem_root, relative)
            for relative in run["required_outputs"]
        ]
        optional = [
            output_status(problem_root, relative)
            for relative in run["optional_outputs"]
        ]
        required_ok = all(item["status"] == "present-nonempty" for item in required)
        exit_code = process.returncode if process is not None else None
        deadline_terminated = run["id"] in deadline_terminated_ids
        termination_action = (
            "kill"
            if run["id"] in deadline_killed_ids
            else "terminate" if deadline_terminated else None
        )
        lane_ok = (
            not interrupted
            and not deadline_exceeded
            and run["id"] not in spawn_errors
            and exit_code == 0
            and required_ok
            and prompt_unchanged
        )
        run_results.append(
            {
                "id": run["id"],
                "attempt_id": run["workspace_rel"].as_posix(),
                "kind": run["kind"],
                "phase": run["phase"],
                "wave": run["wave"],
                "model": run["model"],
                "reasoning_effort": run["reasoning_effort"],
                "stage_status": "launched",
                "success": lane_ok,
                "exit_code": exit_code,
                "spawn_error": spawn_errors.get(run["id"]),
                "deadline_terminated": deadline_terminated,
                "termination_action": termination_action,
                "command": commands[run["id"]],
                "workspace_rel": run["workspace_rel"].as_posix(),
                "prompt_file_rel": run["prompt_file_rel"].as_posix(),
                "prompt_sha256": prompt_digests[run["id"]],
                "prompt_unchanged": prompt_unchanged,
                "stdout_log_rel": run["launch_log_rel"].as_posix(),
                "stderr_log_rel": run["stderr_log_rel"].as_posix(),
                "stdout_log": output_status(problem_root, run["launch_log_rel"]),
                "stderr_log": output_status(problem_root, run["stderr_log_rel"]),
                "required_outputs": required,
                "optional_outputs": optional,
            }
        )

    plan_unchanged = regular_file_sha256(plan_path) == plan_digest
    public_manifest_unchanged = (
        regular_file_sha256(canonical_manifest_path) == canonical_manifest_digest
    )
    success = (
        not interrupted
        and not deadline_exceeded
        and plan_unchanged
        and public_manifest_unchanged
        and all(run["success"] for run in run_results)
    )
    results = {
        "schema_version": 1,
        "runner": RUNNER_NAME,
        "execution_mode": execution_mode,
        "plan_rel": args.plan_rel.as_posix(),
        "plan_sha256": plan_digest,
        "plan_unchanged": plan_unchanged,
        "input_public_manifest_rel": args.manifest_rel.as_posix(),
        "input_public_manifest_sha256": input_manifest_digest,
        "public_manifest_rel": canonical_manifest_rel.as_posix(),
        "public_manifest_sha256": canonical_manifest_digest,
        "public_manifest_unchanged": public_manifest_unchanged,
        "public_files": [
            {"path": path.as_posix(), "sha256": digest}
            for path, digest in public_files
        ],
        "started_at_utc": started_at,
        "finished_at_utc": utc_now(),
        "interrupted": interrupted,
        "success": success,
        "staging_error": None,
        "deadline_failure_reason": (
            "blind-stage deadline reached; unfinished lanes were stopped"
            if deadline_exceeded
            else None
        ),
        "isolation_mode": "trust-based-public-workspace",
        "filesystem_read_isolation": False,
        "semantic_verification": "not-performed",
        "semantic_review_artifact": "audit/blind-claim-reviews.json",
        "runs": run_results,
        **blind_timing_fields(
            started=blind_started,
            deadline=blind_deadline,
            time_limit_seconds=args.blind_time_limit_seconds,
            deadline_exceeded=deadline_exceeded,
            clock_rel=clock_rel,
        ),
    }
    write_results_exclusive(results_path, results)
    print(f"Wrote sweep execution results: {results_rel.as_posix()}")
    if not success:
        print(
            "Sweep execution failed; all lane workspaces and logs were preserved.",
            file=sys.stderr,
        )
        return 1
    print("All solver processes exited successfully and produced non-empty required outputs.")
    return 0


def main() -> int:
    args = parse_args()
    try:
        return execute(args)
    except ContractError as exc:
        print(f"run_sweep.py: error: {exc}", file=sys.stderr)
        return 2
    except FileExistsError as exc:
        print(f"run_sweep.py: refusing to overwrite existing artifact: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
