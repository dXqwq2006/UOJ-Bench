import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from solution.api import SolverCapabilities
from scripts import run_hack_agent_batch as batch


ROOT = Path(__file__).resolve().parents[1]


def sample(sample_id, split, difficulty):
    return batch.HackSample(
        sample_id=sample_id,
        split=split,
        source_index=0,
        problem_id=sample_id,
        problem_statement="statement",
        submission_code="wrong",
        submission_language="C++20",
        difficulty=difficulty,
        metadata={"problem_id": sample_id, "difficulty": difficulty},
    )


def write_dataset(root, hard_count=1):
    problems = [
        {
            "problem_id": "1",
            "statement_en": "easy statement",
            "difficulty": "easy",
            "hackable": 1,
            "title_en": "Public title",
            "reference_solution": "private oracle",
        },
        {
            "problem_id": "2",
            "statement_en": "filtered statement",
            "difficulty": "medium",
            "hackable": 0,
        },
    ]
    hard = []
    for index in range(hard_count):
        problem_id = str(index + 3)
        problems.append(
            {
                "problem_id": problem_id,
                "statement_en": f"hard statement {index}",
                "difficulty": "hard",
                "hackable": 1,
            }
        )
        hard.append(
            {
                "hack_id": f"h{index}",
                "submission_id": f"s{index}",
                "problem_id": problem_id,
                "wrong_code": f"hard wrong {index}",
                "language": "C++14",
                "hidden_oracle": "do not expose",
            }
        )
    easy = [
        {
            "problem_id": "1",
            "wrong_id": "w1",
            "wrong_code": "easy wrong",
            "correct_code": "private correct code",
            "language": "C++20",
        },
        {
            "problem_id": "2",
            "wrong_id": "w2",
            "wrong_code": "filtered wrong",
            "correct_code": "filtered correct",
            "language": "C++",
        },
    ]
    for name, value in (
        ("problems.json", problems),
        ("sampled_large_submission_pairs.json", easy),
        ("hacks.json", hard),
    ):
        (root / name).write_text(json.dumps(value), encoding="utf-8")


class DatasetTests(unittest.TestCase):
    def test_official_easy_and_hard_counts_and_smoke_selection(self):
        samples = batch.load_samples(ROOT / "dataset", "all")
        self.assertEqual(sum(item.split == "easy" for item in samples), 479)
        self.assertEqual(sum(item.split == "hard" for item in samples), 1046)

        smoke = batch.load_samples(ROOT / "dataset", "all", smoke_per_split=5)
        self.assertEqual(sum(item.split == "easy" for item in smoke), 5)
        self.assertEqual(sum(item.split == "hard" for item in smoke), 5)
        for split in ("easy", "hard"):
            selected = [item for item in smoke if item.split == split]
            self.assertEqual(len({item.problem_id for item in selected}), 5)
            self.assertEqual(len({item.submission_language for item in selected}), 5)
        for item in smoke:
            self.assertNotIn("correct_code", item.metadata)
            self.assertNotIn("wrong_code", item.metadata)
            self.assertNotIn("statement_en", item.metadata)


class SummaryTests(unittest.TestCase):
    def test_pass_at_split_difficulty_usage_and_cost(self):
        samples = [
            sample("e1", "easy", "easy"),
            sample("e2", "easy", "hard"),
            sample("h1", "hard", "hard"),
            sample("h2", "hard", "ultrahard"),
        ]
        records = {
            "e1": {
                "status": "completed",
                "score": 1,
                "success_round": 1,
                "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            },
            "e2": {
                "status": "completed",
                "score": 0,
                "success_round": None,
                "usage": {"input_tokens": 20, "output_tokens": 10, "total_tokens": 30},
            },
            "h1": {
                "status": "completed",
                "score": 1,
                "success_round": 3,
                "usage": {"input_tokens": 30, "output_tokens": 15, "total_tokens": 45},
            },
            "h2": {"status": "retryable_error"},
        }

        summary = batch.summarize(samples, records, input_price=5, output_price=30)

        self.assertEqual(summary["splits"]["easy"]["successes"], 1)
        self.assertEqual(
            summary["splits"]["easy"]["pass_at"]["1"],
            {"count": 1, "denominator": 2, "rate": 0.5},
        )
        self.assertEqual(summary["splits"]["hard"]["pass_at"]["2"]["count"], 0)
        self.assertEqual(summary["splits"]["hard"]["pass_at"]["3"]["count"], 1)
        self.assertEqual(summary["splits"]["hard"]["retryable_error"], 1)
        self.assertEqual(summary["by_problem_difficulty"]["hard"]["planned"], 2)
        self.assertEqual(summary["usage"]["total_tokens"], 90)
        self.assertEqual(summary["budget"]["actual_cost_usd"], 0.0012)


class RunnerTests(unittest.TestCase):
    def test_cli_maps_public_flags_to_runner(self):
        expected = {"overall": {"completed": 0}}
        with (
            patch.object(batch, "run_batch", return_value=expected) as run,
            patch("builtins.print"),
        ):
            self.assertEqual(
                batch.main(
                    [
                        "--split", "easy",
                        "--solver", "paper_x",
                        "--model", "model-x",
                        "--max-trials", "7",
                        "--workers", "32",
                        "--split-schedule", "interleaved",
                        "--dataset-dir", "data",
                        "--result-dir", "out",
                        "--resume",
                        "--smoke-per-split", "5",
                        "--input-price-per-million", "2",
                        "--output-price-per-million", "4",
                        "--budget-usd", "100",
                        "--stop-at-usd", "90",
                    ]
                ),
                0,
            )
        run.assert_called_once_with(
            split="easy",
            solver_name="paper_x",
            model="model-x",
            max_trials=7,
            workers=32,
            split_schedule="interleaved",
            dataset_dir=Path("data"),
            result_dir=Path("out"),
            resume=True,
            smoke_per_split=5,
            input_price=2.0,
            output_price=4.0,
            budget_usd=100.0,
            stop_at_usd=90.0,
        )

    def test_interleaved_schedule_keeps_both_splits_active(self):
        samples = [
            sample("e1", "easy", "easy"),
            sample("e2", "easy", "easy"),
            sample("h1", "hard", "hard"),
            sample("h2", "hard", "hard"),
            sample("h3", "hard", "hard"),
        ]

        stages = batch._pending_stages(samples, {"e1": {"status": "completed"}}, "interleaved")

        self.assertEqual([[item.sample_id for item in stage] for stage in stages], [["e2", "h1", "h2", "h3"]])
        sequential = batch._pending_stages(samples, {}, "sequential")
        self.assertEqual(
            [[item.sample_id for item in stage] for stage in sequential],
            [["e1", "e2"], ["h1", "h2", "h3"]],
        )

    def test_atomic_results_resume_and_fail_closed_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dataset = root / "dataset"
            dataset.mkdir()
            write_dataset(dataset)
            result_dir = root / "results"
            calls = []
            lock = threading.Lock()

            def evaluate(solver, problem_id, statement, code, language, max_trials, metadata):
                with lock:
                    calls.append((problem_id, dict(metadata)))
                return (
                    1,
                    [{"role": "user", "content": "prompt"}],
                    [{"result": {"score": 1}}],
                    [{"raw": True}],
                    [{"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}],
                )

            with (
                patch.object(batch, "load_solver", return_value=object()),
                patch.object(batch, "_test_hack_agent", side_effect=evaluate),
            ):
                summary = batch.run_batch(
                    dataset_dir=dataset,
                    result_dir=result_dir,
                    split="all",
                    solver_name="prompt",
                    model="test-model",
                    workers=2,
                    progress=False,
                )

            self.assertEqual(summary["overall"]["completed"], 2)
            self.assertEqual(len(calls), 2)
            self.assertTrue(all("correct_code" not in metadata for _, metadata in calls))
            self.assertTrue(all("reference_solution" not in metadata for _, metadata in calls))
            self.assertEqual(len(list((result_dir / "samples").glob("*.json"))), 2)
            self.assertEqual(list(result_dir.rglob("*.tmp")), [])
            manifest_text = (result_dir / "manifest.json").read_text(encoding="utf-8")
            self.assertNotIn("private correct code", manifest_text)
            self.assertNotIn("private oracle", manifest_text)

            with (
                patch.object(batch, "load_solver", side_effect=AssertionError("must not load")),
                patch.object(batch, "_test_hack_agent", side_effect=AssertionError("must not rerun")),
            ):
                resumed = batch.run_batch(
                    dataset_dir=dataset,
                    result_dir=result_dir,
                    split="all",
                    solver_name="prompt",
                    model="test-model",
                    workers=8,
                    resume=True,
                    progress=False,
                )
            self.assertEqual(resumed["overall"]["completed"], 2)

    def test_retryable_error_is_persisted_and_retried_on_resume(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dataset = root / "dataset"
            dataset.mkdir()
            write_dataset(dataset)
            result_dir = root / "results"

            with (
                patch.object(batch, "load_solver", return_value=object()),
                patch.object(batch, "_test_hack_agent", side_effect=RuntimeError("temporary outage")),
            ):
                first = batch.run_batch(
                    dataset_dir=dataset,
                    result_dir=result_dir,
                    split="hard",
                    solver_name="prompt",
                    model="test-model",
                    workers=1,
                    progress=False,
                )
            self.assertEqual(first["overall"]["retryable_error"], 1)

            success = (1, [], [{"result": {"score": 1}}], [], [{"total_tokens": 3}])
            with (
                patch.object(batch, "load_solver", return_value=object()),
                patch.object(batch, "_test_hack_agent", return_value=success),
            ):
                second = batch.run_batch(
                    dataset_dir=dataset,
                    result_dir=result_dir,
                    split="hard",
                    solver_name="prompt",
                    model="test-model",
                    workers=1,
                    resume=True,
                    progress=False,
                )
            record = json.loads((result_dir / "samples" / "hard-0000.json").read_text())
            self.assertEqual(second["overall"]["completed"], 1)
            self.assertEqual(record["attempt"], 2)
            self.assertEqual(record["retry_errors"][0]["message"], "temporary outage")

    def test_success_round_tracks_consumed_trials_not_usage_entries(self):
        class Session:
            initial_request = "prompt"
            transcript = []

            def record_feedback(self, feedback):
                pass

        class Solver:
            def start_hacking(self, task):
                return Session()

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dataset = root / "dataset"
            dataset.mkdir()
            write_dataset(dataset)

            def evaluate(solver, *args, **kwargs):
                session = solver.start_hacking(None)
                session.record_feedback("invalid output")
                session.record_feedback("runtime error")
                return 1, [], [{"result": {"score": 1}}], [], [{"total_tokens": 3}]

            with (
                patch.object(batch, "load_solver", return_value=Solver()),
                patch.object(batch, "_test_hack_agent", side_effect=evaluate),
            ):
                summary = batch.run_batch(
                    dataset_dir=dataset,
                    result_dir=root / "results",
                    split="hard",
                    solver_name="prompt",
                    model="test-model",
                    workers=1,
                    progress=False,
                )

            record = json.loads((root / "results" / "samples" / "hard-0000.json").read_text())
            self.assertEqual(record["model_turns"], 1)
            self.assertEqual(record["counted_trials"], 2)
            self.assertEqual(record["success_round"], 3)
            self.assertEqual(summary["overall"]["pass_at"]["2"]["count"], 0)
            self.assertEqual(summary["overall"]["pass_at"]["3"]["count"], 1)

    def test_budget_threshold_stops_new_dispatch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dataset = root / "dataset"
            dataset.mkdir()
            write_dataset(dataset, hard_count=2)
            calls = []
            result = (0, [], [], [], [{"prompt_tokens": 1_000_000, "completion_tokens": 0}])

            def evaluate(*args, **kwargs):
                calls.append(args[1])
                return result

            with (
                patch.object(batch, "load_solver", return_value=object()),
                patch.object(batch, "_test_hack_agent", side_effect=evaluate),
            ):
                summary = batch.run_batch(
                    dataset_dir=dataset,
                    result_dir=root / "results",
                    split="hard",
                    solver_name="prompt",
                    model="test-model",
                    workers=1,
                    input_price=1,
                    output_price=0,
                    budget_usd=2,
                    stop_at_usd=0.5,
                    progress=False,
                )

            self.assertEqual(calls, ["3"])
            self.assertEqual(summary["overall"]["completed"], 1)
            self.assertEqual(summary["overall"]["pending"], 1)
            self.assertTrue(summary["budget"]["stopped"])
            self.assertEqual(summary["budget"]["actual_cost_usd"], 1.0)

    def test_one_shot_solver_rejects_agent_batch_before_side_effects(self):
        class OneShotSolver:
            capabilities = SolverCapabilities(hacking_feedback=False)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dataset = root / "dataset"
            dataset.mkdir()
            write_dataset(dataset)
            result_dir = root / "results"

            with (
                patch.object(batch, "load_solver", return_value=OneShotSolver()) as load,
                patch.object(
                    batch,
                    "_test_hack_agent",
                    side_effect=AssertionError("evaluation must not start"),
                ),
            ):
                with self.assertRaisesRegex(ValueError, "max_trials must be 1"):
                    batch.run_batch(
                        dataset_dir=dataset,
                        result_dir=result_dir,
                        split="hard",
                        solver_name="testcase_eval",
                        model="test-model",
                        max_trials=2,
                        progress=False,
                    )

if __name__ == "__main__":
    unittest.main()
