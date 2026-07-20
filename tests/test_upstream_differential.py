import copy
import os
from pathlib import Path
import subprocess
import sys
import types
import unittest
from contextlib import ExitStack
from unittest.mock import patch


os.environ.setdefault("UOJ_API_KEY", "offline")

from scripts import test_debug, test_debug_agent, test_hack, test_hack_agent, test_problem
from solution.prompt import PromptSolver


ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = "ce1c006d9f6cf57670d15e62c3e63a08ea669adb"
MODEL = "fixed-model"


def _unused_call_llm(*_args, **_kwargs):
    raise AssertionError("call_llm_details must be replaced by the response tape")


def _load_upstream(path):
    source = subprocess.check_output(
        ["git", "show", f"{UPSTREAM}:{path}"], cwd=ROOT, text=True
    )
    module = types.ModuleType("upstream_" + Path(path).stem)
    module.__file__ = f"{UPSTREAM}:{path}"

    call_llm = types.ModuleType("utils.call_llm")
    call_llm.__all__ = ["call_llm_details"]
    call_llm.call_llm_details = _unused_call_llm
    previous = sys.modules.get("utils.call_llm")
    sys.modules["utils.call_llm"] = call_llm
    try:
        exec(compile(source, module.__file__, "exec"), module.__dict__)
    finally:
        if previous is None:
            del sys.modules["utils.call_llm"]
        else:
            sys.modules["utils.call_llm"] = previous
    return module


def _provider_messages(message):
    if isinstance(message, str):
        return [{"role": "user", "content": message}]
    return copy.deepcopy(message)


class ResponseTape:
    def __init__(self, responses):
        self.responses = iter(copy.deepcopy(responses))
        self.calls = []

    def __call__(self, message, model):
        self.calls.append((_provider_messages(message), model))
        response = next(self.responses)
        return tuple(copy.deepcopy(item) for item in response)


def _submission_snapshot(request):
    return {
        "problem_id": request.problem_id,
        "data": copy.deepcopy(request.data),
        "files": copy.deepcopy(request.files),
    }


class JudgeTape:
    def __init__(self, outcomes):
        self.outcomes = iter(copy.deepcopy(outcomes))
        self.requests = []

    def makeBackgroundSubmission(self, request):
        self.requests.append(_submission_snapshot(request))
        return copy.deepcopy(next(self.outcomes))


class PatchTape:
    def __init__(self, replacements):
        self.replacements = dict(replacements)
        self.calls = []

    def __call__(self, source, candidate):
        self.calls.append((source, candidate))
        replacement = self.replacements[candidate]
        if isinstance(replacement, BaseException):
            raise replacement
        return replacement


class SimilarityTape:
    def __init__(self, values):
        self.values = dict(values)
        self.calls = []

    def __call__(self, candidate, original):
        self.calls.append((candidate, original))
        return self.values[candidate]


def _run_upstream(module, function_name, args, responses, outcomes, patcher=None, similarity=None):
    calls = ResponseTape(responses)
    judge = JudgeTape(outcomes)
    with ExitStack() as stack:
        stack.enter_context(patch.object(module, "call_llm_details", calls))
        stack.enter_context(patch.object(module, "Client", return_value=judge))
        if patcher is not None:
            stack.enter_context(patch.object(module, "apply_patch_to_code", patcher))
        if similarity is not None:
            stack.enter_context(patch.object(module, "similarity", similarity))
        result = getattr(module, function_name)(MODEL, *args)
    return result, calls, judge


def _run_current(module, function_name, args, responses, outcomes, patcher=None, similarity=None):
    calls = ResponseTape(responses)
    judge = JudgeTape(outcomes)
    solver = PromptSolver(MODEL, calls)
    with ExitStack() as stack:
        stack.enter_context(patch.object(module, "Client", return_value=judge))
        if patcher is not None:
            stack.enter_context(patch.object(module, "apply_patch_to_code", patcher))
        if similarity is not None:
            stack.enter_context(patch.object(module, "similarity", similarity))
        result = getattr(module, function_name)(solver, *args)
    return result, calls, judge


class UpstreamDifferentialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.upstream_problem = _load_upstream("scripts/test_problem.py")
        cls.upstream_hack = _load_upstream("scripts/test_hack.py")
        cls.upstream_hack_agent = _load_upstream("scripts/test_hack_agent.py")
        cls.upstream_debug = _load_upstream("scripts/test_debug.py")
        cls.upstream_debug_agent = _load_upstream("scripts/test_debug_agent.py")

    def _assert_pair(
        self,
        upstream_module,
        current_module,
        function_name,
        upstream_args,
        current_args,
        responses,
        outcomes,
        patch_replacements=None,
        similarity_values=None,
    ):
        upstream_patcher = PatchTape(patch_replacements) if patch_replacements is not None else None
        current_patcher = PatchTape(patch_replacements) if patch_replacements is not None else None
        upstream_similarity = SimilarityTape(similarity_values) if similarity_values is not None else None
        current_similarity = SimilarityTape(similarity_values) if similarity_values is not None else None

        upstream = _run_upstream(
            upstream_module,
            function_name,
            upstream_args,
            responses,
            outcomes,
            upstream_patcher,
            upstream_similarity,
        )
        current = _run_current(
            current_module,
            function_name,
            current_args,
            responses,
            outcomes,
            current_patcher,
            current_similarity,
        )
        upstream_result, upstream_calls, upstream_judge = upstream
        current_result, current_calls, current_judge = current

        self.assertEqual(current_result, upstream_result)
        self.assertEqual(current_calls.calls, upstream_calls.calls)
        self.assertEqual(current_judge.requests, upstream_judge.requests)
        if upstream_patcher is not None:
            self.assertEqual(current_patcher.calls, upstream_patcher.calls)
        if upstream_similarity is not None:
            self.assertEqual(current_similarity.calls, upstream_similarity.calls)
        return current_result, current_judge.requests

    def test_one_shot_prompt_parser_submission_and_score_match_upstream(self):
        full_message = {"reasoning_content": "fixed reasoning", "request_id": "response-1"}
        usage = {"prompt_tokens": 101, "completion_tokens": 17}
        cases = (
            {
                "name": "generation-english",
                "upstream": self.upstream_problem,
                "current": test_problem,
                "function": "TestProblem",
                "upstream_args": (17, "Add two integers.", False),
                "current_args": (17, "Add two integers.", False),
                "response": "prefix```cpp\nint main() { return 0; }\n```suffix",
                "outcomes": ({"result": {"score": 73}},),
                "score": 73,
            },
            {
                "name": "generation-chinese",
                "upstream": self.upstream_problem,
                "current": test_problem,
                "function": "TestProblem",
                "upstream_args": (18, "Chinese statement", True),
                "current_args": (18, "Chinese statement", True),
                "response": "```cpp\nint answer;\n```",
                "outcomes": ({"result": {"score": 41}},),
                "score": 41,
            },
            {
                "name": "hacking-english",
                "upstream": self.upstream_hack,
                "current": test_hack,
                "function": "TestHack",
                "upstream_args": (21, "Find a maximum.", "wrong source", "C++17", False),
                "current_args": (21, "Find a maximum.", "wrong source", "C++17", False),
                "response": "text```python\nprint('hack-1')\n```text",
                "outcomes": ({"result": {"score": 1}},),
                "score": 1,
            },
            {
                "name": "hacking-chinese",
                "upstream": self.upstream_hack,
                "current": test_hack,
                "function": "TestHack",
                "upstream_args": (22, "Chinese statement", "wrong source", "Python3", True),
                "current_args": (22, "Chinese statement", "wrong source", "Python3", True),
                "response": "```python\nprint('not accepted')\n```",
                "outcomes": ({"result": {"score": 0}},),
                "score": 0,
            },
            {
                "name": "repair-english",
                "upstream": self.upstream_debug,
                "current": test_debug,
                "function": "TestDebug",
                "upstream_args": (31, "Repair it.", "old\r\n", "C++14", False),
                "current_args": (31, "Repair it.", "old\r\n", "C++14", False),
                "response": "```patch\nfix-one\n```",
                "outcomes": ({"result": {"score": 100}},),
                "score": 1,
                "patches": {"fix-one\n": "fixed-one\n"},
                "similarities": {"fixed-one\n": 0.95},
            },
            {
                "name": "repair-chinese",
                "upstream": self.upstream_debug,
                "current": test_debug,
                "function": "TestDebug",
                "upstream_args": (32, "Chinese statement", "old\r\n", "C++20", True),
                "current_args": (32, "Chinese statement", "old\r\n", "C++20", True),
                "response": "```patch\nfix-two\n```",
                "outcomes": ({"result": {"score": 0}},),
                "score": 0,
                "patches": {"fix-two\n": "fixed-two\n"},
                "similarities": {"fixed-two\n": 0.95},
            },
        )

        for case in cases:
            with self.subTest(case=case["name"]):
                result, requests = self._assert_pair(
                    case["upstream"],
                    case["current"],
                    case["function"],
                    case["upstream_args"],
                    case["current_args"],
                    [(case["response"], full_message, usage)],
                    case["outcomes"],
                    case.get("patches"),
                    case.get("similarities"),
                )
                self.assertEqual(result[0], case["score"])
                self.assertEqual(len(requests), 1)

    def test_one_shot_invalid_output_matches_upstream_without_submission(self):
        cases = (
            (self.upstream_problem, test_problem, "TestProblem", (41, "P", False), "no output code"),
            (
                self.upstream_hack,
                test_hack,
                "TestHack",
                (42, "P", "wrong", "C++20", False),
                "no output hack data",
            ),
            (
                self.upstream_debug,
                test_debug,
                "TestDebug",
                (43, "P", "wrong", "C++20", False),
                "no output patch",
            ),
        )
        response = [("a fenced block in the wrong language", {"reasoning_content": ""}, {"tokens": 3})]

        for upstream, current, function_name, args, expected_error in cases:
            with self.subTest(function=function_name):
                result, requests = self._assert_pair(
                    upstream, current, function_name, args, args, response, ()
                )
                self.assertEqual(result[0], 0)
                self.assertEqual(result[2], expected_error)
                self.assertEqual(requests, [])

    def test_hacking_agent_feedback_history_submission_and_score_match_upstream(self):
        responses = [
            ("no python block", {"reasoning_content": "", "id": 1}, {"tokens": 11}),
            ("```python\nprint('first')\n```", {"reasoning_content": "reason", "id": 2}, {"tokens": 12}),
            ("```python\nprint('second')\n```", {"reasoning_content": "", "id": 3}, {"tokens": 13}),
        ]
        outcomes = ({"result": {"score": 0}}, {"result": {"score": 1}})
        args = (51, "Hack statement", "wrong source", "C++11", 3)

        result, requests = self._assert_pair(
            self.upstream_hack_agent,
            test_hack_agent,
            "TestHackAgent",
            args,
            args,
            responses,
            outcomes,
        )

        self.assertEqual(result[0], 1)
        self.assertEqual(len(result[1]), 7)
        self.assertEqual(len(result[2]), 2)
        self.assertEqual(len(result[3]), 6)
        self.assertEqual(len(result[4]), 3)
        self.assertEqual(len(requests), 2)

    def test_repair_agent_all_feedback_history_submission_and_score_match_upstream(self):
        responses = [
            ("no patch block", {"reasoning_content": "", "id": 1}, {"tokens": 21}),
            ("```patch\nbad-apply\n```", {"reasoning_content": "r2", "id": 2}, {"tokens": 22}),
            ("```patch\ntoo-large\n```", {"reasoning_content": "", "id": 3}, {"tokens": 23}),
            ("```patch\njudge-one\n```", {"reasoning_content": "r4", "id": 4}, {"tokens": 24}),
            ("```patch\njudge-two\n```", {"reasoning_content": "", "id": 5}, {"tokens": 25}),
        ]
        outcomes = ({"result": {"score": 0}}, {"result": {"score": 100}})
        patches = {
            "bad-apply\n": ValueError("bad hunk"),
            "too-large\n": "large rewrite\n",
            "judge-one\n": "candidate one\n",
            "judge-two\n": "candidate two\n",
        }
        similarities = {
            "large rewrite\n": 0.50,
            "candidate one\n": 0.95,
            "candidate two\n": 0.95,
        }
        args = (61, "Repair statement", "old\r\n", "C++20", 5)

        result, requests = self._assert_pair(
            self.upstream_debug_agent,
            test_debug_agent,
            "TestDebugAgent",
            args,
            args,
            responses,
            outcomes,
            patches,
            similarities,
        )

        self.assertEqual(result[0], 1)
        self.assertEqual(len(result[1]), 15)
        self.assertEqual(len(result[2]), 2)
        self.assertEqual(len(result[3]), 10)
        self.assertEqual(len(result[4]), 5)
        self.assertEqual(len(requests), 2)


if __name__ == "__main__":
    unittest.main()
