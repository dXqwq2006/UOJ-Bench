import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from utils.codecontests_plus import (
    COMPILER_PROFILES,
    RunStore,
    _execute_program,
    _selected_records,
    _snapshot_stats,
    _validate_problem,
    audit_programs,
    execute_pending,
    export_jsonl,
    generation_jobs,
    prepare_dataset,
    require_compile_audit,
    score,
)


PROBLEM_ID = "ccp:Codeforces:1_A"


def fixture_row(*, tpr=0.95, tnr=0.91):
    return {
        "source": "Codeforces",
        "id": "1_A",
        "title": "Example",
        "description": "Read one integer.",
        "time_limit": 1000,
        "memory_limit": 256,
        "validator": "validator",
        "generator": "generator",
        "generator_cmd": "./gen",
        "checker": "checker",
        "correct_submissions": [
            {"code": "right0", "language": "CPP"},
            {"code": "right1", "language": "PY3"},
        ],
        "incorrect_submissions": [
            {"code": "wrong0", "language": "CPP"},
            {"code": "wrong1", "language": "JAVA"},
        ],
        "test_cases": [{"input": "1\n", "output": "1\n"}],
        "true_positive_rate": tpr,
        "true_negative_rate": tnr,
    }


def fixture_problem(problem_id, *, tpr=0.95, tnr=0.91):
    row = fixture_row(tpr=tpr, tnr=tnr)
    row["id"] = problem_id
    return row


def generation_record(generation_id):
    return {
        "policy": "testcase_eval_task1_cot",
        "task": 1,
        "problem_id": PROBLEM_ID,
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


def execution(checked_id, checked_type, result):
    return (
        "testcase_eval_task1_cot",
        1,
        PROBLEM_ID,
        "",
        0,
        checked_id,
        checked_type,
        "",
        "CPP",
        "",
        result,
        "",
        "",
        0.01,
        0,
        1.0,
    )


def audit_all_programs(store):
    store.connection.executemany(
        "INSERT INTO ccplus_program_audits VALUES (?, ?, ?, ?, ?, ?)",
        [
            (row["submission_id"], "profile", "complete", "", "judge", 1.0)
            for row in store.connection.execute(
                "SELECT submission_id FROM submissions"
            )
        ],
    )


class CodeContestsPlusDatasetTests(unittest.TestCase):
    def test_supported_compiler_profiles_are_explicit(self):
        self.assertEqual(COMPILER_PROFILES["CPP"], ("cpp-gnu++17",))
        self.assertEqual(COMPILER_PROFILES["PY2"], ("python2",))
        self.assertEqual(COMPILER_PROFILES["PY3"], ("python3",))
        self.assertEqual(COMPILER_PROFILES["JAVA"], ("java21",))

    def test_program_sample_is_bounded_deterministic_and_excludes_unknown(self):
        row = fixture_row()
        row["correct_submissions"] = [
            {"code": f"code-{index}", "language": "CPP"}
            for index in range(101)
        ] + [{"code": "php", "language": "UNKNOWN"}]

        first = _selected_records(row, "correct_submissions")
        second = _selected_records(row, "correct_submissions")

        self.assertEqual(first, second)
        self.assertEqual(len(first), 100)
        self.assertNotIn(101, {source_index for source_index, _ in first})

    def test_verified_filter_schema_and_fixed_budget(self):
        rows = [fixture_row(tnr=0.89), fixture_row()]
        self.assertEqual(
            _snapshot_stats([rows[1]]),
            {
                "problems": 1,
                "available_correct_programs": 2,
                "available_wrong_programs": 2,
                "correct_programs": 2,
                "wrong_programs": 2,
                "ignored_unknown_language_programs": 0,
                "cpp_programs": 2,
                "py3_programs": 1,
                "java_programs": 1,
            },
        )
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "part.parquet"
            artifact.write_bytes(b"fixture")
            with RunStore(Path(directory) / "run.sqlite3") as store, patch(
                "utils.codecontests_plus._load_dataset", return_value=rows
            ):
                summary = prepare_dataset(
                    store,
                    dataset_parquets=(artifact,),
                    smoke_problems=1,
                )
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
                manifest = store.manifest()

        self.assertEqual(summary["problem_keys"], [PROBLEM_ID])
        self.assertEqual(summary["generations"], 20)
        self.assertEqual([job.generation_id for job in jobs], [0, 1, 2])
        self.assertEqual([job.generation_id for job in remaining], [1, 2])
        self.assertNotIn("problem_sampling", manifest)
        self.assertEqual(
            manifest["dataset_revisions"]["codecontests_plus_verified"][
                "local_artifacts"
            ][0]["sha256"],
            hashlib.sha256(b"fixture").hexdigest(),
        )

    def test_problem_sample_is_fixed_uniform_and_order_independent(self):
        rows = [fixture_problem(f"{index}_A") for index in range(12)]
        rows.append(fixture_problem("unverified", tnr=0.89))

        def prepare(dataset):
            with tempfile.TemporaryDirectory() as directory:
                with RunStore(Path(directory) / "run.sqlite3") as store, patch(
                    "utils.codecontests_plus._load_dataset", return_value=dataset
                ):
                    summary = prepare_dataset(
                        store,
                        sample_problems=5,
                        sample_seed="fixed-seed",
                    )
                    return summary, store.manifest()

        first, first_manifest = prepare(rows)
        second, second_manifest = prepare(list(reversed(rows)))

        self.assertEqual(first["problem_keys"], second["problem_keys"])
        self.assertEqual(len(first["problem_keys"]), 5)
        sampling = first_manifest["problem_sampling"]
        self.assertEqual(sampling["method"], "sha256-minhash")
        self.assertEqual(sampling["seed"], "fixed-seed")
        self.assertEqual(sampling["verified_population"], 12)
        self.assertEqual(sampling["sample_size"], 5)
        self.assertEqual(
            sampling["selected_problem_keys"],
            second_manifest["problem_sampling"]["selected_problem_keys"],
        )

    def test_problem_sample_rejects_conflicting_or_oversized_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            with RunStore(Path(directory) / "run.sqlite3") as store, patch(
                "utils.codecontests_plus._load_dataset",
                return_value=[fixture_row()],
            ):
                with self.assertRaisesRegex(ValueError, "cannot be combined"):
                    prepare_dataset(store, sample_problems=1, smoke_problems=1)
                with self.assertRaisesRegex(
                    ValueError, "exceeds the Verified population"
                ):
                    prepare_dataset(store, sample_problems=2)

    def test_compile_audit_may_exclude_failures_but_keeps_an_oracle(self):
        with tempfile.TemporaryDirectory() as directory:
            with RunStore(Path(directory) / "run.sqlite3") as store, patch(
                "utils.codecontests_plus._load_dataset", return_value=[fixture_row()]
            ):
                prepare_dataset(store)
                submissions = list(
                    store.connection.execute(
                        "SELECT submission_id, submission_type FROM submissions"
                    )
                )
                store.connection.executemany(
                    "INSERT INTO ccplus_program_audits VALUES (?, ?, ?, ?, ?, ?)",
                    [
                        (
                            row["submission_id"],
                            "cpp-gnu++17",
                            "complete" if index else "compile_error",
                            "" if index else "bad source",
                            "judge",
                            1.0,
                        )
                        for index, row in enumerate(submissions)
                    ],
                )
                store.connection.commit()

                require_compile_audit(store)

    @patch(
        "utils.codecontests_plus.preflight",
        return_value={
            "profiles": {"codecontests-plus": {"fingerprint": "a" * 64}}
        },
    )
    def test_completed_compile_audit_resume_is_a_noop(self, _preflight):
        expected = {"scheduled": 4, "complete": 4, "compile_error": 0}
        with tempfile.TemporaryDirectory() as directory:
            with RunStore(Path(directory) / "run.sqlite3") as store, patch(
                "utils.codecontests_plus._load_dataset", return_value=[fixture_row()]
            ):
                prepare_dataset(store)
                audit_all_programs(store)
                store.bind_manifest({"ccplus_compile_audit": expected})

                summary = audit_programs(
                    store,
                    base_url="http://judge",
                    workers=1,
                )

        self.assertEqual(
            {key: summary[key] for key in expected},
            expected,
        )

    @patch("utils.codecontests_plus._batch_results")
    def test_published_validator_is_authoritative(self, batch):
        batch.return_value = {
            "0": {"id": "0", "status": "exited"},
            "1": {"id": "1", "status": "nonzero_exit", "stderr": "bad n"},
        }
        records = _validate_problem(
            "http://judge",
            "policy",
            {
                "problem_id": PROBLEM_ID,
                "metadata_json": json.dumps(
                    {"time_limit_ms": 1000, "memory_limit_mb": 256}
                ),
                "validator": "validator",
            },
            [
                {"generation_id": 0, "test_input": "1"},
                {"generation_id": 1, "test_input": "0"},
            ],
        )

        self.assertEqual([record[3] for record in records], [1, 0])
        self.assertEqual([record[4] for record in records], [
            "validator_accepted",
            "validator_rejected",
        ])


class CodeContestsPlusScoringTests(unittest.TestCase):
    @patch("utils.codecontests_plus._execute_program", return_value=[])
    def test_pending_execution_query_qualifies_joined_problem_id(self, execute):
        with tempfile.TemporaryDirectory() as directory:
            with RunStore(Path(directory) / "run.sqlite3") as store, patch(
                "utils.codecontests_plus._load_dataset", return_value=[fixture_row()]
            ):
                prepare_dataset(store)
                audit_all_programs(store)
                store.bind_manifest({"policies": ["testcase_eval_task1_cot"]})
                store.connection.execute(
                    "INSERT INTO materializations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    ("testcase_eval_task1_cot", 1, PROBLEM_ID, "", 0, "1", "complete", "", 1.0),
                )
                store.connection.execute(
                    "INSERT INTO ccplus_candidates VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    ("testcase_eval_task1_cot", PROBLEM_ID, 0, 1, "complete", "1\n", "", 1.0),
                )
                store.connection.commit()

                counts = execute_pending(
                    store,
                    base_url="http://judge",
                    workers=1,
                )

        self.assertEqual(counts, {"program_batches": 4, "executions": 0})
        self.assertEqual(execute.call_count, 4)

    @patch("utils.codecontests_plus._batch_results")
    @patch("utils.codecontests_plus._run_program")
    def test_checker_decides_solution_acceptance(self, run_program, checker):
        run_program.return_value = {
            "0": {"id": "0", "status": "exited", "stdout": "A"},
            "1": {"id": "1", "status": "exited", "stdout": "B"},
        }
        checker.return_value = {
            "0": {"id": "0", "status": "exited", "exitStatus": 0},
            "1": {"id": "1", "status": "nonzero_exit", "exitStatus": 1},
        }
        records = _execute_program(
            "http://judge",
            "policy",
            {"problem_id": PROBLEM_ID, "submission_id": "s", "submission_type": "wrong_submission", "language": "CPP", "checker": "checker"},
            [
                {"generation_id": 0, "test_input": "1", "oracle_output": "A"},
                {"generation_id": 1, "test_input": "2", "oracle_output": "C"},
            ],
        )

        self.assertEqual([record[10] for record in records], ["accepted", "wrong_answer"])

    @patch("utils.codecontests_plus._batch_results")
    @patch("utils.codecontests_plus._run_program")
    def test_checker_judge_failure_becomes_oracle_conflict(
        self, run_program, checker
    ):
        run_program.return_value = {
            "0": {"id": "0", "status": "exited", "stdout": "valid"},
        }
        checker.return_value = {
            "0": {
                "id": "0",
                "status": "nonzero_exit",
                "exitStatus": 3,
                "stderr": "jury answer is invalid",
            },
        }

        records = _execute_program(
            "http://judge",
            "policy",
            {
                "problem_id": PROBLEM_ID,
                "submission_id": "s",
                "submission_type": "wrong_submission",
                "language": "CPP",
                "checker": "checker",
            },
            [{"generation_id": 0, "test_input": "1", "oracle_output": "bad"}],
        )

        self.assertEqual(records[0][10], "oracle_conflict")
        self.assertEqual(records[0][12], "jury answer is invalid")

    @patch("utils.codecontests_plus._batch_results")
    @patch("utils.codecontests_plus._run_program")
    def test_unknown_checker_failure_remains_fatal(self, run_program, checker):
        run_program.return_value = {
            "0": {"id": "0", "status": "exited", "stdout": "output"},
        }
        checker.return_value = {
            "0": {
                "id": "0",
                "status": "nonzero_exit",
                "exitStatus": 4,
                "stderr": "checker crashed",
            },
        }

        with self.assertRaisesRegex(RuntimeError, "checker failed"):
            _execute_program(
                "http://judge",
                "policy",
                {
                    "problem_id": PROBLEM_ID,
                    "submission_id": "s",
                    "submission_type": "wrong_submission",
                    "language": "CPP",
                    "checker": "checker",
                },
                [
                    {
                        "generation_id": 0,
                        "test_input": "1",
                        "oracle_output": "answer",
                    }
                ],
            )

    @patch("utils.codecontests_plus._execute_program")
    def test_oracle_conflict_invalidates_candidate_and_stops_scheduling(self, execute):
        def result(_base_url, policy, program, tests):
            outcome = (
                "oracle_conflict"
                if program["submission_id"].endswith(":r:000")
                else "accepted"
            )
            record = list(
                execution(
                    program["submission_id"],
                    program["submission_type"],
                    outcome,
                )
            )
            record[12] = "jury answer is invalid" if outcome == "oracle_conflict" else ""
            return [tuple(record)]

        execute.side_effect = result
        with tempfile.TemporaryDirectory() as directory:
            with RunStore(Path(directory) / "run.sqlite3") as store, patch(
                "utils.codecontests_plus._load_dataset", return_value=[fixture_row()]
            ):
                prepare_dataset(store)
                audit_all_programs(store)
                store.bind_manifest({"policies": ["testcase_eval_task1_cot"]})
                store.connection.execute(
                    "INSERT INTO materializations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        "testcase_eval_task1_cot",
                        1,
                        PROBLEM_ID,
                        "",
                        0,
                        "1",
                        "complete",
                        "",
                        1.0,
                    ),
                )
                store.connection.execute(
                    "INSERT INTO ccplus_candidates VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        "testcase_eval_task1_cot",
                        PROBLEM_ID,
                        0,
                        1,
                        "complete",
                        "bad answer",
                        "",
                        1.0,
                    ),
                )
                store.connection.commit()

                counts = execute_pending(store, base_url="http://judge", workers=1)
                candidate = store.connection.execute(
                    "SELECT valid, status, error FROM ccplus_candidates"
                ).fetchone()
                execution_count = store.connection.execute(
                    "SELECT COUNT(*) FROM executions"
                ).fetchone()[0]

        self.assertEqual(counts["oracle_conflicts"], 1)
        self.assertEqual(counts["executions"], 0)
        self.assertEqual(execute.call_count, 2)
        self.assertEqual(tuple(candidate)[:2], (0, "oracle_conflict"))
        self.assertIn(":r:000", candidate["error"])
        self.assertEqual(execution_count, 0)

    def test_invalid_input_remains_in_denominator_and_tpr_tnr_use_checker_results(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with RunStore(root / "run.sqlite3") as store, patch(
                "utils.codecontests_plus._load_dataset", return_value=[fixture_row()]
            ):
                prepare_dataset(store)
                audit_all_programs(store)
                store.bind_manifest(
                    {
                        "model": "model",
                        "policies": ["testcase_eval_task1_cot"],
                        "tasks": [1],
                        "ccplus_max_generations_per_problem": 2,
                    }
                )
                store.save_generation(generation_record(0))
                store.save_generation(generation_record(1))
                store.connection.executemany(
                    "INSERT INTO materializations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        ("testcase_eval_task1_cot", 1, PROBLEM_ID, "", 0, "1", "complete", "", 1.0),
                        ("testcase_eval_task1_cot", 1, PROBLEM_ID, "", 1, "0", "complete", "", 1.0),
                    ],
                )
                store.connection.executemany(
                    "INSERT INTO ccplus_candidates VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        ("testcase_eval_task1_cot", PROBLEM_ID, 0, 1, "complete", "1\n", "", 1.0),
                        ("testcase_eval_task1_cot", PROBLEM_ID, 1, 0, "validator_rejected", "", "bad n", 1.0),
                    ],
                )
                store.connection.executemany(
                    "INSERT INTO executions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        execution(f"{PROBLEM_ID}:r:000", "right_submission", "accepted"),
                        execution(f"{PROBLEM_ID}:r:001", "right_submission", "accepted"),
                        execution(f"{PROBLEM_ID}:w:000", "wrong_submission", "wrong_answer"),
                        execution(f"{PROBLEM_ID}:w:001", "wrong_submission", "accepted"),
                    ],
                )
                stale = list(
                    execution(f"{PROBLEM_ID}:w:001", "wrong_submission", "accepted")
                )
                stale[4] = 1
                store.connection.execute(
                    "INSERT INTO executions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    tuple(stale),
                )
                store.connection.commit()
                summary = score(store)
                exported = export_jsonl(store, root / "tests")

            metric = summary["macro"]
            self.assertTrue(summary["complete"])
            self.assertEqual(summary["expected_executions"], 4)
            self.assertEqual(summary["actual_executions"], 4)
            self.assertEqual(
                metric,
                {
                    "valid_rate": 0.5,
                    "true_positive_rate": 1.0,
                    "true_negative_rate": 0.5,
                },
            )
            self.assertEqual(exported, {PROBLEM_ID: 1})
            output = root / "tests" / "tests-ccp-Codeforces-1_A.jsonl"
            self.assertEqual(
                [json.loads(line) for line in output.read_text().splitlines()],
                [{"input": "1", "output": "1\n"}],
            )

    def test_missing_execution_does_not_count_as_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            with RunStore(Path(directory) / "run.sqlite3") as store, patch(
                "utils.codecontests_plus._load_dataset", return_value=[fixture_row()]
            ):
                prepare_dataset(store)
                audit_all_programs(store)
                store.bind_manifest(
                    {
                        "model": "model",
                        "policies": ["testcase_eval_task1_cot"],
                        "tasks": [1],
                        "ccplus_max_generations_per_problem": 1,
                    }
                )
                store.save_generation(generation_record(0))
                store.connection.execute(
                    "INSERT INTO ccplus_candidates VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    ("testcase_eval_task1_cot", PROBLEM_ID, 0, 1, "complete", "1\n", "", 1.0),
                )
                store.connection.commit()
                summary = score(store)

        self.assertFalse(summary["complete"])
        self.assertEqual(summary["macro"]["true_negative_rate"], 0.0)


if __name__ == "__main__":
    unittest.main()
