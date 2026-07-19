"""Stable contract between benchmark tasks and solver pipelines."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Generic, List, Mapping, Optional, Protocol, TypeVar


@dataclass(frozen=True)
class GenerationInput:
    problem_id: int
    problem_statement: str
    language: str = "C++20"
    chinese: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class HackingInput:
    problem_id: int
    problem_statement: str
    submission_code: str
    submission_language: str = "C++20"
    chinese: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RepairInput:
    problem_id: int
    problem_statement: str
    submission_code: str
    submission_language: str = "C++20"
    chinese: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SolutionCandidate:
    source: str


@dataclass(frozen=True)
class HackCandidate:
    generator: str


@dataclass(frozen=True)
class PatchCandidate:
    patch: str


class FeedbackKind(str, Enum):
    INVALID_OUTPUT = "invalid_output"
    PATCH_ERROR = "patch_error"
    SIMILARITY_REJECTION = "similarity_rejection"
    JUDGE_REJECTED = "judge_rejected"
    RUNTIME_ERROR = "runtime_error"


@dataclass(frozen=True)
class SolverFeedback:
    kind: FeedbackKind
    detail: Any = None


CandidateT = TypeVar("CandidateT")


@dataclass(frozen=True)
class SolverTurn(Generic[CandidateT]):
    candidate: Optional[CandidateT]
    raw_text: str
    message: Any
    usage: Mapping[str, Any]
    error: Optional[str] = None


class SolverSession(Protocol, Generic[CandidateT]):
    @property
    def initial_request(self) -> Any:
        ...

    @property
    def transcript(self) -> List[Dict[str, Any]]:
        ...

    def next(self, feedback: Optional[SolverFeedback] = None) -> SolverTurn[CandidateT]:
        ...

    def record_feedback(self, feedback: SolverFeedback) -> None:
        ...


class Solver(Protocol):
    def start_generation(self, task: GenerationInput) -> SolverSession[SolutionCandidate]:
        ...

    def start_hacking(self, task: HackingInput) -> SolverSession[HackCandidate]:
        ...

    def start_repair(self, task: RepairInput) -> SolverSession[PatchCandidate]:
        ...
