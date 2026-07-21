from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
UOJ_BENCH_ROOT = ROOT

from scripts.smoke_icpc_light_v33_bridge import DEFAULT_SKILL_BUNDLE, run_smoke


class EndToEndSmokeTests(unittest.TestCase):
    def test_supported_pipeline_wiring(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "smoke"
            clean_environment = {
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "PYTHONDONTWRITEBYTECODE": "1",
            }
            with patch.dict(os.environ, clean_environment, clear=True):
                report = run_smoke(
                    uoj_root=UOJ_BENCH_ROOT,
                    output_root=output,
                    skill_bundle=DEFAULT_SKILL_BUNDLE,
                    require_clean_checkout=False,
                )
            self.assertEqual(report["status"], "passed")
            self.assertTrue(report["generation"]["passed"])
            self.assertEqual(report["generation"]["case_count"], 3)
            self.assertEqual(report["generation"]["pipeline"]["lane_count"], 4)
            self.assertEqual(
                report["generation"]["pipeline"]["execution_mode"],
                "v3.3-test-override-blind-sweep",
            )
            self.assertTrue(report["hacking"]["passed"])
            self.assertEqual(report["hacking"]["semantically_exposed"], 2)
            self.assertEqual(len(report["hacking"]["pipeline_receipts"]), 2)
            self.assertTrue(report["fault_exposure"]["passed"])
            self.assertEqual(report["fault_exposure"]["candidate_format"], "raw_input")
            self.assertEqual(report["isolation"]["job_count"], 4)
            self.assertFalse(report["isolation"]["model_called"])
            self.assertFalse(report["isolation"]["uoj_called"])


if __name__ == "__main__":
    unittest.main()
