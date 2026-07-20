import unittest

from solution import load_solver
from solution.api import (
    FeedbackKind,
    FaultExposureInput,
    GenerationInput,
    HackCandidate,
    HackingInput,
    PatchCandidate,
    RepairInput,
    SolutionCandidate,
    SolverFeedback,
    TestCaseCandidate,
    TestCaseFormat,
)
from solution.prompt import PromptSolver
from utils.benchmark import solver_metadata


class FakeCaller:
    def __init__(self, *texts):
        self.texts = iter(texts)
        self.calls = []

    def __call__(self, message, model):
        self.calls.append((message, model))
        text = next(self.texts)
        return text, {"content": text}, {"output_tokens": 7}


def generation_input():
    return GenerationInput(1, "problem")


def hacking_input():
    return HackingInput(2, "problem", "wrong source")


def repair_input():
    return RepairInput(3, "problem", "wrong source")


class PromptSolverTests(unittest.TestCase):
    def test_parses_all_official_candidate_formats(self):
        cases = [
            ("generation", generation_input(), "before```cpp\nint main() {}\n```after", SolutionCandidate("int main() {}\n")),
            ("hacking", hacking_input(), "```python\nprint(1)\n```", HackCandidate("print(1)\n")),
            ("repair", repair_input(), "```patch\n@@ -1 +1 @@\n-a\n+b\n```", PatchCandidate("@@ -1 +1 @@\n-a\n+b\n")),
        ]
        for method, task, text, expected in cases:
            with self.subTest(method=method):
                caller = FakeCaller(text)
                session = getattr(PromptSolver("model", caller), "start_" + method)(task)
                turn = session.next()
                self.assertEqual(turn.candidate, expected)
                self.assertIsNone(turn.error)
                self.assertEqual(turn.raw_text, text)
                self.assertEqual(turn.message, {"content": text})
                self.assertEqual(turn.usage, {"output_tokens": 7})
                self.assertEqual(caller.calls, [(session.initial_request, "model")])

    def test_fault_exposure_reuses_uoj_prompt_and_returns_generator(self):
        caller = FakeCaller("```python\nprint(1)\n```")
        task = FaultExposureInput(
            "2000A", "Problem", 42, "wrong source", "C++20 (GCC 13-64)"
        )
        session = PromptSolver("model", caller).start_fault_exposure(task)
        turn = session.next()

        self.assertIn("Write a python program", session.initial_request)
        self.assertIn("wrong source", session.initial_request)
        self.assertEqual(
            turn.candidate,
            TestCaseCandidate("print(1)\n", TestCaseFormat.PYTHON_GENERATOR),
        )
        self.assertTrue(PromptSolver.capabilities.fault_exposure)

    def test_parser_is_as_strict_as_upstream(self):
        invalid = [
            "```python\nint main() {}\n```",
            "```cpp\r\nint main() {}\n```",
            "```cpp int main() {} ```",
        ]
        for text in invalid:
            with self.subTest(text=text):
                turn = PromptSolver("model", FakeCaller(text)).start_generation(generation_input()).next()
                self.assertIsNone(turn.candidate)
                self.assertEqual(turn.error, "no output code")

    def test_hacking_feedback_uses_official_text_and_history(self):
        caller = FakeCaller("no block", "```python\nprint(2)\n```", "```python\nprint(3)\n```")
        session = PromptSolver("model", caller).start_hacking(hacking_input())
        self.assertEqual(session.next().error, "no output hack data")
        session.next(SolverFeedback(FeedbackKind.INVALID_OUTPUT))
        result = {"result": {"score": 0}}
        session.next(SolverFeedback(FeedbackKind.JUDGE_REJECTED, result))

        retry = "\nTry again! Output a new python code which would generate the correct hack data."
        self.assertEqual(caller.calls[1][0][-1]["content"], "No Python code block found in your response" + retry)
        self.assertEqual(
            caller.calls[2][0][-1]["content"],
            "The python code generate invalid input or the code can still pass your test. "
            f"Here is the results\n{result}\n\n" + retry,
        )

    def test_native_assistant_turn_is_preserved(self):
        native = {"role": "model", "parts": [{"text": "answer", "thoughtSignature": "sig"}]}

        def caller(message, model):
            if isinstance(message, str):
                return "```python\nprint(1)\n```", {
                    "provider": "gemini",
                    "native_turn": native,
                }, {}
            self.assertEqual(message[1]["native_turn"], native)
            return "```python\nprint(2)\n```", {}, {}

        session = PromptSolver("model", caller).start_hacking(hacking_input())
        session.next()
        session.next(SolverFeedback(FeedbackKind.JUDGE_REJECTED, {}))

    def test_repair_feedback_uses_official_text(self):
        patch = "```patch\n@@ -1 +1 @@\n-a\n+b\n```"
        caller = FakeCaller(patch, patch, patch, patch)
        session = PromptSolver("model", caller).start_repair(repair_input())
        session.next()
        session.next(SolverFeedback(FeedbackKind.PATCH_ERROR, "bad hunk"))
        session.next(SolverFeedback(FeedbackKind.SIMILARITY_REJECTION))
        result = {"result": {"score": 0}}
        session.next(SolverFeedback(FeedbackKind.JUDGE_REJECTED, result))

        retry = "\nTry again! Output a new patch which would be directly applied to the code given for the first time."
        self.assertEqual(caller.calls[1][0][-1]["content"], "Meet error when applying patch: bad hunk" + retry)
        self.assertEqual(caller.calls[2][0][-1]["content"], "You made too many changes" + retry)
        self.assertEqual(
            caller.calls[3][0][-1]["content"],
            f"The new code cannot pass all tests. Here is the results\n{result}\n\n" + retry,
        )

    def test_recorded_feedback_is_visible_before_the_next_call(self):
        caller = FakeCaller("```python\nprint(1)\n```", "```python\nprint(2)\n```")
        session = PromptSolver("model", caller).start_hacking(hacking_input())
        session.next()
        session.record_feedback(SolverFeedback(FeedbackKind.INVALID_OUTPUT))

        self.assertEqual(session.transcript[-1]["role"], "user")
        session.next()

    def test_solver_metadata_is_fail_closed(self):
        record = {
            "problem_id": 1,
            "title_en": "Visible title",
            "difficulty": 7,
            "correct_code": "oracle",
            "reference_solution": "future oracle",
            "language": {"nested": "oracle"},
        }

        self.assertEqual(
            solver_metadata(record),
            {"problem_id": 1, "title_en": "Visible title", "difficulty": 7},
        )

    def test_invalid_repair_output_and_session_guards(self):
        caller = FakeCaller("no patch", "still no patch", "```patch\nx\n```")
        session = PromptSolver("model", caller).start_repair(repair_input())
        with self.assertRaisesRegex(ValueError, "previous solver turn"):
            session.next(SolverFeedback(FeedbackKind.INVALID_OUTPUT))
        session.next()
        session.next(SolverFeedback(FeedbackKind.INVALID_OUTPUT))
        retry = "\nTry again! Output a new patch which would be directly applied to the code given for the first time."
        self.assertEqual(caller.calls[1][0][-1]["content"], "No patch block found in your response" + retry)
        session.next()

    def test_rejects_feedback_not_defined_by_official_task(self):
        session = PromptSolver("model", FakeCaller("```cpp\nx\n```", "unused")).start_generation(generation_input())
        session.next()
        with self.assertRaisesRegex(ValueError, "not valid for generation"):
            session.next(SolverFeedback(FeedbackKind.JUDGE_REJECTED, {}))


class SolutionLoaderTests(unittest.TestCase):
    def test_loads_directory_solver_and_rejects_invalid_names(self):
        solver = load_solver("prompt", "model")
        self.assertIsInstance(solver, PromptSolver)
        self.assertEqual(solver.model, "model")
        with self.assertRaisesRegex(ValueError, "invalid solver name"):
            load_solver("../prompt", "model")


if __name__ == "__main__":
    unittest.main()
