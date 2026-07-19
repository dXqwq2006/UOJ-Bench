import os
import requests
import unittest
from unittest.mock import patch

os.environ.setdefault("UOJ_API_KEY", "offline")

from scripts import test_debug, test_debug_agent, test_hack, test_hack_agent, test_problem
from solution.api import (
    FeedbackKind,
    HackCandidate,
    PatchCandidate,
    SolutionCandidate,
    SolverTurn,
)


class Session:
    def __init__(self, candidate, initial_request="solver request"):
        self.turn = SolverTurn(candidate, "raw", {"raw": True}, {"total_tokens": 3})
        self.initial_request = initial_request

    def next(self, feedback=None):
        return self.turn

    @property
    def transcript(self):
        return [{"role": "user", "content": "prompt"}]


class SequenceSession:
    def __init__(self, *candidates):
        self.turns = iter(
            SolverTurn(candidate, "raw", {"raw": True}, {"total_tokens": 3},
                       None if candidate else "invalid")
            for candidate in candidates
        )
        self.feedback = []
        self.next_calls = 0
        self.history = [{"role": "user", "content": "prompt"}]

    @property
    def initial_request(self):
        return "solver request"

    def next(self, feedback=None):
        if feedback is not None:
            self.record_feedback(feedback)
        self.next_calls += 1
        return next(self.turns)

    def record_feedback(self, feedback):
        self.feedback.append(feedback)
        self.history.append({"role": "user", "feedback": feedback.kind})

    @property
    def transcript(self):
        return list(self.history)


class ModelTransportSession(SequenceSession):
    def __init__(self, *candidates):
        super().__init__(*candidates)
        self.failed = False

    def next(self, feedback=None):
        if not self.failed:
            self.failed = True
            self.next_calls += 1
            raise requests.exceptions.ConnectionError("model down")
        return super().next(feedback)


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
        self.scores = iter(score if isinstance(score, list) else [score])
        self.request = None
        self.requests = []

    def makeBackgroundSubmission(self, request):
        self.request = request
        self.requests.append(request)
        outcome = next(self.scores)
        if isinstance(outcome, BaseException):
            raise outcome
        return {"result": {"score": outcome}}


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
        self.assertEqual(prompt, "solver request")

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


class AgentTaskTests(unittest.TestCase):
    def test_hacking_routes_parser_and_judge_feedback_through_session(self):
        session = SequenceSession(None, HackCandidate("print(1)"), HackCandidate("print(2)"))
        solver = FakeSolver(None)
        solver.start_hacking = lambda task: session
        client = FakeClient([0, 1])

        with patch.object(test_hack_agent, "Client", return_value=client):
            score, transcript, results, messages, usages = test_hack_agent.TestHackAgent(
                solver, 1, "statement", "wrong", max_trials=3
            )

        self.assertEqual(score, 1)
        self.assertEqual(transcript, session.transcript)
        self.assertEqual(len(results), 2)
        self.assertEqual(len(messages), 6)
        self.assertEqual(len(usages), 3)
        self.assertEqual(session.feedback[0].kind, FeedbackKind.INVALID_OUTPUT)
        self.assertEqual(session.feedback[1].kind, FeedbackKind.JUDGE_REJECTED)

    def test_repair_routes_local_and_judge_feedback_through_session(self):
        session = SequenceSession(
            PatchCandidate("bad"),
            PatchCandidate("large"),
            PatchCandidate("wrong"),
            PatchCandidate("fixed"),
        )
        solver = FakeSolver(None)
        solver.start_repair = lambda task: session
        client = FakeClient([0, 100])

        def apply_patch(source, candidate):
            if candidate == "bad":
                raise ValueError("bad patch")
            return candidate

        with (
            patch.object(test_debug_agent, "Client", return_value=client),
            patch.object(test_debug_agent, "apply_patch_to_code", side_effect=apply_patch),
            patch.object(test_debug_agent, "similarity", side_effect=[0.5, 0.95, 0.95]),
        ):
            score, *_ = test_debug_agent.TestDebugAgent(
                solver, 1, "statement", "wrong", max_trials=4
            )

        self.assertEqual(score, 1)
        self.assertEqual(
            [feedback.kind for feedback in session.feedback],
            [
                FeedbackKind.PATCH_ERROR,
                FeedbackKind.SIMILARITY_REJECTION,
                FeedbackKind.JUDGE_REJECTED,
            ],
        )
        self.assertEqual(len(client.requests), 2)

    def test_hacking_preserves_outer_round_retry_after_uoj_transport_failure(self):
        session = SequenceSession(HackCandidate("print(1)"), HackCandidate("print(2)"))
        solver = FakeSolver(None)
        solver.start_hacking = lambda task: session
        client = FakeClient([requests.exceptions.ConnectionError("uoj down"), 1])

        with (
            patch.object(test_hack_agent, "Client", return_value=client),
            patch.object(test_hack_agent.time, "sleep"),
        ):
            score, *_ = test_hack_agent.TestHackAgent(
                solver, 1, "statement", "wrong", max_trials=1
            )

        self.assertEqual(score, 1)
        self.assertEqual(session.next_calls, 2)
        self.assertEqual(len(client.requests), 2)

    def test_hacking_model_transport_failure_does_not_consume_a_trial(self):
        session = ModelTransportSession(HackCandidate("print(1)"))
        solver = FakeSolver(None)
        solver.start_hacking = lambda task: session
        client = FakeClient(1)

        with (
            patch.object(test_hack_agent, "Client", return_value=client),
            patch.object(test_hack_agent.time, "sleep"),
        ):
            score, *_ = test_hack_agent.TestHackAgent(
                solver, 1, "statement", "wrong", max_trials=1
            )

        self.assertEqual(score, 1)
        self.assertEqual(session.next_calls, 2)
        self.assertEqual(len(client.requests), 1)

    def test_repair_preserves_outer_round_retry_after_uoj_transport_failure(self):
        session = SequenceSession(PatchCandidate("first"), PatchCandidate("second"))
        solver = FakeSolver(None)
        solver.start_repair = lambda task: session
        client = FakeClient([requests.exceptions.ConnectionError("uoj down"), 100])

        with (
            patch.object(test_debug_agent, "Client", return_value=client),
            patch.object(test_debug_agent, "apply_patch_to_code", return_value="fixed"),
            patch.object(test_debug_agent, "similarity", return_value=0.95),
        ):
            score, *_ = test_debug_agent.TestDebugAgent(
                solver, 1, "statement", "wrong", max_trials=1
            )

        self.assertEqual(score, 1)
        self.assertEqual(session.next_calls, 2)
        self.assertEqual(len(client.requests), 2)

    def test_exhausted_agent_transcript_contains_final_feedback(self):
        session = SequenceSession(None)
        solver = FakeSolver(None)
        solver.start_hacking = lambda task: session

        with patch.object(test_hack_agent, "Client", return_value=FakeClient(1)):
            score, transcript, *_ = test_hack_agent.TestHackAgent(
                solver, 1, "statement", "wrong", max_trials=1
            )

        self.assertEqual(score, 0)
        self.assertEqual(transcript[-1]["feedback"], FeedbackKind.INVALID_OUTPUT)


if __name__ == "__main__":
    unittest.main()
