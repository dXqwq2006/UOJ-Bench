"""Reference public-only Codex agent for supported one-shot task slices.

Production deployment is expected to launch this inside the audited zero-mount
agent boundary, where the credential relay configuration already exists.  It
never reads UOJ credentials and never calls the UOJ API.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import shutil
import subprocess
import time
from typing import Any

from .runtime import (
    MAX_AGENT_LOG_BYTES,
    _read_bounded_regular_file,
    _write_json,
)


MODEL = "gpt-5.6-sol"
REASONING_EFFORT = "xhigh"


def _prompt(task: str) -> str:
    common = """Work only inside the current job workspace. Do not inspect any
parent directory, benchmark dataset, historical result, official solution, UOJ
page, or network resource. The only task inputs are under surface/. First read
skills/icpc-light-problem-builder/SKILL.md and follow the references relevant to
the requested workflow. Do not reveal chain of thought. Do not modify surface/
or skills/.
"""
    if task == "generation":
        return common + """
This is a UOJ-Bench Generation public-only blind-solve slice. Read
surface/statement.md, solve the complete problem with a worst-case-valid
algorithm, and write exactly one complete GNU C++20 submission to
output/main.cpp. This task asks for a solver candidate, not a new problem or a
full ICPC package. Do not create another file under output/.
"""
    if task == "hacking":
        return common + """
This is a UOJ-Bench one-shot Hacking slice. Read surface/statement.md,
surface/task.json for `submission_language`, and the exact target program in
surface/wrong-source.txt. Find one valid complete stdin that makes that exact
program fail the stated problem. Write exactly one artifact: prefer raw bytes
in output/candidate.in; use output/generator.py only if a static input is
genuinely unsuitable. Do not write or seek an accepted source, an answer file,
or another file under output/.
"""
    if task == "fault_coverage":
        return common + """
This is a CodeContests+ Verified or TestCase-Eval Task 1 Fault Coverage slice.
Read surface/statement.md and the public metadata in surface/task.json. Build
one small, complete, valid stdin that is likely to expose common incorrect
solutions; no target program is supplied. Write exactly one artifact: prefer
raw bytes in output/candidate.in; use output/generator.py only if a static input
is genuinely unsuitable. Do not write or seek an accepted or wrong source, an
answer file, or another file under output/. The benchmark will run its own
validator, oracle, checker, and local evaluator; do not call UOJ or any external
judge. Each invocation produces one candidate; the native benchmark runner
controls the independent-generation budget.
"""
    if task == "test_package":
        return common + """
This is the statement-only ICPC Light v3.3 package benchmark. Read only
surface/statement.md and copy it to statement.md as the immutable problem
specification. Execute the full ICPC Light workflow: blind routes, standard
solution and oracle, validator/checker, generators, qualified wrong routes,
adversarial hardening, regression gate, completion receipt, and independent
readiness review. This benchmark port uses gpt-5.6-sol with xhigh for every
model call; record that explicit delta from the vendored ultra configuration.

The final scoreable package is the ordered release_tests array in
audit/regression-plan.json and the matching package/tests/**/*.in files. It must
contain at most 50 tests. Internal differential, stress, survivability, and
adversarial executions are not part of that limit. Do not seek or use benchmark
accepted/wrong programs, validators, checkers, dataset statistics, UOJ, or any
external judge. Finish only after audit/readiness.md has verdict: go and the
vendored readiness verifier succeeds. Keep output/ empty.
"""
    return common + """
This is a TestCase-Eval Task 2 Fault Exposure slice. Read
surface/statement.md, surface/task.json for `submission_id` and
`submission_language`, and the exact target program in
surface/wrong-source.txt. Find one valid complete stdin that makes that exact
program fail the stated problem. Write exactly one artifact: prefer raw bytes
in output/candidate.in; use output/generator.py only if a static input is
genuinely unsuitable. Do not write or seek an accepted source, an answer file,
or another file under output/. The benchmark will run its own local evaluator;
do not call UOJ or any external judge.
"""


def _usage(data: bytes) -> dict[str, int]:
    maxima = {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0}
    for line in data.decode("utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        stack: list[Any] = [event]
        while stack:
            value = stack.pop()
            if isinstance(value, dict):
                for key, child in value.items():
                    if key in maxima and isinstance(child, int):
                        maxima[key] = max(maxima[key], child)
                    elif isinstance(child, (dict, list)):
                        stack.append(child)
            elif isinstance(value, list):
                stack.extend(value)
    maxima["total_tokens"] = maxima["input_tokens"] + maxima["output_tokens"]
    return maxima


def _final_message(data: bytes) -> str:
    """Return the last assistant message from Codex JSONL events."""

    messages: list[str] = []
    for line in data.decode("utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        stack: list[Any] = [event]
        while stack:
            value = stack.pop()
            if isinstance(value, dict):
                item_type = value.get("type")
                if item_type in {"agent_message", "assistant_message"}:
                    text = value.get("text")
                    if isinstance(text, str) and text:
                        messages.append(text)
                if value.get("role") == "assistant":
                    content = value.get("content")
                    if isinstance(content, str) and content:
                        messages.append(content)
                stack.extend(value.values())
            elif isinstance(value, list):
                stack.extend(value)
    return messages[-1] if messages else ""


def _tail(data: bytes, maximum: int = 2000) -> str:
    return data[-maximum * 4 :].decode("utf-8", errors="replace")[-maximum:]


def _write_result(workspace: Path, value: dict[str, Any]) -> None:
    control = workspace / "control"
    metadata = control.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or control.is_symlink():
        raise RuntimeError("control must remain a regular directory")
    _write_json(control / "agent-result.json", value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--task",
        choices=(
            "generation",
            "hacking",
            "fault_coverage",
            "fault_exposure",
            "test_package",
        ),
        required=True,
    )
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args(argv)
    workspace = args.workspace.resolve(strict=True)
    codex = shutil.which("codex")
    if codex is None:
        _write_result(
            workspace,
            {
                "schema_version": 1,
                "status": "retryable_error",
                "task": args.task,
                "error": "codex executable was not found",
                "transcript": [],
                "usage": {},
            },
        )
        return 2
    command = [
        codex,
        "exec",
        "--json",
        "--ephemeral",
        "--skip-git-repo-check",
        "--ignore-user-config",
        "--ignore-rules",
        "--model",
        MODEL,
        "-c",
        f'model_reasoning_effort="{REASONING_EFFORT}"',
        "--sandbox",
        "workspace-write",
        "--cd",
        str(workspace),
        "-",
    ]
    started = time.monotonic()
    events_path = workspace / "control" / "codex-events.jsonl"
    stderr_path = workspace / "control" / "codex-stderr.log"
    with events_path.open("xb") as events, stderr_path.open("xb") as stderr:
        completed = subprocess.run(
            command,
            cwd=workspace,
            input=_prompt(args.task).encode("utf-8"),
            stdout=events,
            stderr=stderr,
            check=False,
        )
    events_data = _read_bounded_regular_file(
        events_path, label="Codex event log", maximum=MAX_AGENT_LOG_BYTES
    )
    stderr_data = _read_bounded_regular_file(
        stderr_path, label="Codex stderr log", maximum=MAX_AGENT_LOG_BYTES
    )
    final = _final_message(events_data)
    status = "completed" if completed.returncode == 0 else "retryable_error"
    result = {
        "schema_version": 1,
        "status": status,
        "task": args.task,
        "raw_text": final,
        "final_message": final,
        "transcript": ([{"role": "assistant", "content": final}] if final else []),
        "usage": _usage(events_data),
        "codex_logs": {
            "events_sha256": hashlib.sha256(events_data).hexdigest(),
            "events_bytes": len(events_data),
            "stderr_sha256": hashlib.sha256(stderr_data).hexdigest(),
            "stderr_bytes": len(stderr_data),
        },
        "duration_seconds": round(time.monotonic() - started, 3),
    }
    if completed.returncode != 0:
        result["error"] = _tail(stderr_data) or "codex exited nonzero"
    _write_result(workspace, result)
    return 0 if status == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
