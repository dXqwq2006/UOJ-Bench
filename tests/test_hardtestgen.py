import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from solution import load_solver
from solution.api import TestPackageInput, require_solver_support
from solution.hardtestgen.api import (
    ExecutionResult,
    HardTestGenInput,
    KitStage,
    TestCaseKit,
)
from solution.hardtestgen.lightcp import HardTestGenLightCP
from solution.hardtestgen.pipeline import (
    HardTestGenPipeline,
    function_names,
    parse_kit_response,
)
from utils.hardtestgen_benchmark import generate_kits, generate_suites
from utils.testcase_eval_benchmark import RunStore


VALIDATOR = """def validate_input(input_str: str) -> bool:
    return input_str != "bad"
"""
REGULAR = """def gen_range_based_input():
    return "2"
"""
HACK = """def gen_hacking_input_edge():
    return "3"
"""


def kit(*, llm_inputs=("1", "bad")) -> TestCaseKit:
    return TestCaseKit(
        input_validator=VALIDATOR,
        output_judging_function=None,
        llm_inputs=tuple(llm_inputs),
        regular_generator=REGULAR,
        regular_functions=("gen_range_based_input",),
        hack_generator=HACK,
        hack_functions=("gen_hacking_input_edge",),
        prompts={"iv_and_ojf": "p1", "input_generation": "p2"},
        responses={"iv_and_ojf": "r1", "input_generation": "r2"},
        messages={},
        usage={"iv_and_ojf": {"total_tokens": 4}, "input_generation": {"total_tokens": 6}},
    )


class FakeExecutor:
    def run_many(self, language, source, inputs, *, time_limit_ms, memory_limit_mb):
        if "values = json.loads" in source:
            values = [value for value in json.loads(inputs[0]) if value != "bad"]
            return [ExecutionResult("exited", "Result: " + json.dumps(values))]
        if "value = gen_range_based_input()" in source:
            values = ["2", "4"] * 20
            return [ExecutionResult("exited", "Result: " + value) for value in values]
        if "value = gen_hacking_input_edge()" in source:
            return [ExecutionResult("exited", "Result: 3") for _ in inputs]
        raise AssertionError(f"unexpected execution: {language} {source[:80]}")


class FakePipeline:
    def __init__(self, model):
        self.model = model

    def generate_iv_and_ojf(self, task):
        return KitStage(
            "iv_and_ojf", "p1", "r1", {}, {"total_tokens": 4},
            {"input_validator": VALIDATOR, "output_judging_function": None},
        )

    def generate_input_generation(self, task, first):
        return KitStage(
            "input_generation", "p2", "r2", {}, {"total_tokens": 6},
            {
                "LLMGen_input": ["1", "bad"],
                "RPGen_SPGen_input_generator": REGULAR,
                "HackGen_input_generator": HACK,
            },
        )

    @staticmethod
    def assemble_kit(first, second):
        return kit()


class FlakySecondStagePipeline(FakePipeline):
    first_calls = 0
    second_calls = 0

    def generate_iv_and_ojf(self, task):
        type(self).first_calls += 1
        return super().generate_iv_and_ojf(task)

    def generate_input_generation(self, task, first):
        type(self).second_calls += 1
        if type(self).second_calls == 1:
            raise RuntimeError("temporary failure")
        return super().generate_input_generation(task, first)


class HardTestGenTests(unittest.TestCase):
    def test_parser_matches_result_json_and_recovers_literal_control_chars(self):
        response = '# Result\n```json\n{"input_validator":"line1\nline2"}\n```'
        self.assertEqual(
            parse_kit_response(response),
            {"input_validator": "line1\nline2"},
        )
        self.assertEqual(parse_kit_response('```json\n{"x": 1}\n```'), {})
        self.assertEqual(
            function_names("def ignored(): pass\n\ndef gen_range_based_input(): pass\n"),
            ("ignored", "gen_range_based_input"),
        )

    def test_two_calls_use_statement_and_validator_without_reference_code(self):
        first = {
            "input_validator": VALIDATOR,
            "needs_custom_output_judging_function": False,
            "output_judging_function": None,
        }
        second = {
            "LLMGen_input": ["1"],
            "RPGen_SPGen_input_generator": REGULAR,
            "HackGen_input_generator": HACK,
        }
        responses = iter(
            [
                "# Result\n```json\n" + json.dumps(first) + "\n```",
                "# Result\n```json\n" + json.dumps(second) + "\n```",
            ]
        )
        calls = []

        def caller(prompt, model):
            calls.append((prompt, model))
            return next(responses), {"model": model}, {"total_tokens": 1}

        generated = HardTestGenPipeline("model", caller).generate_kit(
            HardTestGenInput("p", "PUBLIC STATEMENT", {"private": "SECRET SOURCE"})
        )

        self.assertEqual(len(calls), 2)
        self.assertTrue(all("PUBLIC STATEMENT" in prompt for prompt, _ in calls))
        self.assertTrue(all("SECRET SOURCE" not in prompt for prompt, _ in calls))
        self.assertIn(VALIDATOR.strip(), calls[1][0])
        self.assertEqual(generated.regular_functions, ("gen_range_based_input",))
        self.assertEqual(generated.hack_functions, ("gen_hacking_input_edge",))

    def test_suite_uses_all_generators_and_publishes_inputs_without_outputs(self):
        result = HardTestGenPipeline("unused").generate_suite(
            HardTestGenInput("p", "statement"), kit(), FakeExecutor()
        )

        self.assertEqual(result.status, "complete")
        self.assertEqual(
            [(case.input, case.output, case.method) for case in result.test_cases],
            [
                ("1", "", "LLMGen"),
                ("2", "", "RPGen_SPGen"),
                ("4", "", "RPGen_SPGen"),
                ("3", "", "HackGen"),
            ],
        )

    def test_suite_discards_empty_inputs_before_publication(self):
        result = HardTestGenPipeline("unused").generate_suite(
            HardTestGenInput("p", "statement"),
            TestCaseKit(
                VALIDATOR, None, ("",), None, (), None, (), {}, {}, {}, {}
            ),
            FakeExecutor(),
        )
        self.assertEqual(result.status, "input_generation_failed")
        self.assertEqual(result.test_cases, ())
        self.assertEqual(result.generated_inputs, ())

    def test_suite_over_50_rejects_whole_package(self):
        values = tuple(str(index) for index in range(51))
        result = HardTestGenPipeline("unused").generate_suite(
            HardTestGenInput("p", "statement"),
            TestCaseKit(VALIDATOR, None, values, None, (), None, (), {}, {}, {}, {}),
            FakeExecutor(),
        )
        self.assertEqual(result.status, "test_count_limit_exceeded")
        self.assertEqual(result.test_cases, ())
        self.assertEqual(len(result.generated_inputs), 51)

    def _prepared_store(self, database):
        store = RunStore(database)
        store.bind_manifest({"testcase_eval_upstream_commit": "pinned"})
        store.connection.execute(
            "INSERT INTO problems VALUES (?, ?, ?)", ("p", "statement", "{}")
        )
        store.connection.executemany(
            "INSERT INTO submissions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("submission_all", "o1", "p", "right_submission", "", "Python 3", "", "PRIVATE ORACLE ONE", "{}"),
                ("submission_all", "o2", "p", "right_submission", "", "Python 3", "", "PRIVATE ORACLE TWO", "{}"),
            ],
        )
        store.connection.commit()
        return store

    def test_checkpoint_adapter_publishes_the_full_ordered_package(self):
        with tempfile.TemporaryDirectory() as directory:
            with self._prepared_store(Path(directory) / "results.sqlite3") as store:
                self.assertEqual(
                    generate_kits(
                        store, model="m", workers=1, pipeline_factory=FakePipeline
                    )["complete"],
                    1,
                )
                self.assertEqual(
                    generate_suites(store, executor=FakeExecutor(), workers=1)["complete"],
                    1,
                )
                rows = list(
                    store.connection.execute(
                        "SELECT candidate FROM generations ORDER BY generation_id"
                    )
                )
                calls = "\n".join(
                    row[0] for row in store.connection.execute(
                        "SELECT prompt FROM package_calls ORDER BY call_id"
                    )
                )
                package = store.connection.execute(
                    "SELECT status, declared_test_count FROM package_runs"
                ).fetchone()

        self.assertEqual([row["candidate"] for row in rows], ["1", "2", "4", "3"])
        self.assertEqual(tuple(package), ("complete", 4))
        self.assertNotIn("PRIVATE ORACLE", calls)

    def test_suite_checkpoint_republishes_a_missing_package(self):
        with tempfile.TemporaryDirectory() as directory:
            with self._prepared_store(Path(directory) / "results.sqlite3") as store:
                generate_kits(
                    store, model="m", workers=1, pipeline_factory=FakePipeline
                )
                generate_suites(store, executor=FakeExecutor(), workers=1)
                store.connection.execute("DELETE FROM generations")
                store.connection.execute("DELETE FROM package_tests")
                store.connection.execute("DELETE FROM package_runs")
                store.connection.commit()

                result = generate_suites(
                    store,
                    executor=FakeExecutor(),
                    workers=1,
                )
                package = store.connection.execute(
                    "SELECT status, declared_test_count FROM package_runs"
                ).fetchone()
                generated = store.connection.execute(
                    "SELECT count(*) FROM generations"
                ).fetchone()[0]

        self.assertEqual(result["scheduled"], 1)
        self.assertEqual(tuple(package), ("complete", 4))
        self.assertEqual(generated, 4)

    def test_retry_resumes_only_the_failed_second_llm_stage(self):
        FlakySecondStagePipeline.first_calls = 0
        FlakySecondStagePipeline.second_calls = 0
        with tempfile.TemporaryDirectory() as directory:
            with self._prepared_store(Path(directory) / "results.sqlite3") as store:
                first = generate_kits(
                    store, model="m", workers=1,
                    pipeline_factory=FlakySecondStagePipeline,
                )
                second = generate_kits(
                    store, model="m", workers=1, retry_errors=True,
                    pipeline_factory=FlakySecondStagePipeline,
                )

        self.assertEqual(first["request_error"], 1)
        self.assertEqual(second["complete"], 1)
        self.assertEqual(FlakySecondStagePipeline.first_calls, 1)
        self.assertEqual(FlakySecondStagePipeline.second_calls, 2)

    def test_solver_contract_returns_one_statement_only_ordered_package(self):
        solver = load_solver("hardtestgen", "model")
        require_solver_support(solver, "test_package")
        solver.pipeline.generate_kit = lambda _task: kit()
        with patch(
            "solution.hardtestgen.HardTestGenLightCP", return_value=FakeExecutor()
        ):
            session = solver.start_test_package(
                TestPackageInput(
                    "p", "PUBLIC STATEMENT", {"benchmark": "testcase-eval", "secret": "PRIVATE"}
                )
            )
            self.assertEqual(
                session.initial_request,
                {"problem_id": "p", "problem_statement": "PUBLIC STATEMENT"},
            )
            turn = session.next()

        self.assertEqual(
            [test.content for test in turn.candidate.tests], ["1", "2", "4", "3"]
        )
        self.assertEqual(
            turn.candidate.artifact["methods"],
            ["LLMGen", "RPGen_SPGen", "RPGen_SPGen", "HackGen"],
        )
        with self.assertRaisesRegex(ValueError, "already produced"):
            session.next()

    def test_lightcp_adapter_preserves_batch_order(self):
        def request(_base, path, payload=None):
            if path == "/health":
                return {"ok": True, "profiles": {"testcase-eval": {}}}
            return {
                "results": [
                    {"id": "1", "status": "exited", "stdout": "b"},
                    {"id": "0", "status": "exited", "stdout": "a"},
                ]
            }

        with patch("solution.hardtestgen.lightcp._request_json", request):
            results = HardTestGenLightCP("http://lightcp", "codecontests-plus").run_many(
                "python3", "source", ["x", "y"], time_limit_ms=1, memory_limit_mb=1
            )
        self.assertEqual([result.stdout for result in results], ["a", "b"])

    def test_testcase_eval_uses_single_custom_test_endpoint(self):
        paths = []

        def request(_base, path, payload=None):
            paths.append(path)
            return {"status": "exited", "stdout": payload["stdin"]}

        with patch("solution.hardtestgen.lightcp._request_json", request):
            results = HardTestGenLightCP("http://lightcp", "testcase-eval").run_many(
                "python3", "source", ["x", "y"], time_limit_ms=1, memory_limit_mb=1
            )
        self.assertEqual([result.stdout for result in results], ["x", "y"])
        self.assertEqual(paths, ["/custom-test", "/custom-test"])


if __name__ == "__main__":
    unittest.main()
