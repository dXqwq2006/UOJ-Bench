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
ADDED = {
    ".github/workflows/tests.yml",
    ".gitignore",
    "README_SOLVER.md",
    "docker/testcase_eval.Dockerfile",
    "requirements.txt",
    "scripts/run_hack_agent_batch.py",
    "scripts/run_testcase_eval_batch.py",
    "scripts/test_testcase_eval_task1.py",
    "scripts/test_testcase_eval_task2.py",
    "solution/llm/__init__.py",
    "solution/llm/call_llm.py",
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
    "tests/test_hack_batch.py",
    "tests/test_solver.py",
    "tests/test_testcase_eval.py",
    "tests/test_testcase_eval_benchmark.py",
    "tests/test_testcase_eval_lightcp.py",
    "tests/test_tasks.py",
    "tests/test_codecontests_plus.py",
    "tests/test_upstream_boundary.py",
    "tests/test_upstream_differential.py",
    "utils/benchmark.py",
    "utils/fault_coverage_benchmark.py",
    "utils/codecontests_plus.py",
    "utils/testcase_eval_benchmark.py",
    "utils/testcase_eval_executor.py",
    "utils/testcase_eval_lightcp.py",
    "scripts/run_codecontests_plus.py",
    "scripts/test_paper_hardtestgen.py",
    "solution/hardtestgen/__init__.py",
    "solution/hardtestgen/api.py",
    "solution/hardtestgen/lightcp.py",
    "solution/hardtestgen/pipeline.py",
    "solution/hardtestgen/prompt_templates/test_cases_kit_prompt_ig.toml",
    "solution/hardtestgen/prompt_templates/test_cases_kit_prompt_iv_and_ojf.toml",
    "solution/hardtestgen/prompts.py",
    "tests/test_hardtestgen.py",
    "utils/hardtestgen_benchmark.py",
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
