#!/usr/bin/env python3
"""Exercise the task pipeline with test-only deterministic injected workers."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import time
from typing import Any


MODEL = "gpt-5.6-sol"
# The frozen v3.3 script fixtures validate their upstream release metadata.
# Production model calls are independently frozen to xhigh by the bridge.
EFFORT = "xhigh"


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _run(command: list[str], *, cwd: Path, label: str) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        command,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
        env={
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": os.environ.get("HOME", str(cwd)),
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        },
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"{label} exited {completed.returncode}: "
            f"{(completed.stderr or completed.stdout)[-3000:]}"
        )
    return completed


def _generation(workspace: Path, worker: Path) -> dict[str, Any]:
    started = time.monotonic()
    problem = workspace / "scratch" / "generation-problem"
    problem.mkdir()
    statement = workspace / "surface" / "statement.md"
    shutil.copy2(statement, problem / "statement.md", follow_symlinks=False)
    scripts = workspace / "skills" / "icpc-light-problem-builder" / "scripts"
    build_sweep = scripts / "build_sweep.py"
    run_sweep = scripts / "run_sweep.py"
    run_review = scripts / "run_blind_review.py"
    for path in (build_sweep, run_sweep, run_review):
        if path.is_symlink() or not path.is_file():
            raise RuntimeError(f"frozen pipeline script is missing: {path.name}")

    public_manifest = problem / "blind-solves" / "icpc-light" / "public-manifest.json"
    public_manifest.parent.mkdir(parents=True)
    _write_json(
        public_manifest,
        {
            "schema_version": 1,
            "files": [
                {
                    "path": "statement.md",
                    "sha256": _sha256_file(problem / "statement.md"),
                }
            ],
        },
    )
    python = str(Path(sys.executable).resolve(strict=True))
    _run(
        [
            python,
            str(build_sweep),
            "--problem-dir",
            str(problem),
            "--model",
            MODEL,
            "--reasoning-effort",
            EFFORT,
            "--neutral-count",
            "2",
            "--deceptive-count",
            "2",
        ],
        cwd=problem,
        label="build_sweep",
    )
    solver_template = " ".join(
        (
            shlex.quote(python),
            shlex.quote(str(worker)),
            "--mode blind-lane",
            "--kind {kind}",
            "--lane-id {lane_id}",
        )
    )
    _run(
        [
            python,
            str(run_sweep),
            "--problem-dir",
            str(problem),
            "--plan",
            "blind-solves/icpc-light/sweep-plan.json",
            "--public-manifest",
            "blind-solves/icpc-light/public-manifest.json",
            "--solver-command",
            solver_template,
            "--blind-time-limit-seconds",
            "20",
        ],
        cwd=problem,
        label="run_sweep",
    )
    plan_path = problem / "blind-solves" / "icpc-light" / "sweep-plan.json"
    results_path = problem / "blind-solves" / "icpc-light" / "sweep-plan-results.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    results = json.loads(results_path.read_text(encoding="utf-8"))
    if results.get("execution_mode") != "test-override":
        raise RuntimeError("Generation smoke did not use the test-override sweep")
    runs = plan.get("runs")
    if not isinstance(runs, list) or len(runs) != 4:
        raise RuntimeError("Generation smoke did not launch the required 2+2 lanes")
    neutral = next(item for item in runs if item.get("id") == "neutral-01")
    attempt_id = str(neutral["workspace_rel"])
    review_id = "smoke-neutral-01-review"
    reviewer_id = "smoke-independent-reviewer"
    review_template = " ".join(
        (
            shlex.quote(python),
            shlex.quote(str(worker)),
            "--mode review",
            "--review-id {review_id}",
            "--reviewer-id {reviewer_id}",
            "--attempt-id {attempt_id}",
            "--source-sha256 {source_sha256}",
        )
    )
    _run(
        [
            python,
            str(run_review),
            "--problem-dir",
            str(problem),
            "--attempt-id",
            attempt_id,
            "--review-id",
            review_id,
            "--reviewer-id",
            reviewer_id,
            "--model",
            MODEL,
            "--reasoning-effort",
            EFFORT,
            "--review-command",
            review_template,
            "--blind-time-limit-seconds",
            "20",
        ],
        cwd=problem,
        label="run_blind_review",
    )
    review_receipt = (
        problem / "audit" / "private" / "blind-reviews" / f"{review_id}-test.json"
    )
    if not review_receipt.is_file():
        raise RuntimeError("independent blind review did not publish its test receipt")
    source = problem / attempt_id / "main.cpp"
    shutil.copy2(source, workspace / "output" / "main.cpp", follow_symlinks=False)
    return {
        "execution_mode": "v3.3-test-override-blind-sweep",
        "lane_count": 4,
        "neutral_count": 2,
        "deceptive_count": 2,
        "selected_attempt": attempt_id,
        "sweep_plan_sha256": _sha256_file(plan_path),
        "sweep_results_sha256": _sha256_file(results_path),
        "blind_review_receipt_sha256": _sha256_file(review_receipt),
        "duration_seconds": round(time.monotonic() - started, 3),
    }


def _adversarial(workspace: Path, worker: Path, task: str) -> dict[str, Any]:
    started = time.monotonic()
    stage = workspace / "scratch" / f"{task}-task-slice"
    public = stage / "public"
    public.mkdir(parents=True)
    surface = workspace / "surface"
    before: dict[str, str] = {}
    public_files = (
        ("task.json", "statement.md")
        if task == "fault_coverage"
        else ("task.json", "statement.md", "wrong-source.txt")
    )
    for name in public_files:
        source = surface / name
        before[name] = _sha256_file(source)
        shutil.copy2(source, public / name, follow_symlinks=False)
        if _sha256_file(public / name) != before[name]:
            raise RuntimeError(f"{task} public copy changed {name}")
    python = str(Path(sys.executable).resolve(strict=True))
    _run(
        [
            python,
            str(worker),
            "--mode",
            "coverage" if task == "fault_coverage" else "hack",
        ],
        cwd=stage,
        label=f"public-only {task} task slice",
    )
    for name, digest in before.items():
        if _sha256_file(public / name) != digest:
            raise RuntimeError(f"{task} worker modified public file {name}")
    candidate = stage / "output" / "candidate.in"
    if candidate.is_symlink() or not candidate.is_file():
        raise RuntimeError(f"{task} task slice produced no regular candidate.in")
    shutil.copy2(candidate, workspace / "output" / "candidate.in", follow_symlinks=False)
    receipt = {
        "schema_version": 1,
        "execution_mode": f"test-override-public-only-{task}-slice",
        "public_files": before,
        "candidate_sha256": _sha256_file(candidate),
        "duration_seconds": round(time.monotonic() - started, 3),
    }
    _write_json(stage / "receipt.json", receipt)
    return {**receipt, "receipt_sha256": _sha256_file(stage / "receipt.json")}


def _package(workspace: Path) -> dict[str, Any]:
    started = time.monotonic()
    statement = workspace / "surface" / "statement.md"
    shutil.copy2(statement, workspace / "statement.md", follow_symlinks=False)
    audit = workspace / "audit"
    tests = workspace / "package" / "tests"
    audit.mkdir()
    tests.mkdir(parents=True)
    (tests / "edge.in").write_text("-7 4\n", encoding="utf-8")
    (tests / "basic.in").write_text("2 3\n", encoding="utf-8")
    _write_json(
        audit / "regression-plan.json",
        {
            "release_tests": [
                {"input": "package/tests/edge.in"},
                {"input": "package/tests/basic.in"},
            ]
        },
    )
    (audit / "readiness.md").write_text(
        "# Deterministic readiness\n\nverdict: go\n", encoding="utf-8"
    )
    return {
        "schema_version": 1,
        "execution_mode": "test-override-statement-only-package",
        "release_test_count": 2,
        "statement_sha256": _sha256_file(statement),
        "regression_plan_sha256": _sha256_file(audit / "regression-plan.json"),
        "readiness_sha256": _sha256_file(audit / "readiness.md"),
        "duration_seconds": round(time.monotonic() - started, 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", type=Path, required=True)
    parser.add_argument(
        "--task",
        choices=("generation", "hacking", "fault_coverage", "fault_exposure", "test_package"),
        required=True,
    )
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    workspace = args.workspace.resolve(strict=True)
    worker = args.worker.resolve(strict=True)
    if worker.is_symlink() or not worker.is_file():
        raise RuntimeError("fixture worker must be a regular non-symlink file")
    for name in ("UOJ_API_KEY", "TATU_API_KEY", "OPENAI_API_KEY"):
        if name in os.environ:
            raise RuntimeError(f"secret-like environment crossed into pipeline fixture: {name}")
    detail = (
        _generation(workspace, worker)
        if args.task == "generation"
        else _package(workspace)
        if args.task == "test_package"
        else _adversarial(workspace, worker, args.task)
    )
    result = {
        "schema_version": 1,
        "status": "completed",
        "task": args.task,
        "raw_text": "deterministic task-pipeline fixture",
        "final_message": f"{args.task} test-only pipeline artifact exported",
        "transcript": [
            {
                "role": "assistant",
                "content": "test-only injected workers; no model or UOJ call",
            }
        ],
        "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        "pipeline_test_detail": detail,
    }
    _write_json(workspace / "control" / "agent-result.json", result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
