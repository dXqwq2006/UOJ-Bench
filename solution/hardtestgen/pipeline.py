"""GitHub-compatible HardTestGen kit and test-suite generation."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import asdict
from typing import Any, Callable, Mapping, Sequence
import ast
import json
import re
import time

from . import prompts
from .api import (
    GeneratedInput,
    HardTestGenInput,
    KitStage,
    OracleProgram,
    ProgramExecutor,
    SuiteResult,
    TestCase,
    TestCaseKit,
)


CallDetails = Callable[[Any, str], tuple[str, Mapping[str, Any], Mapping[str, Any]]]
_JSON_FENCE = re.compile(r"```json\s*(.*?)```", re.DOTALL)


def _default_call_details(message: Any, model: str):
    from solution.llm.call_llm import call_llm_details

    return call_llm_details(message, model)


def _fix_control_chars_inside_strings(value: str) -> str:
    result: list[str] = []
    in_string = False
    escaped = False
    for character in value:
        if not in_string:
            result.append(character)
            if character == '"':
                in_string = True
            continue
        if escaped:
            result.append(character)
            escaped = False
        elif character == "\\":
            result.append(character)
            escaped = True
        elif character == '"':
            result.append(character)
            in_string = False
        elif ord(character) < 0x20:
            result.append({"\n": "\\n", "\r": "\\r", "\t": "\\t"}.get(
                character, f"\\u{ord(character):04x}"
            ))
        else:
            result.append(character)
    return "".join(result)


def parse_kit_response(response: str) -> dict[str, Any]:
    """Parse the strict ``# Result`` JSON block used by upstream HardTestGen."""
    _, marker, result = response.partition("# Result")
    if not marker:
        return {}
    match = _JSON_FENCE.search(result)
    if match is None:
        return {}
    payload = match.group(1).strip()
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        try:
            parsed = json.loads(_fix_control_chars_inside_strings(payload))
        except json.JSONDecodeError:
            return {}
    return parsed if isinstance(parsed, dict) else {}


def function_names(source: str | None) -> tuple[str, ...]:
    if not source:
        return ()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return ()
    functions = sorted(
        (node.lineno, node.name)
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
    )
    return tuple(name for _line, name in functions)


def _optional_code(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _stable_unique(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(values))


class HardTestGenPipeline:
    """Two LLM calls followed by sandboxed generation and oracle consensus."""

    def __init__(self, model: str, call_details: CallDetails | None = None):
        self.model = model
        self.call_details = call_details or _default_call_details

    def generate_kit(self, task: HardTestGenInput) -> TestCaseKit:
        first = self.generate_iv_and_ojf(task)
        second = self.generate_input_generation(task, first)
        return self.assemble_kit(first, second)

    def generate_iv_and_ojf(self, task: HardTestGenInput) -> KitStage:
        if not task.oracle_programs:
            raise ValueError("HardTestGen requires at least one correct program")
        oracle = task.oracle_programs[0].source
        iv_prompt = prompts.iv_and_ojf(task.problem_statement, oracle)
        iv_raw, iv_message, iv_usage = self.call_details(iv_prompt, self.model)
        return KitStage(
            "iv_and_ojf",
            iv_prompt,
            iv_raw,
            iv_message,
            iv_usage,
            parse_kit_response(iv_raw),
        )

    def generate_input_generation(
        self, task: HardTestGenInput, first: KitStage
    ) -> KitStage:
        validator = _optional_code(first.parsed.get("input_validator"))
        if validator is None:
            raise ValueError("IV/OJF response has no input_validator")
        oracle = task.oracle_programs[0].source
        ig_prompt = prompts.input_generation(task.problem_statement, oracle, validator)
        ig_raw, ig_message, ig_usage = self.call_details(ig_prompt, self.model)
        return KitStage(
            "input_generation",
            ig_prompt,
            ig_raw,
            ig_message,
            ig_usage,
            parse_kit_response(ig_raw),
        )

    @staticmethod
    def assemble_kit(first: KitStage, second: KitStage) -> TestCaseKit:
        validator = _optional_code(first.parsed.get("input_validator"))
        if validator is None:
            raise ValueError("IV/OJF response has no input_validator")
        first_result = first.parsed
        second_result = second.parsed
        if not second_result:
            raise ValueError("input-generation response has no valid result JSON")
        regular = _optional_code(second_result.get("RPGen_SPGen_input_generator"))
        hack = _optional_code(second_result.get("HackGen_input_generator"))
        llm_inputs = second_result.get("LLMGen_input")
        if not isinstance(llm_inputs, list):
            llm_inputs = []

        return TestCaseKit(
            input_validator=validator,
            output_judging_function=_optional_code(first_result.get("output_judging_function")),
            llm_inputs=tuple(str(value) for value in llm_inputs),
            regular_generator=regular,
            regular_functions=tuple(
                name
                for name in function_names(regular)
                if name.startswith("gen_stratified_input")
                or name.startswith("gen_range_based_input")
            ),
            hack_generator=hack,
            hack_functions=tuple(
                name
                for name in function_names(hack)
                if name.startswith("gen_hacking_input")
            ),
            prompts={"iv_and_ojf": first.prompt, "input_generation": second.prompt},
            responses={"iv_and_ojf": first.raw_text, "input_generation": second.raw_text},
            messages={"iv_and_ojf": first.message, "input_generation": second.message},
            usage={"iv_and_ojf": first.usage, "input_generation": second.usage},
        )

    def generate_suite(
        self,
        task: HardTestGenInput,
        kit: TestCaseKit,
        executor: ProgramExecutor,
    ) -> SuiteResult:
        generated = self._generate_inputs(kit, executor)
        if not generated:
            return SuiteResult("input_generation_failed", error="no valid inputs")
        outputs, status = self._consensus_outputs(
            task.oracle_programs[:5], generated, kit.output_judging_function, executor
        )
        if outputs is None:
            return SuiteResult(status, generated_inputs=tuple(generated))
        cases = tuple(
            TestCase(item.content, output, item.method, item.generator)
            for item, output in zip(generated, outputs)
            if output is not None
        )
        if not cases:
            return SuiteResult(
                "output_generation_failed",
                generated_inputs=tuple(generated),
                error="all consensus outputs were invalid",
            )
        return SuiteResult("complete", cases, tuple(generated))

    def _generate_inputs(
        self, kit: TestCaseKit, executor: ProgramExecutor
    ) -> list[GeneratedInput]:
        started = time.monotonic()
        llm_inputs = self._validate_inputs(
            list(kit.llm_inputs), kit.input_validator, executor, 10_000
        )
        generated = [GeneratedInput(value, "LLMGen") for value in llm_inputs]

        is_multi_category = len(kit.regular_functions) >= 2
        regular_target = 10 if is_multi_category else 20
        regular_attempts = 20 if is_multi_category else 40
        for name in kit.regular_functions:
            if time.monotonic() - started > 180:
                break
            values = self._run_generator(
                kit.regular_generator,
                name,
                kit.input_validator,
                executor,
                regular_attempts,
                regular_target,
            )
            generated.extend(GeneratedInput(value, "RPGen_SPGen", name) for value in values)

        for name in kit.hack_functions:
            if time.monotonic() - started > 180:
                break
            values = self._run_generator(
                kit.hack_generator,
                name,
                kit.input_validator,
                executor,
                20,
                10,
            )
            generated.extend(GeneratedInput(value, "HackGen", name) for value in values)
        return generated

    @staticmethod
    def _validate_inputs(
        inputs: list[str],
        validator: str,
        executor: ProgramExecutor,
        time_limit_ms: int,
    ) -> list[str]:
        if not inputs:
            return []
        source = f'''import json
import sys

{validator}

values = json.loads(sys.stdin.read())
accepted = []
for value in values:
    try:
        if validate_input(value):
            accepted.append(value)
    except Exception:
        pass
print("Result: " + json.dumps(accepted), end="")
'''
        results = executor.run_many(
            "python3", source, [json.dumps(inputs)],
            time_limit_ms=time_limit_ms, memory_limit_mb=15_360,
        )
        if len(results) != 1 or not results[0].succeeded:
            return []
        prefix = "Result: "
        if not results[0].stdout.startswith(prefix):
            return []
        try:
            values = json.loads(results[0].stdout[len(prefix):])
        except json.JSONDecodeError:
            return []
        if not isinstance(values, list):
            return []
        return _stable_unique([value for value in values if isinstance(value, str)])

    @staticmethod
    def _run_generator(
        generator_source: str | None,
        function_name: str,
        validator: str,
        executor: ProgramExecutor,
        attempts: int,
        target: int,
    ) -> list[str]:
        if generator_source is None:
            return []
        source = f'''{generator_source}

{validator}

try:
    value = {function_name}()
    assert isinstance(value, str)
    assert validate_input(value)
except Exception as exc:
    print("Failed: " + str(exc), end="")
else:
    print("Result: " + value, end="")
'''
        results = executor.run_many(
            "python3", source, [""] * attempts,
            time_limit_ms=5_000, memory_limit_mb=15_360,
        )
        values = [
            result.stdout[len("Result: "):]
            for result in results
            if result.succeeded and result.stdout.startswith("Result: ")
        ]
        return _stable_unique(values)[:target]

    def _consensus_outputs(
        self,
        oracles: Sequence[OracleProgram],
        inputs: Sequence[GeneratedInput],
        output_judging_function: str | None,
        executor: ProgramExecutor,
    ) -> tuple[list[str | None] | None, str]:
        required = min(2, len(oracles))
        if required == 0:
            return None, "output_generation_no_code_solutions"
        input_values = [item.content for item in inputs]
        previous: list[list[str | None]] = []
        for oracle in oracles:
            results = executor.run_many(
                oracle.language,
                oracle.source,
                input_values,
                time_limit_ms=5_000,
                memory_limit_mb=15_360,
            )
            if len(results) != len(inputs):
                continue
            outputs: list[str | None] = [
                result.stdout if result.succeeded else None for result in results
            ]
            if sum(value is not None for value in outputs) < len(outputs) / 2:
                continue
            if required == 1:
                return outputs, "complete"
            for earlier in previous:
                verdicts = self._judge_outputs(
                    input_values, earlier, outputs, output_judging_function, executor
                )
                if verdicts and all(verdicts):
                    return outputs, "complete"
            previous.append(outputs)
        if len(previous) < required:
            return None, "output_generation_no_enough_valid_code_solutions"
        return None, "output_generation_verification_failed"

    @staticmethod
    def _judge_outputs(
        inputs: Sequence[str],
        candidates: Sequence[str | None],
        references: Sequence[str | None],
        output_judging_function: str | None,
        executor: ProgramExecutor,
    ) -> list[bool]:
        if output_judging_function is None:
            return [
                _default_output_equal(candidate, reference)
                for candidate, reference in zip(candidates, references)
            ]
        source = f'''import json
import sys

{output_judging_function}

try:
    data = json.loads(sys.stdin.read())
    verdict = output_judging_function(
        input_str=data["input_str"],
        candidate_output=data["candidate_output"],
        reference_output=data["reference_output"],
    )
except Exception:
    verdict = False
print("Result: " + ("True" if verdict else "False"), end="")
'''
        payloads = [
            json.dumps(
                {
                    "input_str": input_value,
                    "candidate_output": candidate,
                    "reference_output": reference,
                }
            )
            for input_value, candidate, reference in zip(inputs, candidates, references)
        ]
        results = executor.run_many(
            "python3", source, payloads,
            time_limit_ms=5_000, memory_limit_mb=15_360,
        )
        if len(results) != len(payloads):
            return []
        return [
            result.succeeded and result.stdout == "Result: True" for result in results
        ]


def _default_output_equal(candidate: str | None, reference: str | None) -> bool:
    if candidate is None and reference is None:
        return True
    if not isinstance(candidate, str) or not isinstance(reference, str):
        return False
    normalize = lambda value: "\n".join(
        line.rstrip() for line in value.rstrip().splitlines()
    )
    return normalize(candidate) == normalize(reference)


def project_test_cases(
    test_cases: Sequence[TestCase], budget: int
) -> list[TestCase]:
    """Round-robin suite groups for the fixed-budget benchmark adapter."""
    if budget < 1:
        raise ValueError("projection budget must be positive")
    groups: OrderedDict[tuple[str, str], list[TestCase]] = OrderedDict()
    for test_case in test_cases:
        groups.setdefault((test_case.method, test_case.generator), []).append(test_case)
    projected: list[TestCase] = []
    offset = 0
    while len(projected) < budget:
        added = False
        for values in groups.values():
            if offset < len(values):
                projected.append(values[offset])
                added = True
                if len(projected) == budget:
                    break
        if not added:
            break
        offset += 1
    return projected


def serializable(value: Any) -> Any:
    return asdict(value)
