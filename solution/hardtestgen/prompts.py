"""Pinned prompt templates from LeiLiLab/HardTestGen at 0355315."""

from pathlib import Path
import tomllib


_ROOT = Path(__file__).with_name("prompt_templates")


def _template(filename: str, section: str) -> str:
    with (_ROOT / filename).open("rb") as stream:
        return str(tomllib.load(stream)[section]["content"])


_IV_AND_OJF = _template(
    "test_cases_kit_prompt_iv_and_ojf.toml",
    "test_cases_kit_prompt_IV_and_OJF",
)
_INPUT_GENERATION = _template(
    "test_cases_kit_prompt_ig.toml",
    "test_cases_kit_prompt_IG",
)


def iv_and_ojf(problem_statement: str, oracle_program: str) -> str:
    return (
        _IV_AND_OJF.replace("{{ problem_specification }}", problem_statement)
        .replace("{{ oracle_program }}", oracle_program)
        .strip()
    )

def input_generation(
    problem_statement: str,
    oracle_program: str,
    input_validator: str,
) -> str:
    return (
        _INPUT_GENERATION.replace("{{ problem_specification }}", problem_statement)
        .replace("{{ oracle_program }}", oracle_program)
        .replace("{{ input_validator }}", input_validator)
        .strip()
    )
