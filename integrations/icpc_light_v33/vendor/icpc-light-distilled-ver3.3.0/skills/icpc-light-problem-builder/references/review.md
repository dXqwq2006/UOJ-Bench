# Review

Freeze the problem contract and establish correctness independently after the
blind lanes finish. Keep the reviewer independent from package implementation.

Load [artifact-contracts.md](artifact-contracts.md) before writing evidence and
[readiness.md](readiness.md) before routing a buildability grade.

Run review and every independent reviewer with `gpt-5.6-sol` and reasoning
effort `xhigh`. A review handoff is complete only when its actual artifacts,
compilation/tests, and downstream schema are present; prose promising that a
proof, contract, or std will be finished later is a failed review and must be
rerun or explicitly escalated.

Run preclassification, solution draft, std materialization, and concrete-source
validation as separate production stage executions through
`scripts/run_stage_agent.py`. Run `scripts/verify_preclassification.py` before
solution drafting and `scripts/verify_solution_handoff.py` before unlocking
hardening. Test-command receipts never satisfy either gate.

## Verify Blind Full-Solution Claims

Close the current blind wave before reading private solution material. For each
clean neutral lane that claims a full route, independently check:

- `main.cpp` exists, is non-empty, compiles, and passes every public sample;
- the claimed algorithm covers the complete contract;
- its proof, worst-case complexity, boundaries, and integer widths are valid;
- code and proof implement the same route;
- exhaustive tiny cases or an independent oracle/differential check finds no
  discrepancy, unless a genuine judge AC or equivalent certificate is stronger.

Use `scripts/run_blind_review.py` from the current skill root to launch each
fresh reviewer. Write every accepted or rejected claim, artifact path, source
hash, reviewer, review-report hash, and production execution receipt to the
machine-readable claim-review manifest defined in `artifact-contracts.md`.
Do not expose rejection counterexamples, earlier attempts, or private material
to later blind lanes.

If no claim is verified, return control to the orchestrator for a fresh
focused-neutral wave. Do not grade, downgrade to data-only work, or treat lack
of an official solution as a blocker. If a previously verified claim is later
disproved, preserve its review with `active: false`, bind `invalidated_by` to
the counterexample/review evidence, delete the stale completion receipt, and
invalidate `std` provenance, regression, and readiness. Reopen the same loop
only while the original blind deadline remains. At or after that deadline set
blind failure to `time-limit-exceeded-after-disproof`, write escalation, and
stop; never reset the clock.

## Route Data Buildability

After the executable blind-stage gate passes, inventory the available trusted
solution material, including the verified blind route, and invoke
[grade-test-data-buildability](../../grade-test-data-buildability/SKILL.md).
Require it to write `audit/data-buildability.md` using the stable schema. Treat
the grader as replaceable: require schema version 2 and consume only its
declared fields and evidence, not implementation-specific behavior.

Require preclassification to freshly write
`audit/private/selected-standard-route.cpp`. With `scam_status: none` or
`suspected`, require it to be an exact copy of the active verified blind route;
the suspected case remains provisional P3 with `decision: escalate`. An
independently proved executable simpler route may replace that copy and set
`scam_status: confirmed`; grade its data-construction axes as continuing
P1/D0, P2/D1, or P3/D2 rather than trying to reject it with tests. Return
S-stop/D3 only for an unverifiable foundation, and return any other
`decision: escalate` to the orchestrator immediately. Do not override
provisional status, downgrade a risk tag, alter the fixed quota/round fields,
or invent a friendlier profile outside the precedence rules in `readiness.md`.

## Freeze the Contract

Read the statement, public attachments, samples, and any authoritative setter
clarifications. Compare them with existing validator/checker behavior when
those files exist, but do not silently redefine the statement around the code.

Write `audit/contract.md` with:

- exact input grammar, test-case aggregation, legal structures, and all bounds;
- exact output grammar and answer semantics;
- whether token, whitespace, ordering, multiplicity, tolerance, or witness
  choices matter;
- intended time and memory complexity targets;
- critical minimum, maximum, duplicate, tie, empty/zero, and multi-case edges;
- whether a standard token checker suffices or a custom checker is required;
- every resolved ambiguity and its authoritative source.

Stop and escalate if authoritative semantics cannot be frozen. Do not build
official data against an unresolved interpretation.

## Establish Independent Correctness Evidence

Use the clean blind summary and supplied solution material only after stage 0
is closed. Independently derive:

1. the core observation and full algorithm;
2. invariants or lemmas that justify every transition or construction;
3. a complete correctness argument;
4. worst-case time and memory complexity under aggregate limits;
5. boundary behavior and required integer widths;
6. the relationship between the selected standard route, the supplied route,
   the active verified blind safety root, and unbroken blind alternatives.

Write this algorithm/proof pass to `audit/solution-review-draft.md`. Bind the
active verified blind source path and hash as the safety root even when a
confirmed simpler route has been selected. The preclassification stage receipt
and draft input receipt must separately hash-bind the fixed selected source.
Add a substantive `## Standard Route Adoption` section that identifies the
selected route, explains whether it is the blind-route copy or a confirmed
replacement, and supplies the correctness, executability, and complete-contract
basis for adoption. Do not claim that a not-yet-created release source compiled
or passed samples. Distinguish proven statements, experimentally supported
claims, and unresolved claims. Do not treat sample agreement, accepted-looking
code, or agreement between dependent reviewers as a proof.

Use the exact substantive section names required by `artifact-contracts.md`.
Before dispatching materialization, run
`scripts/verify_solution_draft_handoff.py`; a missing proof, complexity audit,
boundary audit, route comparison, Standard Route Adoption, oracle domain,
current blind safety-root provenance, or fixed selected-source binding is a
failed review and must be redone.

Complete solution review as one stage-internal three-step gate:

1. A fresh review agent writes `solution-review-draft.md` with the contract,
   algorithm, proof, complexity, boundaries, active-blind safety-root
   provenance, and substantive Standard Route Adoption.
2. The build agent performs only materialization: create the actual
   `package/std.cpp` from the fixed selected source and record whether it is
   byte-for-byte identical to
   `audit/private/selected-standard-route.cpp`. It may make packaging-only
   changes, but must describe every semantic/code delta relative to that
   selected source.
   Run `scripts/verify_std_materialization_handoff.py` immediately afterward;
   it hash-binds the draft/source/std chain and compiles the concrete source
   through LightCPVerifier.
3. A second fresh review agent reviews the exact current `package/std.cpp`,
   compiles it, compares every materialization delta with the draft proof, and
   writes the canonical `audit/solution-review.md` with current hashes. Its
   `std_provenance_path` and `std_provenance_sha256` must point to and hash-bind
   the fixed selected source, not the original blind workspace. It must use
   `pending-machine-regression` for public samples and tiny differential; local
   exploratory runs are not canonical receipts. Build hardening then creates
   the independent oracle/generator/sample manifest and discharges both fields
   through the machine regression gate.

Both concrete-source handoff checks require the CPIdeas Plus adapter and a
healthy LightCPVerifier service in ver3. They accept the same
`--lightcpverifier-url` / `ICPC_LIGHT_LIGHTCPVERIFIER_URL` selection as the
regression gate and never fall back to a host compiler. A successful local
exploratory compile may help diagnose source errors, but it is not handoff
evidence.

The build-and-harden agent remains locked to this materialization substep until
step 3 passes. Only then may it create official data, wrong solutions, and the
machine regression that completes the pending execution obligations. A draft that
names a future std path, a materializer that promises code later, or a final
review that merely repeats the draft is incomplete and must be rerun.

If no credible full solution remains, do not continue this review: reject the
claims and request another focused blind wave. If the supplied solution may be
wrong, neutral routes disagree on the core mechanism, or a plausible
alternative survives without proof or counterexample, classify the alternative
as `scam_status: suspected` and escalate it through correctness review rather
than declaring `std` authoritative or counting it as a wrong solution. Once an
executable simpler route is independently proved, rerun preclassification to
adopt it at the fixed selected-source path and continue under its P1/P2/P3 data
grade.

## Prepare the Build Handoff

Record 5--10 problem-specific wrong-assumption seeds for P1/P2 and 10--15 for
P3 by reversing proof obligations: remove a condition, drop state, localize a
global choice, confuse strict and non-strict relations, ignore ties/provenance,
narrow integer width, or assume a special input shape. These are review seeds,
not qualified wrong solutions. For each assumption, name the changed lemma or
transition and a concrete failure mechanism or counterexample-search target.

Also record:

- the smallest feasible oracle domain and exhaustive axes;
- random-generator dimensions needed for differential testing;
- proof boundaries that require structural data;
- checker obligations for constructive or non-unique output;
- maximum-scale axes needed for time, memory, stack, and overflow checks.

Do not build a full route landscape or subtask ledger. Do perform one compact
route-risk inventory across these six axes before handoff:

- alternative full routes;
- materially different implementations of the same idea;
- logically correct but resource-fragile exact routes;
- exact islands strengthened by fallback, repair, or portfolio logic;
- combined heuristics with a shared blind spot;
- plausible proof gaps and implementation gaps.

For each axis, name concrete seeds/known routes or record a problem-specific
`not-applicable` basis. An unresolved correct-looking route is an escalation,
not an omission note. Build-and-harden must preserve these six decisions in
the private compact coverage matrix and expand only the bounded high-value
candidates into code and breakers. This is an omission audit, not an exhaustive
route ledger and not a requirement to implement every idea.
