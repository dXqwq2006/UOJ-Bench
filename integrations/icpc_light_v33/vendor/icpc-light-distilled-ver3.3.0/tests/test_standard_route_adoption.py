from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT_DIR = (
    Path(__file__).resolve().parents[1]
    / "skills/icpc-light-problem-builder/scripts"
)
sys.path.insert(0, str(SCRIPT_DIR))

import run_stage_agent as stage_runner  # noqa: E402
import verify_completion as completion  # noqa: E402
import verify_preclassification as preclassification_gate  # noqa: E402


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_text(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def confirmed_grade() -> dict[str, object]:
    return {
        "schema_version": 2,
        "preclassification": "P2-structured-bounded",
        "scam_status": "confirmed",
        "data_buildability": "D1-structured",
        "workflow_profile": "L1-ordinary",
        "decision": "continue",
        "provisional": False,
        "stop_reason": "none",
    }


def write_grade_report(root: Path, *, transition: str) -> None:
    if transition == "suspected":
        fields = (
            "preclassification: P3-adversarial-intensive",
            "scam_status: suspected",
            "data_buildability: D2-specialist",
            "workflow_profile: L2-high-risk",
            "decision: escalate",
            "provisional: true",
            "wrong_solution_min: 8",
            "wrong_solution_max: 10",
            "adversarial_round_mode: bounded-multi",
            "adversarial_round_min: 1",
            "adversarial_round_max: 3",
            "stop_reason: shortcut-unresolved",
        )
    elif transition == "stop":
        fields = (
            "preclassification: S-stop",
            "scam_status: none",
            "data_buildability: D3-stop",
            "workflow_profile: outside-light",
            "decision: stop",
            "provisional: false",
            "wrong_solution_min: 0",
            "wrong_solution_max: 0",
            "adversarial_round_mode: none",
            "adversarial_round_min: 0",
            "adversarial_round_max: 0",
            "stop_reason: unverifiable-contract",
        )
    else:  # pragma: no cover - test helper misuse
        raise AssertionError(f"unsupported transition fixture: {transition}")
    write_text(
        root,
        "audit/data-buildability.md",
        "\n".join(
            (
                "---",
                "schema_version: 2",
                "agent_model: gpt-5.6-sol",
                "agent_reasoning_effort: xhigh",
                *fields,
                "confidence: high",
                "risk_tags: []",
                "required_checks: []",
                "regrade_triggers: []",
                "---",
                "Artifact-backed transition evidence.",
            )
        ),
    )


class SelectedStandardRouteTests(unittest.TestCase):
    def assert_not_forwardable(self, transition: str) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            write_grade_report(root, transition=transition)
            with (
                mock.patch.object(
                    preclassification_gate.completion, "check_blind_stage"
                ),
                mock.patch.object(
                    preclassification_gate.completion,
                    "check_stage_execution_receipts",
                ),
                mock.patch.object(
                    preclassification_gate.completion, "check_run_state_policy"
                ),
                mock.patch.object(
                    preclassification_gate.completion,
                    "check_verified_claims",
                    return_value=[],
                ),
                mock.patch.object(
                    preclassification_gate.completion,
                    "check_selected_standard_route",
                ),
            ):
                audit_report, _ = preclassification_gate.build_report(
                    root, require_continuing=False
                )
                forward_report, _ = preclassification_gate.build_report(
                    root, require_continuing=True
                )
            self.assertEqual(audit_report.issues, [])
            self.assertTrue(forward_report.issues)
            self.assertEqual(
                stage_runner.HANDOFF_GATE_ARGUMENTS["solution-draft"],
                ("--require-continuing",),
            )

    def test_suspected_p3_escalation_cannot_enter_solution_draft(self) -> None:
        self.assert_not_forwardable("suspected")

    def test_s_stop_cannot_enter_solution_draft(self) -> None:
        self.assert_not_forwardable("stop")

    def test_completion_grade_accepts_confirmed_continuing_p2(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            write_text(
                root,
                "audit/data-buildability.md",
                "\n".join(
                    (
                        "---",
                        "schema_version: 2",
                        "agent_model: gpt-5.6-sol",
                        "agent_reasoning_effort: xhigh",
                        "preclassification: P2-structured-bounded",
                        "scam_status: confirmed",
                        "data_buildability: D1-structured",
                        "workflow_profile: L1-ordinary",
                        "decision: continue",
                        "confidence: high",
                        "provisional: false",
                        "wrong_solution_min: 5",
                        "wrong_solution_max: 8",
                        "adversarial_round_mode: single",
                        "adversarial_round_min: 1",
                        "adversarial_round_max: 1",
                        "stop_reason: none",
                        "risk_tags: []",
                        "required_checks: []",
                        "regrade_triggers: []",
                        "---",
                        "Independently proved executable simpler route evidence.",
                    )
                ),
            )
            report = completion.Report(root, "", False)
            grade = completion.check_grade(report)
            self.assertIsNotNone(grade)
            self.assertEqual(report.issues, [])

    def test_stage_contract_hashes_selected_route_through_hardening(self) -> None:
        selected = completion.SELECTED_STANDARD_ROUTE_REL
        self.assertIn(selected, stage_runner.STAGES["preclassification"].outputs)
        for stage in (
            "solution-draft",
            "std-materialization",
            "solution-validation",
            "build-hardening",
        ):
            self.assertIn(selected, stage_runner.STAGES[stage].inputs)

    def test_confirmed_simpler_route_may_replace_blind_source(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            blind = b"int main(){return 1;}\n"
            selected = b"int main(){return 0;}\n"
            write_text(
                root,
                completion.SELECTED_STANDARD_ROUTE_REL,
                selected.decode(),
            )
            report = completion.Report(root, "", False)
            result = completion.check_selected_standard_route(
                report,
                confirmed_grade(),
                [{"source_path": "blind/main.cpp", "source_sha256": digest(blind)}],
            )
            self.assertIsNotNone(result)
            self.assertEqual(report.issues, [])
            self.assertEqual(
                report.facts["selected_standard_route_kind"], "verified-simpler"
            )
            self.assertEqual(
                report.facts["selected_standard_route_sha256"], digest(selected)
            )

    def test_unconfirmed_selection_must_match_active_blind_source(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            selected = b"int main(){return 0;}\n"
            write_text(
                root,
                completion.SELECTED_STANDARD_ROUTE_REL,
                selected.decode(),
            )
            grade = confirmed_grade()
            grade["scam_status"] = "none"
            report = completion.Report(root, "", False)
            self.assertIsNone(
                completion.check_selected_standard_route(
                    report,
                    grade,
                    [
                        {
                            "source_path": "blind/main.cpp",
                            "source_sha256": digest(b"different\n"),
                        }
                    ],
                )
            )
            self.assertTrue(
                any("byte-identical" in issue for issue in report.issues)
            )

    def test_exact_copy_and_review_provenance_use_selected_route(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            blind = b"int main(){return 1;}\n"
            selected = b"int main(){return 0;}\n"
            blind_hash = digest(blind)
            selected_hash = digest(selected)
            selected_path = write_text(
                root,
                completion.SELECTED_STANDARD_ROUTE_REL,
                selected.decode(),
            )
            std = write_text(root, "package/std.cpp", selected.decode())
            write_text(
                root,
                "audit/solution-review-draft.md",
                "\n".join(
                    (
                        "---",
                        "schema_version: 1",
                        "agent_model: gpt-5.6-sol",
                        "agent_reasoning_effort: xhigh",
                        "review_status: passed",
                        "blind_source_path: blind/main.cpp",
                        f"blind_source_sha256: {blind_hash}",
                        "---",
                        "proof",
                    )
                ),
            )
            write_text(
                root,
                "audit/std-materialization.md",
                "\n".join(
                    (
                        "---",
                        "schema_version: 1",
                        "agent_model: gpt-5.6-sol",
                        "agent_reasoning_effort: xhigh",
                        "status: passed",
                        "materialization_mode: exact-copy",
                        "blind_source_path: blind/main.cpp",
                        f"blind_source_sha256: {blind_hash}",
                        "std_path: package/std.cpp",
                        f"std_sha256: {selected_hash}",
                        "---",
                    )
                ),
            )
            report = completion.Report(root, "", False)
            mode = completion.check_solution_draft_and_materialization(
                report,
                [{"source_path": "blind/main.cpp", "source_sha256": blind_hash}],
                selected_path,
            )
            self.assertEqual(mode, "exact-copy")
            self.assertEqual(report.issues, [])
            self.assertEqual(
                report.facts["std_provenance_path"],
                completion.SELECTED_STANDARD_ROUTE_REL,
            )

            write_text(
                root,
                "audit/solution-review.md",
                "\n".join(
                    (
                        "---",
                        "schema_version: 1",
                        "agent_model: gpt-5.6-sol",
                        "agent_reasoning_effort: xhigh",
                        "review_status: passed",
                        "std_compilation: passed",
                        "public_samples: pending-machine-regression",
                        "tiny_differential: pending-machine-regression",
                        "materialization_mode: exact-copy",
                        "materialization_delta_review: passed",
                        "std_path: package/std.cpp",
                        f"std_sha256: {digest(std.read_bytes())}",
                        f"std_provenance_path: {completion.SELECTED_STANDARD_ROUTE_REL}",
                        f"std_provenance_sha256: {selected_hash}",
                        "---",
                    )
                ),
            )
            reviewed = completion.check_solution_provenance(
                report, mode, selected_path
            )
            self.assertEqual(reviewed, std)
            self.assertEqual(report.issues, [])


if __name__ == "__main__":
    unittest.main()
