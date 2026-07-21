"""Evaluate durable first-turn Hacking rollouts with UOJ and no LLM calls."""

from __future__ import annotations

import argparse
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import hashlib
import json
from pathlib import Path
import re
import threading
from typing import Any, Mapping

from scripts.run_hack_agent_batch import (
    HackSample,
    _atomic_json,
    _dataset_hashes,
    _now,
    _prepare_run,
    load_samples,
)


SCHEMA_VERSION = 1
TERMINAL = {"completed", "invalid_candidate"}
QUOTA_ERROR = "API usage limit exceeded"
PYTHON_BLOCK = re.compile(r"```python\n(.*?)```", re.DOTALL)


def _load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as file:
        value = json.load(file)
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _candidate_hash(candidate: str | None) -> str | None:
    return hashlib.sha256(candidate.encode()).hexdigest() if candidate else None


def _score(result: Mapping[str, Any]) -> int:
    payload = result.get("result")
    return int(isinstance(payload, Mapping) and payload.get("score") == 1)


def _new_client(on_usage):
    from utils.uoj_api import Client

    client = Client()
    request = client._request

    def tracked_request(method: str, url: str, **kwargs):
        response = request(method, url, **kwargs)
        raw = response.headers.get("X-UOJ-API-Usage")
        if raw:
            try:
                on_usage(json.loads(raw))
            except json.JSONDecodeError:
                pass
        return response

    client._request = tracked_request
    return client


def _judge_candidate(client, sample: HackSample, candidate: str) -> dict[str, Any]:
    from utils.uoj_api import SubmissionRequest

    submission = SubmissionRequest(problem_id=sample.problem_id, type="hack")
    submission.addSourceCodeText(
        "answer", sample.submission_code, language=sample.submission_language
    )
    submission.addHackInputText(candidate, language="Python3")
    submission.flagFormatInputFile()
    return client.makeBackgroundSubmission(submission)


def _read_records(
    directory: Path, known_ids: set[str], rollout_records: Mapping[str, Mapping[str, Any]]
) -> dict[str, dict[str, Any]]:
    records = {}
    if not directory.exists():
        return records
    for path in directory.glob("*.json"):
        record = _load_json(path)
        sample_id = record.get("sample", {}).get("sample_id")
        if sample_id not in known_ids or path.stem != sample_id:
            raise ValueError(f"unexpected evaluation result {path}")
        if record.get("status") not in TERMINAL | {"retryable_error"}:
            raise ValueError(f"invalid status in {path}")
        rollout = rollout_records.get(sample_id, {})
        if record.get("candidate_sha256") != _candidate_hash(rollout.get("candidate")):
            raise ValueError(f"rollout candidate changed after evaluation: {sample_id}")
        records[sample_id] = record
    return records


def _seed_imported(
    result_dir: Path,
    rollout_records: Mapping[str, Mapping[str, Any]],
    records: dict[str, dict[str, Any]],
) -> None:
    for sample_id, rollout in rollout_records.items():
        if records.get(sample_id, {}).get("status") in TERMINAL:
            continue
        if rollout.get("provenance") != "imported_agent_result":
            continue
        candidate = rollout.get("candidate")
        if not isinstance(candidate, str):
            continue
        source_dir = Path(str(rollout.get("source_result_dir", "")))
        source_path = source_dir / "samples" / f"{sample_id}.json"
        if not source_path.exists():
            continue
        source = _load_json(source_path)
        messages = source.get("messages")
        results = source.get("judge_results")
        if not isinstance(messages, list) or len(messages) < 2:
            continue
        raw = messages[1].get("content") if isinstance(messages[1], Mapping) else None
        match = PYTHON_BLOCK.search(raw) if isinstance(raw, str) else None
        if not match or match.group(1) != candidate:
            raise ValueError(f"imported first turn changed: {sample_id}")
        if not isinstance(results, list) or not results or not isinstance(results[0], Mapping):
            continue
        record = {
            "schema_version": SCHEMA_VERSION,
            "status": "completed",
            "sample": rollout["sample"],
            "candidate_sha256": _candidate_hash(candidate),
            "score": _score(results[0]),
            "judge_result": results[0],
            "provenance": "imported_agent_evaluation",
            "source_result_dir": str(source_dir),
            "created_at": _now(),
        }
        _atomic_json(result_dir / "samples" / f"{sample_id}.json", record)
        records[sample_id] = record


def _summary(
    samples: list[HackSample],
    rollout_records: Mapping[str, Mapping[str, Any]],
    records: Mapping[str, Mapping[str, Any]],
    api_usage: Mapping[str, Any] | None,
    halt_reason: str | None,
) -> dict[str, Any]:
    def group(group_samples: list[HackSample]) -> dict[str, Any]:
        ids = {sample.sample_id for sample in group_samples}
        available = {
            sample_id
            for sample_id, record in rollout_records.items()
            if sample_id in ids and record.get("status") == "completed"
        }
        selected = [record for sample_id, record in records.items() if sample_id in ids]
        completed = sum(record.get("status") == "completed" for record in selected)
        failed = sum(record.get("status") == "invalid_candidate" for record in selected)
        retryable = sum(record.get("status") == "retryable_error" for record in selected)
        successes = sum(
            record.get("status") == "completed" and record.get("score") == 1
            for record in selected
        )
        return {
            "planned": len(group_samples),
            "available": len(available),
            "completed": completed,
            "failed": failed,
            "retryable_error": retryable,
            "pending_rollout": len(group_samples) - len(available),
            "pending_evaluation": len(available) - completed - failed,
            "successes": successes,
            "pass_at_1": (
                round(successes / (completed + failed), 6)
                if completed + failed
                else None
            ),
        }

    return {
        "schema_version": SCHEMA_VERSION,
        "phase": "evaluation",
        "round": 1,
        "updated_at": _now(),
        "overall": group(samples),
        "splits": {
            split: group([sample for sample in samples if sample.split == split])
            for split in ("easy", "hard")
            if any(sample.split == split for sample in samples)
        },
        "api_usage": dict(api_usage or {}),
        "halt_reason": halt_reason,
    }


def run_batch(
    *,
    dataset_dir: str | Path,
    rollout_dir: str | Path,
    result_dir: str | Path,
    workers: int = 8,
    resume: bool = False,
    progress: bool = True,
) -> dict[str, Any]:
    if workers < 1:
        raise ValueError("workers must be positive")
    dataset_path = Path(dataset_dir).resolve()
    rollout_path = Path(rollout_dir).resolve()
    result_path = Path(result_dir).resolve()
    rollout_manifest = _load_json(rollout_path / "manifest.json")
    run = rollout_manifest.get("run", {})
    samples = load_samples(dataset_path, run.get("split", "all"), run.get("smoke_per_split", 0))
    if run.get("samples") != [sample.public_record() for sample in samples]:
        raise ValueError("rollout samples do not match the dataset")
    if run.get("datasets") != _dataset_hashes(dataset_path, run.get("split", "all")):
        raise ValueError("rollout dataset hashes do not match")
    identity = {
        "phase": "evaluation",
        "round": 1,
        "rollout_run": run,
    }
    _prepare_run(result_path, identity, resume)
    known_ids = {sample.sample_id for sample in samples}
    rollout_records = _read_records(rollout_path / "samples", known_ids, {})
    # Rollout records use the same status vocabulary but have no evaluation fingerprint.
    for record in rollout_records.values():
        record.pop("candidate_sha256", None)
    records = _read_records(result_path / "samples", known_ids, rollout_records)
    _seed_imported(result_path, rollout_records, records)

    previous = _load_json(result_path / "summary.json") if (result_path / "summary.json").exists() else {}
    api_usage: dict[str, Any] = dict(previous.get("api_usage") or {})
    usage_lock = threading.Lock()
    halt = threading.Event()
    halt_reason: list[str | None] = [None]
    state = threading.local()

    def update_usage(value: Mapping[str, Any]) -> None:
        with usage_lock:
            api_usage.clear()
            api_usage.update(value)

    def run_one(sample: HackSample) -> dict[str, Any]:
        rollout = rollout_records[sample.sample_id]
        candidate = rollout.get("candidate")
        previous_record = records.get(sample.sample_id, {})
        attempt = int(previous_record.get("attempt", 0)) + 1
        base = {
            "schema_version": SCHEMA_VERSION,
            "sample": rollout["sample"],
            "candidate_sha256": _candidate_hash(candidate),
            "attempt": attempt,
            "created_at": _now(),
        }
        if not isinstance(candidate, str) or not candidate:
            return {**base, "status": "invalid_candidate", "score": 0}
        try:
            if not hasattr(state, "client"):
                state.client = _new_client(update_usage)
            result = _judge_candidate(state.client, sample, candidate)
            return {
                **base,
                "status": "completed",
                "score": _score(result),
                "judge_result": result,
                "provenance": "uoj_api",
            }
        except Exception as error:
            reason = "quota" if QUOTA_ERROR in str(error) else "infrastructure"
            halt_reason[0] = reason
            halt.set()
            return {
                **base,
                "status": "retryable_error",
                "error": {"type": type(error).__name__, "message": str(error)},
            }

    pending_by_split = {
        split: [
            sample
            for sample in samples
            if sample.split == split
            and rollout_records.get(sample.sample_id, {}).get("status") == "completed"
            and records.get(sample.sample_id, {}).get("status") not in TERMINAL
        ]
        for split in ("easy", "hard")
    }
    pending = []
    for index in range(max((len(group) for group in pending_by_split.values()), default=0)):
        for split in ("easy", "hard"):
            if index < len(pending_by_split[split]):
                pending.append(pending_by_split[split][index])

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="hack-uoj") as executor:
        position = 0
        futures = {}
        while (position < len(pending) and not halt.is_set()) or futures:
            while not halt.is_set() and position < len(pending) and len(futures) < workers:
                sample = pending[position]
                position += 1
                futures[executor.submit(run_one, sample)] = sample
            if not futures:
                break
            done, _ = wait(futures, return_when=FIRST_COMPLETED)
            for future in done:
                sample = futures.pop(future)
                record = future.result()
                _atomic_json(result_path / "samples" / f"{sample.sample_id}.json", record)
                records[sample.sample_id] = record
                if progress:
                    print(f"[{sample.sample_id}] {record['status']} score={record.get('score')}", flush=True)
            _atomic_json(
                result_path / "summary.json",
                _summary(samples, rollout_records, records, api_usage, halt_reason[0]),
            )

    summary = _summary(samples, rollout_records, records, api_usage, halt_reason[0])
    _atomic_json(result_path / "summary.json", summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, default=Path("dataset"))
    parser.add_argument("--rollout-dir", type=Path, required=True)
    parser.add_argument("--result-dir", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)
    summary = run_batch(**vars(args))
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return {"quota": 3, "infrastructure": 2}.get(summary.get("halt_reason"), 0)


if __name__ == "__main__":
    raise SystemExit(main())
