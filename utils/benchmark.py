"""Helpers owned by the benchmark rather than a solver pipeline."""

from typing import Any, Mapping


def solver_metadata(record: Mapping[str, Any]) -> Mapping[str, Any]:
    public_fields = {
        "difficulty",
        "difficulty-source",
        "hack_id",
        "hackable",
        "language",
        "problem_id",
        "submission_id",
        "title_en",
        "title_zh",
        "wrong_id",
    }
    scalar_types = (str, int, float, bool, type(None))
    return {
        key: value
        for key, value in record.items()
        if key in public_fields and isinstance(value, scalar_types)
    }
