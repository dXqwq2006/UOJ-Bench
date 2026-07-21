from __future__ import annotations

import copy
import json
import shutil
import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace


SCRIPT_DIR = (
    Path(__file__).resolve().parents[1]
    / "skills/icpc-light-problem-builder/scripts"
)
sys.path.insert(0, str(SCRIPT_DIR))

import regression_backend as backend_module  # noqa: E402
import record_adversarial_round as round_recorder  # noqa: E402
import run_regression_gate as gate  # noqa: E402
import verify_adversarial_round_chain as round_chain  # noqa: E402
import verify_completion as completion  # noqa: E402


@dataclass(frozen=True)
class Source:
    role: str
    rel: str
    path: Path


def success(stdout: bytes = b"") -> backend_module.ProgramResult:
    return backend_module.ProgramResult(
        returncode=0,
        timed_out=False,
        duration_seconds=0.001,
        stdout=stdout,
        stderr=b"",
    )


class RecordingBackend:
    name = "recording"

    def __init__(self, *, validator_failure: int | None = None):
        self.validator_failure = validator_failure
        self.calls: list[tuple[str, int]] = []

    def run_dataset(self, program, dataset, *, problem_dir, timeout):
        del problem_dir, timeout
        self.calls.append((program.role, len(dataset)))
        results = []
        for index, invocation in enumerate(dataset):
            if program.role == "generator":
                parameter = invocation.argv[0]
                results.append(success(f"{parameter}\n".encode()))
            elif program.role == "validator" and index == self.validator_failure:
                results.append(
                    backend_module.ProgramResult(
                        returncode=1,
                        timed_out=False,
                        duration_seconds=0.001,
                        stdout=b"",
                        stderr=b"rejected",
                    )
                )
            else:
                results.append(success(invocation.stdin))
        return results


def programs() -> dict[str, backend_module.PreparedProgram]:
    return {
        role: backend_module.PreparedProgram(
            role=role,
            source_rel=f"package/{role}.cpp",
            source_path=Path(f"/{role}.cpp"),
            source_sha256=role,
        )
        for role in ("generator", "validator", "std", "brute")
    }


def differential(count: int) -> gate.DifferentialSpec:
    generator_source = gate.SourceSpec(
        "generator", "package/generators/tiny.cpp", Path(__file__).resolve()
    )
    return gate.DifferentialSpec(
        mode="tiny-exhaustive",
        generator=gate.GeneratorSpec(generator_source, ("{case}",)),
        start=0,
        count=count,
        placeholder="{case}",
    )


class BackendFactoryTests(unittest.TestCase):
    def test_local_backend_requires_explicit_test_mode(self):
        with self.assertRaisesRegex(backend_module.BackendError, "testing-only"):
            backend_module.create_backend(
                "local",
                test_mode=False,
                lightcpverifier_url="http://127.0.0.1:8081",
                program_time_limit_ms=2000,
                memory_limit_mb=1024,
            )

    def test_missing_cpideas_dependency_is_explicit(self):
        if shutil.which("python3") is None:
            self.skipTest("python3 unavailable")
        # The test suite is intentionally standard-library-only and does not add
        # CPIdeas-Plus/src to sys.path.  A missing integration must fail closed.
        try:
            import cpideas_plus.evaluation.dataset  # type: ignore[import-not-found] # noqa: F401
        except ImportError:
            with self.assertRaisesRegex(
                backend_module.BackendError, "requires CPIdeas-Plus"
            ):
                backend_module.create_backend(
                    "lightcpverifier",
                    test_mode=True,
                    lightcpverifier_url="http://127.0.0.1:1",
                    program_time_limit_ms=2000,
                    memory_limit_mb=1024,
                )

    def test_service_identity_requires_matching_attested_digests(self):
        digest = "sha256:" + "a" * 64
        identity = backend_module.validated_lightcp_service_identity(
            {
                "ok": True,
                "service": {
                    "apiRevision": backend_module.LIGHTCP_API_REVISION,
                    "compilerProfile": backend_module.LIGHTCP_CPP_PROFILE,
                    "buildId": digest,
                    "imageId": digest,
                    "goJudgeVersion": "1.12.1",
                    "nodeVersion": "v20.19.4",
                    "executionPolicy": {
                        "runtime": {
                            "minimumCpuTimeMs": 100,
                            "maximumCpuTimeMs": 30000,
                            "wallTimeMultiplier": 2,
                            "minimumMemoryMb": 16,
                            "maximumMemoryMb": 2048,
                            "maximumOutputBytes": 16 * 1024 * 1024,
                        },
                        "compilation": {
                            "cpp": {
                                "cpuTimeMs": 10000,
                                "memoryMb": 512,
                                "processLimit": 50,
                            }
                        },
                        "batch": {
                            "maxTests": 128,
                            "maxCapturedOutputBytes": 32 * 1024 * 1024,
                        },
                    },
                },
            }
        )
        self.assertEqual(identity["buildId"], digest)
        with self.assertRaisesRegex(backend_module.BackendError, "not an attested"):
            backend_module.validated_lightcp_service_identity(
                {
                    "ok": True,
                    "service": {
                        "apiRevision": backend_module.LIGHTCP_API_REVISION,
                        "compilerProfile": backend_module.LIGHTCP_CPP_PROFILE,
                        "buildId": "unversioned",
                        "imageId": digest,
                    },
                }
            )
        invalid_hex = copy.deepcopy(identity)
        invalid_hex["buildId"] = "sha256:" + "g" * 64
        with self.assertRaisesRegex(backend_module.BackendError, "not an attested"):
            backend_module.validated_lightcp_service_identity(
                {"ok": True, "service": invalid_hex}
            )


class LocalBackendTests(unittest.TestCase):
    def test_compile_once_then_run_ordered_dataset(self):
        if not any(shutil.which(name) for name in ("c++", "g++", "clang++")):
            self.skipTest("C++ compiler unavailable")
        with tempfile.TemporaryDirectory() as raw_problem, tempfile.TemporaryDirectory() as raw_build:
            problem = Path(raw_problem)
            build = Path(raw_build)
            package = problem / "package"
            package.mkdir()
            source_path = package / "echo.cpp"
            source_path.write_text(
                """
#include <fstream>
#include <iostream>
#include <string>
int main(int argc, char **argv) {
    std::string input, file;
    std::getline(std::cin, input);
    if (argc > 1) { std::ifstream in(argv[1]); std::getline(in, file); }
    std::cout << input << ':' << file << '\\n';
}
""".strip()
                + "\n",
                encoding="utf-8",
            )
            backend = backend_module.LocalProgramDatasetBackend()
            prepared, records, errors = backend.compile_sources(
                [Source("echo", "package/echo.cpp", source_path)],
                problem_dir=problem,
                build_dir=build,
                timeout=30,
            )
            self.assertEqual(errors, [])
            self.assertEqual(records[0]["status"], "passed")
            observed = backend.run_dataset(
                prepared["echo"],
                [
                    backend_module.DatasetInvocation(
                        stdin=b"one\n",
                        argv=("payload.txt",),
                        copy_in_files={"payload.txt": b"A\n"},
                    ),
                    backend_module.DatasetInvocation(
                        stdin=b"two\n",
                        argv=("payload.txt",),
                        copy_in_files={"payload.txt": b"B\n"},
                    ),
                ],
                problem_dir=problem,
                timeout=2,
            )
            self.assertEqual([item.stdout for item in observed], [b"one:A\n", b"two:B\n"])


class LightCPCompileContextTests(unittest.TestCase):
    def _run_fake_case(
        self,
        *,
        verdict: str,
        raw_ok: bool,
        raw_status: str,
        exit_status: int | None,
        evaluation_complete: bool = True,
        evaluation_status: str = "completed",
        returned_id: str = "case-a",
        case_output_truncated: bool = False,
        raw_present: bool = True,
    ) -> backend_module.ProgramResult:
        try:
            from cpideas_plus.evaluation.dataset import DatasetProgram, DatasetTest
        except ImportError:
            self.skipTest("CPIdeas-Plus dataset API is not installed")

        raw = SimpleNamespace(
            status=raw_status,
            raw_status=raw_status,
            ok=raw_ok,
            output_truncated=False,
            executed=True,
            exit_status=exit_status,
            signal=None,
            time_ns=1_000_000,
            time_ms=1,
            memory_bytes=64,
            stdout="output\n",
            stderr="diagnostic\n",
        )
        case = SimpleNamespace(
            index=0,
            id=returned_id,
            verdict=verdict,
            output_truncated=case_output_truncated,
            stdout=raw.stdout,
            stderr=raw.stderr,
            raw=raw if raw_present else None,
            diagnostic="dataset diagnostic",
        )

        class FakeEvaluation(SimpleNamespace):
            def to_receipt(self):
                return {
                    "schema_version": 1,
                    "kind": "cpideas.program_dataset_evaluation",
                    "status": self.status,
                    "ok": False,
                    "evaluation_complete": self.evaluation_complete,
                    "error": self.error,
                    "configuration": {
                        "requested_time_limit_ms": 2000,
                        "effective_time_limit_ms": 2000,
                        "requested_memory_limit_mb": 1024,
                        "effective_memory_limit_mb": 1024,
                        "max_output_bytes": backend_module.LIGHTCP_MAX_OUTPUT_BYTES,
                        "batch_size": 128,
                        "max_request_bytes": 60 * 1024 * 1024,
                    },
                    "summary": {"total": 1, "verdict_counts": {verdict: 1}},
                    "chunks": [
                        {
                            "index": 0,
                            "start": 0,
                            "stop": 1,
                            "status": self.status,
                            "output_truncated": case_output_truncated,
                        }
                    ],
                    "cases": [
                        {"index": 0, "id": returned_id, "verdict": verdict}
                    ],
                }

        evaluation = FakeEvaluation(
            cases=(case,),
            status=evaluation_status,
            evaluation_complete=evaluation_complete,
            error=None if evaluation_complete else "batch infrastructure failure",
        )

        class FakeEvaluator:
            def evaluate(self, program, tests, **kwargs):
                del program, tests, kwargs
                return evaluation

        backend = backend_module.LightCPVerifierProgramDatasetBackend.__new__(
            backend_module.LightCPVerifierProgramDatasetBackend
        )
        backend._DatasetProgram = DatasetProgram
        backend._DatasetTest = DatasetTest
        backend._evaluator = FakeEvaluator()
        backend._invocations = []
        backend._program_time_limit_ms = 2000
        backend._effective_time_limit_ms = 2000
        backend._effective_memory_limit_mb = 1024
        program = backend_module.PreparedProgram(
            role="candidate",
            source_rel="package/candidate.cpp",
            source_path=Path("/package/candidate.cpp"),
            source_sha256="1" * 64,
            opaque={
                "code": "int main(){}",
                "source_name": "package/candidate.cpp",
                "compile_copy_in_files": {},
            },
        )
        return backend.run_dataset(
            program,
            [backend_module.DatasetInvocation(stdin=b"1\n", case_id="case-a")],
            problem_dir=Path("/problem"),
            timeout=2,
        )[0]

    def test_adapter_matches_cpideas_dataset_contract_when_installed(self):
        try:
            from cpideas_plus.evaluation.dataset import DatasetProgram, DatasetTest
        except ImportError:
            self.skipTest("CPIdeas-Plus dataset API is not installed")

        class FakeEvaluator:
            def __init__(self):
                self.call = None

            def evaluate(self, program, tests, **kwargs):
                self.call = (program, tests, kwargs)
                cases = []
                for index, test in enumerate(tests):
                    raw = SimpleNamespace(
                        status="exited",
                        raw_status="Accepted",
                        ok=True,
                        output_truncated=False,
                        executed=True,
                        exit_status=0,
                        signal=None,
                        time_ns=2_000_000,
                        time_ms=2,
                        memory_bytes=64,
                        stdout=test.stdin,
                        stderr="",
                    )
                    cases.append(
                        SimpleNamespace(
                            index=index,
                            id=test.id,
                            verdict="EXECUTED",
                            output_truncated=False,
                            stdout=test.stdin,
                            stderr="",
                            raw=raw,
                            diagnostic="",
                        )
                    )

                class FakeEvaluation(SimpleNamespace):
                    def to_receipt(self):
                        return {
                            "schema_version": 1,
                            "kind": "cpideas.program_dataset_evaluation",
                            "status": self.status,
                            "ok": True,
                            "evaluation_complete": self.evaluation_complete,
                            "error": self.error,
                            "configuration": {
                                "requested_time_limit_ms": 2000,
                                "effective_time_limit_ms": 2000,
                                "requested_memory_limit_mb": 1024,
                                "effective_memory_limit_mb": 1024,
                                "max_output_bytes": kwargs["max_output_bytes"],
                                "batch_size": 128,
                                "max_request_bytes": 60 * 1024 * 1024,
                            },
                            "summary": {
                                "total": len(cases),
                                "verdict_counts": {"EXECUTED": len(cases)},
                            },
                            "chunks": [],
                            "cases": [
                                {
                                    "index": case.index,
                                    "id": case.id,
                                    "verdict": case.verdict,
                                }
                                for case in cases
                            ],
                        }

                return FakeEvaluation(
                    cases=tuple(cases),
                    status="completed",
                    evaluation_complete=True,
                    error=None,
                )

        backend = backend_module.LightCPVerifierProgramDatasetBackend.__new__(
            backend_module.LightCPVerifierProgramDatasetBackend
        )
        backend._DatasetProgram = DatasetProgram
        backend._DatasetTest = DatasetTest
        backend._evaluator = FakeEvaluator()
        backend._invocations = []
        backend._program_time_limit_ms = 2000
        backend._effective_time_limit_ms = 2000
        backend._effective_memory_limit_mb = 1024
        program = backend_module.PreparedProgram(
            role="std",
            source_rel="package/std.cpp",
            source_path=Path("/package/std.cpp"),
            source_sha256="0" * 64,
            opaque={
                "code": "int main(){}",
                "source_name": "package/std.cpp",
                "compile_copy_in_files": {"package/testlib.h": ""},
            },
        )
        observed = backend.run_dataset(
            program,
            [
                backend_module.DatasetInvocation(stdin=b"1\n", case_id="a"),
                backend_module.DatasetInvocation(stdin=b"2\n", case_id="b"),
            ],
            problem_dir=Path("/problem"),
            timeout=2,
        )
        self.assertEqual([item.stdout for item in observed], [b"1\n", b"2\n"])
        source, tests, kwargs = backend._evaluator.call
        self.assertIsInstance(source, DatasetProgram)
        self.assertTrue(all(isinstance(test, DatasetTest) for test in tests))
        self.assertEqual(kwargs["comparison"], "none")
        self.assertEqual(kwargs["time_limit_ms"], 2000)
        self.assertEqual(observed[0].sandbox_verdict, "EXECUTED")
        self.assertEqual(len(backend._invocations), 1)
        self.assertEqual(
            backend._invocations[0]["evaluation"]["case_results_binding"]["count"],
            2,
        )

    def test_timeout_detection_trusts_sandbox_status_not_elapsed_telemetry(self):
        self.assertTrue(backend_module._lightcp_timed_out("Time Limit Exceeded"))
        self.assertFalse(backend_module._lightcp_timed_out("Accepted"))

    def test_case_infrastructure_verdict_cannot_pass_with_exit_zero(self):
        result = self._run_fake_case(
            verdict="INFRA",
            raw_ok=True,
            raw_status="Accepted",
            exit_status=0,
            evaluation_complete=False,
            evaluation_status="infrastructure_error",
        )
        self.assertIsNotNone(result.launch_error)
        self.assertFalse(backend_module.process_succeeded(result))
        self.assertEqual(result.sandbox_verdict, "INFRA")

    def test_incomplete_evaluation_cannot_hide_behind_expected_tle(self):
        result = self._run_fake_case(
            verdict="TLE",
            raw_ok=False,
            raw_status="Time Limit Exceeded",
            exit_status=None,
            evaluation_complete=False,
            evaluation_status="infrastructure_error",
        )
        self.assertFalse(result.timed_out)
        self.assertIsNotNone(result.launch_error)
        self.assertEqual(result.sandbox_verdict, "TLE")

    def test_missing_raw_result_is_infrastructure_even_for_tle(self):
        result = self._run_fake_case(
            verdict="TLE",
            raw_ok=False,
            raw_status="Time Limit Exceeded",
            exit_status=None,
            evaluation_complete=False,
            evaluation_status="infrastructure_error",
            raw_present=False,
        )
        self.assertFalse(result.timed_out)
        self.assertRegex(result.launch_error or "", "no low-level")

    def test_raw_not_ok_with_exit_zero_is_program_failure(self):
        result = self._run_fake_case(
            verdict="RE",
            raw_ok=False,
            raw_status="runtime_error",
            exit_status=0,
        )
        self.assertEqual(result.returncode, -1)
        self.assertIsNone(result.launch_error)
        self.assertFalse(backend_module.process_succeeded(result))

    def test_mle_and_ole_preserve_exact_sandbox_verdicts(self):
        for verdict, status in (
            ("MLE", "Memory Limit Exceeded"),
            ("OLE", "Output Limit Exceeded"),
        ):
            with self.subTest(verdict=verdict):
                result = self._run_fake_case(
                    verdict=verdict,
                    raw_ok=False,
                    raw_status=status,
                    exit_status=None,
                )
                self.assertEqual(result.returncode, -1)
                self.assertIsNone(result.launch_error)
                self.assertEqual(result.sandbox_verdict, verdict)
                observed = gate.classify_wrong_results_dataset(
                    [result],
                    input_data=[b"1\n"],
                    reference_outputs=[b"1\n"],
                    checker=None,
                    checker_contract=gate.load_checker_contract({}),
                    backend=RecordingBackend(),
                    problem_dir=Path("/problem"),
                    timeout=2,
                )
                self.assertEqual(observed[0][0], verdict)

    def test_case_id_mismatch_fails_closed(self):
        with self.assertRaisesRegex(
            backend_module.BackendError, "identity mismatch"
        ):
            self._run_fake_case(
                verdict="EXECUTED",
                raw_ok=True,
                raw_status="Accepted",
                exit_status=0,
                returned_id="wrong-id",
            )

    def test_compile_files_exclude_every_problem_owned_source_and_header(self):
        with tempfile.TemporaryDirectory() as raw_problem:
            problem = Path(raw_problem)
            source = problem / "audit/private/wrong-solutions/W01.cpp"
            source.parent.mkdir(parents=True)
            source.write_text('#include "testlib.h"\nint main() {}\n', encoding="utf-8")
            package = problem / "package"
            package.mkdir(parents=True)
            for name in (
                "secret.c",
                "secret.cc",
                "std.cpp",
                "secret.cxx",
                "testlib.h",
                "secret.hh",
                "helper.hpp",
                "secret.hxx",
                "secret.inc",
            ):
                (package / name).write_text(f"// problem-owned {name}\n", encoding="utf-8")

            self.assertEqual(backend_module._compile_copy_in_files(), {})
            self.assertEqual(
                backend_module.compile_context_sha256(),
                backend_module.canonical_sha256({}),
            )

    def test_compile_record_binds_echoed_compiler_limits_and_context(self):
        try:
            from cpideas_plus.evaluation.dataset import DatasetProgram
        except ImportError:
            self.skipTest("CPIdeas-Plus dataset API is not installed")
        with tempfile.TemporaryDirectory() as raw_problem, tempfile.TemporaryDirectory() as raw_build:
            problem = Path(raw_problem)
            build = Path(raw_build)
            source = problem / "package/std.cpp"
            source.parent.mkdir(parents=True)
            source.write_text("int main() {}\n", encoding="utf-8")
            (problem / "package/helper.h").write_text("// helper\n", encoding="utf-8")
            (problem / "package/testlib.h").write_text(
                "// untrusted problem-owned testlib\n", encoding="utf-8"
            )
            (problem / "package/brute.cpp").write_text(
                "// private oracle\n", encoding="utf-8"
            )
            digest = backend_module.sha256_file(source)
            result = SimpleNamespace(
                status="COMPILED",
                ok=True,
                diagnostic="",
                source_sha256=digest,
                cached=False,
                time_ms=10,
                requested_time_limit_ms=2000,
                effective_time_limit_ms=2000,
                effective_wall_time_limit_ms=4000,
                requested_memory_limit_mb=1024,
                effective_memory_limit_mb=1024,
                requested_max_output_bytes=16 * 1024 * 1024,
                effective_max_output_bytes=16 * 1024 * 1024,
                compiler_cpu_time_limit_ms=10000,
                compiler_memory_limit_mb=512,
                compiler_process_limit=50,
            )

            class FakeEvaluator:
                def __init__(self):
                    self.programs = []

                def compile(self, program, **kwargs):
                    self.programs.append(program)
                    del kwargs
                    return result

            backend = backend_module.LightCPVerifierProgramDatasetBackend.__new__(
                backend_module.LightCPVerifierProgramDatasetBackend
            )
            backend._DatasetProgram = DatasetProgram
            evaluator = FakeEvaluator()
            backend._evaluator = evaluator
            backend._dataset_api_revision = backend_module.LIGHTCP_DATASET_API_REVISION
            backend._program_time_limit_ms = 2000
            backend._effective_time_limit_ms = 2000
            backend._effective_memory_limit_mb = 1024
            backend._service_identity = {
                "executionPolicy": {
                    "runtime": {"wallTimeMultiplier": 2},
                    "compilation": {
                        "cpp": {
                            "cpuTimeMs": 10000,
                            "memoryMb": 512,
                            "processLimit": 50,
                        }
                    },
                }
            }
            programs, records, errors = backend.compile_sources(
                [Source("std", "package/std.cpp", source)],
                problem_dir=problem,
                build_dir=build,
                timeout=120,
            )
            self.assertEqual(errors, [])
            self.assertIn("std", programs)
            self.assertEqual(len(evaluator.programs), 1)
            compiled_program = evaluator.programs[0]
            self.assertEqual(compiled_program.source_name, "package/std.cpp")
            self.assertEqual(dict(compiled_program.compile_copy_in_files or {}), {})
            evidence = records[0]["compilation_evidence"]
            self.assertEqual(
                evidence["compile_context_policy_revision"],
                backend_module.COMPILE_CONTEXT_POLICY_REVISION,
            )
            self.assertEqual(
                evidence["compile_copy_in_files_sha256"],
                backend_module.canonical_sha256({}),
            )
            self.assertEqual(
                evidence["compiler_limits"],
                {"cpu_time_ms": 10000, "memory_mb": 512, "process_limit": 50},
            )
            core = dict(evidence)
            evidence_hash = core.pop("evidence_sha256")
            self.assertEqual(evidence_hash, backend_module.canonical_sha256(core))
            check = completion.Check("compile-evidence")
            self.assertTrue(
                completion.validate_lightcp_compilation_evidence(
                    check,
                    records[0],
                    problem_dir=problem,
                    source=source,
                    role="std",
                    canonical_time_limit_ms=2000,
                    canonical_memory_limit_mib=1024,
                    canonical_wall_time_multiplier=2,
                ),
                check.issues,
            )
            self.assertEqual(check.issues, [])

            stale_record = copy.deepcopy(records[0])
            stale_evidence = stale_record["compilation_evidence"]
            stale_evidence["compile_context_policy_revision"] = "legacy-full-tree"
            stale_core = dict(stale_evidence)
            stale_core.pop("evidence_sha256")
            stale_evidence["evidence_sha256"] = backend_module.canonical_sha256(
                stale_core
            )
            stale_check = completion.Check("stale-compile-policy")
            self.assertFalse(
                completion.validate_lightcp_compilation_evidence(
                    stale_check,
                    stale_record,
                    problem_dir=problem,
                    source=source,
                    role="std",
                    canonical_time_limit_ms=2000,
                    canonical_memory_limit_mib=1024,
                    canonical_wall_time_multiplier=2,
                )
            )
            self.assertRegex(
                "\n".join(stale_check.issues),
                "compile_context_policy_revision",
            )

            result.compiler_memory_limit_mb = 511
            failed_programs, failed_records, failed_errors = backend.compile_sources(
                [Source("std", "package/std.cpp", source)],
                problem_dir=problem,
                build_dir=build,
                timeout=120,
            )
            self.assertNotIn("std", failed_programs)
            self.assertTrue(failed_errors)
            self.assertEqual(failed_records[0]["status"], "failed")


class DifferentialBatchTests(unittest.TestCase):
    def test_each_program_receives_one_ordered_dataset(self):
        backend = RecordingBackend()
        receipt, errors = gate.run_differential(
            differential(300),
            programs(),
            checker=None,
            checker_contract=gate.load_checker_contract({}),
            backend=backend,
            problem_dir=Path("/problem"),
            timeout=2,
        )
        self.assertEqual(errors, [])
        self.assertEqual(receipt["completed_cases"], 300)
        self.assertEqual(
            backend.calls,
            [("generator", 300), ("validator", 300), ("std", 300), ("brute", 300)],
        )

    def test_first_failure_receipt_keeps_sequential_prefix(self):
        backend = RecordingBackend(validator_failure=2)
        receipt, errors = gate.run_differential(
            differential(5),
            programs(),
            checker=None,
            checker_contract=gate.load_checker_contract({}),
            backend=backend,
            problem_dir=Path("/problem"),
            timeout=2,
        )
        self.assertEqual(errors, ["differential case 2: validator rejected input"])
        self.assertEqual(receipt["generated_cases"], 3)
        self.assertEqual(receipt["validated_cases"], 2)
        self.assertEqual(receipt["completed_cases"], 2)
        self.assertEqual(receipt["first_failure"]["stage"], "validator")
        self.assertEqual(receipt["first_failure"]["ordinal"], 2)
        self.assertEqual(
            backend.calls,
            [("generator", 5), ("validator", 5), ("std", 2), ("brute", 2)],
        )


class RegressionAcceptedAndSurvivabilityTests(unittest.TestCase):
    @staticmethod
    def _plan_fixture(root: Path) -> tuple[dict, Path]:
        statement = root / "statement.md"
        statement.write_text(
            "Time Limit: 2 s\nMemory Limit: 256 MiB\n", encoding="utf-8"
        )
        package = root / "package"
        generators = package / "generators"
        samples = package / "samples"
        tests = package / "tests"
        generators.mkdir(parents=True)
        samples.mkdir(parents=True)
        tests.mkdir(parents=True)
        for name, content in (
            ("std.cpp", "int main(){return 0;}\n"),
            ("brute.cpp", "int main(){int x=0;return x;}\n"),
            ("validator.cpp", "int main(){return 0;}\n"),
        ):
            (package / name).write_text(content, encoding="utf-8")
        (generators / "tiny.cpp").write_text(
            "int main(){return 0;}\n", encoding="utf-8"
        )
        sample_input = samples / "sample-01.in"
        sample_answer = samples / "sample-01.ans"
        sample_input.write_text("1\n", encoding="utf-8")
        sample_answer.write_text("1\n", encoding="utf-8")
        manifest = {
            "schema_version": 1,
            "statement_path": "statement.md",
            "statement_sha256": gate.sha256_file(statement),
            "samples": [
                {
                    "sample_id": "sample-01",
                    "statement_ordinal": 1,
                    "input": "package/samples/sample-01.in",
                    "input_sha256": gate.sha256_file(sample_input),
                    "answer": "package/samples/sample-01.ans",
                    "answer_sha256": gate.sha256_file(sample_answer),
                }
            ],
        }
        (samples / "manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
        release_items = []
        for index, rel in enumerate(
            (
                "ordinary/ordinary-1",
                "survivability/random-1",
                "survivability/structured-1",
                "breakers/W01-breaker",
            )
        ):
            input_path = tests / f"{rel}.in"
            answer_path = tests / f"{rel}.ans"
            input_path.parent.mkdir(parents=True, exist_ok=True)
            input_path.write_text(f"{index}\n", encoding="utf-8")
            answer_path.write_text(f"{index}\n", encoding="utf-8")
            release_items.append(
                {
                    "test_id": f"T{index}",
                    "input": input_path.relative_to(root).as_posix(),
                    "answer": answer_path.relative_to(root).as_posix(),
                    "limit_tags": ["n=max"] if index == 0 else [],
                }
            )
        wrong_source = root / "audit/private/wrong-solutions/W01.cpp"
        wrong_source.parent.mkdir(parents=True)
        wrong_source.write_text("int main(){return 1;}\n", encoding="utf-8")
        alternative_source = root / "audit/private/accepted-solutions/A01.cpp"
        alternative_source.parent.mkdir(parents=True)
        alternative_source.write_text("int main(){return 2;}\n", encoding="utf-8")
        statement_resources = gate.load_statement_resources(root)
        design_basis = {
            "intended_complexity": "O(n)",
            "maximum_scale": "n <= 100",
            "time_limit_rationale": "measured intended implementation",
            "memory_limit_rationale": "measured linear storage",
        }
        policy_core = {
            "schema_version": 1,
            "statement_resources": statement_resources.as_dict(),
            "design_basis": design_basis,
        }
        policy = {
            **policy_core,
            "policy_sha256": gate.sha256_bytes(gate.canonical_json_bytes(policy_core)),
        }
        plan = {
            "schema_version": gate.PLAN_SCHEMA_VERSION,
            "resource_policy": policy,
            "sample_manifest": "package/samples/manifest.json",
            "oracle": {
                "source": "package/brute.cpp",
                "independent_from_std": True,
                "independence_basis": "independent enumeration",
                "applicability": "all tiny cases",
            },
            "required_limit_tags": ["n=max"],
            "differential": {
                "mode": "tiny-exhaustive",
                "generator": {
                    "source": "package/generators/tiny.cpp",
                    "args": ["{case_index}"],
                },
                "case_index_start": 0,
                "count": 1,
            },
            "release_tests": release_items,
            "accepted_alternatives": [
                {
                    "alternative_id": "A01",
                    "source": "audit/private/accepted-solutions/A01.cpp",
                    "independence_basis": "independent control flow and state representation",
                }
            ],
            "wrong_routes": [
                {
                    "route_id": "W01",
                    "source": "audit/private/wrong-solutions/W01.cpp",
                    "sample_inputs": ["package/samples/sample-01.in"],
                    "ordinary_input": "package/tests/ordinary/ordinary-1.in",
                    "survivability_inputs": [
                        {
                            "kind": "small",
                            "input": "package/tests/ordinary/ordinary-1.in",
                        },
                        {
                            "kind": "random",
                            "input": "package/tests/survivability/random-1.in",
                        },
                        {
                            "kind": "structured",
                            "input": "package/tests/survivability/structured-1.in",
                        },
                    ],
                    "breaker_input": "package/tests/breakers/W01-breaker.in",
                    "expected_verdict": "WA",
                }
            ],
        }
        plan_path = root / "audit/regression-plan.json"
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        return plan, plan_path

    def test_schema_three_parses_alternatives_and_survivability(self):
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root).resolve()
            plan, plan_path = self._plan_fixture(root)
            loaded = gate.load_plan(
                root, "audit/regression-plan.json", plan_path, random_minimum=1
            )
            self.assertEqual(loaded[0], plan)
            self.assertEqual(
                [item.kind for item in loaded[3][0].survivability_inputs],
                ["small", "random", "structured"],
            )
            self.assertEqual(
                [item.alternative_id for item in loaded[4].programs], ["A01"]
            )

    def test_token_hash_is_length_delimited(self):
        self.assertNotEqual(gate.token_sha256(b"a\x00b"), gate.token_sha256(b"a b"))
        self.assertEqual(gate.token_sha256(b" a\n b "), gate.token_sha256(b"a b"))

    def test_schema_two_and_missing_survivability_fail_closed(self):
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root).resolve()
            plan, plan_path = self._plan_fixture(root)
            legacy = copy.deepcopy(plan)
            legacy["schema_version"] = 2
            plan_path.write_text(json.dumps(legacy), encoding="utf-8")
            with self.assertRaisesRegex(gate.GateError, "schema_version"):
                gate.load_plan(
                    root, "audit/regression-plan.json", plan_path, random_minimum=1
                )
            missing = copy.deepcopy(plan)
            missing["wrong_routes"][0].pop("survivability_inputs")
            plan_path.write_text(json.dumps(missing), encoding="utf-8")
            with self.assertRaisesRegex(gate.GateError, "survivability_inputs"):
                gate.load_plan(
                    root, "audit/regression-plan.json", plan_path, random_minimum=1
                )

    def test_accepted_alternative_rejects_normalized_token_std_clone(self):
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root).resolve()
            _plan, plan_path = self._plan_fixture(root)
            (root / "audit/private/accepted-solutions/A01.cpp").write_text(
                "// renamed candidate\nint   main ( ) { /* no change */ return 0 ; }\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                gate.GateError, "normalized preprocessing-token clone"
            ):
                gate.load_plan(
                    root, "audit/regression-plan.json", plan_path, random_minimum=1
                )

    def test_accepted_alternative_requires_independence_basis(self):
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root).resolve()
            plan, plan_path = self._plan_fixture(root)
            plan["accepted_alternatives"][0].pop("independence_basis")
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            with self.assertRaisesRegex(gate.GateError, "independence_basis"):
                gate.load_plan(
                    root, "audit/regression-plan.json", plan_path, random_minimum=1
                )

    def test_cpp_clone_normalization_preserves_token_boundaries(self):
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            spaced = root / "spaced.cpp"
            compact = root / "compact.cpp"
            different = root / "different.cpp"
            spaced.write_text(
                "int main ( ) { /* same tokens */ return 0 ; }\n",
                encoding="utf-8",
            )
            compact.write_text("int main(){return 0;}\n", encoding="utf-8")
            different.write_text(
                "int f(int a,int b){return a++ + b;}\n", encoding="utf-8"
            )
            boundary_variant = root / "boundary.cpp"
            boundary_variant.write_text(
                "int f(int a,int b){return a + ++b;}\n", encoding="utf-8"
            )
            separated = root / "separated.cpp"
            separated.write_text(
                "int main(){return 1'000 + 0xFF'00;}\n", encoding="utf-8"
            )
            angle_spaced = root / "angle-spaced.cpp"
            angle_spaced.write_text(
                "using T = std::vector< ::Type>;\n", encoding="utf-8"
            )
            angle_compact = root / "angle-compact.cpp"
            angle_compact.write_text(
                "using T=std::vector<::Type>;\n", encoding="utf-8"
            )
            macro_shift = root / "macro-shift.cpp"
            macro_shift.write_text(
                '#define S(x) #x\nconst char* s=S(>>);\n', encoding="utf-8"
            )
            macro_split = root / "macro-split.cpp"
            macro_split.write_text(
                '#define S(x) #x\nconst char* s=S(> >);\n', encoding="utf-8"
            )
            self.assertEqual(
                gate.cpp_normalized_source_sha256(spaced),
                gate.cpp_normalized_source_sha256(compact),
            )
            self.assertNotEqual(
                gate.cpp_normalized_source_sha256(different),
                gate.cpp_normalized_source_sha256(boundary_variant),
            )
            self.assertRegex(
                gate.cpp_normalized_source_sha256(separated), r"^[0-9a-f]{64}$"
            )
            self.assertEqual(
                gate.cpp_normalized_source_sha256(angle_spaced),
                gate.cpp_normalized_source_sha256(angle_compact),
            )
            self.assertNotEqual(
                gate.cpp_normalized_source_sha256(macro_shift),
                gate.cpp_normalized_source_sha256(macro_split),
            )

    def test_custom_checker_requires_alternative_or_concrete_waiver(self):
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root).resolve()
            plan, plan_path = self._plan_fixture(root)
            (root / "package/checker.cpp").write_text(
                "int main(){return 0;}\n", encoding="utf-8"
            )
            plan["accepted_alternatives"] = []
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            with self.assertRaisesRegex(gate.GateError, "waiver"):
                gate.load_plan(
                    root, "audit/regression-plan.json", plan_path, random_minimum=1
                )
            plan["accepted_alternative_waiver"] = {
                "status": "no-known-alternative",
                "basis": "all reviewed solutions print the same canonical witness",
                "search_scope": "two independent constructions and relabelings",
            }
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            loaded = gate.load_plan(
                root, "audit/regression-plan.json", plan_path, random_minimum=1
            )
            self.assertEqual(loaded[4].waiver["status"], "no-known-alternative")

    def test_accepted_alternative_runs_on_every_release_test(self):
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            source = root / "audit/private/accepted-solutions/A01.cpp"
            source.parent.mkdir(parents=True)
            source.write_text("int main(){}\n", encoding="utf-8")
            tests = root / "package/tests"
            tests.mkdir(parents=True)
            releases = []
            for index in range(2):
                input_path = tests / f"t{index}.in"
                answer_path = tests / f"t{index}.ans"
                input_path.write_text(f"{index}\n", encoding="utf-8")
                answer_path.write_text(f"{index}\n", encoding="utf-8")
                releases.append(
                    gate.ReleaseTest(
                        f"t{index}",
                        input_path.relative_to(root).as_posix(),
                        input_path,
                        answer_path.relative_to(root).as_posix(),
                        answer_path,
                        (),
                    )
                )
            alternative = gate.AcceptedAlternative(
                "A01",
                gate.SourceSpec(
                    "accepted:A01",
                    source.relative_to(root).as_posix(),
                    source,
                ),
                gate.cpp_normalized_source_sha256(source),
                "independent implementation for the fixture",
            )
            prepared = programs()
            prepared["accepted:A01"] = backend_module.PreparedProgram(
                role="accepted:A01",
                source_rel=alternative.source.rel,
                source_path=source,
                source_sha256=gate.sha256_file(source),
            )
            backend = RecordingBackend()
            records, errors = gate.run_accepted_alternatives(
                gate.AcceptedAlternativeAudit((alternative,), None),
                releases,
                prepared,
                checker=None,
                checker_contract=gate.load_checker_contract({}),
                backend=backend,
                problem_dir=root,
                timeout=2,
            )
            self.assertEqual(errors, [])
            self.assertEqual(records[0]["status"], "passed")
            self.assertEqual(
                [item["status"] for item in records[0]["release_results"]],
                ["passed", "passed"],
            )
            self.assertEqual(backend.calls, [("accepted:A01", 2)])

    def test_custom_checker_alternative_requires_non_jury_output(self):
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            source = root / "audit/private/accepted-solutions/A01.cpp"
            source.parent.mkdir(parents=True)
            source.write_text("int main(){}\n", encoding="utf-8")
            tests = root / "package/tests"
            tests.mkdir(parents=True)
            input_path = tests / "t0.in"
            answer_path = tests / "t0.ans"
            input_path.write_text("same\n", encoding="utf-8")
            answer_path.write_text("same\n", encoding="utf-8")
            release = gate.ReleaseTest(
                "t0",
                input_path.relative_to(root).as_posix(),
                input_path,
                answer_path.relative_to(root).as_posix(),
                answer_path,
                (),
            )
            alternative = gate.AcceptedAlternative(
                "A01",
                gate.SourceSpec(
                    "accepted:A01", source.relative_to(root).as_posix(), source
                ),
                gate.cpp_normalized_source_sha256(source),
                "independent implementation for the fixture",
            )
            prepared = programs()
            prepared["accepted:A01"] = backend_module.PreparedProgram(
                role="accepted:A01",
                source_rel=alternative.source.rel,
                source_path=source,
                source_sha256=gate.sha256_file(source),
            )
            checker = backend_module.PreparedProgram(
                role="checker",
                source_rel="package/checker.cpp",
                source_path=root / "package/checker.cpp",
                source_sha256="checker",
            )
            prepared["checker"] = checker
            records, errors = gate.run_accepted_alternatives(
                gate.AcceptedAlternativeAudit((alternative,), None),
                [release],
                prepared,
                checker=checker,
                checker_contract=gate.load_checker_contract({}),
                backend=RecordingBackend(),
                problem_dir=root,
                timeout=2,
            )
            self.assertEqual(records[0]["status"], "passed")
            self.assertEqual(records[0]["non_jury_accepted_test_ids"], [])
            self.assertTrue(any("token sequence distinct" in error for error in errors))

    def test_custom_checker_accepts_distinct_non_jury_witness(self):
        class DistinctAlternativeBackend(RecordingBackend):
            def run_dataset(self, program, dataset, *, problem_dir, timeout):
                if program.role == "accepted:A01":
                    self.calls.append((program.role, len(dataset)))
                    return [success(b"different\n") for _ in dataset]
                return super().run_dataset(
                    program, dataset, problem_dir=problem_dir, timeout=timeout
                )

        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            source = root / "audit/private/accepted-solutions/A01.cpp"
            source.parent.mkdir(parents=True)
            source.write_text("int main(){}\n", encoding="utf-8")
            tests = root / "package/tests"
            tests.mkdir(parents=True)
            input_path = tests / "t0.in"
            answer_path = tests / "t0.ans"
            input_path.write_text("input\n", encoding="utf-8")
            answer_path.write_text("jury\n", encoding="utf-8")
            release = gate.ReleaseTest(
                "t0",
                input_path.relative_to(root).as_posix(),
                input_path,
                answer_path.relative_to(root).as_posix(),
                answer_path,
                (),
            )
            alternative = gate.AcceptedAlternative(
                "A01",
                gate.SourceSpec(
                    "accepted:A01", source.relative_to(root).as_posix(), source
                ),
                gate.cpp_normalized_source_sha256(source),
                "independent implementation for the fixture",
            )
            prepared = programs()
            prepared["accepted:A01"] = backend_module.PreparedProgram(
                role="accepted:A01",
                source_rel=alternative.source.rel,
                source_path=source,
                source_sha256=gate.sha256_file(source),
            )
            checker = backend_module.PreparedProgram(
                role="checker",
                source_rel="package/checker.cpp",
                source_path=root / "package/checker.cpp",
                source_sha256="checker",
            )
            prepared["checker"] = checker
            records, errors = gate.run_accepted_alternatives(
                gate.AcceptedAlternativeAudit((alternative,), None),
                [release],
                prepared,
                checker=checker,
                checker_contract=gate.load_checker_contract({}),
                backend=DistinctAlternativeBackend(),
                problem_dir=root,
                timeout=2,
            )
            self.assertEqual(errors, [])
            self.assertEqual(records[0]["non_jury_accepted_test_ids"], ["t0"])

    def test_wrong_route_survivability_inputs_are_executed_and_receipted(self):
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            source = root / "audit/private/wrong-solutions/W01.cpp"
            source.parent.mkdir(parents=True)
            source.write_text("int main(){}\n", encoding="utf-8")
            tests = root / "package/tests"
            tests.mkdir(parents=True)
            paths = {}
            for name in ("small", "random", "structured", "ordinary", "breaker"):
                path = tests / f"{name}.in"
                path.write_text(f"{name}\n", encoding="utf-8")
                paths[name] = path
            route = gate.WrongRoute(
                route_id="W01",
                source=gate.SourceSpec(
                    "wrong:W01", source.relative_to(root).as_posix(), source
                ),
                ordinary_input=(
                    paths["ordinary"].relative_to(root).as_posix(),
                    paths["ordinary"],
                ),
                breaker_input=(
                    paths["breaker"].relative_to(root).as_posix(),
                    paths["breaker"],
                ),
                survivability_inputs=tuple(
                    gate.SurvivabilityInput(
                        kind,
                        paths[kind].relative_to(root).as_posix(),
                        paths[kind],
                    )
                    for kind in ("small", "random", "structured")
                ),
                expected_verdict="AC",
            )
            prepared = programs()
            prepared["wrong:W01"] = backend_module.PreparedProgram(
                role="wrong:W01",
                source_rel=route.source.rel,
                source_path=source,
                source_sha256=gate.sha256_file(source),
            )
            backend = RecordingBackend()
            records, errors = gate.run_wrong_routes(
                [route],
                [],
                prepared,
                checker=None,
                checker_contract=gate.load_checker_contract({}),
                backend=backend,
                problem_dir=root,
                timeout=2,
            )
            self.assertEqual(errors, [])
            self.assertEqual(records[0]["status"], "passed")
            self.assertEqual(
                [item["kind"] for item in records[0]["survivability_results"]],
                ["small", "random", "structured"],
            )
            self.assertTrue(
                all(
                    item["status"] == "passed"
                    for item in records[0]["survivability_results"]
                )
            )

    @staticmethod
    def _completion_strengthening_receipt(
        root: Path, plan: dict
    ) -> tuple[dict, set[str], list[dict]]:
        success_record = {
            "returncode": 0,
            "timed_out": False,
            "duration_seconds": 0.001,
            "memory_bytes": 1,
            "stderr_preview": "",
        }
        route = plan["wrong_routes"][0]
        survivability_results = []
        for item in route["survivability_inputs"]:
            path = root / item["input"]
            survivability_results.append(
                {
                    "kind": item["kind"],
                    "input": item["input"],
                    "input_sha256": completion.sha256_file(path),
                    "status": "passed",
                    "observed_verdict": "AC",
                    "validator": dict(success_record),
                    "std": dict(success_record),
                    "wrong": {"execution": dict(success_record), "judge": {}},
                }
            )
        alternative = plan["accepted_alternatives"][0]
        release_results = []
        for release in plan["release_tests"]:
            answer_token_sha256 = gate.token_sha256(
                (root / release["answer"]).read_bytes()
            )
            release_results.append(
                {
                    "test_id": release["test_id"],
                    "input": release["input"],
                    "input_sha256": completion.sha256_file(root / release["input"]),
                    "answer": release["answer"],
                    "answer_sha256": completion.sha256_file(root / release["answer"]),
                    "execution": dict(success_record),
                    "judge": {"verdict": "accepted"},
                    "candidate_token_sha256": answer_token_sha256,
                    "answer_token_sha256": answer_token_sha256,
                    "non_jury_output": False,
                    "status": "passed",
                }
            )
        source = root / alternative["source"]
        binding = {
            "alternative_id": alternative["alternative_id"],
            "source": alternative["source"],
            "source_sha256": completion.sha256_file(source),
            "normalized_source_sha256": gate.cpp_normalized_source_sha256(
                source
            ),
            "independence_basis": alternative["independence_basis"],
        }
        receipt = {
            "accepted_alternative_policy": {
                "strategy": "programs",
                "program_count": 1,
                "waiver": None,
            },
            "accepted_alternative_bindings": [binding],
            "accepted_alternatives": [
                {
                    **binding,
                    "compile_status": "passed",
                    "release_results": release_results,
                    "non_jury_accepted_test_ids": [],
                    "status": "passed",
                    "errors": [],
                }
            ],
            "accepted_alternative_output_diversity": {
                "required": False,
                "status": "not-required",
                "witnesses": [],
            },
            "wrong_routes": [
                {
                    "route_id": route["route_id"],
                    "survivability_results": survivability_results,
                }
            ],
            "facts": {
                "survivability_inputs_checked": len(survivability_results),
                "accepted_alternatives_checked": 1,
                "accepted_non_jury_outputs_checked": 0,
                "accepted_alternative_strategy": "programs",
            },
        }
        bound_paths = {
            item["input"] for item in plan["release_tests"]
        } | {
            item["answer"] for item in plan["release_tests"]
        } | {
            alternative["source"]
        }
        matrix = [{"route_id": route["route_id"]}]
        return receipt, bound_paths, matrix

    def test_completion_cross_checks_strengthening_and_alternatives(self):
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root).resolve()
            plan, _ = self._plan_fixture(root)
            receipt, bound_paths, matrix = self._completion_strengthening_receipt(
                root, plan
            )
            report = completion.Report(root, "audit/completion-gate.json", False)
            check = report.new_check("strengthened-regression-test")
            completion.validate_strengthened_regression_evidence(
                report,
                check,
                receipt,
                plan,
                bound_paths=bound_paths,
                wrong_records=receipt["wrong_routes"],
                wrong_matrix=matrix,
            )
            self.assertEqual(report.issues, [])

    def test_completion_rejects_forged_strengthening_or_alternative_result(self):
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root).resolve()
            plan, _ = self._plan_fixture(root)
            receipt, bound_paths, matrix = self._completion_strengthening_receipt(
                root, plan
            )
            receipt["wrong_routes"][0]["survivability_results"][0][
                "observed_verdict"
            ] = "WA"
            receipt["accepted_alternatives"][0]["release_results"][0]["judge"][
                "verdict"
            ] = "rejected"
            report = completion.Report(root, "audit/completion-gate.json", False)
            check = report.new_check("strengthened-regression-test")
            completion.validate_strengthened_regression_evidence(
                report,
                check,
                receipt,
                plan,
                bound_paths=bound_paths,
                wrong_records=receipt["wrong_routes"],
                wrong_matrix=matrix,
            )
            self.assertTrue(
                any("did not independently receive AC" in issue for issue in report.issues)
            )
            self.assertTrue(
                any("failed a release test" in issue for issue in report.issues)
            )

    def test_completion_rejects_missing_or_forged_custom_checker_diversity(self):
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root).resolve()
            plan, _ = self._plan_fixture(root)
            (root / "package/checker.cpp").write_text(
                "int main(){return 0;}\n", encoding="utf-8"
            )
            receipt, bound_paths, matrix = self._completion_strengthening_receipt(
                root, plan
            )
            bound_paths.add("package/checker.cpp")
            report = completion.Report(root, "audit/completion-gate.json", False)
            check = report.new_check("custom-checker-diversity-test")
            completion.validate_strengthened_regression_evidence(
                report,
                check,
                receipt,
                plan,
                bound_paths=bound_paths,
                wrong_records=receipt["wrong_routes"],
                wrong_matrix=matrix,
            )
            self.assertTrue(
                any("no accepted alternative output" in issue for issue in report.issues)
            )

            forged = copy.deepcopy(receipt)
            forged_result = forged["accepted_alternatives"][0]["release_results"][0]
            forged_result["non_jury_output"] = True
            forged["accepted_alternatives"][0]["non_jury_accepted_test_ids"] = [
                forged_result["test_id"]
            ]
            forged["accepted_alternative_output_diversity"] = {
                "required": True,
                "status": "passed",
                "witnesses": [
                    {
                        "alternative_id": "A01",
                        "test_id": forged_result["test_id"],
                        "candidate_token_sha256": forged_result[
                            "candidate_token_sha256"
                        ],
                        "answer_token_sha256": forged_result["answer_token_sha256"],
                    }
                ],
            }
            forged["facts"]["accepted_non_jury_outputs_checked"] = 1
            forged_report = completion.Report(
                root, "audit/completion-gate.json", False
            )
            forged_check = forged_report.new_check(
                "forged-custom-checker-diversity-test"
            )
            completion.validate_strengthened_regression_evidence(
                forged_report,
                forged_check,
                forged,
                plan,
                bound_paths=bound_paths,
                wrong_records=forged["wrong_routes"],
                wrong_matrix=matrix,
            )
            self.assertTrue(
                any("output-diversity flag is stale" in issue for issue in forged_report.issues)
            )


class AdversarialRoundBackendTests(unittest.TestCase):
    def _fixture(
        self,
        root: Path,
        *,
        checker: bool = False,
        checker_source: bool = False,
    ):
        (root / "statement.md").write_text(
            "Time Limit: 2 s\nMemory Limit: 1024 MiB\n",
            encoding="utf-8",
        )
        source = root / "audit/private/wrong-solutions/W01.cpp"
        source.parent.mkdir(parents=True)
        source.write_text(
            "#include <iostream>\nint main(){std::cout << 1 << '\\n';}\n",
            encoding="utf-8",
        )
        tests = root / "package/tests"
        tests.mkdir(parents=True)
        (tests / "breaker.in").write_text("0\n", encoding="utf-8")
        (tests / "breaker.ans").write_text("2\n", encoding="utf-8")
        plan_rel = Path("audit/adversarial-round-plans/round-01.json")
        plan_path = root / plan_rel
        plan_path.parent.mkdir(parents=True)
        test = {
            "test_id": "breaker",
            "input_path": "package/tests/breaker.in",
            "answer_path": "package/tests/breaker.ans",
            "comparison": "checker" if checker else "tokens",
        }
        if checker and not checker_source:
            test["checker_command"] = ["/usr/bin/true"]
            test["checker_wa_exit_codes"] = [1, 2]
        raw = {
            "schema_version": 1,
            "round": 1,
            "trigger": "initial-matrix",
            "delta": "initial wrong-route breaker",
            "previous_receipt": None,
            "timeout_seconds": 2,
            "compile_timeout_seconds": 30,
            "tests": [test],
            "routes": [
                {
                    "route_id": "W01",
                    "source_path": "audit/private/wrong-solutions/W01.cpp",
                    "breaker_test_id": "breaker",
                }
            ],
        }
        if checker_source:
            checker_path = root / "package/checker.cpp"
            checker_path.parent.mkdir(parents=True, exist_ok=True)
            checker_path.write_text(
                """
#include <fstream>
#include <string>
int main(int argc, char **argv) {
    if (argc != 4) return 3;
    std::ifstream actual(argv[2]), answer(argv[3]);
    std::string x, y;
    actual >> x; answer >> y;
    return x == y ? 0 : 1;
}
""".strip()
                + "\n",
                encoding="utf-8",
            )
            raw["checker_source"] = "package/checker.cpp"
        plan_path.write_text(json.dumps(raw), encoding="utf-8")
        parsed = round_recorder.parse_plan(
            raw,
            problem_root=root,
            plan_rel=round_recorder.PurePosixPath(plan_rel.as_posix()),
        )
        return plan_path, parsed

    def test_wrong_route_uses_explicit_local_backend_only_in_test_mode(self):
        if not any(shutil.which(name) for name in ("c++", "g++", "clang++")):
            self.skipTest("C++ compiler unavailable")
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root).resolve()
            plan_path, parsed = self._fixture(root)
            receipt = round_recorder.execute_round(
                problem_root=root,
                plan_rel=round_recorder.PurePosixPath(
                    "audit/adversarial-round-plans/round-01.json"
                ),
                plan_path=plan_path,
                parsed=parsed,
                execution_backend="local",
                test_mode=True,
                lightcpverifier_url="http://127.0.0.1:1",
            )
            self.assertFalse(receipt["production"])
            self.assertEqual(receipt["execution_backend"]["name"], "local")
            self.assertEqual(receipt["routes"][0]["verdict"], "WA")
            self.assertEqual(receipt["routes"][0]["compile"]["exit_code"], 0)

    def test_production_checker_command_fails_closed_before_execution(self):
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root).resolve()
            plan_path, parsed = self._fixture(root, checker=True)
            with self.assertRaisesRegex(
                round_recorder.ContractError, "hash-bound checker_source"
            ):
                round_recorder.execute_round(
                    problem_root=root,
                    plan_rel=round_recorder.PurePosixPath(
                        "audit/adversarial-round-plans/round-01.json"
                    ),
                    plan_path=plan_path,
                    parsed=parsed,
                    execution_backend="lightcpverifier",
                    test_mode=False,
                    lightcpverifier_url="http://127.0.0.1:1",
                )

    def test_source_bound_checker_is_compiled_and_executed_by_backend(self):
        if not any(shutil.which(name) for name in ("c++", "g++", "clang++")):
            self.skipTest("C++ compiler unavailable")
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root).resolve()
            plan_path, parsed = self._fixture(
                root, checker=True, checker_source=True
            )
            receipt = round_recorder.execute_round(
                problem_root=root,
                plan_rel=round_recorder.PurePosixPath(
                    "audit/adversarial-round-plans/round-01.json"
                ),
                plan_path=plan_path,
                parsed=parsed,
                execution_backend="local",
                test_mode=True,
                lightcpverifier_url="http://127.0.0.1:1",
            )
            self.assertEqual(receipt["routes"][0]["verdict"], "WA")
            self.assertEqual(
                receipt["checker_source"]["path"], "package/checker.cpp"
            )
            self.assertEqual(receipt["checker_compile"]["exit_code"], 0)
            self.assertEqual(
                receipt["routes"][0]["checker"]["execution_backend"], "local"
            )
            self.assertEqual(
                receipt["routes"][0]["comparison_evidence"][
                    "checker_source_sha256"
                ],
                receipt["checker_source"]["sha256"],
            )


class CompletionResourcePolicyTests(unittest.TestCase):
    @staticmethod
    def _fixture(root: Path):
        (root / "statement.md").write_text(
            "# Example\n\nTime Limit: 1.5 s\nMemory Limit: 512 MiB\n",
            encoding="utf-8",
        )
        statement_resources = completion.load_statement_resources(root)
        design_basis = {
            "intended_complexity": "O(n log n)",
            "maximum_scale": "n <= 200000",
            "time_limit_rationale": "Allows the intended solution with margin.",
            "memory_limit_rationale": "Covers the intended linear-size buffers.",
        }
        policy_core = {
            "schema_version": completion.RESOURCE_POLICY_SCHEMA_VERSION,
            "statement_resources": statement_resources.as_dict(),
            "design_basis": design_basis,
        }
        policy = {
            **policy_core,
            "policy_sha256": completion.canonical_json_sha256(policy_core),
        }
        plan = {
            "schema_version": completion.REGRESSION_PLAN_SCHEMA_VERSION,
            "resource_policy": copy.deepcopy(policy),
        }
        plan_path = root / "audit/regression-plan.json"
        plan_path.parent.mkdir(parents=True)
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        receipt = {
            "resource_policy": copy.deepcopy(policy),
            "configuration": {
                "resource_policy_sha256": policy["policy_sha256"]
            },
            "plan": {"canonical_sha256": completion.canonical_json_sha256(plan)},
        }
        report = completion.Report(root, "audit/completion-gate.json", False)
        check = completion.Check("resource-policy")
        return report, check, receipt, plan_path

    def test_accepts_policy_bound_to_current_statement_plan_and_receipt(self):
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root).resolve()
            report, check, receipt, plan_path = self._fixture(root)

            policy = completion.validate_machine_resource_policy(
                report, check, receipt, plan_path
            )

            self.assertEqual(check.issues, [])
            self.assertIsNotNone(policy)
            self.assertEqual(policy.time_limit_ms, 1500)
            self.assertEqual(policy.memory_limit_mib, 512)
            self.assertEqual(report.facts["time_limit_ms"], 1500)
            self.assertEqual(report.facts["memory_limit_mib"], 512)
            self.assertIn(Path("statement.md"), report.tracked)

    def test_rejects_receipt_limits_that_differ_from_current_statement(self):
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root).resolve()
            report, check, receipt, plan_path = self._fixture(root)
            receipt["resource_policy"]["statement_resources"][
                "time_limit_ms"
            ] = 2000

            completion.validate_machine_resource_policy(
                report, check, receipt, plan_path
            )

            issues = "\n".join(check.issues)
            self.assertRegex(issues, "current statement limits")
            self.assertRegex(issues, "self-digest")
            self.assertRegex(issues, "canonical plan")

    def test_rejects_stale_plan_and_configuration_bindings(self):
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root).resolve()
            report, check, receipt, plan_path = self._fixture(root)
            receipt["plan"]["canonical_sha256"] = "0" * 64
            receipt["configuration"]["resource_policy_sha256"] = "1" * 64

            completion.validate_machine_resource_policy(
                report, check, receipt, plan_path
            )

            issues = "\n".join(check.issues)
            self.assertRegex(issues, "canonical plan digest")
            self.assertRegex(issues, "configuration is not bound")

    def test_rejects_legacy_regression_plan_schema(self):
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root).resolve()
            report, check, receipt, plan_path = self._fixture(root)
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            plan["schema_version"] = 1
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            receipt["plan"]["canonical_sha256"] = (
                completion.canonical_json_sha256(plan)
            )

            completion.validate_machine_resource_policy(
                report, check, receipt, plan_path
            )

            self.assertRegex(
                "\n".join(check.issues),
                f"schema_version {completion.REGRESSION_PLAN_SCHEMA_VERSION}",
            )


class CompletionBackendEvidenceTests(unittest.TestCase):
    TIME_LIMIT_MS = 1500
    MEMORY_LIMIT_MIB = 512

    def _receipt(self):
        service_digest = "sha256:" + "b" * 64
        policy = {
            "runtime": {
                "minimumCpuTimeMs": 100,
                "maximumCpuTimeMs": 30000,
                "wallTimeMultiplier": 2,
                "minimumMemoryMb": 16,
                "maximumMemoryMb": 2048,
                "minimumOutputBytes": 1024,
                "maximumOutputBytes": 16 * 1024 * 1024,
                "processLimit": 128,
                "addressSpaceLimit": True,
            },
            "compilation": {
                "cpp": {"cpuTimeMs": 10000, "memoryMb": 512, "processLimit": 50}
            },
            "batch": {
                "maxTests": 128,
                "maxCapturedOutputBytes": 32 * 1024 * 1024,
            },
        }
        service_identity = {
            "apiRevision": backend_module.LIGHTCP_API_REVISION,
            "compilerProfile": backend_module.LIGHTCP_CPP_PROFILE,
            "buildId": service_digest,
            "imageId": service_digest,
            "goJudgeVersion": "1.12.1",
            "nodeVersion": "v20.19.4",
            "executionPolicy": policy,
        }
        module_hashes = backend_module.cpideas_module_bindings()
        adapter_sha256 = completion.sha256_file(Path(backend_module.__file__).resolve())
        backend_configuration = {
            "name": "lightcpverifier",
            "sandboxed": True,
            "testing_only": False,
            "requested_program_timeout_seconds": 1.5,
            "effective_program_timeout_seconds": 1.5,
            "verdict_time_limit_seconds": 1.5,
            "sandbox_effective_time_limit_seconds": 1.5,
            "requested_memory_limit_mb": self.MEMORY_LIMIT_MIB,
            "effective_memory_limit_mb": self.MEMORY_LIMIT_MIB,
            "dataset_batch_size": 128,
            "max_request_bytes": 60 * 1024 * 1024,
            "max_output_bytes_per_stream": 16 * 1024 * 1024,
            "compile_context_policy_revision": (
                backend_module.COMPILE_CONTEXT_POLICY_REVISION
            ),
            "cpp_compiler_profile": backend_module.LIGHTCP_CPP_PROFILE,
            "dataset_api_revision": backend_module.LIGHTCP_DATASET_API_REVISION,
            "client_module_sha256": module_hashes,
            "service_identity": service_identity,
            "execution_evidence_schema_version": (
                backend_module.BACKEND_EVIDENCE_SCHEMA_VERSION
            ),
            "adapter_sha256": adapter_sha256,
        }
        compilation = []
        invocations = []
        for index, role in enumerate(("generator", "validator", "std", "brute")):
            source = f"package/{role}.cpp"
            digest = f"{index + 1:x}" * 64
            compilation.append(
                {
                    "role": role,
                    "source": source,
                    "source_sha256": digest,
                    "status": "passed",
                    "compilation_evidence": {
                        "compile_context_policy_revision": (
                            backend_module.COMPILE_CONTEXT_POLICY_REVISION
                        ),
                        "compile_copy_in_files_sha256": "a" * 64
                    },
                }
            )
            evaluation = {
                "schema_version": 1,
                "kind": "cpideas.program_dataset_evaluation",
                "status": "completed",
                "ok": True,
                "evaluation_complete": True,
                "error": None,
                "comparison": "none",
                "program": {
                    "language": "cpp",
                    "source_name": source,
                    "source_sha256": digest,
                    "compile_files_sha256": "a" * 64,
                    "runtime_spec_sha256": "b" * 64,
                },
                "validator": None,
                "configuration": {
                    "requested_time_limit_ms": self.TIME_LIMIT_MS,
                    "effective_time_limit_ms": self.TIME_LIMIT_MS,
                    "requested_memory_limit_mb": self.MEMORY_LIMIT_MIB,
                    "effective_memory_limit_mb": self.MEMORY_LIMIT_MIB,
                    "requested_max_output_bytes": 16 * 1024 * 1024,
                    "effective_max_output_bytes": 16 * 1024 * 1024,
                    "max_batch_output_bytes": 32 * 1024 * 1024,
                    "validator_limits": None,
                    "chunk_count": 1,
                    "batch_size": 128,
                    "max_request_bytes": 60 * 1024 * 1024,
                },
                "compilation": {
                    "status": "COMPILED",
                    "ok": True,
                    "runtime_profile_for_subsequent_execution": {
                        "requested_time_limit_ms": self.TIME_LIMIT_MS,
                        "effective_time_limit_ms": self.TIME_LIMIT_MS,
                        "effective_wall_time_limit_ms": self.TIME_LIMIT_MS * 2,
                        "requested_memory_limit_mb": self.MEMORY_LIMIT_MIB,
                        "effective_memory_limit_mb": self.MEMORY_LIMIT_MIB,
                        "requested_max_output_bytes": 16 * 1024 * 1024,
                        "effective_max_output_bytes": 16 * 1024 * 1024,
                    },
                    "compiler_limits": {
                        "cpu_time_ms": 10000,
                        "memory_mb": 512,
                        "process_limit": 50,
                    },
                },
                "summary": {"total": 1, "verdict_counts": {"EXECUTED": 1}},
                "chunks": [
                    {
                        "index": 0,
                        "start": 0,
                        "stop": 1,
                        "request_bytes_estimate": 128,
                        "status": "completed",
                        "ok": True,
                        "total": 1,
                        "valid": 1,
                        "invalid": 0,
                        "validator_errors": 0,
                        "output_truncated": False,
                        "captured_output_bytes": 16,
                        "max_batch_output_bytes": 32 * 1024 * 1024,
                        "effective_time_limit_ms": self.TIME_LIMIT_MS,
                        "effective_wall_time_limit_ms": self.TIME_LIMIT_MS * 2,
                        "effective_memory_limit_mb": self.MEMORY_LIMIT_MIB,
                        "effective_max_output_bytes": 16 * 1024 * 1024,
                        "effective_validator_time_limit_ms": None,
                        "effective_validator_wall_time_limit_ms": None,
                        "effective_validator_memory_limit_mb": None,
                        "effective_validator_max_output_bytes": None,
                    }
                ],
                "case_results_binding": {"count": 1, "sha256": "c" * 64},
            }
            invocation = {
                "index": index,
                "role": role,
                "source": source,
                "source_sha256": digest,
                "requested_case_count": 1,
                "requested_case_ids_sha256": "d" * 64,
                "status": "completed",
                "evaluation_complete": True,
                "evaluation": evaluation,
                "program_results_sha256": "e" * 64,
            }
            invocation["evidence_sha256"] = backend_module.canonical_sha256(
                invocation
            )
            invocations.append(invocation)
        evidence = {
            "schema_version": backend_module.BACKEND_EVIDENCE_SCHEMA_VERSION,
            "kind": "icpc-light.program-dataset-execution-evidence",
            "backend": "lightcpverifier",
            "sandboxed": True,
            "testing_only": False,
            "adapter_sha256": adapter_sha256,
            "dataset_api_revision": backend_module.LIGHTCP_DATASET_API_REVISION,
            "client_module_sha256": module_hashes,
            "service_identity": service_identity,
            "invocation_count": len(invocations),
            "invocations_sha256": backend_module.canonical_sha256(invocations),
            "invocations": invocations,
        }
        return {
            "configuration": {"execution_backend": backend_configuration},
            "compilation": compilation,
            "execution_backend_evidence": evidence,
        }, backend_configuration

    @staticmethod
    def _rehash(receipt):
        invocations = receipt["execution_backend_evidence"]["invocations"]
        for invocation in invocations:
            core = dict(invocation)
            core.pop("evidence_sha256", None)
            invocation["evidence_sha256"] = backend_module.canonical_sha256(core)
        receipt["execution_backend_evidence"]["invocations_sha256"] = (
            backend_module.canonical_sha256(invocations)
        )

    def _validate(self, check, receipt, configuration):
        completion.validate_execution_backend_evidence(
            check,
            receipt,
            configuration,
            canonical_time_limit_ms=self.TIME_LIMIT_MS,
            canonical_memory_limit_mib=self.MEMORY_LIMIT_MIB,
        )

    def _round_receipt(self):
        machine, configuration = self._receipt()
        invocation = copy.deepcopy(
            machine["execution_backend_evidence"]["invocations"][0]
        )
        source = "audit/private/wrong-solutions/W01.cpp"
        digest = "f" * 64
        empty_sha256 = backend_module.sha256_bytes(b"")
        empty_stream = {
            "size": 0,
            "sha256": empty_sha256,
            "preview_utf8": "",
            "preview_truncated": False,
        }
        execution = {
            "timed_out": False,
            "exit_code": 0,
            "spawn_error": None,
            "memory_bytes": 0,
            "stdout": copy.deepcopy(empty_stream),
            "stderr": copy.deepcopy(empty_stream),
            "duration_seconds": 0.001,
            "sandbox_verdict": "EXECUTED",
            "sandbox_status": "accepted",
        }
        route = {
            "route_id": "W01",
            "source": {"path": source, "sha256": digest},
            "execution": execution,
            "verdict": "WA",
        }
        compact_result = {
            "returncode": 0,
            "timed_out": False,
            "duration_seconds": 0.001,
            "memory_bytes": 0,
            "stderr_preview": "",
            "sandbox_verdict": "EXECUTED",
            "sandbox_status": "accepted",
            "stdout_sha256": empty_sha256,
            "stdout_bytes": 0,
        }
        invocation.update(
            {
                "index": 0,
                "role": "wrong:W01",
                "source": source,
                "source_sha256": digest,
                "requested_case_ids_sha256": backend_module.canonical_sha256(
                    ["W01"]
                ),
                "program_results_sha256": backend_module.canonical_sha256(
                    [compact_result]
                ),
            }
        )
        invocation["evaluation"]["program"].update(
            {"source_name": source, "source_sha256": digest}
        )
        evidence = copy.deepcopy(machine["execution_backend_evidence"])
        evidence["invocations"] = [invocation]
        evidence["invocation_count"] = 1
        receipt = {
            "execution_backend": configuration,
            "execution_backend_evidence": evidence,
        }
        self._rehash(receipt)
        return receipt, configuration, route

    def test_completion_accepts_complete_runtime_evidence(self):
        receipt, configuration = self._receipt()
        check = completion.Check("backend-evidence")
        self._validate(check, receipt, configuration)
        self.assertEqual(check.issues, [])
        self.assertRegex(check.evidence[0], "4 hash-bound")

    def test_completion_accepts_complete_expected_runtime_failure(self):
        receipt, configuration = self._receipt()
        evaluation = receipt["execution_backend_evidence"]["invocations"][0][
            "evaluation"
        ]
        evaluation["ok"] = False
        evaluation["summary"]["verdict_counts"] = {"RE": 1}
        evaluation["chunks"][0]["ok"] = False
        self._rehash(receipt)
        check = completion.Check("backend-evidence")
        self._validate(check, receipt, configuration)
        self.assertEqual(check.issues, [])

    def test_completion_rejects_truncation_and_budget_drift(self):
        receipt, configuration = self._receipt()
        damaged = copy.deepcopy(receipt)
        chunk = damaged["execution_backend_evidence"]["invocations"][0][
            "evaluation"
        ]["chunks"][0]
        chunk["output_truncated"] = True
        chunk["max_batch_output_bytes"] -= 1
        self._rehash(damaged)
        check = completion.Check("backend-evidence")
        self._validate(check, damaged, configuration)
        self.assertRegex("\n".join(check.issues), "truncated")
        self.assertRegex("\n".join(check.issues), "output-budget")

    def test_completion_rejects_forged_batch_status_and_validator_counters(self):
        receipt, configuration = self._receipt()
        damaged = copy.deepcopy(receipt)
        invocation = damaged["execution_backend_evidence"]["invocations"][0]
        invocation["evaluation"]["ok"] = False
        chunk = invocation["evaluation"]["chunks"][0]
        chunk["ok"] = False
        chunk["valid"] = 0
        chunk["invalid"] = 1
        self._rehash(damaged)
        check = completion.Check("backend-evidence")
        self._validate(check, damaged, configuration)
        issues = "\n".join(check.issues)
        self.assertRegex(issues, r"evaluation\.ok")
        self.assertRegex(issues, "incomplete or truncated")
        self.assertRegex(issues, r"\.valid must be 1")
        self.assertRegex(issues, r"\.invalid must be 0")

    def test_verifiers_reject_zero_verdict_count_bypass(self):
        receipt, configuration = self._receipt()
        evaluation = receipt["execution_backend_evidence"]["invocations"][0][
            "evaluation"
        ]
        evaluation["ok"] = False
        evaluation["summary"]["verdict_counts"] = {"EXECUTED": 1, "RE": 0}
        evaluation["chunks"][0]["ok"] = False
        self._rehash(receipt)
        completion_check = completion.Check("backend-evidence")
        self._validate(completion_check, receipt, configuration)
        self.assertRegex("\n".join(completion_check.issues), "verdict counts")

        round_receipt, round_configuration, route = self._round_receipt()
        round_evaluation = round_receipt["execution_backend_evidence"][
            "invocations"
        ][0]["evaluation"]
        round_evaluation["ok"] = False
        round_evaluation["summary"]["verdict_counts"] = {
            "EXECUTED": 1,
            "RE": 0,
        }
        round_evaluation["chunks"][0]["ok"] = False
        self._rehash(round_receipt)
        round_issues = []
        round_chain.validate_round_execution_evidence(
            round_receipt,
            round_configuration,
            [route],
            label="round 1 zero-count receipt",
            issues=round_issues,
        )
        self.assertRegex("\n".join(round_issues), "case-result summary")

    def test_adversarial_chain_accepts_matching_invocation_evidence(self):
        receipt, configuration, route = self._round_receipt()
        issues = []
        round_chain.validate_round_execution_evidence(
            receipt,
            configuration,
            [route],
            label="round 1 receipt",
            issues=issues,
        )
        self.assertEqual(issues, [])

        cutoff_receipt = copy.deepcopy(receipt)
        cutoff_route = copy.deepcopy(route)
        cutoff_route["execution"].update(
            {
                "timed_out": True,
                "sandbox_verdict": "TLE",
                "sandbox_status": "time limit exceeded",
            }
        )
        cutoff_route["verdict"] = "TLE"
        cutoff_evaluation = cutoff_receipt["execution_backend_evidence"][
            "invocations"
        ][0]["evaluation"]
        cutoff_evaluation["ok"] = False
        cutoff_evaluation["summary"]["verdict_counts"] = {"TLE": 1}
        cutoff_evaluation["chunks"][0]["ok"] = False
        cutoff_compact = {
            "returncode": 0,
            "timed_out": True,
            "duration_seconds": 0.001,
            "memory_bytes": 0,
            "stderr_preview": "",
            "sandbox_verdict": "TLE",
            "sandbox_status": "time limit exceeded",
            "stdout_sha256": backend_module.sha256_bytes(b""),
            "stdout_bytes": 0,
        }
        cutoff_receipt["execution_backend_evidence"]["invocations"][0][
            "program_results_sha256"
        ] = backend_module.canonical_sha256([cutoff_compact])
        self._rehash(cutoff_receipt)
        cutoff_issues = []
        round_chain.validate_round_execution_evidence(
            cutoff_receipt,
            configuration,
            [cutoff_route],
            label="round 1 stricter verdict-limit TLE receipt",
            issues=cutoff_issues,
        )
        self.assertEqual(cutoff_issues, [])

    def test_adversarial_chain_binds_source_checker_invocation(self):
        receipt, configuration, route = self._round_receipt()
        checker_source = "package/checker.cpp"
        checker_digest = "9" * 64
        checker_execution = copy.deepcopy(route["execution"])
        checker_execution.update(
            {
                "exit_code": 1,
                "sandbox_verdict": "RE",
                "sandbox_status": "nonzero exit",
                "execution_backend": "lightcpverifier",
            }
        )
        route["checker"] = checker_execution
        receipt["checker_source"] = {
            "path": checker_source,
            "sha256": checker_digest,
        }
        checker_invocation = copy.deepcopy(
            receipt["execution_backend_evidence"]["invocations"][0]
        )
        checker_compact = {
            "returncode": 1,
            "timed_out": False,
            "duration_seconds": checker_execution["duration_seconds"],
            "memory_bytes": checker_execution["memory_bytes"],
            "stderr_preview": "",
            "sandbox_verdict": "RE",
            "sandbox_status": "nonzero exit",
            "stdout_sha256": checker_execution["stdout"]["sha256"],
            "stdout_bytes": checker_execution["stdout"]["size"],
        }
        checker_invocation.update(
            {
                "index": 1,
                "role": "checker",
                "source": checker_source,
                "source_sha256": checker_digest,
                "requested_case_ids_sha256": backend_module.canonical_sha256(
                    ["checker:W01"]
                ),
                "program_results_sha256": backend_module.canonical_sha256(
                    [checker_compact]
                ),
            }
        )
        checker_evaluation = checker_invocation["evaluation"]
        checker_evaluation["ok"] = False
        checker_evaluation["program"].update(
            {"source_name": checker_source, "source_sha256": checker_digest}
        )
        checker_evaluation["summary"]["verdict_counts"] = {"RE": 1}
        checker_evaluation["chunks"][0]["ok"] = False
        receipt["execution_backend_evidence"]["invocations"].append(
            checker_invocation
        )
        receipt["execution_backend_evidence"]["invocation_count"] = 2
        self._rehash(receipt)
        issues = []
        round_chain.validate_round_execution_evidence(
            receipt,
            configuration,
            [route],
            label="round 1 source-checker receipt",
            issues=issues,
        )
        self.assertEqual(issues, [])

        damaged = copy.deepcopy(receipt)
        damaged["checker_source"]["sha256"] = "8" * 64
        damaged_issues = []
        round_chain.validate_round_execution_evidence(
            damaged,
            configuration,
            [route],
            label="round 1 forged-checker receipt",
            issues=damaged_issues,
        )
        self.assertRegex("\n".join(damaged_issues), "program/request binding")

        failed_receipt = copy.deepcopy(receipt)
        failed_evaluation = failed_receipt["execution_backend_evidence"][
            "invocations"
        ][0]["evaluation"]
        failed_evaluation["ok"] = False
        failed_evaluation["summary"]["verdict_counts"] = {"RE": 1}
        failed_evaluation["chunks"][0]["ok"] = False
        failed_route = copy.deepcopy(route)
        failed_route["execution"].update(
            {
                "exit_code": -1,
                "sandbox_verdict": "RE",
                "sandbox_status": "nonzero exit status",
            }
        )
        failed_route["verdict"] = "RE"
        failed_compact = {
            "returncode": -1,
            "timed_out": False,
            "duration_seconds": 0.001,
            "memory_bytes": 0,
            "stderr_preview": "",
            "sandbox_verdict": "RE",
            "sandbox_status": "nonzero exit status",
            "stdout_sha256": backend_module.sha256_bytes(b""),
            "stdout_bytes": 0,
        }
        failed_receipt["execution_backend_evidence"]["invocations"][0][
            "program_results_sha256"
        ] = backend_module.canonical_sha256([failed_compact])
        self._rehash(failed_receipt)
        failed_issues = []
        round_chain.validate_round_execution_evidence(
            failed_receipt,
            configuration,
            [failed_route],
            label="round 1 expected RE receipt",
            issues=failed_issues,
        )
        self.assertEqual(failed_issues, [])

    def test_checker_exit_zero_cannot_hide_sandbox_runtime_failure(self):
        with tempfile.TemporaryDirectory() as raw_root:
            answer = Path(raw_root) / "answer.txt"
            answer.write_text("ok\n", encoding="utf-8")
            route = {
                "execution": {
                    "timed_out": False,
                    "spawn_error": None,
                    "exit_code": 0,
                    "sandbox_verdict": "EXECUTED",
                },
                "checker": {
                    "timed_out": False,
                    "spawn_error": None,
                    "exit_code": 0,
                    "sandbox_verdict": "RE",
                    "execution_backend": "lightcpverifier",
                },
                "comparison_evidence": {
                    "mode": "checker",
                    "checker_exit_code": 0,
                },
            }
            test = {
                "comparison": "checker",
                "checker_wa_exit_codes": [1, 2],
            }
            self.assertIsNone(round_chain.derived_verdict(route, test, answer))
            route["checker"]["sandbox_verdict"] = "EXECUTED"
            self.assertEqual(
                round_chain.derived_verdict(route, test, answer), "AC"
            )

    def test_adversarial_chain_rejects_forged_route_evidence_bindings(self):
        receipt, configuration, route = self._round_receipt()
        invocation = receipt["execution_backend_evidence"]["invocations"][0]
        invocation["requested_case_ids_sha256"] = "0" * 64
        invocation["program_results_sha256"] = "1" * 64
        invocation["evaluation"]["program"]["source_name"] = "package/std.cpp"
        invocation["evaluation"]["ok"] = False
        invocation["evaluation"]["summary"]["verdict_counts"] = {"TLE": 1}
        invocation["evaluation"]["chunks"][0]["ok"] = False
        self._rehash(receipt)
        issues = []
        round_chain.validate_round_execution_evidence(
            receipt,
            configuration,
            [route],
            label="round 1 forged receipt",
            issues=issues,
        )
        combined = "\n".join(issues)
        self.assertRegex(combined, "case-id binding")
        self.assertRegex(combined, "program-result binding")
        self.assertRegex(combined, "evaluation program binding")
        self.assertRegex(combined, "evidence verdict does not match")


class CompletionCompilationEvidenceTests(unittest.TestCase):
    def test_completion_reuses_hash_bound_lightcp_compilation(self):
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root).resolve()
            paths = {
                "std": root / "package/std.cpp",
                "brute": root / "package/brute.cpp",
                "validator": root / "package/validator.cpp",
                "wrong:W01": root / "audit/private/wrong-solutions/W01.cpp",
            }
            for role, path in paths.items():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"// {role}\nint main() {{}}\n", encoding="utf-8")
            compilation = []
            for role, path in paths.items():
                compilation.append(
                    {
                        "role": role,
                        "source": path.relative_to(root).as_posix(),
                        "source_sha256": completion.sha256_file(path),
                        "status": "passed",
                        "result": {
                            "returncode": 0,
                            "timed_out": False,
                            "stderr_preview": "",
                        },
                    }
                )
            receipt = {
                "configuration": {
                    "execution_backend": {
                        "name": "lightcpverifier",
                        "sandboxed": True,
                        "testing_only": False,
                    }
                },
                "compilation": compilation,
            }
            report = completion.Report(root, "", False)
            completion.check_compilation(
                report,
                paths["std"],
                paths["brute"],
                None,
                [paths["validator"]],
                [("W01", paths["wrong:W01"])],
                receipt,
            )
            self.assertTrue(report.passed, report.issues)
            self.assertIn("LightCPVerifier", report.checks[-1].evidence[0])

            receipt["configuration"]["execution_backend"]["name"] = "local"
            failed = completion.Report(root, "", False)
            completion.check_compilation(
                failed,
                paths["std"],
                paths["brute"],
                None,
                [paths["validator"]],
                [("W01", paths["wrong:W01"])],
                receipt,
            )
            self.assertFalse(failed.passed)
            self.assertRegex(failed.issues[0], "production sandboxed")


if __name__ == "__main__":
    unittest.main()
