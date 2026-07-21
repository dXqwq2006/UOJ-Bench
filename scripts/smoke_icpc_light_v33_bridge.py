"""Run a TEST-ONLY supported-task pipeline smoke with no model or UOJ calls."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, Iterator


ROOT = Path(__file__).resolve().parents[1]
INTEGRATION_ROOT = ROOT / "integrations" / "icpc_light_v33"
BRIDGE = INTEGRATION_ROOT / "bin" / "icpc-light-uoj-bridge"
FIXTURE_AGENT = (
    ROOT / "tests" / "fixtures" / "icpc_light_v33_bridge" / "deterministic_pipeline_agent.py"
)
FIXTURE_WORKER = (
    ROOT / "tests" / "fixtures" / "icpc_light_v33_bridge" / "deterministic_pipeline_worker.py"
)
SKILL_BUNDLE_ENV = "ICPC_LIGHT_SKILL_BUNDLE"
DEFAULT_SKILL_BUNDLE = Path(
    os.environ.get(
        SKILL_BUNDLE_ENV,
        str(INTEGRATION_ROOT / "vendor" / "icpc-light-distilled-ver3.3.0"),
    )
)
REVIEWED_UOJ_BASE = "e31cc22cd8d7e5f327a69bbfbeed74e1eae0a36b"

STATEMENT = """# Add Two Integers

Time Limit: 1 second
Memory Limit: 256 MB

Given two signed integers `a` and `b`, print `a + b`.

Input: one line containing `a b`.
Output: one line containing their sum.
"""

WRONG_SOURCE = r'''#include <bits/stdc++.h>
using namespace std;
int main() {
    long long a, b;
    if (!(cin >> a >> b)) return 0;
    cout << a - b << '\n';
    return 0;
}
'''

PYTHON_WRONG_SOURCE = "a, b = map(int, input().split())\nprint(a - b)\n"

REFERENCE_SOURCE = r'''#include <bits/stdc++.h>
using namespace std;
int main() {
    long long a, b;
    if (!(cin >> a >> b)) return 0;
    cout << a + b << '\n';
    return 0;
}
'''


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


@contextmanager
def _environment(values: dict[str, str]) -> Iterator[None]:
    previous = {name: os.environ.get(name) for name in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _load_uoj(
    uoj_root: Path, *, require_clean_checkout: bool
) -> tuple[Any, Any, Any, Any, Any, str, bool]:
    resolved = uoj_root.resolve(strict=True)
    if resolved != ROOT.resolve(strict=True):
        raise RuntimeError("the smoke must run against the checkout containing this script")
    git = shutil.which("git")
    if git is None:
        raise RuntimeError("git is required to verify the UOJ-Bench smoke checkout")
    provenance = subprocess.run(
        [str(Path(git).resolve(strict=True)), "-C", str(resolved), "rev-parse", "HEAD"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=10,
        check=False,
    )
    commit = provenance.stdout.strip()
    status = subprocess.run(
        [
            str(Path(git).resolve(strict=True)),
            "-C",
            str(resolved),
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=10,
        check=False,
    )
    if status.returncode != 0:
        raise RuntimeError("git status failed while verifying the smoke checkout")
    checkout_clean = not status.stdout.strip()
    if require_clean_checkout and not checkout_clean:
        raise RuntimeError(
            "deterministic smoke requires a clean tracked checkout; commit the exact "
            "integration first"
        )
    ancestry = subprocess.run(
        [
            str(Path(git).resolve(strict=True)),
            "-C",
            str(resolved),
            "merge-base",
            "--is-ancestor",
            REVIEWED_UOJ_BASE,
            commit,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
        check=False,
    )
    if provenance.returncode != 0 or ancestry.returncode != 0:
        raise RuntimeError(
            "UOJ-Bench checkout does not descend from the reviewed base "
            f"{REVIEWED_UOJ_BASE}: found {commit or 'unavailable'}"
        )
    if str(resolved) not in sys.path:
        sys.path.insert(0, str(resolved))
    for name in ("UOJ_API_KEY", "TATU_API_KEY", "OPENAI_API_KEY"):
        if name in os.environ:
            raise RuntimeError(f"deterministic smoke refuses credential environment: {name}")
    import solution

    from solution import load_solver
    from solution.api import (
        FaultCoverageInput,
        FaultExposureInput,
        GenerationInput,
        TestPackageInput,
        require_solver_support,
    )
    from solution.icpc_light_v33_bridge.solver import (
        BRIDGE_CONFIG_ENV,
        BRIDGE_ENV,
    )
    from scripts import run_hack_rollout_batch

    loaded_modules = {
        "solution": Path(solution.__file__).resolve(strict=True),
        "solution.api": Path(sys.modules["solution.api"].__file__).resolve(strict=True),
        "scripts.run_hack_rollout_batch": Path(
            run_hack_rollout_batch.__file__
        ).resolve(strict=True),
        "solution.icpc_light_v33_bridge.solver": Path(
            sys.modules["solution.icpc_light_v33_bridge.solver"].__file__
        ).resolve(strict=True),
    }
    escaped = {
        name: str(path) for name, path in loaded_modules.items() if not _inside(path, resolved)
    }
    if escaped:
        raise RuntimeError(f"UOJ smoke imported modules outside the reviewed checkout: {escaped}")

    return (
        load_solver,
        GenerationInput,
        FaultCoverageInput,
        FaultExposureInput,
        TestPackageInput,
        require_solver_support,
        (BRIDGE_ENV, BRIDGE_CONFIG_ENV),
        run_hack_rollout_batch,
        commit,
        checkout_clean,
    )


def _locked_skill_bundle_identity(
    bundle: Path, tree_hasher: Any
) -> tuple[str, int, int]:
    lock_path = INTEGRATION_ROOT / "SKILL_BUNDLE.lock.json"
    if lock_path.is_symlink() or not lock_path.is_file():
        raise RuntimeError("the frozen skill bundle lock is missing or unsafe")
    try:
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("the frozen skill bundle lock is not valid UTF-8 JSON") from exc
    required = {
        "schema_version",
        "bundle",
        "version",
        "source_head",
        "source_manifest_sha256",
        "source_release_sha256",
        "vendored_manifest_sha256",
        "tree_sha256",
        "regular_file_count",
        "regular_file_bytes",
        "publication_redactions",
        "publication_ports",
        "excluded",
    }
    if not isinstance(lock, dict) or set(lock) != required or lock.get("schema_version") != 1:
        raise RuntimeError("the frozen skill bundle lock has unexpected fields")
    expected = lock.get("tree_sha256")
    if not isinstance(expected, str) or len(expected) != 64:
        raise RuntimeError("the frozen skill bundle lock has no valid tree SHA-256")
    actual, file_count, byte_count = tree_hasher(bundle)
    if actual != expected:
        raise RuntimeError(
            f"skill bundle differs from SKILL_BUNDLE.lock.json: {actual} != {expected}"
        )
    if (file_count, byte_count) != (
        lock.get("regular_file_count"),
        lock.get("regular_file_bytes"),
    ):
        raise RuntimeError("skill bundle count/size differs from its frozen lock")
    manifest = bundle / "MANIFEST.sha256"
    if manifest.is_symlink() or not manifest.is_file():
        raise RuntimeError("skill bundle has no safe vendored manifest")
    if hashlib.sha256(manifest.read_bytes()).hexdigest() != lock.get(
        "vendored_manifest_sha256"
    ):
        raise RuntimeError("skill bundle manifest differs from its frozen lock")
    return expected, file_count, byte_count


def _compiler() -> str:
    compiler = shutil.which("c++") or shutil.which("g++") or shutil.which("clang++")
    if compiler is None:
        raise RuntimeError("a C++ compiler is required for the semantic smoke")
    return str(Path(compiler).resolve(strict=True))


def _compile(source: str, path: Path) -> Path:
    source_path = path.with_suffix(".cpp")
    source_path.write_text(source, encoding="utf-8")
    completed = subprocess.run(
        [_compiler(), "-std=c++20", "-O2", "-pipe", str(source_path), "-o", str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=60,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"C++ smoke compile failed: {completed.stderr[-2000:]}")
    return path


def _run(binary: Path, stdin: str) -> str:
    completed = subprocess.run(
        [str(binary)],
        input=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=5,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"smoke binary exited {completed.returncode}")
    return completed.stdout.strip()


def _run_python_source(source: str, stdin: str) -> str:
    completed = subprocess.run(
        [str(Path(sys.executable).resolve(strict=True)), "-I", "-c", source],
        input=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=5,
        check=False,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONIOENCODING": "utf-8"},
    )
    if completed.returncode != 0:
        raise RuntimeError(f"smoke Python wrong source failed: {completed.stderr[-2000:]}")
    return completed.stdout.strip()


def _generator_output(source: str) -> str:
    completed = subprocess.run(
        [str(Path(sys.executable).resolve(strict=True)), "-I", "-c", source],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=5,
        check=False,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONIOENCODING": "utf-8"},
    )
    if completed.returncode != 0:
        raise RuntimeError(f"hack generator failed: {completed.stderr[-2000:]}")
    if not completed.stdout:
        raise RuntimeError("hack generator produced an empty input")
    return completed.stdout


def _write_hack_dataset(root: Path) -> None:
    problems = [
        {
            "problem_id": "900001",
            "statement_en": STATEMENT,
            "difficulty": "easy",
            "hackable": 1,
            "title_en": "Add Two Integers Easy Smoke",
        },
        {
            "problem_id": "900002",
            "statement_en": STATEMENT,
            "difficulty": "hard",
            "hackable": 1,
            "title_en": "Add Two Integers Hard Smoke",
        },
    ]
    easy = [
        {
            "problem_id": "900001",
            "wrong_id": "smoke-wrong",
            "wrong_code": WRONG_SOURCE,
            "correct_code": "must never reach the solver",
            "language": "C++20",
        }
    ]
    hard = [
        {
            "hack_id": "smoke-hack",
            "submission_id": "smoke-submission",
            "problem_id": "900002",
            "wrong_code": PYTHON_WRONG_SOURCE,
            "language": "Python3",
        }
    ]
    for name, value in (
        ("problems.json", problems),
        ("sampled_large_submission_pairs.json", easy),
        ("hacks.json", hard),
    ):
        _write_json(root / name, value)


def run_smoke(
    *,
    uoj_root: Path,
    output_root: Path,
    skill_bundle: Path,
    require_clean_checkout: bool = True,
) -> dict[str, Any]:
    output_root = output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=False)
    workspaces = output_root / "jobs"
    workspaces.mkdir(mode=0o700)
    integration_src = INTEGRATION_ROOT / "src"
    if str(integration_src) not in sys.path:
        sys.path.insert(0, str(integration_src))
    from uoj_skill_bridge.runtime import _tree_sha256

    resolved_bundle = skill_bundle.resolve(strict=True)
    expected_bundle_sha256, bundle_file_count, bundle_byte_count = (
        _locked_skill_bundle_identity(resolved_bundle, _tree_sha256)
    )
    config_path = output_root / "bridge-config.json"
    config = {
        "schema_version": 1,
        "profile": "deterministic-wiring-smoke-v1",
        "workspace_root": str(workspaces),
        "workspace_device": workspaces.stat().st_dev,
        "skill_bundle_root": str(resolved_bundle),
        "skill_bundle_device": resolved_bundle.stat().st_dev,
        "expected_skill_bundle_sha256": expected_bundle_sha256,
        "agent_command": [
            str(Path(sys.executable).resolve(strict=True)),
            str(FIXTURE_AGENT.resolve(strict=True)),
            "--worker",
            str(FIXTURE_WORKER.resolve(strict=True)),
        ],
        "timeout_seconds": 30,
        "max_candidate_bytes": 1024 * 1024,
        "retain_workspaces": True,
    }
    _write_json(config_path, config)
    config_path.chmod(0o600)
    (
        load_solver,
        GenerationInput,
        FaultCoverageInput,
        FaultExposureInput,
        TestPackageInput,
        require_solver_support,
        env_names,
        rollout,
        uoj_commit,
        checkout_clean,
    ) = _load_uoj(uoj_root, require_clean_checkout=require_clean_checkout)
    env = {
        env_names[0]: str(BRIDGE.resolve(strict=True)),
        env_names[1]: str(config_path.resolve(strict=True)),
    }
    with _environment(env):
        solver = load_solver("icpc_light_v33_bridge", "gpt-5.6-sol")
        require_solver_support(solver, "generation")
        require_solver_support(solver, "hacking")
        require_solver_support(solver, "fault_coverage")
        require_solver_support(solver, "fault_exposure")
        require_solver_support(solver, "test_package")
        generation_turn = solver.start_generation(
            GenerationInput(
                900000,
                STATEMENT,
                metadata={"difficulty": "easy", "title_en": "Add Two Integers"},
            )
        ).next()
        if generation_turn.candidate is None:
            raise RuntimeError(f"generation returned no candidate: {generation_turn.error}")

        package_session = solver.start_test_package(
            TestPackageInput(
                "package-smoke",
                STATEMENT,
                {"correct_code": "must never reach the package workspace"},
            )
        )
        if package_session.initial_request["input"]["metadata"]:
            raise RuntimeError("test package request exposed private metadata")
        package_turn = package_session.next()
        if package_turn.candidate is None:
            raise RuntimeError(
                f"test package returned no candidate: {package_turn.error}"
            )
        if [test.content for test in package_turn.candidate.tests] != [
            "-7 4\n",
            "2 3\n",
        ]:
            raise RuntimeError("test package did not preserve release_tests order")

        fault_coverage_turn = solver.start_fault_coverage(
            FaultCoverageInput(
                "ccp:codeforces:900001",
                STATEMENT,
                {
                    "display_problem_id": "codeforces:900001",
                    "source": "codeforces",
                    "source_problem_id": "900001",
                    "time_limit_ms": 2000,
                    "memory_limit_mb": 256,
                    "row_index": 17,
                    "published_true_positive_rate": 0.99,
                },
            )
        ).next()
        if fault_coverage_turn.candidate is None:
            raise RuntimeError(
                "fault coverage returned no candidate: "
                f"{fault_coverage_turn.error}"
            )

        build = output_root / "local-evaluator"
        build.mkdir()
        generated_binary = _compile(generation_turn.candidate.source, build / "generated")
        generation_cases = {"2 3\n": "5", "-7 4\n": "-3", "0 0\n": "0"}
        for stdin, expected in generation_cases.items():
            actual = _run(generated_binary, stdin)
            if actual != expected:
                raise RuntimeError(
                    f"generation semantic smoke failed for {stdin!r}: {actual!r} != {expected!r}"
                )

        dataset = output_root / "dataset"
        dataset.mkdir()
        _write_hack_dataset(dataset)
        rollout_dir = output_root / "hack-rollout"
        summary = rollout.run_batch(
            dataset_dir=dataset,
            result_dir=rollout_dir,
            split="all",
            solver_name="icpc_light_v33_bridge",
            model="gpt-5.6-sol",
            workers=1,
            smoke_per_split=1,
            progress=False,
        )
        if summary["overall"] != {
            "planned": 2,
            "completed": 2,
            "valid_candidate": 2,
            "retryable_error": 0,
        }:
            raise RuntimeError(f"unexpected Hacking rollout summary: {summary['overall']}")

        fault_exposure_turn = solver.start_fault_exposure(
            FaultExposureInput(
                "900003",
                STATEMENT,
                17,
                WRONG_SOURCE,
                "C++20",
                {"difficulty": "hard", "title_en": "Add Two Integers Task 2 Smoke"},
            )
        ).next()
        if fault_exposure_turn.candidate is None:
            raise RuntimeError(
                "fault exposure returned no candidate: "
                f"{fault_exposure_turn.error}"
            )

    wrong_binary = _compile(WRONG_SOURCE, build / "wrong")
    reference_binary = _compile(REFERENCE_SOURCE, build / "reference")
    coverage_candidate = fault_coverage_turn.candidate
    if coverage_candidate.format.value == "raw_input":
        coverage_input = coverage_candidate.content
    elif coverage_candidate.format.value == "python_generator":
        coverage_input = _generator_output(coverage_candidate.content)
    else:
        raise RuntimeError("fault coverage returned an unsupported candidate format")
    coverage_wrong_output = _run(wrong_binary, coverage_input)
    coverage_expected_output = _run(reference_binary, coverage_input)
    if coverage_wrong_output == coverage_expected_output:
        raise RuntimeError("fault coverage did not expose the smoke wrong solution")
    fault_candidate = fault_exposure_turn.candidate
    if fault_candidate.format.value == "raw_input":
        fault_input = fault_candidate.content
    elif fault_candidate.format.value == "python_generator":
        fault_input = _generator_output(fault_candidate.content)
    else:
        raise RuntimeError("fault exposure returned an unsupported candidate format")
    fault_wrong_output = _run(wrong_binary, fault_input)
    fault_expected_output = _run(reference_binary, fault_input)
    if fault_wrong_output == fault_expected_output:
        raise RuntimeError("fault exposure did not expose the smoke wrong solution")
    hacking_records = []
    for path in sorted((rollout_dir / "samples").glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        message = record.get("message")
        if not isinstance(message, dict) or not isinstance(
            message.get("pipeline_identity"), dict
        ):
            raise RuntimeError(f"{path.name} did not persist the pipeline identity")
        pipeline_identity = message["pipeline_identity"]
        if pipeline_identity.get("skill_bundle_sha256") != expected_bundle_sha256:
            raise RuntimeError(f"{path.name} persisted the wrong skill bundle identity")
        generator = record.get("candidate")
        if not isinstance(generator, str):
            raise RuntimeError(f"{path.name} has no hack generator")
        candidate_input = _generator_output(generator)
        language = record["sample"]["submission_language"]
        if language == "Python3":
            wrong_output = _run_python_source(PYTHON_WRONG_SOURCE, candidate_input)
        else:
            wrong_output = _run(wrong_binary, candidate_input)
        expected_output = _run(reference_binary, candidate_input)
        if wrong_output == expected_output:
            raise RuntimeError(f"{path.name} did not expose the smoke wrong solution")
        hacking_records.append(
            {
                "sample_id": record["sample"]["sample_id"],
                "candidate_sha256": _sha256(generator),
                "input_sha256": _sha256(candidate_input),
                "wrong_output": wrong_output,
                "expected_output": expected_output,
                "receipt_sha256": message["receipt_sha256"],
                "pipeline_identity_sha256": _canonical_sha256(pipeline_identity),
            }
        )

    job_dirs = sorted(path for path in workspaces.iterdir() if path.is_dir())
    if len(job_dirs) != 6:
        raise RuntimeError(f"expected six isolated jobs, found {len(job_dirs)}")
    saw_python_hack = False
    generation_pipeline_detail: dict[str, Any] | None = None
    hacking_pipeline_details: list[dict[str, Any]] = []
    fault_coverage_pipeline_detail: dict[str, Any] | None = None
    fault_exposure_pipeline_detail: dict[str, Any] | None = None
    test_package_pipeline_detail: dict[str, Any] | None = None
    for job in job_dirs:
        names = {path.name for path in (job / "surface").iterdir()}
        if "correct.cpp" in names or "reference.cpp" in names:
            raise RuntimeError("a correct/reference source leaked into a task surface")
        request = json.loads((job / "surface" / "task.json").read_text(encoding="utf-8"))
        agent_result = json.loads(
            (job / "control" / "agent-result.json").read_text(encoding="utf-8")
        )
        detail = agent_result.get("pipeline_test_detail")
        if not isinstance(detail, dict):
            raise RuntimeError("pipeline fixture omitted pipeline_test_detail")
        if request["task"] == "generation":
            if (
                detail.get("execution_mode") != "v3.3-test-override-blind-sweep"
                or detail.get("lane_count") != 4
                or detail.get("neutral_count") != 2
                or detail.get("deceptive_count") != 2
                or not isinstance(detail.get("blind_review_receipt_sha256"), str)
            ):
                raise RuntimeError("Generation did not complete the 2+2 sweep and review smoke")
            generation_pipeline_detail = detail
        elif request["task"] == "hacking":
            if detail.get("execution_mode") != "test-override-public-only-hacking-slice":
                raise RuntimeError("Hacking did not complete its public-only task slice")
            hacking_pipeline_details.append(detail)
        elif request["task"] == "fault_coverage":
            if (
                detail.get("execution_mode")
                != "test-override-public-only-fault_coverage-slice"
            ):
                raise RuntimeError(
                    "Fault Coverage did not complete its public-only task slice"
                )
            if "wrong-source.txt" in names:
                raise RuntimeError("Fault Coverage surface received a target source")
            fault_coverage_pipeline_detail = detail
        elif request["task"] == "fault_exposure":
            if (
                detail.get("execution_mode")
                != "test-override-public-only-fault_exposure-slice"
            ):
                raise RuntimeError(
                    "Fault Exposure did not complete its public-only task slice"
                )
            fault_exposure_pipeline_detail = detail
        elif request["task"] == "test_package":
            if (
                detail.get("execution_mode")
                != "test-override-statement-only-package"
                or names != {"statement.md", "task.json"}
            ):
                raise RuntimeError("Test Package did not remain statement-only")
            test_package_pipeline_detail = detail
        else:
            raise RuntimeError(f"unexpected smoke task: {request['task']}")
        if request["task"] == "hacking" and request["input"]["submission_language"] == "Python3":
            saw_python_hack = True
            materialized = (job / "surface" / "wrong-source.txt").read_text(encoding="utf-8")
            if materialized != PYTHON_WRONG_SOURCE:
                raise RuntimeError("non-C++ wrong source changed during materialization")
        receipt = json.loads((job / "control" / "receipt.json").read_text(encoding="utf-8"))
        if receipt.get("status") != "completed":
            raise RuntimeError("a job receipt is not completed")
    if not saw_python_hack:
        raise RuntimeError("smoke did not exercise a non-C++ Hacking target")
    if (
        generation_pipeline_detail is None
        or len(hacking_pipeline_details) != 2
        or fault_coverage_pipeline_detail is None
        or fault_exposure_pipeline_detail is None
        or test_package_pipeline_detail is None
    ):
        raise RuntimeError("pipeline detail count does not match the five smoke jobs")

    report = {
        "schema_version": 1,
        "status": "passed",
        "execution_mode": "deterministic-pipeline-smoke-test-override-no-model-no-uoj",
        "uoj_bench_commit": uoj_commit,
        "checkout_clean": checkout_clean,
        "clean_checkout_required": require_clean_checkout,
        "skill_bundle": {
            "tree_sha256": expected_bundle_sha256,
            "regular_file_count": bundle_file_count,
            "regular_file_bytes": bundle_byte_count,
        },
        "generation": {
            "passed": True,
            "candidate_sha256": _sha256(generation_turn.candidate.source),
            "case_count": len(generation_cases),
            "receipt_sha256": generation_turn.message["receipt_sha256"],
            "pipeline_identity_sha256": _canonical_sha256(
                generation_turn.message["pipeline_identity"]
            ),
            "pipeline": generation_pipeline_detail,
        },
        "hacking": {
            "passed": True,
            "rollout_summary": summary["overall"],
            "semantically_exposed": len(hacking_records),
            "records": hacking_records,
            "pipeline_receipts": [
                item["receipt_sha256"] for item in hacking_pipeline_details
            ],
        },
        "fault_coverage": {
            "passed": True,
            "candidate_format": coverage_candidate.format.value,
            "candidate_sha256": _sha256(coverage_candidate.content),
            "input_sha256": _sha256(coverage_input),
            "wrong_output": coverage_wrong_output,
            "expected_output": coverage_expected_output,
            "receipt_sha256": fault_coverage_turn.message["receipt_sha256"],
            "pipeline_identity_sha256": _canonical_sha256(
                fault_coverage_turn.message["pipeline_identity"]
            ),
            "pipeline": fault_coverage_pipeline_detail,
        },
        "fault_exposure": {
            "passed": True,
            "candidate_format": fault_candidate.format.value,
            "candidate_sha256": _sha256(fault_candidate.content),
            "input_sha256": _sha256(fault_input),
            "wrong_output": fault_wrong_output,
            "expected_output": fault_expected_output,
            "receipt_sha256": fault_exposure_turn.message["receipt_sha256"],
            "pipeline_identity_sha256": _canonical_sha256(
                fault_exposure_turn.message["pipeline_identity"]
            ),
            "pipeline": fault_exposure_pipeline_detail,
        },
        "test_package": {
            "passed": True,
            "test_count": len(package_turn.candidate.tests),
            "test_sha256": [
                _sha256(test.content) for test in package_turn.candidate.tests
            ],
            "release_test_paths": package_turn.candidate.artifact[
                "release_test_paths"
            ],
            "receipt_sha256": package_turn.message["receipt_sha256"],
            "pipeline_identity_sha256": _canonical_sha256(
                package_turn.message["pipeline_identity"]
            ),
            "pipeline": test_package_pipeline_detail,
        },
        "isolation": {
            "job_count": len(job_dirs),
            "correct_source_exposed": False,
            "non_cpp_hacking_surface_checked": True,
            "model_called": False,
            "uoj_called": False,
        },
    }
    _write_json(output_root / "smoke-report.json", report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uoj-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--skill-bundle", type=Path, default=DEFAULT_SKILL_BUNDLE)
    args = parser.parse_args(argv)
    report = run_smoke(
        uoj_root=args.uoj_root,
        output_root=args.output_root,
        skill_bundle=args.skill_bundle,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
