# Orchestration

Own the run state and stage boundaries. Delegate stage work, but keep policy in
this skill rather than in agent entrypoints.

Load [artifact-contracts.md](artifact-contracts.md) before creating run state or
handoffs and [readiness.md](readiness.md) before routing a grade or verdict.
Before either action, run the read-only statement resource preflight. Require
one explicit TL and one explicit ML in `statement.md`; on a missing,
conflicting, unsupported, or out-of-range declaration, return failure before
writing `audit/run-state.md` or launching any agent.

Dispatch every agent with `gpt-5.6-sol` and reasoning effort `xhigh`. Reject a
handoff whose recorded execution configuration differs. Model unavailability
is a workflow failure, never permission to substitute a cheaper or weaker run.
Launch every non-blind stage with `scripts/run_stage_agent.py`; a Markdown
model field is descriptive only and cannot replace its production receipt.
Do not let that stage spawn unreceipted nested agents. Local compilation and
locally run generator, validator, checker, solution, or wrong-route processes
are not production judge evidence in ver3. Route those submitted-program workloads
through the CPIdeas Program × Dataset adapter and LightCPVerifier. Agent/Codex
launchers remain separate orchestration processes and continue to use their
own receipted execution paths.

Treat CPIdeas Plus importability and compatible attested LightCPVerifier
health as production gate prerequisites. Require the expected dataset/API/
compiler revisions, client/adapter hashes, build/image identity and execution
policy, then bind actual per-invocation chunk evidence. A missing dependency,
mismatched identity or unavailable service fails the owning gate; it does not
permit a host-process fallback. Use
`--test-mode --execution-backend local` only for an explicit compatibility
test, and never advance completion or readiness from that receipt. Production
backend setup and ver2 migration are documented in
[the bundle migration guide](../../../MIGRATION.md).

## Profiles and Wall-Clock Budgets

Treat time ranges as parallelization and monitoring estimates, not deadlines or
substitutes for evidence, except for the explicit 7,200-second whole-blind-stage
deadline below. Exceeding an estimate never authorizes stage omission, scope
reduction, or a weaker successful result.

| profile | target | default shape |
| --- | --- | --- |
| `L0-simple-standard` | 30--60 minutes | P1, 2+2 blind lanes, 3--5 qualified wrong solutions, one adversarial round |
| `L1-ordinary` | 60--120 minutes | P2, 2+2 blind lanes, 5--8 qualified wrong solutions, 20--50 purposeful data families, one round |
| `L1G-greedy-deceptive` | 75--150 minutes | P2 plus emphasis on exchange, ordering, ties, and false monotonicity |
| `L1C-constructive-output` | 75--150 minutes | P2 plus checker, witness-legality, and malformed-output emphasis |
| `L1F-flow-model-like` | 75--150 minutes | P2 plus one alternative-model review and reduction-faithfulness cases |
| `L2-high-risk` | 120--240 minutes | P3, 8--10 qualified wrong solutions, one to three evidence-triggered adversarial rounds |
| `outside-light` | no automatic Light budget | stop and write an escalation handoff |

Use these stage budgets while parallelizing independent work:

| stage | budget | owner | required result |
| --- | --- | --- | --- |
| 0. blind batch | 10--25 min per wave; 120 min hard total | blind-solve-sweep | clean initial 2+2, retained outputs, and at least one independently verified full route; repeat within deadline or report failure |
| 1. grade and route | 5--10 min | review, dispatched by orchestrator | data-buildability grade and workflow profile |
| 2. freeze contract | 5--10 min | review | unambiguous `audit/contract.md` |
| 3. review solution | 10--25 min | review | independent correctness evidence or escalation |
| 4. executable skeleton | 10--20 min | build-and-harden | reproducible model/oracle/validation chain |
| 5. differential testing | 10--20 min | build-and-harden | tiny exhaustion or qualifying random stress |
| 6. wrong routes and data | 15--35 min per active round | build-and-harden | strongest-natural qualified wrong solutions, machine survivability/breaker evidence, and compact bound coverage matrix |
| 7. harden and regress | 10--20 min per permitted round | build-and-harden | recorded round evidence and final full regression |
| 8. readiness | 5--10 min | readiness-review | `go`, `hold`, or `escalate` |

## Drive the State Machine

1. Run `scripts/verify_statement_resources.py --problem-dir "$PROBLEM_DIR"`.
   Stop immediately on failure; do not guess TL/ML or create run artifacts.
2. Discover statement, public attachments, solution material, and existing
   package/audit artifacts. Preserve valid existing work and record gaps.
3. Create or update `audit/run-state.md` using the artifact contract. Never
   infer that an existing artifact passed merely because it exists. Record and
   enforce `gpt-5.6-sol` / `xhigh` before dispatch.
4. Freeze a reproducible contestant public surface. Actually launch the initial
   2+2 blind wave and wait for every child. Replace any failed, contaminated,
   or incomplete lane from a fresh workspace until the initial batch is clean.
5. Independently check every claimed-full neutral result. If none is verified,
   launch a fresh focused-neutral wave against the unchanged public surface,
   preserve the rejected attempts, and repeat steps 3--4. Do not expose a retry
   lane to earlier lane output, counterexamples, or private material.
6. Run the executable blind-stage gate. Do not invoke the grader while the gate
   fails.
7. Run the buildability grader. Require schema version 2 and consume its
   preclassification, compatibility grade, quota, round limit, shortcut state,
   decision, and freshly written
   `audit/private/selected-standard-route.cpp` under `readiness.md`. For
   `scam_status: none` or `suspected`, that file must be an exact copy of the
   active verified blind route. Only an independently proved executable simpler
   route may replace it and set `scam_status: confirmed`.
8. Stop on S-stop/D3 only for an unverifiable foundation. If `decision` is
   `escalate`, including every suspected shortcut, write a handoff and pause
   the automatic path. A confirmed simpler route is not a stop: classify the
   selected route by the P1/P2/P3 data axes, require `decision: continue`, and
   route it into the matching profile before freezing the contract.
9. Require independent solution evidence before treating `std` as an oracle.
   Run the stage-internal three-step gate: review writes
   `solution-review-draft.md`, retaining the active blind source as a safety
   root and justifying Standard Route Adoption; build materializes only
   `package/std.cpp` from the fixed selected source; a second fresh reviewer
   compiles/tests that exact source and writes canonical `solution-review.md`
   whose std provenance hash-binds the selected source. Do not unlock
   data/hardening before the final source review passes; never substitute a
   small oracle or special-family answer generator for it.
10. Build, stress, and attack for the recorded number of permitted adversarial
   rounds, then run the canonical machine regression executor from a clean
   reproducible plan. Do not trust a regression frontmatter status by itself.
11. Run the executable completion gate to create
    `audit/completion-gate.json`, then give that hash-bound receipt, the audit
    artifacts, and the release candidate to the readiness reviewer. After it
    writes `audit/readiness.md`, run the final readiness verifier; an assertion
    without these receipts is not a completed `go`.

Update `run-state.md` after every stage transition. Record the required model
and reasoning effort, the formal
preclassification, compatibility grade/profile, shortcut state, wrong-solution
target and qualified count, adversarial round current/limit/status, evidence
paths, blind status/wave/attempt counts/claimed-full and verified-full counts,
blind start/deadline/elapsed/failure reason, current retry reason, lane counts,
current blocker, `repair_used`, and next action.

For each non-blind dispatch, first write a task-specific prompt below
`audit/private/stage-prompts/`, then run:

```bash
python3 "$SKILL_ROOT/scripts/run_stage_agent.py" \
  --problem-dir "$PROBLEM_DIR" \
  --stage STAGE_ID \
  --run-id UNIQUE_RUN_ID \
  --prompt-file audit/private/stage-prompts/PROMPT.txt \
  --model gpt-5.6-sol \
  --reasoning-effort xhigh
```

Use the fixed stage IDs in [artifact-contracts.md](artifact-contracts.md).
Never add `--test-command` to a production workflow. A child final message is
only a progress signal; inspect the current production receipt and run the
semantic handoff verifier before advancing.

## Persist Through Blind Solves

Use a small initial wave, then as many small focused-neutral or replacement
waves as fit before the hard deadline. The 2--3 lane limit applies to one
wave's parallel fan-out, not to the run's lifetime; there is no retry-count
cap. The runner fixes the whole blind stage's deadline at 7,200 seconds after
the first production lane launch, carries it across waves, and terminates
unfinished children when it expires.

These are retry conditions, not blockers: no solver found a full route, a full
claim was disproved, a solver crashed or timed out, required output is empty,
or a lane contaminated itself. Preserve each attempt under a unique wave/run ID
and launch a fresh lane. A later counterexample against the only verified blind
route invalidates downstream std, regression, completion, and readiness. Reopen
the loop only if the original shared blind deadline has not expired; never reset
that clock. At or after the deadline, report terminal blind failure/escalation
instead of launching a new wave. This does not consume `repair_used`.

Before the deadline, lack of an official solution and repeated unsuccessful
solves are retry conditions. At the deadline, if the blind gate still fails,
set `blind_status: failed`, record `blind_failure_reason:
time-limit-exceeded`, write `audit/escalation-handoff.md`, and stop the entire
automatic pipeline. Other immediate failures are an ambiguous public surface,
inability to launch any isolated solver after recorded recovery attempts, no
possible independent correctness authority, exact-model unavailability, or
user cancellation. No waiver may replace the verified-full requirement.

## Enforce Every Stage Handoff

The orchestrator remains active while delegated work runs, waits for every
child, and inspects the saved artifact rather than trusting the child's final
message. Apply these completion tests before advancing:

| stage | pass evidence | incomplete action |
| --- | --- | --- |
| blind execution | production runner receipts and retained outputs | replace/retry before deadline |
| blind correctness | production independent-review receipt and blind gate | run another review or focused wave |
| preclassification | complete schema-v2 routing decision plus freshly verified fixed selected source | rerun grader or stop/escalate |
| contract/solution draft | frozen contract, verified proof, active-blind safety root, and substantive Standard Route Adoption | rerun review; do not materialize an unreviewed route |
| std materialization | non-empty source plus exact-copy/adapted classification relative to the fixed selected source | rerun materializer; do not build data |
| concrete std review | fresh compile, proof/delta audit, current hashes, and std provenance bound to the fixed selected source; sample/tiny fields explicitly pending machine regression | rerun fresh review; keep hardening locked |
| package/differential | compilable mandatory programs and qualifying exhaustion/stress | repair once or escalate |
| wrong routes/data | exact qualified quota and observed breaker matrix | continue the permitted round or escalate |
| regression | every machine regression field passed with replay command | rerun regression; never request readiness |
| readiness | current completion receipt and final readiness verifier | hold/escalate; never emit `go` |

Every non-blind row also requires the ordered, current
`audit/private/stage-executions/<stage>/current.json` receipt written by
`run_stage_agent.py`. The completion gate rejects a missing receipt, a test
override, a different model/effort, stale input/output hashes, a changed output
tree, or a downstream stage whose timestamp precedes its prerequisite.

The stage runner executes the semantic handoffs itself: preclassification
before `solution-draft`, `verify_solution_draft_handoff.py` before
`std-materialization`, `verify_std_materialization_handoff.py` before
`solution-validation`, and `verify_solution_handoff.py` before
`build-hardening`. Before `readiness`, it runs
`verify_completion_handoff.py`, which performs the canonical completion and
regression replay itself; a forged, stale, or failing completion result
prevents the readiness agent from launching. It recursively revalidates the complete prior-receipt chain,
including current prompt/input/output/tree hashes, exact model/effort command,
fresh-output proof, Codex JSONL completion, and verifier hashes. A stale nested
receipt is a launch refusal, not a warning.

Production reruns are reversible but never reuse canonical outputs: the runner
archives the stage-owned old files/trees below the new immutable attempt before
launch, then requires the agent to recreate every required canonical output.
Optional owned outputs are also archived but may remain safely absent. On failure,
leave those archived predecessors in place, keep canonical outputs incomplete,
and redispatch the owning stage; never copy the predecessor forward as if the
failed attempt completed.

A missing file, placeholder, partial result, timeout, nonzero command, or
“continue later” statement is a failed stage result. Redispatch the owning
stage in a fresh exact-model context while its explicit scope remains. When a
hard limit is exhausted, stop and report failure/escalation at that stage; do
not call downstream agents to make the run look complete.

## Bound Adversarial Rounds

Run exactly one initial adversarial round for every continuing profile. P1 and
P2 end general attack work after that round. P3 may open a second or third
round only for a named important survivor, an uncovered proof boundary, or a
new independent failure mechanism with a concrete new attack hypothesis.
Record every round, including its trigger, changed breaker evidence, killed
routes, survivors, commands, and result, in `audit/adversarial-rounds.md`.

An additional P3 round must add material information. Escalate if a round makes
no material progress, the same important survivor remains after two focused
rounds, the third round ends with an important survivor, or confidence would
require an unbounded or external-specialist attack. Finishing early is allowed
once the profile quota and all gates are satisfied. Never exceed the grade
report's `adversarial_round_max`.

## Bound Repairs

Share one targeted repair budget across the entire run after the executable
differential chain exists. A stage-5 semantic discrepancy, a concrete defect
in `std`, oracle, validator, checker, or harness, or a readiness `hold` all
consume the same `repair_used` flag. Require a known verification step, set
`repair_used: true` before the repair, rerun every affected check plus the full
regression, and request a fresh readiness decision when applicable. Initial
compilation and harness setup before the first stress run do not consume this
budget. A planned P3 adversarial round does not consume it.

Do not restart all earlier stages automatically. If a second discrepancy or
defect appears after `repair_used` becomes true, escalate. Mandatory blind
replacement and focused-neutral waves remain governed by `blind-solve.md` and
do not consume this package-repair budget. Do not disguise a new general attack
round as a repair or vice versa.

## Keep Work Out of Scope

Reject these as default Light stages:

- subtask or partial-score design;
- adapted-statement blind solves;
- route-by-subtask verdict matrices;
- UOJ packed/min/dependency semantics;
- mandatory mechanism/composition/scale/noise ledgers;
- four-reviewer sweeps per ledger layer;
- implementation of every proposed route;
- extraction of full reasoning trajectories;
- unbounded adversarial reruns.
