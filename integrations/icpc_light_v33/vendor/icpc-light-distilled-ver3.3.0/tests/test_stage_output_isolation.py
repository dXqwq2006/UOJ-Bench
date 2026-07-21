from __future__ import annotations

import json
import shlex
import sys
import tempfile
import unittest
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from unittest import mock


SCRIPT_DIR = (
    Path(__file__).resolve().parents[1]
    / "skills/icpc-light-problem-builder/scripts"
)
sys.path.insert(0, str(SCRIPT_DIR))

import run_stage_agent as stage_runner  # noqa: E402
import verify_blind_stage as blind_gate  # noqa: E402
import verify_completion as completion  # noqa: E402
import verify_completion_handoff as completion_handoff  # noqa: E402
import verify_readiness as readiness  # noqa: E402


class CodexJsonlTerminalSemanticsTests(unittest.TestCase):
    @staticmethod
    def write_trace(path: Path, events: tuple[dict[str, object], ...]) -> None:
        path.write_text(
            "".join(json.dumps(event) + "\n" for event in events),
            encoding="utf-8",
        )

    def test_recoverable_api_errors_pass_when_final_event_completes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            trace = root / "codex.jsonl"
            self.write_trace(
                trace,
                (
                    {"type": "thread.started"},
                    {"type": "turn.started"},
                    {"type": "error", "message": "HTTP 429; retrying"},
                    {"type": "error", "message": "HTTP 503; retrying"},
                    {"type": "turn.completed"},
                ),
            )

            validation = stage_runner.validate_codex_jsonl(trace)
            self.assertEqual(validation["status"], "passed")
            self.assertEqual(validation["recoverable_error_event_count"], 2)
            self.assertEqual(validation["failure_event_count"], 0)

            gate = blind_gate.Gate(root)
            self.assertTrue(
                blind_gate.validate_completed_codex_jsonl(gate, trace, "trace")
            )
            self.assertEqual(gate.issues, [])

    def test_terminal_failure_remains_failed_after_recoverable_error(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            trace = root / "codex.jsonl"
            self.write_trace(
                trace,
                (
                    {"type": "thread.started"},
                    {"type": "turn.started"},
                    {"type": "error", "message": "HTTP 503; retrying"},
                    {"type": "turn.failed"},
                ),
            )

            validation = stage_runner.validate_codex_jsonl(trace)
            self.assertEqual(validation["status"], "failed")
            self.assertEqual(validation["recoverable_error_event_count"], 1)
            self.assertEqual(validation["failure_event_count"], 1)
            self.assertEqual(validation["terminal_type"], "turn.failed")

            gate = blind_gate.Gate(root)
            self.assertFalse(
                blind_gate.validate_completed_codex_jsonl(gate, trace, "trace")
            )
            self.assertTrue(any("failure event" in issue for issue in gate.issues))


class OptionalAcceptedTreeIsolationTests(unittest.TestCase):
    TREE = "audit/private/accepted-solutions"

    def _write_tree_receipt(
        self,
        root: Path,
        *,
        stage_files: dict[str, str] | None = None,
        preexisting_files: dict[str, str] | None = None,
        optional: bool = True,
    ) -> tuple[str, stage_runner.StageContract, dict[str, object]]:
        stage = "optional-tree-summary"
        run_id = "fixture"
        output_rel = "audit/result.md"
        output_path = root / output_rel
        output_before = stage_runner.file_state(output_path, output_rel)
        stage_files = {} if stage_files is None else stage_files
        preexisting_files = {} if preexisting_files is None else preexisting_files

        for relative, contents in preexisting_files.items():
            target = root / self.TREE / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(contents, encoding="utf-8")
        preexisting_tree = stage_runner.snapshot_tree(root, self.TREE)

        attempt_rel = (stage_runner.RECEIPT_ROOT / stage / run_id).as_posix()
        attempt = root / attempt_rel
        attempt.mkdir(parents=True)
        archive, archive_error = stage_runner.archive_preexisting_outputs(
            root,
            PurePosixPath(attempt_rel),
            [PurePosixPath(output_rel)],
            [PurePosixPath(self.TREE)],
            [output_before],
            [preexisting_tree],
        )
        self.assertIsNone(archive_error)
        tree_before = stage_runner.snapshot_tree(root, self.TREE)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("result\n", encoding="utf-8")
        output_after = stage_runner.file_state(output_path, output_rel)
        for relative, contents in stage_files.items():
            target = root / self.TREE / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(contents, encoding="utf-8")
        tree_after = stage_runner.snapshot_tree(root, self.TREE)

        # Model the trusted evaluator re-materializing its frozen review files.
        # Equal stage-owned files are left in place; different same-path states
        # are deliberately left conflicting for fail-closed tests.
        for relative, contents in preexisting_files.items():
            target = root / self.TREE / relative
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(contents, encoding="utf-8")

        prompt_rel = "audit/private/stage-prompts/optional-tree-summary.md"
        prompt = root / prompt_rel
        prompt.parent.mkdir(parents=True, exist_ok=True)
        prompt.write_text("produce the result\n", encoding="utf-8")
        stdout_rel = f"{attempt_rel}/codex-exec.jsonl"
        stdout = root / stdout_rel
        stdout.write_text(
            '{"type":"thread.started"}\n'
            '{"type":"turn.started"}\n'
            '{"type":"turn.completed"}\n',
            encoding="utf-8",
        )
        stderr_rel = f"{attempt_rel}/stderr.log"
        stderr = root / stderr_rel
        stderr.write_text("", encoding="utf-8")

        receipt: dict[str, object] = {
            "schema_version": 1,
            "runner": "icpc-light-stage-agent-runner",
            "stage": stage,
            "run_id": run_id,
            "execution_mode": "production-codex",
            "model": stage_runner.REQUIRED_MODEL,
            "reasoning_effort": stage_runner.REQUIRED_REASONING_EFFORT,
            "exit_code": 0,
            "spawn_error": None,
            "interrupted": False,
            "success": True,
            "prompt_unchanged": True,
            "inputs_unchanged": True,
            "outputs_materially_updated": True,
            "output_trees_materially_updated": True,
            "codex_jsonl_required": True,
            "command": ["codex", "exec"],
            "started_at_utc": "2026-01-01T00:00:00Z",
            "finished_at_utc": "2026-01-01T00:00:01Z",
            "prompt": stage_runner.file_state(prompt, prompt_rel),
            "stdout_log": stage_runner.file_state(stdout, stdout_rel),
            "stderr_log": stage_runner.file_state(stderr, stderr_rel),
            "codex_jsonl_validation": stage_runner.validate_codex_jsonl(stdout),
            "inputs": [],
            "outputs": [output_after],
            "outputs_before": [output_before],
            "output_changes": [{
                "path": output_rel,
                "before": output_before,
                "after": output_after,
                "materially_changed": True,
            }],
            "output_trees": [tree_after],
            "output_trees_before": [tree_before],
            "output_tree_changes": [{
                "path": self.TREE,
                "before": tree_before,
                "after": tree_after,
                "materially_changed": stage_runner.material_change(
                    tree_before, tree_after
                ),
            }],
            "preexisting_outputs": [output_before],
            "preexisting_output_trees": [preexisting_tree],
            "preexisting_archive": archive,
            "blind_prerequisite_gate": None,
            "handoff_prerequisite_gate": None,
            "prior_stage_receipt": None,
        }
        serialized = json.dumps(receipt, sort_keys=True) + "\n"
        (attempt / "receipt.json").write_text(serialized, encoding="utf-8")
        current = root / stage_runner.RECEIPT_ROOT / stage / "current.json"
        current.write_text(serialized, encoding="utf-8")

        contract = stage_runner.StageContract(
            inputs=(),
            outputs=(output_rel,),
            output_trees=() if optional else (self.TREE,),
            optional_output_trees=(self.TREE,) if optional else (),
        )
        return stage, contract, receipt

    @staticmethod
    def _validate_tree_receipt(
        root: Path, stage: str, contract: stage_runner.StageContract
    ) -> dict[str, object]:
        with (
            mock.patch.dict(stage_runner.STAGES, {stage: contract}),
            mock.patch.object(
                stage_runner, "exact_production_command", return_value=True
            ),
        ):
            return stage_runner.require_prior_stage_receipt(root, stage)

    def _require_receipt_with_optional_tree(
        self, root: Path, *, present: bool
    ) -> dict[str, object]:
        stage, contract, _ = self._write_tree_receipt(
            root,
            stage_files={"A01.cpp": "int main(){}\n"} if present else {},
        )
        return self._validate_tree_receipt(root, stage, contract)

    def test_build_stage_watches_optional_accepted_tree(self) -> None:
        contract = stage_runner.STAGES["build-hardening"]
        self.assertIn(
            self.TREE,
            contract.optional_output_trees,
        )
        self.assertIn("package/checker.cpp", contract.optional_outputs)
        for tree in (
            "package/samples",
            "audit/adversarial-round-plans",
            "audit/adversarial-round-receipts",
        ):
            self.assertIn(tree, contract.output_trees)

    def test_optional_tree_may_be_absent_or_nonempty(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            missing = stage_runner.snapshot_tree(root, self.TREE)
            self.assertEqual(missing["status"], "missing-or-unsafe")
            source = root / self.TREE / "A01.cpp"
            source.parent.mkdir(parents=True)
            source.write_text("int main(){}\n", encoding="utf-8")
            present = stage_runner.snapshot_tree(root, self.TREE)
            self.assertEqual(present["status"], "present")
            self.assertEqual([item["path"] for item in present["files"]], [self.TREE + "/A01.cpp"])

    def test_present_optional_tree_does_not_replace_receipt_summary_path(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            summary = self._require_receipt_with_optional_tree(root, present=True)
            receipt = (
                root
                / "audit/private/stage-executions/optional-tree-summary/current.json"
            )
            self.assertEqual(
                summary,
                {
                    "stage": "optional-tree-summary",
                    "path": (
                        "audit/private/stage-executions/"
                        "optional-tree-summary/current.json"
                    ),
                    "sha256": stage_runner.sha256_file(receipt),
                },
            )

    def test_absent_optional_tree_does_not_replace_receipt_summary_path(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            summary = self._require_receipt_with_optional_tree(root, present=False)
            receipt = (
                root
                / "audit/private/stage-executions/optional-tree-summary/current.json"
            )
            self.assertEqual(
                summary,
                {
                    "stage": "optional-tree-summary",
                    "path": (
                        "audit/private/stage-executions/"
                        "optional-tree-summary/current.json"
                    ),
                    "sha256": stage_runner.sha256_file(receipt),
                },
            )

    def test_cf574_restored_preexisting_review_tree_is_valid(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            stage, contract, _ = self._write_tree_receipt(
                root,
                preexisting_files={
                    "benchmark-review.cpp": "int main() { return 0; }\n",
                    "benchmark-review.json": '{"status":"accepted"}\n',
                },
            )
            summary = self._validate_tree_receipt(root, stage, contract)
            self.assertEqual(summary["stage"], stage)
            self.assertEqual(
                [
                    item["path"]
                    for item in stage_runner.snapshot_tree(root, self.TREE)["files"]
                ],
                [
                    f"{self.TREE}/benchmark-review.cpp",
                    f"{self.TREE}/benchmark-review.json",
                ],
            )

    def test_disjoint_and_identical_optional_tree_union_states_are_valid(self) -> None:
        cases = (
            (
                {"stage-owned.cpp": "int stage_owned;\n"},
                {"benchmark-review.cpp": "int benchmark;\n"},
            ),
            (
                {"shared.cpp": "int exact;\n"},
                {"shared.cpp": "int exact;\n"},
            ),
        )
        for stage_files, preexisting_files in cases:
            with self.subTest(stage_files=stage_files):
                with tempfile.TemporaryDirectory() as raw:
                    root = Path(raw).resolve()
                    stage, contract, _ = self._write_tree_receipt(
                        root,
                        stage_files=stage_files,
                        preexisting_files=preexisting_files,
                    )
                    self._validate_tree_receipt(root, stage, contract)

    def test_optional_tree_union_rejects_conflicting_same_path(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            stage, contract, _ = self._write_tree_receipt(
                root,
                stage_files={"shared.cpp": "int stage_owned;\n"},
                preexisting_files={"shared.cpp": "int benchmark;\n"},
            )
            with self.assertRaisesRegex(
                stage_runner.ContractError, "conflicting file states"
            ):
                self._validate_tree_receipt(root, stage, contract)

    def test_optional_tree_union_rejects_file_descendant_conflict(self) -> None:
        tree = self.TREE
        stage_owned = {
            "path": tree,
            "status": "present",
            "files": [{
                "path": f"{tree}/collision",
                "status": "present-nonempty",
                "size": 1,
                "sha256": "a" * 64,
            }],
            "unsafe_entries": [],
        }
        preexisting = {
            "path": tree,
            "status": "present",
            "files": [{
                "path": f"{tree}/collision/descendant.cpp",
                "status": "present-nonempty",
                "size": 1,
                "sha256": "b" * 64,
            }],
            "unsafe_entries": [],
        }
        with self.assertRaisesRegex(
            stage_runner.ContractError, "file/descendant conflict"
        ):
            stage_runner.optional_tree_union_snapshot(
                stage_owned, preexisting, tree_path=tree
            )

    def test_optional_tree_union_rejects_unknown_or_changed_current_file(self) -> None:
        mutations = {
            "unknown": ("unknown.cpp", "int unknown;\n"),
            "changed": ("benchmark-review.cpp", "int tampered;\n"),
        }
        for name, (relative, contents) in mutations.items():
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as raw:
                    root = Path(raw).resolve()
                    stage, contract, _ = self._write_tree_receipt(
                        root,
                        preexisting_files={
                            "benchmark-review.cpp": "int benchmark;\n"
                        },
                    )
                    target = root / self.TREE / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(contents, encoding="utf-8")
                    with self.assertRaisesRegex(
                        stage_runner.ContractError, "not the exact stage/preexisting union"
                    ):
                        self._validate_tree_receipt(root, stage, contract)

    def test_optional_tree_union_rejects_missing_stage_owned_file(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            stage, contract, _ = self._write_tree_receipt(
                root,
                stage_files={"stage-owned.cpp": "int stage_owned;\n"},
            )
            (root / self.TREE / "stage-owned.cpp").unlink()
            with self.assertRaisesRegex(
                stage_runner.ContractError, "not the exact stage/preexisting union"
            ):
                self._validate_tree_receipt(root, stage, contract)

    def test_optional_tree_union_rejects_tampered_archive(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            stage, contract, receipt = self._write_tree_receipt(
                root,
                preexisting_files={"benchmark-review.cpp": "int benchmark;\n"},
            )
            archive = receipt["preexisting_archive"]
            archived_tree = archive["trees"][0]
            archived = root / archived_tree["archive_path"] / "benchmark-review.cpp"
            archived.write_text("int archive_tampered;\n", encoding="utf-8")
            with self.assertRaisesRegex(
                stage_runner.ContractError, "archived output tree changed"
            ):
                self._validate_tree_receipt(root, stage, contract)

    def test_optional_tree_union_rejects_missing_archive(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            stage, contract, receipt = self._write_tree_receipt(
                root,
                preexisting_files={"benchmark-review.cpp": "int benchmark;\n"},
            )
            archived_tree = receipt["preexisting_archive"]["trees"][0]
            archived = root / archived_tree["archive_path"] / "benchmark-review.cpp"
            archived.unlink()
            with self.assertRaisesRegex(
                stage_runner.ContractError, "archived output tree changed"
            ):
                self._validate_tree_receipt(root, stage, contract)

    def test_required_tree_does_not_accept_preexisting_union(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            stage, contract, _ = self._write_tree_receipt(
                root,
                stage_files={"stage-owned.cpp": "int stage_owned;\n"},
                preexisting_files={"benchmark-review.cpp": "int benchmark;\n"},
                optional=False,
            )
            with self.assertRaisesRegex(
                stage_runner.ContractError, "current output tree changed"
            ):
                self._validate_tree_receipt(root, stage, contract)

    def test_safely_absent_optional_tree_rejects_file_or_symlink_root(self) -> None:
        for root_kind in ("file", "symlink"):
            with self.subTest(root_kind=root_kind):
                with tempfile.TemporaryDirectory() as raw:
                    root = Path(raw).resolve()
                    stage, contract, _ = self._write_tree_receipt(root)
                    tree_root = root / self.TREE
                    if root_kind == "file":
                        tree_root.write_text("not a directory\n", encoding="utf-8")
                    else:
                        target = root / "symlink-target"
                        target.mkdir()
                        tree_root.symlink_to(target, target_is_directory=True)
                    with self.assertRaises(stage_runner.ContractError):
                        self._validate_tree_receipt(root, stage, contract)

    def test_completion_duplicate_tree_path_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            stage, contract, receipt = self._write_tree_receipt(
                root,
                stage_files={"stage-owned.cpp": "int stage_owned;\n"},
            )
            receipt["output_trees"] = [
                *receipt["output_trees"],
                dict(receipt["output_trees"][0]),
            ]
            current_rel = (
                stage_runner.RECEIPT_ROOT / stage / "current.json"
            ).as_posix()
            current = root / current_rel
            current.write_text(
                json.dumps(receipt, sort_keys=True) + "\n", encoding="utf-8"
            )
            recursive_summary = {
                "stage": stage,
                "path": current_rel,
                "sha256": stage_runner.sha256_file(current),
            }
            report = completion.Report(root, "audit/completion-gate.json", False)
            with (
                mock.patch.dict(stage_runner.STAGES, {stage: contract}),
                mock.patch.object(
                    stage_runner,
                    "require_prior_stage_receipt",
                    return_value=recursive_summary,
                ),
                mock.patch.object(
                    completion, "exact_stage_command", return_value=True
                ),
            ):
                completion.check_stage_execution_receipts(report, (stage,))
            self.assertTrue(
                any("duplicates output tree" in issue for issue in report.issues),
                report.issues,
            )

    def test_completion_accepts_exact_optional_tree_union(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            stage, contract, receipt = self._write_tree_receipt(
                root,
                stage_files={"stage-owned.cpp": "int stage_owned;\n"},
                preexisting_files={"benchmark-review.cpp": "int benchmark;\n"},
            )
            current_rel = (
                stage_runner.RECEIPT_ROOT / stage / "current.json"
            ).as_posix()
            current = root / current_rel
            recursive_summary = {
                "stage": stage,
                "path": current_rel,
                "sha256": stage_runner.sha256_file(current),
            }
            report = completion.Report(root, "audit/completion-gate.json", False)
            with (
                mock.patch.dict(stage_runner.STAGES, {stage: contract}),
                mock.patch.object(
                    stage_runner,
                    "require_prior_stage_receipt",
                    return_value=recursive_summary,
                ),
                mock.patch.object(
                    completion, "exact_stage_command", return_value=True
                ),
            ):
                completion.check_stage_execution_receipts(report, (stage,))
            self.assertEqual(report.issues, [])

    def test_preexisting_optional_tree_is_archived_before_retry(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            source = root / self.TREE / "stale.cpp"
            source.parent.mkdir(parents=True)
            source.write_text("int stale;\n", encoding="utf-8")
            before = stage_runner.snapshot_tree(root, self.TREE)
            records, error = stage_runner.archive_preexisting_outputs(
                root,
                PurePosixPath(
                    "audit/private/stage-executions/build-hardening/retry-01"
                ),
                [],
                [PurePosixPath(self.TREE)],
                [],
                [before],
            )
            self.assertIsNone(error)
            self.assertFalse((root / self.TREE).exists())
            archive = root / records["trees"][0]["archive_path"]
            self.assertEqual((archive / "stale.cpp").read_text(), "int stale;\n")

    def test_hidden_archived_entry_is_hash_bound(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            hidden = root / self.TREE / ".stale.cpp"
            hidden.parent.mkdir(parents=True)
            hidden.write_text("int hidden_v1;\n", encoding="utf-8")
            before = stage_runner.snapshot_tree(root, self.TREE)
            self.assertEqual(before["status"], "unsafe-entry")
            self.assertEqual(before["files"][0]["sha256"], stage_runner.sha256_file(hidden))
            records, error = stage_runner.archive_preexisting_outputs(
                root,
                PurePosixPath(
                    "audit/private/stage-executions/build-hardening/retry-hidden"
                ),
                [],
                [PurePosixPath(self.TREE)],
                [],
                [before],
            )
            self.assertIsNone(error)
            archived_snapshot = records["trees"][0]["archive_snapshot"]
            archived_hidden = root / records["trees"][0]["archive_path"] / ".stale.cpp"
            archived_hidden.write_text("int hidden_v2;\n", encoding="utf-8")
            current = stage_runner.snapshot_tree(
                root, records["trees"][0]["archive_path"]
            )
            self.assertNotEqual(
                stage_runner.tree_content_signature(archived_snapshot),
                stage_runner.tree_content_signature(current),
            )

    def test_preexisting_optional_checker_is_archived_before_retry(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            checker = root / "package/checker.cpp"
            checker.parent.mkdir(parents=True)
            checker.write_text("int stale_checker;\n", encoding="utf-8")
            relative = PurePosixPath("package/checker.cpp")
            before = stage_runner.file_state(checker, relative.as_posix())
            records, error = stage_runner.archive_preexisting_outputs(
                root,
                PurePosixPath(
                    "audit/private/stage-executions/build-hardening/retry-02"
                ),
                [relative],
                [],
                [before],
                [],
            )
            self.assertIsNone(error)
            self.assertFalse(checker.exists())
            archive = root / records["files"][0]["archive_path"]
            self.assertEqual(archive.read_text(), "int stale_checker;\n")

    def test_hidden_entry_fails_stage_and_completion_tree_scans(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            tree = root / self.TREE
            tree.mkdir(parents=True)
            (tree / "visible.cpp").write_text("int visible;\n", encoding="utf-8")
            (tree / ".hidden.cpp").write_text("int hidden;\n", encoding="utf-8")
            self.assertEqual(
                stage_runner.snapshot_tree(root, self.TREE)["status"],
                "unsafe-entry",
            )
            report = completion.Report(root, "audit/completion-gate.json", False)
            check = report.new_check("hidden-tree-test")
            completion.tree_files(report, self.TREE, check)
            self.assertTrue(
                any("hidden entries are forbidden" in issue for issue in report.issues)
            )
            readiness_gate = readiness.Gate(root)
            readiness_check = readiness_gate.new_check("hidden-tree-freeze-test")
            readiness.current_tree_files(root, self.TREE, readiness_check)
            self.assertTrue(
                any("hidden entry" in issue for issue in readiness_gate.issues)
            )

    def test_completion_handoff_invokes_canonical_replay(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            fake_payload = {
                "schema_version": 1,
                "status": "pass",
                "receipt_sha256": "a" * 64,
            }
            completed = SimpleNamespace(
                returncode=0,
                stdout=json.dumps(fake_payload),
                stderr="",
            )
            with mock.patch.object(
                completion_handoff.subprocess, "run", return_value=completed
            ) as run:
                evidence = completion_handoff.replay_completion(root)
            command = run.call_args.args[0]
            self.assertEqual(command[0], sys.executable)
            self.assertEqual(Path(command[1]).name, "verify_completion.py")
            self.assertEqual(command[-1], "--json")
            self.assertEqual(evidence["reported_status"], "pass")
            self.assertEqual(evidence["reported_issues"], [])
            self.assertEqual(evidence["receipt_sha256"], "a" * 64)
            self.assertFalse(evidence["timed_out"])

    def test_completion_handoff_retains_bounded_verifier_issues(self) -> None:
        payload = {
            "status": "fail",
            "issues": [
                "stage-execution-receipts: recursive receipt summary is inconsistent",
                "x" * 5000,
            ],
        }
        completed = SimpleNamespace(
            returncode=1,
            stdout=json.dumps(payload),
            stderr="",
        )
        with mock.patch.object(
            completion_handoff.subprocess, "run", return_value=completed
        ):
            replay = completion_handoff.replay_completion(Path(".").resolve())
        self.assertEqual(replay["reported_issues"][0], payload["issues"][0])
        self.assertEqual(
            len(replay["reported_issues"][1]),
            completion_handoff.MAX_REPORTED_ISSUE_CHARACTERS,
        )

    def test_stage_handoff_binds_refreshed_completion_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            receipt = root / "audit/completion-gate.json"
            receipt.parent.mkdir(parents=True)
            receipt.write_text('{"verdict":"passed"}\n', encoding="utf-8")
            receipt_sha256 = stage_runner.sha256_file(receipt)
            completion_verifier = SCRIPT_DIR / "verify_completion.py"
            replay = {
                "command": [
                    sys.executable,
                    "verify_completion.py",
                    "--problem-dir",
                    ".",
                    "--json",
                ],
                "verifier_sha256": stage_runner.sha256_file(completion_verifier),
                "timeout_seconds": 4 * 60 * 60,
                "timed_out": False,
                "exit_code": 0,
                "reported_status": "pass",
                "receipt_sha256": receipt_sha256,
                "stdout_sha256": "a" * 64,
                "stderr_sha256": "b" * 64,
            }
            payload = {
                "status": "pass",
                "completion_replay": replay,
            }
            completed = SimpleNamespace(
                returncode=0,
                stdout=json.dumps(payload),
                stderr="",
            )
            with mock.patch.object(
                stage_runner.subprocess, "run", return_value=completed
            ):
                evidence = stage_runner.run_handoff_gate(
                    root, "verify_completion_handoff.py"
                )
            stage_runner._validate_gate_evidence(
                root,
                evidence,
                verifier_name="verify_completion_handoff.py",
                label="completion handoff test",
                json_flag=True,
            )
            receipt.write_text('{"verdict":"forged"}\n', encoding="utf-8")
            with self.assertRaisesRegex(
                stage_runner.ContractError, "receipt hash is stale"
            ):
                stage_runner._validate_gate_evidence(
                    root,
                    evidence,
                    verifier_name="verify_completion_handoff.py",
                    label="completion handoff test",
                    json_flag=True,
                )

            completed = SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"status": "pass"}),
                stderr="",
            )
            extra_args = stage_runner.HANDOFF_GATE_ARGUMENTS["solution-draft"]
            with mock.patch.object(
                stage_runner.subprocess, "run", return_value=completed
            ):
                evidence = stage_runner.run_handoff_gate(
                    root,
                    "verify_preclassification.py",
                    extra_args,
                )
            self.assertIn("--require-continuing", evidence["command"])
            stage_runner._validate_gate_evidence(
                root,
                evidence,
                verifier_name="verify_preclassification.py",
                label="solution-draft handoff test",
                json_flag=True,
                extra_args=extra_args,
            )

    def test_readiness_freezes_inputs_after_completion_handoff_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            (root / "statement.md").write_text(
                "Time Limit: 2 s\nMemory Limit: 256 MiB\n", encoding="utf-8"
            )
            audit = root / "audit"
            audit.mkdir()
            (audit / "completion-gate.json").write_text(
                '{"generation":0}\n', encoding="utf-8"
            )
            (audit / "regression.md").write_text("regression\n", encoding="utf-8")
            (audit / "regression-machine.json").write_text(
                '{"generation":0}\n', encoding="utf-8"
            )
            prompt = root / "prompt.md"
            prompt.write_text("write readiness\n", encoding="utf-8")
            args = SimpleNamespace(
                problem_dir=root,
                stage="readiness",
                run_id="refresh-order",
                prompt_rel=PurePosixPath("prompt.md"),
                extra_inputs=[],
                extra_outputs=[],
                extra_trees=[],
                test_command=None,
            )

            def refresh(
                _root: Path,
                _script_name: str,
                _extra_args: tuple[str, ...] = (),
            ):
                (audit / "completion-gate.json").write_text(
                    '{"generation":1}\n', encoding="utf-8"
                )
                (audit / "regression-machine.json").write_text(
                    '{"generation":1}\n', encoding="utf-8"
                )
                return {"exit_code": 0, "reported_status": "pass"}

            class CompletedProcess:
                returncode = 0

                @staticmethod
                def wait():
                    return 0

            def spawn(_command, *, cwd, stdin, stdout, stderr, **_kwargs):
                del cwd, stdin, stderr
                (audit / "readiness.md").write_text("ready\n", encoding="utf-8")
                stdout.write(
                    b'{"type":"thread.started"}\n'
                    b'{"type":"turn.started"}\n'
                    b'{"type":"turn.completed"}\n'
                )
                stdout.flush()
                return CompletedProcess()

            with (
                mock.patch.object(
                    stage_runner,
                    "require_prior_stage_receipt",
                    return_value={"stage": "build-hardening"},
                ),
                mock.patch.object(
                    stage_runner, "run_handoff_gate", side_effect=refresh
                ),
                mock.patch.object(stage_runner.subprocess, "Popen", side_effect=spawn),
            ):
                code, _result = stage_runner.execute(args)
            self.assertEqual(code, 0)
            receipt = json.loads(
                (
                    root
                    / "audit/private/stage-executions/readiness/refresh-order/receipt.json"
                ).read_text(encoding="utf-8")
            )
            self.assertTrue(receipt["inputs_unchanged"])
            completion_state = next(
                item
                for item in receipt["inputs"]
                if item["path"] == "audit/completion-gate.json"
            )
            self.assertEqual(
                completion_state["sha256"],
                stage_runner.sha256_file(audit / "completion-gate.json"),
            )

    def test_readiness_test_override_skips_mutating_completion_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            (root / "statement.md").write_text(
                "Time Limit: 2 s\nMemory Limit: 256 MiB\n", encoding="utf-8"
            )
            audit = root / "audit"
            audit.mkdir()
            completion_path = audit / "completion-gate.json"
            completion_path.write_text('{"generation":0}\n', encoding="utf-8")
            (audit / "regression.md").write_text("regression\n", encoding="utf-8")
            machine_path = audit / "regression-machine.json"
            machine_path.write_text('{"generation":0}\n', encoding="utf-8")
            (root / "prompt.md").write_text("write readiness\n", encoding="utf-8")
            script = (
                "from pathlib import Path; "
                "Path('audit/readiness.md').write_text('ready\\n', encoding='utf-8')"
            )
            args = SimpleNamespace(
                problem_dir=root,
                stage="readiness",
                run_id="test-override-no-replay",
                prompt_rel=PurePosixPath("prompt.md"),
                extra_inputs=[],
                extra_outputs=[],
                extra_trees=[],
                test_command=shlex.join([sys.executable, "-c", script]),
            )
            with (
                mock.patch.object(
                    stage_runner,
                    "require_prior_stage_receipt",
                    return_value={"stage": "build-hardening"},
                ),
                mock.patch.object(stage_runner, "run_handoff_gate") as handoff,
            ):
                code, _result = stage_runner.execute(args)
            self.assertEqual(code, 0)
            handoff.assert_not_called()
            self.assertEqual(completion_path.read_text(), '{"generation":0}\n')
            self.assertEqual(machine_path.read_text(), '{"generation":0}\n')


if __name__ == "__main__":
    unittest.main()
