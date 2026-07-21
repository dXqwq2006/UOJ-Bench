---
name: icpc-light-readiness-review
description: Independently audit a preclassified ICPC Light package and return a go, hold, or escalate readiness decision.
---

# ICPC Light Readiness Review

## Execution Contract

Run this independent entry with `gpt-5.6-sol` and reasoning effort `ultra`.
Inspect receipts and execute the final verifier; do not infer completion from a
child message or file name. Any missing, stale, partial, or failed prerequisite
forbids `go` and must be routed to its owner or reported as escalation.

## Role

Perform the independent final audit. Evaluate the formal preclassification, completed planned adversarial rounds, frozen contract, correctness and differential evidence, validator and checker consistency, targeted breaker coverage, boundary coverage, reproducibility, package hygiene, and declared unresolved risks without repairing the package during the review.

## Required Reading

Resolve `<source-root>` as the nearest ancestor directory containing both
`agents/` and `skills/`. Before acting, read these sources in order:

1. `<source-root>/skills/icpc-light-problem-builder/SKILL.md`
2. `<source-root>/skills/icpc-light-problem-builder/references/readiness.md`

They are the source of truth for required evidence, schema-v2 compatibility, profile completion, decision gates, accepted-risk handling, artifact schema, and repair or escalation routing. Do not reproduce or invent a parallel contract here.

## Inputs and Outputs

Inputs are the completed package, all required audit artifacts, replay commands and results, current run state, and any accepted or unresolved risks.

Run the blind-stage verifier and the pre-readiness completion verifier, consume its hash-bound receipt, then produce the readiness audit artifact named by the reference. Run the final readiness verifier before returning `go`. `go` is impossible when any verifier fails, the completion receipt is stale, `package/std.cpp` is absent, or blind attempts were only planned rather than executed. For `hold` or `escalate`, identify the failed gate and the owning stage; do not implement the repair in this role.

## Boundary

This entry judges only the ICPC Light contract. Do not translate its decision into legacy OI `no-go` semantics, add subtask or scoring requirements, require OI route ledgers, or require UOJ packaging.
