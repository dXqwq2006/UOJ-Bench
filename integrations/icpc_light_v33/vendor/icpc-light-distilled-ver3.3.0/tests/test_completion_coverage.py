from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_DIR = (
    Path(__file__).resolve().parents[1]
    / "skills/icpc-light-problem-builder/scripts"
)
sys.path.insert(0, str(SCRIPT_DIR))

import verify_completion as completion  # noqa: E402


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_fixture(root: Path) -> completion.Report:
    audit = root / "audit"
    tests = root / "package/tests"
    generators = root / "package/generators"
    audit.mkdir(parents=True)
    tests.mkdir(parents=True)
    generators.mkdir(parents=True)
    input_path = tests / "path-max.in"
    input_path.write_text("1\n", encoding="utf-8")
    (tests / "path-max.ans").write_text("1\n", encoding="utf-8")
    (generators / "gen.cpp").write_text("int main(){}\n", encoding="utf-8")
    (audit / "test-manifest.md").write_text(
        "\n".join(
            (
                "| family_id | purpose | command or fixed file | seed/params | size/limits reached | target routes | validator status | introduced_round |",
                "| --- | --- | --- | --- | --- | --- | --- | --- |",
                "| F01 | path at maximum n | gen --mode path | seed=1,mode=path | n=max | W01 | passed | 1 |",
                "",
            )
        ),
        encoding="utf-8",
    )
    (audit / "regression-plan.json").write_text(
        json.dumps(
            {
                "schema_version": 3,
                "required_limit_tags": ["n=max"],
                "release_tests": [
                    {
                        "test_id": "path-max",
                        "input": "package/tests/path-max.in",
                        "answer": "package/tests/path-max.ans",
                        "limit_tags": ["n=max"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    matrix = {
        "schema_version": 1,
        "route_axes": [
            {
                "axis": axis,
                "status": "covered" if axis == "proof-and-implementation-gaps" else "not-applicable",
                "basis": "reviewed for this direct fixture",
                "route_ids": ["W01"] if axis == "proof-and-implementation-gaps" else [],
                "obligation_ids": ["O01"] if axis == "proof-and-implementation-gaps" else [],
            }
            for axis in sorted(completion.REQUIRED_ROUTE_AXES)
        ],
        "obligations": [
            {
                "obligation_id": "O01",
                "kind": "wrong-route",
                "description": "break a route that assumes paths stay shallow",
                "family_ids": ["F01"],
                "target_route_ids": ["W01"],
                "required_variant_modes": ["exact", "scaled"],
                "required_composed_dimensions": ["path", "n=max"],
            }
        ],
        "scale_axes": [
            {
                "axis_id": "n",
                "description": "maximum vertex count",
                "limit_tags": ["n=max"],
                "input_paths": ["package/tests/path-max.in"],
                "composed_with": ["path"],
            }
        ],
        "families": [
            {
                "family_id": "F01",
                "purpose": "path at maximum n",
                "inputs": [
                    {
                        "path": "package/tests/path-max.in",
                        "sha256": sha256(input_path),
                    }
                ],
                "generation": {
                    "kind": "generator",
                    "source": "package/generators/gen.cpp",
                    "args": ["--mode", "path", "--seed", "1"],
                    "seed_params": ["mode=path", "seed=1"],
                },
                "target_obligation_ids": ["O01"],
                "target_route_ids": ["W01"],
                "scale_axis_ids": ["n"],
                "variant_modes": ["exact", "scaled"],
                "composed_dimensions": ["path", "n=max"],
            }
        ],
    }
    (audit / "coverage-matrix.json").write_text(
        json.dumps(matrix), encoding="utf-8"
    )
    report = completion.Report(root, "audit/completion-gate.json", False)
    report.facts["wrong_route_matrix"] = [{"route_id": "W01"}]
    return report


class CompletionCoverageTests(unittest.TestCase):
    def test_compact_matrix_binds_routes_limits_recipes_and_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            report = write_fixture(root)
            completion.check_test_manifest(report)
            result = completion.check_coverage_matrix(report)
            self.assertIsNotNone(result)
            self.assertEqual(report.issues, [])
            self.assertEqual(report.facts["coverage_matrix"]["release_inputs"], 1)

    def test_stale_input_hash_fails(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            report = write_fixture(root)
            matrix_path = root / "audit/coverage-matrix.json"
            matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
            matrix["families"][0]["inputs"][0]["sha256"] = "0" * 64
            matrix_path.write_text(json.dumps(matrix), encoding="utf-8")
            completion.check_test_manifest(report)
            self.assertIsNone(completion.check_coverage_matrix(report))
            self.assertTrue(any("sha256 is stale" in issue for issue in report.issues))

    def test_manifest_rejects_unbound_seed_and_target_columns(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            report = write_fixture(root)
            manifest = root / "audit/test-manifest.md"
            manifest.write_text(
                manifest.read_text(encoding="utf-8").replace(
                    "seed=1,mode=path | n=max | W01",
                    " | n=max | ",
                ),
                encoding="utf-8",
            )
            completion.check_test_manifest(report)
            self.assertTrue(any("seed_params" in issue for issue in report.issues))
            self.assertTrue(any("target_routes" in issue for issue in report.issues))

    def test_generator_manifest_accepts_bound_path_forms(self) -> None:
        for command in (
            "package/generators/gen.cpp --mode path",
            "./gen --mode path",
        ):
            with self.subTest(command=command), tempfile.TemporaryDirectory() as raw:
                root = Path(raw).resolve()
                report = write_fixture(root)
                manifest = root / "audit/test-manifest.md"
                manifest.write_text(
                    manifest.read_text(encoding="utf-8").replace(
                        "gen --mode path", command
                    ),
                    encoding="utf-8",
                )
                completion.check_test_manifest(report)
                self.assertIsNotNone(completion.check_coverage_matrix(report))
                self.assertEqual(report.issues, [])

    def test_scale_axis_tags_must_be_carried_by_the_named_release_input(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            report = write_fixture(root)
            plan_path = root / "audit/regression-plan.json"
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            plan["release_tests"][0]["limit_tags"] = []
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            completion.check_test_manifest(report)
            self.assertIsNone(completion.check_coverage_matrix(report))
            self.assertTrue(
                any("tags not carried" in issue for issue in report.issues)
            )

    def test_scale_composition_and_manifest_provenance_are_cross_checked(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            report = write_fixture(root)
            matrix_path = root / "audit/coverage-matrix.json"
            matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
            matrix["scale_axes"][0]["composed_with"] = ["unrepresented-shape"]
            matrix["families"][0]["generation"]["seed_params"] = ["seed=2"]
            matrix_path.write_text(json.dumps(matrix), encoding="utf-8")
            completion.check_test_manifest(report)
            self.assertIsNone(completion.check_coverage_matrix(report))
            issues = "\n".join(report.issues)
            self.assertIn("seed_params differs", issues)
            self.assertIn("composed_with dimensions", issues)

    def test_composed_dimensions_must_coexist_in_one_family(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            report = write_fixture(root)
            second_input = root / "package/tests/max-only.in"
            second_input.write_text("2\n", encoding="utf-8")
            (root / "package/tests/max-only.ans").write_text("2\n", encoding="utf-8")

            plan_path = root / "audit/regression-plan.json"
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            plan["release_tests"].append(
                {
                    "test_id": "max-only",
                    "input": "package/tests/max-only.in",
                    "answer": "package/tests/max-only.ans",
                    "limit_tags": ["n=max"],
                }
            )
            plan_path.write_text(json.dumps(plan), encoding="utf-8")

            manifest = root / "audit/test-manifest.md"
            manifest.write_text(
                manifest.read_text(encoding="utf-8").replace(
                    "\n\n",
                    "\n| F02 | max without path | gen --mode max-only | "
                    "seed=2,mode=max-only | n=max | W01 | passed | 1 |\n\n",
                ),
                encoding="utf-8",
            )

            matrix_path = root / "audit/coverage-matrix.json"
            matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
            first = matrix["families"][0]
            first["composed_dimensions"] = ["path"]
            second = json.loads(json.dumps(first))
            second.update(
                {
                    "family_id": "F02",
                    "purpose": "max without path",
                    "inputs": [
                        {
                            "path": "package/tests/max-only.in",
                            "sha256": sha256(second_input),
                        }
                    ],
                    "generation": {
                        "kind": "generator",
                        "source": "package/generators/gen.cpp",
                        "args": ["--mode", "max-only", "--seed", "2"],
                        "seed_params": ["mode=max-only", "seed=2"],
                    },
                    "composed_dimensions": ["n=max"],
                }
            )
            matrix["families"].append(second)
            matrix["obligations"][0]["family_ids"] = ["F01", "F02"]
            matrix["scale_axes"][0]["input_paths"].append(
                "package/tests/max-only.in"
            )
            matrix["scale_axes"][0]["composed_with"] = ["path", "n=max"]
            matrix_path.write_text(json.dumps(matrix), encoding="utf-8")

            completion.check_test_manifest(report)
            self.assertIsNone(completion.check_coverage_matrix(report))
            issues = "\n".join(report.issues)
            self.assertIn("not jointly covered by any one linked family", issues)
            self.assertIn("not jointly carried by any one linked family", issues)

    def test_fixed_family_manifest_must_name_every_input(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            report = write_fixture(root)
            second_input = root / "package/tests/path-second.in"
            second_input.write_text("2\n", encoding="utf-8")
            (root / "package/tests/path-second.ans").write_text(
                "2\n", encoding="utf-8"
            )

            plan_path = root / "audit/regression-plan.json"
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            plan["release_tests"].append(
                {
                    "test_id": "path-second",
                    "input": "package/tests/path-second.in",
                    "answer": "package/tests/path-second.ans",
                    "limit_tags": ["n=max"],
                }
            )
            plan_path.write_text(json.dumps(plan), encoding="utf-8")

            manifest = root / "audit/test-manifest.md"
            manifest.write_text(
                manifest.read_text(encoding="utf-8").replace(
                    "gen --mode path",
                    "package/tests/path-max.in package/tests/path-second.in.bak",
                ),
                encoding="utf-8",
            )

            matrix_path = root / "audit/coverage-matrix.json"
            matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
            family = matrix["families"][0]
            family["inputs"].append(
                {
                    "path": "package/tests/path-second.in",
                    "sha256": sha256(second_input),
                }
            )
            family["generation"] = {
                "kind": "fixed",
                "recipe": "two hand-constructed maximum path witnesses",
                "seed_params": ["mode=path", "seed=1"],
            }
            matrix["scale_axes"][0]["input_paths"].append(
                "package/tests/path-second.in"
            )
            matrix_path.write_text(json.dumps(matrix), encoding="utf-8")

            completion.check_test_manifest(report)
            self.assertIsNone(completion.check_coverage_matrix(report))
            self.assertTrue(
                any(
                    "fixed inputs are absent" in issue
                    and "path-second.in" in issue
                    for issue in report.issues
                )
            )

    def test_fixed_generation_rejects_generator_only_fields(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            report = write_fixture(root)
            matrix_path = root / "audit/coverage-matrix.json"
            matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
            matrix["families"][0]["generation"] = {
                "kind": "fixed",
                "recipe": "hand-constructed maximum path witness",
                "seed_params": ["mode=path", "seed=1"],
                "args": [],
            }
            matrix_path.write_text(json.dumps(matrix), encoding="utf-8")
            completion.check_test_manifest(report)
            self.assertIsNone(completion.check_coverage_matrix(report))
            self.assertTrue(
                any("args must be omitted" in issue for issue in report.issues)
            )


if __name__ == "__main__":
    unittest.main()
