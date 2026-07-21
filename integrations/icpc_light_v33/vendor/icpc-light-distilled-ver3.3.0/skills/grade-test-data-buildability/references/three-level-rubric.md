# Three-Level Preclassification Rubric

Use this rubric only after checking route adoption and the unverifiable-
foundation stop gate. Score each axis as `1`, `2`, `3`, or `stop`, then use the
hardest-axis rule.

## Contents

- Operational threat model
- Stop gates
- Five axes
- Level 1: random-strong
- Level 2: structured-bounded
- Level 3: adversarial-intensive
- Conservative tie-breaking

## Operational Threat Model

Treat a wrong or heuristic route as one that lacks a complete correctness proof
or lacks a valid worst-case complexity guarantee, even if it performs well on
ordinary random inputs. Exclude programs that inspect or hardcode the released
test files.

The ideal of strong data is that every logically wrong route fails somewhere.
A finite release process cannot prove that universal statement. Use this
auditable acceptance proxy instead:

- derive representative routes from blind solves, proof mutation,
  implementation boundaries, resource analysis, and selected topic leaves;
- retain only high-priority, contestant-plausible, materially distinct routes;
- strengthen retained routes to their strongest natural still-unproved/fragile
  versions and reject trivial dominators;
- require every retained route to compile, pass public samples plus machine-run
  small, ordinary-random, and structured survivability cases, run against the
  suite, and have a legal deterministic breaker with an observed rejection;
- cover every named proof boundary and important size, value, aggregate, and
  resource limit.

Do not claim coverage of the entire space of incorrect programs.

## Stop Gates

Apply these before assigning a level.

### Confirmed Simpler Full Route

Set `scam_status: confirmed` only when a significantly simpler full route has:

- a complete independent proof or equivalent correctness certificate;
- a valid worst-case complexity under the full contract;
- executable full-constraint source code;
- sample plus exhaustive/oracle/differential evidence; and
- a clear comparison with the expected route.

Freshly materialize that executable source at
`audit/private/selected-standard-route.cpp` and make it the canonical input to
the later standard-solution stages. Then score all five data-construction axes
for the selected route and return P1, P2, or P3 with `decision: continue`,
`provisional: false`, and `stop_reason: none`. A correct simpler route changes
the standard solution; it does not stop the workflow. Do not manufacture data
to reject it.

If a shortcut is plausible but not yet proved or refuted, set
`scam_status: suspected`, assign provisional P3 compatibility fields, and use
`decision: escalate`. Resolve correctness before data construction. A
deceptive lane's assertion alone never confirms a scam problem. Keep the
current active verified blind route at the fixed selected-source path until the
suspicion is resolved; never promote the suspected route to std.

When no simpler route is confirmed, freshly materialize the current active
verified blind route at the same fixed path. Thus every continuing
preclassification selects exactly one concrete standard-route source.

### Unverifiable Problem

Return `S-stop` when release evidence cannot be made trustworthy, including:

- statement or checker semantics cannot be frozen;
- no trustworthy solution, oracle, certificate, replay, or independent
  verification path can be established;
- legal instances cannot be validated reliably;
- a checker effectively performs an unauditable hard optimization;
- irreducible interaction, random correctness, or floating-point behavior
  prevents deterministic verification.

S-stop always uses `scam_status: none`. A confirmed or suspected simpler route
is not an unverifiable foundation and therefore cannot produce S-stop.

P3 means a hard data-construction landscape. A continuing automatic P3 run is
bounded, but the classification itself also covers harder landscapes that must
be escalated. P3 must not hide an unverifiable contract or correctness
foundation.

Do not use S-stop merely because test construction may need more work than the
Light budget can bound. If the contract, intended solution, oracle/checker, and
legal generation remain trustworthy but the attack landscape has no credible
1--3-round closure plan, classify it as P3 and use `decision: escalate` with an
unbounded-adversarial-plan reason.

## Five Axes

Score all five and preserve the evidence in the report.

1. **Random representativeness:** whether ordinary random, duplicate-heavy,
   small-domain, and extreme-biased data naturally exercise the proof.
2. **Wrong-route landscape:** the number and independence of plausible
   heuristics, how often they survive ordinary tests, and whether their
   breakers can be made stable and scalable.
3. **Legal generation:** whether strong legal structures can be generated and
   checked without solving another hard problem or collapsing to weak shapes.
4. **Oracle and output judgment:** whether answers or witnesses can be checked
   independently, including non-unique output and checker responsibility.
5. **Resource reproducibility:** whether complexity, memory, stack, overflow,
   numeric, and protocol failures can be triggered legally and deterministically.

Use the hardest-axis rule:

- any `stop` axis returns `S-stop` only for an unverifiable foundation, with
  `scam_status: none`;
- all axes at level 1 return P1;
- at least one level-2 axis and no level-3 axis return P2;
- at least one level-3 axis, with the correctness and verification foundation
  still independently trustworthy, returns P3. Use `continue` when the attack
  plan is bounded and `escalate` when it is not.

These P1/P2/P3 mappings do not change when `scam_status: confirmed`; they are
applied to the newly selected simpler route.

## Level 1: P1 Random-Strong

Assign `P1-random-strong` only when ordinary random distributions and simple
boundaries already have strong discrimination.

Typical evidence:

- the input space is direct or highly symmetric;
- a trustworthy tiny oracle and exact output comparison are available;
- neutral lanes converge;
- deceptive assumptions fail samples, basic boundaries, ordinary uniform
  random data, or ordinary duplicate-heavy random data;
- no tie gadget, special tree shape, periodic string, geometric degeneracy,
  coupling witness, or other purposeful structure is needed;
- maximum-scale resource and integer-width cases are deterministic.

Use `D0-direct / L0-simple-standard`, retain and run 3--5 qualified wrong
solutions, and allow one adversarial round.

Examples can include constant-sized numeric inputs whose cases are symmetric,
or counting problems with super-polynomially many answer-producing
configurations where omissions and double-counts fail across many ordinary
distributions. Treat these as signals, not automatic rules.

## Level 2: P2 Structured-Bounded

Assign `P2-structured-bounded` when random data alone is insufficient but a
finite, well-understood construction plan should close the important gaps in
one adversarial round.

Typical evidence:

- one to three distinct structured failure mechanisms dominate the risk;
- each important wrong route has a describable, reproducible, scalable breaker;
- tie, ordering, coupling, witness-legality, or reduction-faithfulness cases
  are needed but remain straightforward to generate and validate;
- ordinary random generation overproduces one output class such as `No`,
  infeasible, or invalid, but a finite set of feasible/infeasible and
  positive/negative construction modes restores balance;
- a few proof branches or model-solution branches are rare under random data
  but each has an explicit reachable witness;
- the oracle/checker path is trustworthy;
- patching one failed heuristic does not reveal an open-ended family of
  unrelated heuristics.

Use `D1-structured` and one L1 profile. Retain and run 5--8 qualified,
materially distinct wrong solutions. Allow one adversarial round; a later
repair for a concrete implementation defect is not a second general attack
round.

## Level 3: P3 Adversarial-Intensive

Assign `P3-adversarial-intensive` when ordinary random data systematically
hides decisive structure and several independent plausible routes need
different purposeful attacks, while the verification chain remains reliable.

Strong signals include:

- random trees are shallow while path, broom, star, double-star, or hybrid
  shapes control correctness or complexity;
- random strings have little periodicity, few long palindromes, and short LCPs;
- random point sets have a small hull or avoid geometric degeneracy;
- random instances collapse toward `No`, infeasible, invalid, or another
  dominant output while many distinct legal witness modes require separate
  construction;
- many greedy, local-repair, missing-state, or false-monotonicity routes survive
  ordinary data and require different breakers;
- complex rules or proof branches interact so that patching one rare branch
  exposes another independent route;
- optimization problems admit several strong local heuristics;
- flow, matching, cost-flow, convex optimization, CHT, or reduction shortcuts
  require faithfulness and coupling constructions; in CHT-style DP, ordinary
  random instances may keep the maintained hull artificially small;
- amortized structures need hostile fragmentation, height, or update schedules;
- color-segment amortization and Cartesian-tree methods need non-random segment
  fragmentation and tree-height shapes;
- legal data has several structural modes and a merely valid random generator
  would produce weak tests.

P3 still requires a frozen contract, trusted intended correctness evidence,
an independent deterministic oracle/checker path, and validated legal
generation. These are verification requirements, not a promise that the test
attack itself fits the Light budget. If the attack cannot be bounded to 1--3
credible rounds, retain P3 and escalate instead of relabeling the problem
S-stop.

Use `D2-specialist / L2-high-risk`. In a continuing P3 run, retain and run
8--10 qualified, materially distinct wrong solutions and allow one to three
adversarial rounds:

1. Run the initial complete wrong-route matrix and purposeful family set.
2. Run another round only for a named survivor, uncovered proof boundary, or
   newly discovered independent failure mechanism, with a concrete new attack
   hypothesis.
3. Permit a third round under the same rule. Never repeat an unchanged search.

Every additional round must add auditable information: a new breaker, a route
proved correct or wrong, a closed coverage gap, or an explicit escalation.
Escalate if one round makes no material progress, the same important survivor
remains after two focused rounds, the third-round limit is reached with a
survivor, or confidence requires an unbounded or external-specialist attack.

The 8--10 requirement is the final qualified set across all rounds, not a new
quota per round. An escalating P3 report preserves 8--10 and 1--3 as profile
compatibility fields, but they do not authorize construction before the
handoff is resolved and the problem is regraded.

## Conservative Tie-Breaking

When evidence lies between adjacent levels, select the harder level unless
positive evidence establishes the easier one. Do not lower a grade because a
counterexample is small when finding its structure requires a purposeful
gadget. Do not raise P3 to `S-stop` merely because construction is difficult;
stop only when correctness or verification cannot be trusted. A verified
simpler route is adopted as std and remains in P1/P2/P3. An unbounded
construction effort remains P3 with `decision: escalate`.
