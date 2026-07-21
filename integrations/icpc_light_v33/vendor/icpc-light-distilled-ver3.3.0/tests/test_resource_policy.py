from __future__ import annotations

import argparse
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/icpc-light-problem-builder/scripts"
sys.path.insert(0, str(SCRIPTS))

import build_resource_policy  # noqa: E402
import run_regression_gate  # noqa: E402
from statement_resources import load_statement_resources  # noqa: E402


class ResourcePolicyTests(unittest.TestCase):
    def test_builder_matches_regression_gate_contract(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            problem = Path(raw)
            (problem / "statement.md").write_text(
                "Time limit: 3.5 seconds\nMemory limit: 512 MiB\n",
                encoding="utf-8",
            )
            args = argparse.Namespace(
                problem_dir=problem,
                intended_complexity="O(n log n)",
                maximum_scale="n = 200000",
                time_limit_rationale="Measured full-scale std with margin.",
                memory_limit_rationale="Peak arrays remain below 512 MiB.",
            )
            policy = build_resource_policy.build_policy(args)
            parsed = run_regression_gate.load_resource_policy(
                {"resource_policy": policy}, load_statement_resources(problem)
            )
            self.assertEqual(parsed.time_limit_ms, 3500)
            self.assertEqual(parsed.memory_limit_mib, 512)
            self.assertEqual(parsed.policy_sha256, policy["policy_sha256"])

    def test_builder_does_not_accept_blank_design_basis(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            problem = Path(raw)
            (problem / "statement.md").write_text(
                "时间限制：1 秒\n内存限制：256 MB\n", encoding="utf-8"
            )
            args = argparse.Namespace(
                problem_dir=problem,
                intended_complexity=" ",
                maximum_scale="n = 1000",
                time_limit_rationale="measured",
                memory_limit_rationale="bounded",
            )
            with self.assertRaises(ValueError):
                build_resource_policy.build_policy(args)


if __name__ == "__main__":
    unittest.main()
