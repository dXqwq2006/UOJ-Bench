"""Persist the UOJ Hacking agent's first model turn without calling UOJ.

Only round one can be generated ahead of evaluation. Later turns depend on the
previous UOJ result and must remain in the coupled agent runner.
"""

from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import json
import os
from pathlib import Path
import re
import threading
import time
from typing import Any, Mapping

from solution import load_solver
from solution.api import HackingInput, require_solver_support
from scripts.run_hack_agent_batch import (
    SCHEMA_VERSION,
    HackSample,
    _atomic_json,
    _dataset_hashes,
    _now,
    _pending_stages,
    _prepare_run,
    aggregate_usage,
    load_samples,
)


PYTHON_BLOCK = re.compile(r"```python\n(.*?)```", re.DOTALL)
MAX_ATTEMPTS = 3


def _request_identity() -> dict[str, str | None]:
    return {
        "base_url": os.environ.get("TATU_BASE_URL") or None,
        "deployer": os.environ.get("TATU_DEPLOYER") or None,
        "openai_transport": os.environ.get("TATU_OPENAI_TRANSPORT") or None,
        "reasoning_effort": os.environ.get("TATU_REASONING_EFFORT") or None,
        "max_output_tokens": os.environ.get("TATU_MAX_OUTPUT_TOKENS") or None,
        "temperature": os.environ.get("TATU_TEMPERATURE") or None,
    }


def _run_identity(
    samples: list[HackSample],
    dataset_dir: Path,
    split: str,
    solver_name: str,
    model: str,
    smoke_per_split: int,
) -> dict[str, Any]:
    return {
        "phase": "rollout",
        "round": 1,
        "split": split,
        "solver": solver_name,
        "model": model,
        "smoke_per_split": smoke_per_split,
        "request": _request_identity(),
        "datasets": _dataset_hashes(dataset_dir, split),
        "samples": [sample.public_record() for sample in samples],
    }


def _read_records(samples_dir: Path, known_ids: set[str]) -> dict[str, dict[str, Any]]:
    records = {}
    if not samples_dir.exists():
        return records
    for path in samples_dir.glob("*.json"):
        with path.open(encoding="utf-8") as file:
            record = json.load(file)
        sample_id = record.get("sample", {}).get("sample_id")
        if sample_id not in known_ids or path.stem != sample_id:
            raise ValueError(f"unexpected rollout result {path}")
        if record.get("status") not in {"completed", "retryable_error", "api_failed"}:
            raise ValueError(f"invalid status in {path}")
        records[sample_id] = record
    return records


def _summary(samples: list[HackSample], records: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    completed = [record for record in records.values() if record.get("status") == "completed"]
    retryable = sum(record.get("status") == "retryable_error" for record in records.values())
    sources = Counter(str(record.get("provenance", "unknown")) for record in completed)

    def group(items: list[HackSample]) -> dict[str, int]:
        ids = {sample.sample_id for sample in items}
        done = [record for sample_id, record in records.items() if sample_id in ids]
        return {
            "planned": len(items),
            "completed": sum(record.get("status") == "completed" for record in done),
            "failed": sum(record.get("status") == "api_failed" for record in done),
            "valid_candidate": sum(
                record.get("status") == "completed" and bool(record.get("candidate"))
                for record in done
            ),
            "retryable_error": sum(record.get("status") == "retryable_error" for record in done),
        }

    return {
        "schema_version": SCHEMA_VERSION,
        "updated_at": _now(),
        "phase": "rollout",
        "round": 1,
        "overall": group(samples),
        "splits": {
            split: group([sample for sample in samples if sample.split == split])
            for split in ("easy", "hard")
            if any(sample.split == split for sample in samples)
        },
        "usage": aggregate_usage(record.get("usage", {}) for record in completed),
        "provenance": dict(sorted(sources.items())),
        "retryable_error": retryable,
    }


def _seed_record(sample: HackSample, source: Mapping[str, Any], source_dir: Path) -> dict[str, Any] | None:
    if source.get("status") != "completed":
        return None
    transcript = source.get("transcript")
    messages = source.get("messages")
    usages = source.get("usages")
    if not isinstance(transcript, list) or len(transcript) < 2:
        return None
    if not isinstance(messages, list) or len(messages) < 2:
        return None
    if not isinstance(usages, list) or not usages or not isinstance(usages[0], Mapping):
        return None
    raw_text = messages[1].get("content") if isinstance(messages[1], Mapping) else None
    if not isinstance(raw_text, str):
        return None
    match = PYTHON_BLOCK.search(raw_text)
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "completed",
        "sample": sample.public_record(),
        "round": 1,
        "candidate": match.group(1) if match else None,
        "parse_error": None if match else "no output hack data",
        "raw_text": raw_text,
        "message": messages[1],
        "transcript": messages[:2],
        "usage": dict(usages[0]),
        "provenance": "imported_agent_result",
        "source_result_dir": str(source_dir),
        "source_attempt": source.get("attempt"),
        "created_at": _now(),
        "duration_seconds": 0,
    }


def _seed_from_agent_results(
    seed_dir: Path,
    result_dir: Path,
    identity: Mapping[str, Any],
    samples: list[HackSample],
    records: dict[str, dict[str, Any]],
) -> int:
    with (seed_dir / "manifest.json").open(encoding="utf-8") as file:
        seed_manifest = json.load(file)
    seed_run = seed_manifest.get("run", {})
    for key in ("solver", "model", "datasets"):
        if seed_run.get(key) != identity.get(key):
            raise ValueError(f"seed result {key} does not match rollout run")

    seeded = 0
    for sample in samples:
        if records.get(sample.sample_id, {}).get("status") == "completed":
            continue
        path = seed_dir / "samples" / f"{sample.sample_id}.json"
        if not path.exists():
            continue
        with path.open(encoding="utf-8") as file:
            source = json.load(file)
        record = _seed_record(sample, source, seed_dir)
        if record is None:
            continue
        _atomic_json(result_dir / "samples" / path.name, record)
        records[sample.sample_id] = record
        seeded += 1
    return seeded


def run_batch(
    *,
    dataset_dir: str | Path,
    result_dir: str | Path,
    split: str,
    solver_name: str,
    model: str,
    workers: int = 8,
    resume: bool = False,
    smoke_per_split: int = 0,
    split_schedule: str = "interleaved",
    seed_agent_result_dir: str | Path | None = None,
    progress: bool = True,
) -> dict[str, Any]:
    if workers < 1:
        raise ValueError("workers must be positive")
    if split_schedule not in {"sequential", "interleaved"}:
        raise ValueError("split_schedule must be sequential or interleaved")

    dataset_path = Path(dataset_dir).resolve()
    result_path = Path(result_dir).resolve()
    samples = load_samples(dataset_path, split, smoke_per_split)
    require_solver_support(load_solver(solver_name, model), "hacking")
    identity = _run_identity(
        samples, dataset_path, split, solver_name, model, smoke_per_split
    )
    _prepare_run(result_path, identity, resume)
    samples_dir = result_path / "samples"
    records = _read_records(samples_dir, {sample.sample_id for sample in samples})
    for sample_id, record in records.items():
        if (
            record.get("status") == "retryable_error"
            and int(record.get("attempt", 0)) >= MAX_ATTEMPTS
        ):
            record = {**record, "status": "api_failed", "score": 0}
            _atomic_json(samples_dir / f"{sample_id}.json", record)
            records[sample_id] = record

    if seed_agent_result_dir:
        seeded = _seed_from_agent_results(
            Path(seed_agent_result_dir).resolve(),
            result_path,
            identity,
            samples,
            records,
        )
        if progress:
            print(f"seeded {seeded} completed first turns", flush=True)

    state = threading.local()

    def run_one(sample: HackSample) -> dict[str, Any]:
        previous = records.get(sample.sample_id, {})
        attempt = int(previous.get("attempt", 0)) + 1
        started_at = _now()
        started = time.monotonic()
        try:
            if not hasattr(state, "solver"):
                state.solver = load_solver(solver_name, model)
            task = HackingInput(
                sample.problem_id,
                sample.problem_statement,
                sample.submission_code,
                submission_language=sample.submission_language,
                metadata=sample.metadata,
            )
            session = state.solver.start_hacking(task)
            turn = session.next()
            record = {
                "schema_version": SCHEMA_VERSION,
                "status": "completed",
                "attempt": attempt,
                "sample": sample.public_record(),
                "round": 1,
                "candidate": turn.candidate.generator if turn.candidate is not None else None,
                "parse_error": turn.error,
                "raw_text": turn.raw_text,
                "message": turn.message,
                "transcript": session.transcript,
                "usage": dict(turn.usage),
                "provenance": "generated",
                "created_at": started_at,
                "finished_at": _now(),
                "duration_seconds": round(time.monotonic() - started, 3),
            }
        except Exception as error:
            record = {
                "schema_version": SCHEMA_VERSION,
                "status": "api_failed" if attempt >= MAX_ATTEMPTS else "retryable_error",
                "attempt": attempt,
                "sample": sample.public_record(),
                "round": 1,
                "score": 0 if attempt >= MAX_ATTEMPTS else None,
                "error": {"type": type(error).__name__, "message": str(error), "at": _now()},
                "created_at": started_at,
                "finished_at": _now(),
                "duration_seconds": round(time.monotonic() - started, 3),
            }
        _atomic_json(samples_dir / f"{sample.sample_id}.json", record)
        return record

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="hack-rollout") as executor:
        for pending in _pending_stages(samples, records, split_schedule):
            position = 0
            futures = {}
            while position < len(pending) or futures:
                while position < len(pending) and len(futures) < workers:
                    sample = pending[position]
                    position += 1
                    futures[executor.submit(run_one, sample)] = sample
                done, _ = wait(futures, return_when=FIRST_COMPLETED)
                for future in done:
                    sample = futures.pop(future)
                    record = future.result()
                    records[sample.sample_id] = record
                    if progress:
                        print(f"[{sample.sample_id}] {record['status']}", flush=True)
                _atomic_json(result_path / "summary.json", _summary(samples, records))

    summary = _summary(samples, records)
    _atomic_json(result_path / "summary.json", summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate durable first-turn UOJ Hacking rollouts without UOJ calls."
    )
    parser.add_argument("--split", choices=("easy", "hard", "all"), default="all")
    parser.add_argument("--solver", dest="solver_name", default="prompt")
    parser.add_argument("--model", required=True)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument(
        "--split-schedule", choices=("sequential", "interleaved"), default="interleaved"
    )
    parser.add_argument("--dataset-dir", type=Path, default=Path("dataset"))
    parser.add_argument("--result-dir", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--smoke-per-split", type=int, default=0, metavar="N")
    parser.add_argument("--seed-agent-result-dir", type=Path)
    args = parser.parse_args(argv)
    summary = run_batch(**vars(args))
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
