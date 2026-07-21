#!/usr/bin/env python3
"""Build a deterministic, public-only ICPC Light blind-solve plan.

The script only writes (or prints) a plan.  It does not choose public files,
stage workspaces, or launch solver processes.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

from statement_resources import StatementResourceError, load_statement_resources


DEFAULT_NEUTRAL_COUNT = 2
DEFAULT_DECEPTIVE_COUNT = 2
DEFAULT_WORKSPACE_ROOT = "blind-solves/icpc-light"
REQUIRED_MODEL = "gpt-5.6-sol"
DEFAULT_REASONING_EFFORT = "ultra"
REQUIRED_REASONING_EFFORT = "ultra"
MAX_WAVE_COUNT = 3
PHASES = ("initial", "replacement", "focused-neutral", "focused-deceptive")

DECEPTIVE_FOCUS = (
    (
        "greedy rules, sorting keys, local exchange arguments, monotonicity, "
        "and arbitrary tie handling"
    ),
    (
        "dropping one state component, splitting a global constraint into local "
        "constraints, no-crossing assumptions, and independence assumptions"
    ),
    (
        "shortest path, matching, min-cut, flow, cost-flow, and simple "
        "constructive templates"
    ),
)

NEUTRAL_FOCUS = (
    (
        "derive invariants and a proof before optimizing; audit every hidden "
        "assumption against the full bounds"
    ),
    (
        "write a tiny exact oracle or exhaustive enumerator first, use it to "
        "discover structure, and stress every proposed optimization"
    ),
    (
        "seek alternative representations and standard reductions, compare at "
        "least two complete routes, and choose only one with a worst-case proof"
    ),
)


def wave_count(raw: str) -> int:
    """Parse one wave's lane count; repeated waves provide persistence."""
    try:
        value = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if not 0 <= value <= MAX_WAVE_COUNT:
        raise argparse.ArgumentTypeError(
            f"must be between 0 and {MAX_WAVE_COUNT} for one ICPC Light wave"
        )
    return value


def positive_integer(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if value < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return value


def problem_relative_path(raw: str, *, option: str) -> str:
    """Normalize and validate a path that must remain below the problem root."""
    path = Path(raw)
    if not raw.strip() or path == Path("."):
        raise argparse.ArgumentTypeError(f"{option} must not be empty or '.'")
    if path.is_absolute():
        raise argparse.ArgumentTypeError(f"{option} must be problem-relative")
    if ".." in path.parts:
        raise argparse.ArgumentTypeError(f"{option} must not contain '..'")
    return path.as_posix()


def require_safe_problem_path(
    problem_dir: Path, relative: str, *, option: str
) -> Path:
    """Reject paths that escape the problem root or traverse existing symlinks."""
    candidate = problem_dir / relative
    try:
        candidate.resolve(strict=False).relative_to(problem_dir)
    except (ValueError, RuntimeError, OSError) as exc:
        raise argparse.ArgumentTypeError(
            f"{option} must resolve safely below the problem directory"
        ) from exc

    current = problem_dir
    for part in Path(relative).parts:
        current /= part
        if current.is_symlink():
            raise argparse.ArgumentTypeError(
                f"{option} must not traverse a symbolic link: {current}"
            )
    return candidate


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate one deterministic public-only ICPC Light blind-solve "
            "wave. The initial wave is 2-3 neutral plus 2-3 deceptive lanes; "
            "later waves replace failed lanes or add focused solvers."
        )
    )
    parser.add_argument(
        "--problem-dir",
        type=Path,
        required=True,
        help="Existing problem root; it is used as the base for every planned path.",
    )
    parser.add_argument(
        "--model",
        required=True,
        help=f"Required solver model; must be exactly {REQUIRED_MODEL!r}.",
    )
    parser.add_argument(
        "--neutral-count",
        type=wave_count,
        default=None,
        help="Neutral lanes in this wave (0-3; phase-specific default).",
    )
    parser.add_argument(
        "--deceptive-count",
        type=wave_count,
        default=None,
        help="Deceptive lanes in this wave (0-3; phase-specific default).",
    )
    parser.add_argument(
        "--phase",
        choices=PHASES,
        default="initial",
        help=(
            "Wave purpose: initial, replacement, focused-neutral, or "
            "focused-deceptive (default: initial)."
        ),
    )
    parser.add_argument(
        "--wave",
        type=positive_integer,
        default=1,
        help="Monotone wave number used in non-overwriting run IDs (default: 1).",
    )
    parser.add_argument(
        "--workspace-root",
        default=DEFAULT_WORKSPACE_ROOT,
        help=(
            "Problem-relative root for planned lane workspaces "
            f"(default: {DEFAULT_WORKSPACE_ROOT})."
        ),
    )
    parser.add_argument(
        "--plan-out",
        default=None,
        help=(
            "Problem-relative JSON destination. Defaults to "
            "<workspace-root>/sweep-plan.json."
        ),
    )
    parser.add_argument(
        "--reasoning-effort",
        default=DEFAULT_REASONING_EFFORT,
        help=f"Reasoning effort to record (default: {DEFAULT_REASONING_EFFORT}).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the plan to stdout without creating or writing any file.",
    )
    args = parser.parse_args()

    if not args.problem_dir.exists():
        parser.error(f"problem directory does not exist: {args.problem_dir}")
    if not args.problem_dir.is_dir():
        parser.error(f"problem directory is not a directory: {args.problem_dir}")
    args.model = args.model.strip()
    args.reasoning_effort = args.reasoning_effort.strip()
    if args.model != REQUIRED_MODEL:
        parser.error(f"--model must be exactly {REQUIRED_MODEL!r}")
    if args.reasoning_effort != REQUIRED_REASONING_EFFORT:
        parser.error(
            "--reasoning-effort must be exactly "
            f"{REQUIRED_REASONING_EFFORT!r}"
        )

    if args.phase == "initial":
        if args.wave != 1:
            parser.error("the initial phase must use --wave 1")
        args.neutral_count = (
            DEFAULT_NEUTRAL_COUNT
            if args.neutral_count is None
            else args.neutral_count
        )
        args.deceptive_count = (
            DEFAULT_DECEPTIVE_COUNT
            if args.deceptive_count is None
            else args.deceptive_count
        )
        if not 2 <= args.neutral_count <= 3:
            parser.error("the initial phase requires 2-3 neutral lanes")
        if not 2 <= args.deceptive_count <= 3:
            parser.error("the initial phase requires 2-3 deceptive lanes")
    elif args.phase == "focused-neutral":
        args.neutral_count = 2 if args.neutral_count is None else args.neutral_count
        args.deceptive_count = 0 if args.deceptive_count is None else args.deceptive_count
        if not 1 <= args.neutral_count <= 3 or args.deceptive_count != 0:
            parser.error(
                "focused-neutral requires 1-3 neutral lanes and 0 deceptive lanes"
            )
    elif args.phase == "focused-deceptive":
        args.neutral_count = 0 if args.neutral_count is None else args.neutral_count
        args.deceptive_count = 2 if args.deceptive_count is None else args.deceptive_count
        if args.neutral_count != 0 or not 1 <= args.deceptive_count <= 3:
            parser.error(
                "focused-deceptive requires 0 neutral lanes and 1-3 deceptive lanes"
            )
    else:
        args.neutral_count = 1 if args.neutral_count is None else args.neutral_count
        args.deceptive_count = 1 if args.deceptive_count is None else args.deceptive_count
        if args.neutral_count + args.deceptive_count < 1:
            parser.error("replacement requires at least one lane")

    if args.phase != "initial" and args.wave == 1:
        parser.error("supplementary phases must use --wave 2 or greater")

    try:
        args.workspace_root = problem_relative_path(
            args.workspace_root, option="--workspace-root"
        )
        if args.plan_out is None:
            plan_name = (
                "sweep-plan.json"
                if args.phase == "initial"
                else f"sweep-plan-wave-{args.wave:02d}.json"
            )
            args.plan_out = (Path(args.workspace_root) / plan_name).as_posix()
        else:
            args.plan_out = problem_relative_path(args.plan_out, option="--plan-out")
    except argparse.ArgumentTypeError as exc:
        parser.error(str(exc))

    args.problem_dir = args.problem_dir.resolve()
    workspace_path = Path(args.workspace_root)
    plan_path = Path(args.plan_out)
    if len(workspace_path.parts) < 2 or workspace_path.parts[0] != "blind-solves":
        parser.error(
            "--workspace-root must name a dedicated namespace below the "
            "problem's blind-solves/ directory"
        )
    if plan_path.parent != workspace_path or plan_path.suffix != ".json":
        parser.error(
            "--plan-out must name a JSON file directly inside --workspace-root"
        )
    try:
        require_safe_problem_path(
            args.problem_dir, args.workspace_root, option="--workspace-root"
        )
        args.plan_out_path = require_safe_problem_path(
            args.problem_dir, args.plan_out, option="--plan-out"
        )
    except argparse.ArgumentTypeError as exc:
        parser.error(str(exc))

    args.model = args.model.strip()
    args.reasoning_effort = args.reasoning_effort.strip()
    return args


def common_prompt(lane_id: str) -> str:
    return f"""You are one isolated lane in an ICPC public-only blind-solve sweep.

Your lane ID is `{lane_id}`. Record this exact ID in `final-status.md`.

The current working directory is this lane's workspace. Read contestant-visible material only from `public/`. Do not look outside the current workspace, use the internet, or search for non-public material. In particular, do not inspect an official solution, model solution, validator, checker, generator, hidden test, setter note, previous review, or another lane's output.

Work independently. Do not write raw chain-of-thought or a full reasoning trajectory. Report only compact claims and checkable evidence in the requested status file. If you accidentally see material outside the allowed public surface that could help solve the problem, stop and mark the lane contaminated in `final-status.md`."""


def neutral_prompt(index: int, lane_id: str, phase: str, wave: int) -> str:
    focus = NEUTRAL_FOCUS[(wave + index - 2) % len(NEUTRAL_FOCUS)]
    retry_context = (
        " Earlier public-only attempts did not yield a surviving verified full "
        "solution. Do not guess what they tried and do not seek their artifacts; "
        "start over independently."
        if phase == "focused-neutral"
        else ""
    )
    return common_prompt(lane_id) + f"""

Solve the complete problem as a contestant. Establish a worst-case-valid algorithm, implement it in `main.cpp`, and use local hand tests, brute force, exhaustive tiny tests, or random stress tests when useful.

For independent route diversity, emphasize this method: {focus}.{retry_context}

Before finishing, write `final-status.md` containing:
- contamination status;
- the claimed algorithm and a concise proof outline;
- time and memory complexity;
- artifacts produced and verification performed;
- unresolved doubts, if any, and the claimed verdict.

Both `main.cpp` and `final-status.md` are required."""


def deceptive_prompt(index: int, lane_id: str) -> str:
    focus = DECEPTIVE_FOCUS[(index - 1) % len(DECEPTIVE_FOCUS)]
    return common_prompt(lane_id) + f"""

Treat "this may be a scam problem (诈骗题)" as an attack hypothesis, not
as a conclusion.

The surface route suggested by the statement is overcomplicated. Using only the public contest material in `public/`, try to solve it with a simpler route. This lane should emphasize {focus}, while still considering all plausible simple shortcuts.

Prioritize greedy rules, sorting, local exchange, ignoring one state component, splitting a global constraint into local constraints, assuming monotonicity, assuming ties can be handled arbitrarily, no-crossing or independence assumptions, standard graph models such as shortest path / matching / min-cut / flow / cost-flow, and simple constructive templates.

Begin `final-status.md` with the exact lane ID and a contamination status of
`clean` or `contaminated`. For every serious candidate, then record:
1. the simplifying assumption;
2. why a contestant would believe it;
3. which ordinary tests it may pass;
4. the smallest counterexample found, or the counterexample search attempted;
5. exactly one provisional classification:
   - `wrong-solution candidate` when the route has a concrete failure or an
     unresolved proof / worst-case-complexity defect;
   - `shortcut/scam candidate` when it appears to be a materially simpler
     complete route and survives serious attack;
   - `suspicious alternative full route` when it remains plausible but is too
     incomplete or ambiguous for the shortcut/scam label.

If a route becomes concrete, you may implement the strongest candidate as `candidate.cpp` and attack it with hand tests, exhaustive tiny tests, or random tests. Candidate code is optional. This lane cannot confirm a scam or approve a shortcut: send every surviving simpler complete route to independent correctness review as a `shortcut/scam candidate`, and never force it into the wrong-solution list merely to satisfy a quota.

`final-status.md` is required; `candidate.cpp` is optional."""


def output_path(workspace_rel: str, filename: str) -> str:
    return (Path(workspace_rel) / filename).as_posix()


def make_run(
    *,
    kind: str,
    index: int,
    phase: str,
    wave: int,
    workspace_root: str,
    model: str,
    reasoning_effort: str,
) -> dict[str, object]:
    run_id = (
        f"{kind}-{index:02d}"
        if phase == "initial"
        else f"{kind}-w{wave:02d}-{index:02d}"
    )
    run_root = Path(workspace_root) / run_id
    workspace_rel = (run_root / "workspace").as_posix()
    public_materials_rel = (Path(workspace_rel) / "public").as_posix()

    if kind == "neutral":
        prompt = neutral_prompt(index, run_id, phase, wave)
        required_names = ("main.cpp", "final-status.md")
        optional_names: tuple[str, ...] = ()
    elif kind == "deceptive":
        prompt = deceptive_prompt(index, run_id)
        required_names = ("final-status.md",)
        optional_names = ("candidate.cpp",)
    else:  # Guard against accidental extension without defining its contract.
        raise ValueError(f"unsupported lane kind: {kind}")

    return {
        "id": run_id,
        "kind": kind,
        "phase": phase,
        "wave": wave,
        "launch_log_rel": (run_root / "raw-trace" / "codex-exec.jsonl").as_posix(),
        "model": model,
        "optional_outputs": [
            output_path(workspace_rel, name) for name in optional_names
        ],
        "prompt": prompt,
        "prompt_file_rel": (run_root / "prompt.txt").as_posix(),
        "public_materials_rel": public_materials_rel,
        "reasoning_effort": reasoning_effort,
        "required_outputs": [
            output_path(workspace_rel, name) for name in required_names
        ],
        "stderr_log_rel": (run_root / "raw-trace" / "stderr.log").as_posix(),
        "working_directory_rel": workspace_rel,
        "workspace_rel": workspace_rel,
    }


def build_plan(args: argparse.Namespace) -> dict[str, object]:
    runs = [
        make_run(
            kind="neutral",
            index=index,
            phase=args.phase,
            wave=args.wave,
            workspace_root=args.workspace_root,
            model=args.model,
            reasoning_effort=args.reasoning_effort,
        )
        for index in range(1, args.neutral_count + 1)
    ]
    runs.extend(
        make_run(
            kind="deceptive",
            index=index,
            phase=args.phase,
            wave=args.wave,
            workspace_root=args.workspace_root,
            model=args.model,
            reasoning_effort=args.reasoning_effort,
        )
        for index in range(1, args.deceptive_count + 1)
    )

    return {
        "schema_version": 2,
        "planner": "icpc-light-public-blind-solve-sweep",
        "path_base": "problem_dir",
        "phase": args.phase,
        "wave": args.wave,
        "workspace_root_rel": args.workspace_root,
        "model": args.model,
        "reasoning_effort": args.reasoning_effort,
        "run_counts": {
            "neutral": args.neutral_count,
            "deceptive": args.deceptive_count,
            "total": len(runs),
        },
        "isolation_policy": {
            "contestant_visible_material_only": True,
            "cross_lane_sharing_before_all_lanes_finish": False,
            "prompts_are_self_contained": True,
            "raw_reasoning_trajectory_required": False,
        },
        "planner_behavior": {
            "selects_or_stages_material": False,
            "creates_lane_workspaces": False,
            "launches_solver_processes": False,
            "lanes_should_run_in_parallel": True,
        },
        "runs": runs,
    }


def render_json(plan: dict[str, object]) -> str:
    return json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def atomic_write_text(path: Path, text: str) -> None:
    """Replace a regular plan file atomically without following a target symlink."""
    fd, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> int:
    args = parse_args()
    try:
        load_statement_resources(args.problem_dir)
    except StatementResourceError as exc:
        raise SystemExit(f"statement resource preflight failed: {exc}") from exc
    plan_text = render_json(build_plan(args))

    if args.dry_run:
        print(plan_text, end="")
        return 0

    plan_out = args.plan_out_path
    plan_out.parent.mkdir(parents=True, exist_ok=True)
    try:
        plan_out = require_safe_problem_path(
            args.problem_dir, args.plan_out, option="--plan-out"
        )
    except argparse.ArgumentTypeError as exc:
        raise SystemExit(str(exc)) from exc
    if plan_out.exists():
        raise SystemExit(
            f"refusing to overwrite existing sweep plan: {args.plan_out}; "
            "reuse it or allocate the next --wave"
        )
    atomic_write_text(plan_out, plan_text)
    print(f"Wrote sweep plan to problem-relative path: {args.plan_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
