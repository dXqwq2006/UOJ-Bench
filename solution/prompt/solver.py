"""The official prompt baseline solver."""

import copy
import re
from typing import Any, Callable, Dict, Generic, List, Mapping, Optional, TypeVar

from solution.api import (
    FeedbackKind,
    GenerationInput,
    HackCandidate,
    HackingInput,
    PatchCandidate,
    RepairInput,
    SolutionCandidate,
    SolverFeedback,
    SolverSession,
    SolverTurn,
)


CallDetails = Callable[[Any, str], Any]
CandidateT = TypeVar("CandidateT")

_HACK_RETRY = "\nTry again! Output a new python code which would generate the correct hack data."
_REPAIR_RETRY = "\nTry again! Output a new patch which would be directly applied to the code given for the first time."


def _default_call_details(message: Any, model: str) -> Any:
    from .call_llm import call_llm_details

    return call_llm_details(message, model)


class PromptSolver:
    """Send the benchmark prompt to one model and apply its official parser."""

    def __init__(self, model: str, call_details: Optional[CallDetails] = None):
        self.model = model
        self.call_details = call_details or _default_call_details

    def start_generation(self, task: GenerationInput) -> SolverSession[SolutionCandidate]:
        return _PromptSession(self.model, self.call_details, task.official_prompt, "generation")

    def start_hacking(self, task: HackingInput) -> SolverSession[HackCandidate]:
        return _PromptSession(self.model, self.call_details, task.official_prompt, "hacking")

    def start_repair(self, task: RepairInput) -> SolverSession[PatchCandidate]:
        return _PromptSession(self.model, self.call_details, task.official_prompt, "repair")


class _PromptSession(Generic[CandidateT]):
    _PARSERS = {
        "generation": (re.compile(r"```cpp\n(.*?)```", re.DOTALL), SolutionCandidate, "no output code"),
        "hacking": (re.compile(r"```python\n(.*?)```", re.DOTALL), HackCandidate, "no output hack data"),
        "repair": (re.compile(r"```patch\n(.*?)```", re.DOTALL), PatchCandidate, "no output patch"),
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
    def transcript(self) -> List[Dict[str, Any]]:
        return copy.deepcopy(self.history)

    def _append_assistant(self, raw_text: str, message: Any) -> None:
        if isinstance(message, Mapping) and message.get("native_turn"):
            from .call_llm import assistant_history_message

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
                return "No Python code block found in your response" + _HACK_RETRY
            if kind is FeedbackKind.JUDGE_REJECTED:
                return (
                    "The python code generate invalid input or the code can still pass your test. "
                    f"Here is the results\n{feedback.detail}\n\n" + _HACK_RETRY
                )
            if kind is FeedbackKind.RUNTIME_ERROR:
                return f"Meet error {feedback.detail}" + _HACK_RETRY
        elif self.task == "repair":
            if kind is FeedbackKind.INVALID_OUTPUT:
                return "No patch block found in your response" + _REPAIR_RETRY
            if kind is FeedbackKind.PATCH_ERROR:
                return f"Meet error when applying patch: {feedback.detail}" + _REPAIR_RETRY
            if kind is FeedbackKind.SIMILARITY_REJECTION:
                return "You made too many changes" + _REPAIR_RETRY
            if kind is FeedbackKind.JUDGE_REJECTED:
                return f"The new code cannot pass all tests. Here is the results\n{feedback.detail}\n\n" + _REPAIR_RETRY
            if kind is FeedbackKind.RUNTIME_ERROR:
                return f"Meet error {feedback.detail}" + _REPAIR_RETRY
        raise ValueError(f"{kind.value} feedback is not valid for {self.task}")
