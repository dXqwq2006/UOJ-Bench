from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_DIR = (
    Path(__file__).resolve().parents[1]
    / "skills/icpc-light-problem-builder/scripts"
)
sys.path.insert(0, str(SCRIPT_DIR))

import run_regression_gate as gate  # noqa: E402


class PackagePrivacyScanTests(unittest.TestCase):
    def make_package(self, root: Path) -> Path:
        package = root / "package"
        package.mkdir()
        (package / "std.cpp").write_text("int main(){}\n", encoding="utf-8")
        tests = package / "tests"
        tests.mkdir()
        (tests / "01.in").write_text("1\n", encoding="utf-8")
        return package

    def test_clean_package_passes_with_explicit_scope(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            self.make_package(root)
            result = gate.package_privacy_scan(root)
            self.assertEqual(result["status"], "passed")
            self.assertEqual(
                result["scan_scope"],
                "package-boundary-and-common-secret-leak-v1",
            )
            self.assertEqual(result["content_findings"], [])

    def test_common_secret_and_operator_path_are_reported_without_value(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            package = self.make_package(root)
            (package / "helper.py").write_text(
                "endpoint = '/Users/example/private/repo'\n"
                "credential = 'sk-abcdefghijklmnopqrstuvwxyz123456'\n",
                encoding="utf-8",
            )
            result = gate.package_privacy_scan(root)
            self.assertEqual(result["status"], "failed")
            findings = {item["finding"] for item in result["content_findings"]}
            self.assertIn("operator-local absolute path", findings)
            self.assertIn("OpenAI-style API key", findings)
            self.assertNotIn("abcdefghijklmnopqrstuvwxyz123456", repr(result))

    def test_development_and_temporary_files_fail(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            package = self.make_package(root)
            (package / "debug.log").write_text("trace\n", encoding="utf-8")
            result = gate.package_privacy_scan(root)
            self.assertEqual(result["status"], "failed")
            self.assertTrue(
                any("temporary/debug" in item for item in result["forbidden_entries"])
            )


if __name__ == "__main__":
    unittest.main()
