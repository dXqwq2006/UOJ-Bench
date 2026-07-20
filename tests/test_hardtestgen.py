import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from solution.hardtestgen.api import (
    ExecutionResult,
    HardTestGenInput,
    KitStage,
    OracleProgram,
    TestCase,
    TestCaseKit,
)
from solution.hardtestgen.lightcp import HardTestGenLightCP
from solution.hardtestgen.pipeline import (
    HardTestGenPipeline,
    function_names,
    parse_kit_response,
    project_test_cases,
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


def kit() -> TestCaseKit:
    return TestCaseKit(
        input_validator=VALIDATOR,
        output_judging_function=None,
        llm_inputs=("1", "bad"),
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
            values = ["3"] * 20
            return [ExecutionResult("exited", "Result: " + value) for value in values]
        if source == "oracle":
            return [ExecutionResult("exited", str(int(value) * 2)) for value in inputs]
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
            function_names(
                "def ignored(): pass\n\ndef gen_range_based_input(): pass\n"
            ),
            ("ignored", "gen_range_based_input"),
        )

    def test_kit_generation_is_two_independent_calls_with_validator_dependency(self):
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

        task = HardTestGenInput(
            "p", "statement", (OracleProgram("o", "python3", "oracle"),)
        )
        generated = HardTestGenPipeline("model", caller).generate_kit(task)

        self.assertEqual(len(calls), 2)
        self.assertIn("statement", calls[0][0])
        self.assertIn(VALIDATOR.strip(), calls[1][0])
        self.assertEqual(generated.regular_functions, ("gen_range_based_input",))
        self.assertEqual(generated.hack_functions, ("gen_hacking_input_edge",))

    def test_suite_uses_all_three_generators_and_two_oracle_consensus(self):
        task = HardTestGenInput(
            "p",
            "statement",
            (
                OracleProgram("o1", "python3", "oracle"),
                OracleProgram("o2", "python3", "oracle"),
            ),
        )
        result = HardTestGenPipeline("unused").generate_suite(
            task, kit(), FakeExecutor()
        )

        self.assertEqual(result.status, "complete")
        self.assertEqual(
            [(case.input, case.output, case.method) for case in result.test_cases],
            [
                ("1", "2", "LLMGen"),
                ("2", "4", "RPGen_SPGen"),
                ("4", "8", "RPGen_SPGen"),
                ("3", "6", "HackGen"),
            ],
        )

    def test_projection_round_robins_generation_groups(self):
        cases = [
            TestCase("a1", "", "LLMGen"),
            TestCase("a2", "", "LLMGen"),
            TestCase("b1", "", "RPGen_SPGen", "range"),
            TestCase("b2", "", "RPGen_SPGen", "range"),
            TestCase("c1", "", "HackGen", "edge"),
        ]
        self.assertEqual(
            [case.input for case in project_test_cases(cases, 5)],
            ["a1", "b1", "c1", "a2", "b2"],
        )

    def test_checkpoint_adapter_publishes_fixed_budget_projection(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "results.sqlite3"
            with RunStore(database) as store:
                store.bind_manifest({"testcase_eval_upstream_commit": "pinned"})
                store.connection.execute(
                    "INSERT INTO problems VALUES (?, ?, ?)", ("p", "statement", "{}")
                )
                store.connection.executemany(
                    "INSERT INTO submissions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        ("submission_all", "o1", "p", "right_submission", "", "Python 3", "", "oracle", "{}"),
                        ("submission_all", "o2", "p", "right_submission", "", "Python 3", "", "oracle", "{}"),
                    ],
                )
                store.connection.commit()

                self.assertEqual(
                    generate_kits(
                        store,
                        model="m",
                        workers=1,
                        projection_budget=20,
                        pipeline_factory=FakePipeline,
                    )["complete"],
                    1,
                )
                self.assertEqual(
                    generate_suites(
                        store,
                        executor=FakeExecutor(),
                        workers=1,
                        projection_budget=20,
                    )["complete"],
                    1,
                )
                rows = list(
                    store.connection.execute(
                        "SELECT candidate FROM generations ORDER BY generation_id"
                    )
                )
                self.assertEqual(len(rows), 20)
                self.assertEqual([row["candidate"] for row in rows[:4]], ["1", "2", "3", "4"])
                self.assertTrue(all(row["candidate"] == "ERROR" for row in rows[4:]))

    def test_retry_resumes_only_the_failed_second_llm_stage(self):
        FlakySecondStagePipeline.first_calls = 0
        FlakySecondStagePipeline.second_calls = 0
        with tempfile.TemporaryDirectory() as directory:
            with RunStore(Path(directory) / "results.sqlite3") as store:
                store.bind_manifest({"testcase_eval_upstream_commit": "pinned"})
                store.connection.execute(
                    "INSERT INTO problems VALUES (?, ?, ?)", ("p", "statement", "{}")
                )
                store.connection.executemany(
                    "INSERT INTO submissions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        ("submission_all", "o1", "p", "right_submission", "", "Python 3", "", "oracle", "{}"),
                        ("submission_all", "o2", "p", "right_submission", "", "Python 3", "", "oracle", "{}"),
                    ],
                )
                store.connection.commit()

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
