import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import run_testcase_eval_batch

from utils.testcase_eval_benchmark import (
    DATASET_ARTIFACT_SHA256,
    GenerationJob,
    RunStore,
    _load_dataset,
    decode_execution_output,
    encode_execution_output,
    generate,
    generation_jobs,
    score,
    validate_outputs,
)
from solution.api import SolverCapabilities
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
    def test_offline_snapshot_must_match_pinned_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            parquet = (
                Path(directory)
                / "datasets--TestCase-Eval--problem"
                / "snapshots"
                / "b5cc0cc4589f5e38c1b010c24a4c5f513009278e"
                / "data"
                / "train.parquet"
            )
            parquet.parent.mkdir(parents=True)
            parquet.write_bytes(b"not the pinned dataset")
            with self.assertRaisesRegex(
                ValueError,
                DATASET_ARTIFACT_SHA256["problem"],
            ):
                _load_dataset("problem", None, directory)

    def test_official_line_token_numeric_and_boolean_rules(self):
        self.assertTrue(validate_outputs("1  2\nYES", "1 2\nyes"))
        self.assertTrue(validate_outputs("1.0000000000005", "1"))
        self.assertFalse(validate_outputs("1.000000000002", "1"))
        self.assertFalse(validate_outputs("answer", "Answer"))
        self.assertTrue(validate_outputs("same  spacing\n", "same  spacing"))

    def test_execution_output_encoding_is_lossless_and_backward_compatible(self):
        self.assertEqual(decode_execution_output("legacy\n"), "legacy\n")
        self.assertEqual(decode_execution_output(b"legacy bytes\n"), "legacy bytes\n")

        output = ("1234567890 " * 10_000) + "\n"
        encoded = encode_execution_output(output)
        self.assertIsInstance(encoded, memoryview)
        self.assertLess(len(encoded), len(output.encode("utf-8")))
        self.assertEqual(decode_execution_output(encoded), output)

        self.assertEqual(encode_execution_output(""), "")
        self.assertEqual(encode_execution_output("x"), "x")


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
                    model="model",
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
                    model="model",
                    policies=("testcase_eval",),
                    tasks=(1,),
                    task1_generations=2,
                )
                self.assertEqual([job.generation_id for job in remaining], [1])

    def test_jobs_discover_new_policy_from_solver_capabilities(self):
        class PaperSolver:
            capabilities = SolverCapabilities(
                fault_coverage=True,
                fault_exposure=True,
            )

        with tempfile.TemporaryDirectory() as directory:
            with RunStore(Path(directory) / "run.sqlite3") as store:
                insert_problem_and_submissions(store)
                with patch(
                    "utils.testcase_eval_benchmark.load_solver",
                    return_value=PaperSolver(),
                ) as loader:
                    jobs = generation_jobs(
                        store,
                        model="model",
                        policies=("paper_solver",),
                        tasks=(1, 2),
                        task1_generations=2,
                    )

        loader.assert_called_once_with("paper_solver", "model")
        self.assertEqual(len(jobs), 3)
        self.assertEqual({job.policy for job in jobs}, {"paper_solver"})
        self.assertEqual({job.task for job in jobs}, {1, 2})

    def test_replicated_generation_persists_the_first_success_only(self):
        job = GenerationJob(
            policy="testcase_eval",
            task=2,
            problem_id="2000A",
            problem_statement="Problem",
            submission_id="w",
            submission_code="print(1)",
            submission_language="Python 3",
            generation_id=0,
            metadata={},
        )
        plans = iter(
            (
                (0.04, "complete", "late"),
                (0.005, "request_error", ""),
                (0.01, "complete", "first"),
                (0.02, "request_error", ""),
            )
        )
        plan_lock = threading.Lock()

        def fake_generate(_job, _model):
            with plan_lock:
                delay, status, candidate = next(plans)
            time.sleep(delay)
            record = generation_record(2, "w", 0)
            record["status"] = status
            record["candidate"] = candidate
            record["error"] = "" if status == "complete" else "gateway error"
            return record

        with tempfile.TemporaryDirectory() as directory:
            with RunStore(Path(directory) / "run.sqlite3") as store, patch(
                "utils.testcase_eval_benchmark.generation_jobs",
                return_value=[job],
            ), patch(
                "utils.testcase_eval_benchmark._generate_one",
                side_effect=fake_generate,
            ) as request:
                counts = generate(
                    store,
                    model="model",
                    policies=("testcase_eval",),
                    tasks=(2,),
                    workers=1,
                    request_replicas=4,
                )
                row = store.connection.execute(
                    "SELECT status, candidate FROM generations"
                ).fetchone()

        self.assertEqual(
            counts,
            {"scheduled": 1, "complete": 1, "request_error": 0},
        )
        self.assertEqual((row["status"], row["candidate"]), ("complete", "first"))
        self.assertEqual(request.call_count, 4)

    def test_generation_manifest_rejects_mixed_pipeline_signatures(self):
        job = GenerationJob(
            policy="testcase_eval",
            task=2,
            problem_id="2000A",
            problem_statement="Problem",
            submission_id="w",
            submission_code="print(1)",
            submission_language="Python 3",
            generation_id=0,
            metadata={},
        )

        def signed_record(signature):
            record = generation_record(2, "w", 0)
            record["message"] = {
                "pipeline_identity": {
                    "pipeline_signature_sha256": signature,
                }
            }
            return record

        first = "a" * 64
        second = "b" * 64
        with tempfile.TemporaryDirectory() as directory:
            with RunStore(Path(directory) / "run.sqlite3") as store, patch(
                "utils.testcase_eval_benchmark.generation_jobs",
                return_value=[job],
            ), patch(
                "utils.testcase_eval_benchmark._generate_one",
                side_effect=(signed_record(first), signed_record(second)),
            ):
                generate(
                    store,
                    model="model",
                    policies=("testcase_eval",),
                    tasks=(2,),
                    workers=1,
                )
                with self.assertRaisesRegex(ValueError, "mixes pipeline identities"):
                    generate(
                        store,
                        model="model",
                        policies=("testcase_eval",),
                        tasks=(2,),
                        workers=1,
                    )
                self.assertEqual(
                    store.manifest()[
                        "solver_pipeline_signature:testcase_eval"
                    ],
                    first,
                )

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
                        execution_record(
                            2,
                            "w",
                            0,
                            "r1",
                            "right_submission",
                            encode_execution_output("1\n" * 1_000),
                        ),
                        execution_record(
                            2,
                            "w",
                            0,
                            "r2",
                            "right_submission",
                            "1\n" * 1_000,
                        ),
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

    def test_gemini_paper_preflight_requires_high_thinking(self):
        with patch(
            "scripts.run_testcase_eval_batch.require_paper_generation_settings"
        ), patch(
            "solution.llm.call_llm.call_llm_details",
            return_value=(
                "```plaintext\n1\n```",
                {
                    "request_config": {
                        "max_output_tokens": 18_000,
                        "temperature": 1.0,
                    }
                },
                {},
            ),
        ), patch(
            "solution.testcase_eval.solver.extract_test_input_llm",
            return_value=("1", {"model": "gpt-4.1-mini"}, {}),
        ):
            with self.assertRaisesRegex(RuntimeError, "thinking_config"):
                run_testcase_eval_batch._preflight(
                    "gemini-3.1-pro-preview", paper=True
                )

    def test_strict_task1_policies_skip_extractor_preflight(self):
        with patch(
            "solution.llm.call_llm.call_llm_details",
            return_value=("```plaintext\n1\n```", {"request_config": {}}, {}),
        ), patch(
            "solution.testcase_eval.solver.extract_test_input_llm",
            side_effect=AssertionError("extractor must not run"),
        ):
            result = run_testcase_eval_batch._preflight(
                "model",
                paper=False,
                policies=(
                    "testcase_eval_task1_cot",
                    "testcase_eval_task1_direct",
                ),
            )

        self.assertNotIn("extractor", result)


class ExecutorTests(unittest.TestCase):
    def test_process_limit_and_java_class_rewrite(self):
        result = run_process(
            ["python3", "-c", "import sys; print(sys.stdin.read().strip())"],
            "hello",
            timeout=1,
        )
        self.assertEqual(result["result"], "success_run")
        self.assertEqual(result["output"], "hello\n")

        pipe = run_process(
            ["python3", "-c", "import os; print(os.fstat(0).st_size)"],
            "hello",
            timeout=1,
        )
        self.assertEqual(pipe["output"], "0\n")

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
