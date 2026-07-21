from __future__ import annotations

import hashlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


BUNDLE_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = BUNDLE_ROOT / "skills/icpc-light-problem-builder/scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import statement_resources as resources  # noqa: E402


def parse(text: str) -> resources.StatementResources:
    return resources.parse_statement_resources(
        text,
        statement_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


class StatementResourceParserTests(unittest.TestCase):
    def test_english_decimal_and_binary_units(self):
        observed = parse(
            "# Example\n\n**Time Limit:** 1.5 s\n**Memory Limit:** 1 GB\n"
        )
        self.assertEqual(observed.time_limit_ms, 1500)
        self.assertEqual(observed.memory_limit_mib, 1024)
        self.assertEqual(observed.time_evidence[0].line, 3)
        self.assertEqual(observed.memory_evidence[0].line, 4)

    def test_chinese_labels_can_share_one_line(self):
        observed = parse("# \u9898\u76ee\n\u65f6\u95f4\u9650\u5236\uff1a250 ms\uff1b\u5185\u5b58\u9650\u5236\uff1a512 MiB\n")
        self.assertEqual(observed.time_limit_ms, 250)
        self.assertEqual(observed.memory_limit_mib, 512)

    def test_codeforces_style_per_test_declarations(self):
        observed = parse(
            "time limit per test 2 seconds\n"
            "memory limit per test 512 megabytes\n"
        )
        self.assertEqual(observed.time_limit_ms, 2000)
        self.assertEqual(observed.memory_limit_mib, 512)

    def test_every_requested_unit_is_normalized(self):
        cases = (
            ("100 ms", "16 MB", 100, 16),
            ("2 s", "128 MiB", 2000, 128),
            ("0.1 s", "1 GB", 100, 1024),
            ("30000 ms", "2 GiB", 30000, 2048),
        )
        for time_text, memory_text, expected_time, expected_memory in cases:
            with self.subTest(time=time_text, memory=memory_text):
                observed = parse(
                    f"Time Limit: {time_text}\nMemory Limit: {memory_text}\n"
                )
                self.assertEqual(observed.time_limit_ms, expected_time)
                self.assertEqual(observed.memory_limit_mib, expected_memory)

    def test_equivalent_duplicate_declarations_are_allowed(self):
        observed = parse(
            "Time Limit: 1 s\n\u65f6\u95f4\u9650\u5236\uff1a1000 ms\n"
            "Memory Limit: 1 GB\n\u7a7a\u95f4\u9650\u5236\uff1a1024 MB\n"
        )
        self.assertEqual(len(observed.time_evidence), 2)
        self.assertEqual(len(observed.memory_evidence), 2)

    def test_public_payload_has_a_reproducible_self_digest(self):
        observed = parse("Time Limit: 1 s\nMemory Limit: 256 MiB\n")
        payload = observed.as_dict()
        self.assertEqual(payload["canonical_sha256"], observed.canonical_sha256())
        self.assertEqual(len(payload["canonical_sha256"]), 64)
        self.assertNotIn("canonical_sha256", observed.canonical_payload())

    def test_missing_or_unlabelled_limits_fail(self):
        cases = (
            "The intended solution runs in 2 s and uses 256 MB.\n",
            "Time Limit: 2 s\n",
            "Memory Limit: 256 MB\n",
        )
        for text in cases:
            with self.subTest(text=text):
                with self.assertRaises(resources.StatementResourceError):
                    parse(text)

    def test_invalid_units_and_fractional_canonical_values_fail(self):
        cases = (
            "Time Limit: 2 minutes\nMemory Limit: 256 MB\n",
            "Time Limit: 100.5 ms\nMemory Limit: 256 MB\n",
            "Time Limit: 1 s\nMemory Limit: 1.5 MB\n",
        )
        for text in cases:
            with self.subTest(text=text):
                with self.assertRaises(resources.StatementResourceError):
                    parse(text)

    def test_conflicting_values_fail(self):
        with self.assertRaisesRegex(
            resources.StatementResourceError, "conflicting time limit"
        ):
            parse(
                "Time Limit: 1 s\n\u65f6\u95f4\u9650\u5236\uff1a2 s\n"
                "Memory Limit: 256 MB\n"
            )
        with self.assertRaisesRegex(
            resources.StatementResourceError, "conflicting memory limit"
        ):
            parse(
                "Time Limit: 1 s\nMemory Limit: 256 MB\n"
                "\u5185\u5b58\u9650\u5236\uff1a1 GiB\n"
            )

    def test_supported_ranges_are_fail_closed(self):
        cases = (
            "Time Limit: 99 ms\nMemory Limit: 16 MB\n",
            "Time Limit: 30001 ms\nMemory Limit: 16 MB\n",
            "Time Limit: 100 ms\nMemory Limit: 15 MB\n",
            "Time Limit: 100 ms\nMemory Limit: 3 GiB\n",
        )
        for text in cases:
            with self.subTest(text=text):
                with self.assertRaisesRegex(
                    resources.StatementResourceError, "outside supported range"
                ):
                    parse(text)


class StatementResourceEntryPointTests(unittest.TestCase):
    def run_script(self, script: str, problem: Path, *extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT_DIR / script), "--problem-dir", str(problem), *extra],
            text=True,
            capture_output=True,
            check=False,
        )

    def test_verify_cli_reports_normalized_json(self):
        with tempfile.TemporaryDirectory() as raw:
            problem = Path(raw)
            (problem / "statement.md").write_text(
                "Time Limit: 2 s\nMemory Limit: 512 MiB\n", encoding="utf-8"
            )
            completed = self.run_script(
                "verify_statement_resources.py", problem, "--json"
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn('"time_limit_ms": 2000', completed.stdout)
            self.assertIn('"memory_limit_mib": 512', completed.stdout)

    def test_build_sweep_fails_before_creating_blind_artifacts(self):
        with tempfile.TemporaryDirectory() as raw:
            problem = Path(raw)
            (problem / "statement.md").write_text("# Missing limits\n", encoding="utf-8")
            completed = self.run_script(
                "build_sweep.py",
                problem,
                "--model",
                "gpt-5.6-sol",
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("statement resource preflight failed", completed.stderr)
            self.assertFalse((problem / "blind-solves").exists())

    def test_run_sweep_preflight_does_not_write_any_artifact(self):
        with tempfile.TemporaryDirectory() as raw:
            problem = Path(raw)
            (problem / "statement.md").write_text("# Missing limits\n", encoding="utf-8")
            plan = problem / "input-plan.json"
            manifest = problem / "input-manifest.json"
            plan.write_text("{}\n", encoding="utf-8")
            manifest.write_text("{}\n", encoding="utf-8")
            before = {
                path.relative_to(problem).as_posix(): path.read_bytes()
                for path in problem.iterdir()
                if path.is_file()
            }
            completed = self.run_script(
                "run_sweep.py",
                problem,
                "--plan",
                plan.name,
                "--public-manifest",
                manifest.name,
                "--solver-command",
                "false",
            )
            after = {
                path.relative_to(problem).as_posix(): path.read_bytes()
                for path in problem.iterdir()
                if path.is_file()
            }
            self.assertEqual(completed.returncode, 2)
            self.assertIn("statement resource preflight failed", completed.stderr)
            self.assertEqual(after, before)
            self.assertFalse((problem / "blind-solves").exists())

    def test_nonblind_stage_fails_before_creating_receipts(self):
        with tempfile.TemporaryDirectory() as raw:
            problem = Path(raw)
            (problem / "statement.md").write_text(
                "# Missing limits\n", encoding="utf-8"
            )
            completed = self.run_script(
                "run_stage_agent.py",
                problem,
                "--stage",
                "preclassification",
                "--run-id",
                "missing-resources",
                "--prompt-file",
                "missing-prompt.txt",
                "--model",
                "gpt-5.6-sol",
                "--test-command",
                "false",
            )
            self.assertEqual(completed.returncode, 2)
            self.assertIn("statement resource preflight failed", completed.stderr)
            self.assertFalse((problem / "audit").exists())


if __name__ == "__main__":
    unittest.main()
