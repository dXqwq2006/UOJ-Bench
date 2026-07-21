import ast
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = "ce1c006d9f6cf57670d15e62c3e63a08ea669adb"
TASK_SCRIPTS = (
    "scripts/test_problem.py",
    "scripts/test_hack.py",
    "scripts/test_debug.py",
    "scripts/test_hack_agent.py",
    "scripts/test_debug_agent.py",
)
MODIFIED_UPSTREAM = {*TASK_SCRIPTS}
REMOVED_UPSTREAM = {
    "utils/__pycache__/call_llm.cpython-311.pyc",
    "utils/__pycache__/patch.cpython-311.pyc",
    "utils/__pycache__/uoj_api.cpython-311.pyc",
    "utils/call_llm.py",
}
ICPC_LIGHT_V33_ADDED = {
    "integrations/icpc_light_v33/MANIFEST.sha256",
    "integrations/icpc_light_v33/README.md",
    "integrations/icpc_light_v33/SKILL_BUNDLE.lock.json",
    "integrations/icpc_light_v33/bin/icpc-light-uoj-bridge",
    "integrations/icpc_light_v33/bin/icpc-light-uoj-codex-agent",
    "integrations/icpc_light_v33/bin/icpc-light-uoj-zero-mount-scheduler",
    "integrations/icpc_light_v33/contracts/agent-result.schema.json",
    "integrations/icpc_light_v33/contracts/bridge-config.schema.json",
    "integrations/icpc_light_v33/contracts/job-request.schema.json",
    "integrations/icpc_light_v33/contracts/job-response.schema.json",
    "integrations/icpc_light_v33/docker/agent-xhigh.Dockerfile",
    "integrations/icpc_light_v33/docker/codex_wrapper.py",
    "integrations/icpc_light_v33/src/uoj_skill_bridge/__init__.py",
    "integrations/icpc_light_v33/src/uoj_skill_bridge/codex_agent.py",
    "integrations/icpc_light_v33/src/uoj_skill_bridge/runtime.py",
    "integrations/icpc_light_v33/src/uoj_skill_bridge/zero_mount_scheduler.py",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/.gitignore",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/CHANGELOG.md",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/ISOLATION.md",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/MANIFEST.sha256",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/MIGRATION.md",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/README.md",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/RELEASE.json",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/VERSION",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/agents/icpc-light-blind-solve-sweep/AGENT.md",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/agents/icpc-light-build-and-harden/AGENT.md",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/agents/icpc-light-orchestrator/AGENT.md",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/agents/icpc-light-readiness-review/AGENT.md",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/agents/icpc-light-review/AGENT.md",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/grade-test-data-buildability/SKILL.md",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/grade-test-data-buildability/agents/openai.yaml",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/grade-test-data-buildability/references/three-level-rubric.md",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/grade-test-data-buildability/scripts/validate_report.py",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/icpc-light-problem-builder/SKILL.md",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/icpc-light-problem-builder/agents/openai.yaml",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/icpc-light-problem-builder/references/artifact-contracts.md",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/icpc-light-problem-builder/references/blind-solve.md",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/icpc-light-problem-builder/references/build-and-harden.md",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/icpc-light-problem-builder/references/orchestration.md",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/icpc-light-problem-builder/references/readiness.md",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/icpc-light-problem-builder/references/review.md",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/icpc-light-problem-builder/references/topic-routing.md",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/icpc-light-problem-builder/references/topics/adversarial/arrays.md",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/icpc-light-problem-builder/references/topics/adversarial/bitwise.md",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/icpc-light-problem-builder/references/topics/adversarial/constructive.md",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/icpc-light-problem-builder/references/topics/adversarial/data-structures.md",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/icpc-light-problem-builder/references/topics/adversarial/dp.md",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/icpc-light-problem-builder/references/topics/adversarial/games.md",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/icpc-light-problem-builder/references/topics/adversarial/geometry.md",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/icpc-light-problem-builder/references/topics/adversarial/graphs.md",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/icpc-light-problem-builder/references/topics/adversarial/greedy.md",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/icpc-light-problem-builder/references/topics/adversarial/hashing.md",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/icpc-light-problem-builder/references/topics/adversarial/invariants.md",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/icpc-light-problem-builder/references/topics/adversarial/matching-and-flow.md",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/icpc-light-problem-builder/references/topics/adversarial/number-theory.md",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/icpc-light-problem-builder/references/topics/adversarial/numeric-stability.md",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/icpc-light-problem-builder/references/topics/adversarial/optimization.md",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/icpc-light-problem-builder/references/topics/adversarial/permutations.md",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/icpc-light-problem-builder/references/topics/adversarial/randomized-search.md",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/icpc-light-problem-builder/references/topics/adversarial/search.md",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/icpc-light-problem-builder/references/topics/adversarial/shortest-paths.md",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/icpc-light-problem-builder/references/topics/adversarial/strings.md",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/icpc-light-problem-builder/references/topics/adversarial/trees.md",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/icpc-light-problem-builder/references/topics/test-data/amortized-structures.md",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/icpc-light-problem-builder/references/topics/test-data/arrays.md",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/icpc-light-problem-builder/references/topics/test-data/data-structures.md",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/icpc-light-problem-builder/references/topics/test-data/dp-optimizations.md",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/icpc-light-problem-builder/references/topics/test-data/dynamic-programming.md",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/icpc-light-problem-builder/references/topics/test-data/floating-point.md",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/icpc-light-problem-builder/references/topics/test-data/games.md",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/icpc-light-problem-builder/references/topics/test-data/geometry.md",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/icpc-light-problem-builder/references/topics/test-data/graphs.md",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/icpc-light-problem-builder/references/topics/test-data/greedy.md",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/icpc-light-problem-builder/references/topics/test-data/hash.md",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/icpc-light-problem-builder/references/topics/test-data/intervals.md",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/icpc-light-problem-builder/references/topics/test-data/invariants.md",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/icpc-light-problem-builder/references/topics/test-data/number-theory.md",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/icpc-light-problem-builder/references/topics/test-data/permutations.md",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/icpc-light-problem-builder/references/topics/test-data/probability-and-randomization.md",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/icpc-light-problem-builder/references/topics/test-data/search-and-restarts.md",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/icpc-light-problem-builder/references/topics/test-data/shortest-paths.md",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/icpc-light-problem-builder/references/topics/test-data/simulation.md",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/icpc-light-problem-builder/references/topics/test-data/strings.md",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/icpc-light-problem-builder/references/topics/test-data/trees.md",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/icpc-light-problem-builder/scripts/build_resource_policy.py",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/icpc-light-problem-builder/scripts/build_sweep.py",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/icpc-light-problem-builder/scripts/contamination_status.py",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/icpc-light-problem-builder/scripts/record_adversarial_round.py",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/icpc-light-problem-builder/scripts/regression_backend.py",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/icpc-light-problem-builder/scripts/run_blind_review.py",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/icpc-light-problem-builder/scripts/run_regression_gate.py",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/icpc-light-problem-builder/scripts/run_stage_agent.py",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/icpc-light-problem-builder/scripts/run_sweep.py",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/icpc-light-problem-builder/scripts/statement_resources.py",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/icpc-light-problem-builder/scripts/verify_adversarial_round_chain.py",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/icpc-light-problem-builder/scripts/verify_blind_stage.py",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/icpc-light-problem-builder/scripts/verify_completion.py",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/icpc-light-problem-builder/scripts/verify_completion_handoff.py",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/icpc-light-problem-builder/scripts/verify_preclassification.py",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/icpc-light-problem-builder/scripts/verify_readiness.py",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/icpc-light-problem-builder/scripts/verify_solution_draft_handoff.py",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/icpc-light-problem-builder/scripts/verify_solution_handoff.py",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/icpc-light-problem-builder/scripts/verify_statement_resources.py",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/icpc-light-problem-builder/scripts/verify_std_materialization_handoff.py",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/using-testlib/SKILL.md",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/using-testlib/references/components/checkers.md",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/using-testlib/references/components/generators.md",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/using-testlib/references/components/graders-and-public-interfaces.md",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/using-testlib/references/components/interactors-and-communication.md",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/using-testlib/references/components/validators.md",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/using-testlib/references/core-api.md",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/using-testlib/references/local-run-and-debug.md",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/using-testlib/references/review-checklists.md",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/skills/using-testlib/references/templates.md",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/tests/test_adversarial_round_resources.py",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/tests/test_blind_review_status.py",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/tests/test_completion_coverage.py",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/tests/test_package_privacy.py",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/tests/test_regression_backend.py",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/tests/test_resource_policy.py",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/tests/test_stage_output_isolation.py",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/tests/test_standard_route_adoption.py",
    "integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0/tests/test_statement_resources.py",
}
ADDED = {
    *ICPC_LIGHT_V33_ADDED,
    ".github/workflows/tests.yml",
    ".gitignore",
    "README_SOLVER.md",
    "docs/BENCHMARK_MATRIX.md",
    "docs/ICPC_LIGHT_V33_BRIDGE.zh-CN.md",
    "docs/ICPC_LIGHT_V33_ZERO_MOUNT_HANDOFF.zh-CN.md",
    "docker/testcase_eval.Dockerfile",
    "requirements.txt",
    "scripts/run_hack_agent_batch.py",
    "scripts/run_hack_evaluation_batch.py",
    "scripts/run_hack_rollout_batch.py",
    "scripts/run_testcase_eval_batch.py",
    "scripts/smoke_icpc_light_v33_bridge.py",
    "scripts/test_testcase_eval_task1.py",
    "scripts/test_testcase_eval_task2.py",
    "solution/llm/__init__.py",
    "solution/llm/call_llm.py",
    "solution/icpc_light_v33_bridge/__init__.py",
    "solution/icpc_light_v33_bridge/solver.py",
    "solution/__init__.py",
    "solution/api.py",
    "solution/prompt/__init__.py",
    "solution/prompt/call_llm.py",
    "solution/prompt/prompts.py",
    "solution/prompt/solver.py",
    "solution/testcase_eval/__init__.py",
    "solution/testcase_eval/prompts.py",
    "solution/testcase_eval/solver.py",
    "solution/testcase_eval_task1_cot/__init__.py",
    "solution/testcase_eval_task1_cot/prompts.py",
    "solution/testcase_eval_task1_direct/__init__.py",
    "solution/testcase_eval_task1_direct/prompts.py",
    "tests/test_call_llm.py",
    "tests/fixtures/icpc_light_v33_bridge/__init__.py",
    "tests/fixtures/icpc_light_v33_bridge/deterministic_pipeline_agent.py",
    "tests/fixtures/icpc_light_v33_bridge/deterministic_pipeline_worker.py",
    "tests/test_hack_batch.py",
    "tests/test_hack_evaluation.py",
    "tests/test_hack_rollout.py",
    "tests/test_icpc_light_v33_bridge.py",
    "tests/test_icpc_light_v33_bridge_smoke.py",
    "tests/test_solver.py",
    "tests/test_testcase_eval.py",
    "tests/test_testcase_eval_benchmark.py",
    "tests/test_testcase_eval_lightcp.py",
    "tests/test_tasks.py",
    "tests/test_codecontests_plus.py",
    "tests/test_fault_coverage_benchmark.py",
    "tests/test_upstream_boundary.py",
    "tests/test_upstream_differential.py",
    "utils/benchmark.py",
    "utils/fault_coverage_benchmark.py",
    "utils/codecontests_plus.py",
    "utils/testcase_eval_benchmark.py",
    "utils/testcase_eval_executor.py",
    "utils/testcase_eval_lightcp.py",
    "scripts/run_codecontests_plus.py",
    "scripts/run_test_packages.py",
    "scripts/test_paper_hardtestgen.py",
    "solution/hardtestgen/__init__.py",
    "solution/hardtestgen/api.py",
    "solution/hardtestgen/lightcp.py",
    "solution/hardtestgen/pipeline.py",
    "solution/hardtestgen/prompt_templates/test_cases_kit_prompt_ig.toml",
    "solution/hardtestgen/prompt_templates/test_cases_kit_prompt_iv_and_ojf.toml",
    "solution/hardtestgen/prompts.py",
    "tests/test_hardtestgen.py",
    "tests/test_test_package_benchmark.py",
    "utils/hardtestgen_benchmark.py",
    "utils/test_package_benchmark.py",
}


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT)


def upstream(path):
    return git("show", f"{UPSTREAM}:{path}")


def prompt_constants(source):
    values = {}
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if (
            isinstance(target, ast.Name)
            and (target.id.startswith("prompt") or target.id.startswith("try_again_prompt"))
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            values[target.id] = node.value.value
    return values


class UpstreamBoundaryTests(unittest.TestCase):
    def test_protected_benchmark_files_are_byte_identical(self):
        dataset = git("ls-tree", "-r", "--name-only", UPSTREAM, "dataset").decode().splitlines()
        protected = ["README.md", "utils/patch.py", "utils/uoj_api.py", *dataset]
        for path in protected:
            with self.subTest(path=path):
                self.assertEqual((ROOT / path).read_bytes(), upstream(path))

    def test_official_prompt_text_is_unchanged(self):
        generation = prompt_constants(upstream("scripts/test_problem.py").decode())
        hacking = prompt_constants(upstream("scripts/test_hack.py").decode())
        repair = prompt_constants(upstream("scripts/test_debug.py").decode())
        expected = {
            "prompt_generation": generation["prompt"],
            "prompt_generation_chinese": generation["prompt_chinese"],
            "prompt_hacking": hacking["prompt"],
            "prompt_hacking_chinese": hacking["prompt_chinese"],
            "try_again_prompt_hacking": hacking["try_again_prompt"],
            "prompt_repair": repair["prompt"],
            "prompt_repair_chinese": repair["prompt_chinese"],
            "try_again_prompt_repair": repair["try_again_prompt"],
        }
        current = prompt_constants(
            (ROOT / "solution/prompt/prompts.py").read_text(encoding="utf-8")
        )
        self.assertEqual(current, expected)

        for direct, agent in (
            ("scripts/test_hack.py", "scripts/test_hack_agent.py"),
            ("scripts/test_debug.py", "scripts/test_debug_agent.py"),
        ):
            with self.subTest(agent=agent):
                direct_prompts = prompt_constants(upstream(direct).decode())
                agent_prompts = prompt_constants(upstream(agent).decode())
                self.assertEqual(agent_prompts["prompt"], direct_prompts["prompt"])
                self.assertEqual(agent_prompts["try_again_prompt"], direct_prompts["try_again_prompt"])

    def test_task_runners_do_not_import_a_concrete_solver(self):
        for path in TASK_SCRIPTS:
            with self.subTest(path=path):
                source = (ROOT / path).read_text(encoding="utf-8")
                modules = {
                    node.module
                    for node in ast.walk(ast.parse(source))
                    if isinstance(node, ast.ImportFrom) and node.module
                }
                self.assertIn("solution.api", modules)
                self.assertNotIn("solution.prompt", modules)
                self.assertNotIn("PromptSolver", source)
                self.assertNotIn("call_llm", source)
                self.assertFalse(prompt_constants(source))

    def test_diff_stays_inside_solver_boundary(self):
        changes = git("diff", "--no-renames", "--name-status", UPSTREAM).decode().splitlines()
        actual = {tuple(line.split("\t", 1)) for line in changes}
        allowed = {
            *(('M', path) for path in MODIFIED_UPSTREAM),
            *(('D', path) for path in REMOVED_UPSTREAM),
            *(('A', path) for path in ADDED),
        }
        self.assertEqual(actual, allowed)


if __name__ == "__main__":
    unittest.main()
