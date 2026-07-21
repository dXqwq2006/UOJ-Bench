import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from solution.api import HackCandidate, SolverCapabilities, SolverTurn
from scripts import run_hack_rollout_batch as rollout


def write_dataset(root: Path) -> None:
    problems = [
        {
            "problem_id": "1",
            "statement_en": "easy statement",
            "difficulty": "easy",
            "hackable": 1,
        },
        {
            "problem_id": "2",
            "statement_en": "hard statement",
            "difficulty": "hard",
            "hackable": 1,
        },
    ]
    easy = [{"problem_id": "1", "wrong_code": "wrong", "language": "C++20"}]
    hard = [
        {
            "hack_id": "h1",
            "submission_id": "s1",
            "problem_id": "2",
            "wrong_code": "wrong",
            "language": "C++14",
        }
    ]
    for name, value in (
        ("problems.json", problems),
        ("sampled_large_submission_pairs.json", easy),
        ("hacks.json", hard),
    ):
        (root / name).write_text(json.dumps(value), encoding="utf-8")


class FakeSession:
    def __init__(self):
        self._transcript = [{"role": "user", "content": "prompt"}]

    @property
    def initial_request(self):
        return "prompt"

    @property
    def transcript(self):
        return list(self._transcript)

    def next(self, feedback=None):
        raw = "```python\nprint(1)\n```"
        message = {"role": "assistant", "content": raw}
        self._transcript.append(message)
        return SolverTurn(
            candidate=HackCandidate("print(1)\n"),
            raw_text=raw,
            message=message,
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        )

    def record_feedback(self, feedback):
        raise AssertionError("rollout must not record evaluation feedback")


class FakeSolver:
    capabilities = SolverCapabilities()

    def __init__(self, calls):
        self.calls = calls

    def start_hacking(self, task):
        self.calls.append(task.problem_id)
        return FakeSession()


class RolloutTests(unittest.TestCase):
    def test_first_turn_is_durable_and_resume_skips_completed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dataset = root / "dataset"
            dataset.mkdir()
            write_dataset(dataset)
            result = root / "result"
            calls = []

            with (
                patch.object(rollout, "load_solver", side_effect=lambda *_: FakeSolver(calls)),
                patch.dict(os.environ, {"TATU_DEPLOYER": "GOOGLE"}, clear=False),
            ):
                summary = rollout.run_batch(
                    dataset_dir=dataset,
                    result_dir=result,
                    split="all",
                    solver_name="prompt",
                    model="gemini-3.1-pro-preview",
                    workers=2,
                    progress=False,
                )

            self.assertEqual(summary["overall"]["completed"], 2)
            self.assertEqual(summary["overall"]["valid_candidate"], 2)
            self.assertEqual(summary["usage"]["total_tokens"], 30)
            self.assertEqual(sorted(calls), ["1", "2"])
            manifest = json.loads((result / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["run"]["request"]["deployer"], "GOOGLE")
            record = json.loads((result / "samples" / "easy-0000.json").read_text())
            self.assertEqual(record["candidate"], "print(1)\n")
            self.assertEqual(record["provenance"], "generated")

            calls.clear()
            with (
                patch.object(rollout, "load_solver", side_effect=lambda *_: FakeSolver(calls)),
                patch.dict(os.environ, {"TATU_DEPLOYER": "GOOGLE"}, clear=False),
            ):
                resumed = rollout.run_batch(
                    dataset_dir=dataset,
                    result_dir=result,
                    split="all",
                    solver_name="prompt",
                    model="gemini-3.1-pro-preview",
                    workers=2,
                    resume=True,
                    progress=False,
                )
            self.assertEqual(resumed["overall"]["completed"], 2)
            self.assertEqual(calls, [])

    def test_agent_result_import_keeps_only_first_turn(self):
        sample = rollout.HackSample(
            sample_id="easy-0000",
            split="easy",
            source_index=0,
            problem_id="1",
            problem_statement="statement",
            submission_code="wrong",
            submission_language="C++20",
            difficulty="easy",
            metadata={},
        )
        source = {
            "status": "completed",
            "attempt": 3,
            "transcript": [
                {"role": "user", "content": "prompt"},
                {"role": "assistant", "content": "[REASONING] internal reasoning"},
                {"role": "assistant", "content": "[ANSWER] rendered answer"},
                {"role": "user", "content": "judge feedback"},
            ],
            "messages": [
                {"role": "user", "content": "prompt"},
                {"role": "assistant", "content": "```python\nprint(7)\n```"},
            ],
            "usages": [{"prompt_tokens": 4, "completion_tokens": 3, "total_tokens": 7}],
        }

        record = rollout._seed_record(sample, source, Path("seed"))

        self.assertEqual(record["candidate"], "print(7)\n")
        self.assertEqual(len(record["transcript"]), 2)
        self.assertEqual(record["usage"]["total_tokens"], 7)
        self.assertEqual(record["provenance"], "imported_agent_result")


if __name__ == "__main__":
    unittest.main()
