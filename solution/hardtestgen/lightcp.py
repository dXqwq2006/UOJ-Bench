"""LightCP execution adapter for HardTestGen-generated Python and oracles."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from utils.testcase_eval_lightcp import _request_json

from .api import ExecutionResult


_BENCHMARKS = {
    "testcase-eval": ("testcase-eval", "Python 3"),
    "codecontests-plus": ("codecontests-plus", "python3"),
}


class HardTestGenLightCP:
    def __init__(self, base_url: str, benchmark: str):
        try:
            self.profile, self.python_language = _BENCHMARKS[benchmark]
        except KeyError as exc:
            raise ValueError(f"unsupported HardTestGen benchmark: {benchmark}") from exc
        self.base_url = base_url

    def preflight(self) -> Mapping[str, Any]:
        health = _request_json(self.base_url, "/health")
        profiles = health.get("profiles")
        if health.get("ok") is not True or not isinstance(profiles, Mapping):
            raise RuntimeError(f"LightCP health check failed: {health}")
        if self.profile not in profiles:
            raise RuntimeError(f"LightCP profile {self.profile!r} is unavailable")
        result = self.run_many(
            "python3",
            "print(int(input()) + 1)",
            ["1\n"],
            time_limit_ms=2_000,
            memory_limit_mb=256,
        )
        if len(result) != 1 or not result[0].succeeded or result[0].stdout != "2\n":
            raise RuntimeError(f"HardTestGen LightCP probe failed: {result}")
        return health

    def run_many(
        self,
        language: str,
        source: str,
        inputs: Sequence[str],
        *,
        time_limit_ms: int,
        memory_limit_mb: int,
    ) -> list[ExecutionResult]:
        if not inputs:
            return []
        compiler_language = self._language(language)
        response = _request_json(
            self.base_url,
            "/custom-test/batch",
            {
                "profile": self.profile,
                "lang": compiler_language,
                "code": source,
                "sourceName": self._source_name(compiler_language),
                "tests": [
                    {
                        "id": str(index),
                        "stdin": value,
                        "timeLimitMs": time_limit_ms,
                        "memoryLimitMb": memory_limit_mb,
                    }
                    for index, value in enumerate(inputs)
                ],
            },
        )
        raw_results = response.get("results")
        if not isinstance(raw_results, list):
            raise RuntimeError(f"LightCP batch response has no results: {response}")
        by_id = {
            str(value.get("id")): value
            for value in raw_results
            if isinstance(value, Mapping)
        }
        return [self._result(by_id.get(str(index))) for index in range(len(inputs))]

    def _language(self, language: str) -> str:
        normalized = language.strip()
        lowered = normalized.lower().replace(" ", "")
        if lowered in {"python", "python3", "py3", "pypy3", "pypy3-64"}:
            return self.python_language
        if self.profile == "testcase-eval":
            if normalized.startswith("C++"):
                return normalized
            if normalized in {"Python 3", "PyPy 3", "PyPy 3-64"}:
                return "Python 3"
        else:
            if normalized.upper() == "CPP" or normalized.startswith("C++"):
                return "cpp-gnu++17"
            if normalized.upper() == "PY3":
                return "python3"
        raise ValueError(f"unsupported {self.profile} oracle language: {language}")

    @staticmethod
    def _source_name(language: str) -> str:
        if language.startswith("C++") or language.startswith("cpp-"):
            return "main.cpp"
        return "main.py"

    @staticmethod
    def _result(value: Mapping[str, Any] | None) -> ExecutionResult:
        if value is None:
            return ExecutionResult("missing", stderr="LightCP omitted the test result")
        return ExecutionResult(
            str(value.get("status") or "unknown"),
            str(value.get("stdout") or ""),
            str(value.get("stderr") or value.get("message") or ""),
        )
