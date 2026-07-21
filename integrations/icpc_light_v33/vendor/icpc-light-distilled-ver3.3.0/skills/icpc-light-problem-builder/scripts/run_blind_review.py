#!/usr/bin/env python3
"""Run one fresh, artifact-bound review of a neutral blind-solve candidate.

This script validates the candidate's production sweep receipt, stages only the
frozen contestant-visible surface plus that candidate, launches a new Codex
reviewer, writes an execution receipt, and atomically appends the resulting
production review object to ``audit/blind-claim-reviews.json``. It never decides
correctness itself; the fresh reviewer report supplies the status.
"""

from __future__ import annotations

import argparse
import fcntl
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
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from string import Formatter
from typing import Any

from contamination_status import parse_contamination_status


BLIND_ROOT_REL = "blind-solves/icpc-light"
PLAN_NAME_RE = re.compile(r"sweep-plan(?:-wave-(\d{2,}))?\.json")
PLANNER = "icpc-light-public-blind-solve-sweep"
RUNNER = "icpc-light-public-blind-solve-runner"
REQUIRED_MODEL = "gpt-5.6-sol"
REQUIRED_REASONING_EFFORT = "xhigh"
PRODUCTION_BLIND_TIME_LIMIT_SECONDS = 7200
PRODUCTION_CLOCK_NAME = "blind-time-budget.json"
TEST_CLOCK_NAME = "blind-time-budget-test.json"
REVIEW_RECEIPT_ROOT = PurePosixPath("audit/private/blind-reviews")
REVIEW_REPORT_ROOT = PurePosixPath("audit/blind-reviews")
ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
HASH_RE = re.compile(r"[0-9a-f]{64}")
REVIEW_STATUSES = {"verified", "rejected", "inconclusive"}
ALLOWED_TEMPLATE_FIELDS = {
    "attempt_id",
    "model",
    "prompt_file",
    "review_id",
    "reviewer_id",
    "source_sha256",
    "workspace",
}


class ContractError(ValueError):
    """Raised when review inputs do not satisfy the immutable handoff contract."""


@dataclass(frozen=True)
class AttemptEvidence:
    plan_rel: PurePosixPath
    plan_path: Path
    plan_sha256: str
    results_rel: PurePosixPath
    results_path: Path
    lane_id: str
    attempt_id: PurePosixPath
    workspace: Path
    public_dir: Path
    source_rel: PurePosixPath
    source_path: Path
    source_sha256: str
    manifest_rel: PurePosixPath
    manifest_path: Path
    manifest_sha256: str
    inventory: tuple[tuple[PurePosixPath, str], ...]
    blind_started_at: datetime
    blind_deadline: datetime
    blind_time_limit_seconds: float
    blind_clock_rel: PurePosixPath
    blind_clock_path: Path
    blind_clock_sha256: str


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


def valid_hash(value: Any) -> bool:
    return isinstance(value, str) and HASH_RE.fullmatch(value) is not None


def safe_relative(raw: Any, *, label: str) -> PurePosixPath:
    if not isinstance(raw, str) or not raw.strip():
        raise ContractError(f"{label} must be a non-empty problem-relative path")
    if "\\" in raw:
        raise ContractError(f"{label} must not contain backslashes")
    path = PurePosixPath(raw)
    if path.is_absolute() or path == PurePosixPath(".") or ".." in path.parts:
        raise ContractError(f"{label} must stay below the problem root")
    if path.as_posix() != raw:
        raise ContractError(f"{label} must use normalized POSIX syntax")
    return path


def safe_problem_path(
    problem_root: Path,
    relative: PurePosixPath,
    *,
    label: str,
    require_exists: bool,
) -> Path:
    candidate = problem_root.joinpath(*relative.parts)
    current = problem_root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise ContractError(f"{label} traverses symbolic link: {current}")
        if not current.exists():
            break
    try:
        candidate.resolve(strict=False).relative_to(problem_root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ContractError(f"{label} resolves outside the problem root") from exc
    if require_exists and not candidate.exists():
        raise ContractError(f"{label} does not exist: {candidate}")
    return candidate


def require_regular_file(path: Path, *, label: str, nonempty: bool = True) -> None:
    if path.is_symlink() or not path.exists():
        raise ContractError(f"{label} is missing or a symbolic link: {path}")
    try:
        info = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        raise ContractError(f"cannot stat {label}: {path}: {exc}") from exc
    if not stat.S_ISREG(info.st_mode):
        raise ContractError(f"{label} is not a regular file: {path}")
    if nonempty and info.st_size == 0:
        raise ContractError(f"{label} is empty: {path}")


def read_json(path: Path, *, label: str) -> Any:
    require_regular_file(path, label=label)
    try:
        with path.open("r", encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContractError(f"invalid UTF-8 JSON in {label}: {path}: {exc}") from exc


def production_codex_command(value: Any) -> bool:
    if not (
        isinstance(value, list)
        and len(value) >= 2
        and all(isinstance(item, str) and item for item in value)
        and Path(value[0]).name == "codex"
        and value[1] == "exec"
    ):
        return False
    try:
        model_index = value.index("--model")
        config_index = value.index("-c")
    except ValueError:
        return False
    return (
        model_index + 1 < len(value)
        and value[model_index + 1] == REQUIRED_MODEL
        and config_index + 1 < len(value)
        and value[config_index + 1]
        == f'model_reasoning_effort="{REQUIRED_REASONING_EFFORT}"'
    )


def contamination_status(text: str) -> str | None:
    return parse_contamination_status(text)


def file_receipt(path: Path, relative: PurePosixPath) -> dict[str, Any]:
    if path.is_symlink():
        return {
            "path": relative.as_posix(),
            "status": "unsafe-symlink",
            "size": None,
            "sha256": None,
        }
    if not path.exists():
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
    if not stat.S_ISREG(info.st_mode):
        return {
            "path": relative.as_posix(),
            "status": "not-regular",
            "size": info.st_size,
            "sha256": None,
        }
    try:
        digest = sha256_file(path)
    except OSError:
        return {
            "path": relative.as_posix(),
            "status": "unreadable",
            "size": info.st_size,
            "sha256": None,
        }
    return {
        "path": relative.as_posix(),
        "status": "present-nonempty" if info.st_size else "empty",
        "size": info.st_size,
        "sha256": digest,
    }


def receipt_matches(path: Path, relative: PurePosixPath, raw: Any) -> bool:
    if not isinstance(raw, dict):
        return False
    current = file_receipt(path, relative)
    return all(raw.get(key) == current[key] for key in ("path", "status", "size", "sha256"))


def load_inventory(
    problem_root: Path, manifest_rel: PurePosixPath, manifest_path: Path
) -> tuple[tuple[PurePosixPath, str], ...]:
    data = read_json(manifest_path, label="frozen public manifest")
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ContractError("frozen public manifest must use schema_version 1")
    raw_files = data.get("files")
    if not isinstance(raw_files, list) or not raw_files:
        raise ContractError("frozen public manifest.files must be non-empty")
    inventory: list[tuple[PurePosixPath, str]] = []
    seen: set[PurePosixPath] = set()
    for index, entry in enumerate(raw_files):
        if not isinstance(entry, dict) or set(entry) != {"path", "sha256"}:
            raise ContractError(
                f"frozen public manifest.files[{index}] must contain path and sha256"
            )
        relative = safe_relative(
            entry.get("path"), label=f"frozen public manifest.files[{index}].path"
        )
        digest = entry.get("sha256")
        if not valid_hash(digest):
            raise ContractError(
                f"frozen public manifest.files[{index}].sha256 is invalid"
            )
        if relative in seen:
            raise ContractError(f"duplicate frozen public path: {relative.as_posix()}")
        seen.add(relative)
        inventory.append((relative, digest))
    if PurePosixPath("statement.md") not in seen:
        raise ContractError("frozen public manifest does not contain statement.md")
    return tuple(sorted(inventory, key=lambda item: item[0].as_posix()))


def exact_public_surface(
    public_dir: Path, inventory: tuple[tuple[PurePosixPath, str], ...]
) -> None:
    if public_dir.is_symlink() or not public_dir.is_dir():
        raise ContractError(f"attempt public directory is missing or unsafe: {public_dir}")
    actual: set[PurePosixPath] = set()
    for path in public_dir.rglob("*"):
        if path.is_symlink():
            raise ContractError(f"attempt public surface contains symbolic link: {path}")
        if path.is_file():
            actual.add(PurePosixPath(path.relative_to(public_dir).as_posix()))
    expected = {relative for relative, _ in inventory}
    if actual != expected:
        raise ContractError(
            "attempt public surface differs from frozen inventory: "
            f"missing={sorted(str(x) for x in expected - actual)}, "
            f"extra={sorted(str(x) for x in actual - expected)}"
        )
    for relative, digest in inventory:
        path = public_dir.joinpath(*relative.parts)
        require_regular_file(path, label=f"staged public file {relative.as_posix()}")
        if sha256_file(path) != digest:
            raise ContractError(
                f"staged public file hash differs from manifest: {relative.as_posix()}"
            )


def find_attempt(
    problem_root: Path,
    attempt_id: PurePosixPath,
    *,
    expected_execution_mode: str,
    expected_time_limit_seconds: float,
) -> AttemptEvidence:
    blind_root = safe_problem_path(
        problem_root,
        PurePosixPath(BLIND_ROOT_REL),
        label="blind-solve root",
        require_exists=True,
    )
    if blind_root.is_symlink() or not blind_root.is_dir():
        raise ContractError("blind-solve root is not a regular directory")
    matches: list[tuple[Path, dict[str, Any], dict[str, Any]]] = []
    for plan_path in sorted(blind_root.iterdir()):
        if plan_path.is_symlink() or not plan_path.is_file():
            continue
        if PLAN_NAME_RE.fullmatch(plan_path.name) is None:
            continue
        plan = read_json(plan_path, label=f"sweep plan {plan_path.name}")
        if not isinstance(plan, dict):
            continue
        runs = plan.get("runs")
        if not isinstance(runs, list):
            continue
        for run in runs:
            if isinstance(run, dict) and run.get("workspace_rel") == attempt_id.as_posix():
                matches.append((plan_path, plan, run))
    if len(matches) != 1:
        raise ContractError(
            f"--attempt-id must match exactly one planned attempt; found {len(matches)}"
        )
    plan_path, plan, run = matches[0]
    if plan.get("schema_version") != 2 or plan.get("planner") != PLANNER:
        raise ContractError("attempt plan is not a supported production sweep plan")
    if plan.get("workspace_root_rel") != BLIND_ROOT_REL:
        raise ContractError("attempt plan uses an unsupported workspace root")
    if run.get("kind") != "neutral":
        raise ContractError("independent full-solution review requires a neutral attempt")
    lane_id = run.get("id")
    if not isinstance(lane_id, str) or not lane_id:
        raise ContractError("attempt plan has an invalid lane ID")
    expected_attempt = PurePosixPath(BLIND_ROOT_REL) / lane_id / "workspace"
    if attempt_id != expected_attempt:
        raise ContractError("attempt ID does not match the planner's neutral workspace layout")
    if run.get("working_directory_rel") != attempt_id.as_posix():
        raise ContractError("attempt working_directory_rel differs from workspace_rel")
    public_rel = safe_relative(
        run.get("public_materials_rel"), label="attempt public_materials_rel"
    )
    if public_rel != attempt_id / "public":
        raise ContractError("attempt public path does not match the planner layout")
    source_rel = attempt_id / "main.cpp"
    required = run.get("required_outputs")
    if not isinstance(required, list) or source_rel.as_posix() not in required:
        raise ContractError("neutral plan does not require main.cpp")

    plan_rel = PurePosixPath(plan_path.relative_to(problem_root).as_posix())
    results_path = plan_path.with_name(f"{plan_path.stem}-results.json")
    results_rel = PurePosixPath(results_path.relative_to(problem_root).as_posix())
    results = read_json(results_path, label="production sweep results")
    if not isinstance(results, dict):
        raise ContractError("production sweep results must be a JSON object")
    if (
        results.get("schema_version") != 1
        or results.get("runner") != RUNNER
        or results.get("execution_mode") != expected_execution_mode
        or results.get("plan_rel") != plan_rel.as_posix()
        or results.get("plan_unchanged") is not True
        or results.get("public_manifest_unchanged") is not True
        or results.get("success") is not True
        or results.get("blind_deadline_exceeded") is not False
    ):
        raise ContractError("attempt is not bound to a clean matching runner receipt")
    plan_digest = sha256_file(plan_path)
    if results.get("plan_sha256") != plan_digest:
        raise ContractError("production runner receipt no longer binds the sweep plan")

    raw_result_runs = results.get("runs")
    if not isinstance(raw_result_runs, list):
        raise ContractError("production runner receipt has no run list")
    run_results = [
        item
        for item in raw_result_runs
        if isinstance(item, dict) and item.get("id") == lane_id
    ]
    if len(run_results) != 1:
        raise ContractError("production runner receipt does not uniquely bind the lane")
    lane_result = run_results[0]
    if (
        lane_result.get("attempt_id") != attempt_id.as_posix()
        or lane_result.get("workspace_rel") != attempt_id.as_posix()
        or lane_result.get("kind") != "neutral"
        or lane_result.get("success") is not True
        or lane_result.get("stage_status") != "launched"
        or lane_result.get("exit_code") != 0
        or lane_result.get("spawn_error") is not None
        or lane_result.get("deadline_terminated") is not False
        or lane_result.get("prompt_unchanged") is not True
        or lane_result.get("model") != REQUIRED_MODEL
        or lane_result.get("reasoning_effort") != REQUIRED_REASONING_EFFORT
        or (
            expected_execution_mode == "production-codex"
            and not production_codex_command(lane_result.get("command"))
        )
    ):
        raise ContractError("neutral attempt is not a successful compliant solver run")

    recorded_limit = results.get("blind_time_limit_seconds")
    if (
        not isinstance(recorded_limit, (int, float))
        or isinstance(recorded_limit, bool)
        or float(recorded_limit) != float(expected_time_limit_seconds)
    ):
        raise ContractError("runner receipt has an inconsistent blind time limit")
    blind_started = parse_utc(
        results.get("blind_started_at_utc"), label="runner blind start"
    )
    blind_deadline = parse_utc(
        results.get("blind_deadline_utc"), label="runner blind deadline"
    )
    if abs(
        (blind_deadline - blind_started).total_seconds()
        - float(expected_time_limit_seconds)
    ) > 0.001:
        raise ContractError("runner blind deadline does not match its fixed time limit")
    expected_clock_name = (
        PRODUCTION_CLOCK_NAME
        if expected_execution_mode == "production-codex"
        else TEST_CLOCK_NAME
    )
    blind_clock_rel = safe_relative(
        results.get("blind_clock_rel"), label="runner blind_clock_rel"
    )
    expected_clock_rel = PurePosixPath(BLIND_ROOT_REL) / expected_clock_name
    if blind_clock_rel != expected_clock_rel:
        raise ContractError("runner receipt references the wrong blind-stage clock")
    blind_clock_path = safe_problem_path(
        problem_root,
        blind_clock_rel,
        label="blind-stage clock",
        require_exists=True,
    )
    require_regular_file(blind_clock_path, label="blind-stage clock")
    blind_clock_sha256 = sha256_file(blind_clock_path)
    clock = read_json(blind_clock_path, label="blind-stage clock")
    if not isinstance(clock, dict) or clock.get("schema_version") != 1:
        raise ContractError("blind-stage clock has an unsupported schema")
    clock_limit = clock.get("blind_time_limit_seconds")
    if (
        clock.get("execution_mode") != expected_execution_mode
        or clock.get("blind_started_at_utc") != format_utc(blind_started)
        or clock.get("blind_deadline_utc") != format_utc(blind_deadline)
        or not isinstance(clock_limit, (int, float))
        or isinstance(clock_limit, bool)
        or float(clock_limit) != float(expected_time_limit_seconds)
        or clock.get("clock_rel") != blind_clock_rel.as_posix()
    ):
        raise ContractError("blind-stage clock no longer matches the runner receipt")

    workspace = safe_problem_path(
        problem_root, attempt_id, label="attempt workspace", require_exists=True
    )
    public_dir = safe_problem_path(
        problem_root, public_rel, label="attempt public directory", require_exists=True
    )
    source_path = safe_problem_path(
        problem_root, source_rel, label="neutral main.cpp", require_exists=True
    )
    require_regular_file(source_path, label="neutral main.cpp")
    source_digest = sha256_file(source_path)
    final_rel = attempt_id / "final-status.md"
    final_path = safe_problem_path(
        problem_root, final_rel, label="neutral final-status.md", require_exists=True
    )
    require_regular_file(final_path, label="neutral final-status.md")
    try:
        final_text = final_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ContractError(f"cannot read neutral final-status.md: {exc}") from exc
    if lane_id not in final_text or contamination_status(final_text) != "clean":
        raise ContractError("neutral attempt lacks a clean, lane-bound final status")

    raw_outputs = lane_result.get("required_outputs")
    if not isinstance(raw_outputs, list):
        raise ContractError("neutral runner receipt has no required output receipts")
    output_by_path = {
        item.get("path"): item
        for item in raw_outputs
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    }
    for relative, path in ((source_rel, source_path), (final_rel, final_path)):
        raw_receipt = output_by_path.get(relative.as_posix())
        if not receipt_matches(path, relative, raw_receipt):
            raise ContractError(
                f"production output receipt no longer binds {relative.as_posix()}"
            )
        if raw_receipt.get("status") != "present-nonempty":
            raise ContractError(f"production output is not non-empty: {relative.as_posix()}")
    if output_by_path[source_rel.as_posix()].get("sha256") != source_digest:
        raise ContractError("current main.cpp differs from the production runner receipt")

    manifest_rel = safe_relative(
        results.get("public_manifest_rel"), label="runner public_manifest_rel"
    )
    manifest_path = safe_problem_path(
        problem_root,
        manifest_rel,
        label="frozen public manifest",
        require_exists=True,
    )
    require_regular_file(manifest_path, label="frozen public manifest")
    manifest_digest = sha256_file(manifest_path)
    if results.get("public_manifest_sha256") != manifest_digest:
        raise ContractError("runner receipt no longer binds the frozen public manifest")
    inventory = load_inventory(problem_root, manifest_rel, manifest_path)
    expected_public_files = [
        {"path": relative.as_posix(), "sha256": digest}
        for relative, digest in inventory
    ]
    if results.get("public_files") != expected_public_files:
        raise ContractError("runner public_files differs from frozen manifest")
    exact_public_surface(public_dir, inventory)

    return AttemptEvidence(
        plan_rel=plan_rel,
        plan_path=plan_path,
        plan_sha256=plan_digest,
        results_rel=results_rel,
        results_path=results_path,
        lane_id=lane_id,
        attempt_id=attempt_id,
        workspace=workspace,
        public_dir=public_dir,
        source_rel=source_rel,
        source_path=source_path,
        source_sha256=source_digest,
        manifest_rel=manifest_rel,
        manifest_path=manifest_path,
        manifest_sha256=manifest_digest,
        inventory=inventory,
        blind_started_at=blind_started,
        blind_deadline=blind_deadline,
        blind_time_limit_seconds=float(expected_time_limit_seconds),
        blind_clock_rel=blind_clock_rel,
        blind_clock_path=blind_clock_path,
        blind_clock_sha256=blind_clock_sha256,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run a fresh independent Codex review of one clean production neutral "
            "blind-solve candidate and record an artifact-bound receipt."
        )
    )
    parser.add_argument("--problem-dir", type=Path, required=True)
    parser.add_argument(
        "--attempt-id",
        required=True,
        help="Exact neutral workspace_rel from a production sweep plan.",
    )
    parser.add_argument("--review-id", required=True)
    parser.add_argument("--reviewer-id", required=True)
    parser.add_argument(
        "--model",
        required=True,
        help=f"Required reviewer model; must be exactly {REQUIRED_MODEL!r}.",
    )
    parser.add_argument(
        "--reasoning-effort",
        default=REQUIRED_REASONING_EFFORT,
        help=(
            "Required reviewer reasoning effort; must be exactly "
            f"{REQUIRED_REASONING_EFFORT!r}."
        ),
    )
    parser.add_argument(
        "--review-command",
        default=None,
        help=(
            "Testing-only command template, run without a shell. Prompt is stdin and "
            "cwd is the review workspace. Placeholders: {review_id}, {reviewer_id}, "
            "{attempt_id}, {source_sha256}, {model}, {workspace}, {prompt_file}."
        ),
    )
    parser.add_argument(
        "--blind-time-limit-seconds",
        type=positive_seconds,
        default=None,
        help=(
            "Testing-only shared blind-stage time limit. This option is rejected "
            "unless --review-command is supplied; production is fixed at 7200 seconds."
        ),
    )
    args = parser.parse_args()
    if not args.problem_dir.exists() or not args.problem_dir.is_dir():
        parser.error("--problem-dir must be an existing directory")
    if args.problem_dir.is_symlink():
        parser.error("--problem-dir itself must not be a symbolic link")
    args.problem_dir = args.problem_dir.resolve()
    try:
        args.attempt_rel = safe_relative(args.attempt_id, label="--attempt-id")
    except ContractError as exc:
        parser.error(str(exc))
    for option, value in (
        ("--review-id", args.review_id),
        ("--reviewer-id", args.reviewer_id),
    ):
        if not isinstance(value, str) or ID_RE.fullmatch(value) is None:
            parser.error(f"{option} must match {ID_RE.pattern!r}")
    args.model = args.model.strip()
    args.reasoning_effort = args.reasoning_effort.strip()
    if args.model != REQUIRED_MODEL:
        parser.error(f"--model must be exactly {REQUIRED_MODEL!r}")
    if args.reasoning_effort != REQUIRED_REASONING_EFFORT:
        parser.error(
            "--reasoning-effort must be exactly "
            f"{REQUIRED_REASONING_EFFORT!r}"
        )
    if args.blind_time_limit_seconds is not None and args.review_command is None:
        parser.error(
            "--blind-time-limit-seconds is testing-only and requires --review-command"
        )
    if args.review_command is None:
        args.blind_time_limit_seconds = PRODUCTION_BLIND_TIME_LIMIT_SECONDS
    elif args.blind_time_limit_seconds is None:
        args.blind_time_limit_seconds = PRODUCTION_BLIND_TIME_LIMIT_SECONDS
    return args


def build_prompt(
    *, evidence: AttemptEvidence, review_id: str, reviewer_id: str
) -> str:
    inventory_lines = "\n".join(
        f"- `{relative.as_posix()}` sha256 `{digest}`"
        for relative, digest in evidence.inventory
    )
    return f"""You are a fresh independent reviewer of one ICPC blind-solve claim.

Review ID: `{review_id}`
Reviewer ID: `{reviewer_id}`
Attempt ID: `{evidence.attempt_id.as_posix()}`
Candidate SHA-256: `{evidence.source_sha256}`

The current working directory is an isolated review workspace. Read only:
- contestant-visible files below `public/`;
- the untrusted candidate at `candidate/main.cpp`;
- `public-manifest.json`, which inventories the public files below.

Do not inspect the original lane, setter materials, audit artifacts, hidden tests,
the internet, or any path outside this workspace. Do not edit the candidate or
public files. If you accidentally see outside material, mark the review
contaminated and do not verify the claim.

Frozen public inventory:
{inventory_lines}

Independently reconstruct the complete input/output contract. Compile the exact
candidate. Run every public sample that can be recovered from the public
material. Check the algorithm, proof obligations, worst-case time and memory,
numeric bounds, and edge cases. Whenever feasible, implement or use an
independent tiny oracle/exhaustive enumeration and compare it with the compiled
candidate; if that is genuinely infeasible, state a concrete reason. Do not
accept the earlier solver's claim merely because the code looks plausible.

Write `review-report.md`. Keep it compact and include these exact machine fields,
one per line, followed by concise evidence and commands:

review_id: {review_id}
reviewer_id: {reviewer_id}
attempt_id: {evidence.attempt_id.as_posix()}
source_sha256: {evidence.source_sha256}
contamination_status: clean | contaminated
status: verified | rejected | inconclusive
compilation: passed | failed
public_samples: passed | failed | not-available | not-run
contract_review: passed | failed | inconclusive
proof_review: passed | failed | inconclusive
complexity_review: passed | failed | inconclusive
tiny_oracle: passed | failed | not-feasible | not-run
tiny_oracle_reason: <required when not-feasible or not-run>
commands_run: <non-empty compact list of actual commands>

Use `verified` only if the exact source compiled, all available public samples
passed, the full contract/proof/worst-case complexity checks passed, and tiny
checking passed or was concretely shown infeasible. Use `rejected` for a concrete
defect and `inconclusive` when the evidence cannot establish either conclusion.
Do not include hidden chain-of-thought; report only claims and checkable evidence.
"""


def scalar_values(text: str, key: str) -> list[str]:
    matches = re.findall(
        rf"(?im)^\s*(?:[-*]\s*)?(?:\*\*)?{re.escape(key)}"
        rf"(?:\*\*)?\s*[:：]\s*(.*?)\s*$",
        text,
    )
    return [value.strip().strip("`*_ ") for value in matches]


def scalar(text: str, key: str) -> str | None:
    values = scalar_values(text, key)
    if len(values) != 1:
        return None
    return values[0]


def validate_report(
    text: str,
    *,
    evidence: AttemptEvidence,
    review_id: str,
    reviewer_id: str,
) -> tuple[str | None, list[str]]:
    errors: list[str] = []
    expected = {
        "review_id": review_id,
        "reviewer_id": reviewer_id,
        "attempt_id": evidence.attempt_id.as_posix(),
        "source_sha256": evidence.source_sha256,
    }
    values = {key: scalar(text, key) for key in expected}
    for key, expected_value in expected.items():
        if values[key] != expected_value:
            errors.append(f"{key} must be {expected_value!r}")

    machine_keys = {
        *expected,
        "contamination_status",
        "status",
        "compilation",
        "public_samples",
        "contract_review",
        "proof_review",
        "complexity_review",
        "tiny_oracle",
        "tiny_oracle_reason",
        "commands_run",
    }
    for key in sorted(machine_keys):
        count = len(scalar_values(text, key))
        if count > 1:
            errors.append(f"{key} must appear exactly once, found {count}")

    contamination = scalar(text, "contamination_status")
    if contamination != "clean":
        errors.append("contamination_status must be clean")
    status = scalar(text, "status")
    if status not in REVIEW_STATUSES:
        errors.append("status must be verified, rejected, or inconclusive")
        status = None

    checks = {
        "compilation": {"passed", "failed"},
        "public_samples": {"passed", "failed", "not-available", "not-run"},
        "contract_review": {"passed", "failed", "inconclusive"},
        "proof_review": {"passed", "failed", "inconclusive"},
        "complexity_review": {"passed", "failed", "inconclusive"},
        "tiny_oracle": {"passed", "failed", "not-feasible", "not-run"},
    }
    parsed: dict[str, str | None] = {}
    for key, allowed in checks.items():
        parsed[key] = scalar(text, key)
        if parsed[key] not in allowed:
            errors.append(f"{key} must be one of {sorted(allowed)}")
    commands = scalar(text, "commands_run")
    if commands is None or not commands.strip() or commands.casefold() in {"none", "n/a"}:
        errors.append("commands_run must record actual commands")
    if parsed["tiny_oracle"] in {"not-feasible", "not-run"}:
        reason = scalar(text, "tiny_oracle_reason")
        if reason is None or not reason.strip() or reason.casefold() in {"none", "n/a"}:
            errors.append("tiny_oracle_reason is required when tiny checking did not run")
    if status == "verified":
        verified_requirements = {
            "compilation": "passed",
            "contract_review": "passed",
            "proof_review": "passed",
            "complexity_review": "passed",
        }
        for key, required in verified_requirements.items():
            if parsed[key] != required:
                errors.append(f"verified requires {key}: {required}")
        if parsed["public_samples"] not in {"passed", "not-available"}:
            errors.append("verified requires public samples passed or genuinely unavailable")
        if parsed["tiny_oracle"] not in {"passed", "not-feasible"}:
            errors.append("verified requires tiny oracle passed or concretely infeasible")
    return status, errors


def default_command(model: str, reasoning_effort: str, workspace: Path) -> list[str]:
    return [
        "codex",
        "exec",
        "--json",
        "--ephemeral",
        "--skip-git-repo-check",
        "--ignore-user-config",
        "--ignore-rules",
        "--model",
        model,
        "-c",
        f'model_reasoning_effort="{reasoning_effort}"',
        "--sandbox",
        "workspace-write",
        "--cd",
        str(workspace),
        "-",
    ]


def template_command(
    template: str,
    *,
    evidence: AttemptEvidence,
    review_id: str,
    reviewer_id: str,
    model: str,
    workspace: Path,
    prompt_file: Path,
) -> list[str]:
    try:
        tokens = shlex.split(template)
    except ValueError as exc:
        raise ContractError(f"invalid --review-command quoting: {exc}") from exc
    if not tokens:
        raise ContractError("--review-command must not be empty")
    fields = {
        "attempt_id": evidence.attempt_id.as_posix(),
        "model": model,
        "prompt_file": str(prompt_file),
        "review_id": review_id,
        "reviewer_id": reviewer_id,
        "source_sha256": evidence.source_sha256,
        "workspace": str(workspace),
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
            raise ContractError(f"invalid --review-command template: {exc}") from exc
        unsupported = names - ALLOWED_TEMPLATE_FIELDS
        if unsupported:
            raise ContractError(
                "unsupported --review-command placeholder(s): "
                + ", ".join(sorted(unsupported))
            )
        rendered.append(token.format_map(fields))
    return rendered


def write_text_exclusive(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def write_json_exclusive(path: Path, payload: dict[str, Any]) -> None:
    write_text_exclusive(
        path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )


def append_production_review_claim(
    problem_root: Path, claim: dict[str, Any]
) -> str:
    """Append one unique review under an advisory lock and return its new hash."""
    manifest_rel = PurePosixPath("audit/blind-claim-reviews.json")
    manifest_path = safe_problem_path(
        problem_root,
        manifest_rel,
        label="blind claim-review manifest",
        require_exists=False,
    )
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = problem_root / "audit/private/blind-reviews/manifest.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if manifest_path.is_symlink():
            raise ContractError("blind claim-review manifest must not be a symlink")
        if manifest_path.exists():
            data = read_json(manifest_path, label="blind claim-review manifest")
            if not isinstance(data, dict) or set(data) != {
                "schema_version",
                "reviews",
            }:
                raise ContractError(
                    "blind claim-review manifest must contain exactly schema_version/reviews"
                )
            if data.get("schema_version") != 1 or not isinstance(
                data.get("reviews"), list
            ):
                raise ContractError("blind claim-review manifest has invalid schema")
        else:
            data = {"schema_version": 1, "reviews": []}
        reviews = data["reviews"]
        assert isinstance(reviews, list)
        review_id = claim.get("review_id")
        if any(
            isinstance(item, dict) and item.get("review_id") == review_id
            for item in reviews
        ):
            raise ContractError(
                f"blind claim-review manifest already contains review_id {review_id!r}"
            )
        reviews.append(claim)
        encoded = (
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        )
        descriptor, temporary = tempfile.mkstemp(
            prefix=".blind-claim-reviews.", suffix=".tmp", dir=manifest_path.parent
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, manifest_path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    return sha256_file(manifest_path)


def stop_process(process: subprocess.Popen[bytes]) -> str | None:
    """Terminate the review process group, escalating to SIGKILL after 10 seconds."""
    if process.poll() is not None:
        return None
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (OSError, ProcessLookupError):
        try:
            process.terminate()
        except OSError:
            pass
    try:
        process.wait(timeout=10)
        return "terminate"
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            try:
                process.kill()
            except OSError:
                pass
        process.wait()
        return "kill"


def stage_review(
    *,
    evidence: AttemptEvidence,
    review_root: Path,
    workspace: Path,
    prompt_path: Path,
    prompt: str,
) -> tuple[Path, Path]:
    review_root.mkdir(parents=True, exist_ok=False)
    workspace.mkdir()
    public_out = workspace / "public"
    candidate_out = workspace / "candidate"
    public_out.mkdir()
    candidate_out.mkdir()
    (review_root / "raw-trace").mkdir()
    write_text_exclusive(prompt_path, prompt)
    manifest_out = workspace / "public-manifest.json"
    shutil.copy2(evidence.manifest_path, manifest_out, follow_symlinks=False)
    if sha256_file(manifest_out) != evidence.manifest_sha256:
        raise ContractError("staged public manifest hash mismatch")
    for relative, digest in evidence.inventory:
        source = evidence.public_dir.joinpath(*relative.parts)
        destination = public_out.joinpath(*relative.parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination, follow_symlinks=False)
        if sha256_file(destination) != digest:
            raise ContractError(f"staged review public hash mismatch: {relative}")
    candidate = candidate_out / "main.cpp"
    shutil.copy2(evidence.source_path, candidate, follow_symlinks=False)
    if sha256_file(candidate) != evidence.source_sha256:
        raise ContractError("staged review candidate hash mismatch")
    return candidate, manifest_out


def execute(args: argparse.Namespace) -> int:
    problem_root: Path = args.problem_dir
    requested_mode = (
        "test-override" if args.review_command is not None else "production-codex"
    )
    evidence = find_attempt(
        problem_root,
        args.attempt_rel,
        expected_execution_mode=requested_mode,
        expected_time_limit_seconds=args.blind_time_limit_seconds,
    )
    if datetime.now(timezone.utc) >= evidence.blind_deadline:
        raise ContractError(
            "shared blind-stage deadline has expired; refusing to launch a reviewer"
        )
    if args.reviewer_id in {evidence.lane_id, evidence.attempt_id.as_posix()}:
        raise ContractError("--reviewer-id must be distinct from the solver attempt")

    review_base_rel = REVIEW_RECEIPT_ROOT / args.review_id
    review_base = safe_problem_path(
        problem_root,
        review_base_rel,
        label="review run root",
        require_exists=False,
    )
    workspace = review_base / "workspace"
    prompt_path = review_base / "prompt.txt"
    stdout_path = review_base / "raw-trace" / "codex-exec.jsonl"
    stderr_path = review_base / "raw-trace" / "stderr.log"
    workspace_report = workspace / "review-report.md"
    report_rel = REVIEW_REPORT_ROOT / f"{args.review_id}.md"
    report_path = safe_problem_path(
        problem_root, report_rel, label="review report", require_exists=False
    )
    success_receipt_rel = REVIEW_RECEIPT_ROOT / f"{args.review_id}.json"
    failed_receipt_rel = REVIEW_RECEIPT_ROOT / f"{args.review_id}-failed.json"
    test_receipt_rel = REVIEW_RECEIPT_ROOT / f"{args.review_id}-test.json"
    success_receipt = safe_problem_path(
        problem_root,
        success_receipt_rel,
        label="review receipt",
        require_exists=False,
    )
    failed_receipt = safe_problem_path(
        problem_root,
        failed_receipt_rel,
        label="failed review receipt",
        require_exists=False,
    )
    test_receipt = safe_problem_path(
        problem_root,
        test_receipt_rel,
        label="test review receipt",
        require_exists=False,
    )
    for path, label in (
        (review_base, "review run root"),
        (report_path, "review report"),
        (success_receipt, "review receipt"),
        (failed_receipt, "failed review receipt"),
        (test_receipt, "test review receipt"),
    ):
        if path.exists() or path.is_symlink():
            raise ContractError(
                f"refusing to overwrite existing {label}: {path}; use a new review ID"
            )

    prompt = build_prompt(
        evidence=evidence,
        review_id=args.review_id,
        reviewer_id=args.reviewer_id,
    )
    if args.review_command is not None:
        # Validate the template before creating any review artifact.
        template_command(
            args.review_command,
            evidence=evidence,
            review_id=args.review_id,
            reviewer_id=args.reviewer_id,
            model=args.model,
            workspace=workspace,
            prompt_file=prompt_path,
        )

    candidate, staged_manifest = stage_review(
        evidence=evidence,
        review_root=review_base,
        workspace=workspace,
        prompt_path=prompt_path,
        prompt=prompt,
    )
    prompt_digest = sha256_file(prompt_path)
    candidate_digest = sha256_file(candidate)
    staged_manifest_digest = sha256_file(staged_manifest)
    command = (
        template_command(
            args.review_command,
            evidence=evidence,
            review_id=args.review_id,
            reviewer_id=args.reviewer_id,
            model=args.model,
            workspace=workspace,
            prompt_file=prompt_path,
        )
        if args.review_command is not None
        else default_command(args.model, args.reasoning_effort, workspace)
    )

    started_at = utc_now()
    process: subprocess.Popen[bytes] | None = None
    spawn_error: str | None = None
    interrupted = False
    deadline_exceeded = datetime.now(timezone.utc) >= evidence.blind_deadline
    deadline_terminated = False
    termination_action: str | None = None
    if deadline_exceeded:
        spawn_error = "blind-stage deadline expired before reviewer launch"
    else:
        try:
            with (
                prompt_path.open("rb") as stdin_stream,
                stdout_path.open("xb") as stdout_stream,
                stderr_path.open("xb") as stderr_stream,
            ):
                process = subprocess.Popen(
                    command,
                    cwd=workspace,
                    stdin=stdin_stream,
                    stdout=stdout_stream,
                    stderr=stderr_stream,
                    shell=False,
                    start_new_session=True,
                )
        except (OSError, ValueError) as exc:
            spawn_error = f"{type(exc).__name__}: {exc}"
    if process is not None:
        try:
            remaining = (
                evidence.blind_deadline - datetime.now(timezone.utc)
            ).total_seconds()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(command, timeout=0)
            process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            deadline_exceeded = True
            deadline_terminated = process.poll() is None
            termination_action = stop_process(process)
        except KeyboardInterrupt:
            interrupted = True
            stop_process(process)
    if datetime.now(timezone.utc) >= evidence.blind_deadline:
        deadline_exceeded = True
    exit_code = process.returncode if process is not None else None

    report_text: str | None = None
    report_status: str | None = None
    report_errors: list[str] = []
    if workspace_report.is_symlink() or not workspace_report.is_file():
        report_errors.append("review-report.md is missing or not a regular file")
    elif workspace_report.stat().st_size == 0:
        report_errors.append("review-report.md is empty")
    else:
        try:
            report_text = workspace_report.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            report_errors.append(f"cannot read review-report.md: {exc}")
        if report_text is not None:
            report_status, validation_errors = validate_report(
                report_text,
                evidence=evidence,
                review_id=args.review_id,
                reviewer_id=args.reviewer_id,
            )
            report_errors.extend(validation_errors)
            report_path.parent.mkdir(parents=True, exist_ok=True)
            with report_path.open("xb") as output, workspace_report.open("rb") as source:
                shutil.copyfileobj(source, output)
                output.flush()
                os.fsync(output.fileno())

    report_digest = regular_file_sha256(report_path)
    source_unchanged = (
        regular_file_sha256(evidence.source_path) == evidence.source_sha256
    )
    plan_unchanged = regular_file_sha256(evidence.plan_path) == evidence.plan_sha256
    manifest_unchanged = (
        regular_file_sha256(evidence.manifest_path) == evidence.manifest_sha256
    )
    prompt_unchanged = regular_file_sha256(prompt_path) == prompt_digest
    candidate_unchanged = regular_file_sha256(candidate) == candidate_digest
    staged_manifest_unchanged = (
        regular_file_sha256(staged_manifest) == staged_manifest_digest
    )
    blind_clock_unchanged = (
        regular_file_sha256(evidence.blind_clock_path)
        == evidence.blind_clock_sha256
    )
    public_unchanged = True
    try:
        exact_public_surface(workspace / "public", evidence.inventory)
    except ContractError as exc:
        public_unchanged = False
        report_errors.append(str(exc))

    execution_succeeded = (
        not interrupted
        and not deadline_exceeded
        and spawn_error is None
        and exit_code == 0
        and not report_errors
        and report_status in REVIEW_STATUSES
        and source_unchanged
        and plan_unchanged
        and manifest_unchanged
        and prompt_unchanged
        and candidate_unchanged
        and staged_manifest_unchanged
        and blind_clock_unchanged
        and public_unchanged
    )
    receipt_mode = (
        "test-override"
        if args.review_command is not None
        else "production-codex" if execution_succeeded else "failed-production"
    )
    if args.review_command is not None:
        receipt_rel, receipt_path = test_receipt_rel, test_receipt
    elif execution_succeeded:
        receipt_rel, receipt_path = success_receipt_rel, success_receipt
    else:
        receipt_rel, receipt_path = failed_receipt_rel, failed_receipt

    receipt = {
        "schema_version": 1,
        "review_id": args.review_id,
        "attempt_id": evidence.attempt_id.as_posix(),
        "lane_id": evidence.lane_id,
        "reviewer_id": args.reviewer_id,
        "execution_mode": receipt_mode,
        "requested_execution_mode": requested_mode,
        "success": execution_succeeded,
        "status": report_status,
        "exit_code": exit_code,
        "spawn_error": spawn_error,
        "interrupted": interrupted,
        "deadline_terminated": deadline_terminated,
        "termination_action": termination_action,
        "blind_started_at_utc": format_utc(evidence.blind_started_at),
        "blind_deadline_utc": format_utc(evidence.blind_deadline),
        "blind_elapsed_seconds": round(
            max(
                0.0,
                (datetime.now(timezone.utc) - evidence.blind_started_at).total_seconds(),
            ),
            6,
        ),
        "blind_time_limit_seconds": evidence.blind_time_limit_seconds,
        "blind_deadline_exceeded": deadline_exceeded,
        "blind_clock_rel": evidence.blind_clock_rel.as_posix(),
        "blind_clock_sha256": evidence.blind_clock_sha256,
        "blind_clock_unchanged": blind_clock_unchanged,
        "command": command,
        "model": args.model,
        "reasoning_effort": args.reasoning_effort,
        "source_path": evidence.source_rel.as_posix(),
        "source_sha256": evidence.source_sha256,
        "source_unchanged": source_unchanged,
        "plan_rel": evidence.plan_rel.as_posix(),
        "plan_sha256": evidence.plan_sha256,
        "plan_unchanged": plan_unchanged,
        "runner_results_rel": evidence.results_rel.as_posix(),
        "public_manifest_rel": evidence.manifest_rel.as_posix(),
        "public_manifest_sha256": evidence.manifest_sha256,
        "public_manifest_unchanged": manifest_unchanged,
        "review_workspace": review_base_rel.as_posix() + "/workspace",
        "prompt_file": review_base_rel.as_posix() + "/prompt.txt",
        "prompt_sha256": prompt_digest,
        "prompt_unchanged": prompt_unchanged,
        "staged_candidate": review_base_rel.as_posix() + "/workspace/candidate/main.cpp",
        "staged_candidate_sha256": candidate_digest,
        "staged_candidate_unchanged": candidate_unchanged,
        "staged_manifest_sha256": staged_manifest_digest,
        "staged_manifest_unchanged": staged_manifest_unchanged,
        "staged_public_unchanged": public_unchanged,
        "stdout_log": file_receipt(
            stdout_path, review_base_rel / "raw-trace/codex-exec.jsonl"
        ),
        "stderr_log": file_receipt(
            stderr_path, review_base_rel / "raw-trace/stderr.log"
        ),
        "review_report": report_rel.as_posix(),
        "review_report_sha256": report_digest,
        "report_validation_errors": report_errors,
        "started_at_utc": started_at,
        "finished_at_utc": utc_now(),
        "filesystem_read_isolation": False,
        "isolation_mode": "trust-based-fresh-review-workspace",
    }
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    write_json_exclusive(receipt_path, receipt)
    print(f"Wrote review execution receipt: {receipt_rel.as_posix()}")

    if execution_succeeded:
        claim_object = {
            "review_id": args.review_id,
            "attempt_id": evidence.attempt_id.as_posix(),
            "lane_id": evidence.lane_id,
            "claim_type": "full-solution",
            "source_path": evidence.source_rel.as_posix(),
            "source_sha256": evidence.source_sha256,
            "reviewer_id": args.reviewer_id,
            "independent": True,
            "status": report_status,
            "active": True,
            "invalidated_by": None,
            "review_report": report_rel.as_posix(),
            "review_report_sha256": report_digest,
            "execution_receipt": receipt_rel.as_posix(),
        }
        if requested_mode == "test-override":
            print("TEST-ONLY review object; production gate must reject this receipt:")
        else:
            manifest_digest = append_production_review_claim(problem_root, claim_object)
            print(
                "Atomically appended review object to "
                "audit/blind-claim-reviews.json "
                f"(sha256={manifest_digest})."
            )
        print(json.dumps(claim_object, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    print("Review execution failed; workspace and logs were preserved.", file=sys.stderr)
    for error in report_errors:
        print(f"- {error}", file=sys.stderr)
    return 1


def main() -> int:
    args = parse_args()
    try:
        return execute(args)
    except ContractError as exc:
        print(f"run_blind_review.py: error: {exc}", file=sys.stderr)
        return 2
    except FileExistsError as exc:
        print(
            f"run_blind_review.py: refusing to overwrite existing artifact: {exc}",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
