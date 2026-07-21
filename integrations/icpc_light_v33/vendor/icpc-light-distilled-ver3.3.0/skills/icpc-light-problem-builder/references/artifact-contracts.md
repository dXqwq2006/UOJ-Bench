# Artifact Contracts

Use these paths and fields as the stable handoff between thin agents. Preserve
existing evidence when valid; update canonical files rather than creating
competing versions.

## Contents

- Directory boundary
- Run state
- Blind evidence
- Buildability grade
- Contract and solution evidence
- Wrong routes and tests
- Regression and final decision
- Non-blind stage execution receipts

## Directory Boundary

```text
problem/
  statement.md
  solution.md                       # supplied material when available
  package/
    std.cpp
    brute.cpp                       # or documented oracle equivalent
    validator.cpp
    checker.cpp                     # only when required
    generators/
    samples/
      manifest.json                 # canonical statement-sample inventory
      sample-01.in
      sample-01.ans
    tests/
      ordinary/
      breakers/
  blind-solves/
    icpc-light/
      public-manifest.json
      sweep-plan.json
      sweep-plan-public-manifest.json
      sweep-plan-results.json
      sweep-plan-wave-02.json          # only when another wave is required
      sweep-plan-wave-02-public-manifest.json
      sweep-plan-wave-02-results.json
      neutral-01/workspace/           # raw isolated lane workspace
      neutral-02/workspace/
      deceptive-01/workspace/
      deceptive-02/workspace/
  audit/
    run-state.md
    blind-summary.md
    blind-claim-reviews.json
    blind-reviews/
    data-buildability.md
    contract.md
    solution-review-draft.md
    std-materialization.md
    solution-review.md
    wrong-solutions.md
    test-manifest.md
    coverage-matrix.json
    adversarial-rounds.md
    adversarial-round-plans/
      round-01.json
      round-02.json                  # only when a prior receipt justifies it
    adversarial-round-receipts/
      round-01.json
      round-02.json
    regression-plan.json
    regression.md
    regression-machine.json
    completion-gate.json
    readiness.md
    escalation-handoff.md           # only on escalation/stop
    private/
      selected-standard-route.cpp     # fixed downstream std source selected by preclassification
      blind-reviews/
      stage-prompts/
      stage-executions/
      wrong-solutions/
      accepted-solutions/             # known correct alternatives, when any
```

Treat `package/` as the release candidate. Treat `blind-solves/` as private raw
work and all of `audit/` as private distilled evidence. Never copy wrong-source
code, blind prompts/workspaces, audits, or escalation notes into `package/`.

## Run State

Maintain `audit/run-state.md` as the canonical current state. Include:

- problem identifier and active stage/status;
- `agent_model: gpt-5.6-sol`, `agent_reasoning_effort: ultra`, and
  `model_policy_status: enforced`;
- formal preclassification, compatibility grade/profile, shortcut state, and
  whether the grade is provisional;
- `completed_neutral_lanes` and `completed_deceptive_lanes`;
- `blind_status: staging|launching|running|collecting|candidate-review|retrying|complete|failed|blocked`;
- `blind_wave_current`, `blind_attempts_total`, `verified_full_solutions`, and
  `last_blind_retry_reason`;
- `blind_started_at_utc`, `blind_deadline_utc`, `blind_elapsed_seconds`,
  `blind_time_limit_seconds: 7200`, and `blind_failure_reason`;
- `wrong_solution_min`, `wrong_solution_max`, and the current qualified count;
- `adversarial_round_current`, `adversarial_round_limit`, and
  `adversarial_round_status: pending|active|complete|escalated|stopped`;
- evidence paths for completed stages;
- current blocker and next action;
- `repair_used: false|true`;
- last reproducible command needed to resume.

Do not use it as a verbose journal. Replace stale current-state values while
preserving concise transition evidence.

For a continuing or complete blind stage, use `blind_failure_reason: none`. On
ordinary deadline expiry use `blind_status: failed` and
`blind_failure_reason: time-limit-exceeded`. If later evidence revokes the only
verified route after that same deadline, use
`time-limit-exceeded-after-disproof`. In either case write the escalation
handoff, invalidate downstream receipts, and stop all downstream dispatch.

## Blind Evidence

Write `blind-solves/icpc-light/public-manifest.json` as an immutable inventory
of contestant-visible source files. Each entry contains a problem-relative
`path` and its lowercase SHA-256 digest. It must include `statement.md`. Every
lane receives an identical staged copy whose digest matches this inventory.

Let `blind-solves/icpc-light/sweep-plan.json` and later
`sweep-plan-wave-NN.json` files be planner-owned deterministic JSON. Do not
hand-edit them. The runner writes the matching `<plan-stem>-results.json` with
production/test execution mode, process exit status, output status and hashes,
prompt/workspace/log paths and hashes, and canonical public-manifest
provenance. The production gate rejects test overrides. Plans, results,
workspaces, and failed attempts are immutable; a retry always uses the next
wave and fresh run IDs. Record trust-based read isolation honestly and retain
it as a grader risk tag.

Write `audit/blind-summary.md` as one row per clean or contaminated lane:

```text
| attempt_id | wave | lane | kind | execution status | isolation mode | contamination | claimed route | candidate classification | code | proof status | counterexample status | claim-review status | shortcut/scam evidence | useful wrong assumptions |
```

Link lane artifacts by problem-relative path. Summarize outcomes; do not copy
full reasoning trajectories.

Write `audit/blind-claim-reviews.json` with this stable shape:

```json
{
  "schema_version": 1,
  "reviews": [
    {
      "review_id": "review-neutral-01",
      "attempt_id": "blind-solves/icpc-light/neutral-01/workspace",
      "lane_id": "neutral-01",
      "claim_type": "full-solution",
      "source_path": "blind-solves/icpc-light/neutral-01/workspace/main.cpp",
      "source_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
      "reviewer_id": "independent-review-01",
      "independent": true,
      "status": "verified",
      "active": true,
      "invalidated_by": null,
      "review_report": "audit/blind-reviews/review-neutral-01.md",
      "review_report_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
      "execution_receipt": "audit/private/blind-reviews/review-neutral-01.json"
    }
  ]
}
```

Allow `verified`, `rejected`, or `inconclusive`. Require JSON boolean `active`
and `invalidated_by: null` for a current claim. Preserve a later-disproved
claim with `active: false` and bind `invalidated_by` to a non-empty
problem-relative counterexample/review artifact; gates count only active
verified claims. The attempt ID is the exact
plan `workspace_rel`; it must identify a clean neutral attempt. Source and
report hashes must bind the current files. The execution receipt must be a
schema-v1 `production-codex` receipt under `audit/private/blind-reviews/`, with
matching review/attempt/reviewer/source/report fields, exit code 0, and a
recorded `codex exec` command. At least one independent review must be
`verified` before blind completion. Record rejected attempts and counterexamples
as evidence, but never expose them to retry lanes.

## Buildability Grade

Require `audit/data-buildability.md` to start with schema version 2. For
example, a P2 report begins:

```yaml
---
schema_version: 2
agent_model: gpt-5.6-sol
agent_reasoning_effort: ultra
preclassification: P2-structured-bounded
scam_status: none
data_buildability: D1-structured
workflow_profile: L1-ordinary
decision: continue
confidence: medium
provisional: false
wrong_solution_min: 5
wrong_solution_max: 8
adversarial_round_mode: single
adversarial_round_min: 1
adversarial_round_max: 1
stop_reason: none
risk_tags:
  - example-risk
required_checks:
  - example-check
regrade_triggers:
  - example-trigger
---
```

Enforce these fixed combinations:

| preclassification | grade/profile | wrong solutions | round mode and range |
| --- | --- | ---: | --- |
| `P1-random-strong` | `D0-direct` / `L0-simple-standard` | 3--5 | `single`, 1--1 |
| `P2-structured-bounded` | `D1-structured` / one L1 profile | 5--8 | `single`, 1--1 |
| `P3-adversarial-intensive` | `D2-specialist` / `L2-high-risk` | 8--10 | `bounded-multi`, 1--3 |
| `S-stop` | `D3-stop` / `outside-light` | 0 | `none`, 0--0 |

The production preclassification stage must freshly create the non-empty,
regular C++ source `audit/private/selected-standard-route.cpp`; a prior copy is
not a valid output for a new attempt. For `scam_status: none` or `suspected`, it
must be byte-identical to the current active verified blind source. The
suspected case uses provisional P3 compatibility fields with
`decision: escalate` and cannot proceed downstream. Only an independently
proved executable simpler full route may replace the blind copy and set
`scam_status: confirmed`; classify that selected route by the five data axes as
P1, P2, or P3 with `decision: continue`, `provisional: false`, and
`stop_reason: none`. Never classify a confirmed simpler route as S-stop.

The selected source has no new schema-v2 frontmatter fields. Instead, the
preclassification production receipt binds it as a required output and every
solution-stage receipt binds the same fixed path as an input. The deterministic
handoff verifiers rehash the current file, so a stale, missing, or changed
selection invalidates downstream provenance.

Allow `none`, `suspected`, or `confirmed` for `scam_status`; `continue`,
`escalate`, or `stop` for the decision; and `high`, `medium`, or `low` for
confidence. Allow only the `stop_reason` enum declared by the grader skill. Use
`stop_reason: none` exactly for `decision: continue` and require a specific
non-`none` reason for `escalate` or `stop`. Use integers for quotas and rounds,
a YAML boolean for `provisional`, and YAML lists for the final three fields. Add
concise evidence and rationale below the header. Preserve these field names and
enum spellings so a later replacement grader can replace this implementation
without changing consumers. Reject a schema-v1 report for a new run and regrade
it because D2 changed meaning. Reserve S-stop for a concrete unverifiable
foundation and require `scam_status: none`; construction difficulty, a
suspected shortcut, and a confirmed simpler route are not S-stop conditions.

## Contract and Solution Evidence

Make `audit/contract.md` state input/output grammar, legal structures, all
bounds and aggregates, answer semantics, checker choice, complexity target,
critical boundaries, and ambiguity resolutions.

Use these exact, non-placeholder level-two sections so the draft handoff gate
can reject a prose stub before code is materialized:

```text
## Input Contract
## Output Contract
## Bounds and Aggregates
## Complexity Target
## Critical Boundaries
## Checker Choice
## Ambiguity Resolutions
```

Start `audit/solution-review-draft.md` with:

```yaml
---
schema_version: 1
agent_model: gpt-5.6-sol
agent_reasoning_effort: ultra
review_status: passed
blind_source_path: blind-solves/icpc-light/neutral-01/workspace/main.cpp
blind_source_sha256: 0000000000000000000000000000000000000000000000000000000000000000
---
```

After its front matter, the draft must contain substantive sections named
`## Algorithm`, `## Correctness Proof`, `## Complexity`,
`## Boundary and Integer-Width Audit`, `## Route Comparison`,
`## Standard Route Adoption`, `## Unresolved Claims`, and `## Oracle Domain`.
The `blind_source_path` and hash remain the current active verified blind
source as a safety root even when a confirmed simpler route is selected. The
Standard Route Adoption section must identify the fixed selected source,
distinguish blind-copy from confirmed replacement, and substantiate the
replacement's independent proof, executable status, complete-contract
coverage, and relationship to the safety root. Empty headings, `TBD`, or a
pointer back to the statement do not pass the handoff gate.

This first fresh reviewer owns the algorithm/proof only. Then start
`audit/std-materialization.md` with:

```yaml
---
schema_version: 1
agent_model: gpt-5.6-sol
agent_reasoning_effort: ultra
status: passed
materialization_mode: exact-copy
blind_source_path: blind-solves/icpc-light/neutral-01/workspace/main.cpp
blind_source_sha256: 0000000000000000000000000000000000000000000000000000000000000000
std_path: package/std.cpp
std_sha256: 0000000000000000000000000000000000000000000000000000000000000000
---
```

Use `exact-copy` only for byte-identical source. Use `adapted` otherwise and
add a non-empty `## Semantic Deltas` section. Evaluate this mode and every delta
against `audit/private/selected-standard-route.cpp`, not against the
`blind_source_path`; the latter two frontmatter fields deliberately retain the
active-blind safety root and must match the draft. The preclassification output
and stage input receipts supply the fixed selected-source hash binding. A
second fresh reviewer owns the canonical source review below.

Start `audit/solution-review.md` with:

```yaml
---
schema_version: 1
agent_model: gpt-5.6-sol
agent_reasoning_effort: ultra
review_status: passed
std_compilation: passed
public_samples: pending-machine-regression
tiny_differential: pending-machine-regression
materialization_mode: exact-copy
materialization_delta_review: passed
std_path: package/std.cpp
std_sha256: 0000000000000000000000000000000000000000000000000000000000000000
std_provenance_path: audit/private/selected-standard-route.cpp
std_provenance_sha256: 0000000000000000000000000000000000000000000000000000000000000000
---
```

The provenance path must be exactly the fixed selected-source path and its hash
must match the current file bound by preclassification and the downstream stage
receipts; the std path/hash must match the release candidate. The active blind
safety root remains separately bound by the draft and materialization. The mode
must match `std-materialization.md`, and delta review must pass even for an
exact copy. The two pending values are mandatory: prose may not certify sample
execution or differential testing. The build-hardening stage must create the
independent oracle/generator/sample manifest, and only the canonical
`regression-machine.json` can discharge those obligations before completion.
Then state the independent algorithm, lemmas,
correctness proof, worst-case complexity, integer-width/boundary audit, supplied
route comparison, Standard Route Adoption conclusion, unresolved claims,
oracle domain, and initial wrong assumptions.

## Wrong Routes and Tests

Write one row per implemented route in `audit/wrong-solutions.md`:

```text
| route_id | wrong assumption | why plausible | hardening applied | trivial dominator check | survivability evidence | private source | compile status | public samples | ordinary case | expected failure | breaker family/test | breaker status | observed verdict | priority | introduced_round | killed_round | qualified |
```

Use `qualified: yes` only for a materially distinct, strongest-natural version
of the route: it compiles, passes all public samples, passes machine-run small,
random, and structured legal survivability inputs, and has a legal,
deterministic breaker with an observed expected rejection. Record the concrete
repairs/fallbacks tried in `hardening applied`, and a passed/no-dominator or
strongest-natural review in `trivial dominator check`. Do not mark a route
killed without that verdict. Escalate a plausible unbroken route to correctness
review; do not count it toward the profile quota. Every qualified row must point
to its own non-empty source file below `audit/private/wrong-solutions/`; an
inline simulation function in a public evaluator does not qualify.
For every qualified row, record `passed` in compile/public-samples/ordinary-case
status, a concrete breaker status, and the actual WA/TLE/MLE/OLE/RE observed
rejection; placeholders do not pass the completion verifier.

Keep the statement's complete sample set under `package/samples/`. The fixed
`package/samples/manifest.json` uses schema version 1 and lists every sample in
statement order. At top level it binds `statement_path: statement.md` and the
current 64-hex `statement_sha256`; changing the statement invalidates all
sample evidence until the manifest and machine regression are regenerated.
Each sample records `sample_id`, contiguous `statement_ordinal`, same-stem
`input`/`answer` paths, and current `input_sha256`/`answer_sha256`. It must
enumerate every non-manifest file in that directory. Every qualified wrong
route must run and AC all entries in this one canonical manifest; a route-local
subset or substitute “sample” does not qualify. Put ordinary witnesses below
`package/tests/ordinary/` and breakers below `package/tests/breakers/`.

Write one row per purposeful family in `audit/test-manifest.md`:

```text
| family_id | purpose | command or fixed file | seed/params | size/limits reached | target routes | validator status | introduced_round |
```

Record fixed regression witnesses as families. Do not replace this compact
manifest with mechanism/composition/scale/noise or subtask ledgers. In
`seed/params`, use comma-separated canonical items such as
`mode=path, seed=1`; in `target routes`, use comma/space-separated qualified
route IDs. The command cell must name the generator source (filename stem is
enough) or every concrete fixed input. A multi-input fixed family must list
each bound problem-relative input path; a bare basename is accepted only for a
single-input family. These cells are cross-checked with
the JSON matrix rather than accepted as unrelated prose.

Write `audit/coverage-matrix.json` as the private machine-readable link between
that compact manifest and the actual release data. Schema version 1 has four
top-level arrays:

- `route_axes`: exactly `alternative-full-routes`,
  `alternative-implementations`, `resource-fragile-exact-routes`,
  `fallback-repair-portfolio-routes`, `combined-heuristics`, and
  `proof-and-implementation-gaps`. Each records `covered` or a concrete
  `not-applicable` basis and links route/obligation IDs; `escalate` blocks
  completion.
- `obligations`: stable IDs of kind `acceptance`, `boundary`, `contract`,
  `proof-boundary`, `resource`, `structure`, or `wrong-route`, with linked
  family and route IDs plus required variant modes and composed dimensions.
- `scale_axes`: each important statement limit, its exact regression
  `limit_tags`, concrete release inputs, and the semantic dimensions composed
  with that scale. Each named input must actually carry those tags in the
  regression plan, and linked families must carry the `composed_with`
  dimensions. Collectively these must equal the plan's required limit tags.
- `families`: exactly the IDs in `test-manifest.md`; each binds concrete release
  inputs by current SHA-256, a fixed/generator recipe with seed/case parameters,
  target obligations/routes, scale axes, variant modes, and composed
  dimensions.

Allowed variant modes are exactly `aggregate`, `exact`, `high-multiplicity`,
`just-inside`, `just-outside`, `ordinary`, `scaled`, and `structural-noise`.
For a fixed family, replace the generator object above with:

```json
{
  "kind": "fixed",
  "recipe": "hand-construct the minimum legal overflow witness",
  "seed_params": ["case=min-overflow"]
}
```

Omit `source` and `args` for fixed data. Even a fixed witness keeps a stable
case parameter so it is distinguishable and reviewable.

A representative family and obligation look like this:

```json
{
  "schema_version": 1,
  "route_axes": [{
    "axis": "proof-and-implementation-gaps",
    "status": "covered",
    "basis": "integer-width and boundary implementation routes reviewed",
    "route_ids": ["W01"],
    "obligation_ids": ["O01"]
  }],
  "obligations": [{
    "obligation_id": "O01",
    "kind": "wrong-route",
    "description": "break the strongest natural 32-bit implementation",
    "family_ids": ["F01"],
    "target_route_ids": ["W01"],
    "required_variant_modes": ["exact", "scaled"],
    "required_composed_dimensions": ["n=max", "value=max"]
  }],
  "scale_axes": [{
    "axis_id": "n",
    "description": "maximum item count",
    "limit_tags": ["n=max"],
    "input_paths": ["package/tests/max-values.in"],
    "composed_with": ["value=max"]
  }],
  "families": [{
    "family_id": "F01",
    "purpose": "maximum count and value overflow boundary",
    "inputs": [{
      "path": "package/tests/max-values.in",
      "sha256": "0000000000000000000000000000000000000000000000000000000000000000"
    }],
    "generation": {
      "kind": "generator",
      "source": "package/generators/gen.cpp",
      "args": ["--mode", "max", "--seed", "1"],
      "seed_params": ["mode=max", "seed=1"]
    },
    "target_obligation_ids": ["O01"],
    "target_route_ids": ["W01"],
    "scale_axis_ids": ["n"],
    "variant_modes": ["exact", "scaled"],
    "composed_dimensions": ["n=max", "value=max"]
  }]
}
```

The example abbreviates `route_axes`; a real file contains all six exact axis
objects. Every release input must occur in at least one family, every qualified
wrong route and obligation must be targeted, links must be bidirectional, and
all hashes must match current files. This is one compact private matrix, not the
source workflow's multi-layer OI ledger.

Write `audit/adversarial-rounds.md` without overwriting prior-round evidence:

```text
| round | trigger | active routes | new attack hypothesis | new/changed breakers | killed | survivors | commands | material result |
```

Round 1 uses `trigger: initial-matrix`. An additional P3 round must name the
survivor, proof boundary, or newly independent failure mechanism that triggered
it and record a concrete delta. Planned rounds never change `repair_used`.
Write route-list cells as receipt-order comma-separated IDs, or `none` for an
empty killed/survivor list. The completion gate compares active, killed, and
survivor cells exactly with the machine receipt for that numbered row.

The Markdown table is only an index. It cannot prove that a route was compiled
or a breaker was run. Before marking a round complete, write
`audit/adversarial-round-plans/round-NN.json` and execute:

```bash
python3 "$SKILL_ROOT/scripts/record_adversarial_round.py" \
  --problem-dir "$PROBLEM_DIR" \
  --plan "audit/adversarial-round-plans/round-NN.json"
```

Use this plan shape (repeat routes and tests as needed):

```json
{
  "schema_version": 1,
  "round": 1,
  "trigger": "initial-matrix",
  "delta": "Run the first concrete proof-boundary breaker family.",
  "previous_receipt": null,
  "routes": [{
    "route_id": "W01",
    "source_path": "audit/private/wrong-solutions/W01.cpp",
    "breaker_test_id": "B01"
  }],
  "tests": [{
    "test_id": "B01",
    "input_path": "package/tests/breakers/B01.in",
    "answer_path": "package/tests/breakers/B01.ans",
    "comparison": "tokens"
  }]
}
```

The recorder obtains TL/ML from the current statement. A plan may include
`timeout_seconds` and `memory_limit_mb` only as redundant human-readable fields;
if present, they must equal the normalized statement values exactly. Omitting
them is preferred because no default or override is needed.

For round `N > 1`, set `previous_receipt` to
`audit/adversarial-round-receipts/round-(N-1).json`, use a concrete noninitial
trigger/delta, and carry every prior survivor into the new route list. The
recorder compiles every named source and executes it on the named breaker
through the selected Program × Dataset backend, without a shell. Production
defaults to LightCPVerifier and writes `execution_mode: production`,
`production: true`, plus backend configuration. It derives
AC/WA/TLE/MLE/OLE/RE itself,
captures stdout/stderr hashes and previews, hash-binds the
source/input/answer/plan, and exclusively creates
`audit/adversarial-round-receipts/round-NN.json`. AC routes are survivors; all
other derived verdicts are killed routes. Never hand-write, overwrite, or copy
a receipt from another problem. The chain verifier requires production
LightCPVerifier evidence, so it rejects ver2, local, and test-mode receipts.

Production adversarial plans permit `tokens`, `exact`, or source-bound
`checker` comparison. For checker mode, set the plan's top-level
`checker_source` to exactly `package/checker.cpp`, omit `checker_command`, and
optionally set each test's distinct positive `checker_wa_exit_codes` (default
`[1, 2]`). The recorder compiles that source as an isolated `checker` role and
runs it through the same Program × Dataset backend with hash-bound
input/candidate/answer copy-ins. Exit 0 accepts; only assigned WA/PE exits reject;
timeout, launch failure, resource failure, or an unassigned exit is
infrastructure failure. The receipt binds the checker source, compile evidence,
execution, exit contract, and per-route checker-source hash. The legacy
`checker_command` host-command shape remains available only with
`--test-mode --execution-backend local`; its receipt cannot complete the
production chain.

Verify the continuous chain before canonical regression:

```bash
python3 "$SKILL_ROOT/scripts/verify_adversarial_round_chain.py" \
  --problem-dir "$PROBLEM_DIR" \
  --min-rounds "$ADVERSARIAL_ROUND_MIN" \
  --max-rounds "$ADVERSARIAL_ROUND_MAX"
```

The chain verifier rehashes every current plan, route source, breaker input,
and answer; validates every machine-derived verdict and aggregate killed or
survivor list; requires consecutive append-only receipts; validates each
previous-receipt link; and requires a production sandboxed LightCPVerifier
backend on every round. A table row without its passed receipt is an unfinished
round and cannot contribute to completion.

## Regression and Final Decision

`audit/regression-plan.json` is the canonical schema-v3 executable matrix
consumed by `scripts/run_regression_gate.py`. It explicitly binds the current
statement TL/ML plus the LLM's resource-design basis, the canonical sample
manifest, generator commands,
differential mode/count, release `.in`/answer paths, qualified route source,
sample/survivability/ordinary/breaker paths, expected rejection class, accepted
alternative policy, resource policy, checker mode, and limit cases. The executor writes `audit/regression-machine.json`
with source/plan/input hashes, actual commands/counts, per-release and
per-route observed results, aggregate ordered differential input hashes,
privacy result, and pass/fail status. Its `artifact_bindings.files` array binds
the path, size, and SHA-256 of every generator, sample/input answer, ordinary
case, breaker, release test/answer, program source, and private qualified wrong
source used by the matrix. The final completion gate re-hashes that array and
then includes those files in its own receipt. Test-mode receipts cannot certify
a run.

The ver3 receipt also binds execution-backend configuration. Its requested,
effective, verdict, and sandbox CPU limits must all equal the current statement
TL, and its requested/effective memory limits must both equal the current
statement ML. The service enforces these exact limits and supplies TLE/MLE;
there is no fixed 2-second/1024-MiB production fallback or elapsed-time
post-classifier. Production backend load, attestation, or limit-echo failures
are infrastructure failures and never authorize a silent local fallback. A
production receipt records a `lightcpverifier` backend with `sandboxed: true`,
`testing_only: false`, `dataset_batch_size: 128`, 16 MiB per output stream, and
the service-confirmed batch output budget. The requested
120-second compile timeout remains orchestration provenance; the actual C++
compile policy is service-controlled and must come from attested response/
health evidence rather than being invented in the receipt. The separately
versioned `execution_backend_evidence` binds the exact adapter/client hashes,
service/image identity, invocation count/hash, each chunk and the ordered case
result binding; incomplete or truncated evidence invalidates completion.

Use this compact schema shape (repeat release tests and wrong routes as needed):

```json
{
  "schema_version": 3,
  "resource_policy": {
    "schema_version": 1,
    "statement_resources": {
      "schema_version": 1,
      "statement_path": "statement.md",
      "statement_sha256": "9ba3d0963f20472c88a7a09b416597feb591cf8a73bf2ea1b70824d3bcfb633b",
      "time_limit_ms": 2000,
      "memory_limit_mib": 512,
      "time_evidence": [{
        "kind": "time", "line": 1, "text": "Time limit: 2 seconds",
        "raw_value": "2", "raw_unit": "seconds", "normalized_value": 2000
      }],
      "memory_evidence": [{
        "kind": "memory", "line": 2, "text": "Memory limit: 512 MiB",
        "raw_value": "512", "raw_unit": "MiB", "normalized_value": 512
      }],
      "canonical_sha256": "030e491c898e4e96950645be1f57d36969aa62dd255cee0f2aac9e5109a0c5ff"
    },
    "design_basis": {
      "intended_complexity": "O(n log n)",
      "maximum_scale": "n = 200000",
      "time_limit_rationale": "Measured full-scale std with safety margin.",
      "memory_limit_rationale": "Peak model storage remains below 512 MiB."
    },
    "policy_sha256": "ac0075b622a41f2d334f819f35c7434c4d8032367c7862e3c87ad1c91569f562"
  },
  "sample_manifest": "package/samples/manifest.json",
  "oracle": {
    "source": "package/brute.cpp",
    "independent_from_std": true,
    "independence_basis": "independent exhaustive enumeration",
    "applicability": "all instances emitted by the differential generator"
  },
  "checker_verdict_contract": {
    "name": "testlib",
    "accepted_exit_codes": [0],
    "wrong_answer_exit_codes": [1],
    "presentation_error_exit_codes": [2]
  },
  "required_limit_tags": ["n=max", "value=max", "aggregate=max"],
  "differential": {
    "mode": "random-seeds",
    "generator": {
      "source": "package/generators/stress.cpp",
      "args": ["--seed", "{seed}"]
    },
    "seed_start": 1,
    "count": 5000
  },
  "release_tests": [
    {
      "test_id": "max-values",
      "input": "package/tests/max-values.in",
      "answer": "package/tests/max-values.ans",
      "limit_tags": ["n=max", "value=max", "aggregate=max"]
    },
    {
      "test_id": "ordinary-1",
      "input": "package/tests/ordinary/ordinary-1.in",
      "answer": "package/tests/ordinary/ordinary-1.ans",
      "limit_tags": []
    },
    {
      "test_id": "structured-1",
      "input": "package/tests/structured-1.in",
      "answer": "package/tests/structured-1.ans",
      "limit_tags": []
    },
    {
      "test_id": "W01-breaker",
      "input": "package/tests/breakers/W01-breaker.in",
      "answer": "package/tests/breakers/W01-breaker.ans",
      "limit_tags": []
    }
  ],
  "accepted_alternatives": [{
    "alternative_id": "A01",
    "source": "audit/private/accepted-solutions/A01.cpp",
    "independence_basis": "independent construction and state representation"
  }],
  "wrong_routes": [
    {
      "route_id": "W01",
      "source": "audit/private/wrong-solutions/W01.cpp",
      "sample_inputs": ["package/samples/sample-01.in"],
      "ordinary_input": "package/tests/ordinary/ordinary-1.in",
      "survivability_inputs": [
        {"kind": "small", "input": "package/tests/ordinary/ordinary-1.in"},
        {"kind": "random", "input": "package/tests/max-values.in"},
        {"kind": "structured", "input": "package/tests/structured-1.in"}
      ],
      "breaker_input": "package/tests/breakers/W01-breaker.in",
      "expected_verdict": "WA"
    }
  ]
}
```

The example resource hashes match a statement containing exactly
`Time limit: 2 seconds` and `Memory limit: 512 MiB` on two newline-terminated
lines. For a real problem, never copy those values: run
`scripts/build_resource_policy.py` and insert its output unchanged. The
resource policy is private audit evidence and does not enter `package/`.

For `tiny-exhaustive`, replace the differential mode with that value, use
`{case_index}`, `case_index_start`, and a positive exact count. Every production
random run uses at least 5,000 consecutive seeds. Every release `.in` below
`package/tests/` and every same-stem `.ans` must appear exactly once; the union
of its `limit_tags` must equal `required_limit_tags`. Differential generator
sources must stay below `package/generators/`; sample files must stay below
`package/samples/`; and ordinary/breaker inputs must stay in their fixed
`package/tests/ordinary/` and `package/tests/breakers/` subtrees. Every qualified row in
`wrong-solutions.md` must have one exactly matching plan route/source/breaker
and observed verdict. Every qualified route must also name at least one distinct
legal `small`, `random`, and `structured` survivability input. The runner
validates each point, derives the reference output, executes the wrong route,
and requires AC before accepting the route as a realistic survivor.

`accepted_alternatives` is optional for exact-token tasks. Every listed private
C++ source must stay below `audit/private/accepted-solutions/`; the gate compiles
it once, executes it on every release test, and requires acceptance through the
same token comparison or custom checker. Its source hash must differ from the
standard solution and every other alternative; a second normalized
preprocessing-token hash also rejects comment-only copies and whitespace copies
that preserve token boundaries. It intentionally does not equate token-changing
rewrites such as nested-template `>>` versus `> >`, and it cannot detect renamed
or lightly refactored copies. This is a trivial-clone heuristic, not proof of
algorithmic independence, so the review must still explain why the
implementation is materially different. The
plan hash-binds that explanation in each `independence_basis`; placeholders
fail. For a custom-checker task, the listed alternatives collectively must produce at
least one accepted release-test output whose token sequence differs from the
jury answer. Merely recompiling a renamed canonical-output program does not
exercise alternative-answer acceptance. If `package/checker.cpp` exists and no
accepted alternative is known, the plan must instead include:

```json
"accepted_alternative_waiver": {
  "status": "no-known-alternative",
  "basis": "why no materially distinct accepted implementation is known",
  "search_scope": "routes and implementations actually reviewed"
}
```

The waiver is plan-hash-bound audit evidence; it cannot be combined with a
non-empty `accepted_alternatives` array and is not valid for token-only tasks.

The default checker verdict contract is the safe testlib mapping above. A plan
may state a custom checker's documented exit-code contract explicitly, but only
codes listed as wrong-answer or presentation-error count as rejection. A
timeout, launch failure, organizer/internal-failure code, signal, or any unknown
exit code is an infrastructure error and can never kill a wrong route. Require
`package/brute.cpp` to have a source hash different from `package/std.cpp`, and
record a non-empty independence basis and the exact applicability domain; a
renamed or byte-identical std is not an oracle.

The machine receipt exposes ordered per-case results and role-level peak
time/memory observations. The build agent must read those fields, compare the
full-scale std and wrong-route behavior with the declared TL/ML and design
basis, and rerun after any data or statement-limit change. A prose claim that
the limits "look sufficient" is not a substitute for this feedback.

Start `audit/regression.md` with:

```yaml
---
schema_version: 1
agent_model: gpt-5.6-sol
agent_reasoning_effort: ultra
status: passed
validator: passed
differential: passed
wrong_routes: passed
privacy_scan: passed
limit_coverage: passed
differential_mode: tiny-exhaustive
differential_cases: 1024
differential_consecutive_seeds: 0
generated_inputs_validated: 1024
wrong_routes_checked: 6
survivability_inputs_checked: 18
accepted_alternatives_checked: 1
accepted_non_jury_outputs_checked: 1
accepted_alternative_strategy: programs
release_tests_checked: 24
repro_command: python3 SKILL_ROOT/scripts/run_regression_gate.py --problem-dir .
---
```

Then include environment/toolchain, the exact clean command and observed
result, official-test reproduction result, validator result, model/oracle
result, accepted-alternative result, wrong-route matrix result, checker hostile
probes, sanitizer/resource evidence, limit coverage, and privacy scan. These
frontmatter fields summarize executed evidence; they do not replace it.
Use `tiny-exhaustive` with a positive exact case count and zero consecutive
random seeds, or `random-seeds` with at least 5,000 consecutive seeds. The
validated-input, checked-wrong-route, and checked-release-test counts must match
the completed package evidence. For a custom-checker plan with listed
alternatives, `accepted_non_jury_outputs_checked` must be positive; for a
waiver or exact-token comparison it is zero.

The readiness stage runner invokes the authoritative handoff automatically. It
does not trust a pre-existing completion JSON: the handoff runs the canonical
completion verifier, which replays the regression and atomically refreshes
`audit/completion-gate.json`, before the readiness agent may start. To exercise
that same handoff directly, run:

```bash
python3 "$SKILL_ROOT/scripts/verify_completion_handoff.py" \
  --problem-dir "$PROBLEM_DIR"
```

On success the underlying completion replay atomically writes
`audit/completion-gate.json`, whose schema-v1
receipt hash-binds the blind verifier, relevant audit and package inputs,
watched directory inventories, the fixed selected source, std provenance,
grade, quotas, rounds, and compilation checks. A failed replay invalidates any
old receipt. Do not run a separate completion command immediately before the
stage runner; that only duplicates the same expensive regression replay.
Its facts record `selected_standard_route_path`,
`selected_standard_route_sha256`, and
`selected_standard_route_kind: verified-blind|verified-simpler`, plus the
matching `std_provenance_path` and `std_provenance_sha256` used by readiness.

Start `audit/readiness.md` with:

```yaml
---
schema_version: 2
verdict: hold
agent_model: gpt-5.6-sol
agent_reasoning_effort: ultra
model_policy_status: enforced
preclassification: P2-structured-bounded
workflow_profile: L1-ordinary
scam_status: none
blind_gate: passed
verified_full_solutions: 1
std_path: package/std.cpp
std_sha256: 0000000000000000000000000000000000000000000000000000000000000000
wrong_solutions_qualified: 6
wrong_solutions_required_min: 5
adversarial_rounds_completed: 1
adversarial_round_limit: 1
completion_gate: passed
machine_regression: passed
adversarial_round_chain: passed
stage_execution_receipts: passed
std_materialization_mode: exact-copy
repair_used: false
blockers:
  - example-blocker
evidence:
  - audit/regression.md
  - audit/regression-machine.json
  - audit/completion-gate.json
---
```

Allow `go`, `hold`, or `escalate` for `verdict`; copy the exact
preclassification, profile, shortcut state, minimum quota, and round limit from
the buildability grade; record the passed executable gates, verified full count,
and actual `std` path/hash; cite every executed adversarial-round receipt in
`evidence`; and use a YAML boolean for `repair_used`. A `go`
report may not omit or waive these fields. `scam_status: suspected` and S-stop
cannot receive `go`. `scam_status: confirmed` may receive `go` only when the
existing completion facts prove fresh selected-route creation, substantive
Standard Route Adoption, exact-copy/adapted classification relative to that
source, and canonical `std_provenance_path`/hash binding to it; do not add a
parallel readiness field in place of those executable facts. Follow the header
with a concise gate-by-gate decision. On escalation or an outside-Light stop,
also create
`audit/escalation-handoff.md` with trigger, completed evidence, unresolved work,
survivors, reproducible commands, and recommended next workflow.

After the independent reviewer writes `audit/readiness.md`, certify a `go` with:

```bash
python3 "$SKILL_ROOT/scripts/verify_readiness.py" \
  --problem-dir "$PROBLEM_DIR"
```

This final verifier rejects a stale completion receipt, changed package/audit
inputs, schema/profile/quota/round mismatches, a changed std hash, missing gate
fields, or non-empty blockers.

## Non-Blind Stage Execution Receipts

Write each stage prompt below `audit/private/stage-prompts/` and launch it with
`scripts/run_stage_agent.py`, exact `--model gpt-5.6-sol`, and
`--reasoning-effort ultra`. The runner preserves immutable attempts below
`audit/private/stage-executions/<stage>/<run-id>/` and publishes
`<stage>/current.json` only for a successful production run. The fixed stages
are `preclassification`, `solution-draft`, `std-materialization`,
`solution-validation`, `build-hardening`, and `readiness`.

Each current receipt binds the exact Codex command, prompt, prerequisite
inputs, outputs, watched output trees, logs, exit code, and timestamps. Once a
production launch reaches output initialization, it invalidates the old current
receipt even if that rerun later fails; an earlier preflight failure leaves the
old receipt untouched. Test-command receipts never update it. The completion
gate requires the first five in order;
the final readiness gate requires the sixth and proves it started after the
completion receipt was created.

The preclassification stage owns
`audit/private/selected-standard-route.cpp` as a required freshly recreated
output. The solution-draft, std-materialization, and solution-validation stages
consume that exact path as a hash-bound input. This receipt chain is the
machine-readable selection contract; do not add selected-source keys to the
stable grade, draft, materialization, or readiness frontmatter schemas.

For a production launch, the runner first moves each existing required or
optional owned output and watched output tree atomically to
`<attempt>/preexisting/<problem-relative-path>`. It rejects any output that
overlaps the prompt, an input, another owned output/tree, or the receipt root.
The child therefore starts with clear canonical output paths and must recreate
every required output/tree; a byte-identical result is allowed only because it
was newly materialized in this attempt. An optional file/tree may remain safely
absent. If it exists, it must be fresh, non-empty, regular, and free of hidden
entries. The build stage applies this to `package/checker.cpp` and
`audit/private/accepted-solutions/`; it also owns fresh `package/samples/` and
the adversarial-round plan/receipt trees. If the child fails, old artifacts remain in
the private attempt archive, the canonical paths remain incomplete, and no
`current.json` is published. Test overrides never move canonical artifacts.

Production receipts contain `preexisting_outputs`,
`preexisting_output_trees`, a hash-bound `preexisting_archive`,
`codex_jsonl_required: true`, a passed `codex_jsonl_validation`,
`output_changes`, `output_tree_changes`, and the two aggregate
`*_materially_updated` booleans. A zero process exit without exactly one
`thread.started`, one `turn.started`, and terminal `turn.completed` JSONL event
is not a completed stage. Preserve intermediate Codex/API `error` events,
including 429 and 5xx retries, in the immutable JSONL log and count them in the
validation receipt, but do not fail a zero-exit invocation whose final event is
`turn.completed`. A final `turn.failed` or `thread.failed`, nonzero exit,
timeout, malformed JSONL, or missing final completion remains a failed stage.

During later receipt validation, a declared optional watched tree may equal the
exact collision-free union of its stage-owned post-run snapshot and its still
hash-bound preexisting archive. Only safe non-empty directory snapshots or an
exact safely-absent state are eligible: unknown, missing, changed, unsafe, or
conflicting entries fail closed. Required and extra watched trees remain exact
stage-owned snapshots, so this restoration rule never relaxes package trees.
