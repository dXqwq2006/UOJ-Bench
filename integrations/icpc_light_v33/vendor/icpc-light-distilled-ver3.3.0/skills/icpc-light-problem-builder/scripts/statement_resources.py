#!/usr/bin/env python3
"""Parse explicit time and memory limits from an ICPC problem statement.

Only labelled declarations are accepted.  The parser deliberately does not
infer limits from complexity prose or from unlabelled numbers in the statement.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


MIN_TIME_LIMIT_MS = 100
MAX_TIME_LIMIT_MS = 30_000
MIN_MEMORY_LIMIT_MIB = 16
MAX_MEMORY_LIMIT_MIB = 2_048

_LABEL_RE = re.compile(
    r"(?:"
    r"(?P<time>(?<![A-Za-z0-9])time[\s_-]*limit(?![A-Za-z0-9])|\u65f6\u95f4\u9650\u5236)"
    r"|"
    r"(?P<memory>(?<![A-Za-z0-9])memory[\s_-]*limit(?![A-Za-z0-9])|"
    r"\u5185\u5b58\u9650\u5236|\u7a7a\u95f4\u9650\u5236)"
    r")(?:(?:\s+per\s+(?:test|case))\s*(?::|\uff1a|\|)?|"
    r"\s*(?::|\uff1a|\|))\s*",
    re.IGNORECASE,
)
_VALUE_UNIT_RE = re.compile(
    r"(?P<value>[0-9]+(?:\.[0-9]+)?)\s*"
    r"(?P<unit>milliseconds?|msecs?|ms|seconds?|secs?|s|"
    r"megabytes?|mib|mb|gigabytes?|gib|gb|\u6beb\u79d2|\u79d2|\u5146\u5b57\u8282|\u5409\u5b57\u8282)",
    re.IGNORECASE,
)
_MARKDOWN_DECORATION_RE = re.compile(r"[`*_]")
_TRAILING_SEPARATORS = " \t|,\uff0c;\uff1b.\u3002"


class StatementResourceError(ValueError):
    """The statement does not contain one unambiguous supported limit pair."""


@dataclass(frozen=True)
class ResourceEvidence:
    kind: str
    line: int
    text: str
    raw_value: str
    raw_unit: str
    normalized_value: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "line": self.line,
            "text": self.text,
            "raw_value": self.raw_value,
            "raw_unit": self.raw_unit,
            "normalized_value": self.normalized_value,
        }


@dataclass(frozen=True)
class StatementResources:
    statement_path: str
    statement_sha256: str
    time_limit_ms: int
    memory_limit_mib: int
    time_evidence: tuple[ResourceEvidence, ...]
    memory_evidence: tuple[ResourceEvidence, ...]

    def canonical_payload(self) -> dict[str, Any]:
        """Return the hashable policy payload without its self-digest."""

        return {
            "schema_version": 1,
            "statement_path": self.statement_path,
            "statement_sha256": self.statement_sha256,
            "time_limit_ms": self.time_limit_ms,
            "memory_limit_mib": self.memory_limit_mib,
            "time_evidence": [item.as_dict() for item in self.time_evidence],
            "memory_evidence": [item.as_dict() for item in self.memory_evidence],
        }

    def canonical_sha256(self) -> str:
        return hashlib.sha256(
            json.dumps(
                self.canonical_payload(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

    def as_dict(self) -> dict[str, Any]:
        payload = self.canonical_payload()
        payload["canonical_sha256"] = self.canonical_sha256()
        return payload


def _normalized_integer(raw_value: str, factor: int, *, label: str) -> int:
    try:
        scaled = Decimal(raw_value) * factor
    except InvalidOperation as exc:
        raise StatementResourceError(f"{label} has an invalid numeric value") from exc
    integral = scaled.to_integral_value()
    if scaled != integral:
        raise StatementResourceError(
            f"{label} does not normalize to a whole canonical unit"
        )
    return int(integral)


def _normalize_limit(kind: str, raw_value: str, raw_unit: str) -> int:
    unit = raw_unit.casefold()
    if kind == "time":
        factors = {
            "millisecond": 1,
            "milliseconds": 1,
            "msec": 1,
            "msecs": 1,
            "ms": 1,
            "second": 1_000,
            "seconds": 1_000,
            "sec": 1_000,
            "secs": 1_000,
            "s": 1_000,
            "\u6beb\u79d2": 1,
            "\u79d2": 1_000,
        }
        if unit not in factors:
            raise StatementResourceError(
                "time limit unit must be ms or s (\u4e2d\u6587\u6beb\u79d2/\u79d2 is also accepted)"
            )
        return _normalized_integer(
            raw_value, factors[unit], label="time limit"
        )

    factors = {
        "megabyte": 1,
        "megabytes": 1,
        "mb": 1,
        "mib": 1,
        "gigabyte": 1_024,
        "gigabytes": 1_024,
        "gb": 1_024,
        "gib": 1_024,
        "\u5146\u5b57\u8282": 1,
        "\u5409\u5b57\u8282": 1_024,
    }
    if unit not in factors:
        raise StatementResourceError(
            "memory limit unit must be MB, MiB, GB, or GiB"
        )
    return _normalized_integer(raw_value, factors[unit], label="memory limit")


def _parse_segment(kind: str, segment: str, *, line_number: int) -> tuple[str, str, int]:
    candidate = segment.strip(_TRAILING_SEPARATORS)
    match = _VALUE_UNIT_RE.fullmatch(candidate)
    if match is None:
        expected = "ms/s" if kind == "time" else "MB/MiB/GB/GiB"
        raise StatementResourceError(
            f"line {line_number}: labelled {kind} limit must contain exactly "
            f"one numeric value and a supported {expected} unit"
        )
    raw_value = match.group("value")
    raw_unit = match.group("unit")
    try:
        normalized = _normalize_limit(kind, raw_value, raw_unit)
    except StatementResourceError as exc:
        raise StatementResourceError(f"line {line_number}: {exc}") from exc
    return raw_value, raw_unit, normalized


def parse_statement_resources(
    text: str, *, statement_sha256: str, statement_path: str = "statement.md"
) -> StatementResources:
    """Return the one explicit, supported TL/ML pair declared in ``text``."""

    evidence: dict[str, list[ResourceEvidence]] = {"time": [], "memory": []}
    issues: list[str] = []
    for line_number, original_line in enumerate(text.splitlines(), start=1):
        searchable = _MARKDOWN_DECORATION_RE.sub("", original_line)
        labels = list(_LABEL_RE.finditer(searchable))
        for index, label_match in enumerate(labels):
            kind = "time" if label_match.group("time") is not None else "memory"
            stop = labels[index + 1].start() if index + 1 < len(labels) else len(searchable)
            segment = searchable[label_match.end() : stop]
            try:
                raw_value, raw_unit, normalized = _parse_segment(
                    kind, segment, line_number=line_number
                )
            except StatementResourceError as exc:
                issues.append(str(exc))
                continue
            evidence[kind].append(
                ResourceEvidence(
                    kind=kind,
                    line=line_number,
                    text=original_line.strip(),
                    raw_value=raw_value,
                    raw_unit=raw_unit,
                    normalized_value=normalized,
                )
            )

    for kind, display in (("time", "time limit"), ("memory", "memory limit")):
        if not evidence[kind]:
            issues.append(f"statement has no explicit labelled {display}")
            continue
        values = sorted({item.normalized_value for item in evidence[kind]})
        if len(values) != 1:
            suffix = "ms" if kind == "time" else "MiB"
            issues.append(
                f"statement contains conflicting {display} values: "
                + ", ".join(f"{value}{suffix}" for value in values)
            )

    if evidence["time"]:
        time_limit_ms = evidence["time"][0].normalized_value
        if not MIN_TIME_LIMIT_MS <= time_limit_ms <= MAX_TIME_LIMIT_MS:
            issues.append(
                f"time limit {time_limit_ms}ms is outside supported range "
                f"{MIN_TIME_LIMIT_MS}..{MAX_TIME_LIMIT_MS}ms"
            )
    else:
        time_limit_ms = 0
    if evidence["memory"]:
        memory_limit_mib = evidence["memory"][0].normalized_value
        if not MIN_MEMORY_LIMIT_MIB <= memory_limit_mib <= MAX_MEMORY_LIMIT_MIB:
            issues.append(
                f"memory limit {memory_limit_mib}MiB is outside supported range "
                f"{MIN_MEMORY_LIMIT_MIB}..{MAX_MEMORY_LIMIT_MIB}MiB"
            )
    else:
        memory_limit_mib = 0

    if issues:
        raise StatementResourceError("; ".join(dict.fromkeys(issues)))

    return StatementResources(
        statement_path=statement_path,
        statement_sha256=statement_sha256,
        time_limit_ms=time_limit_ms,
        memory_limit_mib=memory_limit_mib,
        time_evidence=tuple(evidence["time"]),
        memory_evidence=tuple(evidence["memory"]),
    )


def load_statement_resources(problem_dir: Path) -> StatementResources:
    """Read and validate ``problem_dir/statement.md`` without writing files."""

    statement = problem_dir / "statement.md"
    if statement.is_symlink() or not statement.is_file():
        raise StatementResourceError(
            f"statement must be a non-symlink regular file: {statement}"
        )
    try:
        raw = statement.read_bytes()
    except OSError as exc:
        raise StatementResourceError(f"cannot read statement: {exc}") from exc
    if not raw:
        raise StatementResourceError("statement.md must not be empty")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise StatementResourceError(f"statement.md is not UTF-8: {exc}") from exc
    return parse_statement_resources(
        text,
        statement_sha256=hashlib.sha256(raw).hexdigest(),
        statement_path="statement.md",
    )
