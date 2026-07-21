from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPT_DIR = (
    Path(__file__).resolve().parents[1]
    / "skills/icpc-light-problem-builder/scripts"
)
sys.path.insert(0, str(SCRIPT_DIR))

import run_blind_review as blind_review  # noqa: E402
import verify_blind_stage as blind_gate  # noqa: E402


class ContaminationStatusTests(unittest.TestCase):
    PARSERS = (
        blind_review.contamination_status,
        blind_gate.status_contamination,
    )

    def assert_parsers(self, text: str, expected: str | None) -> None:
        for parser in self.PARSERS:
            with self.subTest(parser=parser.__module__, text=text):
                self.assertEqual(parser(text), expected)

    def test_accepts_existing_clean_machine_field(self) -> None:
        for text in (
            "contamination_status: clean\n",
            "Contamination status: `clean`\n",
            "- **Contamination Status**: **clean**. Public files only.\n",
        ):
            self.assert_parsers(text, "clean")

    def test_accepts_explicit_markdown_no_with_trailing_explanation(self) -> None:
        text = (
            "Lane: neutral-01\n"
            "Contamination: **No**. Only files under public/ were inspected.\n"
        )
        self.assert_parsers(text, "clean")

    def test_accepts_explicit_uncontaminated_alias_in_both_consumers(self) -> None:
        self.assert_parsers(
            "Contamination status: **uncontaminated**.\n",
            "clean",
        )

    def test_yes_and_contaminated_never_become_clean(self) -> None:
        for text in (
            "Contamination: Yes. Setter material was visible.\n",
            "contamination_status: contaminated\n",
        ):
            self.assert_parsers(text, "contaminated")

    def test_conflicting_explicit_fields_fail_closed(self) -> None:
        text = "Contamination: No.\ncontamination_status: contaminated\n"
        self.assert_parsers(text, None)

    def test_missing_or_ambiguous_values_fail_closed(self) -> None:
        for text in (
            "Only files under public/ were inspected.\n",
            "Contamination: unknown\n",
            "Contamination: No or Yes\n",
            "Contamination: **No/Yes**\n",
            "Contamination: No. Yes\n",
            "Contamination: No; contaminated\n",
            "Contamination: probably no\n",
            "Contamination: No evidence was recorded\n",
        ):
            self.assert_parsers(text, None)


if __name__ == "__main__":
    unittest.main()
