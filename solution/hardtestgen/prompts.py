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

_NO_ORACLE = (
    "No reference program is provided. Infer the input and output contracts "
    "only from the problem specification."
)
_PACKAGE_LIMIT = (
    "\n\nBenchmark adaptation: the competitor receives only the problem "
    "statement. The final ordered package must contain at most 50 test inputs; "
    "do not assume access to accepted solutions, wrong solutions, validators, "
    "checkers, or hidden dataset metadata."
)


def iv_and_ojf(problem_statement: str) -> str:
    return (
        _IV_AND_OJF.replace("{{ problem_specification }}", problem_statement)
        .replace("{{ oracle_program }}", _NO_ORACLE)
        .strip()
        + _PACKAGE_LIMIT
    )

def input_generation(
    problem_statement: str,
    input_validator: str,
) -> str:
    return (
        _INPUT_GENERATION.replace("{{ problem_specification }}", problem_statement)
        .replace("{{ oracle_program }}", _NO_ORACLE)
        .replace("{{ input_validator }}", input_validator)
        .strip()
        + _PACKAGE_LIMIT
    )
