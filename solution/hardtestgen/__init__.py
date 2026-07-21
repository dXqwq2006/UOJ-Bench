"""HardTestGen paper pipeline exposed as a statement-only package solver."""

from __future__ import annotations

from copy import deepcopy
import os
from typing import Any

from solution.api import (
    SolverCapabilities,
    SolverFeedback,
    SolverTurn,
    TestCaseCandidate,
    TestPackageCandidate,
    TestPackageInput,
)

from .api import HardTestGenInput, SuiteResult, TestCaseKit
from .lightcp import HardTestGenLightCP
from .pipeline import HardTestGenPipeline


UPSTREAM_COMMIT = "03553153e9fcd2b94eefe600c20e42ef8c4dcef5"
CAPABILITIES = SolverCapabilities(
    generation=False,
    hacking=False,
    repair=False,
    fault_coverage=False,
    fault_exposure=False,
    test_package=True,
)


class _HardTestGenSession:
    def __init__(
        self,
        pipeline: HardTestGenPipeline,
        task: TestPackageInput,
    ):
        self.pipeline = pipeline
        self.task = task
        self._started = False
        self._transcript: list[dict[str, Any]] = []

    @property
    def initial_request(self) -> dict[str, str]:
        return {
            "problem_id": self.task.problem_id,
            "problem_statement": self.task.problem_statement,
        }

    @property
    def transcript(self) -> list[dict[str, Any]]:
        return deepcopy(self._transcript)

    def record_feedback(self, feedback: SolverFeedback) -> None:
        raise ValueError("HardTestGen package sessions are one-shot")

    def next(self, feedback: SolverFeedback | None = None) -> SolverTurn[Any]:
        if feedback is not None:
            raise ValueError("HardTestGen package sessions do not accept feedback")
        if self._started:
            raise ValueError("HardTestGen package session already produced its turn")
        self._started = True

        kit = self.pipeline.generate_kit(
            HardTestGenInput(self.task.problem_id, self.task.problem_statement)
        )
        benchmark = str(
            self.task.metadata.get(
                "benchmark", os.environ.get("HARDTESTGEN_BENCHMARK", "testcase-eval")
            )
        )
        executor = HardTestGenLightCP(
            os.environ.get("HARDTESTGEN_LIGHTCP_URL", "http://127.0.0.1:8082"),
            benchmark,
        )
        result = self.pipeline.generate_suite(
            HardTestGenInput(self.task.problem_id, self.task.problem_statement),
            kit,
            executor,
        )
        self._transcript = [
            {"stage": stage, "prompt": kit.prompts.get(stage, "")}
            for stage in ("iv_and_ojf", "input_generation")
        ]
        raw_text = "\n\n".join(
            kit.responses.get(stage, "")
            for stage in ("iv_and_ojf", "input_generation")
        )
        if result.status != "complete":
            return SolverTurn(
                candidate=None,
                raw_text=raw_text,
                message={"pipeline": "hardtestgen", "suite_status": result.status},
                usage=kit.usage,
                error=result.error or result.status,
            )
        candidate = TestPackageCandidate(
            tuple(TestCaseCandidate(test.input) for test in result.test_cases),
            {
                "suite_status": result.status,
                "methods": [test.method for test in result.test_cases],
                "generators": [test.generator for test in result.test_cases],
            },
        )
        return SolverTurn(
            candidate=candidate,
            raw_text=raw_text,
            message={"pipeline": "hardtestgen", "suite_status": result.status},
            usage=kit.usage,
        )


class HardTestGenSolver:
    capabilities = CAPABILITIES

    def __init__(self, model: str):
        self.pipeline = HardTestGenPipeline(model)

    def start_test_package(self, task: TestPackageInput) -> _HardTestGenSession:
        return _HardTestGenSession(self.pipeline, task)

    def start_fault_coverage(self, task: Any) -> Any:
        raise NotImplementedError("HardTestGen emits an ordered package")


def build_solver(model: str) -> HardTestGenSolver:
    return HardTestGenSolver(model)


__all__ = [
    "CAPABILITIES",
    "HardTestGenInput",
    "HardTestGenPipeline",
    "SuiteResult",
    "TestCaseKit",
    "UPSTREAM_COMMIT",
    "build_solver",
]
