import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from utils.codecontests_plus import DATASET_KEY, _create_ccplus_schema, score as cc_score
from utils.test_package_benchmark import (
    bind_package_contract,
    package_metrics,
    publish_package,
    run_solver_packages,
    sync_generation_package,
)
from utils.testcase_eval_benchmark import RunStore, score as tce_score


class TestPackageBenchmarkTests(unittest.TestCase):
    def _store(self, directory):
        store = RunStore(Path(directory) / "results.sqlite3")
        store.connection.execute(
            "INSERT INTO problems VALUES (?, ?, ?)", ("p", "PUBLIC", "{}")
        )
        store.connection.commit()
        return store

    def test_package_runner_forwards_only_public_resource_metadata(self):
        from solution.api import (
            SolverCapabilities,
            SolverTurn,
            TestCaseCandidate,
            TestPackageCandidate,
        )

        seen = []

        class Session:
            initial_request = {"public": True}

            def next(self):
                return SolverTurn(
                    candidate=TestPackageCandidate(
                        tests=(TestCaseCandidate("1\n"),),
                        artifact={"release_test_paths": ["package/tests/01.in"]},
                    ),
                    raw_text="",
                    message={},
                    usage={},
                )

        class Solver:
            capabilities = SolverCapabilities(test_package=True)

            def start_test_package(self, task):
                seen.append(task)
                return Session()

        with tempfile.TemporaryDirectory() as directory:
            with self._store(directory) as store:
                store.connection.execute(
                    "UPDATE problems SET metadata_json = ? WHERE problem_id = ?",
                    (
                        json.dumps({
                            "time_limit_ms": 2000,
                            "memory_limit_mb": 256,
                            "validator": "HIDDEN",
                        }),
                        "p",
                    ),
                )
                store.connection.commit()
                with patch("solution.load_solver", return_value=Solver()):
                    result = run_solver_packages(
                        store,
                        policy="solver",
                        model="model",
                        dataset="testcase-eval",
                        fidelity="adapted",
                        call_contract="package",
                        workers=1,
                    )

        self.assertEqual(result["complete"], 1)
        self.assertEqual(
            seen[0].metadata,
            {
                "benchmark": "testcase-eval",
                "time_limit_ms": 2000,
                "memory_limit_mb": 256,
            },
        )

    def test_publish_mirrors_ordered_inputs_to_hidden_jury_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            with self._store(directory) as store:
                status = publish_package(
                    store,
                    policy="solver",
                    problem_id="p",
                    tests=[
                        {"content": "first\n", "source_path": "package/tests/a.in"},
                        {"content": "second\n", "source_path": "package/tests/b.in"},
                    ],
                    fidelity="adapted",
                )
                rows = list(
                    store.connection.execute(
                        "SELECT generation_id, raw_text, candidate, candidate_format "
                        "FROM generations ORDER BY generation_id"
                    )
                )
        self.assertEqual(status, "complete")
        self.assertEqual(
            [tuple(row) for row in rows],
            [
                (0, "", "first\n", "raw_input"),
                (1, "", "second\n", "raw_input"),
            ],
        )

    def test_overflow_rejects_the_whole_package_without_truncation(self):
        with tempfile.TemporaryDirectory() as directory:
            with self._store(directory) as store:
                status = publish_package(
                    store,
                    policy="solver",
                    problem_id="p",
                    tests=[str(index) for index in range(51)],
                    fidelity="adapted",
                )
                run = store.connection.execute(
                    "SELECT status, declared_test_count FROM package_runs"
                ).fetchone()
                stored = store.connection.execute(
                    "SELECT COUNT(*) FROM package_tests"
                ).fetchone()[0]
                mirrored = store.connection.execute(
                    "SELECT COUNT(*) FROM generations"
                ).fetchone()[0]
        self.assertEqual(status, "over_limit")
        self.assertEqual(tuple(run), ("over_limit", 51))
        self.assertEqual((stored, mirrored), (0, 0))

    def test_native_sync_preserves_prompt_and_call_slots_byte_for_byte(self):
        prompts = ["exact prompt 0\n", "exact prompt 1\n"]
        with tempfile.TemporaryDirectory() as directory:
            with self._store(directory) as store:
                for index, prompt in enumerate(prompts):
                    store.save_generation(
                        {
                            "policy": "native",
                            "task": 1,
                            "problem_id": "p",
                            "submission_id": "",
                            "generation_id": index,
                            "prompt": prompt,
                            "raw_text": "raw",
                            "candidate": str(index),
                            "candidate_format": "raw_input",
                            "message": {},
                            "usage": {},
                            "status": "complete",
                            "error": "",
                        }
                    )
                result = sync_generation_package(
                    store, policy="native", fidelity="native", expected_calls=2
                )
                copied = [
                    row[0] for row in store.connection.execute(
                        "SELECT prompt FROM package_calls ORDER BY call_id"
                    )
                ]
        self.assertEqual(result, {"packages": 1, "calls": 2, "tests": 2, "incomplete": 0})
        self.assertEqual(copied, prompts)

    def test_ccplus_score_accepts_variable_package_size(self):
        with tempfile.TemporaryDirectory() as directory:
            with self._store(directory) as store:
                _create_ccplus_schema(store)
                store.connection.execute(
                    "UPDATE problems SET metadata_json = ? WHERE problem_id = 'p'",
                    (json.dumps({
                        "published_true_positive_rate": 1.0,
                        "published_true_negative_rate": 1.0,
                    }),),
                )
                store.connection.executemany(
                    "INSERT INTO submissions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        (DATASET_KEY, "r", "p", "right_submission", "", "PY3", "", "RIGHT", "{}"),
                        (DATASET_KEY, "w", "p", "wrong_submission", "", "PY3", "", "WRONG", "{}"),
                    ],
                )
                store.connection.executemany(
                    "INSERT INTO ccplus_program_audits VALUES (?, ?, ?, ?, ?, ?)",
                    [
                        ("r", "python3", "complete", "", "test", time.time()),
                        ("w", "python3", "complete", "", "test", time.time()),
                    ],
                )
                store.bind_manifest({
                    "benchmark": "codecontests-plus-verified-adapted",
                    "policies": ["solver"],
                    "tasks": [1],
                })
                bind_package_contract(
                    store,
                    policy="solver",
                    dataset="codecontests-plus",
                    fidelity="adapted",
                    call_contract="package",
                )
                publish_package(
                    store,
                    policy="solver",
                    problem_id="p",
                    tests=["kill", "later"],
                    fidelity="adapted",
                )
                now = time.time()
                store.connection.executemany(
                    "INSERT INTO ccplus_candidates VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        ("solver", "p", 0, 1, "complete", "answer", "", now),
                        ("solver", "p", 1, 1, "complete", "answer", "", now),
                    ],
                )
                executions = []
                for index in range(2):
                    for submission, role, result in (
                        ("r", "right_submission", "accepted"),
                        ("w", "wrong_submission", "wrong_answer"),
                    ):
                        executions.append((
                            "solver", 1, "p", "", index, submission, role, "", "PY3", "",
                            result, "", "", 0.01, 1, now,
                        ))
                store.connection.executemany(
                    "INSERT INTO executions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    executions,
                )
                store.connection.commit()
                summary = cc_score(store)

        self.assertTrue(summary["complete"])
        self.assertEqual(summary["expected_generations"], 2)
        self.assertEqual(summary["problems"]["p"]["tests"], 2)
        self.assertEqual(summary["macro"]["true_negative_rate"], 1.0)

    def test_ccplus_empty_package_vacuously_preserves_all_programs(self):
        with tempfile.TemporaryDirectory() as directory:
            with self._store(directory) as store:
                _create_ccplus_schema(store)
                store.connection.execute(
                    "UPDATE problems SET metadata_json = ? WHERE problem_id = 'p'",
                    (
                        json.dumps(
                            {
                                "published_true_positive_rate": 1.0,
                                "published_true_negative_rate": 1.0,
                            }
                        ),
                    ),
                )
                store.connection.executemany(
                    "INSERT INTO submissions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        (
                            DATASET_KEY,
                            "r",
                            "p",
                            "right_submission",
                            "",
                            "PY3",
                            "",
                            "RIGHT",
                            "{}",
                        ),
                        (
                            DATASET_KEY,
                            "w",
                            "p",
                            "wrong_submission",
                            "",
                            "PY3",
                            "",
                            "WRONG",
                            "{}",
                        ),
                    ],
                )
                store.connection.executemany(
                    "INSERT INTO ccplus_program_audits VALUES (?, ?, ?, ?, ?, ?)",
                    [
                        ("r", "python3", "complete", "", "test", time.time()),
                        ("w", "python3", "complete", "", "test", time.time()),
                    ],
                )
                store.bind_manifest(
                    {
                        "benchmark": "codecontests-plus-verified-adapted",
                        "policies": ["solver"],
                        "tasks": [1],
                    }
                )
                bind_package_contract(
                    store,
                    policy="solver",
                    dataset="codecontests-plus",
                    fidelity="adapted",
                    call_contract="package",
                )
                status = publish_package(
                    store,
                    policy="solver",
                    problem_id="p",
                    tests=[],
                    fidelity="adapted",
                )
                store.connection.commit()
                summary = cc_score(store)
                metrics = package_metrics(
                    store, dataset="codecontests-plus", policy="solver"
                )

        self.assertEqual(status, "no_valid_tests")
        self.assertEqual(summary["problems"]["p"]["correct_accepted"], 1)
        self.assertEqual(summary["problems"]["p"]["true_positive_rate"], 1.0)
        self.assertEqual(summary["problems"]["p"]["wrong_rejected"], 0)
        self.assertEqual(summary["problems"]["p"]["true_negative_rate"], 0.0)
        self.assertEqual(metrics["correct_preserved"], 1)
        self.assertEqual(metrics["correct_preservation_rate"], 1.0)
        self.assertEqual(metrics["union_coverage"]["killed"], 0)

    def test_tce_metrics_use_hidden_jury_and_ordered_union_coverage(self):
        with tempfile.TemporaryDirectory() as directory:
            with self._store(directory) as store:
                store.connection.executemany(
                    "INSERT INTO submissions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        ("submission_all", "r", "p", "right_submission", "", "Python 3", "", "RIGHT SECRET", "{}"),
                        ("submission_all", "w", "p", "wrong_submission", "", "Python 3", "", "WRONG SECRET", "{}"),
                    ],
                )
                store.bind_manifest({"policies": ["solver"], "tasks": [1]})
                bind_package_contract(
                    store,
                    policy="solver",
                    dataset="testcase-eval",
                    fidelity="adapted",
                    call_contract="package",
                )
                publish_package(
                    store,
                    policy="solver",
                    problem_id="p",
                    tests=["kill", "later"],
                    fidelity="adapted",
                )
                now = time.time()
                executions = []
                for index in range(2):
                    for submission, role, output in (
                        ("r", "right_submission", "answer"),
                        ("w", "wrong_submission", "wrong"),
                    ):
                        executions.append(
                            (
                                "solver", 1, "p", "", index, submission, role, "",
                                "Python 3", "", "success_run", output, "", 0.01, 1, now,
                            )
                        )
                store.connection.executemany(
                    "INSERT INTO executions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    executions,
                )
                store.connection.commit()
                metrics = package_metrics(
                    store, dataset="testcase-eval", policy="solver"
                )
                summary = tce_score(store)
        self.assertEqual(metrics["valid_tests"], 2)
        self.assertEqual(metrics["valid_rate"], 1.0)
        self.assertEqual(metrics["coverage"]["cov@1"], {"killed": 1, "total": 1, "ratio": 1.0})
        self.assertEqual(metrics["union_coverage"], metrics["coverage"]["cov@50"])
        self.assertTrue(summary["complete"])
        self.assertEqual(summary["expected"], {"generations": 2, "executions": 4})
        self.assertEqual(
            summary["policies"]["solver"]["task1"]["package"],
            {"killed": 1, "total": 1, "ratio": 1.0},
        )


if __name__ == "__main__":
    unittest.main()
