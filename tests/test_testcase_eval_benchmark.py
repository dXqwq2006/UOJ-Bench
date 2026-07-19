import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import run_testcase_eval_batch

from utils.testcase_eval_benchmark import (
    RunStore,
    generation_jobs,
    score,
    validate_outputs,
)
from utils.testcase_eval_executor import _java_source, run_process


def insert_problem_and_submissions(store):
    store.connection.execute(
        "INSERT INTO problems VALUES (?, ?, ?)",
        ("2000A", "Problem", "{}"),
    )
    rows = [
        ("submission_all", "w", "wrong_submission", "WRONG_ANSWER", "Python 3", "hard"),
        ("submission_all", "r1", "right_submission", "OK", "Python 3", ""),
        ("submission_all", "r2", "right_submission", "OK", "Python 3", ""),
        ("submission_lite", "w", "wrong_submission", "WRONG_ANSWER", "Python 3", "hard"),
        ("submission_lite", "r1", "right_submission", "OK", "Python 3", ""),
        ("submission_lite", "r2", "right_submission", "OK", "Python 3", ""),
    ]
    store.connection.executemany(
        """
        INSERT INTO submissions (
            dataset_name, submission_id, problem_id, submission_type,
            verdict, language, difficulty, source, metadata_json
        ) VALUES (?, ?, '2000A', ?, ?, ?, ?, 'print(1)', '{}')
        """,
        rows,
    )
    store.connection.commit()


def generation_record(task, submission_id, generation_id):
    return {
        "policy": "testcase_eval",
        "task": task,
        "problem_id": "2000A",
        "submission_id": submission_id,
        "generation_id": generation_id,
        "prompt": "prompt",
        "raw_text": "raw",
        "candidate": "1",
        "candidate_format": "raw_input",
        "message": {},
        "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
        "status": "complete",
        "error": "",
    }


def execution_record(task, submission_id, generation_id, checked_id, checked_type, output):
    return (
        "testcase_eval",
        task,
        "2000A",
        submission_id,
        generation_id,
        checked_id,
        checked_type,
        "WRONG_ANSWER" if checked_type == "wrong_submission" else "OK",
        "Python 3",
        "hard" if checked_type == "wrong_submission" else "",
        "success_run",
        output,
        "",
        0.01,
        0,
        1.0,
    )


class ComparatorTests(unittest.TestCase):
    def test_official_line_token_numeric_and_boolean_rules(self):
        self.assertTrue(validate_outputs("1  2\nYES", "1 2\nyes"))
        self.assertTrue(validate_outputs("1.0000000000005", "1"))
        self.assertFalse(validate_outputs("1.000000000002", "1"))
        self.assertFalse(validate_outputs("answer", "Answer"))
        self.assertTrue(validate_outputs("same  spacing\n", "same  spacing"))


class StoreAndScoreTests(unittest.TestCase):
    def test_manifest_is_fail_closed_and_jobs_are_resumable(self):
        with tempfile.TemporaryDirectory() as directory:
            with RunStore(Path(directory) / "run.sqlite3") as store:
                insert_problem_and_submissions(store)
                store.bind_manifest({"model": "model"})
                store.bind_manifest({"model": "model"})
                with self.assertRaisesRegex(ValueError, "different model"):
                    store.bind_manifest({"model": "other"})

                jobs = generation_jobs(
                    store,
                    policies=("testcase_eval", "prompt"),
                    tasks=(1, 2),
                    task1_generations=2,
                )
                self.assertEqual(len(jobs), 4)
                self.assertEqual(
                    {(job.policy, job.task) for job in jobs},
                    {("testcase_eval", 1), ("testcase_eval", 2), ("prompt", 2)},
                )

                store.save_generation(generation_record(1, "", 0))
                remaining = generation_jobs(
                    store,
                    policies=("testcase_eval",),
                    tasks=(1,),
                    task1_generations=2,
                )
                self.assertEqual([job.generation_id for job in remaining], [1])

    def test_score_matches_task1_union_and_task2_target_exposure(self):
        with tempfile.TemporaryDirectory() as directory:
            with RunStore(Path(directory) / "run.sqlite3") as store:
                insert_problem_and_submissions(store)
                store.bind_manifest(
                    {
                        "policies": ["testcase_eval"],
                        "tasks": [1, 2],
                        "task1_generations": 2,
                    }
                )
                store.save_generation(generation_record(1, "", 0))
                store.save_generation(generation_record(1, "", 1))
                store.save_generation(generation_record(2, "w", 0))

                records = []
                for generation_id in (0, 1):
                    records.extend(
                        [
                            execution_record(1, "", generation_id, "w", "wrong_submission", "0"),
                            execution_record(1, "", generation_id, "r1", "right_submission", "1"),
                            execution_record(1, "", generation_id, "r2", "right_submission", "1\n"),
                        ]
                    )
                records.extend(
                    [
                        execution_record(2, "w", 0, "w", "wrong_submission", "0"),
                        execution_record(2, "w", 0, "r1", "right_submission", "1"),
                        execution_record(2, "w", 0, "r2", "right_submission", "1"),
                    ]
                )
                store.connection.executemany(
                    """
                    INSERT INTO executions VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    records,
                )
                store.connection.commit()

                summary = score(store)

                self.assertTrue(summary["complete"])
                self.assertEqual(summary["expected"], {"generations": 3, "executions": 9})
                task1 = summary["policies"]["testcase_eval"]["task1"]
                self.assertEqual(task1["cov@1"], {"killed": 1, "total": 1, "ratio": 1.0})
                task2 = summary["policies"]["testcase_eval"]["task2"]
                self.assertEqual((task2["killed"], task2["total"], task2["ratio"]), (1, 1, 1.0))
                self.assertEqual(summary["usage"]["testcase_eval"]["1"]["total_tokens"], 10)


class PreflightTests(unittest.TestCase):
    def test_fixed_extractor_failure_blocks_the_strict_run(self):
        with patch(
            "solution.llm.call_llm.call_llm_details",
            return_value=("response", {"request_config": {}}, {}),
        ), patch(
            "solution.testcase_eval.solver.extract_test_input_llm",
            side_effect=RuntimeError("model unavailable"),
        ):
            with self.assertRaisesRegex(
                RuntimeError, "strict benchmark is blocked"
            ):
                run_testcase_eval_batch._preflight("model", paper=False)


class ExecutorTests(unittest.TestCase):
    def test_process_limit_and_java_class_rewrite(self):
        result = run_process(
            ["python3", "-c", "import sys; print(sys.stdin.read().strip())"],
            "hello",
            timeout=1,
        )
        self.assertEqual(result["result"], "success_run")
        self.assertEqual(result["output"], "hello\n")

        timeout = run_process(
            ["python3", "-c", "while True: pass"],
            "",
            timeout=0.05,
        )
        self.assertEqual(timeout["result"], "time_limit_exceeded")

        source = "public class Main { public Main() {} public static void main(String[] x) {} }"
        rewritten = _java_source(source, "Tmp123")
        self.assertIn("public class Tmp123", rewritten)
        self.assertIn("public Tmp123()", rewritten)


if __name__ == "__main__":
    unittest.main()
