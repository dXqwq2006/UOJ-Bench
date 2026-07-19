import subprocess
import sys
import unittest
from unittest.mock import patch

from scripts import test_hack_agent
from solution import load_solver
from solution.api import (
    FeedbackKind,
    GenerationInput,
    HackingInput,
    RepairInput,
    SolverCapabilities,
    SolverFeedback,
    require_solver_support,
    solver_capabilities,
)
from solution.testcase_eval import TestCaseEvalSolver
from solution.testcase_eval.solver import extract_test_input


class FakeCaller:
    def __init__(self, text):
        self.text = text
        self.calls = []

    def __call__(self, request, model):
        self.calls.append((request, model))
        return self.text, {"content": self.text}, {"output_tokens": 7}


def hacking_input(**overrides):
    values = {
        "problem_id": 2,
        "problem_statement": "Full problem statement",
        "submission_code": "int main() { return 0; }",
        "metadata": {"title_en": "Public title"},
    }
    values.update(overrides)
    return HackingInput(**values)


class TestCaseEvalSolverTests(unittest.TestCase):
    def test_prompt_parsing_and_generator_are_one_model_call(self):
        caller = FakeCaller("analysis\n```plaintext\n2\n1 2\n```")
        session = TestCaseEvalSolver("model", caller).start_hacking(hacking_input())

        turn = session.next()

        self.assertIn("Title: Public title", session.initial_request)
        self.assertIn("Full problem statement", session.initial_request)
        self.assertIn("int main() { return 0; }", session.initial_request)
        self.assertIn("Think step by step.", session.initial_request)
        self.assertEqual(caller.calls, [(session.initial_request, "model")])
        self.assertIsNone(turn.error)
        self.assertEqual(turn.usage, {"output_tokens": 7})
        output = subprocess.check_output([sys.executable, "-c", turn.candidate.generator])
        self.assertEqual(output, b"2\n1 2")

    def test_deterministic_official_regex_variants(self):
        cases = [
            (
                "<answer>\n```plaintext\n1\n7\n```\n</answer>",
                "1\n7",
            ),
            ("reason\n```\n2\n3 4\n```", "2\n3 4"),
            ("```plaintext\n3\n```\ntrailing", "3"),
        ]
        for response, expected in cases:
            with self.subTest(response=response):
                self.assertEqual(extract_test_input(response), expected)

        for response in ("1\n7", "```plaintext\n\n```", "no test"):
            with self.subTest(response=response):
                self.assertIsNone(extract_test_input(response))

    def test_feedback_is_audited_but_never_consumed(self):
        caller = FakeCaller("```plaintext\n1\n```")
        session = TestCaseEvalSolver("model", caller).start_hacking(hacking_input())
        with self.assertRaisesRegex(ValueError, "previous solver turn"):
            session.record_feedback(SolverFeedback(FeedbackKind.INVALID_OUTPUT))

        session.next()
        session.record_feedback(
            SolverFeedback(FeedbackKind.JUDGE_REJECTED, {"result": {"score": 0}})
        )

        self.assertIn("judge_rejected", session.transcript[-1]["content"])
        with self.assertRaisesRegex(RuntimeError, "one-shot"):
            session.next()
        self.assertEqual(len(caller.calls), 1)

    def test_capabilities_unsupported_tasks_and_loader(self):
        solver = TestCaseEvalSolver("model", FakeCaller("unused"))
        self.assertEqual(solver_capabilities(object()), SolverCapabilities())
        self.assertFalse(solver.capabilities.generation)
        self.assertTrue(solver.capabilities.hacking)
        self.assertFalse(solver.capabilities.hacking_feedback)

        with self.assertRaisesRegex(ValueError, "max_trials must be 1"):
            require_solver_support(solver, "hacking", feedback=True)
        with self.assertRaisesRegex(NotImplementedError, "generation"):
            solver.start_generation(GenerationInput(1, "problem"))
        with self.assertRaisesRegex(NotImplementedError, "repair"):
            solver.start_repair(RepairInput(1, "problem", "wrong"))
        with self.assertRaisesRegex(NotImplementedError, "English"):
            solver.start_hacking(hacking_input(chinese=True))

        loaded = load_solver("testcase_eval", "model")
        self.assertIsInstance(loaded, TestCaseEvalSolver)

    def test_agent_runner_rejects_multiple_trials_before_client_creation(self):
        solver = TestCaseEvalSolver("model", FakeCaller("unused"))
        with patch.object(
            test_hack_agent,
            "Client",
            side_effect=AssertionError("client must not be created"),
        ):
            with self.assertRaisesRegex(ValueError, "max_trials must be 1"):
                test_hack_agent.TestHackAgent(
                    solver,
                    2,
                    "problem",
                    "wrong",
                    max_trials=2,
                )


if __name__ == "__main__":
    unittest.main()
