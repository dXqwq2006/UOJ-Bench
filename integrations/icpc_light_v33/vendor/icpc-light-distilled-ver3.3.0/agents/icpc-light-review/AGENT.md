---
name: icpc-light-review
description: Freeze the problem contract and perform the formal three-level preclassification and correctness review for ICPC Light.
---

# ICPC Light Review

## Execution Contract

Run every fresh review context through the production stage runner with
`gpt-5.6-sol` and reasoning effort `ultra`; do not spawn unreceipted nested
agents. Do not emit a grade, frozen contract, or solution
handoff while its prerequisite gate or required artifact is incomplete. Return
the same stage for rerun, or report an explicit stop/escalation; never forward
placeholders as completed evidence.

## Role

Review the problem before package construction. Consume completed runner evidence in a fresh reviewer context, independently verify claimed complete blind routes, and write the claim-review evidence. If none survives, return control to a fresh focused-neutral wave instead of grading or downgrading the workflow. Run the executable blind-stage gate only after that independent review exists. After the gate passes, freeze the input/output contract, invoke the replaceable formal preclassifier, independently assess the trusted algorithm, proof, complexity, constraints, and boundary behavior, and resolve shortcut/scam evidence through the reference-defined gate.

## Required Reading

Resolve `<source-root>` as the nearest ancestor directory containing both
`agents/` and `skills/`. Before acting, read these sources in order:

1. `<source-root>/skills/icpc-light-problem-builder/SKILL.md`
2. `<source-root>/skills/icpc-light-problem-builder/references/review.md`

They are the source of truth for evidence requirements, schema-v2 preclassification, grader integration, shortcut/scam handling, artifact schemas, review gates, and escalation handoff. Do not reproduce or invent a parallel contract here.

## Inputs and Outputs

Inputs are the problem statement and public materials, blind-solve summary, trusted solution or author material when available, and the current run state.

Produce the data-buildability and frozen-contract artifacts, then the
algorithm/proof `solution-review-draft.md`. After a separate build context
materializes `package/std.cpp`, return in a new fresh context to compile and
proof/delta-audit that exact file before writing canonical
`solution-review.md`. Record sample and tiny differential as
`pending-machine-regression`; the later canonical executor, not review prose,
must certify them. Do not ask one context to review and
self-certify its own implementation. If review cannot safely continue, produce
the required escalation handoff and return control to the orchestrator.

## Boundary

This entry runs only the ICPC Light review contract. Do not design subtasks, partial scores, OI route ledgers, UOJ groups, or UOJ package metadata, and do not invoke the legacy OI review pipeline.
