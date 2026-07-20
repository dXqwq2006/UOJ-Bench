import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from utils.testcase_eval_benchmark import RunStore
from utils.testcase_eval_executor import bind_judge_backend
from utils.testcase_eval_lightcp import (
    _backend_identity,
    _benchmark_stdin,
    _execute_one,
    _normalize_result,
    _program_request,
)


class TestCaseEvalLightCPTests(unittest.TestCase):
    def test_program_request_preserves_exact_language_profile(self):
        cpp = _program_request(
            "C++20 (GCC 13-64)",
            "#ifndef ONLINE_JUDGE\n#error local\n#endif\nint main(){}",
        )
        self.assertEqual(cpp["profile"], "testcase-eval")
        self.assertEqual(cpp["lang"], "C++20 (GCC 13-64)")
        self.assertEqual(cpp["sourceName"], "main.cpp")

        python2 = _program_request("Python 2", "print 1")
        self.assertEqual(python2["lang"], "Python 2")
        self.assertEqual(python2["sourceName"], "main.py")

    def test_java_request_uses_reference_class_rewrite(self):
        request = _program_request(
            "Java 21",
            (
                "public class Original { "
                "Original() {} "
                "static class Reader {} "
                "public static void main(String[] args) {}"
                " }"
            ),
        )

        class_name = request["sourceName"].removesuffix(".java")
        self.assertTrue(class_name.startswith("Tmp"))
        self.assertIn(f"public class {class_name}", request["code"])
        self.assertIn(f"{class_name}()", request["code"])
        self.assertIn("static class Reader", request["code"])

    def test_result_mapping_matches_benchmark_vocabulary(self):
        cases = {
            "exited": "success_run",
            "compile_error": "compilation_error",
            "Time Limit Exceeded": "time_limit_exceeded",
            "Memory Limit Exceeded": "memory_limit_exceeded",
            "Nonzero Exit Status": "runtime_error",
        }
        for status, expected in cases.items():
            with self.subTest(status=status):
                self.assertEqual(_normalize_result({"status": status}), expected)

        self.assertEqual(
            _normalize_result(
                {
                    "status": "Nonzero Exit Status",
                    "stderr": "std::bad_alloc",
                }
            ),
            "memory_limit_exceeded",
        )
        self.assertEqual(
            _normalize_result({"status": "Signalled", "signal": "killed"}),
            "memory_limit_exceeded",
        )

    def test_stdin_matches_reference_trailing_newline(self):
        self.assertEqual(_benchmark_stdin("1 2"), "1 2\n")
        self.assertEqual(_benchmark_stdin("1 2\n"), "1 2\n")
        self.assertEqual(_benchmark_stdin(""), "")

    def test_backend_identity_requires_service_fingerprint(self):
        fingerprint = "a" * 64
        self.assertEqual(
            _backend_identity(
                {"profiles": {"testcase-eval": {"fingerprint": fingerprint}}}
            ),
            f"lightcp:testcase-eval:{fingerprint}",
        )
        with self.assertRaisesRegex(RuntimeError, "fingerprint is unavailable"):
            _backend_identity({"profiles": {}})

    def test_transport_failure_aborts_without_an_execution_record(self):
        row = {
            "materialization_status": "complete",
            "dataset_name": "submission_lite",
            "checked_submission_id": "s1",
            "test_input": "1",
        }
        programs = {
            ("submission_lite", "s1"): {
                "cache_key": "program",
                "request": {},
            }
        }
        with patch(
            "utils.testcase_eval_lightcp._request_json",
            side_effect=OSError("service down"),
        ):
            with self.assertRaisesRegex(RuntimeError, "request failed"):
                _execute_one(
                    row,
                    programs,
                    "http://127.0.0.1:1",
                    {},
                    threading.Lock(),
                )

    def test_result_database_cannot_mix_judge_backends(self):
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "results.sqlite3"
            with RunStore(database):
                pass

            lightcp = f"lightcp:testcase-eval:{'a' * 64}"
            container = f"container:sha256:{'b' * 64}"
            bind_judge_backend(database, lightcp)
            bind_judge_backend(database, lightcp)
            connection = sqlite3.connect(database)
            encoded = connection.execute(
                "SELECT value_json FROM manifest WHERE key='judge_backend'"
            ).fetchone()[0]
            connection.close()
            self.assertEqual(json.loads(encoded), lightcp)
            with self.assertRaisesRegex(RuntimeError, "already bound"):
                bind_judge_backend(database, container)


if __name__ == "__main__":
    unittest.main()
