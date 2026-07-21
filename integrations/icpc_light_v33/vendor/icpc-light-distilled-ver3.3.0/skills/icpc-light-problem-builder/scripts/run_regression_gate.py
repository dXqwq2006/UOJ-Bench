#!/usr/bin/env python3
"""Execute the canonical ICPC Light regression gate.

The input is a deliberately small, declarative JSON plan.  This program never
executes plan text through a shell.  It compiles all programs into a temporary
directory, executes the differential/release/wrong-route matrix, and atomically
writes a hash-bound machine receipt on both success and failure.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from regression_backend import (
    DatasetInvocation,
    PreparedProgram,
    ProgramDatasetBackend,
    ProgramResult,
    create_backend,
    process_succeeded,
)
from statement_resources import (
    StatementResourceError,
    StatementResources,
    load_statement_resources,
)


SCHEMA_VERSION = 1
PLAN_SCHEMA_VERSION = 3
RESOURCE_POLICY_SCHEMA_VERSION = 1
GATE_NAME = "icpc-light-regression-machine"
DEFAULT_PLAN = "audit/regression-plan.json"
DEFAULT_RECEIPT = "audit/regression-machine.json"
PRODUCTION_RANDOM_MINIMUM = 5000
PRODUCTION_COMPILE_TIMEOUT_SECONDS = 120.0
CPP_SUFFIXES = {".cc", ".cpp", ".cxx"}
FORBIDDEN_PACKAGE_COMPONENT_PREFIXES = ("audit", "blind", "private")
FORBIDDEN_PACKAGE_COMPONENT_NAMES = {
    ".DS_Store",
    ".git",
    ".idea",
    ".vscode",
    "__pycache__",
}
FORBIDDEN_PACKAGE_FILE_SUFFIXES = {
    ".bak",
    ".log",
    ".orig",
    ".rej",
    ".swo",
    ".swp",
    ".tmp",
}
PACKAGE_METADATA_TEXT_SUFFIXES = {
    ".c",
    ".cc",
    ".conf",
    ".cpp",
    ".cxx",
    ".h",
    ".hpp",
    ".json",
    ".md",
    ".py",
    ".sh",
    ".txt",
    ".yaml",
    ".yml",
}
COMMON_SECRET_PATTERNS = (
    ("private-key material", re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")),
    ("OpenAI-style API key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("GitHub-style token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    (
        "bearer authorization value",
        re.compile(r"(?i)\bauthorization\s*:\s*bearer\s+[^\s]+"),
    ),
)
OPERATOR_METADATA_PATTERNS = (
    (
        "operator-local absolute path",
        re.compile(r"(?:/Users/[^/\s]+/|/home/[^/\s]+/|[A-Za-z]:\\Users\\[^\\\s]+\\)"),
    ),
    (
        "private workflow path/name",
        re.compile(r"(?i)(?:audit/private|blind-solves|wrong-solutions)"),
    ),
)
CANONICAL_SAMPLE_MANIFEST = "package/samples/manifest.json"
DEFAULT_TESTLIB_CHECKER_CONTRACT = {
    "name": "testlib",
    "accepted_exit_codes": [0],
    "wrong_answer_exit_codes": [1],
    "presentation_error_exit_codes": [2],
}


class GateError(RuntimeError):
    """A closed-gate validation or execution failure."""


@dataclass(frozen=True)
class SourceSpec:
    role: str
    rel: str
    path: Path


@dataclass(frozen=True)
class GeneratorSpec:
    source: SourceSpec
    args: tuple[str, ...]


@dataclass(frozen=True)
class DifferentialSpec:
    mode: str
    generator: GeneratorSpec
    start: int
    count: int
    placeholder: str


@dataclass(frozen=True)
class ReleaseTest:
    test_id: str
    input_rel: str
    input_path: Path
    answer_rel: str | None
    answer_path: Path | None
    limit_tags: tuple[str, ...]


@dataclass(frozen=True)
class SampleCase:
    sample_id: str
    statement_ordinal: int
    input_rel: str
    input_path: Path
    input_sha256: str
    answer_rel: str
    answer_path: Path
    answer_sha256: str


@dataclass(frozen=True)
class FixedInputSpec:
    label: str
    rel: str
    path: Path
    expected_output: bytes | None = None


@dataclass(frozen=True)
class FixedInputOutcome:
    record: dict[str, Any]
    reference_output: bytes | None
    error: str | None


@dataclass(frozen=True)
class CheckerVerdictContract:
    name: str
    accepted_exit_codes: tuple[int, ...]
    wrong_answer_exit_codes: tuple[int, ...]
    presentation_error_exit_codes: tuple[int, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "accepted_exit_codes": list(self.accepted_exit_codes),
            "wrong_answer_exit_codes": list(self.wrong_answer_exit_codes),
            "presentation_error_exit_codes": list(self.presentation_error_exit_codes),
            "unknown_exit_code_verdict": "infrastructure-error",
        }


@dataclass(frozen=True)
class OracleContract:
    independent_from_std: bool
    independence_basis: str
    applicability: str


@dataclass(frozen=True)
class ResourcePolicy:
    statement_resources: StatementResources
    design_basis: dict[str, str]
    policy_sha256: str

    @property
    def time_limit_ms(self) -> int:
        return self.statement_resources.time_limit_ms

    @property
    def memory_limit_mib(self) -> int:
        return self.statement_resources.memory_limit_mib

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": RESOURCE_POLICY_SCHEMA_VERSION,
            "statement_resources": self.statement_resources.as_dict(),
            "design_basis": dict(self.design_basis),
            "policy_sha256": self.policy_sha256,
        }


@dataclass(frozen=True)
class WrongRoute:
    route_id: str
    source: SourceSpec
    ordinary_input: tuple[str, Path]
    breaker_input: tuple[str, Path]
    survivability_inputs: tuple["SurvivabilityInput", ...]
    expected_verdict: str


@dataclass(frozen=True)
class SurvivabilityInput:
    kind: str
    rel: str
    path: Path


@dataclass(frozen=True)
class AcceptedAlternative:
    alternative_id: str
    source: SourceSpec
    normalized_source_sha256: str
    independence_basis: str


@dataclass(frozen=True)
class AcceptedAlternativeAudit:
    programs: tuple[AcceptedAlternative, ...]
    waiver: dict[str, str] | None


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def cpp_normalized_source_sha256(path: Path) -> str:
    """Hash a conservative C++ preprocessing-token sequence.

    Comments and whitespace between tokens disappear, but whitespace that
    changes tokenization does not. This remains a trivial-clone detector rather
    than proof of algorithmic independence.
    """

    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise GateError(f"cannot normalize C++ source {path}: {exc}") from exc
    # Backslash-newline splicing precedes tokenization in C++ translation.
    text = re.sub(r"\\\r?\n", "", text)
    tokens: list[str] = []
    punctuators = tuple(
        sorted(
            {
                "%:%:",
                "<<=", ">>=", "<=>", "...", "->*",
                "::", ".*", "->", "++", "--", "<<", ">>", "<=", ">=",
                "==", "!=", "&&", "||", "*=", "/=", "%=", "+=", "-=",
                "&=", "^=", "|=", "##", "<:", ":>", "<%", "%>", "%:",
                "{", "}", "[", "]", "(", ")", "#", ";", ":", "?", ".",
                "+", "-", "*", "/", "%", "^", "&", "|", "~", "!", "=",
                "<", ">", ",",
            },
            key=len,
            reverse=True,
        )
    )
    raw_prefixes = ("u8R\"", "uR\"", "UR\"", "LR\"", "R\"")
    quoted_prefixes = (
        "u8\"", "u\"", "U\"", "L\"", "\"",
        "u8'", "u'", "U'", "L'", "'",
    )
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char.isspace():
            index += 1
            continue
        if text.startswith("//", index):
            newline = text.find("\n", index + 2)
            index = length if newline < 0 else newline + 1
            continue
        if text.startswith("/*", index):
            end = text.find("*/", index + 2)
            if end < 0:
                raise GateError(f"unterminated block comment in C++ source {path}")
            index = end + 2
            continue
        raw_prefix = next(
            (prefix for prefix in raw_prefixes if text.startswith(prefix, index)),
            None,
        )
        if raw_prefix is not None:
            delimiter_start = index + len(raw_prefix)
            delimiter_end = text.find(
                "(", delimiter_start, min(length, delimiter_start + 17)
            )
            if delimiter_end < 0:
                raise GateError(f"invalid raw string delimiter in C++ source {path}")
            delimiter = text[delimiter_start:delimiter_end]
            terminator = ")" + delimiter + '"'
            literal_end = text.find(terminator, delimiter_end + 1)
            if literal_end < 0:
                raise GateError(f"unterminated raw string in C++ source {path}")
            stop = literal_end + len(terminator)
            tokens.append(text[index:stop])
            index = stop
            continue
        quoted_prefix = next(
            (prefix for prefix in quoted_prefixes if text.startswith(prefix, index)),
            None,
        )
        if quoted_prefix is not None:
            quote = quoted_prefix[-1]
            start = index
            index += len(quoted_prefix)
            escaped = False
            while index < length:
                current = text[index]
                index += 1
                if escaped:
                    escaped = False
                elif current == "\\":
                    escaped = True
                elif current == quote:
                    break
            else:
                raise GateError(f"unterminated literal in C++ source {path}")
            tokens.append(text[start:index])
            continue
        if char == "_" or char.isalpha() or ord(char) >= 128:
            start = index
            index += 1
            while index < length and (
                text[index] == "_"
                or text[index].isalnum()
                or ord(text[index]) >= 128
            ):
                index += 1
            tokens.append(text[start:index])
            continue
        if char.isdigit() or (
            char == "." and index + 1 < length and text[index + 1].isdigit()
        ):
            start = index
            index += 1
            while index < length:
                current = text[index]
                if current.isalnum() or current in {"_", "."}:
                    index += 1
                    continue
                if (
                    current == "'"
                    and index + 1 < length
                    and (text[index + 1].isalnum() or text[index + 1] == "_")
                ):
                    index += 1
                    continue
                if current in {"+", "-"} and text[index - 1] in "eEpP":
                    index += 1
                    continue
                break
            tokens.append(text[start:index])
            continue
        # C++ maximal munch has one explicit preprocessing-token exception:
        # in ``<::X`` (where X is neither ':' nor '>'), '<' is its own token.
        # Without it, adding whitespace in ``< ::X`` would change this
        # normalizer even though the compiler sees the same token sequence.
        if text.startswith("<::", index) and (
            index + 3 >= length or text[index + 3] not in {":", ">"}
        ):
            punctuator = "<"
        else:
            punctuator = next(
                (value for value in punctuators if text.startswith(value, index)),
                None,
            )
        if punctuator is None:
            # Preserve an unknown source character as its own token. Compilation
            # will decide whether it is legal; clone detection must not merge it.
            punctuator = char
        tokens.append(punctuator)
        index += len(punctuator)
    digest = hashlib.sha256()
    for token in tokens:
        encoded = token.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.hexdigest()


def token_sha256(data: bytes) -> str:
    digest = hashlib.sha256()
    for token in data.split():
        digest.update(len(token).to_bytes(8, "big"))
        digest.update(token)
    return digest.hexdigest()


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def preview_bytes(data: bytes, limit: int = 1000) -> str:
    clipped = data[:limit].decode("utf-8", errors="replace")
    return clipped + ("\n... truncated ..." if len(data) > limit else "")


def require_plain_int(value: Any, label: str, *, minimum: int | None = None) -> int:
    if type(value) is not int:
        raise GateError(f"{label} must be an integer")
    if minimum is not None and value < minimum:
        raise GateError(f"{label} must be at least {minimum}")
    return value


def require_exit_codes(value: Any, label: str) -> tuple[int, ...]:
    if not isinstance(value, list) or not value:
        raise GateError(f"{label} must be a non-empty exit-code array")
    codes: list[int] = []
    for index, raw in enumerate(value):
        code = require_plain_int(raw, f"{label}[{index}]", minimum=0)
        if code > 255:
            raise GateError(f"{label}[{index}] must be at most 255")
        if code in codes:
            raise GateError(f"{label} contains duplicate exit code {code}")
        codes.append(code)
    return tuple(codes)


def load_checker_contract(plan: dict[str, Any]) -> CheckerVerdictContract:
    raw = plan.get("checker_verdict_contract", DEFAULT_TESTLIB_CHECKER_CONTRACT)
    if not isinstance(raw, dict):
        raise GateError("checker_verdict_contract must be an object")
    name = require_nonempty_string(raw.get("name"), "checker_verdict_contract.name")
    accepted = require_exit_codes(
        raw.get("accepted_exit_codes"),
        "checker_verdict_contract.accepted_exit_codes",
    )
    wrong = require_exit_codes(
        raw.get("wrong_answer_exit_codes"),
        "checker_verdict_contract.wrong_answer_exit_codes",
    )
    presentation = require_exit_codes(
        raw.get("presentation_error_exit_codes"),
        "checker_verdict_contract.presentation_error_exit_codes",
    )
    if set(accepted) & (set(wrong) | set(presentation)) or set(wrong) & set(presentation):
        raise GateError("checker verdict exit-code classes must be disjoint")
    if 0 not in accepted:
        raise GateError("checker accepted_exit_codes must include 0")
    return CheckerVerdictContract(name, accepted, wrong, presentation)


def load_sample_manifest(problem_dir: Path) -> tuple[str, Path, list[SampleCase]]:
    manifest_rel, manifest_path = resolve_file(
        problem_dir, CANONICAL_SAMPLE_MANIFEST, "canonical sample manifest"
    )
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise GateError(f"cannot parse canonical sample manifest: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise GateError("canonical sample manifest must use schema_version 1")
    statement_rel, statement_path = resolve_file(
        problem_dir, "statement.md", "contestant statement"
    )
    if raw.get("statement_path") != statement_rel:
        raise GateError("canonical sample manifest.statement_path must be 'statement.md'")
    if raw.get("statement_sha256") != sha256_file(statement_path):
        raise GateError(
            "canonical sample manifest.statement_sha256 does not bind the current statement"
        )
    entries = raw.get("samples")
    if not isinstance(entries, list) or not entries:
        raise GateError("canonical sample manifest.samples must be non-empty")
    cases: list[SampleCase] = []
    seen_ids: set[str] = set()
    seen_ordinals: set[int] = set()
    seen_paths: set[str] = set()
    for index, item in enumerate(entries):
        label = f"canonical sample manifest.samples[{index}]"
        if not isinstance(item, dict):
            raise GateError(f"{label} must be an object")
        sample_id = require_nonempty_string(item.get("sample_id"), f"{label}.sample_id")
        ordinal = require_plain_int(
            item.get("statement_ordinal"), f"{label}.statement_ordinal", minimum=1
        )
        if sample_id in seen_ids or ordinal in seen_ordinals:
            raise GateError(f"{label} duplicates a sample ID or statement ordinal")
        seen_ids.add(sample_id)
        seen_ordinals.add(ordinal)
        input_rel, input_path = resolve_file(problem_dir, item.get("input"), f"{label}.input")
        answer_rel, answer_path = resolve_file(
            problem_dir, item.get("answer"), f"{label}.answer"
        )
        if not input_rel.startswith("package/samples/") or input_path.suffix != ".in":
            raise GateError(f"{label}.input must be package/samples/*.in")
        if not answer_rel.startswith("package/samples/") or answer_path.suffix != ".ans":
            raise GateError(f"{label}.answer must be package/samples/*.ans")
        if input_path.with_suffix(".ans") != answer_path:
            raise GateError(f"{label} input/answer must be a same-stem pair")
        if input_rel in seen_paths or answer_rel in seen_paths:
            raise GateError(f"{label} reuses a sample file")
        seen_paths.update((input_rel, answer_rel))
        input_digest = require_nonempty_string(item.get("input_sha256"), f"{label}.input_sha256")
        answer_digest = require_nonempty_string(item.get("answer_sha256"), f"{label}.answer_sha256")
        if input_digest != sha256_file(input_path) or answer_digest != sha256_file(answer_path):
            raise GateError(f"{label} hash does not match its current sample files")
        cases.append(
            SampleCase(
                sample_id,
                ordinal,
                input_rel,
                input_path,
                input_digest,
                answer_rel,
                answer_path,
                answer_digest,
            )
        )
    if sorted(seen_ordinals) != list(range(1, len(cases) + 1)):
        raise GateError("canonical sample statement ordinals must be contiguous from 1")
    samples_root = problem_dir / "package/samples"
    actual = {
        path.relative_to(problem_dir).as_posix()
        for path in samples_root.rglob("*")
        if path.is_file() and path != manifest_path
    }
    if any(path.is_symlink() for path in samples_root.rglob("*")):
        raise GateError("package/samples must not contain symbolic links")
    if actual != seen_paths:
        raise GateError(
            "canonical sample manifest must enumerate every package/samples data file; "
            f"missing={sorted(actual - seen_paths)}, extra={sorted(seen_paths - actual)}"
        )
    return manifest_rel, manifest_path, sorted(cases, key=lambda item: item.statement_ordinal)


def require_nonempty_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise GateError(f"{label} must be a non-empty string")
    return value


def load_resource_policy(
    plan: dict[str, Any], statement_resources: StatementResources
) -> ResourcePolicy:
    raw = plan.get("resource_policy")
    if not isinstance(raw, dict):
        raise GateError("resource_policy must be an object")
    expected_keys = {
        "schema_version",
        "statement_resources",
        "design_basis",
        "policy_sha256",
    }
    if set(raw) != expected_keys:
        raise GateError(
            "resource_policy must contain exactly schema_version, "
            "statement_resources, design_basis, and policy_sha256"
        )
    if raw.get("schema_version") != RESOURCE_POLICY_SCHEMA_VERSION:
        raise GateError("resource_policy.schema_version must be integer 1")
    if raw.get("statement_resources") != statement_resources.as_dict():
        raise GateError(
            "resource_policy.statement_resources does not match current statement.md"
        )
    design_raw = raw.get("design_basis")
    required_design_fields = (
        "intended_complexity",
        "maximum_scale",
        "time_limit_rationale",
        "memory_limit_rationale",
    )
    if not isinstance(design_raw, dict) or set(design_raw) != set(
        required_design_fields
    ):
        raise GateError(
            "resource_policy.design_basis must contain exactly "
            + ", ".join(required_design_fields)
        )
    design_basis = {
        key: require_nonempty_string(
            design_raw.get(key), f"resource_policy.design_basis.{key}"
        ).strip()
        for key in required_design_fields
    }
    hash_payload = {
        "schema_version": RESOURCE_POLICY_SCHEMA_VERSION,
        "statement_resources": statement_resources.as_dict(),
        "design_basis": design_basis,
    }
    expected_sha256 = sha256_bytes(canonical_json_bytes(hash_payload))
    if raw.get("policy_sha256") != expected_sha256:
        raise GateError("resource_policy.policy_sha256 does not match its content")
    return ResourcePolicy(statement_resources, design_basis, expected_sha256)


def safe_problem_path(
    problem_dir: Path,
    raw: Any,
    *,
    label: str,
    require_exists: bool,
) -> tuple[str, Path]:
    value = require_nonempty_string(raw, label)
    if "\\" in value:
        raise GateError(f"{label} must use normalized POSIX path syntax")
    pure = PurePosixPath(value)
    if pure.is_absolute() or pure == PurePosixPath(".") or ".." in pure.parts:
        raise GateError(f"{label} must stay below the problem directory")
    if any(part.startswith(".") for part in pure.parts):
        raise GateError(f"{label} must not contain hidden path components")
    normalized = pure.as_posix()
    if normalized != value:
        raise GateError(f"{label} must use normalized POSIX path syntax")
    current = problem_dir
    for part in pure.parts:
        current /= part
        if current.is_symlink():
            raise GateError(f"{label} traverses a symbolic link: {normalized}")
        if not current.exists():
            break
    path = problem_dir.joinpath(*pure.parts)
    try:
        path.resolve(strict=False).relative_to(problem_dir)
    except (OSError, RuntimeError, ValueError) as exc:
        raise GateError(f"{label} resolves outside the problem directory") from exc
    if require_exists and not path.exists():
        raise GateError(f"{label} does not exist: {normalized}")
    return normalized, path


def require_regular_file(path: Path, label: str) -> None:
    if path.is_symlink() or not path.is_file():
        raise GateError(f"{label} must be a non-symlink regular file: {path}")
    try:
        if path.stat().st_size == 0:
            raise GateError(f"{label} must not be empty: {path}")
    except OSError as exc:
        raise GateError(f"cannot stat {label}: {exc}") from exc


def resolve_file(problem_dir: Path, raw: Any, label: str) -> tuple[str, Path]:
    rel, path = safe_problem_path(
        problem_dir, raw, label=label, require_exists=True
    )
    require_regular_file(path, label)
    return rel, path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run differential, release-test, wrong-route, validator, checker, "
            "and privacy gates and write audit/regression-machine.json."
        )
    )
    parser.add_argument("--problem-dir", type=Path, required=True)
    parser.add_argument("--plan", default=DEFAULT_PLAN)
    parser.add_argument("--receipt-out", default=DEFAULT_RECEIPT)
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--execution-backend",
        choices=("lightcpverifier", "local"),
        default="lightcpverifier",
        help=(
            "Program x Dataset execution backend. Production defaults to the "
            "sandboxed LightCPVerifier service; local is testing-only."
        ),
    )
    parser.add_argument(
        "--lightcpverifier-url",
        default=os.environ.get(
            "ICPC_LIGHT_LIGHTCPVERIFIER_URL", "http://127.0.0.1:8081"
        ),
        help="Base URL for --execution-backend lightcpverifier.",
    )
    parser.add_argument(
        "--test-mode",
        action="store_true",
        help="Mark the receipt non-production and allow testing-only overrides.",
    )
    parser.add_argument(
        "--min-random-count",
        type=int,
        help="Testing-only replacement for the production minimum of 5000.",
    )
    parser.add_argument(
        "--program-timeout-seconds",
        type=float,
        help="Testing-only per-process timeout override.",
    )
    parser.add_argument(
        "--compile-timeout-seconds",
        type=float,
        help="Testing-only per-source compilation timeout override.",
    )
    args = parser.parse_args()
    if not args.problem_dir.exists() or not args.problem_dir.is_dir():
        parser.error(f"problem directory is not an existing directory: {args.problem_dir}")
    if args.problem_dir.is_symlink():
        parser.error("--problem-dir itself must not be a symbolic link")
    args.problem_dir = args.problem_dir.resolve()
    overrides = (
        args.min_random_count,
        args.program_timeout_seconds,
        args.compile_timeout_seconds,
    )
    if any(value is not None for value in overrides) and not args.test_mode:
        parser.error("testing-only threshold/timeout overrides require --test-mode")
    if args.execution_backend == "local" and not args.test_mode:
        parser.error("--execution-backend local requires --test-mode")
    if args.min_random_count is not None and args.min_random_count < 1:
        parser.error("--min-random-count must be positive")
    for name in ("program_timeout_seconds", "compile_timeout_seconds"):
        value = getattr(args, name)
        if value is not None and value <= 0:
            parser.error(f"--{name.replace('_', '-')} must be positive")
    return args


def load_plan(
    problem_dir: Path, plan_rel: str, plan_path: Path, random_minimum: int
) -> tuple[
    dict[str, Any],
    DifferentialSpec,
    list[ReleaseTest],
    list[WrongRoute],
    AcceptedAlternativeAudit,
    tuple[str, ...],
    tuple[str, Path],
    list[SampleCase],
    CheckerVerdictContract,
    OracleContract,
    ResourcePolicy,
]:
    require_regular_file(plan_path, "regression plan")
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise GateError(f"cannot parse {plan_rel}: {exc}") from exc
    if not isinstance(plan, dict):
        raise GateError("regression plan root must be an object")
    if plan.get("schema_version") != PLAN_SCHEMA_VERSION:
        raise GateError(
            f"regression plan schema_version must be integer {PLAN_SCHEMA_VERSION}"
        )
    try:
        statement_resources = load_statement_resources(problem_dir)
    except StatementResourceError as exc:
        raise GateError(f"statement resource policy is invalid: {exc}") from exc
    resource_policy = load_resource_policy(plan, statement_resources)
    if plan.get("sample_manifest") != CANONICAL_SAMPLE_MANIFEST:
        raise GateError(
            f"sample_manifest must be exactly {CANONICAL_SAMPLE_MANIFEST!r}"
        )
    sample_manifest_rel, sample_manifest_path, samples = load_sample_manifest(problem_dir)
    checker_contract = load_checker_contract(plan)
    oracle_raw = plan.get("oracle")
    if not isinstance(oracle_raw, dict):
        raise GateError("oracle must be an object")
    if oracle_raw.get("source") != "package/brute.cpp":
        raise GateError("oracle.source must be exactly package/brute.cpp")
    if oracle_raw.get("independent_from_std") is not True:
        raise GateError("oracle.independent_from_std must be true")
    oracle_contract = OracleContract(
        True,
        require_nonempty_string(
            oracle_raw.get("independence_basis"), "oracle.independence_basis"
        ),
        require_nonempty_string(oracle_raw.get("applicability"), "oracle.applicability"),
    )

    required_tags_raw = plan.get("required_limit_tags")
    if not isinstance(required_tags_raw, list) or not required_tags_raw:
        raise GateError("required_limit_tags must be a non-empty string array")
    required_limit_tags: list[str] = []
    for index, raw in enumerate(required_tags_raw):
        tag = require_nonempty_string(raw, f"required_limit_tags[{index}]")
        if tag in required_limit_tags:
            raise GateError(f"duplicate required limit tag: {tag}")
        required_limit_tags.append(tag)

    differential_raw = plan.get("differential")
    if not isinstance(differential_raw, dict):
        raise GateError("differential must be an object")
    mode = differential_raw.get("mode")
    if mode not in {"tiny-exhaustive", "random-seeds"}:
        raise GateError("differential.mode must be tiny-exhaustive or random-seeds")
    generator_raw = differential_raw.get("generator")
    if not isinstance(generator_raw, dict):
        raise GateError("differential.generator must be an object")
    gen_rel, gen_path = resolve_file(
        problem_dir,
        generator_raw.get("source"),
        "differential.generator.source",
    )
    if not gen_rel.startswith("package/generators/"):
        raise GateError("differential.generator.source must stay below package/generators/")
    if gen_path.suffix.lower() not in CPP_SUFFIXES:
        raise GateError("differential.generator.source must be C++")
    raw_args = generator_raw.get("args")
    if not isinstance(raw_args, list) or not raw_args:
        raise GateError("differential.generator.args must be a non-empty token array")
    args: list[str] = []
    for index, raw in enumerate(raw_args):
        token = require_nonempty_string(raw, f"differential.generator.args[{index}]")
        args.append(token)
    placeholder = "{case_index}" if mode == "tiny-exhaustive" else "{seed}"
    if not any(placeholder in token for token in args):
        raise GateError(f"generator args must contain {placeholder}")
    for token in args:
        remainder = token.replace(placeholder, "")
        if "{" in remainder or "}" in remainder:
            raise GateError("generator args contain an unsupported placeholder")
    count_minimum = 1 if mode == "tiny-exhaustive" else random_minimum
    count = require_plain_int(
        differential_raw.get("count"), "differential.count", minimum=count_minimum
    )
    start_key = "case_index_start" if mode == "tiny-exhaustive" else "seed_start"
    start = differential_raw.get(start_key, 0 if mode == "tiny-exhaustive" else 1)
    start = require_plain_int(start, f"differential.{start_key}", minimum=0)
    differential = DifferentialSpec(
        mode=mode,
        generator=GeneratorSpec(
            source=SourceSpec("generator", gen_rel, gen_path), args=tuple(args)
        ),
        start=start,
        count=count,
        placeholder=placeholder,
    )

    release_raw = plan.get("release_tests")
    if not isinstance(release_raw, list) or not release_raw:
        raise GateError("release_tests must be a non-empty array")
    releases: list[ReleaseTest] = []
    seen_test_ids: set[str] = set()
    seen_test_inputs: set[str] = set()
    for index, item in enumerate(release_raw):
        label = f"release_tests[{index}]"
        if not isinstance(item, dict):
            raise GateError(f"{label} must be an object")
        test_id = require_nonempty_string(item.get("test_id"), f"{label}.test_id")
        if test_id in seen_test_ids:
            raise GateError(f"duplicate release test_id: {test_id}")
        seen_test_ids.add(test_id)
        input_rel, input_path = resolve_file(
            problem_dir, item.get("input"), f"{label}.input"
        )
        if input_path.suffix != ".in":
            raise GateError(f"{label}.input must have the .in suffix")
        if not input_rel.startswith("package/tests/"):
            raise GateError(f"{label}.input must stay below package/tests/")
        if input_rel in seen_test_inputs:
            raise GateError(f"duplicate release input: {input_rel}")
        seen_test_inputs.add(input_rel)
        answer_rel, answer_path = resolve_file(
            problem_dir, item.get("answer"), f"{label}.answer"
        )
        if answer_path.suffix != ".ans":
            raise GateError(f"{label}.answer must have the .ans suffix")
        if not answer_rel.startswith("package/tests/"):
            raise GateError(f"{label}.answer must stay below package/tests/")
        if input_path.with_suffix(".ans") != answer_path:
            raise GateError(f"{label} input/answer must be a same-stem pair")
        tags_raw = item.get("limit_tags")
        if not isinstance(tags_raw, list):
            raise GateError(f"{label}.limit_tags must be a string array")
        tags: list[str] = []
        for tag_index, raw_tag in enumerate(tags_raw):
            tag = require_nonempty_string(
                raw_tag, f"{label}.limit_tags[{tag_index}]"
            )
            if tag in tags:
                raise GateError(f"{label}.limit_tags duplicates {tag!r}")
            tags.append(tag)
        unknown = set(tags) - set(required_limit_tags)
        if unknown:
            raise GateError(f"{label}.limit_tags contains unknown tags: {sorted(unknown)}")
        releases.append(
            ReleaseTest(
                test_id,
                input_rel,
                input_path,
                answer_rel,
                answer_path,
                tuple(tags),
            )
        )
    covered_tags = {tag for release in releases for tag in release.limit_tags}
    missing_tags = set(required_limit_tags) - covered_tags
    if missing_tags:
        raise GateError(
            f"release_tests do not cover required limit tags: {sorted(missing_tags)}"
        )
    tests_root = problem_dir / "package/tests"
    if tests_root.is_symlink() or not tests_root.is_dir():
        raise GateError("package/tests must be a non-symlink directory")
    actual_release_inputs: set[str] = set()
    actual_release_answers: set[str] = set()
    for path in sorted(tests_root.rglob("*")):
        if path.is_symlink():
            raise GateError(f"package/tests contains a symbolic link: {path}")
        if path.is_file() and path.suffix == ".in":
            actual_release_inputs.add(path.relative_to(problem_dir).as_posix())
        if path.is_file() and path.suffix == ".ans":
            actual_release_answers.add(path.relative_to(problem_dir).as_posix())
    if seen_test_inputs != actual_release_inputs:
        missing = sorted(actual_release_inputs - seen_test_inputs)
        extra = sorted(seen_test_inputs - actual_release_inputs)
        raise GateError(
            "release_tests must enumerate every package/tests .in exactly once; "
            f"missing={missing}, extra={extra}"
        )
    seen_test_answers = {release.answer_rel for release in releases}
    if seen_test_answers != actual_release_answers:
        missing = sorted(actual_release_answers - seen_test_answers)
        extra = sorted(seen_test_answers - actual_release_answers)
        raise GateError(
            "release_tests must enumerate every package/tests .ans exactly once; "
            f"missing={missing}, extra={extra}"
        )

    wrong_raw = plan.get("wrong_routes")
    if not isinstance(wrong_raw, list) or not wrong_raw:
        raise GateError("wrong_routes must be a non-empty array of qualified routes")
    wrongs: list[WrongRoute] = []
    seen_route_ids: set[str] = set()
    seen_wrong_sources: set[str] = set()
    for index, item in enumerate(wrong_raw):
        label = f"wrong_routes[{index}]"
        if not isinstance(item, dict):
            raise GateError(f"{label} must be an object")
        route_id = require_nonempty_string(item.get("route_id"), f"{label}.route_id")
        if route_id in seen_route_ids:
            raise GateError(f"duplicate wrong route_id: {route_id}")
        seen_route_ids.add(route_id)
        source_rel, source_path = resolve_file(
            problem_dir, item.get("source"), f"{label}.source"
        )
        if not source_rel.startswith("audit/private/wrong-solutions/"):
            raise GateError(
                f"{label}.source must be below audit/private/wrong-solutions/"
            )
        if source_path.suffix.lower() not in CPP_SUFFIXES:
            raise GateError(f"{label}.source must be C++")
        if source_rel in seen_wrong_sources:
            raise GateError(f"wrong source reused by multiple routes: {source_rel}")
        seen_wrong_sources.add(source_rel)
        sample_raw = item.get("sample_inputs")
        canonical_sample_inputs = [sample.input_rel for sample in samples]
        if sample_raw != canonical_sample_inputs:
            raise GateError(
                f"{label}.sample_inputs must equal every canonical sample input in order"
            )
        ordinary = resolve_file(
            problem_dir, item.get("ordinary_input"), f"{label}.ordinary_input"
        )
        if not ordinary[0].startswith("package/tests/ordinary/") or not ordinary[0].endswith(".in"):
            raise GateError(f"{label}.ordinary_input must be package/tests/ordinary/*.in")
        breaker = resolve_file(
            problem_dir, item.get("breaker_input"), f"{label}.breaker_input"
        )
        if not breaker[0].startswith("package/tests/breakers/") or not breaker[0].endswith(".in"):
            raise GateError(f"{label}.breaker_input must be package/tests/breakers/*.in")
        survivability_raw = item.get("survivability_inputs")
        if not isinstance(survivability_raw, list) or not survivability_raw:
            raise GateError(
                f"{label}.survivability_inputs must be a non-empty array"
            )
        survivability: list[SurvivabilityInput] = []
        seen_survivability_inputs: set[str] = set()
        seen_survivability_kinds: set[str] = set()
        allowed_survivability_kinds = {
            "small",
            "random",
            "structured",
            "resource",
        }
        for survival_index, survival_raw in enumerate(survivability_raw):
            survival_label = f"{label}.survivability_inputs[{survival_index}]"
            if not isinstance(survival_raw, dict):
                raise GateError(f"{survival_label} must be an object")
            kind = require_nonempty_string(
                survival_raw.get("kind"), f"{survival_label}.kind"
            ).lower()
            if kind not in allowed_survivability_kinds:
                raise GateError(
                    f"{survival_label}.kind must be small, random, structured, "
                    "or resource"
                )
            survival_rel, survival_path = resolve_file(
                problem_dir, survival_raw.get("input"), f"{survival_label}.input"
            )
            if not survival_rel.startswith("package/tests/") or not survival_rel.endswith(
                ".in"
            ):
                raise GateError(
                    f"{survival_label}.input must be a package/tests/*.in file"
                )
            if survival_rel in seen_survivability_inputs:
                raise GateError(
                    f"{label}.survivability_inputs duplicates {survival_rel!r}"
                )
            if survival_rel == breaker[0]:
                raise GateError(
                    f"{survival_label}.input must not reuse the breaker input"
                )
            seen_survivability_inputs.add(survival_rel)
            seen_survivability_kinds.add(kind)
            survivability.append(SurvivabilityInput(kind, survival_rel, survival_path))
        required_survivability_kinds = {"small", "random", "structured"}
        missing_survivability_kinds = (
            required_survivability_kinds - seen_survivability_kinds
        )
        if missing_survivability_kinds:
            raise GateError(
                f"{label}.survivability_inputs lacks required kinds: "
                f"{sorted(missing_survivability_kinds)}"
            )
        expected = require_nonempty_string(
            item.get("expected_verdict"), f"{label}.expected_verdict"
        ).upper()
        if expected not in {"WA", "TLE", "MLE", "OLE", "RE"}:
            raise GateError(
                f"{label}.expected_verdict must be WA, TLE, MLE, OLE, or RE"
            )
        wrongs.append(
            WrongRoute(
                route_id=route_id,
                source=SourceSpec(f"wrong:{route_id}", source_rel, source_path),
                ordinary_input=ordinary,
                breaker_input=breaker,
                survivability_inputs=tuple(survivability),
                expected_verdict=expected,
            )
        )

    alternatives_raw = plan.get("accepted_alternatives", [])
    if not isinstance(alternatives_raw, list):
        raise GateError("accepted_alternatives must be an array when present")
    alternatives: list[AcceptedAlternative] = []
    seen_alternative_ids: set[str] = set()
    seen_alternative_sources: set[str] = set()
    seen_alternative_hashes: set[str] = set()
    seen_alternative_normalized_hashes: set[str] = set()
    std_source_rel, std_source_path = resolve_file(
        problem_dir, "package/std.cpp", "package/std.cpp"
    )
    del std_source_rel
    std_source_sha256 = sha256_file(std_source_path)
    std_normalized_source_sha256 = cpp_normalized_source_sha256(std_source_path)
    for index, item in enumerate(alternatives_raw):
        label = f"accepted_alternatives[{index}]"
        if not isinstance(item, dict):
            raise GateError(f"{label} must be an object")
        alternative_id = require_nonempty_string(
            item.get("alternative_id"), f"{label}.alternative_id"
        )
        independence_basis = require_nonempty_string(
            item.get("independence_basis"), f"{label}.independence_basis"
        ).strip()
        if independence_basis.lower() in {
            "-", "none", "n/a", "na", "tbd", "todo", "pending"
        }:
            raise GateError(f"{label}.independence_basis must be concrete")
        if alternative_id in seen_alternative_ids:
            raise GateError(f"duplicate accepted alternative_id: {alternative_id}")
        seen_alternative_ids.add(alternative_id)
        source_rel, source_path = resolve_file(
            problem_dir, item.get("source"), f"{label}.source"
        )
        if not source_rel.startswith("audit/private/accepted-solutions/"):
            raise GateError(
                f"{label}.source must be below audit/private/accepted-solutions/"
            )
        if source_path.suffix.lower() not in CPP_SUFFIXES:
            raise GateError(f"{label}.source must be C++")
        if source_rel in seen_alternative_sources:
            raise GateError(
                f"accepted alternative source reused by multiple programs: {source_rel}"
            )
        seen_alternative_sources.add(source_rel)
        source_sha256 = sha256_file(source_path)
        normalized_source_sha256 = cpp_normalized_source_sha256(source_path)
        if source_sha256 == std_source_sha256:
            raise GateError(
                f"{label}.source must be materially distinct from package/std.cpp"
            )
        if normalized_source_sha256 == std_normalized_source_sha256:
            raise GateError(
                f"{label}.source is a normalized preprocessing-token clone "
                "of package/std.cpp"
            )
        if source_sha256 in seen_alternative_hashes:
            raise GateError(
                f"{label}.source duplicates another accepted alternative by content"
            )
        seen_alternative_hashes.add(source_sha256)
        if normalized_source_sha256 in seen_alternative_normalized_hashes:
            raise GateError(
                f"{label}.source duplicates another alternative after "
                "preprocessing-token normalization"
            )
        seen_alternative_normalized_hashes.add(normalized_source_sha256)
        alternatives.append(
            AcceptedAlternative(
                alternative_id=alternative_id,
                source=SourceSpec(
                    f"accepted:{alternative_id}", source_rel, source_path
                ),
                normalized_source_sha256=normalized_source_sha256,
                independence_basis=independence_basis,
            )
        )

    waiver_raw = plan.get("accepted_alternative_waiver")
    waiver: dict[str, str] | None = None
    if alternatives and waiver_raw is not None:
        raise GateError(
            "accepted_alternative_waiver is only valid when accepted_alternatives is empty"
        )
    if waiver_raw is not None:
        if not isinstance(waiver_raw, dict):
            raise GateError("accepted_alternative_waiver must be an object")
        if waiver_raw.get("status") != "no-known-alternative":
            raise GateError(
                "accepted_alternative_waiver.status must be no-known-alternative"
            )
        waiver = {
            "status": "no-known-alternative",
            "basis": require_nonempty_string(
                waiver_raw.get("basis"), "accepted_alternative_waiver.basis"
            ),
            "search_scope": require_nonempty_string(
                waiver_raw.get("search_scope"),
                "accepted_alternative_waiver.search_scope",
            ),
        }
        for field in ("basis", "search_scope"):
            if waiver[field].strip().lower() in {
                "-",
                "none",
                "n/a",
                "na",
                "tbd",
                "todo",
                "pending",
            }:
                raise GateError(
                    f"accepted_alternative_waiver.{field} must be concrete"
                )
    checker_path = problem_dir / "package/checker.cpp"
    if (checker_path.exists() or checker_path.is_symlink()) and not alternatives:
        if waiver is None:
            raise GateError(
                "a custom-checker plan must execute accepted_alternatives or provide "
                "accepted_alternative_waiver"
            )
    elif waiver is not None:
        raise GateError(
            "accepted_alternative_waiver is only valid for a custom-checker plan"
        )
    accepted_audit = AcceptedAlternativeAudit(tuple(alternatives), waiver)
    return (
        plan,
        differential,
        releases,
        wrongs,
        accepted_audit,
        tuple(required_limit_tags),
        (sample_manifest_rel, sample_manifest_path),
        samples,
        checker_contract,
        oracle_contract,
        resource_policy,
    )


def package_privacy_scan(problem_dir: Path) -> dict[str, Any]:
    package = problem_dir / "package"
    if package.is_symlink() or not package.is_dir():
        raise GateError("package must be a non-symlink directory")
    inventory: list[dict[str, Any]] = []
    forbidden: list[str] = []
    content_findings: list[dict[str, str]] = []
    non_utf8_skipped: list[str] = []
    text_files_scanned = 0
    for path in sorted(package.rglob("*")):
        rel_to_package = path.relative_to(package)
        problem_rel = path.relative_to(problem_dir).as_posix()
        if path.is_symlink():
            forbidden.append(f"{problem_rel}: symbolic link")
            continue
        for component in rel_to_package.parts:
            lowered = component.lower()
            if component in FORBIDDEN_PACKAGE_COMPONENT_NAMES or component.startswith("."):
                forbidden.append(f"{problem_rel}: hidden/development component {component!r}")
                break
            if any(lowered.startswith(prefix) for prefix in FORBIDDEN_PACKAGE_COMPONENT_PREFIXES):
                forbidden.append(f"{problem_rel}: forbidden component {component!r}")
                break
        if path.is_file():
            if path.suffix.lower() in FORBIDDEN_PACKAGE_FILE_SUFFIXES or path.name.endswith("~"):
                forbidden.append(f"{problem_rel}: temporary/debug file")
            inventory.append(
                {
                    "path": problem_rel,
                    "sha256": sha256_file(path),
                    "size": path.stat().st_size,
                }
            )
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                non_utf8_skipped.append(problem_rel)
            except OSError as exc:
                forbidden.append(f"{problem_rel}: cannot read for leak scan: {exc}")
            else:
                text_files_scanned += 1
                for finding, pattern in COMMON_SECRET_PATTERNS:
                    if pattern.search(text):
                        content_findings.append(
                            {"path": problem_rel, "finding": finding}
                        )
                if path.suffix.lower() in PACKAGE_METADATA_TEXT_SUFFIXES:
                    for finding, pattern in OPERATOR_METADATA_PATTERNS:
                        if pattern.search(text):
                            content_findings.append(
                                {"path": problem_rel, "finding": finding}
                            )
        elif not path.is_dir():
            forbidden.append(f"{problem_rel}: unsupported filesystem entry")
    binding = sha256_bytes(canonical_json_bytes(inventory))
    return {
        "status": "passed" if not forbidden and not content_findings else "failed",
        "scan_scope": "package-boundary-and-common-secret-leak-v1",
        "files_scanned": len(inventory),
        "text_files_scanned": text_files_scanned,
        "inventory_binding_sha256": binding,
        "binding_algorithm": "sha256(canonical-json(ordered path,sha256,size records))",
        "forbidden_entries": forbidden,
        "content_findings": content_findings,
        "non_utf8_content_scan_skipped": non_utf8_skipped,
    }


def artifact_bindings(
    *,
    problem_dir: Path,
    differential: DifferentialSpec,
    releases: list[ReleaseTest],
    wrongs: list[WrongRoute],
    accepted_audit: AcceptedAlternativeAudit,
    sample_manifest: tuple[str, Path],
    samples: list[SampleCase],
    has_checker: bool,
    checker_contract: CheckerVerdictContract,
    oracle_contract: OracleContract,
) -> dict[str, Any]:
    paths: dict[str, Path] = {
        "statement.md": problem_dir / "statement.md",
        "package/std.cpp": problem_dir / "package/std.cpp",
        "package/brute.cpp": problem_dir / "package/brute.cpp",
        "package/validator.cpp": problem_dir / "package/validator.cpp",
        differential.generator.source.rel: differential.generator.source.path,
        sample_manifest[0]: sample_manifest[1],
    }
    generators_root = problem_dir / "package/generators"
    for generator_path in generators_root.rglob("*"):
        if generator_path.is_file() and not generator_path.is_symlink():
            paths[generator_path.relative_to(problem_dir).as_posix()] = generator_path
    if has_checker:
        paths["package/checker.cpp"] = problem_dir / "package/checker.cpp"
    for sample in samples:
        paths[sample.input_rel] = sample.input_path
        paths[sample.answer_rel] = sample.answer_path
    for release in releases:
        paths[release.input_rel] = release.input_path
        if release.answer_rel is not None and release.answer_path is not None:
            paths[release.answer_rel] = release.answer_path
    for route in wrongs:
        paths[route.source.rel] = route.source.path
        paths[route.ordinary_input[0]] = route.ordinary_input[1]
        paths[route.breaker_input[0]] = route.breaker_input[1]
        for survivability_input in route.survivability_inputs:
            paths[survivability_input.rel] = survivability_input.path
    for alternative in accepted_audit.programs:
        paths[alternative.source.rel] = alternative.source.path
    files = [
        {
            "path": rel,
            "sha256": sha256_file(path),
            "size": path.stat().st_size,
        }
        for rel, path in sorted(paths.items())
    ]
    std_hash = sha256_file(problem_dir / "package/std.cpp")
    oracle_hash = sha256_file(problem_dir / "package/brute.cpp")
    if std_hash == oracle_hash:
        raise GateError(
            "package/brute.cpp must have a source hash distinct from package/std.cpp"
        )
    return {
        "files": files,
        "files_binding_sha256": sha256_bytes(canonical_json_bytes(files)),
        "sample_manifest": {
            "path": sample_manifest[0],
            "sha256": sha256_file(sample_manifest[1]),
            "statement_path": "statement.md",
            "statement_sha256": sha256_file(problem_dir / "statement.md"),
            "sample_count": len(samples),
            "sample_ids": [sample.sample_id for sample in samples],
        },
        "checker_verdict_contract": checker_contract.as_dict(),
        "oracle": {
            "source": "package/brute.cpp",
            "source_sha256": oracle_hash,
            "std_source": "package/std.cpp",
            "std_source_sha256": std_hash,
            "source_hashes_distinct": True,
            "independent_from_std": oracle_contract.independent_from_std,
            "independence_basis": oracle_contract.independence_basis,
            "applicability": oracle_contract.applicability,
        },
    }


def required_sources(
    problem_dir: Path,
    differential: DifferentialSpec,
    wrongs: list[WrongRoute],
    accepted_audit: AcceptedAlternativeAudit,
) -> tuple[list[SourceSpec], bool]:
    sources: list[SourceSpec] = []
    for role, rel in (
        ("std", "package/std.cpp"),
        ("brute", "package/brute.cpp"),
        ("validator", "package/validator.cpp"),
    ):
        source_rel, source_path = resolve_file(problem_dir, rel, rel)
        if source_path.suffix.lower() not in CPP_SUFFIXES:
            raise GateError(f"{rel} must be C++")
        sources.append(SourceSpec(role, source_rel, source_path))
    checker_rel = "package/checker.cpp"
    checker_path = problem_dir / checker_rel
    has_checker = checker_path.exists() or checker_path.is_symlink()
    if has_checker:
        source_rel, source_path = resolve_file(problem_dir, checker_rel, checker_rel)
        if source_path.suffix.lower() not in CPP_SUFFIXES:
            raise GateError("package/checker.cpp must be C++")
        sources.append(SourceSpec("checker", source_rel, source_path))
    sources.append(differential.generator.source)
    sources.extend(route.source for route in wrongs)
    sources.extend(alternative.source for alternative in accepted_audit.programs)
    seen_roles: set[str] = set()
    for source in sources:
        if source.role in seen_roles:
            raise GateError(f"duplicate program role: {source.role}")
        seen_roles.add(source.role)
    return sources, has_checker


def validate_input(
    validator: PreparedProgram,
    data: bytes,
    *,
    backend: ProgramDatasetBackend,
    problem_dir: Path,
    timeout: float,
) -> ProgramResult:
    return backend.run_dataset(
        validator,
        [DatasetInvocation(stdin=data)],
        problem_dir=problem_dir,
        timeout=timeout,
    )[0]


def execute_solution(
    program: PreparedProgram,
    data: bytes,
    *,
    backend: ProgramDatasetBackend,
    problem_dir: Path,
    timeout: float,
) -> ProgramResult:
    return backend.run_dataset(
        program,
        [DatasetInvocation(stdin=data)],
        problem_dir=problem_dir,
        timeout=timeout,
    )[0]


def judge_outputs(
    *,
    checker: PreparedProgram | None,
    checker_contract: CheckerVerdictContract,
    input_data: bytes,
    candidate: bytes,
    answer: bytes,
    backend: ProgramDatasetBackend,
    problem_dir: Path,
    timeout: float,
) -> tuple[str, dict[str, Any]]:
    return judge_outputs_dataset(
        checker=checker,
        checker_contract=checker_contract,
        comparisons=[(input_data, candidate, answer)],
        backend=backend,
        problem_dir=problem_dir,
        timeout=timeout,
    )[0]


def judge_outputs_dataset(
    *,
    checker: PreparedProgram | None,
    checker_contract: CheckerVerdictContract,
    comparisons: list[tuple[bytes, bytes, bytes]],
    backend: ProgramDatasetBackend,
    problem_dir: Path,
    timeout: float,
) -> list[tuple[str, dict[str, Any]]]:
    """Judge ordered output triples, batching checker invocations when present."""

    if checker is None:
        return [
            (
                "accepted" if candidate.split() == answer.split() else "rejected",
                {
                    "mode": "exact-tokens",
                    "candidate_sha256": sha256_bytes(candidate),
                    "answer_sha256": sha256_bytes(answer),
                },
            )
            for _input_data, candidate, answer in comparisons
        ]
    dataset = [
        DatasetInvocation(
            argv=("input.txt", "candidate.txt", "answer.txt"),
            copy_in_files={
                "input.txt": input_data,
                "candidate.txt": candidate,
                "answer.txt": answer,
            },
            case_id=index,
        )
        for index, (input_data, candidate, answer) in enumerate(comparisons)
    ]
    results = backend.run_dataset(
        checker,
        dataset,
        problem_dir=problem_dir,
        timeout=timeout,
    )
    judged: list[tuple[str, dict[str, Any]]] = []
    for result, (_input_data, candidate, answer) in zip(results, comparisons):
        if result.timed_out or result.launch_error is not None or result.returncode is None:
            verdict = "infrastructure-error"
        elif result.returncode in checker_contract.accepted_exit_codes:
            verdict = "accepted"
        elif result.returncode in (
            checker_contract.wrong_answer_exit_codes
            + checker_contract.presentation_error_exit_codes
        ):
            verdict = "rejected"
        else:
            verdict = "infrastructure-error"
        judged.append(
            (
                verdict,
                {
                    "mode": "checker",
                    "verdict_contract": checker_contract.as_dict(),
                    "command_template": [
                        "$BUILD_DIR/checker",
                        "$INPUT",
                        "$CANDIDATE_OUTPUT",
                        "$ANSWER_OUTPUT",
                    ],
                    "result": result.compact(),
                    "candidate_sha256": sha256_bytes(candidate),
                    "answer_sha256": sha256_bytes(answer),
                },
            )
        )
    return judged


def require_successful_stage(result: ProgramResult, stage: str) -> None:
    if not process_succeeded(result):
        if result.timed_out:
            detail = "timed out"
        elif result.launch_error:
            detail = result.launch_error
        else:
            detail = f"exit {result.returncode}: {preview_bytes(result.stderr)}"
        raise GateError(f"{stage} failed: {detail}")


def expand_generator_args(spec: DifferentialSpec, value: int) -> list[str]:
    replacement = str(value)
    return [token.replace(spec.placeholder, replacement) for token in spec.generator.args]


def first_failure(
    *, stage: str, ordinal: int, parameter: int, data: bytes | None, detail: Any
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "stage": stage,
        "ordinal": ordinal,
        "parameter": parameter,
        "detail": detail,
    }
    if data is not None:
        record.update(
            {
                "input_sha256": sha256_bytes(data),
                "input_bytes": len(data),
                "input_preview": preview_bytes(data),
            }
        )
    return record


def _successful_prefix(results: list[ProgramResult]) -> int:
    for index, result in enumerate(results):
        if not process_succeeded(result):
            return index
    return len(results)


def _accepted_prefix(results: list[tuple[str, dict[str, Any]]]) -> int:
    for index, (verdict, _detail) in enumerate(results):
        if verdict != "accepted":
            return index
    return len(results)


def run_differential(
    spec: DifferentialSpec,
    binaries: dict[str, PreparedProgram],
    *,
    checker: PreparedProgram | None,
    checker_contract: CheckerVerdictContract,
    backend: ProgramDatasetBackend,
    problem_dir: Path,
    timeout: float,
) -> tuple[dict[str, Any], list[str]]:
    binding_digest = hashlib.sha256()
    input_digest = hashlib.sha256()
    errors: list[str] = []
    completed = 0
    generated = 0
    validated = 0
    failure: dict[str, Any] | None = None

    # Each stage is one Program x ordered Dataset operation.  The production
    # adapter deterministically chunks this dataset at the service limit (128),
    # while the receipt below still exposes the earliest sequential failure.
    generator_results = backend.run_dataset(
        binaries["generator"],
        [
            DatasetInvocation(
                argv=tuple(expand_generator_args(spec, spec.start + ordinal)),
                case_id=ordinal,
            )
            for ordinal in range(spec.count)
        ],
        problem_dir=problem_dir,
        timeout=timeout,
    )
    generated_prefix = _successful_prefix(generator_results)
    generated_inputs = [
        result.stdout for result in generator_results[:generated_prefix]
    ]

    validator_results = backend.run_dataset(
        binaries["validator"],
        [
            DatasetInvocation(stdin=data, case_id=ordinal)
            for ordinal, data in enumerate(generated_inputs)
        ],
        problem_dir=problem_dir,
        timeout=timeout,
    )
    validated_prefix = _successful_prefix(validator_results)
    validated_inputs = generated_inputs[:validated_prefix]

    std_results = backend.run_dataset(
        binaries["std"],
        [
            DatasetInvocation(stdin=data, case_id=ordinal)
            for ordinal, data in enumerate(validated_inputs)
        ],
        problem_dir=problem_dir,
        timeout=timeout,
    )
    std_prefix = _successful_prefix(std_results)
    std_inputs = validated_inputs[:std_prefix]

    brute_results = backend.run_dataset(
        binaries["brute"],
        [
            DatasetInvocation(stdin=data, case_id=ordinal)
            for ordinal, data in enumerate(std_inputs)
        ],
        problem_dir=problem_dir,
        timeout=timeout,
    )
    brute_prefix = _successful_prefix(brute_results)

    self_judgments = judge_outputs_dataset(
        checker=checker,
        checker_contract=checker_contract,
        comparisons=[
            (std_inputs[index], std_results[index].stdout, std_results[index].stdout)
            for index in range(brute_prefix)
        ],
        backend=backend,
        problem_dir=problem_dir,
        timeout=timeout,
    )
    self_prefix = _accepted_prefix(self_judgments)
    differential_judgments = judge_outputs_dataset(
        checker=checker,
        checker_contract=checker_contract,
        comparisons=[
            (
                std_inputs[index],
                brute_results[index].stdout,
                std_results[index].stdout,
            )
            for index in range(self_prefix)
        ],
        backend=backend,
        problem_dir=problem_dir,
        timeout=timeout,
    )

    for ordinal in range(spec.count):
        parameter = spec.start + ordinal
        generated_result = generator_results[ordinal]
        if not process_succeeded(generated_result):
            failure = first_failure(
                stage="generator",
                ordinal=ordinal,
                parameter=parameter,
                data=None,
                detail=generated_result.compact(),
            )
            errors.append(f"differential case {ordinal}: generator failed")
            break
        data = generated_result.stdout
        generated += 1
        input_hash = sha256_bytes(data)
        input_digest.update((input_hash + "\n").encode("ascii"))

        validator_result = validator_results[ordinal]
        if not process_succeeded(validator_result):
            failure = first_failure(
                stage="validator",
                ordinal=ordinal,
                parameter=parameter,
                data=data,
                detail=validator_result.compact(),
            )
            errors.append(f"differential case {ordinal}: validator rejected input")
            break
        validated += 1

        std_result = std_results[ordinal]
        if not process_succeeded(std_result):
            failure = first_failure(
                stage="std",
                ordinal=ordinal,
                parameter=parameter,
                data=data,
                detail=std_result.compact(),
            )
            errors.append(f"differential case {ordinal}: std failed")
            break

        brute_result = brute_results[ordinal]
        if not process_succeeded(brute_result):
            failure = first_failure(
                stage="brute",
                ordinal=ordinal,
                parameter=parameter,
                data=data,
                detail=brute_result.compact(),
            )
            errors.append(f"differential case {ordinal}: brute failed")
            break

        self_verdict, self_judge = self_judgments[ordinal]
        if self_verdict != "accepted":
            failure = first_failure(
                stage="checker-self-calibration",
                ordinal=ordinal,
                parameter=parameter,
                data=data,
                detail=self_judge,
            )
            errors.append(f"differential case {ordinal}: checker rejected std itself")
            break

        verdict, judge = differential_judgments[ordinal]
        case_record = {
            "ordinal": ordinal,
            "parameter": parameter,
            "input_sha256": input_hash,
            "std_stdout_sha256": sha256_bytes(std_result.stdout),
            "brute_stdout_sha256": sha256_bytes(brute_result.stdout),
            "judge": verdict,
        }
        binding_digest.update(canonical_json_bytes(case_record) + b"\n")
        if verdict != "accepted":
            failure = first_failure(
                stage="differential-judge",
                ordinal=ordinal,
                parameter=parameter,
                data=data,
                detail={"case_binding": case_record, "judge": judge},
            )
            errors.append(f"differential case {ordinal}: std and brute disagree")
            break
        completed += 1
    consecutive = completed if spec.mode == "random-seeds" else 0

    def peak(results: list[ProgramResult]) -> dict[str, Any]:
        if not results:
            return {
                "observed_cases": 0,
                "max_time_ms": 0,
                "max_time_case_index": None,
                "max_memory_bytes": 0,
                "max_memory_case_index": None,
            }
        max_time_index = max(
            range(len(results)), key=lambda index: results[index].duration_seconds
        )
        max_memory_index = max(
            range(len(results)), key=lambda index: results[index].memory_bytes
        )
        return {
            "observed_cases": len(results),
            "max_time_ms": round(
                results[max_time_index].duration_seconds * 1000, 3
            ),
            "max_time_case_index": max_time_index,
            "max_memory_bytes": results[max_memory_index].memory_bytes,
            "max_memory_case_index": max_memory_index,
        }

    receipt = {
        "status": "passed" if completed == spec.count and not errors else "failed",
        "mode": spec.mode,
        "parameter_name": spec.placeholder.strip("{}"),
        "parameter_start": spec.start,
        "requested_cases": spec.count,
        "generated_cases": generated,
        "validated_cases": validated,
        "completed_cases": completed,
        "consecutive_seeds": consecutive,
        "generator": {
            "source": spec.generator.source.rel,
            "source_sha256": sha256_file(spec.generator.source.path),
            "command_template": [
                "$BUILD_DIR/generator",
                *spec.generator.args,
            ],
            "invocations_per_case": 1,
        },
        "ordered_input_hashes_sha256": input_digest.hexdigest(),
        "ordered_case_binding_sha256": binding_digest.hexdigest(),
        "case_binding_algorithm": (
            "sha256(concat(canonical-json({ordinal,parameter,input_sha256,"
            "std_stdout_sha256,brute_stdout_sha256,judge}) + newline))"
        ),
        "first_failure": failure,
        "resource_observations": {
            "generator": peak(generator_results),
            "validator": peak(validator_results),
            "std": peak(std_results),
            "brute": peak(brute_results),
        },
    }
    return receipt, errors


def read_input(path: Path, label: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise GateError(f"cannot read {label}: {exc}") from exc


def check_fixed_inputs_dataset(
    requests: list[FixedInputSpec],
    *,
    binaries: dict[str, PreparedProgram],
    checker: PreparedProgram | None,
    checker_contract: CheckerVerdictContract,
    backend: ProgramDatasetBackend,
    problem_dir: Path,
    timeout: float,
) -> list[FixedInputOutcome]:
    """Validate and materialize std answers for an ordered fixed dataset."""

    if not requests:
        return []
    data_items: list[bytes | None] = []
    records: list[dict[str, Any]] = []
    errors: list[str | None] = [None] * len(requests)
    for index, request in enumerate(requests):
        try:
            data = read_input(request.path, request.label)
        except GateError as exc:
            data = None
            errors[index] = str(exc)
            record = {"input": request.rel}
        else:
            record = {
                "input": request.rel,
                "input_sha256": sha256_bytes(data),
                "input_bytes": len(data),
                "command_templates": {
                    "validator": ["$BUILD_DIR/validator"],
                    "std": ["$BUILD_DIR/std"],
                },
            }
        data_items.append(data)
        records.append(record)

    readable = [index for index, data in enumerate(data_items) if data is not None]
    validator_results = backend.run_dataset(
        binaries["validator"],
        [
            DatasetInvocation(stdin=data_items[index] or b"", case_id=index)
            for index in readable
        ],
        problem_dir=problem_dir,
        timeout=timeout,
    )
    validator_by_index = dict(zip(readable, validator_results))
    valid: list[int] = []
    for index in readable:
        result = validator_by_index[index]
        records[index]["validator"] = result.compact(include_output_hash=False)
        try:
            require_successful_stage(result, f"{requests[index].label} validator")
        except GateError as exc:
            errors[index] = str(exc)
        else:
            valid.append(index)

    std_results = backend.run_dataset(
        binaries["std"],
        [
            DatasetInvocation(stdin=data_items[index] or b"", case_id=index)
            for index in valid
        ],
        problem_dir=problem_dir,
        timeout=timeout,
    )
    std_by_index = dict(zip(valid, std_results))
    materialized: list[int] = []
    for index in valid:
        result = std_by_index[index]
        records[index]["std"] = result.compact()
        try:
            require_successful_stage(result, f"{requests[index].label} std")
        except GateError as exc:
            errors[index] = str(exc)
        else:
            materialized.append(index)

    calibrated = materialized
    if checker is not None:
        self_judgments = judge_outputs_dataset(
            checker=checker,
            checker_contract=checker_contract,
            comparisons=[
                (
                    data_items[index] or b"",
                    std_by_index[index].stdout,
                    std_by_index[index].stdout,
                )
                for index in materialized
            ],
            backend=backend,
            problem_dir=problem_dir,
            timeout=timeout,
        )
        calibrated = []
        for index, (verdict, judge) in zip(materialized, self_judgments):
            records[index]["checker_self_calibration"] = {
                "verdict": verdict,
                **judge,
            }
            if verdict != "accepted":
                errors[index] = (
                    f"{requests[index].label}: checker rejected std against itself"
                )
            else:
                calibrated.append(index)

    expected_indices = [
        index
        for index in calibrated
        if requests[index].expected_output is not None
    ]
    expected_judgments = judge_outputs_dataset(
        checker=checker,
        checker_contract=checker_contract,
        comparisons=[
            (
                data_items[index] or b"",
                std_by_index[index].stdout,
                requests[index].expected_output or b"",
            )
            for index in expected_indices
        ],
        backend=backend,
        problem_dir=problem_dir,
        timeout=timeout,
    )
    for index, (verdict, judge) in zip(expected_indices, expected_judgments):
        records[index]["answer_judge"] = {"verdict": verdict, **judge}
        if verdict != "accepted":
            errors[index] = (
                f"{requests[index].label}: std did not match the supplied answer"
            )

    return [
        FixedInputOutcome(
            record=records[index],
            reference_output=(
                std_by_index[index].stdout
                if index in std_by_index and errors[index] is None
                else None
            ),
            error=errors[index],
        )
        for index in range(len(requests))
    ]


def check_fixed_input(
    *,
    label: str,
    rel: str,
    path: Path,
    binaries: dict[str, PreparedProgram],
    checker: PreparedProgram | None,
    checker_contract: CheckerVerdictContract,
    backend: ProgramDatasetBackend,
    problem_dir: Path,
    timeout: float,
    expected_output: bytes | None = None,
) -> tuple[dict[str, Any], bytes]:
    outcome = check_fixed_inputs_dataset(
        [FixedInputSpec(label, rel, path, expected_output)],
        binaries=binaries,
        checker=checker,
        checker_contract=checker_contract,
        backend=backend,
        problem_dir=problem_dir,
        timeout=timeout,
    )[0]
    if outcome.error is not None or outcome.reference_output is None:
        raise GateError(outcome.error or f"{label}: std produced no reference output")
    return outcome.record, outcome.reference_output


def run_release_tests(
    releases: list[ReleaseTest],
    binaries: dict[str, PreparedProgram],
    *,
    checker: PreparedProgram | None,
    checker_contract: CheckerVerdictContract,
    backend: ProgramDatasetBackend,
    problem_dir: Path,
    timeout: float,
) -> tuple[list[dict[str, Any]], list[str]]:
    records: list[dict[str, Any]] = [
        {
            "test_id": release.test_id,
            "input": release.input_rel,
            "limit_tags": list(release.limit_tags),
        }
        for release in releases
    ]
    errors: list[str] = []
    requests: list[FixedInputSpec] = []
    request_indices: list[int] = []
    for index, release in enumerate(releases):
        record = records[index]
        try:
            expected: bytes | None = None
            if release.answer_path is not None:
                expected = read_input(release.answer_path, f"release {release.test_id} answer")
                record.update(
                    {
                        "answer": release.answer_rel,
                        "answer_sha256": sha256_bytes(expected),
                        "answer_bytes": len(expected),
                    }
                )
            requests.append(
                FixedInputSpec(
                    label=f"release {release.test_id}",
                    rel=release.input_rel,
                    path=release.input_path,
                    expected_output=expected,
                )
            )
            request_indices.append(index)
        except GateError as exc:
            record["status"] = "failed"
            record["error"] = str(exc)
            errors.append(str(exc))

    outcomes = check_fixed_inputs_dataset(
        requests,
        binaries=binaries,
        checker=checker,
        checker_contract=checker_contract,
        backend=backend,
        problem_dir=problem_dir,
        timeout=timeout,
    )
    for index, outcome in zip(request_indices, outcomes):
        record = records[index]
        if outcome.error is None:
            record.update(outcome.record)
            record["status"] = "passed"
        else:
            record["status"] = "failed"
            record["error"] = outcome.error
            errors.append(outcome.error)
    return records, errors


def run_accepted_alternatives(
    accepted_audit: AcceptedAlternativeAudit,
    releases: list[ReleaseTest],
    binaries: dict[str, PreparedProgram],
    *,
    checker: PreparedProgram | None,
    checker_contract: CheckerVerdictContract,
    backend: ProgramDatasetBackend,
    problem_dir: Path,
    timeout: float,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Execute every claimed accepted alternative on every release test."""

    records: list[dict[str, Any]] = []
    errors: list[str] = []
    inputs = [
        read_input(release.input_path, f"release {release.test_id} input")
        for release in releases
    ]
    answers = [
        read_input(release.answer_path, f"release {release.test_id} answer")
        if release.answer_path is not None
        else b""
        for release in releases
    ]
    for alternative in accepted_audit.programs:
        record: dict[str, Any] = {
            "alternative_id": alternative.alternative_id,
            "source": alternative.source.rel,
            "source_sha256": sha256_file(alternative.source.path),
            "normalized_source_sha256": alternative.normalized_source_sha256,
            "independence_basis": alternative.independence_basis,
            "command_template": [
                f"$BUILD_DIR/accepted:{alternative.alternative_id}"
            ],
            "compile_status": (
                "passed" if alternative.source.role in binaries else "failed"
            ),
            "release_results": [],
        }
        alternative_errors: list[str] = []
        program = binaries.get(alternative.source.role)
        if program is None:
            alternative_errors.append("accepted alternative did not compile")
        else:
            results = backend.run_dataset(
                program,
                [
                    DatasetInvocation(stdin=data, case_id=release.test_id)
                    for release, data in zip(releases, inputs)
                ],
                problem_dir=problem_dir,
                timeout=timeout,
            )
            if len(results) != len(releases):
                alternative_errors.append(
                    "accepted alternative returned an incomplete release dataset"
                )
            judged_indices = [
                index
                for index, result in enumerate(results)
                if process_succeeded(result)
            ]
            judgments = judge_outputs_dataset(
                checker=checker,
                checker_contract=checker_contract,
                comparisons=[
                    (inputs[index], results[index].stdout, answers[index])
                    for index in judged_indices
                ],
                backend=backend,
                problem_dir=problem_dir,
                timeout=timeout,
            )
            judgments_by_index = dict(zip(judged_indices, judgments))
            for index, release in enumerate(releases):
                result_record: dict[str, Any] = {
                    "test_id": release.test_id,
                    "input": release.input_rel,
                    "input_sha256": sha256_bytes(inputs[index]),
                    "answer": release.answer_rel,
                    "answer_sha256": sha256_bytes(answers[index]),
                }
                if index >= len(results):
                    result_record.update(
                        {
                            "status": "failed",
                            "error": "missing program result",
                        }
                    )
                    alternative_errors.append(
                        f"release {release.test_id}: missing program result"
                    )
                else:
                    result = results[index]
                    result_record["execution"] = result.compact()
                    if not process_succeeded(result):
                        result_record["status"] = "failed"
                        result_record["error"] = "program execution did not succeed"
                        alternative_errors.append(
                            f"release {release.test_id}: execution did not succeed"
                        )
                    else:
                        verdict, judge = judgments_by_index[index]
                        candidate_token_sha256 = token_sha256(result.stdout)
                        answer_token_sha256 = token_sha256(answers[index])
                        result_record["judge"] = {"verdict": verdict, **judge}
                        result_record.update(
                            {
                                "candidate_token_sha256": candidate_token_sha256,
                                "answer_token_sha256": answer_token_sha256,
                                "non_jury_output": (
                                    candidate_token_sha256 != answer_token_sha256
                                ),
                            }
                        )
                        result_record["status"] = (
                            "passed" if verdict == "accepted" else "failed"
                        )
                        if verdict != "accepted":
                            alternative_errors.append(
                                f"release {release.test_id}: observed {verdict}, "
                                "expected accepted"
                            )
                record["release_results"].append(result_record)
        record["non_jury_accepted_test_ids"] = [
            item["test_id"]
            for item in record["release_results"]
            if item.get("status") == "passed"
            and item.get("non_jury_output") is True
        ]
        record["status"] = "passed" if not alternative_errors else "failed"
        record["errors"] = alternative_errors
        errors.extend(
            f"accepted alternative {alternative.alternative_id}: {error}"
            for error in alternative_errors
        )
        records.append(record)
    if checker is not None and accepted_audit.programs and not any(
        record.get("non_jury_accepted_test_ids") for record in records
    ):
        errors.append(
            "custom-checker accepted alternatives produced no accepted output "
            "with a token sequence distinct from the jury answer"
        )
    return records, errors


def classify_wrong_result(
    result: ProgramResult,
    *,
    checker: PreparedProgram | None,
    checker_contract: CheckerVerdictContract,
    input_data: bytes,
    reference_output: bytes,
    backend: ProgramDatasetBackend,
    problem_dir: Path,
    timeout: float,
) -> tuple[str, dict[str, Any]]:
    return classify_wrong_results_dataset(
        [result],
        input_data=[input_data],
        reference_outputs=[reference_output],
        checker=checker,
        checker_contract=checker_contract,
        backend=backend,
        problem_dir=problem_dir,
        timeout=timeout,
    )[0]


def classify_wrong_results_dataset(
    results: list[ProgramResult],
    *,
    input_data: list[bytes],
    reference_outputs: list[bytes],
    checker: PreparedProgram | None,
    checker_contract: CheckerVerdictContract,
    backend: ProgramDatasetBackend,
    problem_dir: Path,
    timeout: float,
) -> list[tuple[str, dict[str, Any]]]:
    if len(results) != len(input_data) or len(results) != len(reference_outputs):
        raise GateError("wrong-result classification dataset lengths do not match")
    classified: list[tuple[str, dict[str, Any]] | None] = [None] * len(results)
    judge_indices: list[int] = []
    for index, result in enumerate(results):
        if result.timed_out:
            classified[index] = ("TLE", {"execution": result.compact()})
        elif result.launch_error is not None or result.returncode is None:
            classified[index] = ("INFRA", {"execution": result.compact()})
        elif result.sandbox_verdict in {"MLE", "OLE", "RE"}:
            classified[index] = (
                result.sandbox_verdict,
                {"execution": result.compact()},
            )
        elif result.returncode != 0:
            classified[index] = ("RE", {"execution": result.compact()})
        else:
            judge_indices.append(index)
    judgments = judge_outputs_dataset(
        checker=checker,
        checker_contract=checker_contract,
        comparisons=[
            (input_data[index], results[index].stdout, reference_outputs[index])
            for index in judge_indices
        ],
        backend=backend,
        problem_dir=problem_dir,
        timeout=timeout,
    )
    for index, (verdict, judge) in zip(judge_indices, judgments):
        if verdict == "accepted":
            observed = "AC"
        elif verdict == "rejected":
            observed = "WA"
        else:
            observed = "INFRA"
        classified[index] = (
            observed,
            {"execution": results[index].compact(), "judge": judge},
        )
    if any(item is None for item in classified):
        raise GateError("wrong-result classification left an unclassified point")
    return [item for item in classified if item is not None]


def run_wrong_input(
    *,
    label: str,
    rel: str,
    path: Path,
    wrong_result: ProgramResult,
    binaries: dict[str, PreparedProgram],
    checker: PreparedProgram | None,
    checker_contract: CheckerVerdictContract,
    backend: ProgramDatasetBackend,
    problem_dir: Path,
    timeout: float,
    expected_output: bytes | None = None,
    fixed_outcome: FixedInputOutcome | None = None,
    classified_result: tuple[str, dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], str]:
    if fixed_outcome is None:
        checked, reference = check_fixed_input(
            label=label,
            rel=rel,
            path=path,
            binaries=binaries,
            checker=checker,
            checker_contract=checker_contract,
            backend=backend,
            problem_dir=problem_dir,
            timeout=timeout,
            expected_output=expected_output,
        )
    else:
        if fixed_outcome.error is not None or fixed_outcome.reference_output is None:
            raise GateError(
                fixed_outcome.error or f"{label}: std produced no reference output"
            )
        checked = fixed_outcome.record
        reference = fixed_outcome.reference_output
    data = read_input(path, label)
    if classified_result is None:
        observed, detail = classify_wrong_result(
            wrong_result,
            checker=checker,
            checker_contract=checker_contract,
            input_data=data,
            reference_output=reference,
            backend=backend,
            problem_dir=problem_dir,
            timeout=timeout,
        )
    else:
        observed, detail = classified_result
    return {**checked, "observed_verdict": observed, "wrong": detail}, observed


def run_wrong_routes(
    wrongs: list[WrongRoute],
    samples: list[SampleCase],
    binaries: dict[str, PreparedProgram],
    *,
    checker: PreparedProgram | None,
    checker_contract: CheckerVerdictContract,
    backend: ProgramDatasetBackend,
    problem_dir: Path,
    timeout: float,
) -> tuple[list[dict[str, Any]], list[str]]:
    records: list[dict[str, Any]] = []
    errors: list[str] = []
    for route in wrongs:
        record: dict[str, Any] = {
            "route_id": route.route_id,
            "source": route.source.rel,
            "source_sha256": sha256_file(route.source.path),
            "expected_verdict": route.expected_verdict,
            "command_template": [f"$BUILD_DIR/wrong:{route.route_id}"],
            "compile_status": "passed" if route.source.role in binaries else "failed",
            "sample_results": [],
            "survivability_results": [],
        }
        route_errors: list[str] = []
        wrong_binary = binaries.get(route.source.role)
        if wrong_binary is None:
            route_errors.append("wrong program did not compile")
        else:
            ordered_paths = [sample.input_path for sample in samples]
            ordered_paths.extend(
                item.path for item in route.survivability_inputs
            )
            ordered_paths.extend((route.ordinary_input[1], route.breaker_input[1]))
            fixed_specs = [
                FixedInputSpec(
                    label=f"wrong route {route.route_id} sample {index + 1}",
                    rel=sample.input_rel,
                    path=sample.input_path,
                    expected_output=read_input(
                        sample.answer_path,
                        f"canonical sample {sample.sample_id} answer",
                    ),
                )
                for index, sample in enumerate(samples)
            ]
            fixed_specs.extend(
                FixedInputSpec(
                    label=(
                        f"wrong route {route.route_id} survivability "
                        f"{survivability.kind}"
                    ),
                    rel=survivability.rel,
                    path=survivability.path,
                )
                for survivability in route.survivability_inputs
            )
            fixed_specs.extend(
                (
                    FixedInputSpec(
                        label=f"wrong route {route.route_id} ordinary",
                        rel=route.ordinary_input[0],
                        path=route.ordinary_input[1],
                    ),
                    FixedInputSpec(
                        label=f"wrong route {route.route_id} breaker",
                        rel=route.breaker_input[0],
                        path=route.breaker_input[1],
                    ),
                )
            )
            fixed_outcomes = check_fixed_inputs_dataset(
                fixed_specs,
                binaries=binaries,
                checker=checker,
                checker_contract=checker_contract,
                backend=backend,
                problem_dir=problem_dir,
                timeout=timeout,
            )
            runnable_indices = [
                index
                for index, outcome in enumerate(fixed_outcomes)
                if outcome.error is None and outcome.reference_output is not None
            ]
            runnable_results = backend.run_dataset(
                wrong_binary,
                [
                    DatasetInvocation(
                        stdin=read_input(
                            path,
                            f"wrong route {route.route_id} dataset input {index + 1}",
                        ),
                        case_id=index,
                    )
                    for index, path in enumerate(ordered_paths)
                    if index in runnable_indices
                ],
                problem_dir=problem_dir,
                timeout=timeout,
            )
            wrong_results = dict(zip(runnable_indices, runnable_results))
            classified_results = classify_wrong_results_dataset(
                runnable_results,
                input_data=[
                    read_input(
                        ordered_paths[index],
                        f"wrong route {route.route_id} classification input {index + 1}",
                    )
                    for index in runnable_indices
                ],
                reference_outputs=[
                    fixed_outcomes[index].reference_output or b""
                    for index in runnable_indices
                ],
                checker=checker,
                checker_contract=checker_contract,
                backend=backend,
                problem_dir=problem_dir,
                timeout=timeout,
            )
            classified_by_index = dict(zip(runnable_indices, classified_results))
            skipped_wrong_result = ProgramResult(
                returncode=None,
                timed_out=False,
                duration_seconds=0.0,
                stdout=b"",
                stderr=b"",
                launch_error="wrong program skipped because fixed-input checks failed",
            )
            for index, sample_case in enumerate(samples):
                rel, path = sample_case.input_rel, sample_case.input_path
                try:
                    expected_sample = read_input(
                        sample_case.answer_path,
                        f"canonical sample {sample_case.sample_id} answer",
                    )
                    sample, observed = run_wrong_input(
                        label=f"wrong route {route.route_id} sample {index + 1}",
                        rel=rel,
                        path=path,
                        wrong_result=wrong_results.get(index, skipped_wrong_result),
                        binaries=binaries,
                        checker=checker,
                        checker_contract=checker_contract,
                        backend=backend,
                        problem_dir=problem_dir,
                        timeout=timeout,
                        expected_output=expected_sample,
                        fixed_outcome=fixed_outcomes[index],
                        classified_result=classified_by_index.get(index),
                    )
                    sample.update(
                        {
                            "sample_id": sample_case.sample_id,
                            "statement_ordinal": sample_case.statement_ordinal,
                            "answer": sample_case.answer_rel,
                            "answer_sha256": sample_case.answer_sha256,
                        }
                    )
                    sample["status"] = "passed" if observed == "AC" else "failed"
                    if observed != "AC":
                        route_errors.append(
                            f"sample {index + 1} observed {observed}, expected AC"
                        )
                except GateError as exc:
                    sample = {"input": rel, "status": "failed", "error": str(exc)}
                    route_errors.append(str(exc))
                record["sample_results"].append(sample)
            survivability_start = len(samples)
            for offset, survivability_input in enumerate(
                route.survivability_inputs
            ):
                result_index = survivability_start + offset
                try:
                    survival, observed = run_wrong_input(
                        label=(
                            f"wrong route {route.route_id} survivability "
                            f"{survivability_input.kind}"
                        ),
                        rel=survivability_input.rel,
                        path=survivability_input.path,
                        wrong_result=wrong_results.get(
                            result_index, skipped_wrong_result
                        ),
                        binaries=binaries,
                        checker=checker,
                        checker_contract=checker_contract,
                        backend=backend,
                        problem_dir=problem_dir,
                        timeout=timeout,
                        fixed_outcome=fixed_outcomes[result_index],
                        classified_result=classified_by_index.get(result_index),
                    )
                    survival.update(
                        {
                            "kind": survivability_input.kind,
                            "status": "passed" if observed == "AC" else "failed",
                        }
                    )
                    if observed != "AC":
                        route_errors.append(
                            f"survivability {survivability_input.kind} observed "
                            f"{observed}, expected AC"
                        )
                except GateError as exc:
                    survival = {
                        "kind": survivability_input.kind,
                        "input": survivability_input.rel,
                        "status": "failed",
                        "error": str(exc),
                    }
                    route_errors.append(str(exc))
                record["survivability_results"].append(survival)
            ordinary_index = len(samples) + len(route.survivability_inputs)
            ordinary_rel, ordinary_path = route.ordinary_input
            try:
                ordinary, observed = run_wrong_input(
                    label=f"wrong route {route.route_id} ordinary",
                    rel=ordinary_rel,
                    path=ordinary_path,
                    wrong_result=wrong_results.get(ordinary_index, skipped_wrong_result),
                    binaries=binaries,
                    checker=checker,
                    checker_contract=checker_contract,
                    backend=backend,
                    problem_dir=problem_dir,
                    timeout=timeout,
                    fixed_outcome=fixed_outcomes[ordinary_index],
                    classified_result=classified_by_index.get(ordinary_index),
                )
                ordinary["status"] = "passed" if observed == "AC" else "failed"
                if observed != "AC":
                    route_errors.append(f"ordinary input observed {observed}, expected AC")
            except GateError as exc:
                ordinary = {
                    "input": ordinary_rel,
                    "status": "failed",
                    "error": str(exc),
                }
                route_errors.append(str(exc))
            record["ordinary_result"] = ordinary
            breaker_rel, breaker_path = route.breaker_input
            try:
                breaker, observed = run_wrong_input(
                    label=f"wrong route {route.route_id} breaker",
                    rel=breaker_rel,
                    path=breaker_path,
                    wrong_result=wrong_results.get(
                        ordinary_index + 1, skipped_wrong_result
                    ),
                    binaries=binaries,
                    checker=checker,
                    checker_contract=checker_contract,
                    backend=backend,
                    problem_dir=problem_dir,
                    timeout=timeout,
                    fixed_outcome=fixed_outcomes[ordinary_index + 1],
                    classified_result=classified_by_index.get(ordinary_index + 1),
                )
                breaker["status"] = (
                    "passed" if observed == route.expected_verdict else "failed"
                )
                if observed != route.expected_verdict:
                    route_errors.append(
                        f"breaker observed {observed}, expected {route.expected_verdict}"
                    )
            except GateError as exc:
                breaker = {
                    "input": breaker_rel,
                    "status": "failed",
                    "error": str(exc),
                }
                route_errors.append(str(exc))
            record["breaker_result"] = breaker
        record["status"] = "passed" if not route_errors else "failed"
        record["errors"] = route_errors
        errors.extend(f"wrong route {route.route_id}: {error}" for error in route_errors)
        records.append(record)
    return records, errors


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    if parent.is_symlink() or not parent.is_dir():
        raise GateError(f"receipt parent is not a regular directory: {parent}")
    descriptor, raw_temp = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=parent
    )
    temp = Path(raw_temp)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, sort_keys=True, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def main() -> int:
    args = parse_args()
    started_at = utc_now()
    monotonic_started = time.monotonic()
    execution_mode = "test" if args.test_mode else "production"
    random_minimum = (
        args.min_random_count
        if args.min_random_count is not None
        else PRODUCTION_RANDOM_MINIMUM
    )
    program_timeout = args.program_timeout_seconds
    compile_timeout = (
        args.compile_timeout_seconds
        if args.compile_timeout_seconds is not None
        else PRODUCTION_COMPILE_TIMEOUT_SECONDS
    )

    try:
        receipt_rel, receipt_path = safe_problem_path(
            args.problem_dir,
            args.receipt_out,
            label="--receipt-out",
            require_exists=False,
        )
        plan_rel, plan_path = safe_problem_path(
            args.problem_dir, args.plan, label="--plan", require_exists=False
        )
        if receipt_path == plan_path:
            raise GateError("--receipt-out must not overwrite --plan")
    except GateError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    # A prior pass must never survive a failed or interrupted re-execution.
    try:
        if receipt_path.exists():
            if receipt_path.is_symlink() or not receipt_path.is_file():
                raise GateError("existing receipt path is not a regular file")
            receipt_path.unlink()
    except (OSError, GateError) as exc:
        print(f"error: cannot invalidate old receipt: {exc}", file=sys.stderr)
        return 2

    receipt: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "gate": GATE_NAME,
        "execution_mode": execution_mode,
        "production": execution_mode == "production",
        "status": "failed",
        "started_at_utc": started_at,
        "problem_dir": ".",
        "plan": {
            "path": plan_rel,
            "sha256": None,
        },
        "receipt_path": receipt_rel,
        "configuration": {
            "random_minimum": random_minimum,
            "program_timeout_seconds": None,
            "requested_program_timeout_seconds": None,
            "requested_memory_limit_mb": None,
            "compile_timeout_seconds": compile_timeout,
            "requested_compile_timeout_seconds": compile_timeout,
            "execution_backend": {"name": args.execution_backend},
            "testing_overrides": {
                "min_random_count": args.min_random_count,
                "program_timeout_seconds": args.program_timeout_seconds,
                "compile_timeout_seconds": args.compile_timeout_seconds,
            },
        },
        "errors": [],
    }

    backend: ProgramDatasetBackend | None = None
    try:
        require_regular_file(plan_path, "regression plan")
        receipt["plan"]["sha256"] = sha256_file(plan_path)
        (
            plan,
            differential,
            releases,
            wrongs,
            accepted_audit,
            required_limit_tags,
            sample_manifest,
            samples,
            checker_contract,
            oracle_contract,
            resource_policy,
        ) = load_plan(args.problem_dir, plan_rel, plan_path, random_minimum)
        receipt["plan"]["canonical_sha256"] = sha256_bytes(
            canonical_json_bytes(plan)
        )
        receipt["resource_policy"] = resource_policy.as_dict()
        if program_timeout is None:
            program_timeout = resource_policy.time_limit_ms / 1000
        requested_time_limit_ms = round(program_timeout * 1000)
        receipt["configuration"].update(
            {
                "program_timeout_seconds": program_timeout,
                "requested_program_timeout_seconds": program_timeout,
                "requested_memory_limit_mb": resource_policy.memory_limit_mib,
                "resource_policy_sha256": resource_policy.policy_sha256,
            }
        )
        backend = create_backend(
            args.execution_backend,
            test_mode=args.test_mode,
            lightcpverifier_url=args.lightcpverifier_url,
            program_time_limit_ms=requested_time_limit_ms,
            memory_limit_mb=resource_policy.memory_limit_mib,
        )
        backend_configuration = backend.configuration(
            requested_program_timeout_seconds=program_timeout,
            requested_compile_timeout_seconds=compile_timeout,
            requested_memory_limit_mb=resource_policy.memory_limit_mib,
        )
        receipt["configuration"]["execution_backend"] = backend_configuration
        receipt["configuration"]["verdict_time_limit_seconds"] = (
            backend_configuration["verdict_time_limit_seconds"]
        )
        receipt["configuration"]["sandbox_effective_time_limit_seconds"] = (
            backend_configuration["sandbox_effective_time_limit_seconds"]
        )
        receipt["configuration"]["sandbox_effective_memory_limit_mb"] = (
            backend_configuration["effective_memory_limit_mb"]
        )
        privacy = package_privacy_scan(args.problem_dir)
        receipt["privacy_scan"] = privacy
        if privacy["status"] != "passed":
            receipt["errors"].append("package privacy scan failed")
        sources, has_checker = required_sources(
            args.problem_dir, differential, wrongs, accepted_audit
        )
        receipt["judge_mode"] = "checker" if has_checker else "exact-tokens"
        receipt["checker_verdict_contract"] = checker_contract.as_dict()
        receipt["artifact_bindings"] = artifact_bindings(
            problem_dir=args.problem_dir,
            differential=differential,
            releases=releases,
            wrongs=wrongs,
            accepted_audit=accepted_audit,
            sample_manifest=sample_manifest,
            samples=samples,
            has_checker=has_checker,
            checker_contract=checker_contract,
            oracle_contract=oracle_contract,
        )
        receipt["execution_command_templates"] = {
            "generator": ["$BUILD_DIR/generator", *differential.generator.args],
            "validator": ["$BUILD_DIR/validator"],
            "std": ["$BUILD_DIR/std"],
            "brute": ["$BUILD_DIR/brute"],
            "checker": (
                [
                    "$BUILD_DIR/checker",
                    "$INPUT",
                    "$CANDIDATE_OUTPUT",
                    "$ANSWER_OUTPUT",
                ]
                if has_checker
                else None
            ),
            "wrong_routes": {
                route.route_id: [f"$BUILD_DIR/wrong:{route.route_id}"]
                for route in wrongs
            },
            "accepted_alternatives": {
                alternative.alternative_id: [
                    f"$BUILD_DIR/accepted:{alternative.alternative_id}"
                ]
                for alternative in accepted_audit.programs
            },
        }
        receipt["accepted_alternative_policy"] = (
            {
                "strategy": "programs",
                "program_count": len(accepted_audit.programs),
                "waiver": None,
            }
            if accepted_audit.programs
            else {
                "strategy": (
                    "no-known-alternative"
                    if accepted_audit.waiver is not None
                    else "not-required"
                ),
                "program_count": 0,
                "waiver": accepted_audit.waiver,
            }
        )

        with tempfile.TemporaryDirectory(prefix="icpc-light-regression-") as raw_build:
            build_dir = Path(raw_build)
            binaries, compilation, compile_errors = backend.compile_sources(
                sources,
                problem_dir=args.problem_dir,
                build_dir=build_dir,
                timeout=compile_timeout,
            )
            receipt["compilation"] = compilation
            receipt["errors"].extend(compile_errors)
            expected_roles = {source.role for source in sources}
            executable_ready = expected_roles == set(binaries)
            checker = binaries.get("checker") if has_checker else None
            if executable_ready and privacy["status"] == "passed":
                differential_receipt, differential_errors = run_differential(
                    differential,
                    binaries,
                    checker=checker,
                    checker_contract=checker_contract,
                    backend=backend,
                    problem_dir=args.problem_dir,
                    timeout=program_timeout,
                )
                receipt["differential"] = differential_receipt
                receipt["errors"].extend(differential_errors)
                release_records, release_errors = run_release_tests(
                    releases,
                    binaries,
                    checker=checker,
                    checker_contract=checker_contract,
                    backend=backend,
                    problem_dir=args.problem_dir,
                    timeout=program_timeout,
                )
                receipt["release_tests"] = release_records
                receipt["errors"].extend(release_errors)
                alternative_records, alternative_errors = run_accepted_alternatives(
                    accepted_audit,
                    releases,
                    binaries,
                    checker=checker,
                    checker_contract=checker_contract,
                    backend=backend,
                    problem_dir=args.problem_dir,
                    timeout=program_timeout,
                )
                receipt["accepted_alternatives"] = alternative_records
                receipt["errors"].extend(alternative_errors)
                wrong_records, wrong_errors = run_wrong_routes(
                    wrongs,
                    samples,
                    binaries,
                    checker=checker,
                    checker_contract=checker_contract,
                    backend=backend,
                    problem_dir=args.problem_dir,
                    timeout=program_timeout,
                )
                receipt["wrong_routes"] = wrong_records
                receipt["errors"].extend(wrong_errors)
            else:
                receipt["errors"].append(
                    "runtime matrix skipped because compilation or privacy prerequisites failed"
                )

        route_bindings = [
            {
                "route_id": route.route_id,
                "source": route.source.rel,
                "source_sha256": sha256_file(route.source.path),
            }
            for route in wrongs
        ]
        receipt["qualified_wrong_route_bindings"] = route_bindings
        receipt["accepted_alternative_bindings"] = [
            {
                "alternative_id": alternative.alternative_id,
                "source": alternative.source.rel,
                "source_sha256": sha256_file(alternative.source.path),
                "normalized_source_sha256": (
                    alternative.normalized_source_sha256
                ),
                "independence_basis": alternative.independence_basis,
            }
            for alternative in accepted_audit.programs
        ]
        diversity_witnesses = [
            {
                "alternative_id": record.get("alternative_id"),
                "test_id": result.get("test_id"),
                "candidate_token_sha256": result.get("candidate_token_sha256"),
                "answer_token_sha256": result.get("answer_token_sha256"),
            }
            for record in receipt.get("accepted_alternatives", [])
            for result in record.get("release_results", [])
            if result.get("status") == "passed"
            and result.get("non_jury_output") is True
        ]
        diversity_required = has_checker and bool(accepted_audit.programs)
        receipt["accepted_alternative_output_diversity"] = {
            "required": diversity_required,
            "status": (
                ("passed" if diversity_witnesses else "failed")
                if diversity_required
                else (
                    "waived"
                    if has_checker and accepted_audit.waiver is not None
                    else "not-required"
                )
            ),
            "witnesses": diversity_witnesses,
        }
        differential_receipt = receipt.get("differential", {})
        receipt["facts"] = {
            "differential_mode": differential.mode,
            "differential_cases_requested": differential.count,
            "differential_cases_completed": differential_receipt.get(
                "completed_cases", 0
            ),
            "differential_consecutive_seeds": differential_receipt.get(
                "consecutive_seeds", 0
            ),
            "generated_inputs_validated": differential_receipt.get(
                "validated_cases", 0
            ),
            "release_tests_checked": sum(
                1
                for record in receipt.get("release_tests", [])
                if record.get("status") == "passed"
            ),
            "wrong_routes_checked": sum(
                1
                for record in receipt.get("wrong_routes", [])
                if record.get("status") == "passed"
            ),
            "survivability_inputs_checked": sum(
                1
                for route_record in receipt.get("wrong_routes", [])
                for record in route_record.get("survivability_results", [])
                if record.get("status") == "passed"
            ),
            "accepted_alternatives_checked": sum(
                1
                for record in receipt.get("accepted_alternatives", [])
                if record.get("status") == "passed"
            ),
            "accepted_non_jury_outputs_checked": len(diversity_witnesses),
            "accepted_alternative_strategy": receipt.get(
                "accepted_alternative_policy", {}
            ).get("strategy"),
            "canonical_samples_checked_per_wrong_route": len(samples),
            "sample_manifest_sha256": sha256_file(sample_manifest[1]),
            "qualified_wrong_route_ids": [item["route_id"] for item in route_bindings],
            "required_limit_tags": list(required_limit_tags),
            "covered_limit_tags": sorted(
                {tag for release in releases for tag in release.limit_tags}
            ),
            "limit_coverage_status": "passed",
        }
        receipt["status"] = "passed" if not receipt["errors"] else "failed"
    except Exception as exc:  # A failed gate still needs a diagnostic receipt.
        receipt["errors"].append(f"{type(exc).__name__}: {exc}")
        receipt["exception_traceback"] = traceback.format_exc(limit=12)[-12000:]
        receipt["status"] = "failed"

    if backend is not None:
        try:
            receipt["execution_backend_evidence"] = backend.execution_evidence()
        except Exception as exc:
            receipt["errors"].append(
                f"backend execution evidence unavailable: {type(exc).__name__}: {exc}"
            )
            receipt["status"] = "failed"

    receipt["finished_at_utc"] = utc_now()
    receipt["duration_seconds"] = round(time.monotonic() - monotonic_started, 6)
    try:
        atomic_write_json(receipt_path, receipt)
    except (OSError, GateError) as exc:
        print(f"error: could not write receipt: {exc}", file=sys.stderr)
        return 2

    summary = {
        "schema_version": SCHEMA_VERSION,
        "gate": GATE_NAME,
        "execution_mode": execution_mode,
        "status": receipt["status"],
        "receipt": receipt_rel,
        "errors": receipt["errors"],
    }
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True, ensure_ascii=False))
    elif receipt["status"] == "passed":
        print(f"PASS: machine regression receipt written to {receipt_rel}")
    else:
        print(f"FAIL: machine regression receipt written to {receipt_rel}", file=sys.stderr)
        for error in receipt["errors"]:
            print(f"- {error}", file=sys.stderr)
    return 0 if receipt["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
