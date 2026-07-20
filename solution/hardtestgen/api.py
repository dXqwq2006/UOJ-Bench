"""Data contract for the HardTestGen paper pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence


@dataclass(frozen=True)
class OracleProgram:
    program_id: str
    language: str
    source: str


@dataclass(frozen=True)
class HardTestGenInput:
    problem_id: str
    problem_statement: str
    oracle_programs: Sequence[OracleProgram]
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExecutionResult:
    status: str
    stdout: str = ""
    stderr: str = ""

    @property
    def succeeded(self) -> bool:
        return self.status == "exited"


class ProgramExecutor(Protocol):
    def run_many(
        self,
        language: str,
        source: str,
        inputs: Sequence[str],
        *,
        time_limit_ms: int,
        memory_limit_mb: int,
    ) -> list[ExecutionResult]:
        ...


@dataclass(frozen=True)
class GeneratedInput:
    content: str
    method: str
    generator: str = ""


@dataclass(frozen=True)
class TestCase:
    input: str
    output: str
    method: str
    generator: str = ""


@dataclass(frozen=True)
class KitStage:
    stage: str
    prompt: str
    raw_text: str
    message: Mapping[str, Any]
    usage: Mapping[str, Any]
    parsed: Mapping[str, Any]


@dataclass(frozen=True)
class TestCaseKit:
    input_validator: str
    output_judging_function: str | None
    llm_inputs: tuple[str, ...]
    regular_generator: str | None
    regular_functions: tuple[str, ...]
    hack_generator: str | None
    hack_functions: tuple[str, ...]
    prompts: Mapping[str, str]
    responses: Mapping[str, str]
    messages: Mapping[str, Any]
    usage: Mapping[str, Any]


@dataclass(frozen=True)
class SuiteResult:
    status: str
    test_cases: tuple[TestCase, ...] = ()
    generated_inputs: tuple[GeneratedInput, ...] = ()
    error: str = ""
