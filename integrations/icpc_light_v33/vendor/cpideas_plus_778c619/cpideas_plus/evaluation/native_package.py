"""CPIdeas native package adapter.

Wraps the JSON-config CPIdeas package layout described in
``docs/RUN_ARTIFACTS.md`` for local generation and verification.
"""

from __future__ import annotations

import json
from pathlib import Path

from .package_adapter import LocalPackageAdapter
from .spec import PackageSpec, PackageSolutionSpec, PackageTestSpec


class NativePackageAdapter(LocalPackageAdapter):
    """Adapter for the CPIdeas package layout (JSON-driven instead of XML)."""

    @property
    def generate_report_name(self) -> str:
        return "cpideas_generate_report.json"

    @property
    def verify_report_name(self) -> str:
        return "cpideas_report.json"

    def inspect(self) -> PackageSpec:
        """Parse ``config/package.json`` + sibling files into a ``PackageSpec``.

        Required files:
            * ``config/package.json`` — manifest with TL, ML, and component paths.
            * ``generator_script/generate.json`` — test plan (manual + generated).
            * ``solution_verdicts/expected.json`` — per-solution expected verdicts.

        TL defaults to 2000 ms and ML to 1024 MB when missing; these defaults match the
        values that ``NativePackageExporter.export`` writes.
        """
        config_path = self.package_dir / "config" / "package.json"
        if not config_path.exists():
            raise FileNotFoundError(f"config/package.json not found: {config_path}")
        config = _load_json_object(config_path)

        script_path = self.package_dir / _required_str(config, "generator_script")
        script = _load_json_object(script_path)
        tests = _load_tests(script)

        verdict_path = self.package_dir / _required_str(config, "solution_verdicts")
        verdicts = _load_json_object(verdict_path)
        solutions = _load_solutions(verdicts)

        generators = _load_generators(config)
        default_generator = _optional_path(config, "generator")
        if default_generator is None and generators:
            # Pick a deterministic default for backward-compatible single-generator
            # consumers. Prefer an entry literally named "generator" if it exists.
            default_generator = generators.get("generator") or next(
                iter(generators.values())
            )

        return PackageSpec(
            root=self.package_dir,
            short_name=str(config.get("id", self.package_dir.name)),
            name=str(config.get("title", config.get("id", self.package_dir.name))),
            time_limit_ms=int(config.get("time_limit_ms", 2000)),
            memory_limit_bytes=int(config.get("memory_limit_mb", 256)) * (1 << 20),
            input_pattern="tests/%03d.in",
            answer_pattern="tests/%03d.ans",
            generator_source=default_generator,
            validator_source=_optional_path(config, "validator"),
            checker_source=_optional_path(config, "checker"),
            tests=tests,
            solutions=solutions,
            format="cpideas",
            generators=generators or None,
        )


def _load_json_object(path: Path) -> dict[str, object]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return data


def _required_str(data: dict[str, object], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"config/package.json must contain string field {key!r}")
    return value


def _optional_path(data: dict[str, object], key: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError(
            f"config/package.json field {key!r} must be a string when present"
        )
    return value


def _load_tests(script: dict[str, object]) -> list[PackageTestSpec]:
    raw_tests = script.get("tests")
    if not isinstance(raw_tests, list) or not raw_tests:
        raise ValueError("generator_script must contain a non-empty tests array")

    tests: list[PackageTestSpec] = []
    for index, item in enumerate(raw_tests, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"generator_script test #{index} must be an object")
        kind = str(item.get("kind", "")).lower()
        test_id = str(item.get("id", f"{index:03d}"))
        group = None if item.get("group") is None else str(item.get("group"))
        input_path = f"tests/{index:03d}.in"
        answer_path = f"tests/{index:03d}.ans"
        if kind == "manual":
            if "input" not in item:
                raise ValueError(f"manual test {test_id!r} must contain input")
            tests.append(
                PackageTestSpec(
                    index=index,
                    method="manual",
                    sample=group == "samples" or bool(item.get("sample", False)),
                    cmd=None,
                    input_path=input_path,
                    answer_path=answer_path,
                    group=group,
                    manual_input=str(item["input"]),
                )
            )
        elif kind == "generated":
            args = item.get("args")
            if not isinstance(args, list):
                raise ValueError(f"generated test {test_id!r} must contain args array")
            generator_name = item.get("generator")
            if generator_name is not None and not isinstance(generator_name, str):
                raise ValueError(
                    f"generated test {test_id!r} field 'generator' must be a string when present"
                )
            tests.append(
                PackageTestSpec(
                    index=index,
                    method="generated",
                    sample=bool(item.get("sample", False)),
                    cmd=" ".join(str(part) for part in args),
                    input_path=input_path,
                    answer_path=answer_path,
                    group=group,
                    generator_args=[str(part) for part in args],
                    generator=generator_name,
                )
            )
        else:
            raise ValueError(
                f"Unsupported generator_script test kind for {test_id!r}: {kind!r}"
            )
    return tests


def _load_generators(config: dict[str, object]) -> dict[str, str]:
    """Parse ``config['generators']`` into a ``name -> source_path`` map.

    The field is optional. When absent the caller still gets the single
    ``config['generator']`` via ``_optional_path`` for backward compatibility. When
    present it must map non-empty string names to non-empty source paths.
    """
    raw = config.get("generators")
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("config/package.json field 'generators' must be a JSON object")
    result: dict[str, str] = {}
    for name, value in raw.items():
        if not isinstance(name, str) or not name:
            raise ValueError(
                "Each key in config/package.json 'generators' must be a non-empty string"
            )
        if not isinstance(value, str) or not value:
            raise ValueError(
                f"config/package.json 'generators[{name!r}]' must be a non-empty path"
            )
        result[name] = value
    return result


def _load_solutions(verdicts: dict[str, object]) -> list[PackageSolutionSpec]:
    raw_solutions = verdicts.get("solutions")
    if not isinstance(raw_solutions, list) or not raw_solutions:
        raise ValueError("solution_verdicts must contain a non-empty solutions array")
    solutions: list[PackageSolutionSpec] = []
    for index, item in enumerate(raw_solutions, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"solution_verdicts entry #{index} must be an object")
        path = item.get("path")
        if not isinstance(path, str) or not path:
            raise ValueError(f"solution_verdicts entry #{index} must contain path")
        role = str(item.get("role", Path(path).stem)).lower().replace("_", "-")
        expected = str(item.get("expected", "REJECTED")).upper()
        test_scope = (
            None if item.get("test_scope") is None else str(item.get("test_scope"))
        )
        solutions.append(PackageSolutionSpec(role, path, expected, test_scope))
    if not any(solution.tag == "main" for solution in solutions):
        raise ValueError("solution_verdicts must include one solution with role main")
    return solutions
