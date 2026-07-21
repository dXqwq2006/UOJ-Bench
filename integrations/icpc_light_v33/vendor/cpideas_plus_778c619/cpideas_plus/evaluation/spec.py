"""Neutral package specs and verification result data classes."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from .local_runtime import CommandResult, TestPointResult


@dataclass(frozen=True)
class PackageTestSpec:
    """One row in the materialized test plan.

    ``generator`` names which compiled generator binary should produce this test's
    input. When ``None``, the package's default generator (``PackageSpec`` field
    ``generator_source``) is used.
    """

    index: int
    method: str
    sample: bool
    cmd: str | None
    input_path: str
    answer_path: str
    group: str | None = None
    manual_input: str | None = None
    generator_args: list[str] | None = None
    generator: str | None = None


@dataclass(frozen=True)
class PackageSolutionSpec:
    tag: str
    source_path: str
    expected: str | None = None
    test_scope: str | None = None


@dataclass(frozen=True)
class PackageSpec:
    """Parsed package metadata used by ``generate`` / ``verify``.

    ``generator_source`` is the *default* generator; CPIdeas packages can additionally
    declare multiple generators via ``generators`` (mapping ``name -> source_path``).
    Each ``PackageTestSpec`` may then choose one by name via its ``generator`` field.
    """

    root: Path
    short_name: str
    name: str
    time_limit_ms: int
    memory_limit_bytes: int
    input_pattern: str
    answer_pattern: str
    generator_source: str | None
    validator_source: str | None
    checker_source: str | None
    tests: list[PackageTestSpec]
    solutions: list[PackageSolutionSpec]
    format: str = "cpideas"
    generators: dict[str, str] | None = None

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["root"] = str(self.root)
        return data

    def resolved_generators(self) -> dict[str, str]:
        """Return the effective ``name -> source_path`` map.

        Falls back to ``{"generator": generator_source}`` when ``generators`` is unset
        and a default ``generator_source`` exists. Returns ``{}`` when no generator is
        declared at all.
        """
        if self.generators:
            return dict(self.generators)
        if self.generator_source:
            return {"generator": self.generator_source}
        return {}

    def default_generator_name(self) -> str | None:
        """Return the name used for tests that do not pin a specific generator."""
        if self.generators:
            # Prefer an entry literally named "generator" if it exists.
            if "generator" in self.generators:
                return "generator"
            return next(iter(self.generators))
        if self.generator_source:
            return "generator"
        return None


@dataclass(frozen=True)
class SolutionResult:
    source_path: str
    tag: str
    expected: str
    verdict: str
    compile: CommandResult
    failed_test: str | None = None
    detail: str = ""
    tests: list[TestPointResult] | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "source_path": self.source_path,
            "tag": self.tag,
            "expected": self.expected,
            "verdict": self.verdict,
            "compile": self.compile.to_dict(),
            "failed_test": self.failed_test,
            "detail": self.detail,
            "tests": [test.to_dict() for test in self.tests or []],
        }


__all__ = [
    "PackageSolutionSpec",
    "PackageSpec",
    "PackageTestSpec",
    "SolutionResult",
]
