"""Run the official hacking agent benchmark with durable per-sample results."""

from __future__ import annotations

import argparse
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from typing import Any, Iterable, Mapping

from solution import load_solver
from utils.benchmark import solver_metadata


SCHEMA_VERSION = 1
DEFAULT_PRICING = {
    "gpt-5.6-sol": (5.0, 30.0),
    "gpt-oss-120b": (0.0, 0.0),
}


def _test_hack_agent(*args: Any, **kwargs: Any) -> Any:
    from scripts.test_hack_agent import TestHackAgent

    return TestHackAgent(*args, **kwargs)


@dataclass(frozen=True)
class HackSample:
    sample_id: str
    split: str
    source_index: int
    problem_id: str
    problem_statement: str
    submission_code: str
    submission_language: str
    difficulty: str
    metadata: Mapping[str, Any]

    def public_record(self) -> dict[str, Any]:
        return {
            "sample_id": self.sample_id,
            "split": self.split,
            "source_index": self.source_index,
            "problem_id": self.problem_id,
            "submission_language": self.submission_language,
            "difficulty": self.difficulty,
            "metadata": dict(self.metadata),
        }


class _TrialCountingSession:
    def __init__(self, session: Any):
        self.session = session
        self.counted_trials = 0

    @property
    def initial_request(self) -> Any:
        return self.session.initial_request

    @property
    def transcript(self) -> Any:
        return self.session.transcript

    def next(self, feedback: Any = None) -> Any:
        return self.session.next(feedback)

    def record_feedback(self, feedback: Any) -> None:
        self.session.record_feedback(feedback)
        self.counted_trials += 1


class _TrialCountingSolver:
    def __init__(self, solver: Any):
        self.solver = solver
        self.session: _TrialCountingSession | None = None

    def start_hacking(self, task: Any) -> _TrialCountingSession:
        self.session = _TrialCountingSession(self.solver.start_hacking(task))
        return self.session

    @property
    def counted_trials(self) -> int:
        return self.session.counted_trials if self.session is not None else 0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_list(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as file:
        value = json.load(file)
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError(f"{path} must contain a JSON array of objects")
    return value


def _sample(split: str, index: int, record: Mapping[str, Any], problem: Mapping[str, Any]) -> HackSample:
    problem_id = str(record.get("problem_id", ""))
    statement = problem.get("statement_en")
    code = record.get("wrong_code")
    language = record.get("language", "C++20")
    if not problem_id or not isinstance(statement, str) or not isinstance(code, str) or not isinstance(language, str):
        raise ValueError(f"invalid {split} sample at index {index}")

    # solver_metadata is the single fail-closed boundary for solver-visible metadata.
    metadata = solver_metadata({**problem, **record})
    return HackSample(
        sample_id=f"{split}-{index:04d}",
        split=split,
        source_index=index,
        problem_id=problem_id,
        problem_statement=statement,
        submission_code=code,
        submission_language=language,
        difficulty=str(problem.get("difficulty") or "unknown"),
        metadata=metadata,
    )


def _smoke_samples(samples: list[HackSample], count: int) -> list[HackSample]:
    if count <= 0 or count >= len(samples):
        return samples

    chosen: list[HackSample] = []
    remaining = list(samples)
    languages: set[str] = set()
    problems: set[str] = set()
    predicates = (
        lambda item: item.submission_language not in languages and item.problem_id not in problems,
        lambda item: item.submission_language not in languages,
        lambda item: item.problem_id not in problems,
        lambda item: True,
    )
    for predicate in predicates:
        while len(chosen) < count:
            try:
                position = next(i for i, item in enumerate(remaining) if predicate(item))
            except StopIteration:
                break
            item = remaining.pop(position)
            chosen.append(item)
            languages.add(item.submission_language)
            problems.add(item.problem_id)
    return chosen


def load_samples(dataset_dir: str | Path, split: str = "all", smoke_per_split: int = 0) -> list[HackSample]:
    """Load the paper's 479 Easy and/or 1046 Hard hacking samples."""
    if split not in {"easy", "hard", "all"}:
        raise ValueError("split must be easy, hard, or all")
    if smoke_per_split < 0:
        raise ValueError("smoke_per_split must be non-negative")

    root = Path(dataset_dir)
    problems = _json_list(root / "problems.json")
    problems_by_id = {str(problem.get("problem_id")): problem for problem in problems}
    loaded: list[HackSample] = []

    if split in {"easy", "all"}:
        easy = []
        for index, record in enumerate(_json_list(root / "sampled_large_submission_pairs.json")):
            problem_id = str(record.get("problem_id", ""))
            problem = problems_by_id.get(problem_id)
            if problem is None:
                raise ValueError(f"missing problem {problem_id!r} for Easy sample {index}")
            if problem.get("hackable"):
                easy.append(_sample("easy", index, record, problem))
        loaded.extend(_smoke_samples(easy, smoke_per_split))

    if split in {"hard", "all"}:
        hard = []
        for index, record in enumerate(_json_list(root / "hacks.json")):
            problem_id = str(record.get("problem_id", ""))
            problem = problems_by_id.get(problem_id)
            if problem is None:
                raise ValueError(f"missing problem {problem_id!r} for Hard sample {index}")
            hard.append(_sample("hard", index, record, problem))
        loaded.extend(_smoke_samples(hard, smoke_per_split))
    return loaded


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            json.dump(value, file, ensure_ascii=False, indent=2, sort_keys=True, default=str)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _dataset_hashes(dataset_dir: Path, split: str) -> dict[str, str]:
    names = ["problems.json"]
    if split in {"easy", "all"}:
        names.append("sampled_large_submission_pairs.json")
    if split in {"hard", "all"}:
        names.append("hacks.json")
    return {name: _sha256(dataset_dir / name) for name in names}


def _run_identity(
    samples: Iterable[HackSample], dataset_dir: Path, split: str, solver: str, model: str,
    max_trials: int, smoke_per_split: int,
) -> dict[str, Any]:
    return {
        "split": split,
        "solver": solver,
        "model": model,
        "max_trials": max_trials,
        "smoke_per_split": smoke_per_split,
        "datasets": _dataset_hashes(dataset_dir, split),
        "samples": [sample.public_record() for sample in samples],
    }


def _prepare_run(result_dir: Path, identity: Mapping[str, Any], resume: bool) -> None:
    manifest_path = result_dir / "manifest.json"
    if manifest_path.exists():
        if not resume:
            raise FileExistsError(f"{manifest_path} already exists; pass --resume to continue")
        with manifest_path.open(encoding="utf-8") as file:
            manifest = json.load(file)
        if manifest.get("schema_version") != SCHEMA_VERSION or manifest.get("run") != identity:
            raise ValueError("existing manifest does not match this run")
        return

    result_dir.mkdir(parents=True, exist_ok=True)
    if any(result_dir.iterdir()):
        raise ValueError(f"{result_dir} is non-empty but has no manifest")
    _atomic_json(
        manifest_path,
        {"schema_version": SCHEMA_VERSION, "created_at": _now(), "run": dict(identity)},
    )


def _number(value: Any) -> int:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


def aggregate_usage(usages: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    totals = {"input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0, "total_tokens": 0}
    for usage in usages:
        if not isinstance(usage, Mapping):
            continue
        input_tokens = _number(usage.get("prompt_tokens", usage.get("input_tokens")))
        output_tokens = _number(usage.get("completion_tokens", usage.get("output_tokens")))
        details = usage.get("completion_tokens_details")
        reasoning_tokens = _number(usage.get("reasoning_tokens"))
        if not reasoning_tokens and isinstance(details, Mapping):
            reasoning_tokens = _number(details.get("reasoning_tokens"))
        total_tokens = _number(usage.get("total_tokens")) or input_tokens + output_tokens
        totals["input_tokens"] += input_tokens
        totals["output_tokens"] += output_tokens
        totals["reasoning_tokens"] += reasoning_tokens
        totals["total_tokens"] += total_tokens
    return totals


def _cost(usage: Mapping[str, int], input_price: float, output_price: float) -> float:
    return round(
        (usage["input_tokens"] * input_price + usage["output_tokens"] * output_price) / 1_000_000,
        6,
    )


def _metrics(samples: list[HackSample], records: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    present = [records[sample.sample_id] for sample in samples if sample.sample_id in records]
    completed = [record for record in present if record.get("status") == "completed"]
    retryable = sum(record.get("status") == "retryable_error" for record in present)
    successes = sum(record.get("score") == 1 for record in completed)
    denominator = len(completed)
    pass_at = {}
    for trial in range(1, 11):
        count = sum(
            record.get("score") == 1 and _number(record.get("success_round")) <= trial
            for record in completed
        )
        pass_at[str(trial)] = {
            "count": count,
            "denominator": denominator,
            "rate": round(count / denominator, 6) if denominator else None,
        }
    return {
        "planned": len(samples),
        "completed": denominator,
        "pending": len(samples) - len(present),
        "retryable_error": retryable,
        "successes": successes,
        "success_rate": round(successes / denominator, 6) if denominator else None,
        "pass_at": pass_at,
    }


def summarize(
    samples: list[HackSample], records: Mapping[str, Mapping[str, Any]], input_price: float = 0,
    output_price: float = 0, budget_usd: float | None = None, stop_at_usd: float | None = None,
    budget_stopped: bool = False,
) -> dict[str, Any]:
    """Build Pass@1..10, split, difficulty, usage, and budget summaries."""
    completed = [record for record in records.values() if record.get("status") == "completed"]
    usage = aggregate_usage(record.get("usage", {}) for record in completed)
    actual_cost = _cost(usage, input_price, output_price)
    projected_cost = None
    if completed:
        projected_cost = round(actual_cost * len(samples) / len(completed), 6)

    splits = {}
    for split in ("easy", "hard"):
        split_samples = [sample for sample in samples if sample.split == split]
        if not split_samples:
            continue
        difficulties = {}
        order = ["easy", "medium", "hard", "ultrahard", "unknown"]
        known = {sample.difficulty for sample in split_samples}
        for difficulty in order + sorted(known - set(order)):
            group = [sample for sample in split_samples if sample.difficulty == difficulty]
            if group:
                difficulties[difficulty] = _metrics(group, records)
        splits[split] = {
            **_metrics(split_samples, records),
            "by_problem_difficulty": difficulties,
        }

    all_difficulties = {}
    for difficulty in sorted({sample.difficulty for sample in samples}):
        all_difficulties[difficulty] = _metrics(
            [sample for sample in samples if sample.difficulty == difficulty], records
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "updated_at": _now(),
        "overall": _metrics(samples, records),
        "splits": splits,
        "by_problem_difficulty": all_difficulties,
        "usage": usage,
        "budget": {
            "input_price_per_million": input_price,
            "output_price_per_million": output_price,
            "actual_cost_usd": actual_cost,
            "projected_total_cost_usd": projected_cost,
            "limit_usd": budget_usd,
            "stop_at_usd": stop_at_usd,
            "stopped": budget_stopped,
        },
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
            raise ValueError(f"unexpected sample result {path}")
        if record.get("status") not in {"completed", "retryable_error"}:
            raise ValueError(f"invalid status in {path}")
        records[sample_id] = record
    return records


def _pricing(model: str, input_price: float | None, output_price: float | None) -> tuple[float, float]:
    if (input_price is None) != (output_price is None):
        raise ValueError("input and output prices must be provided together")
    if input_price is not None and output_price is not None:
        if input_price < 0 or output_price < 0:
            raise ValueError("prices must be non-negative")
        return input_price, output_price
    return DEFAULT_PRICING.get(model, (0.0, 0.0))


def _pending_stages(
    samples: list[HackSample], records: Mapping[str, Mapping[str, Any]], split_schedule: str,
) -> list[list[HackSample]]:
    pending = {
        split: [
            sample for sample in samples
            if sample.split == split and records.get(sample.sample_id, {}).get("status") != "completed"
        ]
        for split in ("easy", "hard")
    }
    if split_schedule == "sequential":
        return [pending[split] for split in ("easy", "hard") if pending[split]]
    if split_schedule != "interleaved":
        raise ValueError("split_schedule must be sequential or interleaved")

    interleaved = []
    for index in range(max((len(group) for group in pending.values()), default=0)):
        for split in ("easy", "hard"):
            if index < len(pending[split]):
                interleaved.append(pending[split][index])
    return [interleaved] if interleaved else []


def run_batch(
    *, dataset_dir: str | Path, result_dir: str | Path, split: str, solver_name: str, model: str,
    max_trials: int = 10, workers: int = 8, resume: bool = False, smoke_per_split: int = 0,
    input_price: float | None = None, output_price: float | None = None,
    budget_usd: float | None = None, stop_at_usd: float | None = None,
    split_schedule: str = "sequential", progress: bool = True,
) -> dict[str, Any]:
    if not 1 <= max_trials <= 10:
        raise ValueError("max_trials must be between 1 and 10")
    if workers < 1:
        raise ValueError("workers must be positive")
    if split_schedule not in {"sequential", "interleaved"}:
        raise ValueError("split_schedule must be sequential or interleaved")
    if budget_usd is not None and budget_usd <= 0:
        raise ValueError("budget_usd must be positive")
    if stop_at_usd is None:
        stop_at_usd = budget_usd
    if stop_at_usd is not None and stop_at_usd <= 0:
        raise ValueError("stop_at_usd must be positive")
    if budget_usd is not None and stop_at_usd is not None and stop_at_usd > budget_usd:
        raise ValueError("stop_at_usd cannot exceed budget_usd")

    input_price, output_price = _pricing(model, input_price, output_price)
    if stop_at_usd is not None and not (input_price or output_price):
        raise ValueError("a budget requires non-zero token prices")

    dataset_path = Path(dataset_dir).resolve()
    result_path = Path(result_dir).resolve()
    samples = load_samples(dataset_path, split, smoke_per_split)
    identity = _run_identity(
        samples, dataset_path, split, solver_name, model, max_trials, smoke_per_split
    )
    _prepare_run(result_path, identity, resume)
    samples_dir = result_path / "samples"
    records = _read_records(samples_dir, {sample.sample_id for sample in samples})
    state = threading.local()

    def run_one(sample: HackSample) -> dict[str, Any]:
        previous = records.get(sample.sample_id, {})
        retry_errors = list(previous.get("retry_errors", []))
        if previous.get("status") == "retryable_error" and previous.get("error"):
            retry_errors.append(previous["error"])
        attempt = _number(previous.get("attempt")) + 1
        started_at = _now()
        started = time.monotonic()
        try:
            if not hasattr(state, "solver"):
                state.solver = load_solver(solver_name, model)
            counting_solver = _TrialCountingSolver(state.solver)
            score, transcript, judge_results, messages, usages = _test_hack_agent(
                counting_solver,
                sample.problem_id,
                sample.problem_statement,
                sample.submission_code,
                sample.submission_language,
                max_trials=max_trials,
                metadata=sample.metadata,
            )
            usage = aggregate_usage(usages)
            record = {
                "schema_version": SCHEMA_VERSION,
                "status": "completed",
                "attempt": attempt,
                "sample": sample.public_record(),
                "score": score,
                "success_round": counting_solver.counted_trials + 1 if score == 1 else None,
                "counted_trials": counting_solver.counted_trials,
                "model_turns": len(usages),
                "judge_attempts": len(judge_results),
                "transcript": transcript,
                "judge_results": judge_results,
                "messages": messages,
                "usages": usages,
                "usage": usage,
                "estimated_cost_usd": _cost(usage, input_price, output_price),
                "retry_errors": retry_errors,
                "started_at": started_at,
                "finished_at": _now(),
                "duration_seconds": round(time.monotonic() - started, 3),
            }
        except Exception as error:
            record = {
                "schema_version": SCHEMA_VERSION,
                "status": "retryable_error",
                "attempt": attempt,
                "sample": sample.public_record(),
                "error": {"type": type(error).__name__, "message": str(error), "at": _now()},
                "retry_errors": retry_errors,
                "started_at": started_at,
                "finished_at": _now(),
                "duration_seconds": round(time.monotonic() - started, 3),
            }
        _atomic_json(samples_dir / f"{sample.sample_id}.json", record)
        return record

    budget_stopped = False
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="hack-agent") as executor:
        for pending in _pending_stages(samples, records, split_schedule):
            position = 0
            futures = {}
            while position < len(pending) or futures:
                current = summarize(
                    samples, records, input_price, output_price, budget_usd, stop_at_usd, budget_stopped
                )
                actual_cost = current["budget"]["actual_cost_usd"]
                can_dispatch = stop_at_usd is None or actual_cost < stop_at_usd
                while can_dispatch and position < len(pending) and len(futures) < workers:
                    sample = pending[position]
                    position += 1
                    futures[executor.submit(run_one, sample)] = sample
                if not futures:
                    if position < len(pending):
                        budget_stopped = True
                    break

                done, _ = wait(futures, return_when=FIRST_COMPLETED)
                for future in done:
                    sample = futures.pop(future)
                    record = future.result()
                    records[sample.sample_id] = record
                    if progress:
                        detail = f"score={record.get('score')}" if record["status"] == "completed" else record["status"]
                        print(f"[{sample.sample_id}] {detail}", flush=True)
                summary = summarize(
                    samples, records, input_price, output_price, budget_usd, stop_at_usd, budget_stopped
                )
                _atomic_json(result_path / "summary.json", summary)
            if budget_stopped:
                break

    summary = summarize(
        samples, records, input_price, output_price, budget_usd, stop_at_usd, budget_stopped
    )
    _atomic_json(result_path / "summary.json", summary)
    return summary


def _positive(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=("easy", "hard", "all"), default="all")
    parser.add_argument("--solver", default="prompt", dest="solver_name")
    parser.add_argument("--model", default="gpt-oss-120b")
    parser.add_argument("--max-trials", type=_positive, default=10)
    parser.add_argument("--workers", type=_positive, default=8)
    parser.add_argument(
        "--split-schedule", choices=("sequential", "interleaved"), default="sequential",
        help="run Easy then Hard, or keep both splits active concurrently",
    )
    parser.add_argument("--dataset-dir", type=Path, default=Path("dataset"))
    parser.add_argument("--result-dir", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--smoke-per-split", type=int, default=0, metavar="N")
    parser.add_argument("--input-price-per-million", type=float, dest="input_price")
    parser.add_argument("--output-price-per-million", type=float, dest="output_price")
    parser.add_argument("--budget-usd", type=float)
    parser.add_argument("--stop-at-usd", type=float)
    args = parser.parse_args(argv)
    try:
        summary = run_batch(**vars(args))
    except (FileExistsError, OSError, ValueError) as error:
        parser.error(str(error))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
