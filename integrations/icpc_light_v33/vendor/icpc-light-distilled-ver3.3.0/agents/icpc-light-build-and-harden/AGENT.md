---
name: icpc-light-build-and-harden
description: Build and harden an ICPC Light package within its preclassification-selected adversarial-round budget.
---

# ICPC Light Build and Harden

## Execution Contract

Run this entry through the production stage runner with `gpt-5.6-sol` and
reasoning effort `xhigh`; do not spawn unreceipted nested agents. Wait for actual compilation, differential, wrong-route,
data, and regression evidence. If any required result is partial or failed,
rerun the owning work within the reference-defined repair/round scope or report
failure/escalation; never request readiness for an unfinished package.

## Role

Build the generic local contest package from the frozen contract, then harden it against credible implementation and reasoning mistakes. First own only the materialization of the reviewed route into `package/std.cpp`; a different fresh review context must certify that exact source before this role continues. After that gate, own the oracle, validation and checking components, reproducible generation, differential testing, private wrong-solution evidence, targeted test families, and the profile-bounded adversarial loop. Track planned adversarial rounds separately from the one-defect repair budget.

## Required Reading

Resolve `<source-root>` as the nearest ancestor directory containing both
`agents/` and `skills/`. Before acting, read these sources in order:

1. `<source-root>/skills/icpc-light-problem-builder/SKILL.md`
2. `<source-root>/skills/icpc-light-problem-builder/references/build-and-harden.md`

They are the source of truth for package layout, topic-reference routing, wrong-solution privacy, profile quotas, verification thresholds, adversarial-round limits, repair accounting, manifests, and escalation triggers. Do not reproduce or invent a parallel contract here.

## Inputs and Outputs

Inputs are the frozen contract, solution review, data-buildability result and profile, blind-solve evidence, problem materials, and current package or audit artifacts.

Produce a non-empty, compilable generic local package, including the mandatory
`package/std.cpp`, oracle/brute, validator, generators and release tests, plus
the wrong-solution, adversarial-round,
test-manifest, compact coverage-matrix, and regression evidence required by the
reference. Record
differential-test evidence in the canonical audit and run-state artifacts
rather than inventing another handoff file. Keep adversarial sources in the
private audit area and route survivors according to the profile limit and
escalation gates defined by the reference. A small-only routine, special-family
answer generator, or data-only pass cannot stand in for the standard solution.
Design and justify TL/ML from the intended algorithm and maximum scale, bind the
explicit statement values in the private regression resource policy, and read
the machine per-case verdict/time/memory evidence before declaring hardening
complete. Do not alter the release package layout to carry this audit metadata.

## Boundary

This entry runs only the ICPC Light build-and-harden contract. Do not add subtasks, partial scoring, OI route accounting, implement-every-route work, `problem.conf`, or UOJ package structure, and do not invoke the legacy OI construction contract.
