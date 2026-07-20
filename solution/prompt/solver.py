"""The official prompt baseline solver."""

import copy
import re
from typing import Any, Callable, Dict, Generic, List, Mapping, Optional, TypeVar

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
    TestCaseFormat,
)
from . import prompts


CallDetails = Callable[[Any, str], Any]
CandidateT = TypeVar("CandidateT")

def _default_call_details(message: Any, model: str) -> Any:
    from solution.llm.call_llm import call_llm_details

    return call_llm_details(message, model)


class PromptSolver:
    """Send the benchmark prompt to one model and apply its official parser."""

    capabilities = SolverCapabilities(fault_exposure=True)

    def __init__(self, model: str, call_details: Optional[CallDetails] = None):
        self.model = model
        self.call_details = call_details or _default_call_details

    def start_generation(self, task: GenerationInput) -> SolverSession[SolutionCandidate]:
        return _PromptSession(self.model, self.call_details, prompts.generation(task), "generation")

    def start_hacking(self, task: HackingInput) -> SolverSession[HackCandidate]:
        return _PromptSession(self.model, self.call_details, prompts.hacking(task), "hacking")

    def start_repair(self, task: RepairInput) -> SolverSession[PatchCandidate]:
        return _PromptSession(self.model, self.call_details, prompts.repair(task), "repair")

    def start_fault_coverage(
        self, task: FaultCoverageInput
    ) -> SolverSession[TestCaseCandidate]:
        raise NotImplementedError("The UOJ prompt has no problem-level coverage task")

    def start_fault_exposure(
        self, task: FaultExposureInput
    ) -> SolverSession[TestCaseCandidate]:
        hacking = HackingInput(
            problem_id=0,
            problem_statement=task.problem_statement,
            submission_code=task.submission_code,
            submission_language=task.submission_language,
            metadata=task.metadata,
        )
        return _PromptSession(
            self.model, self.call_details, prompts.hacking(hacking), "fault_exposure"
        )


class _PromptSession(Generic[CandidateT]):
    _PARSERS = {
        "generation": (re.compile(r"```cpp\n(.*?)```", re.DOTALL), SolutionCandidate, "no output code"),
        "hacking": (re.compile(r"```python\n(.*?)```", re.DOTALL), HackCandidate, "no output hack data"),
        "repair": (re.compile(r"```patch\n(.*?)```", re.DOTALL), PatchCandidate, "no output patch"),
        "fault_exposure": (
            re.compile(r"```python\n(.*?)```", re.DOTALL),
            lambda content: TestCaseCandidate(content, TestCaseFormat.PYTHON_GENERATOR),
            "no output hack data",
        ),
    }

    def __init__(self, model: str, call_details: CallDetails, prompt: str, task: str):
        self.model = model
        self.call_details = call_details
        self.prompt = prompt
        self.task = task
        self.history: List[Dict[str, Any]] = [{"role": "user", "content": prompt}]
        self.started = False
        self.feedback_pending = False

    def next(self, feedback: Optional[SolverFeedback] = None) -> SolverTurn[CandidateT]:
        if feedback is not None:
            if not self.started:
                raise ValueError("feedback requires a previous solver turn")
            self.record_feedback(feedback)

        request = copy.deepcopy(self.history) if self.started or self.feedback_pending else self.prompt
        raw_text, message, usage = self.call_details(request, self.model)
        self.started = True
        self.feedback_pending = False
        self._append_assistant(raw_text, message)

        pattern, candidate_type, error = self._PARSERS[self.task]
        match = pattern.search(raw_text)
        return SolverTurn(
            candidate=candidate_type(match.group(1)) if match else None,
            raw_text=raw_text,
            message=message,
            usage=usage,
            error=None if match else error,
        )

    def record_feedback(self, feedback: SolverFeedback) -> None:
        self.history.append({"role": "user", "content": self._render_feedback(feedback)})
        self.feedback_pending = True

    @property
    def initial_request(self) -> str:
        return self.prompt

    @property
    def transcript(self) -> List[Dict[str, Any]]:
        return copy.deepcopy(self.history)

    def _append_assistant(self, raw_text: str, message: Any) -> None:
        if isinstance(message, Mapping) and message.get("native_turn"):
            from solution.llm.call_llm import assistant_history_message

            self.history.append(assistant_history_message(raw_text, message))
            return
        reasoning = message.get("reasoning_content", "") if isinstance(message, Mapping) else ""
        if reasoning or self.task == "repair":
            self.history.append({"role": "assistant", "content": "[REASONING]" + reasoning})
            self.history.append({"role": "assistant", "content": "[ANSWER]" + raw_text})
        else:
            self.history.append({"role": "assistant", "content": raw_text})

    def _render_feedback(self, feedback: SolverFeedback) -> str:
        kind = FeedbackKind(feedback.kind)
        if self.task == "hacking":
            if kind is FeedbackKind.INVALID_OUTPUT:
                return "No Python code block found in your response" + prompts.try_again_prompt_hacking
            if kind is FeedbackKind.JUDGE_REJECTED:
                return (
                    "The python code generate invalid input or the code can still pass your test. "
                    f"Here is the results\n{feedback.detail}\n\n" + prompts.try_again_prompt_hacking
                )
            if kind is FeedbackKind.RUNTIME_ERROR:
                return f"Meet error {feedback.detail}" + prompts.try_again_prompt_hacking
        elif self.task == "repair":
            if kind is FeedbackKind.INVALID_OUTPUT:
                return "No patch block found in your response" + prompts.try_again_prompt_repair
            if kind is FeedbackKind.PATCH_ERROR:
                return f"Meet error when applying patch: {feedback.detail}" + prompts.try_again_prompt_repair
            if kind is FeedbackKind.SIMILARITY_REJECTION:
                return "You made too many changes" + prompts.try_again_prompt_repair
            if kind is FeedbackKind.JUDGE_REJECTED:
                return f"The new code cannot pass all tests. Here is the results\n{feedback.detail}\n\n" + prompts.try_again_prompt_repair
            if kind is FeedbackKind.RUNTIME_ERROR:
                return f"Meet error {feedback.detail}" + prompts.try_again_prompt_repair
        raise ValueError(f"{kind.value} feedback is not valid for {self.task}")
