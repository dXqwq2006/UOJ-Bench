import unittest

from utils.solver import (
    FeedbackKind,
    GenerationInput,
    HackCandidate,
    HackingInput,
    PatchCandidate,
    PromptSolver,
    RepairInput,
    SolutionCandidate,
    SolverFeedback,
)


class FakeCaller:
    def __init__(self, *texts):
        self.texts = iter(texts)
        self.calls = []

    def __call__(self, message, model):
        self.calls.append((message, model))
        text = next(self.texts)
        return text, {"content": text}, {"output_tokens": 7}


def generation_input():
    return GenerationInput(1, "problem", "generation prompt")


def hacking_input():
    return HackingInput(2, "problem", "wrong source", "hacking prompt")


def repair_input():
    return RepairInput(3, "problem", "wrong source", "repair prompt")


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
                self.assertEqual(caller.calls, [(task.official_prompt, "model")])

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

    def test_invalid_repair_output_and_session_guards(self):
        caller = FakeCaller("no patch", "still no patch")
        session = PromptSolver("model", caller).start_repair(repair_input())
        with self.assertRaisesRegex(ValueError, "previous solver turn"):
            session.next(SolverFeedback(FeedbackKind.INVALID_OUTPUT))
        session.next()
        session.next(SolverFeedback(FeedbackKind.INVALID_OUTPUT))
        retry = "\nTry again! Output a new patch which would be directly applied to the code given for the first time."
        self.assertEqual(caller.calls[1][0][-1]["content"], "No patch block found in your response" + retry)
        with self.assertRaisesRegex(ValueError, "require feedback"):
            session.next()

    def test_rejects_feedback_not_defined_by_official_task(self):
        session = PromptSolver("model", FakeCaller("```cpp\nx\n```", "unused")).start_generation(generation_input())
        session.next()
        with self.assertRaisesRegex(ValueError, "not valid for generation"):
            session.next(SolverFeedback(FeedbackKind.JUDGE_REJECTED, {}))


if __name__ == "__main__":
    unittest.main()
