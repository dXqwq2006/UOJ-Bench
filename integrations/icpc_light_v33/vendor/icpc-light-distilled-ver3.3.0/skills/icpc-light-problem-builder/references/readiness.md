# Readiness

Route buildability risk and make an independent final decision from saved
evidence. Do not repair the package during readiness review.

Load [artifact-contracts.md](artifact-contracts.md) before validating inputs or
writing the final decision.

## Consume the Buildability Grade

Run the executable blind-stage verifier first. Refuse to consume a grade when
the initial clean 2+2 batch, retained lane outputs, blind summary, claim-review
manifest, or at least one independently verified full blind solution is
missing. In full-build mode, provisional grading is not a substitute for this
gate.

Require `audit/data-buildability.md` to satisfy schema version 2 in the
artifact contract. A schema-v1 report is legacy evidence and must be regraded.
Require preclassification to have freshly written and verified
`audit/private/selected-standard-route.cpp`. For `scam_status: none` or
`suspected`, it must exactly copy the active verified blind route. For
`scam_status: confirmed`, it must instead be the independently proved,
executable simpler route selected for all downstream standard-solution work.
Apply these routes exactly:

| preclassification | grade | profile | route |
| --- | --- | --- | --- |
| `P1-random-strong` | `D0-direct` | `L0-simple-standard` | continue for one round |
| `P2-structured-bounded` | `D1-structured` | one L1 profile | continue for one round |
| `P3-adversarial-intensive` | `D2-specialist` | `L2-high-risk` | continue for 1--3 bounded rounds when `decision: continue`; otherwise escalate |
| `S-stop` | `D3-stop` | `outside-light` | stop immediately |

For D1, choose at most one profile modifier with this precedence:

```text
L1C-constructive-output > L1F-flow-model-like > L1G-greedy-deceptive > L1-ordinary
```

Require every quota and round field to match the table in the grader skill.
Treat a grade made without the passed blind gate as invalid in a full build.
The grader's provisional path exists only for an explicitly requested
audit-only opinion. Regrade when a listed trigger occurs; never erase a trigger
merely to preserve a lower profile.

## Route Explicit Stop and Escalation

For S-stop/D3, require `scam_status: none` and a concrete unverifiable
foundation, record the stop reason in run state and the grade report, write
`audit/escalation-handoff.md`, and stop immediately without running package
stages or final readiness review. Never use S-stop merely because a simpler
correct route was confirmed.

For any report with `decision: escalate`, including every suspected shortcut
or a P3 whose correctness/verification prerequisites are missing, write the
handoff and pause construction until regrading. A confirmed simpler route must
instead be scored on the data-construction axes as continuing P1/P2/P3; do not
try to kill it as a wrong route. Do not override an escalation merely because
the compatibility grade is D0--D2.

P3 may contain difficult greedy, structure, checker, resource, or reduction
signals when they have trustworthy verification and a bounded attack plan.
Escalate on any of these unresolved signals in a continuing P1/P2/P3 run:

- no trustworthy oracle or independent correctness argument;
- strong disagreement about the core route or several distinct unreviewed
  correct-looking routes;
- statement semantics that cannot be frozen;
- complex optimization judgment in a checker;
- constructive legality too subtle for bounded checker audit;
- interactive or communication protocol risk;
- floating-point geometry with delicate degeneracy or cancellation;
- randomized correctness or heuristic search central to the solution;
- a legal intended output that may approach or exceed the fixed 16 MiB
  per-stream Light profile cap;
- tight limits whose worst case cannot be reproduced deterministically within
  the recorded P3 plan;
- essential anti-hash, engineered worst-case, or other attack work beyond the
  bounded P3 capability;
- an adversarial round with no material progress;
- the same important survivor after two focused P3 rounds;
- an important survivor after the final permitted round;
- a second package defect after the one targeted repair.

Write `audit/escalation-handoff.md` with the trigger, completed evidence,
unresolved question, survivor artifacts, last reproducible commands, and the
recommended heavier capability. Do not silently invoke the old OI workflow.

## Decide Readiness

Write `audit/readiness.md` and return exactly one verdict:

- `go`: every gate below has direct evidence.
- `hold`: one concrete, bounded, Light-scope defect has a known fix and
  verification command, and `repair_used` is false.
- `escalate`: any high-risk signal remains, evidence cannot be established
  within the profile's adversarial-round or one-defect-repair scope, or a prior
  `hold` already consumed the targeted repair. Outside the explicit 120-minute
  blind-solve deadline, elapsed wall-clock time alone is not an escalation or
  scope-reduction condition.

Return `go` only when all of these are true:

1. The executable blind-stage gate passes, including at least two clean neutral
   and two clean deceptive lanes and one independently verified full blind
   solution. All attempts and rejection evidence remain indexed.
2. The schema-v2 grade has `decision: continue`, `provisional: false`, and
   `scam_status: none` or `confirmed` with a valid P1/P2/P3 profile. The
   executable selection gate proves the current fixed selected source was
   freshly produced; `confirmed` is accepted only when that gate passes and
   independent correctness/executability and Standard Route Adoption evidence
   for the selected simpler route also pass. A suspected shortcut and every
   S-stop report remain ineligible for `go`.
3. The solution draft, separate std materialization, and fresh concrete-source
   review production receipts pass in order; the exact current std has compiled
   and matches the reviewed provenance/delta relative to the fixed selected
   source. The draft retains the active verified blind route as its safety root
   and contains substantive Standard Route Adoption evidence; the canonical
   review's `std_provenance_path` and hash bind
   `audit/private/selected-standard-route.cpp`. Its review keeps sample/tiny
   execution explicitly pending until the later canonical machine regression,
   whose passed receipt is mandatory for `go`.
4. Non-empty `package/std.cpp`, oracle, validator, generators, and release tests
   exist; the model and required package programs have current, hash-bound
   sandbox compilation records and their provenance is recorded.
5. Tiny exhaustion or 5,000--10,000 consecutive differential seeds pass.
6. Every generated input, including stress, fixed regression, and official
   package data, passes the validator.
7. The final qualified wrong-solution count lies within the report's exact
   profile range: P1 3--5, P2 5--8, or P3 8--10. Every counted route satisfies
   the qualification contract, has its own retained private source, and has a
   reproducible observed rejection. Each also records its strengthening and
   passed trivial-dominator audit, ACs its ordinary legal witness, and ACs the
   machine-run small, random, and structured survivability inputs before its
   independent breaker.
8. The adversarial-round machine chain passes: P1/P2 exactly one production
   LightCPVerifier receipt; P3 one to three consecutive hash-linked production
   LightCPVerifier receipts within its recorded limit. Each receipt binds its
   routes/tests, machine-derived verdicts, backend provenance, and concrete
   delta; each extra round links the prior receipt and carries its survivors.
   No important survivor or open proof boundary remains.
9. The compact coverage matrix passes: it maps the six route-risk axes and all
   named obligations through purposeful families to every release input, every
   qualified route, and every required limit tag. Tests actually reach all
   important size, value, aggregate, and resource limits, including relevant
   semantic-structure × scale interactions rather than isolated maxima only.
10. Statement, validator, model, and checker share one frozen contract. The
   custom-checker review records malformed-output rejection probes; these
   negative probes remain reviewed audit evidence rather than a structured
   machine field in schema v3. Known
   accepted alternative implementations have machine-run every release input;
   when they are listed for a custom-checker task, at least one accepted output
   is token-distinct from the jury answer. Otherwise the task records the
   explicit no-known-alternative waiver required by the regression contract.
11. The canonical machine regression has actually run every declared program,
    differential case, release test, sample/ordinary route case, and breaker
    through the production LightCPVerifier backend; its backend evidence says
    `sandboxed: true` and `testing_only: false`, binds the expected client,
    adapter, service/image and execution-policy identities, and contains
    complete hash-valid chunk/result evidence for every invocation; its current
    receipt and human summary agree.
12. The readiness handoff has itself executed the canonical pre-readiness
    completion verifier from the problem root, and the readiness-stage receipt
    proves that its exact refreshed completion receipt remained unchanged.
13. Current exact-model production receipts exist for every non-blind stage,
    including this independent readiness review, and their input/output hashes
    and ordering remain valid.
14. The release package contains no private wrong solutions, audit notes, hidden
   prompts, or other private material.
15. No unexplained high-risk alternative or wrong route remains.

`go` certifies this package/data/execution contract only. It does not certify
problem novelty, interest, contestant thinking difficulty, contest slot, or
set-level suitability; those remain an upstream problem-quality review.

An unavailable CPIdeas import, unhealthy LightCPVerifier service, transport or
batch-order failure, or infrastructure checker result prevents `go`; it cannot
be waived by rerunning on the host. A receipt created with
`--test-mode --execution-backend local` is diagnostic evidence only and never
satisfies items 4, 8, or 11.

An `accepted_risks` entry cannot waive a missing blind solve, verified full
route, `std`, oracle, validator, required differential evidence, or qualified
wrong-source file. Never issue `go for focused data`, `data-only go`, or another
qualified variant of `go`; those are scope changes, not readiness verdicts.

Produce readiness through the `readiness` stage of `run_stage_agent.py`. For a
proposed `go`, run `scripts/verify_readiness.py` from the current skill root
after the production readiness receipt exists. Return `go` only if that final
verifier exits zero. `hold` and `escalate` remain reviewer decisions and must identify the
failed gate and owning stage; they cannot masquerade as a verified release.

Do not block `go` merely because the run did not enumerate every conceivable
wrong program, implement a low-plausibility trivial error, create noise for
every family, or prove rejection of the entire incorrect-solution space.

After a `hold`, set `repair_used: true` before repair and require a fresh full
regression and readiness review. The next verdict must be `go` or `escalate`;
do not issue a second `hold`.
