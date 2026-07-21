#!/usr/local/bin/python3 -I
"""Frozen Codex provider gate for the zero-mount production agent image.

The production runtime gives agents placeholder credentials and access only to
the internal credential relay.  Codex 0.144 does not reliably derive a custom
Responses provider from ``OPENAI_BASE_URL`` alone, especially together with
``--ignore-user-config``.  This wrapper therefore binds every permitted
``codex exec`` invocation to the audited relay provider through one-off CLI
configuration overrides.  It never reads or distributes the real API key.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath


REAL_CODEX = "/usr/local/bin/codex"
PLACEHOLDER_API_TOKEN = "skill-eval-placeholder-token"
INTERNAL_RELAY_BASE_URL = "http://credential-relay:8080/v1"
EXACT_TATU_CODING_BASE_URL = (
    "https://maas.tatucloud.com/deployer/coding_tatu/v1"
)
REQUESTED_CODEX_SANDBOX = "workspace-write"
MODEL_PROBE_SANDBOX = "read-only"
SHORTCUT_REVIEW_SANDBOX = "danger-full-access"
CODEX_SANDBOX_BYPASS = "--dangerously-bypass-approvals-and-sandbox"
SHORTCUT_REVIEW_PROMPT = "Read /input/PROMPT.md and perform that review exactly."
EXACT_MODEL = "gpt-5.6-sol"
EXACT_REASONING_EFFORT = "xhigh"
PROVIDER_OVERRIDES = (
    "--strict-config",
    "-c",
    'model_provider="noc"',
    "-c",
    'model_providers.noc.name="noc"',
    "-c",
    'model_providers.noc.base_url="http://credential-relay:8080/v1"',
    "-c",
    'model_providers.noc.env_key="OPENAI_API_KEY"',
    "-c",
    'model_providers.noc.wire_api="responses"',
    "-c",
    "model_providers.noc.supports_websockets=false",
    "-c",
    "features.plugins=false",
    "-c",
    "features.remote_plugin=false",
    "-c",
    "features.plugin_sharing=false",
)

_COMMON_EXEC_ARGUMENTS = (
    "exec",
    "--json",
    "--ephemeral",
    "--skip-git-repo-check",
    "--ignore-user-config",
    "--ignore-rules",
    "--model",
    EXACT_MODEL,
    "-c",
    f'model_reasoning_effort="{EXACT_REASONING_EFFORT}"',
)
_MODEL_PROBE_ARGUMENTS = _COMMON_EXEC_ARGUMENTS + (
    "--sandbox",
    MODEL_PROBE_SANDBOX,
    "-",
)
_PHYSICAL_BLIND_ARGUMENTS = _COMMON_EXEC_ARGUMENTS + (
    "--cd",
    "/workspace",
    CODEX_SANDBOX_BYPASS,
    "-",
)
_SHORTCUT_REVIEW_ARGUMENTS = _COMMON_EXEC_ARGUMENTS + (
    "--sandbox",
    SHORTCUT_REVIEW_SANDBOX,
    "--cd",
    "/output",
    SHORTCUT_REVIEW_PROMPT,
)


class WrapperError(RuntimeError):
    """The requested invocation does not satisfy the production boundary."""


def _validate_environment(environment: Mapping[str, str]) -> None:
    expected = {
        "OPENAI_API_KEY": PLACEHOLDER_API_TOKEN,
        "CODEX_API_KEY": PLACEHOLDER_API_TOKEN,
        "OPENAI_BASE_URL": INTERNAL_RELAY_BASE_URL,
        "SKILL_EVAL_UPSTREAM_BASE_URL": EXACT_TATU_CODING_BASE_URL,
    }
    if any(environment.get(name) != value for name, value in expected.items()):
        # Deliberately do not include an observed value in this diagnostic.
        raise WrapperError("Codex production environment binding is invalid")


def _canonical_native_workspace(value: str) -> bool:
    """Return whether *value* is a canonical path within ``/work/cell``."""

    path = PurePosixPath(value)
    root = PurePosixPath("/work/cell")
    return (
        path.is_absolute()
        and str(path) == value
        and all(ord(character) >= 32 and character != "\x7f" for character in value)
        and ".." not in path.parts
        and (path == root or root in path.parents)
    )


def _effective_formal_arguments(arguments: Sequence[str]) -> tuple[str, ...]:
    """Accept only the four argv grammars emitted by the frozen v3 runtime."""

    requested = tuple(arguments)
    if requested == _MODEL_PROBE_ARGUMENTS:
        return requested
    if requested == _PHYSICAL_BLIND_ARGUMENTS:
        return requested
    if requested == _SHORTCUT_REVIEW_ARGUMENTS:
        return requested

    native_prefix = _COMMON_EXEC_ARGUMENTS + (
        "--sandbox",
        REQUESTED_CODEX_SANDBOX,
        "--cd",
    )
    if (
        len(requested) == len(native_prefix) + 2
        and requested[: len(native_prefix)] == native_prefix
        and requested[-1] == "-"
        and _canonical_native_workspace(requested[-2])
    ):
        # A second bubblewrap namespace cannot be created inside the already
        # zero-mount, cap-dropped Docker boundary.  Translate only this exact
        # frozen native grammar; every alternative spelling fails closed.
        return _COMMON_EXEC_ARGUMENTS + (
            CODEX_SANDBOX_BYPASS,
            "--cd",
            requested[-2],
            "-",
        )
    raise WrapperError("production Codex received an unsupported formal execution shape")


def wrapped_argv(
    arguments: Sequence[str], environment: Mapping[str, str]
) -> tuple[str, ...]:
    """Return the absolute real-Codex argv for one audited invocation."""

    if list(arguments) == ["--version"]:
        return (REAL_CODEX, "--version")
    _validate_environment(environment)
    translated_arguments = _effective_formal_arguments(arguments)

    result = [REAL_CODEX, *translated_arguments]
    # Keep the audited prompt boundary final.  These are the final
    # configuration overrides, so an earlier ordinary
    # `-c model_reasoning_effort=...` cannot replace the provider/auth boundary.
    result[-1:-1] = PROVIDER_OVERRIDES
    return tuple(result)


def main() -> int:
    try:
        command = wrapped_argv(sys.argv[1:], os.environ)
    except WrapperError as exc:
        print(f"codex-wrapper: {exc}", file=sys.stderr, flush=True)
        return 64
    os.execv(REAL_CODEX, list(command))
    raise RuntimeError("real Codex exec unexpectedly returned")


if __name__ == "__main__":
    raise SystemExit(main())
