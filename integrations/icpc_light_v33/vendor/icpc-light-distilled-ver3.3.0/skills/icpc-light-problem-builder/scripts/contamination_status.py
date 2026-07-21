#!/usr/bin/env python3
"""Parse the explicit contamination field shared by blind-stage consumers."""

from __future__ import annotations

import re


FIELD_PATTERN = re.compile(
    r"(?i)^\s*(?:[-*+]\s+)?(?:\*\*|__|`)?\s*"
    r"contamination(?:[_ -]+status)?"
    r"(?:\*\*|__|`)?\s*[:：]\s*(?:\*\*|__|`)?"
    r"(?P<value>[^\r\n]*)$"
)
STATUS_TOKEN_PATTERN = re.compile(
    r"(?i)(?<![A-Za-z0-9_])"
    r"(uncontaminated|contaminated|clean|no|yes)"
    r"(?![A-Za-z0-9_])"
)
INITIAL_STATUS_PATTERN = re.compile(
    r"(?i)^\s*(?:(?:\*\*|__|`)\s*)*"
    r"(?P<value>uncontaminated|contaminated|clean|no|yes)"
    r"(?![A-Za-z0-9_])"
    r"\s*(?:(?:\*\*|__|`)\s*)*"
    r"(?=$|[.,;:!?。；，：！？()\[\]{}—–/\\-])"
)
STATUS_ALIASES = {
    "clean": "clean",
    "no": "clean",
    "uncontaminated": "clean",
    "contaminated": "contaminated",
    "yes": "contaminated",
}


def parse_contamination_status(text: str) -> str | None:
    """Return one unambiguous explicit status, or fail closed with ``None``.

    The first value on every matching field must be a recognized status token.
    All other recognized status tokens on that line and on repeated fields must
    map to the same canonical value.  This permits explanatory prose while
    rejecting forms such as ``No/Yes`` and ``No. Yes``.
    """

    values: set[str] = set()
    found_field = False
    for line in text.splitlines():
        field = FIELD_PATTERN.match(line)
        if field is None:
            continue
        found_field = True
        raw_value = field.group("value")
        initial = INITIAL_STATUS_PATTERN.match(raw_value)
        if initial is None:
            return None
        initial_value = STATUS_ALIASES[initial.group("value").lower()]
        line_values = {
            STATUS_ALIASES[match.group(1).lower()]
            for match in STATUS_TOKEN_PATTERN.finditer(raw_value)
        }
        if line_values != {initial_value}:
            return None
        values.add(initial_value)
    if not found_field or len(values) != 1:
        return None
    return next(iter(values))
