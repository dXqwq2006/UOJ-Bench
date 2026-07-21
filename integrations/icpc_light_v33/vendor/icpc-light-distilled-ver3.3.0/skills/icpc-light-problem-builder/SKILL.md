---
name: icpc-light-problem-builder
description: Build, finish, harden, or audit ordinary ICPC problem packages with persistent isolated public-only blind solves, a verified full-solve completion gate, formal three-level test-data preclassification, mandatory std/oracle/validator construction, brute-force differential testing, profile-sized wrong-solution attacks, bounded adversarial rounds, and machine-checked go/hold/escalate review. Use for local ICPC preparation where a full package must be produced and incomplete, data-only, or artifact-free work must not be accepted, without OI subtasks, partial scoring, UOJ packaging, or exhaustive route ledgers.
---

# ICPC Light Problem Builder

Build an ordinary ICPC package around one binary objective:

```text
correct solutions -> AC
important plausible wrong solutions -> reliably rejected
```

Treat this file and its directly linked references as the canonical ICPC Light
contract. Keep agent entrypoints thin; do not copy quotas, gates, or policy into
them. Do not invoke the existing OI orchestrator or its subtask, scoring,
implement-every-route, or large-ledger stages.

## Audit Statement Resources First

Before creating `audit/run-state.md`, a blind plan, a lane workspace, or any
other run artifact, run `scripts/verify_statement_resources.py` against the
problem root. The contestant statement must explicitly and unambiguously
declare both a time limit and a memory limit. Missing, conflicting, unsupported,
or out-of-range declarations fail the workflow immediately; do not infer a
limit from complexity prose or continue with a hidden default.

When authoring or revising the problem, choose those limits from the intended
algorithm, maximum legal scale, language/runtime assumptions, and measured
full-scale behavior. Preserve that reasoning in the private regression
`resource_policy`. A later statement edit, including a TL/ML edit, invalidates
the hash-bound resource policy and downstream judge evidence and requires a
clean rerun. This audit changes no release-package path or package payload.

## Enforce the Execution Model

Run the orchestrator, every stage agent, every blind lane, and every independent
reviewer with model `gpt-5.6-sol` and reasoning effort `ultra`. Record these
exact values in `audit/run-state.md` and in every executable receipt. Do not
fall back to another model, shorten reasoning effort, or silently reuse output
from a differently configured agent. If this exact pair is unavailable, stop
the automatic workflow and report failure; do not start a reduced-quality run.

Outside the dedicated blind fan-out, keep each stage in one receipted agent
context. Do not spawn an untracked nested agent. If a future implementation
adds child-agent execution, first extend the production receipt schema so the
completion gate can verify each child's exact command, model, and effort.
Keep agent-process parallelism inside the receipted stage instead of delegating
it to invisible agents. Submitted-program judging follows the separate sandbox
contract below; do not treat an agent process and a contestant/generator/checker
process as the same trust boundary.

## Enforce the Judge Execution Backend

In ver3, production package judging uses the backend-neutral Program × Dataset
contract with LightCPVerifier as the required execution backend. The canonical
regression gate retains ownership of the plan, checker contract, verdicts,
ordered hashes, and receipts; CPIdeas Plus and LightCPVerifier own the bottom
layer that compiles and runs generators, validators, std/brute programs,
checkers, release candidates, and wrong solutions.

Before starting a production judging gate, require a Python environment that
can import `cpideas_plus.evaluation.dataset` and a healthy LightCPVerifier
service. The default endpoint is `http://127.0.0.1:8081`; configure another one
with `--lightcpverifier-url` or `ICPC_LIGHT_LIGHTCPVERIFIER_URL`. Fail closed on
missing imports, an unhealthy service, transport/output/order errors, or
infrastructure checker results. Never fall back automatically to host
subprocesses.

The adapter preserves ordered per-case results and deterministically chunks
datasets into requests of at most 128. Read the statement-bound TL/ML from the
private resource policy and require the service to apply those exact values to
every candidate and validator run. TLE and MLE come from the sandbox result
under those limits; do not post-classify against a different hidden threshold.
Require hash-bound execution evidence for the exact client modules,
service/image identity, every chunk's requested/effective limits, peak time and
memory, and every ordered result; do not infer these facts from defaults.
The local backend exists only for compatibility testing and requires both
`--test-mode` and `--execution-backend local`; its receipt cannot satisfy
completion or readiness. Read the bundle-level [migration guide](../../MIGRATION.md)
before reusing ver2 artifacts.

The current Light profile has a fixed 16 MiB cap for each stdout/stderr stream;
unlike TL/ML, it does not yet accept a statement-designed output-limit policy.
If a legal intended output can approach or exceed that cap, stop and classify
the problem as outside the current automatic profile instead of treating the
resulting OLE as contestant evidence or silently changing the package.

Adversarial-round compilation/execution follows the same production backend,
and the chain verifier rejects local/test receipts. Production plans may use
`tokens`, `exact`, or source-bound `checker` comparison. Checker mode binds the
top-level source to exactly `package/checker.cpp`, compiles it as an isolated
role, and passes only explicit input/candidate/answer copy-ins. The legacy
arbitrary `checker_command` has no source/hash binding and remains
local-test-only.

## Resolve Resources

Resolve `SKILL_ROOT` as the directory containing this file and `SKILLS_ROOT` as
its parent. Resolve every script, reference, and sibling skill from those roots.
Never assume the source directory is named `src` or that its path has no spaces.

Use these sibling capabilities narrowly:

- Run [grade-test-data-buildability](../grade-test-data-buildability/SKILL.md)
  only after the executable blind-stage gate passes, and consume its stable
  audit artifact.
- Use [using-testlib](../using-testlib/SKILL.md) only for batch validator,
  reproducible generator, ordinary checker, and harness implementation
  details. Route interactive, communication, scored-output, or grader protocol
  work through the risk gates instead of expanding this workflow through that
  component skill.
- Read selected vendored topic leaf references only through the routing rules
  below. They are local knowledge cards, not an invitation to load or inherit
  any source OI workflow contract.

## Run the Workflow

1. Audit `statement.md` for explicit TL and ML. Stop without creating run
   artifacts if either is missing or ambiguous; only then inspect existing
   artifacts and create `audit/run-state.md`.
2. Actually launch and wait for two neutral and two deceptive isolated
   public-only lanes. Replace every failed, contaminated, or incomplete lane.
   If no full neutral route survives independent verification, launch fresh
   focused-neutral waves. Attempt count is unbounded within the blind stage's
   hard 120-minute wall-clock deadline.
3. Pass the executable blind-stage gate. Only then run preclassification,
   which freshly writes `audit/private/selected-standard-route.cpp`. With no
   shortcut or only a suspected one, select an exact copy of the active
   verified blind route; keep a suspected shortcut at `decision: escalate`.
   An independently proved executable simpler route may replace that copy,
   after which `scam_status: confirmed` still routes by the P1/P2/P3 data axes
   with `decision: continue`. Reserve S-stop for an unverifiable foundation.
   For a continuing P1/P2/P3 route, freeze the contract, review the
   algorithm/proof and route adoption, materialize `std`, and have a second
   fresh reviewer certify that exact source and its selected-source provenance.
4. After the concrete-std handoff gate passes, build or audit the oracle, validator, optional
   checker, reproducible generators, and replayable stress/regression plans.
   Run production judging evidence through the required sandbox backend. A
   data-only workspace is not a completion mode.
5. Exhaust tiny cases or pass 5,000--10,000 consecutive random differential
   seeds; retain minimized failures.
6. Generate a bounded wrong-route pool and retain, compile, and run the
   profile quota: 3--5 qualified routes for P1, 5--8 for P2, or 8--10 for P3.
   Strengthen each to its strongest natural still-wrong version, reject trivial
   dominators, machine-run its small/random/structured survivability bank, and
   design purposeful breaker families together with those routes.
7. Run one adversarial round for P1/P2 or 1--3 evidence-triggered rounds for
   P3. Generate every round through the append-only machine recorder and pass
   the hash-linked round-chain verifier; a Markdown table alone is incomplete.
   Then run the canonical machine regression executor. Keep the single targeted
   repair budget for concrete package defects separate from planned rounds.
8. Run the executable pre-readiness completion gate, perform an independent
   readiness review, and run the final readiness verifier before returning
   `go`. A `hold` or `escalate` report names its failed gate and owner.

During build/hardening, keep the private compact coverage matrix current. It
must bind the reviewed route-risk axes, proof/contract/resource obligations,
families, concrete tests, generation provenance, scale axes, and important
combinations. This audit metadata never enters or rearranges `package/`.

Read the stage reference before acting in that stage:

- [orchestration.md](references/orchestration.md): stage ownership, budgets,
  state transitions, and bounded reruns.
- [blind-solve.md](references/blind-solve.md): planner interface, public-surface
  isolation, neutral lanes, and deceptive protocol.
- [review.md](references/review.md): contract freeze and independent solution
  review.
- [build-and-harden.md](references/build-and-harden.md): executable package,
  stress testing, wrong solutions, data, and regression.
- [readiness.md](references/readiness.md): grading routes, escalation signals,
  stop conditions, and final verdicts.
- [artifact-contracts.md](references/artifact-contracts.md): required paths,
  schemas, evidence fields, and privacy boundary.
- [topic-routing.md](references/topic-routing.md): aliases and selective local
  vendored topic-reference reuse.

## Finish Required Work

Treat ordinary wall-clock ranges as planning estimates and monitoring points,
never as permission to skip a stage, lower a quota, accept missing artifacts,
or change the meaning of `go`. The sole hard time stop is the user-required
blind deadline: 7,200 seconds from the first production lane launch. Before
that deadline, solver crash, contamination, missing output, non-full status, or
a rejected claim requires a fresh isolated replacement or focused-neutral
wave. Preserve every attempt and its rejection evidence; never overwrite a
prior lane.

The blind stage has no normal partial terminal state. Continue until the
initial clean 2+2 batch exists and at least one blind `main.cpp` is independently
verified as a complete correct route. If that gate has not passed when the
7,200-second deadline is reached, terminate remaining blind children, preserve
their evidence, set the blind stage to failed, write an escalation handoff, and
report failure. A missing official solution is not an earlier stop, and a blind
failure never authorizes grading, data construction, or readiness.

For every non-blind stage, launch the agent through
`scripts/run_stage_agent.py`; its production receipt is mandatory and fixes
the exact model/effort, prerequisite hashes, output hashes, logs, and stage
order. Then validate the stage's semantic handoff gate before dispatching a
downstream stage. If a delegated agent returns prose,
future-work suggestions, provisional placeholders, empty files, a failed
command, or only part of its contract, reject that handoff and rerun the same
stage in a fresh `gpt-5.6-sol`/`ultra` context. Continue until its gate passes or
an explicit Light round/repair limit, genuine external blocker, or stop rule
requires a recorded failure/escalation. Never hide unfinished work behind a
later stage.

Never return `go` for “focused data”, “data-only”, “tests plus answers”, or any
other scope invented during execution. Full mode requires the canonical blind
artifacts and a compilable `package/std.cpp`, oracle, validator, generators,
tests, regression evidence, and qualified wrong-solution sources.

Do not accept `passed` strings in Markdown as executable evidence. The
completion gate requires current production receipts for preclassification,
fresh selected-route materialization, solution draft, std materialization,
concrete-source validation, and build/hardening; it also launches the canonical
regression executor itself. The draft must retain the active verified blind
source as its safety root and substantively justify Standard Route Adoption;
the final std provenance must hash-bind the fixed selected source.
The final readiness verifier separately requires a current production receipt
for the readiness agent.

## Preserve the Light Boundary

Do not add subtasks, score bands, partial-credit semantics, adapted statements,
UOJ configuration, route-by-subtask matrices, four-layer test ledgers, or an
implementation of every imagined route. Do not extend a run merely to exhaust
the solution space. P3 adversarial hardening is a bounded enhanced profile,
not permission for unlimited attack rounds; escalate work that remains open
after its recorded round limit instead of silently turning the light workflow
into a heavy workflow. This adversarial-round bound does not cap the mandatory
blind-solve retry loop.
