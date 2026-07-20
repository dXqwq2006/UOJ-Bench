"""Shared records and generation loop for offline fault-coverage benchmarks."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence

from solution import load_solver
from solution.api import (
    FaultCoverageInput,
    FaultExposureInput,
    TestCaseFormat,
)


@dataclass(frozen=True)
class ProblemSpec:
    key: str
    display_id: str | None
    statement: str
    budget: int
    time_limit_ms: int
    memory_limit_mb: int
    metadata: Mapping[str, Any]


@dataclass(frozen=True)
class ProgramSpec:
    key: str
    problem_key: str
    role: str
    language: str
    source: str
    metadata: Mapping[str, Any]


@dataclass(frozen=True)
class GenerationJob:
    policy: str
    task: int
    problem_id: str
    problem_statement: str
    submission_id: str
    submission_code: str
    submission_language: str
    generation_id: int
    metadata: Mapping[str, Any]


class GenerationStore(Protocol):
    def save_generation(self, record: Mapping[str, Any]) -> None:
        ...


def _generate_one(job: GenerationJob, model: str) -> dict[str, Any]:
    prompt = ""
    try:
        solver = load_solver(job.policy, model)
        if job.task == 1:
            session = solver.start_fault_coverage(
                FaultCoverageInput(
                    job.problem_id,
                    job.problem_statement,
                    job.metadata,
                )
            )
        else:
            submission_id: Any = job.submission_id
            if job.submission_id.isdigit():
                submission_id = int(job.submission_id)
            session = solver.start_fault_exposure(
                FaultExposureInput(
                    job.problem_id,
                    job.problem_statement,
                    submission_id,
                    job.submission_code,
                    job.submission_language,
                    job.metadata,
                )
            )
        prompt = session.initial_request
        turn = session.next()
        candidate = turn.candidate
        return {
            "policy": job.policy,
            "task": job.task,
            "problem_id": job.problem_id,
            "submission_id": job.submission_id,
            "generation_id": job.generation_id,
            "prompt": prompt,
            "raw_text": turn.raw_text,
            "candidate": candidate.content if candidate is not None else "ERROR",
            "candidate_format": (
                candidate.format.value
                if candidate is not None
                else TestCaseFormat.RAW_INPUT.value
            ),
            "message": turn.message,
            "usage": turn.usage,
            "status": "complete",
            "error": turn.error or "",
        }
    except Exception as exc:
        return {
            "policy": job.policy,
            "task": job.task,
            "problem_id": job.problem_id,
            "submission_id": job.submission_id,
            "generation_id": job.generation_id,
            "prompt": prompt,
            "raw_text": "",
            "candidate": "",
            "candidate_format": TestCaseFormat.RAW_INPUT.value,
            "message": {},
            "usage": {},
            "status": "request_error",
            "error": f"{type(exc).__name__}: {exc}",
        }


def run_generation_jobs(
    store: GenerationStore,
    jobs: Sequence[GenerationJob],
    *,
    model: str,
    workers: int,
) -> dict[str, int]:
    if workers < 1:
        raise ValueError("workers must be positive")
    counts = {"scheduled": len(jobs), "complete": 0, "request_error": 0}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_generate_one, job, model) for job in jobs]
        for completed, future in enumerate(as_completed(futures), 1):
            record = future.result()
            store.save_generation(record)
            counts[record["status"]] += 1
            if completed % 10 == 0 or completed == len(futures):
                print(
                    f"generation {completed}/{len(futures)} "
                    f"complete={counts['complete']} errors={counts['request_error']}",
                    flush=True,
                )
    return counts
