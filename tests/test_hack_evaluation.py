import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import run_hack_evaluation_batch as evaluation
from scripts import run_hack_rollout_batch as rollout


def prepare(root: Path, *, imported: bool = False) -> tuple[Path, Path]:
    dataset = root / "dataset"
    dataset.mkdir()
    (dataset / "problems.json").write_text(json.dumps([
        {"problem_id": "1", "statement_en": "one", "difficulty": "easy", "hackable": 1},
        {"problem_id": "2", "statement_en": "two", "difficulty": "hard", "hackable": 1},
    ]))
    (dataset / "sampled_large_submission_pairs.json").write_text(json.dumps([
        {"problem_id": "1", "wrong_code": "wrong one", "language": "C++20"},
    ]))
    (dataset / "hacks.json").write_text(json.dumps([
        {"problem_id": "2", "wrong_code": "wrong two", "language": "C++14"},
    ]))
    samples = rollout.load_samples(dataset, "all", 0)
    run = rollout._run_identity(samples, dataset.resolve(), "all", "prompt", "model", 0)
    rollout_dir = root / "rollout"
    (rollout_dir / "samples").mkdir(parents=True)
    (rollout_dir / "manifest.json").write_text(json.dumps({"schema_version": 1, "run": run}))
    source_dir = root / "source"
    for sample in samples:
        record = {
            "status": "completed",
            "sample": sample.public_record(),
            "candidate": "print(1)\n",
            "provenance": "generated",
        }
        if imported and sample.sample_id == "easy-0000":
            record.update({"provenance": "imported_agent_result", "source_result_dir": str(source_dir)})
            (source_dir / "samples").mkdir(parents=True)
            (source_dir / "samples" / f"{sample.sample_id}.json").write_text(json.dumps({
                "messages": [{"role": "user", "content": "prompt"}, {"role": "assistant", "content": "```python\nprint(1)\n```"}],
                "judge_results": [{"result": {"score": 1}}],
            }))
        (rollout_dir / "samples" / f"{sample.sample_id}.json").write_text(json.dumps(record))
    return dataset, rollout_dir


class EvaluationTests(unittest.TestCase):
    def test_evaluates_and_resumes_without_llm(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dataset, rollout_dir = prepare(root, imported=True)
            calls = []

            def judge(_client, sample, _candidate):
                calls.append(sample.sample_id)
                return {"result": {"score": 0}}

            with patch.object(evaluation, "_new_client", return_value=object()), patch.object(
                evaluation, "_judge_candidate", side_effect=judge
            ):
                summary = evaluation.run_batch(
                    dataset_dir=dataset,
                    rollout_dir=rollout_dir,
                    result_dir=root / "result",
                    workers=1,
                    resume=True,
                    progress=False,
                )
                resumed = evaluation.run_batch(
                    dataset_dir=dataset,
                    rollout_dir=rollout_dir,
                    result_dir=root / "result",
                    workers=1,
                    resume=True,
                    progress=False,
                )

            self.assertEqual(summary["overall"]["completed"], 2)
            self.assertEqual(summary["overall"]["successes"], 1)
            self.assertEqual(calls, ["hard-0000"])
            self.assertEqual(resumed["overall"]["completed"], 2)

    def test_quota_error_stops_new_dispatch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dataset, rollout_dir = prepare(root)
            with patch.object(evaluation, "_new_client", return_value=object()), patch.object(
                evaluation, "_judge_candidate", side_effect=RuntimeError(evaluation.QUOTA_ERROR)
            ):
                summary = evaluation.run_batch(
                    dataset_dir=dataset,
                    rollout_dir=rollout_dir,
                    result_dir=root / "result",
                    workers=1,
                    resume=True,
                    progress=False,
                )
            self.assertEqual(summary["halt_reason"], "quota")
            self.assertEqual(summary["overall"]["retryable_error"], 1)
            self.assertEqual(summary["overall"]["pending_evaluation"], 2)

    def test_invalid_first_turn_counts_in_pass_at_1_denominator(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dataset, rollout_dir = prepare(root, imported=True)
            path = rollout_dir / "samples" / "hard-0000.json"
            record = json.loads(path.read_text())
            record["candidate"] = None
            path.write_text(json.dumps(record))
            with patch.object(
                evaluation, "_new_client", side_effect=AssertionError("no UOJ call expected")
            ):
                summary = evaluation.run_batch(
                    dataset_dir=dataset,
                    rollout_dir=rollout_dir,
                    result_dir=root / "result",
                    workers=1,
                    resume=True,
                    progress=False,
                )
            self.assertEqual(summary["overall"]["completed"], 1)
            self.assertEqual(summary["overall"]["failed"], 1)
            self.assertEqual(summary["overall"]["pass_at_1"], 0.5)


if __name__ == "__main__":
    unittest.main()
