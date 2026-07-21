#!/usr/bin/env python3
"""Launch one non-blind ICPC Light stage and bind its actual artifacts.

Blind solving has a separate public-only runner.  This runner covers every
later agent context.  It fixes the model/effort pair, hashes prerequisites,
waits for the child, rejects missing outputs, preserves logs, and publishes a
current production receipt only after fresh material output and normal Codex
JSONL completion.  The runner also executes and hash-binds the semantic gate at
each partial handoff; it proves execution/provenance and refuses to advance on
an incomplete prior artifact, while making no claim to be a theorem prover.
Before a production child starts, each prior stage-owned artifact is atomically
archived inside that immutable attempt so retries cannot inherit stale success.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from string import Formatter
from typing import Any

from statement_resources import StatementResourceError, load_statement_resources


REQUIRED_MODEL = "gpt-5.6-sol"
REQUIRED_REASONING_EFFORT = "xhigh"
RECEIPT_ROOT = PurePosixPath("audit/private/stage-executions")
ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
HASH_RE = re.compile(r"[0-9a-f]{64}")
ALLOWED_TEMPLATE_FIELDS = {
    "model",
    "problem_dir",
    "prompt_file",
    "reasoning_effort",
    "run_id",
    "stage",
}


@dataclass(frozen=True)
class StageContract:
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    optional_outputs: tuple[str, ...] = ()
    output_trees: tuple[str, ...] = ()
    optional_output_trees: tuple[str, ...] = ()
    requires_blind_gate: bool = False


STAGES: dict[str, StageContract] = {
    "preclassification": StageContract(
        inputs=(
            "statement.md",
            "audit/blind-summary.md",
            "audit/blind-claim-reviews.json",
        ),
        outputs=(
            "audit/data-buildability.md",
            "audit/private/selected-standard-route.cpp",
        ),
        requires_blind_gate=True,
    ),
    "solution-draft": StageContract(
        inputs=(
            "statement.md",
            "audit/data-buildability.md",
            "audit/blind-claim-reviews.json",
            "audit/private/selected-standard-route.cpp",
        ),
        outputs=("audit/contract.md", "audit/solution-review-draft.md"),
    ),
    "std-materialization": StageContract(
        inputs=(
            "statement.md",
            "audit/contract.md",
            "audit/solution-review-draft.md",
            "audit/private/selected-standard-route.cpp",
        ),
        outputs=("package/std.cpp", "audit/std-materialization.md"),
    ),
    "solution-validation": StageContract(
        inputs=(
            "statement.md",
            "audit/contract.md",
            "audit/solution-review-draft.md",
            "audit/std-materialization.md",
            "package/std.cpp",
            "audit/blind-claim-reviews.json",
            "audit/private/selected-standard-route.cpp",
        ),
        outputs=("audit/solution-review.md",),
    ),
    "build-hardening": StageContract(
        inputs=(
            "statement.md",
            "audit/contract.md",
            "audit/data-buildability.md",
            "audit/solution-review.md",
            "audit/private/selected-standard-route.cpp",
            "package/std.cpp",
        ),
        outputs=(
            "package/brute.cpp",
            "package/validator.cpp",
            "audit/wrong-solutions.md",
            "audit/test-manifest.md",
            "audit/coverage-matrix.json",
            "audit/adversarial-rounds.md",
            "audit/regression-plan.json",
            "audit/regression.md",
        ),
        optional_outputs=("package/checker.cpp",),
        output_trees=(
            "package/generators",
            "package/samples",
            "package/tests",
            "audit/adversarial-round-plans",
            "audit/adversarial-round-receipts",
            "audit/private/wrong-solutions",
        ),
        optional_output_trees=("audit/private/accepted-solutions",),
    ),
    "readiness": StageContract(
        inputs=(
            "statement.md",
            "audit/completion-gate.json",
            "audit/regression.md",
            "audit/regression-machine.json",
        ),
        outputs=("audit/readiness.md",),
    ),
}

IMMEDIATE_PRIOR_STAGE = {
    "solution-draft": "preclassification",
    "std-materialization": "solution-draft",
    "solution-validation": "std-materialization",
    "build-hardening": "solution-validation",
    "readiness": "build-hardening",
}

HANDOFF_GATE_BY_STAGE = {
    "solution-draft": "verify_preclassification.py",
    "std-materialization": "verify_solution_draft_handoff.py",
    "solution-validation": "verify_std_materialization_handoff.py",
    "build-hardening": "verify_solution_handoff.py",
    "readiness": "verify_completion_handoff.py",
}

HANDOFF_GATE_ARGUMENTS = {
    "solution-draft": ("--require-continuing",),
}

HANDOFF_GATE_DEPENDENCIES = {
    "verify_preclassification.py": ("verify_completion.py", "run_stage_agent.py"),
    "verify_solution_draft_handoff.py": (
        "verify_completion.py",
        "run_stage_agent.py",
    ),
    "verify_std_materialization_handoff.py": (
        "verify_completion.py",
        "verify_solution_draft_handoff.py",
        "run_stage_agent.py",
    ),
    "verify_solution_handoff.py": ("verify_completion.py", "run_stage_agent.py"),
    "verify_completion_handoff.py": (
        "verify_readiness.py",
        "verify_completion.py",
        "run_stage_agent.py",
    ),
}

HANDOFF_GATE_TIMEOUT_SECONDS = {
    # This handoff performs the one authoritative production completion replay,
    # including the canonical regression, before readiness may start.
    "verify_completion_handoff.py": 4 * 60 * 60 + 300,
}

CODEX_JSONL_TERMINAL_TYPE = "turn.completed"
CODEX_JSONL_RECOVERABLE_ERROR_TYPES = {"error"}
CODEX_JSONL_FAILURE_TYPES = {"turn.failed", "thread.failed"}


class ContractError(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_relative(raw: Any, *, label: str) -> PurePosixPath:
    if not isinstance(raw, str) or not raw.strip() or "\\" in raw:
        raise ContractError(f"{label} must be a non-empty normalized relative path")
    path = PurePosixPath(raw)
    if path.is_absolute() or path == PurePosixPath(".") or ".." in path.parts:
        raise ContractError(f"{label} must stay below the problem directory")
    if path.as_posix() != raw:
        raise ContractError(f"{label} must use normalized POSIX syntax")
    return path


def problem_path(
    root: Path, relative: PurePosixPath, *, label: str, require_exists: bool
) -> Path:
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise ContractError(f"{label} traverses a symbolic link: {relative}")
    try:
        current.resolve(strict=False).relative_to(root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ContractError(f"{label} resolves outside the problem directory") from exc
    if require_exists and not current.exists():
        raise ContractError(f"{label} is missing: {relative}")
    return current


def file_state(path: Path, relative: str) -> dict[str, Any]:
    if path.is_symlink():
        return {"path": relative, "status": "unsafe-symlink", "size": None, "sha256": None}
    if not path.exists():
        return {"path": relative, "status": "missing", "size": None, "sha256": None}
    if not path.is_file():
        return {"path": relative, "status": "not-regular", "size": None, "sha256": None}
    try:
        size = path.stat().st_size
        digest = sha256_file(path)
    except OSError:
        return {"path": relative, "status": "unreadable", "size": None, "sha256": None}
    return {
        "path": relative,
        "status": "present-nonempty" if size else "empty",
        "size": size,
        "sha256": digest,
    }


def snapshot_tree(root: Path, relative: str) -> dict[str, Any]:
    path = problem_path(root, safe_relative(relative, label="output tree"), label="output tree", require_exists=False)
    result: dict[str, Any] = {
        "path": relative,
        "status": "present",
        "files": [],
        "unsafe_entries": [],
    }
    if path.is_symlink() or not path.is_dir():
        result["status"] = "missing-or-unsafe"
        return result
    files: list[dict[str, Any]] = []
    unsafe_entries: list[dict[str, Any]] = []
    for item in sorted(path.rglob("*")):
        inside = item.relative_to(path)
        hidden = any(part.startswith(".") for part in inside.parts)
        item_rel = item.relative_to(root).as_posix()
        if hidden:
            result["status"] = "unsafe-entry"
        if item.is_symlink():
            try:
                target = os.readlink(item)
            except OSError:
                target_digest = None
            else:
                target_digest = hashlib.sha256(
                    target.encode("utf-8", errors="surrogateescape")
                ).hexdigest()
            unsafe_entries.append(
                {
                    "path": item_rel,
                    "status": "unsafe-symlink",
                    "link_target_sha256": target_digest,
                }
            )
            result["status"] = "unsafe-entry"
            continue
        if item.exists() and not (item.is_file() or item.is_dir()):
            unsafe_entries.append(
                {"path": item_rel, "status": "unsupported-entry"}
            )
            result["status"] = "unsafe-entry"
        elif item.is_file():
            state = file_state(item, item_rel)
            files.append(state)
            if hidden:
                unsafe_entries.append(dict(state))
        elif hidden:
            unsafe_entries.append(
                {"path": item_rel, "status": "hidden-directory"}
            )
    result["files"] = files
    result["unsafe_entries"] = unsafe_entries
    if result["status"] == "present" and (
        not files or any(entry["status"] != "present-nonempty" for entry in files)
    ):
        result["status"] = "empty-or-invalid"
    return result


def material_change(before: dict[str, Any], after: dict[str, Any]) -> bool:
    """Return whether a file/tree was created or changed in substance.

    Timestamps are deliberately not evidence: a rewritten byte-identical file
    is still a no-op, while a missing/empty artifact becoming a non-empty file
    is a real materialization.
    """

    return before != after


def same_or_below(path: PurePosixPath, parent: PurePosixPath) -> bool:
    return path == parent or parent in path.parents


def validate_output_layout(
    prompt: PurePosixPath,
    inputs: list[PurePosixPath],
    outputs: list[PurePosixPath],
    trees: list[PurePosixPath],
) -> None:
    """Reject ownership overlaps before production archival can move inputs."""

    protected = [prompt, *inputs, RECEIPT_ROOT]
    for output in outputs:
        if any(
            same_or_below(output, item) or same_or_below(item, output)
            for item in protected
        ):
            raise ContractError(
                f"stage output overlaps a prompt, prerequisite, or receipt root: {output}"
            )
    for tree in trees:
        if any(
            same_or_below(item, tree) or same_or_below(tree, item)
            for item in protected
        ):
            raise ContractError(
                f"output tree overlaps a prompt, prerequisite, or receipt root: {tree}"
            )
    owned = [*outputs, *trees]
    for index, first in enumerate(owned):
        for second in owned[index + 1 :]:
            if same_or_below(first, second) or same_or_below(second, first):
                raise ContractError(
                    f"stage outputs/output trees overlap: {first} and {second}"
                )


def tree_content_signature(snapshot: dict[str, Any]) -> dict[str, Any]:
    raw_root = snapshot.get("path")
    if not isinstance(raw_root, str):
        return {"status": snapshot.get("status"), "files": None}
    root = PurePosixPath(raw_root)
    files: list[dict[str, Any]] = []
    raw_files = snapshot.get("files")
    if not isinstance(raw_files, list):
        return {"status": snapshot.get("status"), "files": None}
    for item in raw_files:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            return {"status": snapshot.get("status"), "files": None}
        path = PurePosixPath(item["path"])
        try:
            inside = path.relative_to(root).as_posix()
        except ValueError:
            return {"status": snapshot.get("status"), "files": None}
        files.append(
            {
                "path": inside,
                "status": item.get("status"),
                "size": item.get("size"),
                "sha256": item.get("sha256"),
            }
        )
    unsafe_entries: list[dict[str, Any]] = []
    raw_unsafe_entries = snapshot.get("unsafe_entries")
    if not isinstance(raw_unsafe_entries, list):
        return {"status": snapshot.get("status"), "files": None}
    for item in raw_unsafe_entries:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            return {"status": snapshot.get("status"), "files": None}
        path = PurePosixPath(item["path"])
        try:
            inside = path.relative_to(root).as_posix()
        except ValueError:
            return {"status": snapshot.get("status"), "files": None}
        unsafe_entries.append(
            {
                "path": inside,
                "status": item.get("status"),
                "size": item.get("size"),
                "sha256": item.get("sha256"),
                "link_target_sha256": item.get("link_target_sha256"),
            }
        )
    return {
        "status": snapshot.get("status"),
        "files": files,
        "unsafe_entries": unsafe_entries,
    }


def _safely_absent_tree_snapshot(snapshot: Any, tree_path: str) -> bool:
    return snapshot == {
        "path": tree_path,
        "status": "missing-or-unsafe",
        "files": [],
        "unsafe_entries": [],
    }


def _restorable_tree_files(
    snapshot: Any, *, tree_path: str, label: str
) -> list[dict[str, Any]]:
    """Return the fully bound regular files in one optional-tree snapshot.

    ``snapshot_tree`` deliberately gives a missing root, a root symlink, and a
    root non-directory the same status.  Historical restoration is therefore
    eligible only for an exact safely-absent snapshot or for a non-empty,
    unsafe-free directory snapshot whose regular files are all hash-bound.
    The caller separately proves that an archived snapshot still has a safe
    directory (or safely absent) backing path.
    """

    if _safely_absent_tree_snapshot(snapshot, tree_path):
        return []
    if not isinstance(snapshot, dict) or set(snapshot) != {
        "path",
        "status",
        "files",
        "unsafe_entries",
    }:
        raise ContractError(f"{label} has an invalid tree snapshot schema")
    if snapshot.get("path") != tree_path or snapshot.get("status") != "present":
        raise ContractError(f"{label} is not a restorable directory snapshot")
    if snapshot.get("unsafe_entries") != []:
        raise ContractError(f"{label} contains unsafe entries")
    files = snapshot.get("files")
    if not isinstance(files, list) or not files:
        raise ContractError(f"{label} has no hash-bound regular files")

    tree_root = safe_relative(tree_path, label=f"{label} root")
    paths: list[str] = []
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(files):
        if not isinstance(item, dict) or set(item) != {
            "path",
            "status",
            "size",
            "sha256",
        }:
            raise ContractError(f"{label}.files[{index}] has an invalid state")
        raw_path = item.get("path")
        relative = safe_relative(raw_path, label=f"{label}.files[{index}]")
        try:
            inside = relative.relative_to(tree_root)
        except ValueError as exc:
            raise ContractError(
                f"{label}.files[{index}] escapes {tree_path}"
            ) from exc
        if inside == PurePosixPath("."):
            raise ContractError(f"{label}.files[{index}] names the tree root")
        size = item.get("size")
        digest = item.get("sha256")
        if (
            item.get("status") != "present-nonempty"
            or not isinstance(size, int)
            or isinstance(size, bool)
            or size <= 0
            or not isinstance(digest, str)
            or HASH_RE.fullmatch(digest) is None
        ):
            raise ContractError(
                f"{label}.files[{index}] is not a hash-bound non-empty file"
            )
        paths.append(raw_path)
        normalized.append(dict(item))
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        raise ContractError(f"{label} file paths are duplicate or non-canonical")
    return normalized


def optional_tree_union_snapshot(
    stage_owned: Any, preexisting: Any, *, tree_path: str
) -> dict[str, Any]:
    """Build the only canonical state allowed after optional-tree restoration.

    Equal same-path file states coalesce.  Different states at the same path,
    or a regular file that is an ancestor of another file, are ownership
    conflicts and fail closed.
    """

    owned_files = _restorable_tree_files(
        stage_owned,
        tree_path=tree_path,
        label=f"{tree_path} stage-owned snapshot",
    )
    archived_files = _restorable_tree_files(
        preexisting,
        tree_path=tree_path,
        label=f"{tree_path} preexisting snapshot",
    )
    merged: dict[str, dict[str, Any]] = {}
    for item in (*owned_files, *archived_files):
        raw_path = item["path"]
        previous = merged.get(raw_path)
        if previous is not None and previous != item:
            raise ContractError(
                f"optional output tree has conflicting file states: {raw_path}"
            )
        merged[raw_path] = item

    merged_paths = [PurePosixPath(path) for path in sorted(merged)]
    for index, first in enumerate(merged_paths):
        for second in merged_paths[index + 1 :]:
            if same_or_below(first, second) or same_or_below(second, first):
                raise ContractError(
                    "optional output tree has a file/descendant conflict: "
                    f"{first.as_posix()} and {second.as_posix()}"
                )
    if not merged:
        return {
            "path": tree_path,
            "status": "missing-or-unsafe",
            "files": [],
            "unsafe_entries": [],
        }
    return {
        "path": tree_path,
        "status": "present",
        "files": [merged[path] for path in sorted(merged)],
        "unsafe_entries": [],
    }


def validate_current_output_tree(
    problem_root: Path,
    stage: str,
    tree_path: str,
    recorded: Any,
    *,
    preexisting: Any | None = None,
) -> dict[str, Any]:
    """Validate one current tree against the receipt's ownership view.

    Required and extra watched trees remain exact stage-owned snapshots.  A
    declared optional tree may additionally contain the exact collision-free
    preexisting snapshot, but callers must first validate that snapshot against
    the immutable archive recorded by the same receipt.
    """

    if stage not in STAGES:
        raise ContractError(f"unknown stage for output-tree validation: {stage}")
    current = snapshot_tree(problem_root, tree_path)
    optional = tree_path in set(STAGES[stage].optional_output_trees)
    if not optional:
        if recorded != current or current.get("status") != "present":
            raise ContractError(f"{stage} current output tree changed: {tree_path}")
        return current
    if preexisting is None:
        raise ContractError(
            f"{stage} optional output tree lacks a bound preexisting snapshot: "
            f"{tree_path}"
        )
    expected = optional_tree_union_snapshot(
        recorded, preexisting, tree_path=tree_path
    )
    if current != expected:
        raise ContractError(
            f"{stage} current optional output tree is not the exact "
            f"stage/preexisting union: {tree_path}"
        )
    if expected.get("status") == "missing-or-unsafe":
        relative = safe_relative(tree_path, label=f"{stage} optional output tree")
        current_path = problem_path(
            problem_root,
            relative,
            label=f"{stage} optional output tree",
            require_exists=False,
        )
        if current_path.exists() or current_path.is_symlink():
            raise ContractError(
                f"{stage} optional output tree root is unsafe: {tree_path}"
            )
    return current


def archive_preexisting_outputs(
    root: Path,
    base_rel: PurePosixPath,
    outputs: list[PurePosixPath],
    trees: list[PurePosixPath],
    output_snapshots: list[dict[str, Any]],
    tree_snapshots: list[dict[str, Any]],
) -> tuple[dict[str, Any], str | None]:
    """Atomically move owned canonical artifacts into this production attempt."""

    archive_root_rel = base_rel / "preexisting"
    records: dict[str, Any] = {
        "performed": True,
        "root": archive_root_rel.as_posix(),
        "files": [],
        "trees": [],
    }
    archive_error: str | None = None
    pairs = [
        ("files", relative, before)
        for relative, before in zip(outputs, output_snapshots)
    ] + [
        ("trees", relative, before)
        for relative, before in zip(trees, tree_snapshots)
    ]
    for kind, relative, before in pairs:
        source = problem_path(
            root, relative, label="preexisting canonical output", require_exists=False
        )
        archive_rel = archive_root_rel / relative
        archive = problem_path(
            root, archive_rel, label="preexisting output archive", require_exists=False
        )
        archived = source.exists() or source.is_symlink()
        record: dict[str, Any] = {
            "path": relative.as_posix(),
            "archive_path": archive_rel.as_posix(),
            "archived": archived,
            "before": before,
        }
        try:
            if archived:
                archive.parent.mkdir(parents=True, exist_ok=True)
                os.replace(source, archive)
            if kind == "files":
                archived_state = file_state(archive, archive_rel.as_posix())
                record["archive_state"] = archived_state
                if archived and (
                    archived_state.get("status") != before.get("status")
                    or archived_state.get("size") != before.get("size")
                    or archived_state.get("sha256") != before.get("sha256")
                ):
                    raise OSError("archived file state differs from its source snapshot")
                if not archived and archived_state.get("status") != "missing":
                    raise OSError("unexpected file already exists at archive destination")
            else:
                archived_snapshot = snapshot_tree(root, archive_rel.as_posix())
                record["archive_snapshot"] = archived_snapshot
                if archived and tree_content_signature(
                    archived_snapshot
                ) != tree_content_signature(before):
                    raise OSError("archived tree differs from its source snapshot")
                if not archived and archived_snapshot.get("status") != "missing-or-unsafe":
                    raise OSError("unexpected tree already exists at archive destination")
        except OSError as exc:
            archive_error = (
                f"{type(exc).__name__} while archiving {relative.as_posix()}: {exc}"
            )
            record["archive_error"] = archive_error
            records[kind].append(record)
            break
        records[kind].append(record)
    records["error"] = archive_error
    return records, archive_error


def validate_codex_jsonl(path: Path) -> dict[str, Any]:
    """Validate the stable completion envelope emitted by ``codex exec --json``.

    A zero process exit is insufficient: killed or internally failed Codex
    executions can leave a syntactically incomplete trace.  A normal one-turn
    execution starts a thread and turn and ends with ``turn.completed`` as its
    last non-empty JSONL record.  Codex may emit recoverable API ``error``
    events while retrying; retain and count them, but let the final terminal
    event determine the outcome.
    """

    result: dict[str, Any] = {
        "status": "failed",
        "event_count": 0,
        "thread_started_count": 0,
        "turn_started_count": 0,
        "turn_completed_count": 0,
        "recoverable_error_event_count": 0,
        "failure_event_count": 0,
        "terminal_type": None,
        "issues": [],
    }
    issues: list[str] = result["issues"]
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        issues.append(f"cannot read Codex JSONL: {exc}")
        return result
    event_types: list[str] = []
    for line_number, raw in enumerate(lines, 1):
        if not raw.strip():
            continue
        try:
            event = json.loads(raw)
        except json.JSONDecodeError as exc:
            issues.append(f"line {line_number} is invalid JSON: {exc.msg}")
            continue
        if not isinstance(event, dict):
            issues.append(f"line {line_number} is not a JSON object")
            continue
        event_type = event.get("type")
        if not isinstance(event_type, str) or not event_type:
            issues.append(f"line {line_number} lacks a non-empty event type")
            continue
        event_types.append(event_type)
    result["event_count"] = len(event_types)
    result["thread_started_count"] = event_types.count("thread.started")
    result["turn_started_count"] = event_types.count("turn.started")
    result["turn_completed_count"] = event_types.count(CODEX_JSONL_TERMINAL_TYPE)
    result["recoverable_error_event_count"] = sum(
        event_type in CODEX_JSONL_RECOVERABLE_ERROR_TYPES
        for event_type in event_types
    )
    result["failure_event_count"] = sum(
        event_type in CODEX_JSONL_FAILURE_TYPES for event_type in event_types
    )
    result["terminal_type"] = event_types[-1] if event_types else None
    if not event_types:
        issues.append("Codex JSONL contains no events")
    if result["thread_started_count"] != 1:
        issues.append("Codex JSONL must contain exactly one thread.started event")
    if result["turn_started_count"] != 1:
        issues.append("Codex JSONL must contain exactly one turn.started event")
    if result["turn_completed_count"] != 1:
        issues.append("Codex JSONL must contain exactly one turn.completed event")
    if result["failure_event_count"]:
        issues.append("Codex JSONL contains an explicit failure event")
    if result["terminal_type"] != CODEX_JSONL_TERMINAL_TYPE:
        issues.append("Codex JSONL does not end with turn.completed")
    if not issues:
        result["status"] = "passed"
    return result


def write_json_exclusive(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one exact-model non-blind ICPC Light stage agent.")
    parser.add_argument("--problem-dir", type=Path, required=True)
    parser.add_argument("--stage", choices=sorted(STAGES), required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--prompt-file", required=True, help="Problem-relative prompt file.")
    parser.add_argument("--model", required=True)
    parser.add_argument("--reasoning-effort", default=REQUIRED_REASONING_EFFORT)
    parser.add_argument("--input", action="append", default=[], help="Additional problem-relative prerequisite; repeatable.")
    parser.add_argument("--output", action="append", default=[], help="Additional required output; repeatable.")
    parser.add_argument("--watch-output-tree", action="append", default=[], help="Additional non-empty output tree; repeatable.")
    parser.add_argument("--test-command", default=None, help="Testing-only command template; production receipts reject it.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if not args.problem_dir.is_dir() or args.problem_dir.is_symlink():
        parser.error("--problem-dir must be an existing non-symlink directory")
    args.problem_dir = args.problem_dir.resolve()
    if ID_RE.fullmatch(args.run_id or "") is None:
        parser.error(f"--run-id must match {ID_RE.pattern!r}")
    args.model = args.model.strip()
    args.reasoning_effort = args.reasoning_effort.strip()
    if args.model != REQUIRED_MODEL:
        parser.error(f"--model must be exactly {REQUIRED_MODEL!r}")
    if args.reasoning_effort != REQUIRED_REASONING_EFFORT:
        parser.error(f"--reasoning-effort must be exactly {REQUIRED_REASONING_EFFORT!r}")
    try:
        args.prompt_rel = safe_relative(args.prompt_file, label="--prompt-file")
        args.extra_inputs = [safe_relative(value, label="--input") for value in args.input]
        args.extra_outputs = [safe_relative(value, label="--output") for value in args.output]
        args.extra_trees = [safe_relative(value, label="--watch-output-tree") for value in args.watch_output_tree]
    except ContractError as exc:
        parser.error(str(exc))
    return args


def default_command(args: argparse.Namespace) -> list[str]:
    return [
        "codex", "exec", "--json", "--ephemeral", "--skip-git-repo-check",
        "--ignore-user-config", "--ignore-rules", "--model", REQUIRED_MODEL,
        "-c", f'model_reasoning_effort="{REQUIRED_REASONING_EFFORT}"',
        "--sandbox", "workspace-write", "--cd", str(args.problem_dir), "-",
    ]


def test_command(args: argparse.Namespace, prompt_path: Path) -> list[str]:
    assert args.test_command is not None
    try:
        tokens = shlex.split(args.test_command)
    except ValueError as exc:
        raise ContractError(f"invalid --test-command quoting: {exc}") from exc
    fields = {
        "model": REQUIRED_MODEL,
        "problem_dir": str(args.problem_dir),
        "prompt_file": str(prompt_path),
        "reasoning_effort": REQUIRED_REASONING_EFFORT,
        "run_id": args.run_id,
        "stage": args.stage,
    }
    rendered: list[str] = []
    for token in tokens:
        names = {
            name for _, name, _, _ in Formatter().parse(token) if name is not None
        }
        if names - ALLOWED_TEMPLATE_FIELDS:
            raise ContractError("unsupported --test-command placeholder")
        rendered.append(token.format_map(fields))
    if not rendered:
        raise ContractError("--test-command must not be empty")
    return rendered


def stop_process(process: subprocess.Popen[bytes]) -> str:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except OSError:
        process.terminate()
    try:
        process.wait(timeout=10)
        return "terminate"
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            process.kill()
        process.wait()
        return "kill"


def exact_production_command(value: Any, problem_root: Path) -> bool:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in value
    ):
        return False
    if len(value) < 2 or Path(value[0]).name != "codex" or value[1] != "exec":
        return False
    models = [i for i, item in enumerate(value) if item == "--model"]
    cds = [i for i, item in enumerate(value) if item == "--cd"]
    efforts = [
        value[i + 1]
        for i, item in enumerate(value[:-1])
        if item in {"-c", "--config"}
        and value[i + 1].startswith("model_reasoning_effort=")
    ]
    return (
        len(models) == 1
        and models[0] + 1 < len(value)
        and value[models[0] + 1] == REQUIRED_MODEL
        and efforts == [f'model_reasoning_effort="{REQUIRED_REASONING_EFFORT}"']
        and len(cds) == 1
        and cds[0] + 1 < len(value)
        and Path(value[cds[0] + 1]).resolve() == problem_root
        and value.count("--json") == 1
        and value.count("--ephemeral") == 1
        and value.count("--skip-git-repo-check") == 1
        and value.count("--ignore-user-config") == 1
        and value.count("--ignore-rules") == 1
        and value.count("--sandbox") == 1
        and "--sandbox" in value
        and value[value.index("--sandbox") + 1 : value.index("--sandbox") + 2]
        == ["workspace-write"]
        and value[-1:] == ["-"]
    )


def _current_state_matches(
    problem_root: Path,
    raw: Any,
    *,
    label: str,
    require_nonempty: bool,
) -> str:
    if not isinstance(raw, dict) or not isinstance(raw.get("path"), str):
        raise ContractError(f"{label} must be a file-state object")
    relative = safe_relative(raw["path"], label=f"{label}.path")
    path = problem_path(
        problem_root, relative, label=label, require_exists=require_nonempty
    )
    current = file_state(path, relative.as_posix())
    if raw != current:
        raise ContractError(f"{label} no longer matches the current file")
    if require_nonempty and current.get("status") != "present-nonempty":
        raise ContractError(f"{label} must be a non-empty regular file")
    return relative.as_posix()


def _validate_gate_evidence(
    problem_root: Path,
    raw: Any,
    *,
    verifier_name: str,
    label: str,
    json_flag: bool,
    extra_args: tuple[str, ...] = (),
) -> None:
    if not isinstance(raw, dict):
        raise ContractError(f"{label} must be a gate receipt object")
    if raw.get("exit_code") != 0:
        raise ContractError(f"{label} did not pass")
    verifier = Path(__file__).resolve().with_name(verifier_name)
    if raw.get("verifier_sha256") != sha256_file(verifier):
        raise ContractError(f"{label} verifier hash is stale")
    expected_dependencies = {
        name: sha256_file(Path(__file__).resolve().with_name(name))
        for name in HANDOFF_GATE_DEPENDENCIES.get(verifier_name, ())
    }
    if raw.get("dependency_sha256") != expected_dependencies:
        raise ContractError(f"{label} dependency hashes are stale")
    expected_command = [
        sys.executable,
        verifier.name,
        "--problem-dir",
        ".",
        *extra_args,
    ]
    if json_flag:
        expected_command.append("--json")
    if raw.get("command") != expected_command:
        raise ContractError(f"{label} command is not canonical")
    if json_flag and raw.get("reported_status") != "pass":
        raise ContractError(f"{label} did not report semantic pass")
    for field in ("stdout_sha256", "stderr_sha256"):
        if not isinstance(raw.get(field), str) or HASH_RE.fullmatch(raw[field]) is None:
            raise ContractError(f"{label}.{field} must be a SHA-256 digest")
    semantic_evidence = raw.get("semantic_evidence")
    if verifier_name == "verify_completion_handoff.py":
        if not isinstance(semantic_evidence, dict):
            raise ContractError(f"{label} has no canonical completion replay evidence")
        replay = semantic_evidence.get("completion_replay")
        if not isinstance(replay, dict):
            raise ContractError(f"{label} completion replay evidence is invalid")
        completion_verifier = Path(__file__).resolve().with_name(
            "verify_completion.py"
        )
        expected_replay_command = [
            sys.executable,
            completion_verifier.name,
            "--problem-dir",
            ".",
            "--json",
        ]
        if (
            replay.get("command") != expected_replay_command
            or replay.get("verifier_sha256") != sha256_file(completion_verifier)
            or replay.get("timed_out") is not False
            or replay.get("exit_code") != 0
            or replay.get("reported_status") != "pass"
        ):
            raise ContractError(f"{label} canonical completion replay did not pass")
        completion_relative = safe_relative(
            "audit/completion-gate.json", label=f"{label} completion receipt"
        )
        completion_path = problem_path(
            problem_root,
            completion_relative,
            label=f"{label} completion receipt",
            require_exists=True,
        )
        if replay.get("receipt_sha256") != sha256_file(completion_path):
            raise ContractError(f"{label} refreshed completion receipt hash is stale")
    elif semantic_evidence is not None:
        raise ContractError(f"{label} has unexpected semantic evidence")


def _validate_time_pair(receipt: dict[str, Any], stage: str) -> None:
    parsed: dict[str, datetime] = {}
    for field in ("started_at_utc", "finished_at_utc"):
        raw = receipt.get(field)
        if not isinstance(raw, str) or not raw.endswith("Z"):
            raise ContractError(f"{stage} current receipt.{field} is not canonical UTC")
        try:
            value = datetime.fromisoformat(raw[:-1] + "+00:00")
        except ValueError as exc:
            raise ContractError(
                f"{stage} current receipt.{field} is invalid"
            ) from exc
        if value.utcoffset() != timezone.utc.utcoffset(value):
            raise ContractError(f"{stage} current receipt.{field} is not UTC")
        parsed[field] = value
    if parsed["finished_at_utc"] < parsed["started_at_utc"]:
        raise ContractError(f"{stage} current receipt finishes before it starts")


def require_prior_stage_receipt(
    problem_root: Path, stage: str, *, _seen: set[str] | None = None
) -> dict[str, Any]:
    if stage not in STAGES:
        raise ContractError(f"unknown prior stage: {stage}")
    seen = set() if _seen is None else _seen
    if stage in seen:
        raise ContractError(f"cyclic stage receipt chain at {stage}")
    seen.add(stage)
    receipt_relative = RECEIPT_ROOT / stage / "current.json"
    path = problem_path(
        problem_root,
        receipt_relative,
        label=f"{stage} current receipt",
        require_exists=True,
    )
    if path.is_symlink() or not path.is_file():
        raise ContractError(f"{stage} current receipt is not a regular file")
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContractError(f"invalid {stage} current receipt: {exc}") from exc
    run_id = receipt.get("run_id") if isinstance(receipt, dict) else None
    if not isinstance(run_id, str) or ID_RE.fullmatch(run_id) is None:
        raise ContractError(f"{stage} current receipt has an invalid run_id")
    attempt_relative = RECEIPT_ROOT / stage / run_id / "receipt.json"
    attempt_path = problem_path(
        problem_root,
        attempt_relative,
        label=f"{stage} immutable attempt receipt",
        require_exists=True,
    )
    if attempt_path.is_symlink() or not attempt_path.is_file():
        raise ContractError(f"{stage} immutable attempt receipt is unsafe")
    try:
        attempt_receipt = json.loads(attempt_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContractError(f"invalid {stage} immutable attempt receipt: {exc}") from exc
    if attempt_receipt != receipt:
        raise ContractError(f"{stage} current receipt differs from immutable attempt")
    expected = {
        "schema_version": 1,
        "runner": "icpc-light-stage-agent-runner",
        "stage": stage,
        "execution_mode": "production-codex",
        "model": REQUIRED_MODEL,
        "reasoning_effort": REQUIRED_REASONING_EFFORT,
        "exit_code": 0,
        "spawn_error": None,
        "interrupted": False,
        "success": True,
        "prompt_unchanged": True,
        "inputs_unchanged": True,
        "outputs_materially_updated": True,
        "output_trees_materially_updated": True,
        "codex_jsonl_required": True,
    }
    for key, value in expected.items():
        if receipt.get(key) != value:
            raise ContractError(f"{stage} current receipt.{key} is not {value!r}")
    if not exact_production_command(receipt.get("command"), problem_root):
        raise ContractError(f"{stage} current receipt lacks exact production command")
    _validate_time_pair(receipt, stage)

    prompt_path = _current_state_matches(
        problem_root,
        receipt.get("prompt"),
        label=f"{stage} current receipt.prompt",
        require_nonempty=True,
    )
    if not prompt_path.startswith("audit/private/stage-prompts/"):
        raise ContractError(f"{stage} prompt must stay under audit/private/stage-prompts/")
    stdout_rel = _current_state_matches(
        problem_root,
        receipt.get("stdout_log"),
        label=f"{stage} current receipt.stdout_log",
        require_nonempty=True,
    )
    _current_state_matches(
        problem_root,
        receipt.get("stderr_log"),
        label=f"{stage} current receipt.stderr_log",
        require_nonempty=False,
    )
    attempt_root = (RECEIPT_ROOT / stage / run_id).as_posix()
    if stdout_rel != f"{attempt_root}/codex-exec.jsonl":
        raise ContractError(f"{stage} stdout log is outside its immutable attempt")
    stderr_state = receipt.get("stderr_log")
    if not isinstance(stderr_state, dict) or stderr_state.get("path") != (
        f"{attempt_root}/stderr.log"
    ):
        raise ContractError(f"{stage} stderr log is outside its immutable attempt")
    stdout_path = problem_path(
        problem_root,
        safe_relative(stdout_rel, label=f"{stage} stdout log"),
        label=f"{stage} stdout log",
        require_exists=True,
    )
    current_jsonl = validate_codex_jsonl(stdout_path)
    if receipt.get("codex_jsonl_validation") != current_jsonl:
        raise ContractError(f"{stage} Codex JSONL validation is stale or invalid")
    if current_jsonl.get("status") != "passed":
        raise ContractError(f"{stage} Codex JSONL did not complete normally")

    inputs = receipt.get("inputs")
    if not isinstance(inputs, list):
        raise ContractError(f"{stage} current receipt.inputs is not a list")
    input_paths: list[str] = []
    for index, item in enumerate(inputs):
        input_paths.append(
            _current_state_matches(
                problem_root,
                item,
                label=f"{stage} current receipt.inputs[{index}]",
                require_nonempty=True,
            )
        )
    if len(input_paths) != len(set(input_paths)):
        raise ContractError(f"{stage} current receipt has duplicate inputs")
    missing_inputs = set(STAGES[stage].inputs) - set(input_paths)
    if missing_inputs:
        raise ContractError(
            f"{stage} current receipt omits required inputs: {sorted(missing_inputs)}"
        )

    outputs = receipt.get("outputs")
    outputs_before = receipt.get("outputs_before")
    changes = receipt.get("output_changes")
    if not isinstance(outputs, list) or not isinstance(outputs_before, list):
        raise ContractError(f"{stage} current receipt output states are not lists")
    if not isinstance(changes, list):
        raise ContractError(f"{stage} current receipt.output_changes is not a list")
    optional_output_paths = set(STAGES[stage].optional_outputs)
    output_paths: list[str] = []
    output_by_path: dict[str, dict[str, Any]] = {}
    before_by_path: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(outputs):
        raw_path = item.get("path") if isinstance(item, dict) else None
        is_optional = raw_path in optional_output_paths
        output_path = _current_state_matches(
            problem_root,
            item,
            label=f"{stage} current receipt.outputs[{index}]",
            require_nonempty=not is_optional,
        )
        if is_optional:
            output_relative = safe_relative(
                output_path, label=f"{stage} optional output"
            )
            current_path = problem_path(
                problem_root,
                output_relative,
                label=f"{stage} optional output",
                require_exists=False,
            )
            safely_absent = not current_path.exists() and not current_path.is_symlink()
            if item.get("status") != "present-nonempty" and not safely_absent:
                raise ContractError(
                    f"{stage} optional output is unsafe: {output_path}"
                )
        output_paths.append(output_path)
        output_by_path[output_path] = item
    for item in outputs_before:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise ContractError(f"{stage} outputs_before contains an invalid state")
        if item["path"] in before_by_path:
            raise ContractError(f"{stage} current receipt has duplicate outputs_before")
        before_by_path[item["path"]] = item
    if len(output_paths) != len(set(output_paths)):
        raise ContractError(f"{stage} current receipt has duplicate outputs")
    if set(before_by_path) != set(output_paths):
        raise ContractError(f"{stage} outputs_before paths differ from outputs")
    expected_changes = [
        {
            "path": path,
            "before": before_by_path[path],
            "after": output_by_path[path],
            "materially_changed": material_change(
                before_by_path[path], output_by_path[path]
            ),
        }
        for path in output_paths
    ]
    if changes != expected_changes or not all(
        item["materially_changed"]
        or (
            item["path"] in optional_output_paths
            and item["after"].get("status") == "missing"
        )
        for item in expected_changes
    ):
        raise ContractError(f"{stage} receipt does not prove fresh material output")
    if any(item.get("status") != "missing" for item in outputs_before):
        raise ContractError(f"{stage} production outputs were not clear at launch")
    missing_outputs = set(STAGES[stage].outputs) - set(output_paths)
    if missing_outputs:
        raise ContractError(
            f"{stage} current receipt omits required outputs: {sorted(missing_outputs)}"
        )
    missing_optional_outputs = optional_output_paths - set(output_paths)
    if missing_optional_outputs:
        raise ContractError(
            f"{stage} current receipt omits optional watched outputs: "
            f"{sorted(missing_optional_outputs)}"
        )

    trees = receipt.get("output_trees")
    trees_before = receipt.get("output_trees_before")
    tree_changes = receipt.get("output_tree_changes")
    if not isinstance(trees, list) or not isinstance(trees_before, list):
        raise ContractError(f"{stage} current receipt output tree states are not lists")
    if not isinstance(tree_changes, list):
        raise ContractError(f"{stage} current receipt.output_tree_changes is not a list")
    optional_tree_paths = set(STAGES[stage].optional_output_trees)
    tree_by_path: dict[str, dict[str, Any]] = {}
    tree_before_by_path: dict[str, dict[str, Any]] = {}
    for item in trees:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise ContractError(f"{stage} output_trees contains an invalid state")
        if item["path"] in tree_by_path:
            raise ContractError(f"{stage} current receipt has duplicate output trees")
        tree_by_path[item["path"]] = item
        if item["path"] not in optional_tree_paths:
            validate_current_output_tree(
                problem_root,
                stage,
                item["path"],
                item,
            )
    for item in trees_before:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise ContractError(f"{stage} output_trees_before contains an invalid state")
        if item["path"] in tree_before_by_path:
            raise ContractError(
                f"{stage} current receipt has duplicate output_trees_before"
            )
        tree_before_by_path[item["path"]] = item
    if set(tree_before_by_path) != set(tree_by_path):
        raise ContractError(f"{stage} output_trees_before paths differ from output_trees")
    expected_tree_changes = [
        {
            "path": path,
            "before": tree_before_by_path[path],
            "after": tree_by_path[path],
            "materially_changed": material_change(
                tree_before_by_path[path], tree_by_path[path]
            ),
        }
        for path in tree_by_path
    ]
    if tree_changes != expected_tree_changes or not all(
        item["materially_changed"]
        or (
            item["path"] in optional_tree_paths
            and item["after"].get("status") == "missing-or-unsafe"
        )
        for item in expected_tree_changes
    ):
        raise ContractError(f"{stage} receipt does not prove fresh output trees")
    if any(
        item.get("status") != "missing-or-unsafe" or item.get("files") != []
        for item in trees_before
    ):
        raise ContractError(f"{stage} production output trees were not clear at launch")
    missing_trees = set(STAGES[stage].output_trees) - set(tree_by_path)
    if missing_trees:
        raise ContractError(
            f"{stage} current receipt omits required output trees: {sorted(missing_trees)}"
        )
    missing_optional_trees = optional_tree_paths - set(tree_by_path)
    if missing_optional_trees:
        raise ContractError(
            f"{stage} current receipt omits optional watched output trees: "
            f"{sorted(missing_optional_trees)}"
        )

    preexisting_outputs = receipt.get("preexisting_outputs")
    preexisting_trees = receipt.get("preexisting_output_trees")
    archive = receipt.get("preexisting_archive")
    if not isinstance(preexisting_outputs, list) or not isinstance(
        preexisting_trees, list
    ):
        raise ContractError(f"{stage} preexisting snapshots are not lists")
    if not isinstance(archive, dict):
        raise ContractError(f"{stage} preexisting_archive is not an object")
    expected_archive_root = (RECEIPT_ROOT / stage / run_id / "preexisting").as_posix()
    if (
        archive.get("performed") is not True
        or archive.get("root") != expected_archive_root
        or archive.get("error") is not None
    ):
        raise ContractError(f"{stage} production preexisting archive is incomplete")
    archive_files = archive.get("files")
    archive_trees = archive.get("trees")
    if not isinstance(archive_files, list) or not isinstance(archive_trees, list):
        raise ContractError(f"{stage} preexisting archive records are not lists")
    if len(preexisting_outputs) != len(output_paths) or len(archive_files) != len(
        output_paths
    ):
        raise ContractError(f"{stage} preexisting file archive count is inconsistent")
    for index, output_path in enumerate(output_paths):
        before = preexisting_outputs[index]
        archived = archive_files[index]
        if not isinstance(before, dict) or before.get("path") != output_path:
            raise ContractError(f"{stage} preexisting output order/path is invalid")
        if not isinstance(archived, dict):
            raise ContractError(f"{stage} preexisting file archive record is invalid")
        expected_archive_path = f"{expected_archive_root}/{output_path}"
        if (
            archived.get("path") != output_path
            or archived.get("archive_path") != expected_archive_path
            or archived.get("before") != before
        ):
            raise ContractError(f"{stage} preexisting file archive binding is invalid")
        archive_path = problem_path(
            problem_root,
            safe_relative(expected_archive_path, label="archived output"),
            label="archived output",
            require_exists=False,
        )
        current_archive_state = file_state(archive_path, expected_archive_path)
        if archived.get("archive_state") != current_archive_state:
            raise ContractError(f"{stage} archived output changed: {output_path}")
        expected_archived = before.get("status") != "missing"
        if archived.get("archived") is not expected_archived:
            raise ContractError(f"{stage} archived output presence is inconsistent")
        if expected_archived:
            if (
                current_archive_state.get("status") != before.get("status")
                or current_archive_state.get("size") != before.get("size")
                or current_archive_state.get("sha256") != before.get("sha256")
            ):
                raise ContractError(f"{stage} archived output lost content: {output_path}")
        elif current_archive_state.get("status") != "missing":
            raise ContractError(f"{stage} unexpected archived output: {output_path}")
    if len(preexisting_trees) != len(tree_by_path) or len(archive_trees) != len(
        tree_by_path
    ):
        raise ContractError(f"{stage} preexisting tree archive count is inconsistent")
    bound_preexisting_trees: dict[str, dict[str, Any]] = {}
    for index, tree_path in enumerate(tree_by_path):
        before = preexisting_trees[index]
        archived = archive_trees[index]
        if not isinstance(before, dict) or before.get("path") != tree_path:
            raise ContractError(f"{stage} preexisting tree order/path is invalid")
        if not isinstance(archived, dict):
            raise ContractError(f"{stage} preexisting tree archive record is invalid")
        expected_archive_path = f"{expected_archive_root}/{tree_path}"
        if (
            archived.get("path") != tree_path
            or archived.get("archive_path") != expected_archive_path
            or archived.get("before") != before
        ):
            raise ContractError(f"{stage} preexisting tree archive binding is invalid")
        archive_path = problem_path(
            problem_root,
            safe_relative(expected_archive_path, label="archived output tree"),
            label="archived output tree",
            require_exists=False,
        )
        current_archive_snapshot = snapshot_tree(problem_root, expected_archive_path)
        if archived.get("archive_snapshot") != current_archive_snapshot:
            raise ContractError(f"{stage} archived output tree changed: {tree_path}")
        archive_is_symlink = archive_path.is_symlink()
        archive_exists = archive_path.exists()
        archive_is_directory = (
            archive_exists and not archive_is_symlink and archive_path.is_dir()
        )
        archive_safely_absent = not archive_exists and not archive_is_symlink
        if not archive_is_directory and not archive_safely_absent:
            raise ContractError(f"{stage} archived output tree root is unsafe: {tree_path}")
        expected_archived = archive_is_directory
        if archived.get("archived") is not expected_archived:
            raise ContractError(f"{stage} archived tree presence is inconsistent")
        if expected_archived and tree_content_signature(
            current_archive_snapshot
        ) != tree_content_signature(before):
            raise ContractError(f"{stage} archived output tree lost content: {tree_path}")
        if not expected_archived and not _safely_absent_tree_snapshot(
            before, tree_path
        ):
            raise ContractError(f"{stage} archived output tree is unexpectedly absent: {tree_path}")
        bound_preexisting_trees[tree_path] = before

    for tree_path in optional_tree_paths:
        validate_current_output_tree(
            problem_root,
            stage,
            tree_path,
            tree_by_path[tree_path],
            preexisting=bound_preexisting_trees[tree_path],
        )

    if STAGES[stage].requires_blind_gate:
        _validate_gate_evidence(
            problem_root,
            receipt.get("blind_prerequisite_gate"),
            verifier_name="verify_blind_stage.py",
            label=f"{stage} blind prerequisite",
            json_flag=False,
        )
    elif receipt.get("blind_prerequisite_gate") is not None:
        raise ContractError(f"{stage} receipt has an unexpected blind prerequisite")

    gate_name = HANDOFF_GATE_BY_STAGE.get(stage)
    if gate_name is None:
        if receipt.get("handoff_prerequisite_gate") is not None:
            raise ContractError(f"{stage} receipt has an unexpected handoff gate")
    else:
        _validate_gate_evidence(
            problem_root,
            receipt.get("handoff_prerequisite_gate"),
            verifier_name=gate_name,
            label=f"{stage} handoff prerequisite",
            json_flag=True,
            extra_args=HANDOFF_GATE_ARGUMENTS.get(stage, ()),
        )

    expected_prior = IMMEDIATE_PRIOR_STAGE.get(stage)
    if expected_prior is None:
        if receipt.get("prior_stage_receipt") is not None:
            raise ContractError(f"{stage} receipt has an unexpected prior stage")
    else:
        current_prior = require_prior_stage_receipt(
            problem_root, expected_prior, _seen=seen
        )
        if receipt.get("prior_stage_receipt") != current_prior:
            raise ContractError(f"{stage} prior-stage receipt hash is stale")

    summary = {
        "stage": stage,
        "path": receipt_relative.as_posix(),
        "sha256": sha256_file(path),
    }
    seen.remove(stage)
    return summary


def run_blind_prerequisite(problem_root: Path) -> dict[str, Any]:
    verifier = Path(__file__).resolve().with_name("verify_blind_stage.py")
    completed = subprocess.run(
        [sys.executable, str(verifier), "--problem-dir", str(problem_root)],
        cwd=problem_root,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    return {
        "command": [sys.executable, verifier.name, "--problem-dir", "."],
        "verifier_sha256": sha256_file(verifier),
        "dependency_sha256": {},
        "exit_code": completed.returncode,
        "stdout_sha256": hashlib.sha256(completed.stdout.encode()).hexdigest(),
        "stderr_sha256": hashlib.sha256(completed.stderr.encode()).hexdigest(),
    }


def run_handoff_gate(
    problem_root: Path,
    script_name: str,
    extra_args: tuple[str, ...] = (),
) -> dict[str, Any]:
    verifier = Path(__file__).resolve().with_name(script_name)
    command = [
        sys.executable,
        str(verifier),
        "--problem-dir",
        str(problem_root),
        *extra_args,
        "--json",
    ]
    completed = subprocess.run(
        command,
        cwd=problem_root,
        capture_output=True,
        text=True,
        timeout=HANDOFF_GATE_TIMEOUT_SECONDS.get(script_name, 300),
        check=False,
    )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        payload = None
    reported_status = payload.get("status") if isinstance(payload, dict) else None
    semantic_evidence = None
    if script_name == "verify_completion_handoff.py" and isinstance(payload, dict):
        semantic_evidence = {
            "completion_replay": payload.get("completion_replay")
        }
    dependency_sha256 = {
        name: sha256_file(Path(__file__).resolve().with_name(name))
        for name in HANDOFF_GATE_DEPENDENCIES.get(script_name, ())
    }
    return {
        "command": [
            sys.executable,
            verifier.name,
            "--problem-dir",
            ".",
            *extra_args,
            "--json",
        ],
        "verifier_sha256": sha256_file(verifier),
        "dependency_sha256": dependency_sha256,
        "exit_code": completed.returncode,
        "reported_status": reported_status,
        "semantic_evidence": semantic_evidence,
        "stdout_sha256": hashlib.sha256(completed.stdout.encode()).hexdigest(),
        "stderr_sha256": hashlib.sha256(completed.stderr.encode()).hexdigest(),
    }


def execute(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    root: Path = args.problem_dir
    try:
        load_statement_resources(root)
    except StatementResourceError as exc:
        raise ContractError(f"statement resource preflight failed: {exc}") from exc
    contract = STAGES[args.stage]
    prompt_path = problem_path(root, args.prompt_rel, label="prompt", require_exists=True)
    prompt_before = file_state(prompt_path, args.prompt_rel.as_posix())
    if prompt_before["status"] != "present-nonempty":
        raise ContractError("prompt must be a non-empty regular file")

    required_inputs = [safe_relative(value, label="stage input") for value in contract.inputs]
    required_inputs.extend(args.extra_inputs)
    required_outputs = [safe_relative(value, label="stage output") for value in contract.outputs]
    required_outputs.extend(args.extra_outputs)
    optional_outputs = [
        safe_relative(value, label="optional stage output")
        for value in contract.optional_outputs
    ]
    outputs = [*required_outputs, *optional_outputs]
    required_output_trees = [
        safe_relative(value, label="output tree") for value in contract.output_trees
    ]
    optional_output_trees = [
        safe_relative(value, label="optional output tree")
        for value in contract.optional_output_trees
    ]
    output_trees = [*required_output_trees, *optional_output_trees]
    output_trees.extend(args.extra_trees)
    if len({path.as_posix() for path in required_inputs}) != len(required_inputs):
        raise ContractError("stage inputs contain duplicates")
    if len({path.as_posix() for path in outputs}) != len(outputs):
        raise ContractError("stage outputs contain duplicates")
    if len({path.as_posix() for path in output_trees}) != len(output_trees):
        raise ContractError("stage output trees contain duplicates")
    validate_output_layout(
        args.prompt_rel, required_inputs, outputs, output_trees
    )

    base_rel = RECEIPT_ROOT / args.stage / args.run_id
    base = problem_path(root, base_rel, label="stage execution root", require_exists=False)
    current_rel = RECEIPT_ROOT / args.stage / "current.json"
    current_path = problem_path(root, current_rel, label="current stage receipt", require_exists=False)
    if base.exists() or base.is_symlink():
        raise ContractError(f"refusing to overwrite stage attempt: {base_rel}")

    blind_gate: dict[str, Any] | None = None
    prior_stage_receipt: dict[str, Any] | None = None
    handoff_gate: dict[str, Any] | None = None
    if contract.requires_blind_gate:
        blind_gate = run_blind_prerequisite(root)
        if blind_gate["exit_code"] != 0:
            raise ContractError("blind-stage verifier failed; refusing stage launch")
    prior_stage = IMMEDIATE_PRIOR_STAGE.get(args.stage)
    if prior_stage is not None:
        prior_stage_receipt = require_prior_stage_receipt(root, prior_stage)
    gate_name = HANDOFF_GATE_BY_STAGE.get(args.stage)
    mutating_test_handoff = (
        args.test_command is not None
        and gate_name == "verify_completion_handoff.py"
    )
    if gate_name is not None and not mutating_test_handoff:
        handoff_gate = run_handoff_gate(
            root,
            gate_name,
            HANDOFF_GATE_ARGUMENTS.get(args.stage, ()),
        )
    if handoff_gate is not None and (
        handoff_gate["exit_code"] != 0
        or handoff_gate.get("reported_status") != "pass"
    ):
        raise ContractError(
            f"{args.stage} prerequisite handoff gate failed; refusing stage launch"
        )

    # Handoff gates may refresh canonical prerequisites. In particular, the
    # readiness handoff reruns completion and rewrites both completion and
    # machine-regression receipts. Freeze inputs only after that trusted replay,
    # then require the child stage to leave the refreshed state untouched.
    inputs_before: list[dict[str, Any]] = []
    for relative in required_inputs:
        path = problem_path(root, relative, label="stage input", require_exists=True)
        state = file_state(path, relative.as_posix())
        if state["status"] != "present-nonempty":
            raise ContractError(f"stage input is not a non-empty regular file: {relative}")
        inputs_before.append(state)
    preexisting_outputs = [
        file_state(problem_path(root, relative, label="stage output", require_exists=False), relative.as_posix())
        for relative in outputs
    ]
    preexisting_trees = [
        snapshot_tree(root, relative.as_posix()) for relative in output_trees
    ]

    command = test_command(args, prompt_path) if args.test_command else default_command(args)
    if args.test_command is None:
        if current_path.is_file() or current_path.is_symlink():
            current_path.unlink()
        elif current_path.exists():
            raise ContractError(f"current stage receipt is not a file: {current_rel}")
    base.mkdir(parents=True)
    stdout_path = base / "codex-exec.jsonl"
    stderr_path = base / "stderr.log"
    receipt_path = base / "receipt.json"

    started = utc_now()
    if args.test_command is None:
        preexisting_archive, archive_error = archive_preexisting_outputs(
            root,
            base_rel,
            outputs,
            output_trees,
            preexisting_outputs,
            preexisting_trees,
        )
    else:
        preexisting_archive = {
            "performed": False,
            "root": None,
            "files": [],
            "trees": [],
            "error": None,
        }
        archive_error = None
    outputs_before = [
        file_state(
            problem_path(root, relative, label="stage output", require_exists=False),
            relative.as_posix(),
        )
        for relative in outputs
    ]
    trees_before = [
        snapshot_tree(root, relative.as_posix()) for relative in output_trees
    ]
    process: subprocess.Popen[bytes] | None = None
    spawn_error: str | None = (
        f"preexisting archive failed: {archive_error}" if archive_error else None
    )
    interrupted = False
    termination_action: str | None = None
    try:
        with (
            prompt_path.open("rb") as stdin_stream,
            stdout_path.open("xb") as stdout_stream,
            stderr_path.open("xb") as stderr_stream,
        ):
            if archive_error is None:
                process = subprocess.Popen(
                    command,
                    cwd=root,
                    stdin=stdin_stream,
                    stdout=stdout_stream,
                    stderr=stderr_stream,
                    shell=False,
                    start_new_session=True,
                )
                try:
                    process.wait()
                except KeyboardInterrupt:
                    interrupted = True
                    termination_action = stop_process(process)
    except (OSError, ValueError) as exc:
        spawn_error = f"{type(exc).__name__}: {exc}"

    inputs_after = [
        file_state(problem_path(root, relative, label="stage input", require_exists=False), relative.as_posix())
        for relative in required_inputs
    ]
    inputs_unchanged = inputs_after == inputs_before
    prompt_after = file_state(prompt_path, args.prompt_rel.as_posix())
    prompt_unchanged = prompt_after == prompt_before
    outputs_after = [
        file_state(problem_path(root, relative, label="stage output", require_exists=False), relative.as_posix())
        for relative in outputs
    ]
    trees_after = [snapshot_tree(root, relative.as_posix()) for relative in output_trees]
    output_changes = [
        {
            "path": after["path"],
            "before": before,
            "after": after,
            "materially_changed": material_change(before, after),
        }
        for before, after in zip(outputs_before, outputs_after)
    ]
    tree_changes = [
        {
            "path": after["path"],
            "before": before,
            "after": after,
            "materially_changed": material_change(before, after),
        }
        for before, after in zip(trees_before, trees_after)
    ]
    optional_output_paths = {path.as_posix() for path in optional_outputs}
    outputs_complete = True
    for relative, item in zip(outputs, outputs_after):
        if relative.as_posix() not in optional_output_paths:
            outputs_complete = outputs_complete and item["status"] == "present-nonempty"
            continue
        path = problem_path(
            root,
            relative,
            label="optional stage output",
            require_exists=False,
        )
        safely_absent = not path.exists() and not path.is_symlink()
        outputs_complete = outputs_complete and (
            item["status"] == "present-nonempty" or safely_absent
        )
    optional_tree_paths = {path.as_posix() for path in optional_output_trees}
    trees_complete = True
    for relative, item in zip(output_trees, trees_after):
        if relative.as_posix() not in optional_tree_paths:
            trees_complete = trees_complete and item["status"] == "present"
            continue
        path = problem_path(
            root,
            relative,
            label="optional output tree",
            require_exists=False,
        )
        safely_absent = not path.exists() and not path.is_symlink()
        trees_complete = trees_complete and (
            item["status"] == "present" or safely_absent
        )
    outputs_materially_updated = all(
        item["materially_changed"]
        or (
            item["path"] in optional_output_paths
            and item["after"].get("status") == "missing"
        )
        for item in output_changes
    )
    output_trees_materially_updated = all(
        item["materially_changed"]
        or (
            item["path"] in optional_tree_paths
            and item["after"].get("status") == "missing-or-unsafe"
        )
        for item in tree_changes
    )
    codex_jsonl_validation = validate_codex_jsonl(stdout_path)
    codex_jsonl_required = args.test_command is None
    codex_completed = (
        not codex_jsonl_required
        or codex_jsonl_validation.get("status") == "passed"
    )
    exit_code = process.returncode if process is not None else None
    success = (
        not interrupted
        and spawn_error is None
        and exit_code == 0
        and prompt_unchanged
        and inputs_unchanged
        and outputs_complete
        and trees_complete
        and outputs_materially_updated
        and output_trees_materially_updated
        and codex_completed
    )
    mode = "test-override" if args.test_command else "production-codex"
    receipt = {
        "schema_version": 1,
        "runner": "icpc-light-stage-agent-runner",
        "stage": args.stage,
        "run_id": args.run_id,
        "execution_mode": mode,
        "model": REQUIRED_MODEL,
        "reasoning_effort": REQUIRED_REASONING_EFFORT,
        "command": command,
        "started_at_utc": started,
        "finished_at_utc": utc_now(),
        "exit_code": exit_code,
        "spawn_error": spawn_error,
        "interrupted": interrupted,
        "termination_action": termination_action,
        "success": success,
        "blind_prerequisite_gate": blind_gate,
        "prior_stage_receipt": prior_stage_receipt,
        "handoff_prerequisite_gate": handoff_gate,
        "prompt": prompt_after,
        "prompt_unchanged": prompt_unchanged,
        "inputs": inputs_after,
        "inputs_unchanged": inputs_unchanged,
        "preexisting_outputs": preexisting_outputs,
        "preexisting_output_trees": preexisting_trees,
        "preexisting_archive": preexisting_archive,
        "outputs_before": outputs_before,
        "outputs": outputs_after,
        "output_changes": output_changes,
        "outputs_materially_updated": outputs_materially_updated,
        "output_trees_before": trees_before,
        "output_trees": trees_after,
        "output_tree_changes": tree_changes,
        "output_trees_materially_updated": output_trees_materially_updated,
        "codex_jsonl_required": codex_jsonl_required,
        "codex_jsonl_validation": codex_jsonl_validation,
        "stdout_log": file_state(stdout_path, stdout_path.relative_to(root).as_posix()),
        "stderr_log": file_state(stderr_path, stderr_path.relative_to(root).as_posix()),
    }
    write_json_exclusive(receipt_path, receipt)
    if success and args.test_command is None:
        atomic_write_json(current_path, receipt)
    result = {
        "schema_version": 1,
        "status": "pass" if success else "fail",
        "stage": args.stage,
        "attempt_receipt": receipt_path.relative_to(root).as_posix(),
        "current_receipt": current_rel.as_posix() if success and args.test_command is None else None,
    }
    return (0 if success else 1), result


def main() -> int:
    args = parse_args()
    try:
        code, result = execute(args)
    except (ContractError, OSError, subprocess.SubprocessError) as exc:
        print(f"run_stage_agent.py: error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(
            f"ICPC Light stage {result['stage']}: {result['status'].upper()}\n"
            f"attempt receipt: {result['attempt_receipt']}"
        )
        if result["current_receipt"]:
            print(f"current production receipt: {result['current_receipt']}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
