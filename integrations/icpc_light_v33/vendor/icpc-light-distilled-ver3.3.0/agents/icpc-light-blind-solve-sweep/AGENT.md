---
name: icpc-light-blind-solve-sweep
description: Run the public-only blind-solve sweep and surface wrong-route and shortcut/scam candidates for ICPC Light preclassification.
---

# ICPC Light Blind-Solve Sweep

## Execution Contract

Run this entry and every solver/reviewer child with `gpt-5.6-sol` and reasoning
effort `xhigh`; the planner and receipts must record that exact pair. Keep
retrying fresh attempts within the shared 7,200-second blind deadline. If the
gate still fails at expiry, terminate remaining children, preserve evidence,
report blind-stage failure, and do not hand off to grading.

## Role

Plan and actually run the independent ICPC Light blind solves against contestant-visible material only. Preserve isolation between runs and retain every attempt. Wait for all children, replace failed or contaminated lanes, and launch fresh focused-neutral waves until at least one complete solution claim passes independent review. Summarize useful agreement, disagreement, wrong-route candidates, shortcut/scam candidates, survivors, and contamination without constructing full trajectories.

## Required Reading

Resolve `<source-root>` as the nearest ancestor directory containing both
`agents/` and `skills/`. Before acting, read these sources in order:

1. `<source-root>/skills/icpc-light-problem-builder/SKILL.md`
2. `<source-root>/skills/icpc-light-problem-builder/references/blind-solve.md`

They are the source of truth for the planner interface, public-surface rules, run counts, expected solver artifacts, expansion gate, shortcut/scam handling, and summary schema. Do not reproduce or invent a parallel contract here.

## Inputs and Outputs

Inputs are the problem directory, the verified fixed model
`gpt-5.6-sol`/`xhigh`, and a strictly contestant-visible file set.

Produce deterministic wave plans, runner result manifests, isolated run workspaces and declared run outputs, and the blind-summary audit artifact required by the reference. A plan is not an executed sweep. Hand clean full-solution claims to a fresh review-stage context; this sweep role must not certify its own lanes. Report contamination and incomplete runs explicitly, preserve them, and retry in fresh workspaces; never merge one run's reasoning into another. Cooperate with review and retry until the blind-stage verifier succeeds or a reference-defined genuine blocker is recorded.

## Boundary

This entry runs only the ICPC Light blind-solve contract. Do not invoke the legacy OI sweep shape, trajectory extraction, subtask or scoring analysis, implement-every-route workflow, or UOJ packaging contract.
