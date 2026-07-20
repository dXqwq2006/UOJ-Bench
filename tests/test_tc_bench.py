import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from utils.tc_bench import (
    DATASET_PARQUET_SHA256,
    RunStore,
    _load_dataset,
    _snapshot_stats,
    export_jsonl,
    generation_jobs,
    normalize_output,
    outputs_equal,
    prepare_dataset,
    refresh_oracles,
    score,
)


def fixture_row(problem_id="Example"):
    return {
        "problem_id": problem_id,
        "description": "# Example\nRead one integer.",
        "time_limit": 1000,
        "memory_limit": 256,
        "sample_input": "1\n",
        "sample_output": "1\n",
        "solutions": [{"code": "right", "lang": "cpp"}],
        "wrong_solutions": [
            {"code": "wrong0", "lang": "cpp", "output_str": "W"},
            {"code": "wrong1", "lang": "c", "output_str": "A"},
        ],
        "rank": 2,
    }


def generation_record(generation_id):
    return {
        "policy": "testcase_eval_task1_cot",
        "task": 1,
        "problem_id": "tc:0000",
        "submission_id": "",
        "generation_id": generation_id,
        "prompt": "prompt",
        "raw_text": "raw",
        "candidate": str(generation_id + 1),
        "candidate_format": "raw_input",
        "message": {},
        "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
        "status": "complete",
        "error": "",
    }


def execution(generation_id, checked_id, checked_type, result, output):
    return (
        "testcase_eval_task1_cot",
        1,
        "tc:0000",
        "",
        generation_id,
        checked_id,
        checked_type,
        "",
        "cpp",
        "",
        result,
        output,
        "",
        0.01,
        0,
        1.0,
    )


class TCBenchDatasetTests(unittest.TestCase):
    def test_offline_parquet_must_match_pinned_sha256(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.parquet"
            path.write_bytes(b"not the pinned dataset")
            with self.assertRaisesRegex(ValueError, DATASET_PARQUET_SHA256):
                _load_dataset(None, path)

    def test_snapshot_shape_stable_keys_and_rank_budget(self):
        rows = [fixture_row(None)]
        self.assertEqual(
            _snapshot_stats(rows),
            {
                "problems": 1,
                "rank_sum": 2,
                "correct_programs": 1,
                "wrong_programs": 2,
                "cpp_programs": 2,
                "c_programs": 1,
                "null_problem_ids": 1,
            },
        )
        with tempfile.TemporaryDirectory() as directory:
            with RunStore(Path(directory) / "run.sqlite3") as store, patch(
                "utils.tc_bench._load_dataset", return_value=rows
            ):
                summary = prepare_dataset(store, validate_snapshot=False)
                jobs = generation_jobs(
                    store,
                    policy="testcase_eval_task1_cot",
                    max_generations_per_problem=3,
                )
                store.save_generation(generation_record(0))
                remaining = generation_jobs(
                    store,
                    policy="testcase_eval_task1_cot",
                    max_generations_per_problem=3,
                )

                self.assertEqual(summary["problem_keys"], ["tc:0000"])
                self.assertEqual(summary["generations"], 10)
                self.assertEqual([job.generation_id for job in jobs], [0, 1, 2])
                self.assertEqual([job.generation_id for job in remaining], [1, 2])


class TCBenchScoringTests(unittest.TestCase):
    def test_public_comparator(self):
        self.assertEqual(normalize_output(" 1  \n\n 2 \n"), "1 2")
        self.assertTrue(outputs_equal("1.0000001\n", "1.0000002"))
        self.assertFalse(outputs_equal("1.000002", "1.0"))
        self.assertFalse(outputs_equal("YES", "yes"))

    def test_invalid_candidate_stays_in_pass_rate_denominator_and_export(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with RunStore(root / "run.sqlite3") as store, patch(
                "utils.tc_bench._load_dataset", return_value=[fixture_row()]
            ):
                prepare_dataset(store, validate_snapshot=False)
                store.bind_manifest(
                    {
                        "model": "model",
                        "policies": ["testcase_eval_task1_cot"],
                        "tasks": [1],
                        "tc_max_generations_per_problem": 2,
                    }
                )
                store.save_generation(generation_record(0))
                store.save_generation(generation_record(1))
                store.connection.executemany(
                    "INSERT INTO materializations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        ("testcase_eval_task1_cot", 1, "tc:0000", "", 0, "1", "complete", "", 1.0),
                        ("testcase_eval_task1_cot", 1, "tc:0000", "", 1, "ERROR", "invalid_input", "bad", 1.0),
                    ],
                )
                store.connection.executemany(
                    "INSERT INTO executions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        execution(0, "tc:0000:r:000", "right_submission", "success_run", "1\n"),
                        execution(0, "tc:0000:w:000", "wrong_submission", "success_run", "0\n"),
                        execution(0, "tc:0000:w:001", "wrong_submission", "success_run", "1\n"),
                    ],
                )
                store.connection.commit()
                self.assertEqual(
                    refresh_oracles(store),
                    {"candidates": 2, "valid": 1, "invalid": 1},
                )
                summary = score(store)
                exported = export_jsonl(store, root / "tests")

            metric = summary["macro"]["1xrank"]
            self.assertTrue(summary["complete"])
            self.assertEqual(metric, {"pass_rate": 0.5, "hack_rate": 0.5})
            self.assertEqual(exported, {"tc:0000": 1})
            values = [
                json.loads(line)
                for line in (root / "tests" / "tests-tc-0000.jsonl").read_text().splitlines()
            ]
            self.assertEqual(values, [{"input": "1", "output": "1\n"}])


if __name__ == "__main__":
    unittest.main()
