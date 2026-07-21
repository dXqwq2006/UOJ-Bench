from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
BRIDGE_SRC = ROOT / "integrations" / "icpc_light_v33" / "src"
sys.path.insert(0, str(BRIDGE_SRC))

import solution

from solution import load_solver
from solution.api import (
    FaultCoverageInput,
    FaultExposureInput,
    GenerationInput,
    HackingInput,
    TestCaseFormat,
    TestPackageInput,
    require_solver_support,
)
from solution.icpc_light_v33_bridge import solver as bridge_solver
from solution.icpc_light_v33_bridge.solver import (
    BRIDGE_CONFIG_ENV,
    BRIDGE_ENV,
    BridgeProtocolError,
)
from uoj_skill_bridge.runtime import (
    BridgeContractError,
    CONFIG_ENV,
    _test_package_candidate,
    _tree_sha256,
    execute_request,
)
from uoj_skill_bridge.codex_agent import _final_message
from uoj_skill_bridge.zero_mount_scheduler import (
    SchedulerError,
    _attest_integration,
    _container_create_argv,
    _job_subnet,
)


def fake_bridge(root: Path, response: dict) -> Path:
    path = root / "fake-bridge"
    payload = json.dumps(response, ensure_ascii=False)
    path.write_text(
        textwrap.dedent(
            f"""\
            #!{sys.executable}
            import json
            import os
            import sys
            request = json.load(sys.stdin)
            assert request["schema_version"] == 1
            assert request["reasoning_effort"] == "xhigh"
            assert "UOJ_API_KEY" not in os.environ
            assert "TATU_API_KEY" not in os.environ
            assert os.environ.get("PYTHONDONTWRITEBYTECODE") == "1"
            sys.stdout.write({payload!r})
            """
        ),
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def fake_adapter_config(root: Path) -> tuple[Path, str]:
    path = root / "adapter-config.json"
    value: dict[str, object] = {}
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    path.write_bytes(canonical + b"\n")
    path.chmod(0o600)
    return path, hashlib.sha256(canonical).hexdigest()


def bind_fake_response(
    response: dict, config_sha256: str, request: dict
) -> dict:
    bound = dict(response)
    bound.setdefault("raw_text", "")
    bound.setdefault("transcript", [])
    bound.setdefault("usage", {})
    candidate = response["candidate"]
    candidate_bytes = (
        json.dumps(
            candidate, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        if candidate.get("kind") == "test_package"
        else candidate["content"].encode("utf-8")
    )
    request_bytes = json.dumps(
        request,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    bound["pipeline_identity"] = {
        "schema_version": 1,
        "profile": "adapter-unit-test",
        "model": "gpt-5.6-sol",
        "reasoning_effort": "xhigh",
        "request_sha256": hashlib.sha256(request_bytes).hexdigest(),
        "surface_sha256": "2" * 64,
        "skill_bundle_sha256": "1" * 64,
        "expected_skill_bundle_sha256": "1" * 64,
        "copied_skills_sha256": "3" * 64,
        "skill_files": 1,
        "skill_bytes": 1,
        "bridge_config_sha256": config_sha256,
        "agent_command": [{"index": 0, "value": "fake-bridge"}],
        "candidate_sha256": hashlib.sha256(candidate_bytes).hexdigest(),
    }
    signature_value = {
        key: bound["pipeline_identity"][key]
        for key in (
            "profile",
            "model",
            "reasoning_effort",
            "skill_bundle_sha256",
            "expected_skill_bundle_sha256",
            "copied_skills_sha256",
            "bridge_config_sha256",
            "agent_command",
        )
    }
    bound["pipeline_identity"]["pipeline_signature_sha256"] = hashlib.sha256(
        json.dumps(
            signature_value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    bound["message"] = {
        "role": "assistant",
        "content": str(response.get("raw_text", "")),
        "pipeline_identity": bound["pipeline_identity"],
        "receipt_sha256": "4" * 64,
    }
    return bound


def fake_task_agent(root: Path) -> Path:
    path = root / "fake-task-agent.py"
    path.write_text(
        textwrap.dedent(
            f"""\
            #!{sys.executable}
            import argparse
            import json
            import os
            from pathlib import Path

            parser = argparse.ArgumentParser()
            parser.add_argument("--task", required=True)
            parser.add_argument("--workspace", type=Path, required=True)
            args = parser.parse_args()
            assert "UOJ_API_KEY" not in os.environ
            assert "TATU_API_KEY" not in os.environ
            assert os.environ.get("PYTHONDONTWRITEBYTECODE") == "1"
            output = args.workspace / "output"
            if args.task == "generation":
                (output / "main.cpp").write_text("int main() {{ return 0; }}\\n")
            elif args.task == "test_package":
                audit = args.workspace / "audit"
                tests = args.workspace / "package" / "tests"
                audit.mkdir()
                tests.mkdir(parents=True)
                (audit / "readiness.md").write_text("verdict: go\\n")
                (audit / "regression-plan.json").write_text(json.dumps({{
                    "release_tests": [{{"input": "package/tests/01.in"}}]
                }}))
                (tests / "01.in").write_text("2 3\\n")
            else:
                (output / "candidate.in").write_text("2 3\\n")
            result = {{
                "schema_version": 1,
                "status": "completed",
                "task": args.task,
                "transcript": [],
                "usage": {{"total_tokens": 0}},
            }}
            (args.workspace / "control" / "agent-result.json").write_text(
                json.dumps(result) + "\\n"
            )
            """
        ),
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


class SolverAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        bridge_solver._FROZEN_CONFIG_BINDING = None
        bridge_solver._FROZEN_PIPELINE_SIGNATURE = None

    def tearDown(self) -> None:
        bridge_solver._FROZEN_CONFIG_BINDING = None
        bridge_solver._FROZEN_PIPELINE_SIGNATURE = None

    def test_generation_candidate_and_public_metadata(self) -> None:
        response = {
            "schema_version": 1,
            "status": "completed",
            "candidate": {"kind": "solution", "content": "int main() { return 0; }\n"},
            "transcript": [{"stage": "blind-review", "status": "verified"}],
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "message": {"pipeline": "icpc-light-v3.3-blind"},
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, config_sha256 = fake_adapter_config(root)
            expected_request = {
                "schema_version": 1,
                "task": "generation",
                "model": "gpt-5.6-sol",
                "reasoning_effort": "xhigh",
                "input": {
                    "problem_id": 7,
                    "problem_statement": "statement",
                    "language": "C++20",
                    "chinese": False,
                    "metadata": {"difficulty": "hard", "title_en": "Public title"},
                },
            }
            bridge = fake_bridge(
                root, bind_fake_response(response, config_sha256, expected_request)
            )
            with patch.dict(
                os.environ,
                {
                    BRIDGE_ENV: str(bridge),
                    BRIDGE_CONFIG_ENV: str(config),
                    "UOJ_API_KEY": "must-not-cross-boundary",
                    "TATU_API_KEY": "must-not-cross-boundary",
                },
            ):
                solver = load_solver("icpc_light_v33_bridge", "gpt-5.6-sol")
                task = GenerationInput(
                    7,
                    "statement",
                    metadata={
                        "difficulty": "hard",
                        "title_en": "Public title",
                        "secret": "scalar secret",
                        "correct_code": "private accepted source",
                    },
                )
                require_solver_support(solver, "generation")
                session = solver.start_generation(task)
                self.assertEqual(
                    session.initial_request["input"]["metadata"],
                    {"difficulty": "hard", "title_en": "Public title"},
                )
                turn = session.next()

        self.assertEqual(turn.candidate.source, "int main() { return 0; }\n")
        self.assertEqual(turn.usage["output_tokens"], 5)
        self.assertEqual(session.transcript[0]["status"], "verified")
        self.assertEqual(
            turn.message["pipeline_identity"]["skill_bundle_sha256"], "1" * 64
        )
        with self.assertRaisesRegex(ValueError, "already produced"):
            session.next()

    def test_hacking_raw_input_is_wrapped_as_python_generator(self) -> None:
        raw_input = "3\n1 2 3\n"
        response = {
            "schema_version": 1,
            "status": "completed",
            "candidate": {"kind": "hack", "format": "raw_input", "content": raw_input},
            "transcript": [],
            "usage": {},
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, config_sha256 = fake_adapter_config(root)
            expected_request = {
                "schema_version": 1,
                "task": "hacking",
                "model": "gpt-5.6-sol",
                "reasoning_effort": "xhigh",
                "input": {
                    "problem_id": 1,
                    "problem_statement": "statement",
                    "submission_code": "wrong source",
                    "submission_language": "C++20",
                    "chinese": False,
                    "metadata": {},
                },
            }
            bridge = fake_bridge(
                root, bind_fake_response(response, config_sha256, expected_request)
            )
            with patch.dict(
                os.environ,
                {BRIDGE_ENV: str(bridge), BRIDGE_CONFIG_ENV: str(config)},
            ):
                solver = load_solver("icpc_light_v33_bridge", "gpt-5.6-sol")
                require_solver_support(solver, "hacking")
                turn = solver.start_hacking(
                    HackingInput(1, "statement", "wrong source")
                ).next()

        namespace = {"__name__": "__main__"}
        with patch("sys.stdout.write") as write:
            exec(turn.candidate.generator, namespace)
        write.assert_called_once_with(raw_input)

    def test_fault_exposure_preserves_testcase_eval_candidate_format(self) -> None:
        raw_input = "3\n1 2 3\n"
        response = {
            "schema_version": 1,
            "status": "completed",
            "candidate": {
                "kind": "test_case",
                "format": "raw_input",
                "content": raw_input,
            },
            "transcript": [],
            "usage": {},
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, config_sha256 = fake_adapter_config(root)
            expected_request = {
                "schema_version": 1,
                "task": "fault_exposure",
                "model": "gpt-5.6-sol",
                "reasoning_effort": "xhigh",
                "input": {
                    "problem_id": "2000A",
                    "problem_statement": "statement",
                    "submission_id": 17,
                    "submission_code": "wrong source",
                    "submission_language": "Python 3",
                    "metadata": {"difficulty": "hard"},
                },
            }
            bridge = fake_bridge(
                root, bind_fake_response(response, config_sha256, expected_request)
            )
            with patch.dict(
                os.environ,
                {BRIDGE_ENV: str(bridge), BRIDGE_CONFIG_ENV: str(config)},
            ):
                solver = load_solver("icpc_light_v33_bridge", "gpt-5.6-sol")
                require_solver_support(solver, "fault_exposure")
                turn = solver.start_fault_exposure(
                    FaultExposureInput(
                        "2000A",
                        "statement",
                        17,
                        "wrong source",
                        "Python 3",
                        {"difficulty": "hard", "correct_code": "private source"},
                    )
                ).next()

        self.assertEqual(turn.candidate.content, raw_input)
        self.assertEqual(turn.candidate.format, TestCaseFormat.RAW_INPUT)

    def test_fault_coverage_exposes_only_public_problem_data(self) -> None:
        raw_input = "2 3\n"
        response = {
            "schema_version": 1,
            "status": "completed",
            "candidate": {
                "kind": "test_case",
                "format": "raw_input",
                "content": raw_input,
            },
            "transcript": [],
            "usage": {},
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, config_sha256 = fake_adapter_config(root)
            expected_request = {
                "schema_version": 1,
                "task": "fault_coverage",
                "model": "gpt-5.6-sol",
                "reasoning_effort": "xhigh",
                "input": {
                    "problem_id": "ccp:codeforces:1",
                    "problem_statement": "statement",
                    "metadata": {
                        "display_problem_id": "codeforces:1",
                        "memory_limit_mb": 256,
                        "time_limit_ms": 2000,
                    },
                },
            }
            bridge = fake_bridge(
                root, bind_fake_response(response, config_sha256, expected_request)
            )
            with patch.dict(
                os.environ,
                {BRIDGE_ENV: str(bridge), BRIDGE_CONFIG_ENV: str(config)},
            ):
                solver = load_solver("icpc_light_v33_bridge", "gpt-5.6-sol")
                require_solver_support(solver, "fault_coverage")
                session = solver.start_fault_coverage(
                    FaultCoverageInput(
                        "ccp:codeforces:1",
                        "statement",
                        {
                            "display_problem_id": "codeforces:1",
                            "memory_limit_mb": 256,
                            "time_limit_ms": 2000,
                            "row_index": 7,
                            "published_true_positive_rate": 0.99,
                        },
                    )
                )
                self.assertNotIn("submission_code", session.initial_request["input"])
                turn = session.next()

        self.assertEqual(turn.candidate.content, raw_input)
        self.assertEqual(turn.candidate.format, TestCaseFormat.RAW_INPUT)

    def test_package_exposes_only_statement_and_preserves_declared_order(self) -> None:
        response = {
            "schema_version": 1,
            "status": "completed",
            "candidate": {
                "kind": "test_package",
                "tests": [
                    {"path": "package/tests/02.in", "content": "second\n"},
                    {"path": "package/tests/01.in", "content": "first\n"},
                ],
                "artifact": {"readiness": "go"},
            },
            "transcript": [],
            "usage": {"total_tokens": 3},
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, config_sha256 = fake_adapter_config(root)
            expected_request = {
                "schema_version": 1,
                "task": "test_package",
                "model": "gpt-5.6-sol",
                "reasoning_effort": "xhigh",
                "input": {
                    "problem_id": "p",
                    "problem_statement": (
                        "Time limit: 2000 ms\n"
                        "Memory limit: 256 MB\n"
                        "PUBLIC STATEMENT"
                    ),
                    "metadata": {},
                },
            }
            bridge = fake_bridge(
                root, bind_fake_response(response, config_sha256, expected_request)
            )
            with patch.dict(
                os.environ,
                {BRIDGE_ENV: str(bridge), BRIDGE_CONFIG_ENV: str(config)},
            ):
                solver = load_solver("icpc_light_v33_bridge", "gpt-5.6-sol")
                require_solver_support(solver, "test_package")
                session = solver.start_test_package(
                    TestPackageInput(
                        "p",
                        "PUBLIC STATEMENT",
                        {
                            "accepted_source": "SECRET",
                            "time_limit_ms": 2000,
                            "memory_limit_mb": 256,
                        },
                    )
                )
                self.assertEqual(session.initial_request, expected_request)
                turn = session.next()

        self.assertEqual(
            [test.content for test in turn.candidate.tests],
            ["second\n", "first\n"],
        )
        self.assertEqual(
            turn.candidate.artifact["release_test_paths"],
            ["package/tests/02.in", "package/tests/01.in"],
        )


    def test_package_normalizes_upstream_resource_labels_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, _ = fake_adapter_config(root)
            with patch.dict(os.environ, {BRIDGE_CONFIG_ENV: str(config)}):
                solver = load_solver("icpc_light_v33_bridge", "gpt-5.6-sol")
            original = (
                "Title: A\n"
                "time_limit_ms: 1500\n"
                "memory_limit_mb: 512\n"
                "Description: unchanged"
            )
            request = solver.start_test_package(
                TestPackageInput("p", original, {})
            ).initial_request
        self.assertEqual(
            request["input"]["problem_statement"],
            (
                "Title: A\n"
                "Time limit: 1500 ms\n"
                "Memory limit: 512 MB\n"
                "Description: unchanged"
            ),
        )
        self.assertEqual(request["input"]["metadata"], {})

    def test_wrong_model_and_bridge_failures_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires model"):
            load_solver("icpc_light_v33_bridge", "gpt-oss-120b")

        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(BridgeProtocolError, BRIDGE_CONFIG_ENV):
                load_solver("icpc_light_v33_bridge", "gpt-5.6-sol")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, config_sha256 = fake_adapter_config(root)
            solver = None
            with patch.dict(os.environ, {BRIDGE_CONFIG_ENV: str(config)}):
                solver = load_solver("icpc_light_v33_bridge", "gpt-5.6-sol")
            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(BridgeProtocolError, BRIDGE_ENV):
                    solver.start_generation(GenerationInput(1, "statement")).next()

            response = {
                "schema_version": 1,
                "status": "completed",
                "candidate": {"kind": "solution", "content": "int main(){}\n"},
                "raw_text": "",
                "message": {},
                "transcript": [],
                "usage": {},
            }
            bridge = fake_bridge(root, response)
            with patch.dict(
                os.environ,
                {BRIDGE_ENV: str(bridge), BRIDGE_CONFIG_ENV: str(config)},
            ):
                with self.assertRaisesRegex(BridgeProtocolError, "pipeline_identity"):
                    solver.start_generation(GenerationInput(1, "statement")).next()


class BridgeRuntimeTests(unittest.TestCase):
    def test_codex_event_parser_uses_last_assistant_message(self) -> None:
        events = b"\n".join(
            [
                json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "first"}}).encode(),
                json.dumps({"role": "assistant", "content": "last"}).encode(),
            ]
        )
        self.assertEqual(_final_message(events), "last")

    def test_zero_mount_create_contract_is_xhigh_and_has_no_mounts(self) -> None:
        image = "sha256:" + "1" * 64
        argv = _container_create_argv(
            name="icpc-light-v33-agent-test",
            network_id="2" * 64,
            image_id=image,
            user="1000:1000",
            task="hacking",
        )
        self.assertNotIn("--mount", argv)
        self.assertNotIn("--volume", argv)
        self.assertNotIn("--tmpfs", argv)
        self.assertIn("SKILL_EVAL_REASONING_EFFORT=xhigh", argv)
        self.assertIn("OPENAI_API_KEY=skill-eval-placeholder-token", argv)
        self.assertEqual(argv[argv.index("--entrypoint") + 2], image)

    def test_zero_mount_job_subnets_are_small_and_deterministic(self) -> None:
        self.assertEqual(_job_subnet("0" * 20), "10.240.0.0/29")
        self.assertEqual(_job_subnet("0" * 19 + "1"), "10.240.0.8/29")
        with self.assertRaisesRegex(SchedulerError, "suffix"):
            _job_subnet("not-hex")

    def test_zero_mount_scheduler_binds_complete_integration_manifest(self) -> None:
        integration = ROOT / "integrations" / "icpc_light_v33"
        manifest_sha256 = hashlib.sha256(
            (integration / "MANIFEST.sha256").read_bytes()
        ).hexdigest()
        root, actual = _attest_integration(manifest_sha256)
        self.assertEqual(root, integration)
        self.assertEqual(actual, manifest_sha256)
        with self.assertRaisesRegex(SchedulerError, "identity changed"):
            _attest_integration("0" * 64)

    def test_vendored_skill_bundle_matches_lock_and_manifest(self) -> None:
        integration = ROOT / "integrations" / "icpc_light_v33"
        bundle = integration / "vendor" / "icpc-light-distilled-ver3.3.0"
        lock = json.loads(
            (integration / "SKILL_BUNDLE.lock.json").read_text(encoding="utf-8")
        )
        tree_sha, file_count, byte_count = _tree_sha256(bundle)
        self.assertEqual(tree_sha, lock["tree_sha256"])
        self.assertEqual(file_count, lock["regular_file_count"])
        self.assertEqual(byte_count, lock["regular_file_bytes"])

        manifest_path = bundle / "MANIFEST.sha256"
        self.assertEqual(
            hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
            lock["vendored_manifest_sha256"],
        )
        self.assertEqual(
            lock["source_manifest_sha256"],
            "f79d9655e7adeae65598c81075eb1ef90c416df8cb5b5e6a96ffb7a6f6ebd94d",
        )
        self.assertEqual(
            lock["source_release_sha256"],
            "4bd3b6ff8cd89eff49fb7f7417c207b43d2a966a7edd4613dfc40a3aa37683ca",
        )
        self.assertEqual(
            lock["publication_redactions"],
            [
                {
                    "path": "RELEASE.json",
                    "json_pointer": "/validation/live_docker_gojudge_e2e_reason",
                    "reason": "removed a host-specific absolute Docker socket path",
                }
            ],
        )
        expected = {"MANIFEST.sha256"}
        for line in manifest_path.read_text(encoding="utf-8").splitlines():
            digest, relative = line.split("  ", 1)
            normalized = relative.removeprefix("./")
            expected.add(normalized)
            self.assertEqual(
                hashlib.sha256((bundle / normalized).read_bytes()).hexdigest(),
                digest,
            )
        actual = {
            path.relative_to(bundle).as_posix()
            for path in bundle.rglob("*")
            if path.is_file()
        }
        self.assertEqual(actual, expected)

    def _fixture(self, root: Path, *, expected: str | None = None) -> tuple[Path, str]:
        workspaces = root / "jobs"
        workspaces.mkdir(mode=0o700)
        bundle = root / "bundle"
        skill = bundle / "skills" / "icpc-light-problem-builder"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("# deterministic test skill\n", encoding="utf-8")
        bundle_sha = _tree_sha256(bundle)[0]
        agent = fake_task_agent(root)
        config = {
            "schema_version": 1,
            "profile": "unit-test",
            "workspace_root": str(workspaces),
            "workspace_device": workspaces.stat().st_dev,
            "skill_bundle_root": str(bundle),
            "skill_bundle_device": bundle.stat().st_dev,
            "expected_skill_bundle_sha256": expected or bundle_sha,
            "agent_command": [str(Path(sys.executable).resolve()), str(agent)],
            "timeout_seconds": 10,
            "max_candidate_bytes": 1024 * 1024,
            "retain_workspaces": True,
        }
        config_path = root / "bridge-config.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        config_path.chmod(0o600)
        return config_path, bundle_sha

    def test_all_supported_runtime_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, bundle_sha = self._fixture(root)
            generation = {
                "schema_version": 1,
                "task": "generation",
                "model": "gpt-5.6-sol",
                "reasoning_effort": "xhigh",
                "input": {
                    "problem_id": 1,
                    "problem_statement": "statement",
                    "language": "C++20",
                    "chinese": False,
                    "metadata": {},
                },
            }
            hacking = {
                "schema_version": 1,
                "task": "hacking",
                "model": "gpt-5.6-sol",
                "reasoning_effort": "xhigh",
                "input": {
                    "problem_id": 1,
                    "problem_statement": "statement",
                    "submission_code": "wrong",
                    "submission_language": "Python3",
                    "chinese": False,
                    "metadata": {},
                },
            }
            fault_exposure = {
                "schema_version": 1,
                "task": "fault_exposure",
                "model": "gpt-5.6-sol",
                "reasoning_effort": "xhigh",
                "input": {
                    "problem_id": "2000A",
                    "problem_statement": "statement",
                    "submission_id": 17,
                    "submission_code": "wrong",
                    "submission_language": "Python 3",
                    "metadata": {},
                },
            }
            fault_coverage = {
                "schema_version": 1,
                "task": "fault_coverage",
                "model": "gpt-5.6-sol",
                "reasoning_effort": "xhigh",
                "input": {
                    "problem_id": "ccp:codeforces:1",
                    "problem_statement": "statement",
                    "metadata": {"time_limit_ms": 2000},
                },
            }
            test_package = {
                "schema_version": 1,
                "task": "test_package",
                "model": "gpt-5.6-sol",
                "reasoning_effort": "xhigh",
                "input": {
                    "problem_id": "p",
                    "problem_statement": "statement",
                    "metadata": {},
                },
            }
            with patch.dict(os.environ, {CONFIG_ENV: str(config)}):
                generation_response = execute_request(generation)
                hacking_response = execute_request(hacking)
                coverage_response = execute_request(fault_coverage)
                package_response = execute_request(test_package)
                fault_response = execute_request(fault_exposure)

            self.assertEqual(generation_response["candidate"]["kind"], "solution")
            self.assertEqual(hacking_response["candidate"]["format"], "raw_input")
            self.assertEqual(coverage_response["candidate"]["kind"], "test_case")
            self.assertEqual(package_response["candidate"]["kind"], "test_package")
            self.assertEqual(package_response["candidate"]["tests"][0]["content"], "2 3\n")
            self.assertEqual(fault_response["candidate"]["kind"], "test_case")
            self.assertEqual(fault_response["candidate"]["format"], "raw_input")
            for response in (
                generation_response,
                hacking_response,
                coverage_response,
                package_response,
                fault_response,
            ):
                identity = response["pipeline_identity"]
                self.assertEqual(identity["skill_bundle_sha256"], bundle_sha)
                self.assertEqual(identity["expected_skill_bundle_sha256"], bundle_sha)
                self.assertRegex(identity["copied_skills_sha256"], r"^[0-9a-f]{64}$")
                self.assertRegex(
                    identity["pipeline_signature_sha256"], r"^[0-9a-f]{64}$"
                )

    def test_package_parser_rejects_unsafe_or_inconsistent_release_plans(self) -> None:
        def workspace(root, release, *, verdict="go", extra=False):
            audit = root / "audit"
            tests = root / "package" / "tests"
            audit.mkdir(parents=True)
            tests.mkdir(parents=True)
            (audit / "readiness.md").write_text(
                f"verdict: {verdict}\n", encoding="utf-8"
            )
            (audit / "regression-plan.json").write_text(
                json.dumps({"release_tests": release}), encoding="utf-8"
            )
            (tests / "01.in").write_text("1\n", encoding="utf-8")
            if extra:
                (tests / "extra.in").write_text("2\n", encoding="utf-8")
            return root

        cases = (
            ([{"input": "../secret.in"}], "go", False, "escapes"),
            ([{"input": "package/tests/01.in"}], "stop", False, "not go"),
            ([{"input": "package/tests/01.in"}], "go", True, "enumerate every"),
            (
                [{"input": f"package/tests/{index}.in"} for index in range(51)],
                "go",
                False,
                "1 to 50",
            ),
        )
        for release, verdict, extra, message in cases:
            with self.subTest(message=message), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                workspace(root, release, verdict=verdict, extra=extra)
                with self.assertRaisesRegex(BridgeContractError, message):
                    _test_package_candidate(root, 1024 * 1024)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace(
                root, [{"input": "package/tests/01.in"}], verdict="go"
            )
            link = root / "package" / "tests" / "link.in"
            try:
                link.symlink_to(root / "package" / "tests" / "01.in")
            except OSError:
                self.skipTest("symlinks are unavailable")
            with self.assertRaisesRegex(BridgeContractError, "symlink"):
                _test_package_candidate(root, 1024 * 1024)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            external = root / "external-audit"
            external.mkdir()
            (external / "readiness.md").write_text("verdict: go\n")
            (external / "regression-plan.json").write_text(json.dumps({
                "release_tests": [{"input": "package/tests/01.in"}]
            }))
            tests = root / "package" / "tests"
            tests.mkdir(parents=True)
            (tests / "01.in").write_text("1\n")
            (root / "audit").symlink_to(external, target_is_directory=True)
            with self.assertRaisesRegex(BridgeContractError, "audit.*safe directory"):
                _test_package_candidate(root, 1024 * 1024)

    def test_runtime_rejects_skill_bundle_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, _ = self._fixture(root, expected="0" * 64)
            request = {
                "schema_version": 1,
                "task": "generation",
                "model": "gpt-5.6-sol",
                "reasoning_effort": "xhigh",
                "input": {
                    "problem_id": 1,
                    "problem_statement": "statement",
                    "language": "C++20",
                    "chinese": False,
                    "metadata": {},
                },
            }
            with patch.dict(os.environ, {CONFIG_ENV: str(config)}):
                with self.assertRaisesRegex(BridgeContractError, "frozen expected"):
                    execute_request(request)

    def test_tree_hash_rejects_hard_links(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = root / "original"
            linked = root / "linked"
            original.write_text("same inode\n", encoding="utf-8")
            try:
                os.link(original, linked)
            except (AttributeError, NotImplementedError, OSError):
                self.skipTest("hard links are unavailable on this filesystem")
            with self.assertRaisesRegex(BridgeContractError, "hard-linked"):
                _tree_sha256(root)

    def test_tree_hash_allows_empty_only_when_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(BridgeContractError, "empty"):
                _tree_sha256(root)
            digest, files, total = _tree_sha256(root, allow_empty=True)
            self.assertEqual(digest, hashlib.sha256(b"").hexdigest())
            self.assertEqual((files, total), (0, 0))


if __name__ == "__main__":
    unittest.main()
