---
name: using-testlib
description: Implement, review, or locally run testlib-based batch validators, deterministic generators, ordinary exact or semantic checkers, and replay helpers as a component of an ICPC Light package. Use for component mechanics after the main workflow freezes the contract. Return interactive, communication, scored-output, partial-score, grader, or stub protocols to the ICPC Light risk gates instead of handling them here.
---

# Using Testlib

## Overview

This skill is the ICPC Light bundle's component-level reference for package-side
C++ artifacts built on `testlib.h`. It does not own data-family quotas,
differential thresholds, retry policy, or release readiness.

Use it when the work is about one or more of:

- input validators;
- generators;
- ordinary exact or semantic custom checkers;
- local compile / run / replay commands and small runner helpers for those artifacts.

This skill is not the main guide for:

- choosing testcase families;
- designing anti-cheese data;
- broader contest-package release workflow;
- problem-idea evaluation.

For those, return to the calling
[ICPC Light workflow](../icpc-light-problem-builder/SKILL.md) and use this skill
only for the `testlib` implementation surface. The caller's contracts and
thresholds always take precedence over examples or defaults in this skill.

Interactive, communication, run-twice, scored-output, partial-score, grader,
stub, or public-header protocols are outside this component entry. Return them
to the main workflow for formal P1/P2/P3 or S-stop routing; do not continue into
an out-of-scope component reference merely because it is present locally.

## Core Stance

Work with these rules throughout:

1. Freeze the contract before writing the artifact.
2. Pick the component first; do not write a "generic utility file" and hope it becomes the right tool later.
3. Parse strictly and fail closed.
4. Keep contestant mistakes and organizer mistakes on different verdict paths.
5. Make randomness replayable from the full command line, plus any explicit seed parameter if the generator defines one.
6. Stop and return protocol-heavy work to the main workflow's risk gates.
7. Always have a local run command and a short hostile-probe bank before calling the artifact done.

If you cannot state exactly what the file reads, what it writes, and which side it is allowed to blame on failure, the contract is still too vague.

## Choose The Component First

If the surface is unfamiliar, cross-component, or easy to misremember, load [core-api.md](references/core-api.md) first. If the task is narrow and familiar, you can jump straight to the leaf guide and come back to `core-api.md` only when needed.

- Validator: load [validators.md](references/components/validators.md).
- Generator: load [generators.md](references/components/generators.md).
- Checker: load [checkers.md](references/components/checkers.md).
- Local commands, replay helpers, wrapper scripts, or debugging: load [local-run-and-debug.md](references/local-run-and-debug.md).
- Skeleton code: load [templates.md](references/templates.md).
- Final audit or review: load [review-checklists.md](references/review-checklists.md).

Artifact aliases:

- output-format checker -> usually `checker`
- semantic output checker -> `checker`
- expected-output generator -> usually `generator` if it synthesizes tests, otherwise a model solution or grader concern

## Default Workflow

Use this loop unless the current task is tiny and already well specified.

1. Write the contract in one sentence.
   - Example: `Write a strict validator for n,m and a simple undirected graph with no loops or multi-edges.`
   - Example: `Write a checker that accepts any valid minimum spanning tree witness and rejects malformed edge lists before scoring.`
2. Name the file type explicitly.
   - `validator`, `generator`, `checker`, `interactor`, `grader`, `stub`, `header`, or `local harness`.
3. Load the smallest relevant references.
4. Start from a minimal skeleton from [templates.md](references/templates.md), not from a blank file.
5. Add only the checks or helper routines that belong to that component.
6. Write the local compile and run commands immediately.
7. Probe hostile cases:
   - malformed input for validators;
   - malformed contestant output for checkers;
   - flush / EOF / invalid-command behavior for interactors;
   - API mismatch probes for graders.
8. Run the relevant checklist before handoff.

## Deliverables

A good `using-testlib` run usually leaves behind:

- one compileable artifact;
- one canonical local compile command;
- one canonical local run or replay command;
- a small probe bank covering the trust boundary;
- clear verdict discipline;
- deterministic generator parameters and any explicit seed discipline where relevant.

## Fast Routing

Use these rules to avoid reading irrelevant material.

- If you are only validating official input shape and hidden promises, skip checker and interactor references.
- If you are only building random testcase sources, skip checker verdict details and focus on generator reproducibility.
- If you are writing a constructive or optimization checker, load `checkers.md`, `templates.md`, and `review-checklists.md`.
- If the task is interactive, communication-like, scored output, or invokes
  contestant functions through a grader/stub, stop this component path and
  return the signal to the ICPC Light workflow.

## Red Flags

Stop and fix the design if you see any of these:

- validator missing `inf.readEof()`;
- validator reading loosely when the statement promises exact line structure;
- generator depending on hidden global randomness or wall-clock time;
- generator with undocumented command-line knobs or unused options;
- checker accepting only the jury witness instead of any valid witness;
- checker using `_wa` for jury-answer corruption or impossible organizer-side states;
- checker scoring before checking legality;
- no faithful local replay command.

## Provenance

This skill is based on:

- the official upstream `testlib` repository and current `testlib.h`;
- official bundled checker, validator, generator, and interactor samples from that repository;
- a compact synthesis of practical validator, checker, generator, and local
  replay guidance.

Latest upstream `testlib.h` (download it if you don't see it locally):

- https://raw.githubusercontent.com/MikeMirzayanov/testlib/master/testlib.h
- Repository: https://github.com/MikeMirzayanov/testlib
