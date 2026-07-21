"""Pure verifier-election helpers for native package verification."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from .local_runtime import CommandResult
from .spec import PackageSolutionSpec, PackageSpec, PackageTestSpec


OutputMatcher = Callable[[str, str, str], bool]


def _expected_verdict_for_tag(tag: str) -> str:
    normalized = tag.lower().replace("_", "-")
    if normalized in {"main", "accepted", "ok"}:
        return "AC"
    return "REJECTED"


def _is_bruteforce_tag(tag: str) -> bool:
    normalized = tag.lower().replace("_", "-")
    return normalized in {"brute-force", "bruteforce"} or normalized.startswith(
        ("brute-force-", "bruteforce-")
    )


def _normalize_tokens(text: str) -> tuple[str, ...]:
    """Tokenize for whitespace-insensitive output equality.

    Brute-force majority voting (Pre-Phase A) and the candidate qualification
    filter (Phase B) use this when no checker is configured. It mirrors the
    package adapter's token-comparison fallback.
    """
    return tuple(text.split())


def _majority_vote_truth(
    tests: list[PackageTestSpec],
    brute_runs: dict[str, dict[str, dict[str, object]]],
) -> tuple[dict[str, dict[str, object]], list[dict[str, object]]]:
    """Compute per-test majority-vote truth from brute-force runs.

    For each test we look at brute forces that produced output (status == AC).
    Outputs are grouped by token equivalence. If any group contains >= 2 voters,
    that group's output becomes the truth (the largest group wins; ties resolved
    by which brute force voted first).

    A "disagreement" is recorded when >= 2 brute forces produced output but no two
    outputs match; that strongly suggests one brute force has a bug, and the run
    must halt before pretending to know the right answer.

    Returns ``(truth, disagreements)`` where ``truth[input_path] = {"output": ...,
    "voters": [bf_source_path, ...]}`` and ``disagreements`` is a list of
    per-test diagnostic dicts.
    """
    truth: dict[str, dict[str, object]] = {}
    disagreements: list[dict[str, object]] = []
    brute_order = list(brute_runs.keys())
    for spec in tests:
        groups: list[
            dict[str, object]
        ] = []  # {"tokens": tuple, "voters": list[str], "output": str}
        participants: list[str] = []
        for bf_path in brute_order:
            record = brute_runs[bf_path].get(spec.input_path)
            if record is None or record["status"] != "AC":
                continue
            participants.append(bf_path)
            tokens = _normalize_tokens(str(record["output"]))
            for group in groups:
                if group["tokens"] == tokens:
                    group["voters"].append(bf_path)
                    break
            else:
                groups.append(
                    {
                        "tokens": tokens,
                        "voters": [bf_path],
                        "output": str(record["output"]),
                    }
                )
        if not groups:
            # No brute force could even attempt this test (all exit-67 or failed).
            # That is OK: this test simply has no oracle and will not gate any
            # candidate during Phase B.
            continue
        majority = max(groups, key=lambda g: len(g["voters"]))
        if len(majority["voters"]) >= 2:
            truth[spec.input_path] = {
                "output": majority["output"],
                "voters": list(majority["voters"]),
            }
        elif len(participants) >= 2:
            # >= 2 brute forces produced output but no two agree -> disagreement.
            disagreements.append(
                {
                    "input_path": spec.input_path,
                    "participants": participants,
                    "groups": [
                        {
                            "voters": list(g["voters"]),
                            "preview": _preview_output(g["output"]),
                        }
                        for g in groups
                    ],
                }
            )
        # else: single participant -> no truth, no disagreement (we just lack data).
    return truth, disagreements


def _preview_output(text: str, max_chars: int = 400) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "...[truncated]"


def _bruteforce_vote_summary(
    tests: list[PackageTestSpec],
    brute_runs: dict[str, dict[str, dict[str, object]]],
    truth: dict[str, dict[str, object]],
) -> dict[str, object]:
    """Aggregate per-test vote counts for the report.

    Counts how often each brute force ended up in the winning majority versus
    being the only voter or producing no output at all. Useful for spotting a
    brute force that systematically disagrees with the consensus.
    """
    per_bf = {
        bf_path: {"agreed_with_truth": 0, "disagreed_with_truth": 0, "no_output": 0}
        for bf_path in brute_runs
    }
    tests_with_truth = 0
    tests_without_truth = 0
    for spec in tests:
        record = truth.get(spec.input_path)
        if record is None:
            tests_without_truth += 1
        else:
            tests_with_truth += 1
        truth_tokens = (
            _normalize_tokens(str(record["output"])) if record is not None else None
        )
        for bf_path, runs in brute_runs.items():
            run = runs.get(spec.input_path)
            if run is None or run["status"] != "AC":
                per_bf[bf_path]["no_output"] += 1
                continue
            if truth_tokens is None:
                continue
            if _normalize_tokens(str(run["output"])) == truth_tokens:
                per_bf[bf_path]["agreed_with_truth"] += 1
            else:
                per_bf[bf_path]["disagreed_with_truth"] += 1
    return {
        "tests_with_truth": tests_with_truth,
        "tests_without_truth": tests_without_truth,
        "per_bruteforce": per_bf,
    }


def _write_disagreement_report(
    work_dir: Path,
    disagreements: list[dict[str, object]],
    brute_runs: dict[str, dict[str, dict[str, object]]],
) -> None:
    """Persist per-test disagreement diagnostics to ``bruteforce_disagreement.json``."""
    detailed = []
    for entry in disagreements:
        rich_groups = []
        for group in entry["groups"]:  # type: ignore[index]
            voters = group["voters"]  # type: ignore[index]
            rich_groups.append(
                {
                    "voters": voters,
                    "preview": group["preview"],  # type: ignore[index]
                }
            )
        detailed.append(
            {
                "input_path": entry["input_path"],
                "participants": entry["participants"],
                "groups": rich_groups,
            }
        )
    path = work_dir / "bruteforce_disagreement.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "cpideas.bruteforce_disagreement.v1",
                "disagreements": detailed,
                "brute_force_sources": list(brute_runs.keys()),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _qualify_candidates(
    ac_candidates: list[PackageSolutionSpec],
    candidate_runs: dict[str, dict[str, dict[str, object]]],
    truth: dict[str, dict[str, object]],
    output_matcher: OutputMatcher | None = None,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Filter AC candidates by per-test agreement with brute-force majority.

    A candidate passes when, on every test for which ``truth`` is defined, its
    output (when produced) matches the truth. Tests where the candidate
    ``exit(67)``-s are tolerated; that just means the candidate cannot answer at
    this scale, which is expected for baseline solutions on large tests.

    Returns ``(qualified, disqualified)``. Both are JSON-serializable lists of
    dicts so they can be embedded in the verify report.
    """
    qualified: list[dict[str, object]] = []
    disqualified: list[dict[str, object]] = []
    for ac in ac_candidates:
        runs = candidate_runs.get(ac.source_path, {})
        compile_ok = bool(runs)
        if not compile_ok:
            disqualified.append(
                {
                    "source_path": ac.source_path,
                    "reason": "compile_failed",
                }
            )
            continue
        first_failure: dict[str, object] | None = None
        covered = 0
        max_time_ms = 0
        for input_path, expected_record in truth.items():
            run = runs.get(input_path)
            if run is None or run["status"] == "UNSUPPORTED":
                continue
            if run["status"] != "AC":
                first_failure = {
                    "input_path": input_path,
                    "status": run["status"],
                    "preview_actual": _preview_output(str(run["output"])),
                }
                break
            if output_matcher is None:
                agree = _normalize_tokens(str(run["output"])) == _normalize_tokens(
                    str(expected_record["output"])
                )
            else:
                agree = output_matcher(
                    input_path,
                    str(run["output"]),
                    str(expected_record["output"]),
                )
            if not agree:
                first_failure = {
                    "input_path": input_path,
                    "status": "DISAGREES_WITH_BRUTEFORCE",
                    "preview_expected": _preview_output(str(expected_record["output"])),
                    "preview_actual": _preview_output(str(run["output"])),
                }
                break
        if first_failure is not None:
            disqualified.append(
                {
                    "source_path": ac.source_path,
                    "tag": ac.tag,
                    "reason": "mismatched_truth",
                    "first_failure": first_failure,
                }
            )
            continue
        # Count covered tests + worst-case time across the candidate's full run.
        for run in runs.values():
            if run["status"] != "UNSUPPORTED":
                covered += 1
            try:
                max_time_ms = max(max_time_ms, int(run["time_ms"]))
            except (TypeError, ValueError):
                pass
        qualified.append(
            {
                "source_path": ac.source_path,
                "tag": ac.tag,
                "covered_tests": covered,
                "max_time_ms": max_time_ms,
                "claimed_complexity": _claimed_complexity_for_spec(ac),
            }
        )
    return qualified, disqualified


def _claimed_complexity_for_spec(_solution: PackageSolutionSpec) -> str | None:
    # PackageSolutionSpec does not carry the claimed_complexity today; this hook
    # keeps the JSON shape forward-compatible when we wire it through in a
    # follow-up.
    return None


def _elect_best(qualified: list[dict[str, object]]) -> dict[str, object]:
    """Pick the best qualified candidate.

    Ranking: highest ``covered_tests`` first (a candidate that ``exit(67)``-s on
    fewer large tests is effectively faster), then lowest ``max_time_ms`` as the
    tiebreaker. Both metrics are objective and easy to compute from the candidate
    run records collected in Phase B.
    """
    return sorted(
        qualified,
        key=lambda entry: (-int(entry["covered_tests"]), int(entry["max_time_ms"])),
    )[0]


def _package_with_tests(
    package: PackageSpec, tests: list[PackageTestSpec]
) -> PackageSpec:
    """Return a copy of ``package`` whose ``tests`` field is restricted to ``tests``.

    Used when Phase D drops uncovered tests so the downstream ``_verify_solution``
    pass does not try to judge against missing answer files.
    """
    return PackageSpec(
        root=package.root,
        short_name=package.short_name,
        name=package.name,
        time_limit_ms=package.time_limit_ms,
        memory_limit_bytes=package.memory_limit_bytes,
        input_pattern=package.input_pattern,
        answer_pattern=package.answer_pattern,
        generator_source=package.generator_source,
        validator_source=package.validator_source,
        checker_source=package.checker_source,
        tests=tests,
        solutions=package.solutions,
        format=package.format,
        generators=package.generators,
    )


def _normalize_extra_report(extra: dict[str, object]) -> dict[str, object]:
    """Convert the election extras dict into a JSON-friendly shape.

    The compile-result entries (``bruteforce_compile``, ``candidate_compile``) are
    received as ``dict[source_path -> CommandResult]`` so the verifier can keep
    typed CommandResult objects until the very last step. Here we turn them into
    plain dicts so ``json.dumps`` can handle the report.
    """
    out = dict(extra)
    for key in ("bruteforce_compile", "candidate_compile"):
        if key in out and isinstance(out[key], dict):
            converted: dict[str, object] = {}
            for path, value in out[key].items():
                if isinstance(value, CommandResult):
                    converted[path] = value.to_dict()
                else:
                    converted[path] = value
            out[key] = converted
    return out


__all__ = [
    "_bruteforce_vote_summary",
    "_claimed_complexity_for_spec",
    "_elect_best",
    "_expected_verdict_for_tag",
    "_is_bruteforce_tag",
    "_majority_vote_truth",
    "_normalize_extra_report",
    "_normalize_tokens",
    "_package_with_tests",
    "_preview_output",
    "_qualify_candidates",
    "_write_disagreement_report",
]
