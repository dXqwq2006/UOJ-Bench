from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path, PurePosixPath


SCRIPT_DIR = (
    Path(__file__).resolve().parents[1]
    / "skills/icpc-light-problem-builder/scripts"
)
sys.path.insert(0, str(SCRIPT_DIR))

import record_adversarial_round as recorder  # noqa: E402
import regression_backend as backend_module  # noqa: E402
import verify_adversarial_round_chain as chain  # noqa: E402


def make_problem(root: Path) -> tuple[dict[str, object], Path, PurePosixPath]:
    (root / "statement.md").write_text(
        "Time Limit: 1.5 s\nMemory Limit: 256 MiB\n", encoding="utf-8"
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
    plan_rel = PurePosixPath("audit/adversarial-round-plans/round-01.json")
    plan_path = root.joinpath(*plan_rel.parts)
    plan_path.parent.mkdir(parents=True)
    raw: dict[str, object] = {
        "schema_version": 1,
        "round": 1,
        "trigger": "initial-matrix",
        "delta": "initial resource-bound breaker",
        "previous_receipt": None,
        "compile_timeout_seconds": 30,
        "tests": [
            {
                "test_id": "breaker",
                "input_path": "package/tests/breaker.in",
                "answer_path": "package/tests/breaker.ans",
                "comparison": "tokens",
            }
        ],
        "routes": [
            {
                "route_id": "W01",
                "source_path": "audit/private/wrong-solutions/W01.cpp",
                "breaker_test_id": "breaker",
            }
        ],
    }
    plan_path.write_text(json.dumps(raw), encoding="utf-8")
    return raw, plan_path, plan_rel


class AdversarialRoundStatementPolicyTests(unittest.TestCase):
    def test_plan_defaults_to_exact_statement_limits(self):
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root).resolve()
            raw, _plan_path, plan_rel = make_problem(root)
            self.assertNotIn("timeout_seconds", raw)
            self.assertNotIn("memory_limit_mb", raw)
            parsed = recorder.parse_plan(
                raw, problem_root=root, plan_rel=plan_rel
            )
            self.assertEqual(parsed["timeout"], 1.5)
            self.assertEqual(parsed["memory_limit_mb"], 256)
            policy = parsed["statement_resources"]
            self.assertEqual(policy.time_limit_ms, 1500)
            self.assertEqual(policy.memory_limit_mib, 256)

    def test_explicit_plan_limits_must_equal_statement(self):
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root).resolve()
            raw, _plan_path, plan_rel = make_problem(root)
            matching = dict(raw, timeout_seconds=1.5, memory_limit_mb=256)
            parsed = recorder.parse_plan(
                matching, problem_root=root, plan_rel=plan_rel
            )
            self.assertEqual(parsed["timeout"], 1.5)

            with self.assertRaisesRegex(recorder.ContractError, "time limit"):
                recorder.parse_plan(
                    dict(raw, timeout_seconds=2),
                    problem_root=root,
                    plan_rel=plan_rel,
                )
            with self.assertRaisesRegex(recorder.ContractError, "finite positive"):
                recorder.parse_plan(
                    dict(raw, timeout_seconds=None),
                    problem_root=root,
                    plan_rel=plan_rel,
                )
            with self.assertRaisesRegex(recorder.ContractError, "time limit"):
                recorder.parse_plan(
                    dict(raw, timeout_seconds=1.5001),
                    problem_root=root,
                    plan_rel=plan_rel,
                )
            with self.assertRaisesRegex(recorder.ContractError, "memory limit"):
                recorder.parse_plan(
                    dict(raw, memory_limit_mb=None),
                    problem_root=root,
                    plan_rel=plan_rel,
                )
            with self.assertRaisesRegex(recorder.ContractError, "memory limit"):
                recorder.parse_plan(
                    dict(raw, memory_limit_mb=1024),
                    problem_root=root,
                    plan_rel=plan_rel,
                )

    def test_local_round_receipt_binds_policy_and_uses_statement_timeout(self):
        if not any(shutil.which(name) for name in ("c++", "g++", "clang++")):
            self.skipTest("C++ compiler unavailable")
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root).resolve()
            raw, plan_path, plan_rel = make_problem(root)
            parsed = recorder.parse_plan(raw, problem_root=root, plan_rel=plan_rel)
            receipt = recorder.execute_round(
                problem_root=root,
                plan_rel=plan_rel,
                plan_path=plan_path,
                parsed=parsed,
                execution_backend="local",
                test_mode=True,
                lightcpverifier_url="http://127.0.0.1:1",
            )
            self.assertEqual(receipt["statement_resources"]["time_limit_ms"], 1500)
            self.assertEqual(receipt["statement_resources"]["memory_limit_mib"], 256)
            self.assertEqual(
                receipt["statement_resources_sha256"],
                parsed["statement_resources"].canonical_sha256(),
            )
            backend = receipt["execution_backend"]
            self.assertEqual(backend["requested_program_timeout_seconds"], 1.5)
            self.assertEqual(backend["requested_memory_limit_mb"], 256)
            self.assertEqual(receipt["routes"][0]["verdict"], "WA")
            self.assertEqual(receipt["routes"][0]["execution"]["memory_bytes"], 0)

    def test_statement_change_after_parse_is_rejected(self):
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root).resolve()
            raw, plan_path, plan_rel = make_problem(root)
            parsed = recorder.parse_plan(raw, problem_root=root, plan_rel=plan_rel)
            (root / "statement.md").write_text(
                "Time Limit: 2 s\nMemory Limit: 256 MiB\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(recorder.ContractError, "changed"):
                recorder.execute_round(
                    problem_root=root,
                    plan_rel=plan_rel,
                    plan_path=plan_path,
                    parsed=parsed,
                    execution_backend="local",
                    test_mode=True,
                    lightcpverifier_url="http://127.0.0.1:1",
                )


class AdversarialRoundVerdictTests(unittest.TestCase):
    @staticmethod
    def result(
        verdict: str | None,
        *,
        timed_out: bool = False,
        returncode: int | None = -1,
        launch_error: str | None = None,
    ) -> backend_module.ProgramResult:
        return backend_module.ProgramResult(
            returncode=returncode,
            timed_out=timed_out,
            duration_seconds=0.01,
            stdout=b"",
            stderr=b"",
            memory_bytes=1234,
            launch_error=launch_error,
            sandbox_verdict=verdict,
            sandbox_status=verdict,
        )

    def test_recorder_preserves_every_runtime_resource_verdict(self):
        cases = (
            (self.result("TLE", timed_out=True), "TLE"),
            (self.result("MLE"), "MLE"),
            (self.result("MLE", timed_out=True), "MLE"),
            (self.result("OLE", launch_error="output truncated"), "OLE"),
            (self.result("RE"), "RE"),
            (self.result(None, returncode=7), "RE"),
        )
        for result, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(recorder.program_failure_verdict(result), expected)

    def test_round_execution_record_reconstructs_program_result_with_memory(self):
        result = self.result("EXECUTED", returncode=0)
        record = recorder.backend_execution_record(
            result, role="wrong:W01", timeout=1.5, backend_name="lightcpverifier"
        )
        issues: list[str] = []
        compact = chain.compact_route_program_result(
            {"execution": record}, label="round result", issues=issues
        )
        self.assertEqual(issues, [])
        self.assertEqual(compact, result.compact())

    def test_compile_evidence_uses_dynamic_statement_profile(self):
        backend = {
            "requested_program_timeout_seconds": 1.5,
            "sandbox_effective_time_limit_seconds": 1.5,
            "requested_memory_limit_mb": 256,
            "effective_memory_limit_mb": 256,
            "max_output_bytes_per_stream": 16 * 1024 * 1024,
            "compile_context_policy_revision": (
                backend_module.COMPILE_CONTEXT_POLICY_REVISION
            ),
            "cpp_compiler_profile": backend_module.LIGHTCP_CPP_PROFILE,
            "service_identity": {
                "compilerProfile": backend_module.LIGHTCP_CPP_PROFILE,
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
            },
        }
        evidence = {
            "schema_version": 1,
            "kind": "cpideas.dataset_compilation",
            "dataset_api_revision": chain.DATASET_API_REVISION,
            "source_name": "audit/private/wrong-solutions/W01.cpp",
            "source_sha256": "a" * 64,
            "compile_context_policy_revision": (
                backend_module.COMPILE_CONTEXT_POLICY_REVISION
            ),
            "compile_copy_in_files_sha256": (
                backend_module.compile_context_sha256()
            ),
            "status": "COMPILED",
            "ok": True,
            "runtime_profile_for_subsequent_execution": {
                "requested_time_limit_ms": 1500,
                "effective_time_limit_ms": 1500,
                "effective_wall_time_limit_ms": 3000,
                "requested_memory_limit_mb": 256,
                "effective_memory_limit_mb": 256,
                "requested_max_output_bytes": 16 * 1024 * 1024,
                "effective_max_output_bytes": 16 * 1024 * 1024,
            },
            "compiler_limits": {
                "cpu_time_ms": 10000,
                "memory_mb": 512,
                "process_limit": 50,
            },
        }
        evidence["evidence_sha256"] = chain.canonical_digest(evidence)
        compile_record = {
            "command": ["$BACKEND_COMPILE"],
            "timed_out": False,
            "spawn_error": None,
            "exit_code": 0,
            "stdout": {},
            "stderr": {},
            "source": "audit/private/wrong-solutions/W01.cpp",
            "source_sha256": "a" * 64,
            "compilation_evidence": evidence,
        }
        self.assertTrue(chain.successful_compile(compile_record, backend))
        evidence["runtime_profile_for_subsequent_execution"][
            "effective_memory_limit_mb"
        ] = 1024
        core = dict(evidence)
        core.pop("evidence_sha256")
        evidence["evidence_sha256"] = chain.canonical_digest(core)
        self.assertFalse(chain.successful_compile(compile_record, backend))

    def test_chain_derives_mle_ole_and_re_without_collapsing_them(self):
        with tempfile.TemporaryDirectory() as raw_root:
            answer = Path(raw_root) / "answer.txt"
            answer.write_text("0\n", encoding="utf-8")
            for verdict in ("TLE", "MLE", "OLE", "RE"):
                with self.subTest(verdict=verdict):
                    route = {
                        "execution": {
                            "timed_out": verdict == "TLE",
                            "spawn_error": (
                                "output truncated" if verdict == "OLE" else None
                            ),
                            "exit_code": -1,
                            "sandbox_verdict": verdict,
                        }
                    }
                    self.assertEqual(
                        chain.derived_verdict(route, {"comparison": "tokens"}, answer),
                        verdict,
                    )


if __name__ == "__main__":
    unittest.main()
