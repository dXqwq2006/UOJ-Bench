#!/usr/bin/env python3
"""Validate the replaceable ICPC Light preclassification interface.

The validator intentionally uses only the Python standard library.  It checks
the stable schema and cross-field routing rules; it does not decide the grade.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


REQUIRED_MODEL = "gpt-5.6-sol"
REQUIRED_EFFORT = "ultra"
FIELDS = (
    "schema_version",
    "agent_model",
    "agent_reasoning_effort",
    "preclassification",
    "scam_status",
    "data_buildability",
    "workflow_profile",
    "decision",
    "confidence",
    "provisional",
    "wrong_solution_min",
    "wrong_solution_max",
    "adversarial_round_mode",
    "adversarial_round_min",
    "adversarial_round_max",
    "stop_reason",
    "risk_tags",
    "required_checks",
    "regrade_triggers",
)
STOP_REASONS = {
    "none",
    "shortcut-unresolved",
    "unverifiable-contract",
    "unverifiable-oracle",
    "unverifiable-generation",
    "unverifiable-checker",
    "unverifiable-protocol",
    "unverifiable-numeric",
    "unbounded-adversarial-plan",
    "adversarial-budget-exhausted",
    "outside-scope",
}
UNVERIFIABLE_STOP_REASONS = {
    "unverifiable-contract",
    "unverifiable-oracle",
    "unverifiable-generation",
    "unverifiable-checker",
    "unverifiable-protocol",
    "unverifiable-numeric",
}
P2_RISK_PRECEDENCE = (
    ("constructive-output", "L1C-constructive-output"),
    ("flow-model-like", "L1F-flow-model-like"),
    ("greedy-deceptive", "L1G-greedy-deceptive"),
)


class ReportError(ValueError):
    pass


def yaml_scalar(raw: str) -> Any:
    value = raw.strip()
    if not value:
        return None
    if value in {"[]", "[ ]"}:
        return []
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        return [] if not inner else [yaml_scalar(part) for part in inner.split(",")]
    lower = value.lower()
    if lower == "true":
        return True
    if lower == "false":
        return False
    if lower in {"null", "~"}:
        return None
    if re.fullmatch(r"[-+]?\d+", value):
        return int(value)
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value.strip("`")


def parse_front_matter(path: Path) -> tuple[dict[str, Any], str]:
    if path.is_symlink() or not path.is_file():
        raise ReportError(f"report is not a regular file: {path}")
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ReportError(f"cannot read UTF-8 report: {exc}") from exc
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ReportError("report must begin with YAML front matter")
    try:
        end = next(index for index in range(1, len(lines)) if lines[index].strip() == "---")
    except StopIteration as exc:
        raise ReportError("front matter has no closing delimiter") from exc
    result: dict[str, Any] = {}
    active_list: str | None = None
    for line_number, line in enumerate(lines[1:end], start=2):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if line[:1].isspace() and stripped.startswith("-"):
            if active_list is None:
                raise ReportError(f"line {line_number}: list item has no field")
            result[active_list].append(yaml_scalar(stripped[1:].strip()))
            continue
        match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_-]*)\s*:\s*(.*)", stripped)
        if match is None:
            raise ReportError(f"line {line_number}: unsupported YAML syntax")
        key, raw = match.groups()
        if key in result:
            raise ReportError(f"line {line_number}: duplicate field {key!r}")
        if raw.strip():
            result[key] = yaml_scalar(raw)
            active_list = None
        else:
            result[key] = []
            active_list = key
    body = "\n".join(lines[end + 1 :]).strip()
    return result, body


def require_string_lists(data: dict[str, Any], issues: list[str]) -> None:
    for key in ("risk_tags", "required_checks", "regrade_triggers"):
        value = data.get(key)
        if not isinstance(value, list):
            issues.append(f"{key} must be a YAML list")
        elif not all(isinstance(item, str) and item.strip() for item in value):
            issues.append(f"{key} must contain only non-empty strings")
        elif len(value) != len(set(value)):
            issues.append(f"{key} must not contain duplicates")


def validate(data: dict[str, Any], *, require_body: bool, body: str = "") -> list[str]:
    issues: list[str] = []
    missing = [field for field in FIELDS if field not in data]
    extra = sorted(set(data) - set(FIELDS))
    if missing:
        issues.append("missing fields: " + ", ".join(missing))
    if extra:
        issues.append("unsupported fields: " + ", ".join(extra))
    if data.get("schema_version") != 2 or type(data.get("schema_version")) is not int:
        issues.append("schema_version must be integer 2")
    if data.get("agent_model") != REQUIRED_MODEL:
        issues.append(f"agent_model must be {REQUIRED_MODEL!r}")
    if data.get("agent_reasoning_effort") != REQUIRED_EFFORT:
        issues.append(f"agent_reasoning_effort must be {REQUIRED_EFFORT!r}")
    if data.get("confidence") not in {"low", "medium", "high"}:
        issues.append("confidence must be low, medium, or high")
    if type(data.get("provisional")) is not bool:
        issues.append("provisional must be a YAML boolean")
    if data.get("scam_status") not in {"none", "suspected", "confirmed"}:
        issues.append("scam_status is outside its enum")
    if data.get("decision") not in {"continue", "escalate", "stop"}:
        issues.append("decision is outside its enum")
    if data.get("stop_reason") not in STOP_REASONS:
        issues.append("stop_reason is outside its enum")
    if (data.get("stop_reason") == "none") != (data.get("decision") == "continue"):
        issues.append("stop_reason must be none exactly when decision is continue")
    require_string_lists(data, issues)
    for key in (
        "wrong_solution_min",
        "wrong_solution_max",
        "adversarial_round_min",
        "adversarial_round_max",
    ):
        if type(data.get(key)) is not int:
            issues.append(f"{key} must be an integer")

    pre = data.get("preclassification")
    combination = (
        data.get("data_buildability"),
        data.get("workflow_profile"),
        data.get("wrong_solution_min"),
        data.get("wrong_solution_max"),
        data.get("adversarial_round_mode"),
        data.get("adversarial_round_min"),
        data.get("adversarial_round_max"),
    )
    if pre == "P1-random-strong":
        expected = ("D0-direct", "L0-simple-standard", 3, 5, "single", 1, 1)
        if combination != expected:
            issues.append("P1 compatibility/profile/quota/round fields are inconsistent")
        if data.get("decision") != "continue" or data.get("provisional") is not False:
            issues.append("P1 must be a non-provisional continuing decision")
        if data.get("scam_status") not in {"none", "confirmed"}:
            issues.append("P1 scam_status must be none or confirmed")
    elif pre == "P2-structured-bounded":
        tags = data.get("risk_tags") if isinstance(data.get("risk_tags"), list) else []
        expected_profile = "L1-ordinary"
        for tag, profile in P2_RISK_PRECEDENCE:
            if tag in tags:
                expected_profile = profile
                break
        expected = ("D1-structured", expected_profile, 5, 8, "single", 1, 1)
        if combination != expected:
            issues.append(
                "P2 compatibility/profile/quota/round fields violate the fixed risk precedence"
            )
        if data.get("decision") != "continue" or data.get("provisional") is not False:
            issues.append("P2 must be a non-provisional continuing decision")
        if data.get("scam_status") not in {"none", "confirmed"}:
            issues.append("P2 scam_status must be none or confirmed")
    elif pre == "P3-adversarial-intensive":
        expected = ("D2-specialist", "L2-high-risk", 8, 10, "bounded-multi", 1, 3)
        if combination != expected:
            issues.append("P3 compatibility/profile/quota/round fields are inconsistent")
        if data.get("decision") not in {"continue", "escalate"}:
            issues.append("P3 decision must be continue or escalate")
        if data.get("decision") == "continue" and data.get("provisional") is not False:
            issues.append("a continuing P3 decision must be non-provisional")
        if data.get("scam_status") == "confirmed" and not (
            data.get("decision") == "continue"
            and data.get("provisional") is False
            and data.get("stop_reason") == "none"
        ):
            issues.append("confirmed simpler route must be a non-provisional continuing P3 decision")
        if data.get("scam_status") == "suspected" and not (
            data.get("decision") == "escalate"
            and data.get("provisional") is True
            and data.get("stop_reason") == "shortcut-unresolved"
        ):
            issues.append("suspected shortcut must be provisional P3/escalate/shortcut-unresolved")
        if data.get("stop_reason") == "shortcut-unresolved" and not (
            data.get("scam_status") == "suspected"
            and data.get("provisional") is True
        ):
            issues.append("shortcut-unresolved requires a provisional suspected shortcut")
        if (
            data.get("decision") == "escalate"
            and data.get("provisional") is False
            and data.get("stop_reason") in UNVERIFIABLE_STOP_REASONS
        ):
            issues.append("non-provisional P3 escalation cannot use an unverifiable-foundation reason")
        if (
            data.get("decision") == "escalate"
            and data.get("provisional") is True
            and data.get("stop_reason")
            in {"unbounded-adversarial-plan", "adversarial-budget-exhausted"}
        ):
            issues.append("provisional P3 escalation cannot claim a completed adversarial-plan assessment")
    elif pre == "S-stop":
        expected = ("D3-stop", "outside-light", 0, 0, "none", 0, 0)
        if combination != expected:
            issues.append("S-stop compatibility/profile/quota/round fields are inconsistent")
        if data.get("decision") != "stop" or data.get("provisional") is not False:
            issues.append("S-stop must be a non-provisional stop decision")
        if data.get("scam_status") != "none":
            issues.append("S-stop is reserved for unverifiable foundations and scam_status must be none")
        if data.get("stop_reason") not in UNVERIFIABLE_STOP_REASONS:
            issues.append("S-stop requires a concrete unverifiable-foundation stop reason")
    else:
        issues.append("preclassification is outside the schema-v2 enum")
    if require_body and not body:
        issues.append("report body must preserve the evidence and next-owner explanation")
    return issues


def base_case(pre: str) -> dict[str, Any]:
    data: dict[str, Any] = {
        "schema_version": 2,
        "agent_model": REQUIRED_MODEL,
        "agent_reasoning_effort": REQUIRED_EFFORT,
        "preclassification": pre,
        "scam_status": "none",
        "decision": "continue",
        "confidence": "high",
        "provisional": False,
        "stop_reason": "none",
        "risk_tags": [],
        "required_checks": [],
        "regrade_triggers": [],
    }
    if pre == "P1-random-strong":
        data.update(data_buildability="D0-direct", workflow_profile="L0-simple-standard", wrong_solution_min=3, wrong_solution_max=5, adversarial_round_mode="single", adversarial_round_min=1, adversarial_round_max=1)
    elif pre == "P2-structured-bounded":
        data.update(data_buildability="D1-structured", workflow_profile="L1-ordinary", wrong_solution_min=5, wrong_solution_max=8, adversarial_round_mode="single", adversarial_round_min=1, adversarial_round_max=1)
    elif pre == "P3-adversarial-intensive":
        data.update(data_buildability="D2-specialist", workflow_profile="L2-high-risk", wrong_solution_min=8, wrong_solution_max=10, adversarial_round_mode="bounded-multi", adversarial_round_min=1, adversarial_round_max=3)
    else:
        data.update(data_buildability="D3-stop", workflow_profile="outside-light", wrong_solution_min=0, wrong_solution_max=0, adversarial_round_mode="none", adversarial_round_min=0, adversarial_round_max=0, decision="stop", stop_reason="unverifiable-contract")
    return data


def self_test() -> list[str]:
    failures: list[str] = []
    cases: list[tuple[str, dict[str, Any]]] = [
        ("D0", base_case("P1-random-strong")),
        ("D1", base_case("P2-structured-bounded")),
        ("D2", base_case("P3-adversarial-intensive")),
        ("D3", base_case("S-stop")),
    ]
    for tag, profile in P2_RISK_PRECEDENCE:
        case = base_case("P2-structured-bounded")
        case["risk_tags"] = [tag]
        case["workflow_profile"] = profile
        cases.append((profile, case))
    suspected = base_case("P3-adversarial-intensive")
    suspected.update(scam_status="suspected", decision="escalate", provisional=True, stop_reason="shortcut-unresolved")
    cases.append(("shortcut-suspected", suspected))
    for pre in (
        "P1-random-strong",
        "P2-structured-bounded",
        "P3-adversarial-intensive",
    ):
        confirmed = base_case(pre)
        confirmed["scam_status"] = "confirmed"
        cases.append((f"shortcut-confirmed-{pre}", confirmed))
    for name, case in cases:
        found = validate(case, require_body=False)
        if found:
            failures.append(f"{name}: {'; '.join(found)}")
    precedence_negative = base_case("P2-structured-bounded")
    precedence_negative["risk_tags"] = ["constructive-output", "flow-model-like"]
    precedence_negative["workflow_profile"] = "L1F-flow-model-like"
    if not validate(precedence_negative, require_body=False):
        failures.append("risk precedence negative case was incorrectly accepted")
    provisional_d0 = base_case("P1-random-strong")
    provisional_d0["provisional"] = True
    if not validate(provisional_d0, require_body=False):
        failures.append("provisional P1 negative case was incorrectly accepted")
    confirmed_stop = base_case("S-stop")
    confirmed_stop["scam_status"] = "confirmed"
    if not validate(confirmed_stop, require_body=False):
        failures.append("confirmed S-stop negative case was incorrectly accepted")
    removed_reason = base_case("S-stop")
    removed_reason["stop_reason"] = "shortcut-confirmed"
    if not validate(removed_reason, require_body=False):
        failures.append("removed shortcut-confirmed stop reason was incorrectly accepted")
    nonfoundation_stop = base_case("S-stop")
    nonfoundation_stop["stop_reason"] = "unbounded-adversarial-plan"
    if not validate(nonfoundation_stop, require_body=False):
        failures.append("non-foundation S-stop reason was incorrectly accepted")
    reverse_shortcut = base_case("P3-adversarial-intensive")
    reverse_shortcut.update(
        decision="escalate",
        provisional=False,
        stop_reason="shortcut-unresolved",
    )
    if not validate(reverse_shortcut, require_body=False):
        failures.append("shortcut-unresolved reverse mapping was incorrectly accepted")
    nonprovisional_unverifiable = base_case("P3-adversarial-intensive")
    nonprovisional_unverifiable.update(
        decision="escalate",
        provisional=False,
        stop_reason="unverifiable-contract",
    )
    if not validate(nonprovisional_unverifiable, require_body=False):
        failures.append("non-provisional unverifiable P3 escalation was incorrectly accepted")
    provisional_plan = base_case("P3-adversarial-intensive")
    provisional_plan.update(
        decision="escalate",
        provisional=True,
        stop_reason="unbounded-adversarial-plan",
    )
    if not validate(provisional_plan, require_body=False):
        failures.append("provisional adversarial-plan P3 escalation was incorrectly accepted")
    return failures


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate schema-v2 ICPC Light preclassification output.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--report", type=Path)
    group.add_argument("--self-test", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.self_test:
        issues = self_test()
        target = "built-in forward fixtures"
    else:
        assert args.report is not None
        try:
            data, body = parse_front_matter(args.report.resolve())
        except ReportError as exc:
            issues = [str(exc)]
        else:
            issues = validate(data, require_body=True, body=body)
        target = str(args.report)
    payload = {
        "schema_version": 1,
        "validator": "icpc-light-preclassification-schema-v2",
        "status": "pass" if not issues else "fail",
        "target": target,
        "issues": issues,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        stream = sys.stdout if not issues else sys.stderr
        print(f"Preclassification validator: {'PASS' if not issues else 'FAIL'}", file=stream)
        for issue in issues:
            print(f"- {issue}", file=stream)
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
