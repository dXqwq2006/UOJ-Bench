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
class FaultCoverageInput:
    problem_id: str
    problem_statement: str
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FaultExposureInput:
    problem_id: str
    problem_statement: str
    submission_id: int
    submission_code: str
    submission_language: str
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TestPackageInput:
    problem_id: str
    problem_statement: str
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


class TestCaseFormat(str, Enum):
    RAW_INPUT = "raw_input"
    PYTHON_GENERATOR = "python_generator"


@dataclass(frozen=True)
class TestCaseCandidate:
    content: str
    format: TestCaseFormat = TestCaseFormat.RAW_INPUT


@dataclass(frozen=True)
class TestPackageCandidate:
    tests: tuple[TestCaseCandidate, ...]
    artifact: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SolverCapabilities:
    generation: bool = True
    hacking: bool = True
    repair: bool = True
    generation_feedback: bool = False
    hacking_feedback: bool = True
    repair_feedback: bool = True
    fault_coverage: bool = False
    fault_exposure: bool = False
    fault_coverage_feedback: bool = False
    fault_exposure_feedback: bool = False
    test_package: bool = False
    test_package_feedback: bool = False


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
    @property
    def capabilities(self) -> SolverCapabilities:
        ...

    def start_generation(self, task: GenerationInput) -> SolverSession[SolutionCandidate]:
        ...

    def start_hacking(self, task: HackingInput) -> SolverSession[HackCandidate]:
        ...

    def start_repair(self, task: RepairInput) -> SolverSession[PatchCandidate]:
        ...

    def start_fault_coverage(
        self, task: FaultCoverageInput
    ) -> SolverSession[TestCaseCandidate]:
        ...

    def start_fault_exposure(
        self, task: FaultExposureInput
    ) -> SolverSession[TestCaseCandidate]:
        ...

    def start_test_package(
        self, task: TestPackageInput
    ) -> SolverSession[TestPackageCandidate]:
        ...


def solver_capabilities(solver: Any) -> SolverCapabilities:
    """Return declared capabilities, defaulting old solvers to current behavior."""
    capabilities = getattr(solver, "capabilities", None)
    if capabilities is None:
        return SolverCapabilities()
    if not isinstance(capabilities, SolverCapabilities):
        raise TypeError("solver capabilities must be a SolverCapabilities instance")
    return capabilities


def require_solver_support(solver: Any, task: str, *, feedback: bool = False) -> None:
    if task not in {
        "generation",
        "hacking",
        "repair",
        "fault_coverage",
        "fault_exposure",
        "test_package",
    }:
        raise ValueError(f"unknown solver task: {task}")

    capabilities = solver_capabilities(solver)
    if not getattr(capabilities, task):
        raise ValueError(f"{type(solver).__name__} does not support {task}")
    if feedback and not getattr(capabilities, f"{task}_feedback"):
        raise ValueError(
            f"{type(solver).__name__} supports one-shot {task} only; max_trials must be 1"
        )
