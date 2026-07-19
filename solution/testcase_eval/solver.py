"""Official TestCase-Eval fault-coverage and fault-exposure policy."""

import copy
import json
import os
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
ExtractDetails = Callable[[str], Any]

_ANSWER_PATTERN = re.compile(r"<answer>\s*(.*?)\s*</answer>", re.DOTALL)
_PLAINTEXT_PATTERN = re.compile(r"```plaintext\s*(.*?)```", re.DOTALL)
_GENERIC_FENCE_PATTERN = re.compile(r"```\s*(.*?)```", re.DOTALL)

_EXTRACTOR_PROMPT = """You are given an LLM-generated response to a test-input-generation task.

**Your job:**
Extract the *complete, valid, and effective* test input from the response, even if the response does not strictly follow the expected ` ```plaintext ... ``` ` format.
The test input is usually the actual data or values to be given to the algorithm, not code or explanations.
Sometimes the LLM response may have formatting errors, be missing code blocks, or present the test input in plain text.
Please do your best to identify and extract the correct test input, ignoring code generation code, explanations, or markdown formatting.

**Common formats to look for:**
1. Content within ` ```plaintext ... ``` ` or ` ``` ... ``` ` code blocks
2. Content following **Test Input:** heading until the next heading (like **Explanation:** or **Output:**)
3. Plain text that represents test data/input values

**Requirements:**
- If you cannot find any valid test input in the response, return "None" as the test value.
- The extracted test input should be as complete, valid, and precise as possible, even if the response is imperfectly formatted.
- If there are multiple possible candidates, choose the one that most likely represents the actual input expected by the algorithm problem.
- For **Test Input:** format, extract everything from after the heading until the next markdown heading or explanation section.
- Remove any trailing explanations or comments, keep only the raw input data.

**LLM Response:**
{response}
"""


def _response_output_text(raw: Mapping[str, Any]) -> str:
    if isinstance(raw.get("output_text"), str):
        return raw["output_text"]
    for item in raw.get("output", []):
        if not isinstance(item, Mapping) or item.get("type") != "message":
            continue
        for block in item.get("content", []):
            if isinstance(block, Mapping) and block.get("type") == "output_text":
                return str(block.get("text", ""))
    raise RuntimeError("extractor response has no output_text")


def extract_test_input_llm(raw_text: str) -> tuple[str, Mapping[str, Any], Mapping[str, Any]]:
    """Run the paper's fixed GPT-4.1-mini fallback extractor."""
    import requests

    key = os.environ.get(
        "TESTCASE_EVAL_EXTRACTOR_API_KEY", os.environ.get("TATU_API_KEY", "")
    ).strip()
    if not key:
        raise RuntimeError("TESTCASE_EVAL_EXTRACTOR_API_KEY is required")
    base = os.environ.get(
        "TESTCASE_EVAL_EXTRACTOR_BASE_URL", "https://maas.tatucloud.com/v1"
    ).rstrip("/")
    model = os.environ.get("TESTCASE_EVAL_EXTRACTOR_MODEL", "gpt-4.1-mini")
    payload = {
        "model": model,
        "input": [{"role": "user", "content": _EXTRACTOR_PROMPT.format(response=raw_text)}],
        "max_output_tokens": 1024,
        "text": {
            "format": {
                "type": "json_schema",
                "name": "test_extraction",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {"test": {"type": "string"}},
                    "required": ["test"],
                    "additionalProperties": False,
                },
            }
        },
    }
    response = requests.post(
        f"{base}/responses",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json=payload,
        timeout=float(os.environ.get("TESTCASE_EVAL_EXTRACTOR_TIMEOUT_SECONDS", "900")),
    )
    response.raise_for_status()
    raw = response.json()
    parsed = json.loads(_response_output_text(raw))
    test = parsed.get("test") if isinstance(parsed, Mapping) else None
    if not isinstance(test, str):
        raise RuntimeError("extractor response has no string test")
    message = {
        "model": str(raw.get("model") or model),
        "request_config": {"max_output_tokens": 1024, "structured": True},
        "raw_response": raw,
    }
    return test, message, raw.get("usage", {})


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

    def __init__(
        self,
        model: str,
        call_details: Optional[CallDetails] = None,
        extract_details: Optional[ExtractDetails] = None,
    ):
        self.model = model
        self.call_details = call_details or _default_call_details

        self.extract_details = extract_details or extract_test_input_llm

    def start_generation(self, task: GenerationInput) -> SolverSession[SolutionCandidate]:
        raise NotImplementedError("TestCase-Eval Task 2 does not support solution generation")

    def start_hacking(self, task: HackingInput) -> SolverSession[HackCandidate]:
        return _OneShotSession(
            self.model,
            self.call_details,
            self.extract_details,
            prompts.hacking(task),
            lambda value: HackCandidate(test_input_generator(value)),
        )

    def start_repair(self, task: RepairInput) -> SolverSession[PatchCandidate]:
        raise NotImplementedError("TestCase-Eval Task 2 does not support solution repair")

    def start_fault_coverage(
        self, task: FaultCoverageInput
    ) -> SolverSession[TestCaseCandidate]:
        return _OneShotSession(
            self.model,
            self.call_details,
            self.extract_details,
            prompts.fault_coverage(task),
            TestCaseCandidate,
        )

    def start_fault_exposure(
        self, task: FaultExposureInput
    ) -> SolverSession[TestCaseCandidate]:
        return _OneShotSession(
            self.model,
            self.call_details,
            self.extract_details,
            prompts.fault_exposure(task),
            TestCaseCandidate,
        )


class _OneShotSession:
    def __init__(
        self,
        model: str,
        call_details: CallDetails,
        extract_details: ExtractDetails,
        prompt: str,
        candidate_type,
    ):
        self.model = model
        self.call_details = call_details
        self.prompt = prompt
        self.candidate_type = candidate_type
        self.extract_details = extract_details
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
        error = None
        if test_input is None:
            try:
                test_input, extractor_message, extractor_usage = self.extract_details(raw_text)
                message = {"generation": message, "extractor": extractor_message}
                usage = {"generation": usage, "extractor": extractor_usage}
            except Exception as exc:
                error = f"test extractor failed: {exc}"
        return SolverTurn(
            candidate=self.candidate_type(test_input) if test_input is not None else None,
            raw_text=raw_text,
            message=message,
            usage=usage,
            error=error or (None if test_input is not None else "no test input found"),
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
