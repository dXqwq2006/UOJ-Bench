"""Official TestCase-Eval fault-coverage and fault-exposure policy."""

import copy
import re
from typing import Any, Callable, List, Mapping, Optional

from solution.api import (
    FeedbackKind,
    FaultCoverageInput,
    FaultExposureInput,
    GenerationInput,
    HackCandidate,
    HackingInput,
    PatchCandidate,
    RepairInput,
    SolutionCandidate,
    SolverCapabilities,
    SolverFeedback,
    SolverSession,
    SolverTurn,
    TestCaseCandidate,
)
from solution.llm.call_llm import assistant_history_message

from . import prompts


CallDetails = Callable[[Any, str], Any]

_ANSWER_PATTERN = re.compile(r"<answer>\s*(.*?)\s*</answer>", re.DOTALL)
_PLAINTEXT_PATTERN = re.compile(r"```plaintext\s*(.*?)```", re.DOTALL)
_GENERIC_FENCE_PATTERN = re.compile(r"```\s*(.*?)```", re.DOTALL)


def _default_call_details(message: Any, model: str) -> Any:
    from solution.llm.call_llm import call_llm_details

    return call_llm_details(message, model)


def _extract_fenced(content: str) -> Optional[str]:
    for pattern in (_PLAINTEXT_PATTERN, _GENERIC_FENCE_PATTERN):
        match = pattern.search(content)
        if match:
            test_input = match.group(1).strip()
            return test_input or None
    return None


def extract_test_input(raw_text: str) -> Optional[str]:
    """Apply the deterministic extraction stage from TestCase-Eval."""
    answer = _ANSWER_PATTERN.search(raw_text)
    if answer:
        extracted = _extract_fenced(answer.group(1))
        if extracted is not None:
            return extracted
    return _extract_fenced(raw_text)


def test_input_generator(test_input: str) -> str:
    """Wrap raw input without asking the model to synthesize Python."""
    return "import sys\nsys.stdout.write(" + repr(test_input) + ")\n"


class TestCaseEvalSolver:
    """Generate raw test inputs with the official one-shot CoT prompts."""

    capabilities = SolverCapabilities(
        generation=False,
        hacking=True,
        repair=False,
        fault_coverage=True,
        fault_exposure=True,
        generation_feedback=False,
        hacking_feedback=False,
        repair_feedback=False,
    )

    def __init__(self, model: str, call_details: Optional[CallDetails] = None):
        self.model = model
        self.call_details = call_details or _default_call_details

    def start_generation(self, task: GenerationInput) -> SolverSession[SolutionCandidate]:
        raise NotImplementedError("TestCase-Eval Task 2 does not support solution generation")

    def start_hacking(self, task: HackingInput) -> SolverSession[HackCandidate]:
        return _OneShotSession(
            self.model,
            self.call_details,
            prompts.hacking(task),
            lambda value: HackCandidate(test_input_generator(value)),
        )

    def start_repair(self, task: RepairInput) -> SolverSession[PatchCandidate]:
        raise NotImplementedError("TestCase-Eval Task 2 does not support solution repair")

    def start_fault_coverage(
        self, task: FaultCoverageInput
    ) -> SolverSession[TestCaseCandidate]:
        return _OneShotSession(
            self.model, self.call_details, prompts.fault_coverage(task), TestCaseCandidate
        )

    def start_fault_exposure(
        self, task: FaultExposureInput
    ) -> SolverSession[TestCaseCandidate]:
        return _OneShotSession(
            self.model, self.call_details, prompts.fault_exposure(task), TestCaseCandidate
        )


class _OneShotSession:
    def __init__(
        self, model: str, call_details: CallDetails, prompt: str, candidate_type
    ):
        self.model = model
        self.call_details = call_details
        self.prompt = prompt
        self.candidate_type = candidate_type
        self.history: List[dict[str, Any]] = [{"role": "user", "content": prompt}]
        self.started = False

    def next(self, feedback: Optional[SolverFeedback] = None) -> SolverTurn[Any]:
        if feedback is not None:
            self.record_feedback(feedback)
        if self.started:
            raise RuntimeError("TestCase-Eval Task 2 sessions are one-shot")

        raw_text, message, usage = self.call_details(self.prompt, self.model)
        self.started = True
        self._append_assistant(raw_text, message)
        test_input = extract_test_input(raw_text)
        return SolverTurn(
            candidate=self.candidate_type(test_input) if test_input else None,
            raw_text=raw_text,
            message=message,
            usage=usage,
            error=None if test_input else "no test input found",
        )

    def record_feedback(self, feedback: SolverFeedback) -> None:
        if not self.started:
            raise ValueError("feedback requires a previous solver turn")
        kind = FeedbackKind(feedback.kind).value
        detail = "" if feedback.detail is None else f": {feedback.detail}"
        self.history.append(
            {"role": "user", "content": f"Benchmark feedback ({kind}){detail}"}
        )

    @property
    def initial_request(self) -> str:
        return self.prompt

    @property
    def transcript(self) -> List[dict[str, Any]]:
        return copy.deepcopy(self.history)

    def _append_assistant(self, raw_text: str, message: Any) -> None:
        if isinstance(message, Mapping) and message.get("native_turn"):
            self.history.append(assistant_history_message(raw_text, message))
            return
        reasoning = message.get("reasoning_content", "") if isinstance(message, Mapping) else ""
        if reasoning:
            self.history.append({"role": "assistant", "content": "[REASONING]" + reasoning})
            self.history.append({"role": "assistant", "content": "[ANSWER]" + raw_text})
        else:
            self.history.append({"role": "assistant", "content": raw_text})
