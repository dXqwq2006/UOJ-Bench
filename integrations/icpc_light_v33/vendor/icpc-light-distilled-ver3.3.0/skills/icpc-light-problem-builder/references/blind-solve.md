# Blind Solve

Run a small independent contestant-view batch before grading risk. Preserve
isolation so the results measure natural routes rather than leaked hindsight.
Load [artifact-contracts.md](artifact-contracts.md) before writing the plan or
summary handoff.

## Contents

- Build the plan
- Stage the public surface
- Launch and wait
- Require lane outputs
- Direct the deceptive lanes
- Verify full claims and repeat until complete

## Build the Plan

Require the exact model `gpt-5.6-sol` with reasoning effort `ultra`. Check that
pair on the local Codex surface, record it in `audit/run-state.md`, and stop with
failure if it is unavailable. Do not fall back or interpret "latest" as an
acceptable substitute. Set `MODEL=gpt-5.6-sol` for the commands below.

Resolve the planner from the current skill root and quote paths:

```bash
python3 "$SKILL_ROOT/scripts/build_sweep.py" \
  --problem-dir "$PROBLEM_DIR" \
  --model "$MODEL" \
  --reasoning-effort ultra \
  --phase initial \
  --wave 1 \
  --neutral-count 2 \
  --deceptive-count 2
```

Treat `--problem-dir`, `--model`, `--neutral-count`, `--deceptive-count`, and
`--phase`, `--wave`, `--workspace-root`, `--plan-out`, `--reasoning-effort`, and
`--dry-run` as the planner interface. The initial phase requires two or three
lanes of each kind and wave 1. `--dry-run` previews but does not satisfy the
stage; execute again without it to save the plan.

Use `replacement` for fresh substitutes after launch failure, contamination,
or missing output. Use `focused-neutral` when no claimed-full route survives
review, and `focused-deceptive` only when the initial deceptive evidence itself
was incomplete. Supplementary waves use `--wave 2` and then strictly increasing
wave numbers. They allow one to three lanes of the selected kind per wave. The
per-wave maximum bounds parallel fan-out, not total attempts; there is no
lifetime attempt-count limit. All waves and independent reviews still share the
hard 7,200-second deadline measured from the first production lane launch.

Keep the default workspace root at `blind-solves/icpc-light`; use overrides
only for isolated testing. Require every override to name its own
`blind-solves/<namespace>` directory. Keep manager-only plans and execution
results directly in that namespace; never place them in a lane workspace.

Make the JSON deterministic for identical arguments: use stable unique wave/run
IDs, problem-relative workspace paths, no timestamps, and no absolute machine
paths. The initial plan is `sweep-plan.json`; later plans are
`sweep-plan-wave-NN.json`. Refuse to overwrite either plans or attempts. The
plan must contain only contestant-surface references, prompts, workspace paths,
expected outputs, and execution metadata; it must not name or stage private
solution, checker, generator, topic, audit, or prior-lane files.

Use `neutral-01`, `neutral-02`, `deceptive-01`, and `deceptive-02` for the
default order and wave-qualified IDs such as `neutral-w02-01` later. Record the
retry reason and next wave in `audit/run-state.md` before planning it.

## Stage the Public Surface

Give every lane its own self-contained workspace. Copy only material available
to contestants at contest time:

- statement and samples;
- public limits, protocol, stub, or attachment;
- public clarifications that belong to the simulated contest surface.

Exclude official or accepted solutions, editorials, setter notes, validators,
checkers, generators, hidden tests, audit files, topic catalogs, previous
reviews, and every other lane's workspace or output. Do not let a solver search
parent directories, repository metadata, home directories, process lists, the
internet, or unrelated workspace files.

Reject symlinks in staged public material and save a manager-side inventory of
the staged files. Use the strongest filesystem-read and network sandbox the
execution surface provides, restricted to the lane workspace. Record
`isolation_mode: enforced` and the mechanism when this is mechanically
enforced. If the surface cannot enforce it, record
`isolation_mode: trust-based`, do not describe the lane as physically isolated,
and lower confidence in downstream grading. Treat any actual or uncertain
boundary read as contamination.

Run planning and staging only while the manager controls a quiescent problem
tree. Abort if another process can concurrently replace problem-path
components; the planner rejects symlinks at validation and immediately before
writing, but it is not a security boundary against a hostile concurrent
filesystem writer.

Launch all initial lanes in parallel. Do not merge or disclose their results
until every lane is finished. Mark a lane contaminated and exclude its route
claims from grading if it reads private or cross-lane material. Preserve its
raw attempt privately for audit and recovery, but promote only a compact
contamination report to downstream stages.

Create an explicit manager-side public manifest before launching. It must use
this schema and every digest must match the source file at launch time:

```json
{
  "schema_version": 1,
  "files": [
    {"path": "statement.md", "sha256": "<64 lowercase hex digits>"}
  ]
}
```

Paths are problem-relative. Include every genuinely public attachment needed
to solve the problem and nothing private. Then run the saved plan with the
runner from the current skill root:

```bash
python3 "$SKILL_ROOT/scripts/run_sweep.py" \
  --problem-dir "$PROBLEM_DIR" \
  --plan blind-solves/icpc-light/sweep-plan.json \
  --public-manifest blind-solves/icpc-light/public-manifest.json
```

The runner, not the plan file, is the evidence that solvers were actually
started. It stages the hash-checked public files, launches every lane in the
wave before waiting for any lane, waits for all child processes, checks their
declared outputs, and writes `sweep-plan-results.json`. A dry-run, a saved plan,
an orchestrator assertion, or hand-created lane files cannot substitute for a
successful runner result.

The initial runner receipt freezes `blind_started_at_utc` and a deadline 7,200
seconds later. Every later production wave and independent review must inherit
that deadline. The runner terminates remaining children at expiry and refuses
to launch another production attempt after it. This is a failed blind stage,
not a timeout that may be waived.

For each run, write the exact plan prompt to its `prompt_file_rel`, outside the
solver-visible workspace. Start a fresh solver session with the planned
workspace, model, and reasoning effort; feed the prompt through stdin; capture
JSONL stdout and diagnostic stderr at the planned log paths. When launching
several shell processes, keep the parent launcher alive and wait for every
child before collecting outputs. Do not let background sessions outlive the
launcher or share a session/context.

Keep every lane workspace, prompt, declared output, execution result, stdout,
and stderr under `blind-solves/icpc-light/`, outside `audit/`. Never overwrite
or delete a failed, contaminated, incomplete, or superseded attempt during the
run. Promote only the compact summary and required evidence links into
`audit/`. Treat JSONL as a private recovery log, not as a reasoning trajectory,
and never feed raw logs or earlier-lane artifacts to later solvers, graders, or
reviewers. Retention exists for audit and recovery, not for cross-lane hints.

## Require Lane Outputs

Require every neutral lane to produce:

- `main.cpp` containing its best submission;
- `final-status.md` containing lane ID, contamination status, claimed route,
  proof status, complexity, tests performed, and claimed verdict.

For the contamination field, prefer the canonical
`contamination_status: clean | contaminated` spelling. Both the review runner
and the final blind-stage gate also recognize an explicit semantic
`Contamination: No | Yes` field and `uncontaminated` as a clean alias
(including a Markdown-formatted value and an explanation after punctuation).
They fail closed on a missing, ambiguous, unrecognized, or conflicting value,
including conflicts later on the same line; `Yes` and `contaminated` are never
clean.

Require every deceptive lane to produce `final-status.md`. Allow candidate code
but do not require it. Require the status to list, for each useful candidate:

1. simplifying assumption;
2. why a contestant would believe it;
3. ordinary cases it may pass;
4. smallest counterexample found or search attempted;
5. classification as a wrong-solution candidate, shortcut/scam candidate, or
   suspicious alternative full route.

Do not request or retain full chain-of-thought, raw reasoning trajectories, or
cross-lane synthesis. Retain only artifacts and compact evidence needed for the
later review.

## Direct the Deceptive Lanes

Tell each deceptive lane confidently that the problem may be a scam problem
whose surface route is overcomplicated. Direct it to seek a simpler route using
only public material. Prioritize greedy and sorting rules, local exchange,
dropping one state component, splitting a global constraint into local ones,
false monotonicity or independence, arbitrary tie handling, no-crossing
assumptions, standard shortest-path/matching/min-cut/flow models, and simple
constructive templates.

Require concrete routes to be implemented and attacked with hand tests, tiny
exhaustion, or random tests when practical. Treat “scam problem” as an attack
hypothesis, not a lane verdict. A lane cannot confirm it by assertion alone.
Forbid calling a route correct without a proof. Promote an unbroken plausible
route to independent review as a shortcut/scam candidate or suspicious full
route; never label it wrong merely because the intended solution differs, and
never use it merely to fill a wrong-solution quota.

## Verify Full Claims and Repeat Until Complete

After every wave, independently review every neutral lane that claims a full
solution. Compile its `main.cpp`; check samples, the complete input/output
contract, proof obligations, worst-case complexity, boundary cases, and code
against the claimed algorithm; use an independently written tiny oracle or
exhaustion where practical. Record accepted and rejected claims in
`audit/blind-claim-reviews.json` using the schema in
[artifact-contracts.md](artifact-contracts.md). A lane cannot review itself.

Launch that reviewer in a fresh context with the provided executor, one claim
at a time:

```bash
python3 "$SKILL_ROOT/scripts/run_blind_review.py" \
  --problem-dir "$PROBLEM_DIR" \
  --attempt-id blind-solves/icpc-light/neutral-01/workspace \
  --review-id review-neutral-01 \
  --reviewer-id independent-review-01 \
  --model "$MODEL" \
  --reasoning-effort ultra
```

Inspect the compact report and the printed review object. On a successful
production review, the executor atomically appends that exact object to
`audit/blind-claim-reviews.json`; do not hand-copy it. The executor binds the
exact candidate and report hashes, saves its production `codex exec` receipt, and refuses a
non-clean or non-production solve attempt. Its `--review-command` override is
testing-only; the production blind gate rejects such a receipt.
New claim objects use `active: true` and `invalidated_by: null`. Never delete a
disproved review: switch it to `active: false` and bind `invalidated_by` to the
saved counterexample or fresh rejection report. Gates ignore inactive claims.

Use a fresh, non-overwriting wave whenever the stage is incomplete:

- launch failure, crash, timeout, contamination, or missing required output:
  run a `replacement` wave for the affected kind;
- no neutral full-solution claim survives independent review: run a
  `focused-neutral` wave;
- fewer than two clean deceptive lanes: run a `focused-deceptive` or
  `replacement` wave;
- a later checker, oracle, stress test, or proof review disproves the only
  active verified blind route: revoke that review and return to a new
  `focused-neutral` wave only when the original shared deadline still has
  remaining time; otherwise stop with blind-stage failure. Never reset the
  first-launch clock.

Generate supplementary plans with strictly increasing wave numbers and run
each with `run_sweep.py`, for example:

```bash
python3 "$SKILL_ROOT/scripts/build_sweep.py" \
  --problem-dir "$PROBLEM_DIR" \
  --model "$MODEL" \
  --reasoning-effort ultra \
  --phase focused-neutral \
  --wave 2

python3 "$SKILL_ROOT/scripts/run_sweep.py" \
  --problem-dir "$PROBLEM_DIR" \
  --plan blind-solves/icpc-light/sweep-plan-wave-02.json \
  --public-manifest blind-solves/icpc-light/public-manifest.json
```

Two or three lanes is a per-wave parallelism bound, never a lifetime attempt
limit. Ten minutes, twenty-five minutes, repeated rejected ideas, or the lack
of an official solution are not early stopping conditions. Continue launching
fresh focused waves until at least one complete neutral route is independently
verified or the shared 7,200-second deadline expires. On expiry, terminate
remaining blind/review children, preserve all attempts, set `blind_status:
failed`, record `blind_failure_reason: time-limit-exceeded`, write
`audit/escalation-handoff.md`, and report failure without invoking the grader or
any package-construction stage.

Summarize every clean, failed, contaminated, and superseded attempt in
`audit/blind-summary.md`, including each candidate classification and the
aggregate shortcut/scam status. Maintain the exact blind-stage counters and
status fields in `audit/run-state.md`, then run:

```bash
python3 "$SKILL_ROOT/scripts/verify_blind_stage.py" \
  --problem-dir "$PROBLEM_DIR"
```

Do not invoke the buildability grader, freeze a release algorithm, or construct
tests until this command succeeds. The blind stage has no normal partial or
“data-only” terminal state.
