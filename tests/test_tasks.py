import os
import unittest
from unittest.mock import patch

os.environ.setdefault("UOJ_API_KEY", "offline")

from scripts import test_debug, test_hack, test_problem
from utils.solver import (
    HackCandidate,
    PatchCandidate,
    SolutionCandidate,
    SolverTurn,
)


class Session:
    def __init__(self, candidate):
        self.turn = SolverTurn(candidate, "raw", {"raw": True}, {"total_tokens": 3})

    def next(self, feedback=None):
        return self.turn


class FakeSolver:
    def __init__(self, candidate):
        self.candidate = candidate
        self.task = None

    def start_generation(self, task):
        self.task = task
        return Session(self.candidate)

    def start_hacking(self, task):
        self.task = task
        return Session(self.candidate)

    def start_repair(self, task):
        self.task = task
        return Session(self.candidate)


class FakeClient:
    def __init__(self, score):
        self.score = score
        self.request = None

    def makeBackgroundSubmission(self, request):
        self.request = request
        return {"result": {"score": self.score}}


class DirectTaskTests(unittest.TestCase):
    def test_generation_uses_solution_candidate_and_keeps_uoj_score(self):
        solver = FakeSolver(SolutionCandidate("int main() {}"))
        client = FakeClient(73)
        with patch.object(test_problem, "Client", return_value=client):
            score, prompt, result, message, usage = test_problem.TestProblem(
                solver, 1, "statement", metadata={"title_en": "Title"}
            )

        self.assertEqual(score, 73)
        self.assertEqual(result, {"result": {"score": 73}})
        self.assertEqual(message, {"raw": True})
        self.assertEqual(usage, {"total_tokens": 3})
        self.assertEqual(solver.task.metadata, {"title_en": "Title"})
        self.assertEqual(client.request.files["sub_answer_text"], (None, "int main() {}"))
        self.assertIn("### Question:\nstatement", prompt)

    def test_hacking_uses_generator_candidate_and_official_binary_score(self):
        solver = FakeSolver(HackCandidate("print(1)"))
        client = FakeClient(1)
        with patch.object(test_hack, "Client", return_value=client):
            score, *_ = test_hack.TestHack(
                solver, 2, "statement", "wrong", "C++20", metadata={"hack_id": "h"}
            )

        self.assertEqual(score, 1)
        self.assertEqual(client.request.data, {"type": "hack", "format_input_file": True})
        self.assertEqual(client.request.files["sub_answer_text"], (None, "wrong"))
        self.assertEqual(client.request.files["sub_hack_input_text"], (None, "print(1)"))

    def test_repair_uses_patch_candidate_before_unchanged_uoj_gate(self):
        solver = FakeSolver(PatchCandidate("patch"))
        client = FakeClient(100)
        with (
            patch.object(test_debug, "Client", return_value=client),
            patch.object(test_debug, "apply_patch_to_code", return_value="fixed"),
            patch.object(test_debug, "similarity", return_value=0.95),
        ):
            score, *_ = test_debug.TestDebug(
                solver, 3, "statement", "wrong", metadata={"wrong_id": "w"}
            )

        self.assertEqual(score, 1)
        self.assertEqual(client.request.files["sub_answer_text"], (None, "fixed"))


if __name__ == "__main__":
    unittest.main()
