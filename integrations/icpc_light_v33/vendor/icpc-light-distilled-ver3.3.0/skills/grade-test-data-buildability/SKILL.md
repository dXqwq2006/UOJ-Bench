---
name: grade-test-data-buildability
description: "Preclassify how difficult it is to build strong test data for an ICPC problem. Use after the full builder has completed and machine-verified its public-only neutral/deceptive blind stage, or explicitly in audit-only provisional mode, to choose P1 random-strong, P2 structured-bounded, or P3 adversarial-intensive, adopting any independently verified simpler full route as the standard solution and stopping only for unverifiable foundations. This is a test-construction risk grade, not a problem-quality or contestant-difficulty score."
---

# Grade Test Data Buildability

Preclassify test-data construction before package hardening. Write the
canonical `audit/data-buildability.md` and freshly materialize the selected
full-solution source at `audit/private/selected-standard-route.cpp`;
downstream agents own all later construction.

Run this grader with model `gpt-5.6-sol` and reasoning effort `xhigh`. Refuse a
lower/different execution configuration. Do not write a full-build grade until
the blind-stage verifier passes; an incomplete input returns to the owning
stage or becomes an explicit audit-only provisional opinion, never a fabricated
continuing handoff.

Read [three-level-rubric.md](references/three-level-rubric.md) completely before
grading. Apply its route-adoption and unverifiable-foundation rules before its
three levels.

## Gather Evidence

Read, when available:

- statement, constraints, grammar, samples, and public attachments;
- intended solution, proof, reference implementation, or trustworthy outline;
- public-only blind summary, including deceptive/shortcut candidates;
- existing oracle, validator, checker, generator, and known wrong solutions.

Do not read hidden tests or topic catalogs to improve supposedly blind evidence.
Treat idea-quality and contestant difficulty scores as unrelated.

## Score and Route

Score the rubric's five axes as `1`, `2`, `3`, or `stop`. Use the hardest-axis
rule and these fixed compatibility mappings:

| preclassification | compatibility grade | profile | default decision | qualified wrong solutions | adversarial rounds |
| --- | --- | --- | --- | ---: | ---: |
| `P1-random-strong` | `D0-direct` | `L0-simple-standard` | `continue` | 3--5 | 1 |
| `P2-structured-bounded` | `D1-structured` | one L1 profile | `continue` | 5--8 | 1 |
| `P3-adversarial-intensive` | `D2-specialist` | `L2-high-risk` | `continue` | 8--10 | 1--3 |
| `S-stop` | `D3-stop` | `outside-light` | `stop` | 0 | 0 |

For P2 choose exactly one profile with this precedence:

```text
L1C-constructive-output > L1F-flow-model-like > L1G-greedy-deceptive > L1-ordinary
```

Keep secondary signals in `risk_tags` and `required_checks`.
Use the canonical risk tags `constructive-output`, `flow-model-like`, and
`greedy-deceptive` when those signals apply. The bundled validator enforces the
precedence above whenever more than one is present.

P3 is the hard test-construction class, not automatically outside the
workflow. Use
`decision: continue` only when the contract, intended correctness, oracle or
checker, legal generation, and attack plan are independently trustworthy. If
the correctness or verification foundation is temporarily incomplete, keep
provisional P3 compatibility fields and use `decision: escalate` until
regrading. If that foundation is trustworthy but no credible 1--3-round attack
plan exists, keep non-provisional P3 and use `decision: escalate`; construction
difficulty alone is not S-stop.

## Handle Shortcut Evidence

Use `scam_status: confirmed` only for an independently proved and verified
significantly simpler full route whose executable source is valid under the
complete contract. Freshly materialize that source as
`audit/private/selected-standard-route.cpp`, make it the canonical input for
the later standard-solution stages, and score the five data-construction axes
against this selected route. Return the resulting P1, P2, or P3 profile with
`decision: continue`, `provisional: false`, and `stop_reason: none`. Do not try
to kill a correct simpler route with data, and do not return S-stop merely
because it invalidates the previously expected route.

Use `scam_status: suspected` for an unresolved correct-looking shortcut. Return
provisional P3 compatibility fields with `decision: escalate`; do not count it
as a wrong solution or begin construction until correctness review resolves it.
Materialize the current active verified blind route, not the suspected route,
at the fixed selected-source path so no unproved source can become std.

When `scam_status: none`, freshly materialize the current active verified blind
route at the same fixed path. Every continuing full-builder report therefore
hands downstream stages one concrete selected source. An audit-only provisional
opinion is non-forwardable when no trustworthy full source exists and must not
pretend that a canonical source was selected.

## Handle Missing Evidence

- In a full `icpc-light-problem-builder` run, do not grade at all until the
  executable blind-stage gate proves the initial clean 2+2 batch and at least
  one independently verified full blind route. Missing blind or full-solution
  evidence returns control to the persistent blind loop; it never authorizes a
  data-only downgrade.
- Only when the user explicitly requests an audit-only provisional opinion may
  a report be written with incomplete evidence. Without public-only blind
  evidence, set `provisional: true` and never assign P1. Without any trustworthy
  full solution/proof evidence, assign at least provisional P3 and use
  `decision: escalate`.
- With trust-based rather than enforced blind isolation, retain a
  `trust-based-blind-isolation` risk tag and do not derive high confidence from
  lane agreement alone.
- Lower confidence rather than inventing evidence.
- Regrade when the contract changes, an oracle fails, checker behavior grows,
  reviewers disagree, a shortcut is confirmed/refuted, or an important route
  survives a permitted adversarial round.

## Write Schema Version 2

Start `audit/data-buildability.md` with exactly these stable fields:

```yaml
---
schema_version: 2
agent_model: gpt-5.6-sol
agent_reasoning_effort: xhigh
preclassification: P1-random-strong | P2-structured-bounded | P3-adversarial-intensive | S-stop
scam_status: none | suspected | confirmed
data_buildability: D0-direct | D1-structured | D2-specialist | D3-stop
workflow_profile: L0-simple-standard | L1-ordinary | L1G-greedy-deceptive | L1C-constructive-output | L1F-flow-model-like | L2-high-risk | outside-light
decision: continue | escalate | stop
confidence: low | medium | high
provisional: true | false
wrong_solution_min: 3
wrong_solution_max: 5
adversarial_round_mode: single | bounded-multi | none
adversarial_round_min: 1
adversarial_round_max: 1
stop_reason: none | shortcut-unresolved | unverifiable-contract | unverifiable-oracle | unverifiable-generation | unverifiable-checker | unverifiable-protocol | unverifiable-numeric | unbounded-adversarial-plan | adversarial-budget-exhausted | outside-scope
risk_tags: []
required_checks: []
regrade_triggers: []
---
```

Replace every union with one value and use integers for counts. The model and
reasoning fields are fixed and may not be replaced. Enforce these
combinations:

- P1: wrong solutions `3/5`, rounds `single`, `1/1`;
- P2: wrong solutions `5/8`, rounds `single`, `1/1`;
- P3: wrong solutions `8/10`, rounds `bounded-multi`, `1/3`;
- S-stop: wrong solutions `0/0`, rounds `none`, `0/0`.

`scam_status: confirmed` is valid only on a non-provisional continuing P1, P2,
or P3 report. S-stop is reserved for an unverifiable foundation and requires
`scam_status: none`.

Require `stop_reason: none` exactly when `decision: continue`; require a
specific non-`none` reason when the decision is `stop` or `escalate`. A
schema-v1 report is legacy evidence and must be regraded before a new run
consumes it; D2 changed meaning in schema v2.

After the frontmatter include:

1. a five-axis evidence table with numeric/stop scores;
2. three to eight artifact-backed reasons;
3. shortcut evidence, its proof status, and which executable source was freshly
   selected at `audit/private/selected-standard-route.cpp`;
4. exact missing evidence when provisional;
5. the concrete next owner for `escalate` or `stop`.

Before handing the report downstream, validate the replaceable interface from
this skill directory:

```bash
python3 scripts/validate_report.py --report /absolute/problem/audit/data-buildability.md
```

Run `python3 scripts/validate_report.py --self-test` after replacing this
temporary grader. The built-in forward fixtures cover D0, ordinary/L1G/L1F/L1C
D1 routing, D2/P3, unverifiable D3/S-stop, confirmed-route adoption at every
continuing level, suspected-route escalation, provisional rejection, and risk
precedence.

Do not construct tests, add subtasks or scores, claim release readiness, or
promise rejection of every possible incorrect program. This report selects the
bounded workflow that performs those checks.
