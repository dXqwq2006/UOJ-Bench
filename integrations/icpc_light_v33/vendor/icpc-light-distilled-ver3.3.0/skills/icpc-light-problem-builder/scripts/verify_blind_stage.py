#!/usr/bin/env python3
"""Machine-check the ICPC Light blind-solve stage before preclassification.

This command is an evidence/provenance gate, not a source-code judge.  It
requires real production runner receipts, immutable public inputs and outputs,
at least two clean successful neutral attempts, at least two clean successful
deceptive attempts, and an independently recorded verification of at least one
neutral full-solution claim.  Historical failed, timed-out, or contaminated
attempts remain auditable but do not permanently poison the gate: they are
indexed in the summary and run state, while only successful clean replacements
count toward the minimums.

The runner-owned public manifest is discovered through each matching
``<plan-stem>-results.json``.  Its schema is::

    {
      "schema_version": 1,
      "files": [
        {"path": "statement.md", "sha256": "<64 lowercase hex digits>"}
      ]
    }

All waves must expose exactly the same inventory.  Every staged ``public/``
directory for a counted attempt must contain exactly those files and hashes.

The semantic handoff is ``audit/blind-claim-reviews.json``::

    {
      "schema_version": 1,
      "reviews": [{
        "review_id": "review-neutral-01",
        "attempt_id": "blind-solves/icpc-light/neutral-01/workspace",
        "lane_id": "neutral-01",
        "claim_type": "full-solution",
        "source_path": "blind-solves/icpc-light/neutral-01/workspace/main.cpp",
        "source_sha256": "<64 lowercase hex digits>",
        "reviewer_id": "independent-review-01",
        "independent": true,
        "status": "verified",
        "active": true,
        "invalidated_by": null,
        "review_report": "audit/blind-review-neutral-01.md",
        "review_report_sha256": "<64 lowercase hex digits>",
        "execution_receipt": "audit/private/blind-reviews/review-neutral-01.json"
      }]
    }

The execution receipt is a JSON object with ``schema_version: 1`` and fields
``review_id``, ``attempt_id``, ``reviewer_id``, ``execution_mode:
production-codex``, ``exit_code: 0``, non-empty ``command`` beginning with a
Codex executable and ``exec``, plus the same ``source_path``, ``source_sha256``,
``review_report``, and ``review_report_sha256``.  This proves that the expected
review process and bound artifacts were recorded; it still cannot
cryptographically prove reviewer independence or algorithmic correctness.

Exit zero means the blind stage may proceed to preclassification.  Exit one
prints every discovered issue.  Argument errors exit two.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from contamination_status import parse_contamination_status


BLIND_ROOT_REL = "blind-solves/icpc-light"
PLAN_NAME_RE = re.compile(r"sweep-plan(?:-wave-(\d{2,}))?\.json")
PLANNER = "icpc-light-public-blind-solve-sweep"
RUNNER = "icpc-light-public-blind-solve-runner"
REQUIRED_MODEL = "gpt-5.6-sol"
REQUIRED_REASONING_EFFORT = "ultra"
PRODUCTION_BLIND_TIME_LIMIT_SECONDS = 7200
MIN_CLEAN_NEUTRAL = 2
MIN_CLEAN_DECEPTIVE = 2
HASH_RE = re.compile(r"[0-9a-f]{64}")
CLAIM_REVIEW_REL = "audit/blind-claim-reviews.json"
BLIND_SUMMARY_REL = "audit/blind-summary.md"
RUN_STATE_REL = "audit/run-state.md"
REVIEW_STATUSES = {"verified", "rejected", "inconclusive"}
PLAN_PHASES = {"initial", "replacement", "focused-neutral", "focused-deceptive"}
CODEX_JSONL_FAILURE_TYPES = {"turn.failed", "thread.failed"}

FORBIDDEN_PUBLIC_ROOTS = {
    ".git",
    "adversarial",
    "audit",
    "blind-solves",
    "data",
    "hidden",
    "package",
    "private",
    "solutions",
    "tests",
    "tools",
}
FORBIDDEN_PUBLIC_BASENAMES = {
    "answer.cpp",
    "brute.cpp",
    "checker.cpp",
    "editorial.md",
    "generator.cpp",
    "sol.cpp",
    "sol.md",
    "solution.cpp",
    "solution.md",
    "std.cpp",
    "validator.cpp",
}
FORBIDDEN_PUBLIC_STEMS = {
    "answer",
    "brute",
    "checker",
    "editorial",
    "generator",
    "official-solution",
    "sol",
    "solution",
    "std",
    "validator",
}


@dataclass
class Attempt:
    plan_rel: str
    lane_id: str
    kind: str
    phase: str
    wave: int
    attempt_id: str
    workspace: Path
    public_dir: Path
    prompt_path: Path
    stdout_path: Path
    stderr_path: Path
    required_rel: list[str]
    required_paths: list[Path]
    runner_success: bool = False
    stage_status: str = "unknown"
    public_ok: bool = False
    outputs_ok: bool = False
    status_clean: bool = False
    clean_and_complete: bool = False


@dataclass
class PlanEvidence:
    plan_rel: str
    wave: int
    inventory: tuple[tuple[str, str], ...]
    blind_started_at: datetime | None = None
    blind_deadline: datetime | None = None
    finished_at: datetime | None = None
    deadline_exceeded: bool = False
    attempts: list[Attempt] = field(default_factory=list)


class Gate:
    def __init__(self, problem_dir: Path) -> None:
        self.problem_dir = problem_dir
        self.issues: list[str] = []
        self.max_observed_blind_elapsed_seconds = 0.0

    def error(self, message: str) -> None:
        self.issues.append(message)

    def safe_problem_path(self, raw: Any, label: str) -> tuple[str, Path] | None:
        if not isinstance(raw, str) or not raw.strip():
            self.error(f"{label}: expected a non-empty problem-relative path")
            return None
        if "\\" in raw:
            self.error(f"{label}: backslashes are not allowed: {raw!r}")
            return None
        pure = PurePosixPath(raw)
        if pure.is_absolute() or pure == PurePosixPath(".") or ".." in pure.parts:
            self.error(f"{label}: path must stay below the problem root: {raw!r}")
            return None
        normalized = pure.as_posix()
        if normalized != raw:
            self.error(f"{label}: path must use normalized POSIX syntax: {raw!r}")
            return None
        candidate = self.problem_dir.joinpath(*pure.parts)
        current = self.problem_dir
        for part in pure.parts:
            current /= part
            if current.is_symlink():
                self.error(f"{label}: symbolic links are not allowed: {normalized}")
                return None
        try:
            candidate.resolve(strict=False).relative_to(self.problem_dir)
        except (OSError, RuntimeError, ValueError):
            self.error(f"{label}: path resolves outside the problem root: {raw!r}")
            return None
        return normalized, candidate

    def require_nonempty_file(self, path: Path, label: str) -> bool:
        if path.is_symlink():
            self.error(f"{label}: symbolic links are not accepted: {path}")
            return False
        if not path.exists():
            self.error(f"{label}: missing file: {path}")
            return False
        if not path.is_file():
            self.error(f"{label}: expected a regular file: {path}")
            return False
        try:
            size = path.stat().st_size
        except OSError as exc:
            self.error(f"{label}: cannot stat {path}: {exc}")
            return False
        if size == 0:
            self.error(f"{label}: file is empty: {path}")
            return False
        return True

    def load_json(self, path: Path, label: str) -> Any | None:
        if not self.require_nonempty_file(path, label):
            return None
        try:
            with path.open("r", encoding="utf-8") as stream:
                return json.load(stream)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            self.error(f"{label}: invalid UTF-8 JSON in {path}: {exc}")
            return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Block ICPC Light preclassification until real production blind-solve "
            "receipts prove at least 2 clean neutral lanes, 2 clean deceptive "
            "lanes, and 1 independently verified neutral full-solution claim."
        ),
        epilog=(
            "This checks immutable artifacts and recorded review state; it does "
            "not judge algorithm correctness. See the module docstring for JSON "
            "schemas."
        ),
    )
    parser.add_argument(
        "--problem-dir",
        type=Path,
        required=True,
        help="Problem root containing statement.md, blind-solves/, and audit/.",
    )
    args = parser.parse_args()
    if not args.problem_dir.exists():
        parser.error(f"problem directory does not exist: {args.problem_dir}")
    if not args.problem_dir.is_dir():
        parser.error(f"problem directory is not a directory: {args.problem_dir}")
    if args.problem_dir.is_symlink():
        parser.error("--problem-dir itself must not be a symbolic link")
    args.problem_dir = args.problem_dir.resolve()
    return args


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def valid_hash(value: Any) -> bool:
    return isinstance(value, str) and HASH_RE.fullmatch(value) is not None


def parse_utc_timestamp(gate: Gate, raw: Any, label: str) -> datetime | None:
    """Parse the runner's canonical ``...Z`` timestamps as aware UTC values."""
    if not isinstance(raw, str) or not raw.endswith("Z"):
        gate.error(f"{label}: expected a canonical UTC timestamp ending in Z")
        return None
    try:
        value = datetime.fromisoformat(raw[:-1] + "+00:00")
    except ValueError:
        gate.error(f"{label}: invalid UTC timestamp {raw!r}")
        return None
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        gate.error(f"{label}: timestamp must be UTC")
        return None
    return value.astimezone(timezone.utc)


def exact_number(raw: Any, expected: float) -> bool:
    return (
        isinstance(raw, (int, float))
        and not isinstance(raw, bool)
        and float(raw) == float(expected)
    )


def nonnegative_finite_number(raw: Any) -> bool:
    return (
        isinstance(raw, (int, float))
        and not isinstance(raw, bool)
        and 0.0 <= float(raw) < float("inf")
    )


def is_below(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def production_codex_command(value: Any) -> bool:
    if not isinstance(value, list) or len(value) < 2:
        return False
    if not all(isinstance(item, str) and item for item in value):
        return False
    return Path(value[0]).name == "codex" and value[1] == "exec"


def required_sweep_command(value: Any) -> bool:
    """Require the production Codex command to carry the frozen model settings."""
    if not production_codex_command(value):
        return False
    assert isinstance(value, list)
    model_indexes = [index for index, item in enumerate(value) if item == "--model"]
    if len(model_indexes) != 1:
        return False
    model_index = model_indexes[0]
    if model_index + 1 >= len(value) or value[model_index + 1] != REQUIRED_MODEL:
        return False
    effort_values = [
        value[index + 1]
        for index, item in enumerate(value[:-1])
        if item in {"-c", "--config"}
        and value[index + 1].startswith("model_reasoning_effort=")
    ]
    return effort_values == [
        f'model_reasoning_effort="{REQUIRED_REASONING_EFFORT}"'
    ]


def status_contamination(text: str) -> str | None:
    return parse_contamination_status(text)


def current_file_receipt(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        return {"status": "unsafe-symlink", "size": None, "sha256": None}
    if not path.exists():
        return {"status": "missing", "size": None, "sha256": None}
    try:
        info = path.stat()
    except OSError:
        return {"status": "unreadable", "size": None, "sha256": None}
    if not stat.S_ISREG(info.st_mode):
        return {"status": "not-regular", "size": info.st_size, "sha256": None}
    try:
        digest = sha256_file(path)
    except OSError:
        return {"status": "unreadable", "size": info.st_size, "sha256": None}
    status = "present-nonempty" if info.st_size > 0 else "empty"
    return {"status": status, "size": info.st_size, "sha256": digest}


def validate_completed_codex_jsonl(gate: Gate, path: Path, label: str) -> bool:
    """Require one completed turn, allowing audited recoverable API errors."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        gate.error(f"{label}: cannot read Codex JSONL: {exc}")
        return False
    event_types: list[str] = []
    ok = True
    for line_number, raw in enumerate(lines, 1):
        if not raw.strip():
            continue
        try:
            event = json.loads(raw)
        except json.JSONDecodeError as exc:
            gate.error(f"{label}: line {line_number} is invalid JSON: {exc.msg}")
            ok = False
            continue
        event_type = event.get("type") if isinstance(event, dict) else None
        if not isinstance(event_type, str) or not event_type:
            gate.error(f"{label}: line {line_number} lacks a non-empty event type")
            ok = False
            continue
        event_types.append(event_type)
    expected_counts = {
        "thread.started": 1,
        "turn.started": 1,
        "turn.completed": 1,
    }
    for event_type, expected in expected_counts.items():
        actual = event_types.count(event_type)
        if actual != expected:
            gate.error(
                f"{label}: expected {expected} {event_type} event, found {actual}"
            )
            ok = False
    failures = [item for item in event_types if item in CODEX_JSONL_FAILURE_TYPES]
    if failures:
        gate.error(f"{label}: contains explicit failure event(s): {failures}")
        ok = False
    if not event_types or event_types[-1] != "turn.completed":
        gate.error(f"{label}: does not end with turn.completed")
        ok = False
    return ok


def validate_receipt_item(
    gate: Gate, raw: Any, expected_rel: str, label: str, *, require_nonempty: bool
) -> bool:
    if not isinstance(raw, dict):
        gate.error(f"{label}: expected a receipt object")
        return False
    if raw.get("path") != expected_rel:
        gate.error(f"{label}.path: expected {expected_rel!r}")
        return False
    resolved = gate.safe_problem_path(expected_rel, f"{label}.path")
    if resolved is None:
        return False
    _, path = resolved
    current = current_file_receipt(path)
    ok = True
    for key in ("status", "size", "sha256"):
        if raw.get(key) != current[key]:
            gate.error(
                f"{label}.{key}: runner recorded {raw.get(key)!r}, "
                f"current artifact is {current[key]!r}"
            )
            ok = False
    if require_nonempty and current["status"] != "present-nonempty":
        gate.error(f"{label}: successful attempt requires a non-empty regular file")
        ok = False
    return ok


def forbidden_public_path(path: str) -> bool:
    pure = PurePosixPath(path)
    lower_parts = [part.lower() for part in pure.parts]
    if not lower_parts:
        return True
    if lower_parts[0] in FORBIDDEN_PUBLIC_ROOTS:
        return True
    if any(part.startswith(".") for part in lower_parts):
        return True
    basename = lower_parts[-1]
    return (
        basename in FORBIDDEN_PUBLIC_BASENAMES
        or PurePosixPath(basename).stem in FORBIDDEN_PUBLIC_STEMS
    )


def load_public_manifest(
    gate: Gate, path: Path, label: str
) -> tuple[tuple[str, str], ...]:
    data = gate.load_json(path, label)
    if not isinstance(data, dict):
        if data is not None:
            gate.error(f"{label}: top level must be an object")
        return ()
    if set(data) != {"schema_version", "files"}:
        gate.error(f"{label}: only schema_version and files are allowed")
    if type(data.get("schema_version")) is not int or data.get("schema_version") != 1:
        gate.error(f"{label}: schema_version must be integer 1")
    raw_files = data.get("files")
    if not isinstance(raw_files, list) or not raw_files:
        gate.error(f"{label}: files must be a non-empty array")
        return ()
    inventory: list[tuple[str, str]] = []
    seen: set[str] = set()
    for index, item in enumerate(raw_files):
        item_label = f"{label}.files[{index}]"
        if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
            gate.error(f"{item_label}: expected exactly path and sha256")
            continue
        resolved = gate.safe_problem_path(item.get("path"), f"{item_label}.path")
        if resolved is None:
            continue
        relative, source = resolved
        if relative in seen:
            gate.error(f"{item_label}.path: duplicate {relative!r}")
            continue
        seen.add(relative)
        if forbidden_public_path(relative):
            gate.error(f"{item_label}.path: private-looking path is forbidden: {relative}")
        digest = item.get("sha256")
        if not valid_hash(digest):
            gate.error(f"{item_label}.sha256: expected 64 lowercase hex digits")
            continue
        if gate.require_nonempty_file(source, f"{item_label} source"):
            try:
                actual = sha256_file(source)
            except OSError as exc:
                gate.error(f"{item_label}: cannot hash source: {exc}")
            else:
                if actual != digest:
                    gate.error(
                        f"{item_label}: source hash mismatch; expected {digest}, got {actual}"
                    )
        inventory.append((relative, digest))
    inventory.sort()
    if not any(relative == "statement.md" for relative, _ in inventory):
        gate.error(f"{label}: must inventory problem-root statement.md")
    return tuple(inventory)


def validate_public_workspace(
    gate: Gate,
    public_dir: Path,
    inventory: tuple[tuple[str, str], ...],
    label: str,
) -> bool:
    if public_dir.is_symlink() or not public_dir.is_dir():
        gate.error(f"{label}: missing regular public directory: {public_dir}")
        return False
    expected = {relative: digest for relative, digest in inventory}
    actual_files: set[str] = set()
    ok = True
    try:
        entries = list(public_dir.rglob("*"))
    except OSError as exc:
        gate.error(f"{label}: cannot enumerate public directory: {exc}")
        return False
    for path in entries:
        relative = path.relative_to(public_dir).as_posix()
        if path.is_symlink():
            gate.error(f"{label}: public surface contains symbolic link: {relative}")
            ok = False
        elif path.is_file():
            actual_files.add(relative)
        elif not path.is_dir():
            gate.error(f"{label}: public surface contains non-file entry: {relative}")
            ok = False
    for extra in sorted(actual_files - set(expected)):
        gate.error(f"{label}: manifest-external public file is forbidden: {extra}")
        ok = False
    for missing in sorted(set(expected) - actual_files):
        gate.error(f"{label}: staged public file is missing: {missing}")
        ok = False
    for relative in sorted(actual_files & set(expected)):
        path = public_dir.joinpath(*PurePosixPath(relative).parts)
        if not gate.require_nonempty_file(path, f"{label} staged {relative}"):
            ok = False
            continue
        try:
            digest = sha256_file(path)
        except OSError as exc:
            gate.error(f"{label}: cannot hash staged {relative}: {exc}")
            ok = False
            continue
        if digest != expected[relative]:
            gate.error(f"{label}: staged hash mismatch: {relative}")
            ok = False
    return ok


def load_plan_paths(gate: Gate) -> list[Path]:
    root = gate.problem_dir / BLIND_ROOT_REL
    if root.is_symlink():
        gate.error(f"{BLIND_ROOT_REL}: symbolic links are not accepted")
        return []
    if not root.is_dir():
        gate.error(f"{BLIND_ROOT_REL}: missing directory: {root}")
        return []
    paths = sorted(
        path
        for path in root.iterdir()
        if path.is_file() and not path.is_symlink() and PLAN_NAME_RE.fullmatch(path.name)
    )
    if not paths:
        gate.error(f"sweep plans: none found directly below {root}")
    return paths


def plan_run_paths(
    gate: Gate, run: dict[str, Any], plan_rel: str, index: int
) -> dict[str, tuple[str, Path]] | None:
    label = f"sweep plan {plan_rel}.runs[{index}]"
    lane_id = run.get("id")
    if not isinstance(lane_id, str) or not lane_id.strip():
        gate.error(f"{label}.id: expected a non-empty string")
        return None
    fields: dict[str, tuple[str, Path]] = {}
    for key in (
        "workspace_rel",
        "public_materials_rel",
        "prompt_file_rel",
        "launch_log_rel",
        "stderr_log_rel",
    ):
        resolved = gate.safe_problem_path(run.get(key), f"{label}.{key}")
        if resolved is None:
            return None
        fields[key] = resolved
    workspace_rel, workspace = fields["workspace_rel"]
    run_root = PurePosixPath(BLIND_ROOT_REL) / lane_id
    expected = {
        "workspace_rel": (run_root / "workspace").as_posix(),
        "public_materials_rel": (run_root / "workspace" / "public").as_posix(),
        "prompt_file_rel": (run_root / "prompt.txt").as_posix(),
        "launch_log_rel": (run_root / "raw-trace" / "codex-exec.jsonl").as_posix(),
        "stderr_log_rel": (run_root / "raw-trace" / "stderr.log").as_posix(),
    }
    for key, expected_value in expected.items():
        if fields[key][0] != expected_value:
            gate.error(f"{label}.{key}: expected {expected_value!r}")
    if not is_below(fields["public_materials_rel"][1], workspace):
        gate.error(f"{label}: public_materials_rel is outside workspace")
    return fields


def result_path_for(plan_path: Path) -> Path:
    return plan_path.with_name(f"{plan_path.stem}-results.json")


def validate_production_timing(
    gate: Gate,
    raw: dict[str, Any],
    label: str,
    *,
    require_clock: bool = True,
) -> tuple[
    datetime | None,
    datetime | None,
    datetime | None,
    datetime | None,
    bool,
]:
    """Validate one immutable runner/reviewer receipt against the 120-minute cap."""
    started = parse_utc_timestamp(
        gate, raw.get("blind_started_at_utc"), f"{label}.blind_started_at_utc"
    )
    deadline = parse_utc_timestamp(
        gate, raw.get("blind_deadline_utc"), f"{label}.blind_deadline_utc"
    )
    finished = parse_utc_timestamp(
        gate, raw.get("finished_at_utc"), f"{label}.finished_at_utc"
    )
    invocation_started = parse_utc_timestamp(
        gate, raw.get("started_at_utc"), f"{label}.started_at_utc"
    )
    if not exact_number(
        raw.get("blind_time_limit_seconds"), PRODUCTION_BLIND_TIME_LIMIT_SECONDS
    ):
        gate.error(
            f"{label}.blind_time_limit_seconds: expected numeric "
            f"{PRODUCTION_BLIND_TIME_LIMIT_SECONDS}"
        )
    elapsed = raw.get("blind_elapsed_seconds")
    if not nonnegative_finite_number(elapsed):
        gate.error(f"{label}.blind_elapsed_seconds: expected a non-negative number")
    elif float(elapsed) > PRODUCTION_BLIND_TIME_LIMIT_SECONDS:
        gate.error(f"{label}.blind_elapsed_seconds: exceeds the 120-minute limit")
    else:
        gate.max_observed_blind_elapsed_seconds = max(
            gate.max_observed_blind_elapsed_seconds, float(elapsed)
        )
    deadline_exceeded = raw.get("blind_deadline_exceeded") is True
    if raw.get("blind_deadline_exceeded") is not False:
        gate.error(f"{label}.blind_deadline_exceeded: production PASS requires false")
    expected_clock = f"{BLIND_ROOT_REL}/blind-time-budget.json"
    if require_clock and raw.get("blind_clock_rel") != expected_clock:
        gate.error(f"{label}.blind_clock_rel: expected {expected_clock!r}")
    if not require_clock and "blind_clock_rel" in raw and raw.get(
        "blind_clock_rel"
    ) != expected_clock:
        gate.error(f"{label}.blind_clock_rel: expected {expected_clock!r}")
    if started is not None and deadline is not None:
        actual_limit = (deadline - started).total_seconds()
        if abs(actual_limit - PRODUCTION_BLIND_TIME_LIMIT_SECONDS) > 0.001:
            gate.error(
                f"{label}: blind deadline must be exactly "
                f"{PRODUCTION_BLIND_TIME_LIMIT_SECONDS} seconds after start"
            )
    if invocation_started is not None and finished is not None:
        if finished < invocation_started:
            gate.error(f"{label}: finished_at_utc precedes started_at_utc")
    if started is not None and finished is not None and nonnegative_finite_number(elapsed):
        timestamp_elapsed = max(0.0, (finished - started).total_seconds())
        if abs(float(elapsed) - timestamp_elapsed) > 2.0:
            gate.error(
                f"{label}.blind_elapsed_seconds is inconsistent with blind start/finish timestamps"
            )
    if finished is not None and deadline is not None and finished > deadline:
        gate.error(f"{label}: completion occurred after the blind deadline")
        deadline_exceeded = True
    return started, deadline, invocation_started, finished, deadline_exceeded


def validate_plan_and_results(gate: Gate, plan_path: Path) -> PlanEvidence | None:
    plan_rel = plan_path.relative_to(gate.problem_dir).as_posix()
    plan = gate.load_json(plan_path, f"sweep plan {plan_rel}")
    if not isinstance(plan, dict):
        if plan is not None:
            gate.error(f"sweep plan {plan_rel}: top level must be an object")
        return None
    if type(plan.get("schema_version")) is not int or plan.get("schema_version") != 2:
        gate.error(f"sweep plan {plan_rel}: schema_version must be integer 2")
    if plan.get("planner") != PLANNER:
        gate.error(f"sweep plan {plan_rel}: planner must be {PLANNER!r}")
    if plan.get("path_base") != "problem_dir":
        gate.error(f"sweep plan {plan_rel}: path_base must be 'problem_dir'")
    if plan.get("workspace_root_rel") != BLIND_ROOT_REL:
        gate.error(
            f"sweep plan {plan_rel}: workspace_root_rel must be {BLIND_ROOT_REL!r}"
        )
    if plan.get("model") != REQUIRED_MODEL:
        gate.error(
            f"sweep plan {plan_rel}: model must be {REQUIRED_MODEL!r}, "
            f"got {plan.get('model')!r}"
        )
    if plan.get("reasoning_effort") != REQUIRED_REASONING_EFFORT:
        gate.error(
            f"sweep plan {plan_rel}: reasoning_effort must be "
            f"{REQUIRED_REASONING_EFFORT!r}, got {plan.get('reasoning_effort')!r}"
        )
    phase = plan.get("phase")
    wave = plan.get("wave")
    if phase not in PLAN_PHASES:
        gate.error(f"sweep plan {plan_rel}: unsupported phase {phase!r}")
    if not isinstance(wave, int) or isinstance(wave, bool) or wave < 1:
        gate.error(f"sweep plan {plan_rel}: wave must be a positive integer")
        return None
    expected_name = "sweep-plan.json" if wave == 1 else f"sweep-plan-wave-{wave:02d}.json"
    if plan_path.name != expected_name:
        gate.error(f"sweep plan {plan_rel}: wave {wave} must use {expected_name!r}")
    if (wave == 1) != (phase == "initial"):
        gate.error(f"sweep plan {plan_rel}: only wave 1 may be initial")

    results_path = result_path_for(plan_path)
    results_rel = results_path.relative_to(gate.problem_dir).as_posix()
    results = gate.load_json(results_path, f"runner results {results_rel}")
    if not isinstance(results, dict):
        if results is not None:
            gate.error(f"runner results {results_rel}: top level must be an object")
        return PlanEvidence(plan_rel, wave, ())
    rlabel = f"runner results {results_rel}"
    if (
        type(results.get("schema_version")) is not int
        or results.get("schema_version") != 1
    ):
        gate.error(f"{rlabel}: schema_version must be integer 1")
    if results.get("runner") != RUNNER:
        gate.error(f"{rlabel}: runner must be {RUNNER!r}")
    if results.get("execution_mode") != "production-codex":
        gate.error(f"{rlabel}: production gate rejects non-production execution_mode")
    (
        blind_started,
        blind_deadline,
        _invocation_started,
        finished_at,
        deadline_exceeded,
    ) = (
        validate_production_timing(gate, results, rlabel)
    )
    if results.get("plan_rel") != plan_rel:
        gate.error(f"{rlabel}: plan_rel must be {plan_rel!r}")
    try:
        plan_digest = sha256_file(plan_path)
    except OSError as exc:
        gate.error(f"{rlabel}: cannot hash plan: {exc}")
        plan_digest = None
    if results.get("plan_sha256") != plan_digest or not valid_hash(
        results.get("plan_sha256")
    ):
        gate.error(f"{rlabel}: plan_sha256 does not bind the current plan")
    if results.get("plan_unchanged") is not True:
        gate.error(f"{rlabel}: plan_unchanged must be true")

    manifest_resolved = gate.safe_problem_path(
        results.get("public_manifest_rel"), f"{rlabel}.public_manifest_rel"
    )
    inventory: tuple[tuple[str, str], ...] = ()
    if manifest_resolved is not None:
        manifest_rel, manifest_path = manifest_resolved
        inventory = load_public_manifest(gate, manifest_path, f"{rlabel} canonical manifest")
        try:
            manifest_digest = sha256_file(manifest_path)
        except OSError as exc:
            gate.error(f"{rlabel}: cannot hash canonical public manifest: {exc}")
            manifest_digest = None
        if results.get("public_manifest_sha256") != manifest_digest or not valid_hash(
            results.get("public_manifest_sha256")
        ):
            gate.error(f"{rlabel}: public_manifest_sha256 does not bind {manifest_rel}")
        if results.get("public_manifest_unchanged") is not True:
            gate.error(f"{rlabel}: public_manifest_unchanged must be true")
        expected_public_files = [
            {"path": relative, "sha256": digest} for relative, digest in inventory
        ]
        if results.get("public_files") != expected_public_files:
            gate.error(f"{rlabel}: public_files differs from canonical manifest")

    input_manifest_resolved = gate.safe_problem_path(
        results.get("input_public_manifest_rel"),
        f"{rlabel}.input_public_manifest_rel",
    )
    if input_manifest_resolved is not None:
        _, input_manifest_path = input_manifest_resolved
        if gate.require_nonempty_file(input_manifest_path, f"{rlabel} input manifest"):
            try:
                input_digest = sha256_file(input_manifest_path)
            except OSError as exc:
                gate.error(f"{rlabel}: cannot hash input manifest: {exc}")
            else:
                if results.get("input_public_manifest_sha256") != input_digest:
                    gate.error(f"{rlabel}: input manifest hash does not match receipt")

    if results.get("semantic_verification") != "not-performed":
        gate.error(f"{rlabel}: runner must not claim semantic verification")
    if results.get("semantic_review_artifact") != CLAIM_REVIEW_REL:
        gate.error(f"{rlabel}: semantic_review_artifact must be {CLAIM_REVIEW_REL!r}")
    if results.get("isolation_mode") != "trust-based-public-workspace":
        gate.error(f"{rlabel}: unsupported isolation_mode")
    if results.get("filesystem_read_isolation") is not False:
        gate.error(f"{rlabel}: filesystem_read_isolation receipt must be JSON false")

    runs = plan.get("runs")
    result_runs = results.get("runs")
    if not isinstance(runs, list) or not runs:
        gate.error(f"sweep plan {plan_rel}: runs must be a non-empty array")
        return PlanEvidence(plan_rel, wave, inventory)
    if not isinstance(result_runs, list):
        gate.error(f"{rlabel}: runs must be an array")
        result_runs = []
    result_by_id: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(result_runs):
        if not isinstance(item, dict):
            gate.error(f"{rlabel}.runs[{index}]: expected an object")
            continue
        run_id = item.get("id")
        if not isinstance(run_id, str) or not run_id:
            gate.error(f"{rlabel}.runs[{index}].id: expected non-empty string")
            continue
        if run_id in result_by_id:
            gate.error(f"{rlabel}: duplicate result run ID {run_id!r}")
        result_by_id[run_id] = item

    evidence = PlanEvidence(
        plan_rel=plan_rel,
        wave=wave,
        inventory=inventory,
        blind_started_at=blind_started,
        blind_deadline=blind_deadline,
        finished_at=finished_at,
        deadline_exceeded=deadline_exceeded,
    )
    plan_ids: set[str] = set()
    kind_counts: Counter[str] = Counter()
    for index, run in enumerate(runs):
        label = f"sweep plan {plan_rel}.runs[{index}]"
        if not isinstance(run, dict):
            gate.error(f"{label}: expected an object")
            continue
        lane_id = run.get("id")
        kind = run.get("kind")
        if not isinstance(lane_id, str) or not lane_id:
            gate.error(f"{label}.id: expected a non-empty string")
            continue
        if lane_id in plan_ids:
            gate.error(f"sweep plan {plan_rel}: duplicate lane ID {lane_id!r}")
            continue
        plan_ids.add(lane_id)
        if kind not in {"neutral", "deceptive"}:
            gate.error(f"{label}.kind: expected neutral or deceptive")
            continue
        kind_counts[kind] += 1
        if run.get("phase") != phase or run.get("wave") != wave:
            gate.error(f"{label}: phase/wave differs from plan")
        if run.get("model") != REQUIRED_MODEL:
            gate.error(f"{label}.model: must be {REQUIRED_MODEL!r}")
        if run.get("reasoning_effort") != REQUIRED_REASONING_EFFORT:
            gate.error(
                f"{label}.reasoning_effort: must be {REQUIRED_REASONING_EFFORT!r}"
            )
        paths = plan_run_paths(gate, run, plan_rel, index)
        if paths is None:
            continue
        workspace_rel, workspace = paths["workspace_rel"]

        required_raw = run.get("required_outputs")
        required_rel: list[str] = []
        required_paths: list[Path] = []
        if not isinstance(required_raw, list):
            gate.error(f"{label}.required_outputs: expected an array")
        else:
            for output_index, raw_output in enumerate(required_raw):
                resolved = gate.safe_problem_path(
                    raw_output, f"{label}.required_outputs[{output_index}]"
                )
                if resolved is None:
                    continue
                output_rel, output_path = resolved
                if output_path.parent != workspace:
                    gate.error(f"{label}: required output must be directly in workspace")
                    continue
                required_rel.append(output_rel)
                required_paths.append(output_path)
        expected_names = (
            {"main.cpp", "final-status.md"} if kind == "neutral" else {"final-status.md"}
        )
        if {Path(value).name for value in required_rel} != expected_names:
            gate.error(f"{label}: required_outputs violates {kind} contract")

        attempt = Attempt(
            plan_rel=plan_rel,
            lane_id=lane_id,
            kind=kind,
            phase=str(phase),
            wave=wave,
            attempt_id=workspace_rel,
            workspace=workspace,
            public_dir=paths["public_materials_rel"][1],
            prompt_path=paths["prompt_file_rel"][1],
            stdout_path=paths["launch_log_rel"][1],
            stderr_path=paths["stderr_log_rel"][1],
            required_rel=required_rel,
            required_paths=required_paths,
        )
        result = result_by_id.get(lane_id)
        if result is None:
            gate.error(f"{rlabel}: missing result for lane {lane_id!r}")
            evidence.attempts.append(attempt)
            continue
        for key, expected in (
            ("attempt_id", workspace_rel),
            ("workspace_rel", workspace_rel),
            ("kind", kind),
            ("phase", phase),
            ("wave", wave),
            ("model", run.get("model")),
            ("reasoning_effort", run.get("reasoning_effort")),
            ("prompt_file_rel", paths["prompt_file_rel"][0]),
            ("stdout_log_rel", paths["launch_log_rel"][0]),
            ("stderr_log_rel", paths["stderr_log_rel"][0]),
        ):
            if result.get(key) != expected:
                gate.error(f"{rlabel} lane {lane_id}.{key}: expected {expected!r}")
        attempt.stage_status = str(result.get("stage_status"))
        attempt.runner_success = result.get("success") is True
        if attempt.runner_success:
            if attempt.stage_status != "launched":
                gate.error(f"{rlabel} lane {lane_id}: successful lane was not launched")
            if result.get("exit_code") != 0 or isinstance(result.get("exit_code"), bool):
                gate.error(f"{rlabel} lane {lane_id}: success requires integer exit_code 0")
            if result.get("spawn_error") is not None:
                gate.error(f"{rlabel} lane {lane_id}: success requires null spawn_error")
            if not required_sweep_command(result.get("command")):
                gate.error(
                    f"{rlabel} lane {lane_id}: no production codex exec receipt "
                    f"for {REQUIRED_MODEL}/{REQUIRED_REASONING_EFFORT}"
                )
            prompt_hash = result.get("prompt_sha256")
            if not valid_hash(prompt_hash):
                gate.error(f"{rlabel} lane {lane_id}: invalid prompt_sha256")
            elif current_file_receipt(attempt.prompt_path)["sha256"] != prompt_hash:
                gate.error(f"{rlabel} lane {lane_id}: prompt hash changed after execution")
            if result.get("prompt_unchanged") is not True:
                gate.error(f"{rlabel} lane {lane_id}: prompt_unchanged must be true")
            expected_prompt = run.get("prompt")
            if isinstance(expected_prompt, str) and gate.require_nonempty_file(
                attempt.prompt_path, f"{rlabel} lane {lane_id} prompt"
            ):
                try:
                    actual_prompt = attempt.prompt_path.read_text(encoding="utf-8")
                except (OSError, UnicodeError) as exc:
                    gate.error(f"{rlabel} lane {lane_id}: cannot read prompt: {exc}")
                else:
                    if actual_prompt != expected_prompt + "\n":
                        gate.error(f"{rlabel} lane {lane_id}: prompt differs from plan")

            stdout_raw = result.get("stdout_log")
            stdout_ok = validate_receipt_item(
                gate,
                stdout_raw,
                paths["launch_log_rel"][0],
                f"{rlabel} lane {lane_id}.stdout_log",
                require_nonempty=True,
            )
            if stdout_ok and not validate_completed_codex_jsonl(
                gate,
                attempt.stdout_path,
                f"{rlabel} lane {lane_id}.stdout_log",
            ):
                stdout_ok = False
            validate_receipt_item(
                gate,
                result.get("stderr_log"),
                paths["stderr_log_rel"][0],
                f"{rlabel} lane {lane_id}.stderr_log",
                require_nonempty=False,
            )
            result_required = result.get("required_outputs")
            result_required_by_path: dict[str, Any] = {}
            if not isinstance(result_required, list):
                gate.error(f"{rlabel} lane {lane_id}.required_outputs: expected array")
            else:
                for item in result_required:
                    if isinstance(item, dict) and isinstance(item.get("path"), str):
                        result_required_by_path[item["path"]] = item
            outputs_ok = stdout_ok
            if set(result_required_by_path) != set(required_rel):
                gate.error(f"{rlabel} lane {lane_id}: required output receipt paths differ")
                outputs_ok = False
            for output_rel in required_rel:
                item = result_required_by_path.get(output_rel)
                if not validate_receipt_item(
                    gate,
                    item,
                    output_rel,
                    f"{rlabel} lane {lane_id} required {Path(output_rel).name}",
                    require_nonempty=True,
                ):
                    outputs_ok = False
            attempt.outputs_ok = outputs_ok
            attempt.public_ok = validate_public_workspace(
                gate,
                attempt.public_dir,
                inventory,
                f"{rlabel} lane {lane_id} public surface",
            )

            final_status = attempt.workspace / "final-status.md"
            if gate.require_nonempty_file(final_status, f"{rlabel} lane {lane_id} status"):
                try:
                    status_text = final_status.read_text(encoding="utf-8")
                except (OSError, UnicodeError) as exc:
                    gate.error(f"{rlabel} lane {lane_id}: cannot read status: {exc}")
                else:
                    if lane_id not in status_text:
                        gate.error(f"{rlabel} lane {lane_id}: status omits lane ID")
                    contamination = status_contamination(status_text)
                    if contamination is None:
                        gate.error(
                            f"{rlabel} lane {lane_id}: status needs explicit "
                            "contamination status: clean|contaminated"
                        )
                    attempt.status_clean = contamination == "clean"
            attempt.clean_and_complete = (
                attempt.runner_success
                and attempt.outputs_ok
                and attempt.public_ok
                and attempt.status_clean
            )
        else:
            # Failed historical attempts are evidence, not permanent blockers.
            # Their result shape still has to identify a real planned attempt.
            if result.get("stage_status") not in {
                "launched",
                "staged-not-launched",
                "staging-failed",
                "not-staged",
            }:
                gate.error(f"{rlabel} lane {lane_id}: invalid failed stage_status")
            if result.get("stage_status") == "launched" and not required_sweep_command(
                result.get("command")
            ):
                gate.error(
                    f"{rlabel} lane {lane_id}: launched failure lacks the required "
                    "production codex model/effort receipt"
                )
            if result.get("stage_status") == "launched":
                prompt_hash = result.get("prompt_sha256")
                current_prompt_hash = current_file_receipt(attempt.prompt_path)["sha256"]
                if not valid_hash(prompt_hash):
                    gate.error(f"{rlabel} lane {lane_id}: invalid failed prompt_sha256")
                else:
                    expected_unchanged = current_prompt_hash == prompt_hash
                    if result.get("prompt_unchanged") is not expected_unchanged:
                        gate.error(
                            f"{rlabel} lane {lane_id}: failed prompt_unchanged "
                            "disagrees with current prompt hash"
                        )
                validate_receipt_item(
                    gate,
                    result.get("stdout_log"),
                    paths["launch_log_rel"][0],
                    f"{rlabel} lane {lane_id}.stdout_log",
                    require_nonempty=False,
                )
                validate_receipt_item(
                    gate,
                    result.get("stderr_log"),
                    paths["stderr_log_rel"][0],
                    f"{rlabel} lane {lane_id}.stderr_log",
                    require_nonempty=False,
                )
                failed_required = result.get("required_outputs")
                failed_by_path: dict[str, Any] = {}
                if not isinstance(failed_required, list):
                    gate.error(
                        f"{rlabel} lane {lane_id}.required_outputs: expected array"
                    )
                else:
                    for item in failed_required:
                        if isinstance(item, dict) and isinstance(item.get("path"), str):
                            failed_by_path[item["path"]] = item
                if set(failed_by_path) != set(required_rel):
                    gate.error(
                        f"{rlabel} lane {lane_id}: failed required receipt paths differ"
                    )
                for output_rel in required_rel:
                    validate_receipt_item(
                        gate,
                        failed_by_path.get(output_rel),
                        output_rel,
                        f"{rlabel} lane {lane_id} failed {Path(output_rel).name}",
                        require_nonempty=False,
                    )
        evidence.attempts.append(attempt)

    if set(result_by_id) != plan_ids:
        extras = sorted(set(result_by_id) - plan_ids)
        if extras:
            gate.error(f"{rlabel}: result has unplanned lanes: {extras}")
    expected_counts = {
        "neutral": kind_counts["neutral"],
        "deceptive": kind_counts["deceptive"],
        "total": len(plan_ids),
    }
    if plan.get("run_counts") != expected_counts:
        gate.error(f"sweep plan {plan_rel}: run_counts does not match runs")
    if phase == "initial" and not (
        2 <= kind_counts["neutral"] <= 3 and 2 <= kind_counts["deceptive"] <= 3
    ):
        gate.error(f"sweep plan {plan_rel}: initial wave must plan 2-3 of each kind")

    actual_top_success = (
        results.get("interrupted") is False
        and results.get("staging_error") is None
        and all(attempt.runner_success for attempt in evidence.attempts)
        and len(evidence.attempts) == len(plan_ids)
    )
    if results.get("success") is not actual_top_success:
        gate.error(f"{rlabel}: top-level success is inconsistent with per-run receipts")
    return evidence


def check_blind_clock(
    gate: Gate,
    expected_started: datetime | None,
    expected_deadline: datetime | None,
) -> None:
    clock_rel = f"{BLIND_ROOT_REL}/blind-time-budget.json"
    resolved = gate.safe_problem_path(clock_rel, "production blind clock")
    if resolved is None:
        return
    _, path = resolved
    raw = gate.load_json(path, "production blind clock")
    if not isinstance(raw, dict):
        if raw is not None:
            gate.error("production blind clock: top level must be an object")
        return
    if raw.get("schema_version") != 1 or isinstance(raw.get("schema_version"), bool):
        gate.error("production blind clock.schema_version: expected integer 1")
    if raw.get("execution_mode") != "production-codex":
        gate.error("production blind clock.execution_mode: expected 'production-codex'")
    if raw.get("clock_rel") != clock_rel:
        gate.error(f"production blind clock.clock_rel: expected {clock_rel!r}")
    if not exact_number(
        raw.get("blind_time_limit_seconds"), PRODUCTION_BLIND_TIME_LIMIT_SECONDS
    ):
        gate.error(
            "production blind clock.blind_time_limit_seconds: expected numeric 7200"
        )
    started = parse_utc_timestamp(
        gate,
        raw.get("blind_started_at_utc"),
        "production blind clock.blind_started_at_utc",
    )
    deadline = parse_utc_timestamp(
        gate,
        raw.get("blind_deadline_utc"),
        "production blind clock.blind_deadline_utc",
    )
    if started is not None and deadline is not None:
        if abs(
            (deadline - started).total_seconds()
            - PRODUCTION_BLIND_TIME_LIMIT_SECONDS
        ) > 0.001:
            gate.error("production blind clock: deadline is not exactly 7200 seconds later")
    if expected_started is not None and started != expected_started:
        gate.error("production blind clock: start differs from runner receipts")
    if expected_deadline is not None and deadline != expected_deadline:
        gate.error("production blind clock: deadline differs from runner receipts")


def collect_evidence(
    gate: Gate,
) -> tuple[list[Attempt], int, datetime | None, datetime | None, bool]:
    plan_paths = load_plan_paths(gate)
    plans: list[PlanEvidence] = []
    seen_waves: set[int] = set()
    seen_lanes: set[str] = set()
    seen_attempts: set[str] = set()
    canonical_inventory: tuple[tuple[str, str], ...] | None = None
    canonical_started: datetime | None = None
    canonical_deadline: datetime | None = None
    any_deadline_exceeded = False
    for path in plan_paths:
        evidence = validate_plan_and_results(gate, path)
        if evidence is None:
            continue
        if evidence.wave in seen_waves:
            gate.error(f"sweep plans: duplicate wave number {evidence.wave}")
        seen_waves.add(evidence.wave)
        if canonical_inventory is None:
            canonical_inventory = evidence.inventory
        elif evidence.inventory != canonical_inventory:
            gate.error(
                f"sweep plan {evidence.plan_rel}: public inventory differs from prior wave"
            )
        if evidence.blind_started_at is not None:
            if canonical_started is None:
                canonical_started = evidence.blind_started_at
            elif evidence.blind_started_at != canonical_started:
                gate.error(
                    f"sweep plan {evidence.plan_rel}: blind start differs from prior wave"
                )
        if evidence.blind_deadline is not None:
            if canonical_deadline is None:
                canonical_deadline = evidence.blind_deadline
            elif evidence.blind_deadline != canonical_deadline:
                gate.error(
                    f"sweep plan {evidence.plan_rel}: blind deadline differs from prior wave"
                )
        any_deadline_exceeded = any_deadline_exceeded or evidence.deadline_exceeded
        for attempt in evidence.attempts:
            if attempt.lane_id in seen_lanes:
                gate.error(
                    f"sweep plans: lane ID is not retry-unique: {attempt.lane_id!r}"
                )
            seen_lanes.add(attempt.lane_id)
            if attempt.attempt_id in seen_attempts:
                gate.error(
                    f"sweep plans: duplicate attempt_id/workspace: {attempt.attempt_id!r}"
                )
            seen_attempts.add(attempt.attempt_id)
        plans.append(evidence)
    if plans and 1 not in seen_waves:
        gate.error("sweep plans: initial wave 1 is missing")
    if seen_waves:
        missing_waves = sorted(set(range(1, max(seen_waves) + 1)) - seen_waves)
        if missing_waves:
            gate.error(
                "sweep plans: historical waves were not retained; missing "
                + ", ".join(str(wave) for wave in missing_waves)
            )
    check_blind_clock(gate, canonical_started, canonical_deadline)
    attempts = [attempt for plan in plans for attempt in plan.attempts]
    return (
        attempts,
        max(seen_waves, default=0),
        canonical_started,
        canonical_deadline,
        any_deadline_exceeded,
    )


def check_blind_summary(gate: Gate, attempts: list[Attempt]) -> None:
    path = gate.problem_dir / BLIND_SUMMARY_REL
    if not gate.require_nonempty_file(path, "blind summary"):
        return
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        gate.error(f"blind summary: cannot read {path}: {exc}")
        return
    for attempt in attempts:
        if attempt.attempt_id not in text:
            gate.error(
                f"blind summary: missing historical attempt_id {attempt.attempt_id!r}"
            )


def markdown_scalar(text: str, key: str) -> str | None:
    pattern = re.compile(
        rf"(?im)^\s*(?:[-*]\s*)?(?:\*\*)?{re.escape(key)}"
        rf"(?:\*\*)?\s*:\s*(.*?)\s*$"
    )
    match = pattern.search(text)
    if match is None:
        return None
    return match.group(1).strip().strip("`*_ ")


def check_review_receipt(
    gate: Gate,
    receipt_path: Path,
    review: dict[str, Any],
    label: str,
    blind_started_at: datetime | None,
    blind_deadline: datetime | None,
) -> bool:
    receipt = gate.load_json(receipt_path, f"{label} execution receipt")
    if not isinstance(receipt, dict):
        if receipt is not None:
            gate.error(f"{label} execution receipt: top level must be an object")
        return False
    expected = {
        "review_id": review.get("review_id"),
        "attempt_id": review.get("attempt_id"),
        "reviewer_id": review.get("reviewer_id"),
        "execution_mode": "production-codex",
        "source_path": review.get("source_path"),
        "source_sha256": review.get("source_sha256"),
        "review_report": review.get("review_report"),
        "review_report_sha256": review.get("review_report_sha256"),
        "requested_execution_mode": "production-codex",
        "model": REQUIRED_MODEL,
        "reasoning_effort": REQUIRED_REASONING_EFFORT,
        "success": True,
        "status": review.get("status"),
        "spawn_error": None,
        "interrupted": False,
        "source_unchanged": True,
        "plan_unchanged": True,
        "public_manifest_unchanged": True,
        "prompt_unchanged": True,
        "staged_candidate_unchanged": True,
        "staged_manifest_unchanged": True,
        "staged_public_unchanged": True,
        "report_validation_errors": [],
        "filesystem_read_isolation": False,
        "isolation_mode": "trust-based-fresh-review-workspace",
    }
    ok = True
    if type(receipt.get("schema_version")) is not int or receipt.get(
        "schema_version"
    ) != 1:
        gate.error(f"{label} execution receipt.schema_version: expected integer 1")
        ok = False
    if type(receipt.get("exit_code")) is not int or receipt.get("exit_code") != 0:
        gate.error(f"{label} execution receipt.exit_code: expected integer 0")
        ok = False
    for key, value in expected.items():
        if receipt.get(key) != value:
            gate.error(f"{label} execution receipt.{key}: expected {value!r}")
            ok = False
    if not required_sweep_command(receipt.get("command")):
        gate.error(
            f"{label} execution receipt: command must be production codex exec "
            f"with exact {REQUIRED_MODEL}/{REQUIRED_REASONING_EFFORT} settings"
        )
        ok = False

    (
        receipt_blind_started,
        receipt_blind_deadline,
        review_started,
        review_finished,
        review_deadline_exceeded,
    ) = validate_production_timing(
        gate,
        receipt,
        f"{label} execution receipt",
        require_clock=False,
    )
    if blind_started_at is not None and receipt_blind_started != blind_started_at:
        gate.error(f"{label} execution receipt: blind start differs from sweep window")
        ok = False
    if blind_deadline is not None and receipt_blind_deadline != blind_deadline:
        gate.error(f"{label} execution receipt: blind deadline differs from sweep window")
        ok = False
    if (
        receipt_blind_started is not None
        and review_started is not None
        and review_started < receipt_blind_started
    ):
        gate.error(f"{label} execution receipt: review started before blind window")
        ok = False
    if (
        receipt_blind_deadline is not None
        and review_finished is not None
        and review_finished > receipt_blind_deadline
    ):
        gate.error(f"{label} execution receipt: review finished after blind deadline")
        ok = False
    if review_deadline_exceeded:
        ok = False

    review_id = review.get("review_id")
    expected_base = f"audit/private/blind-reviews/{review_id}"
    path_fields = {
        "review_workspace": f"{expected_base}/workspace",
        "prompt_file": f"{expected_base}/prompt.txt",
        "staged_candidate": f"{expected_base}/workspace/candidate/main.cpp",
    }
    for key, expected_path in path_fields.items():
        if receipt.get(key) != expected_path:
            gate.error(f"{label} execution receipt.{key}: expected {expected_path!r}")
            ok = False

    for key, hash_key, require_nonempty in (
        ("prompt_file", "prompt_sha256", True),
        ("staged_candidate", "staged_candidate_sha256", True),
    ):
        resolved = gate.safe_problem_path(
            receipt.get(key), f"{label} execution receipt.{key}"
        )
        if resolved is None:
            ok = False
            continue
        relative, artifact = resolved
        current = current_file_receipt(artifact)
        if require_nonempty and current["status"] != "present-nonempty":
            gate.error(f"{label} execution receipt.{key}: artifact is not non-empty")
            ok = False
        if not valid_hash(receipt.get(hash_key)) or receipt.get(hash_key) != current[
            "sha256"
        ]:
            gate.error(
                f"{label} execution receipt.{hash_key}: does not bind {relative}"
            )
            ok = False
    if receipt.get("staged_candidate_sha256") != review.get("source_sha256"):
        gate.error(f"{label} execution receipt: staged candidate differs from source")
        ok = False

    for key in ("plan_rel", "runner_results_rel", "public_manifest_rel"):
        resolved = gate.safe_problem_path(
            receipt.get(key), f"{label} execution receipt.{key}"
        )
        if resolved is None:
            ok = False
            continue
        relative, artifact = resolved
        if not gate.require_nonempty_file(artifact, f"{label} receipt {key}"):
            ok = False
            continue
        if key == "plan_rel":
            hash_key = "plan_sha256"
        elif key == "public_manifest_rel":
            hash_key = "public_manifest_sha256"
        else:
            hash_key = None
        if hash_key is not None:
            try:
                digest = sha256_file(artifact)
            except OSError as exc:
                gate.error(f"{label}: cannot hash receipt artifact {relative}: {exc}")
                ok = False
            else:
                if not valid_hash(receipt.get(hash_key)) or receipt.get(hash_key) != digest:
                    gate.error(
                        f"{label} execution receipt.{hash_key}: does not bind {relative}"
                    )
                    ok = False

    for log_key in ("stdout_log", "stderr_log"):
        raw_log = receipt.get(log_key)
        if not isinstance(raw_log, dict) or not isinstance(raw_log.get("path"), str):
            gate.error(f"{label} execution receipt.{log_key}: invalid file receipt")
            ok = False
            continue
        if not validate_receipt_item(
            gate,
            raw_log,
            raw_log["path"],
            f"{label} execution receipt.{log_key}",
            require_nonempty=log_key == "stdout_log",
        ):
            ok = False
        elif log_key == "stdout_log":
            resolved_log = gate.safe_problem_path(
                raw_log["path"], f"{label} execution receipt.stdout_log.path"
            )
            if resolved_log is None or not validate_completed_codex_jsonl(
                gate,
                resolved_log[1],
                f"{label} execution receipt.stdout_log",
            ):
                ok = False

    manifest_resolved = gate.safe_problem_path(
        receipt.get("public_manifest_rel"),
        f"{label} execution receipt.public_manifest_rel",
    )
    workspace_resolved = gate.safe_problem_path(
        receipt.get("review_workspace"),
        f"{label} execution receipt.review_workspace",
    )
    if manifest_resolved is not None and workspace_resolved is not None:
        _, manifest_path = manifest_resolved
        _, workspace_path = workspace_resolved
        inventory = load_public_manifest(
            gate, manifest_path, f"{label} review frozen public manifest"
        )
        if not validate_public_workspace(
            gate,
            workspace_path / "public",
            inventory,
            f"{label} review public surface",
        ):
            ok = False
    return ok


def check_claim_reviews(
    gate: Gate,
    attempts: list[Attempt],
    blind_started_at: datetime | None,
    blind_deadline: datetime | None,
) -> int:
    path = gate.problem_dir / CLAIM_REVIEW_REL
    data = gate.load_json(path, "blind claim reviews")
    if not isinstance(data, dict):
        if data is not None:
            gate.error("blind claim reviews: top level must be an object")
        return 0
    if type(data.get("schema_version")) is not int or data.get("schema_version") != 1:
        gate.error("blind claim reviews: schema_version must be integer 1")
    raw_reviews = data.get("reviews")
    if not isinstance(raw_reviews, list) or not raw_reviews:
        gate.error("blind claim reviews: reviews must be a non-empty array")
        return 0
    attempts_by_id = {attempt.attempt_id: attempt for attempt in attempts}
    seen_review_ids: set[str] = set()
    verified = 0
    for index, review in enumerate(raw_reviews):
        label = f"blind claim reviews.reviews[{index}]"
        if not isinstance(review, dict):
            gate.error(f"{label}: expected an object")
            continue
        review_id = review.get("review_id")
        if not isinstance(review_id, str) or not review_id:
            gate.error(f"{label}.review_id: expected non-empty string")
            continue
        if review_id in seen_review_ids:
            gate.error(f"{label}.review_id: duplicate {review_id!r}")
        seen_review_ids.add(review_id)
        attempt = attempts_by_id.get(review.get("attempt_id"))
        if attempt is None:
            gate.error(f"{label}.attempt_id: no matching planned attempt")
            continue
        ok = True
        expected_source_rel = (
            PurePosixPath(attempt.attempt_id) / "main.cpp"
        ).as_posix()
        for key, expected in (
            ("lane_id", attempt.lane_id),
            ("claim_type", "full-solution"),
            ("source_path", expected_source_rel),
        ):
            if review.get(key) != expected:
                gate.error(f"{label}.{key}: expected {expected!r}")
                ok = False
        if attempt.kind != "neutral":
            gate.error(f"{label}: only neutral attempts may claim full solutions")
            ok = False
        reviewer_id = review.get("reviewer_id")
        if not isinstance(reviewer_id, str) or not reviewer_id:
            gate.error(f"{label}.reviewer_id: expected non-empty string")
            ok = False
        elif reviewer_id in {attempt.lane_id, attempt.attempt_id}:
            gate.error(f"{label}.reviewer_id: reviewer is not distinct from solver")
            ok = False
        if review.get("independent") is not True:
            gate.error(f"{label}.independent: must be JSON boolean true")
            ok = False
        status = review.get("status")
        if status not in REVIEW_STATUSES:
            gate.error(f"{label}.status: unsupported value {status!r}")
            ok = False

        active = review.get("active")
        invalidated_by = review.get("invalidated_by")
        if type(active) is not bool:
            gate.error(f"{label}.active: expected a JSON boolean")
            ok = False
        elif active:
            if invalidated_by is not None:
                gate.error(f"{label}.invalidated_by: active claim must use JSON null")
                ok = False
        else:
            invalidation_resolved = gate.safe_problem_path(
                invalidated_by, f"{label}.invalidated_by"
            )
            if invalidation_resolved is None:
                ok = False
            else:
                invalidation_rel, invalidation_path = invalidation_resolved
                if not invalidation_rel.startswith("audit/"):
                    gate.error(
                        f"{label}.invalidated_by: disproof evidence must stay under audit/"
                    )
                    ok = False
                if not gate.require_nonempty_file(
                    invalidation_path, f"{label} invalidation evidence"
                ):
                    ok = False

        source_resolved = gate.safe_problem_path(review.get("source_path"), f"{label}.source_path")
        source_digest = None
        if source_resolved is not None:
            _, source_path = source_resolved
            if gate.require_nonempty_file(source_path, f"{label} source"):
                try:
                    source_digest = sha256_file(source_path)
                except OSError as exc:
                    gate.error(f"{label}: cannot hash source: {exc}")
        if not valid_hash(review.get("source_sha256")) or review.get(
            "source_sha256"
        ) != source_digest:
            gate.error(f"{label}.source_sha256: does not bind current neutral main.cpp")
            ok = False

        report_resolved = gate.safe_problem_path(
            review.get("review_report"), f"{label}.review_report"
        )
        report_digest = None
        if report_resolved is not None:
            report_rel, report_path = report_resolved
            if not report_rel.startswith("audit/") or report_rel == CLAIM_REVIEW_REL:
                gate.error(f"{label}.review_report: must be a distinct audit/ artifact")
                ok = False
            if gate.require_nonempty_file(report_path, f"{label} review report"):
                try:
                    report_digest = sha256_file(report_path)
                except OSError as exc:
                    gate.error(f"{label}: cannot hash review report: {exc}")
        if not valid_hash(review.get("review_report_sha256")) or review.get(
            "review_report_sha256"
        ) != report_digest:
            gate.error(f"{label}.review_report_sha256: does not bind current report")
            ok = False

        receipt_resolved = gate.safe_problem_path(
            review.get("execution_receipt"), f"{label}.execution_receipt"
        )
        if receipt_resolved is None:
            ok = False
        else:
            receipt_rel, receipt_path = receipt_resolved
            if not receipt_rel.startswith("audit/private/blind-reviews/"):
                gate.error(
                    f"{label}.execution_receipt: must be under "
                    "audit/private/blind-reviews/"
                )
                ok = False
            if not check_review_receipt(
                gate,
                receipt_path,
                review,
                label,
                blind_started_at,
                blind_deadline,
            ):
                ok = False
        if status == "verified" and not attempt.clean_and_complete:
            gate.error(f"{label}: verified review points to non-countable attempt")
            ok = False
        if status == "verified" and active is True and ok:
            verified += 1
    if verified < 1:
        gate.error(
            "blind claim reviews: need at least one clean neutral full-solution "
            "claim with an independent production review marked verified"
        )
    return verified


def check_run_state(
    gate: Gate,
    attempts: list[Attempt],
    max_wave: int,
    verified_reviews: int,
    blind_started_at: datetime | None,
    blind_deadline: datetime | None,
    any_deadline_exceeded: bool,
) -> None:
    path = gate.problem_dir / RUN_STATE_REL
    if not gate.require_nonempty_file(path, "run state"):
        return
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        gate.error(f"run state: cannot read {path}: {exc}")
        return
    clean_counts = Counter(
        attempt.kind for attempt in attempts if attempt.clean_and_complete
    )
    expected_scalars: dict[str, str] = {
        "agent_model": REQUIRED_MODEL,
        "agent_reasoning_effort": REQUIRED_REASONING_EFFORT,
        "model_policy_status": "enforced",
        "blind_status": "complete",
        "blind_wave_current": str(max_wave),
        "blind_attempts_total": str(len(attempts)),
        "completed_neutral_lanes": str(clean_counts["neutral"]),
        "completed_deceptive_lanes": str(clean_counts["deceptive"]),
        "verified_full_solutions": str(verified_reviews),
        "blind_time_limit_seconds": str(PRODUCTION_BLIND_TIME_LIMIT_SECONDS),
        "blind_failure_reason": "none",
    }
    if blind_started_at is not None:
        expected_scalars["blind_started_at_utc"] = blind_started_at.isoformat().replace(
            "+00:00", "Z"
        )
    if blind_deadline is not None:
        expected_scalars["blind_deadline_utc"] = blind_deadline.isoformat().replace(
            "+00:00", "Z"
        )
    for key, expected in expected_scalars.items():
        actual = markdown_scalar(text, key)
        if actual is None:
            gate.error(f"run state: missing machine-readable {key}: {expected}")
        elif actual != expected:
            gate.error(f"run state: {key} records {actual!r}, expected {expected!r}")
    retry_reason = markdown_scalar(text, "last_blind_retry_reason")
    if retry_reason is None or not retry_reason.strip():
        gate.error("run state: missing non-empty last_blind_retry_reason")
    elif max_wave > 1 and retry_reason.lower() in {"none", "not-needed", "initial"}:
        gate.error("run state: retry wave requires a concrete last_blind_retry_reason")

    elapsed = markdown_scalar(text, "blind_elapsed_seconds")
    if elapsed is None:
        gate.error("run state: missing machine-readable blind_elapsed_seconds")
    else:
        try:
            elapsed_value = float(elapsed)
        except ValueError:
            gate.error("run state: blind_elapsed_seconds must be numeric")
        else:
            if not 0.0 <= elapsed_value <= PRODUCTION_BLIND_TIME_LIMIT_SECONDS:
                gate.error(
                    "run state: blind_elapsed_seconds must be within the shared "
                    "0..7200 second production window"
                )
            elif abs(
                elapsed_value - gate.max_observed_blind_elapsed_seconds
            ) > 5.0:
                gate.error(
                    "run state: blind_elapsed_seconds must match the latest production "
                    "runner/reviewer evidence within five seconds"
                )
    if any_deadline_exceeded:
        gate.error(
            "run state: blind stage cannot be complete after a production receipt "
            "crossed the shared deadline"
        )


def readiness_declares_go(problem_dir: Path) -> bool:
    path = problem_dir / "audit/readiness.md"
    if not path.is_file() or path.is_symlink():
        return False
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return False
    return markdown_scalar(text, "verdict") == "go"


def main() -> int:
    args = parse_args()
    gate = Gate(args.problem_dir)
    gate.require_nonempty_file(args.problem_dir / "statement.md", "contestant statement")
    (
        attempts,
        max_wave,
        blind_started_at,
        blind_deadline,
        any_deadline_exceeded,
    ) = collect_evidence(gate)
    clean_counts = Counter(
        attempt.kind for attempt in attempts if attempt.clean_and_complete
    )
    if clean_counts["neutral"] < MIN_CLEAN_NEUTRAL:
        gate.error(
            f"clean neutral attempts: need at least {MIN_CLEAN_NEUTRAL}, "
            f"found {clean_counts['neutral']}"
        )
    if clean_counts["deceptive"] < MIN_CLEAN_DECEPTIVE:
        gate.error(
            f"clean deceptive attempts: need at least {MIN_CLEAN_DECEPTIVE}, "
            f"found {clean_counts['deceptive']}"
        )
    check_blind_summary(gate, attempts)
    verified_reviews = check_claim_reviews(
        gate, attempts, blind_started_at, blind_deadline
    )
    check_run_state(
        gate,
        attempts,
        max_wave,
        verified_reviews,
        blind_started_at,
        blind_deadline,
        any_deadline_exceeded,
    )

    if gate.issues:
        if readiness_declares_go(args.problem_dir):
            gate.error(
                "audit/readiness.md declares verdict: go while the blind-stage "
                "hard gate is failing"
            )
        print(f"Blind-stage gate: FAIL ({len(gate.issues)} issue(s))", file=sys.stderr)
        for issue in gate.issues:
            print(f"- {issue}", file=sys.stderr)
        print(
            "Preclassification and package construction must not proceed; launch "
            "replacement/focused waves until the gate passes.",
            file=sys.stderr,
        )
        return 1

    print("Blind-stage gate: PASS")
    print(f"problem_dir: {args.problem_dir}")
    print(f"sweep_waves: {max_wave}")
    print(f"historical_attempts: {len(attempts)}")
    print(f"clean_neutral_attempts: {clean_counts['neutral']}")
    print(f"clean_deceptive_attempts: {clean_counts['deceptive']}")
    print(f"independently_verified_full_solution_claims: {verified_reviews}")
    print("semantic_note: review receipt verified; algorithm correctness not self-judged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
