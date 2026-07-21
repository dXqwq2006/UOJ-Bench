from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from utils.fault_coverage_benchmark import GenerationJob, run_generation_jobs
from utils.testcase_eval_benchmark import RunStore


def generation_record(job: GenerationJob, signature: str) -> dict[str, object]:
    return {
        "policy": job.policy,
        "task": job.task,
        "problem_id": job.problem_id,
        "submission_id": job.submission_id,
        "generation_id": job.generation_id,
        "prompt": "prompt",
        "raw_text": "candidate",
        "candidate": "1\n",
        "candidate_format": "raw_input",
        "message": {
            "pipeline_identity": {"pipeline_signature_sha256": signature}
        },
        "usage": {},
        "status": "complete",
        "error": "",
    }


class FaultCoverageGenerationTests(unittest.TestCase):
    def test_result_database_rejects_mixed_pipeline_signatures(self) -> None:
        first = GenerationJob("bridge", 1, "p", "statement", "", "", "", 0, {})
        second = GenerationJob("bridge", 1, "p", "statement", "", "", "", 1, {})
        with tempfile.TemporaryDirectory() as directory:
            with RunStore(Path(directory) / "results.sqlite3") as store:
                with patch(
                    "utils.fault_coverage_benchmark._generate_one",
                    return_value=generation_record(first, "1" * 64),
                ):
                    counts = run_generation_jobs(
                        store, [first], model="gpt-5.6-sol", workers=1
                    )
                self.assertEqual(counts["complete"], 1)
                self.assertEqual(
                    store.manifest()["solver_pipeline_signature:bridge"],
                    "1" * 64,
                )

                with patch(
                    "utils.fault_coverage_benchmark._generate_one",
                    return_value=generation_record(second, "2" * 64),
                ):
                    with self.assertRaisesRegex(ValueError, "mixes pipeline identities"):
                        run_generation_jobs(
                            store, [second], model="gpt-5.6-sol", workers=1
                        )
                persisted = store.connection.execute(
                    "SELECT COUNT(*) FROM generations WHERE generation_id = 1"
                ).fetchone()[0]
                self.assertEqual(persisted, 0)


if __name__ == "__main__":
    unittest.main()
