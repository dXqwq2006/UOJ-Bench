# Build and Harden

Turn the frozen contract and reviewed solution into a reproducible local ICPC
package. Couple wrong-solution design with tests instead of building either in
isolation.

Load [artifact-contracts.md](artifact-contracts.md) before writing package or
audit files and [topic-routing.md](topic-routing.md) before consulting the
vendored topic knowledge.

Run the construction/testing/attack stage through its production receipt with
`gpt-5.6-sol` and reasoning effort `ultra`; do not spawn unreceipted nested
agents. Keep the owning stage active until its executable
result and audit contract are complete. A child that only proposes code/data,
leaves commands unrun, misses its quota, or reports future work has failed the
stage; rerun the owning work within the explicit repair/round scope. When that
scope is exhausted, report escalation and stop rather than forwarding a partial
package.

## Contents

- Build the executable chain
- Differential-test the model
- Select plausible wrong solutions
- Design purposeful data
- Run profile-bounded adversarial rounds and regress once

## Build the Executable Chain

Create or audit:

- `package/std.cpp` as the reviewed model solution;
- `package/brute.cpp` or another trustworthy small oracle;
- `package/validator.cpp` matching the frozen input grammar;
- `package/checker.cpp` only when token comparison cannot express the answer;
- deterministic generators under `package/generators/`;
- the complete statement sample set plus hash-bound
  `package/samples/manifest.json` under `package/samples/`;
- reproducible stress and regression commands.

All mandatory artifacts must be non-empty and runnable. Enter this stage first
in `std-materialization` mode only: consume `audit/solution-review-draft.md`,
create the actual `package/std.cpp`, and record whether it is an exact copy of
the verified blind source or enumerate every packaging/code delta. Return that
source to a new review context. Only after the reviewer writes a passed
canonical `audit/solution-review.md` binding both current hashes may this agent
unlock oracle, generator, official-data, wrong-route, and regression work.

A small-only DP, special-family answer routine, answer-file generator, or
collection of known optimal large families is an oracle component, not a
substitute for `std.cpp`. If no complete full-constraint route is available,
return to the persistent blind-solve loop. Do not continue as a data-only
build, and do not let the materializing agent self-certify its own source.

Read the sibling [using-testlib](../../using-testlib/SKILL.md) skill for
component-level implementation and trust-boundary rules. Establish this chain
before designing official data:

```text
generator -> validator -> std versus brute/oracle differential test
```

Store release tests under `package/tests/`. Do not create a parallel root
`tests/` tree that bypasses package and readiness checks. Compile and exercise
the skeleton before starting hardening. Run the executable completion gate only
after the qualifying differential test, wrong-route quota, adversarial rounds,
and full regression are complete; a missing or uncompilable mandatory artifact
is a failed stage, not an accepted risk.

Make generator commands fully replayable. Validate every generated input. Make
a custom checker accept every legal answer, not only the jury witness, and
reject malformed output without confusing contestant and organizer failures.
Use the safe testlib checker exit contract by default: 0 accepts, 1 is WA, and
2 is PE. Only exit codes explicitly assigned to WA/PE in the regression plan
may count as a contestant rejection; timeout, signal, internal-failure, and
unknown codes are infrastructure failures. Never “kill” a wrong solution with
a checker crash.

Implement `package/brute.cpp` independently from `package/std.cpp`: its source
hash must differ, and the plan must state both the concrete independence basis
and the input domain on which the oracle is authoritative. Renaming or copying
the standard solution is not oracle evidence.

## Differential-Test the Model

Exhaust all tiny legal instances when the state space is tractable. Otherwise
run 5,000--10,000 consecutive reproducible small random seeds. Use a 100-seed
run only as a smoke test; never use it for final acceptance.

Cover uniform random, duplicate-heavy, small-domain, biased-extreme, and
problem-specific structures. Run relevant sanitizers. On every discrepancy:

1. preserve the seed and inputs/outputs;
2. minimize the witness when practical;
3. determine whether model, oracle, generator, validator, checker, or contract
   is wrong;
4. if `repair_used` is already true, stop and escalate; otherwise set it true
   and fix the responsible artifact;
5. promote the minimized witness into permanent regression coverage;
6. restart the qualifying consecutive-seed count.

The single repair budget begins only after the executable differential chain is
established; ordinary compile/setup corrections before the first stress run do
not consume it. Do not continue to official hardening while model correctness
is unresolved. Escalate if a second independent discrepancy appears, the same
failure recurs, or the repair exceeds the profile's one-defect scope. Elapsed
wall-clock time by itself is never that condition.

## Select Plausible Wrong Solutions

Generate 10--15 candidates before implementation for P1/P2 and 15--20 for P3.
These are design candidates, not a quota to implement. Derive roughly half
from this problem's proof, then use selected topic leaves and implementation
review to fill genuine coverage gaps. Require every candidate to state:

- wrong assumption and why a contestant may adopt it;
- exact changed condition, transition, lemma, or boundary;
- expected WA/TLE/MLE/OLE/RE behavior under the statement-bound limits;
- concrete semantic or resource failure mechanism.

Rank candidates by:

```text
adoption probability * impact * coverage gap / verification cost
```

Use the exact quota from the schema-v2 grade report:

- P1: 3--5 qualified wrong solutions;
- P2: 5--8 qualified wrong solutions;
- P3: 8--10 qualified wrong solutions in total across all rounds.

A route counts as qualified only after it is materially distinct, compiles and
runs, passes every entry in the canonical package sample manifest and at least
one ordinary legal case, has a
legal deterministic breaker, and produces the expected observed rejection.
For resource failures, save measured evidence under the target limits. Prefer
routes that differ from the correct route by one meaningful condition. Reject
or merge trivial, nonsensical, private-detail, hidden-seed, duplicate,
sample-failing, data-hardcoded, or still-possibly-correct candidates. Send an
unbroken correct-looking route back to correctness review instead of counting
it as wrong. Store all wrong-source code under
`audit/private/wrong-solutions/`, never in the release package.

Before qualification, strengthen each route to the strongest natural version
that preserves its named missing guarantee. Apply the relevant tiny/exact
fallback, obvious edge-case fixes, safe repair, retuned constants, multiple
orders, or small portfolio that a competent contestant would naturally add.
Reject a candidate that is strictly dominated by such a local improvement.
Keep a route whose intended failure is resource-only logically correct; do not
add an unrelated WA merely to make it easy to kill.

Every qualified route must also AC a small/exact-friendly input, an ordinary
random-style input, and a structured-friendly input selected independently of
its breaker. Put at least three such cases in the regression plan's
`survivability_inputs`; the machine runner, not prose, records their verdicts.
Record the applied strengthening, passed trivial-dominator review, and receipt-
backed survivability evidence in `audit/wrong-solutions.md`. A route that only
passes one convenient ordinary case remains a baseline and cannot fill the
profile quota.

Do not let each route nominate its own convenient sample subset. Put every true
statement sample and answer under `package/samples/`, and make every route use
the exact manifest order. Keep ordinary witnesses in
`package/tests/ordinary/`, breakers in `package/tests/breakers/`, and release
inputs/answers as same-stem pairs below `package/tests/`.

Do not leave this stage merely because an initial candidate set was weak.
Continue generating, implementing, and attacking materially distinct routes
until the profile minimum is genuinely qualified, or the permitted adversarial
scope produces an explicit escalation condition. Sample-failing stubs and
unexecuted evaluator functions never fill the count.

## Design Purposeful Data

Design wrong solutions and breakers together in `audit/wrong-solutions.md`.
As planning guidance, target about 20--50 purposeful families for P2 and
30--60 accumulated families for P3; P1 may use a smaller set when every gate is
still covered. Family count is not a substitute for evidence. Include:

- 5--10 boundary cases;
- 5--15 structural or proof-boundary cases;
- 1--3 clean breakers per important wrong route;
- 2--5 full-scale performance/resource cases;
- a few noisy variants only when special-casing is plausible.

Exercise every important maximum constraint rather than merely setting one
headline size. Measure resource failures when claiming TLE, MLE, stack, or
overflow coverage. Add a reusable generator knob for a general missed
mechanism; avoid one-off opaque hacks.

Enumerate every problem-relevant scale axis, including per-case and aggregate
limits such as `n`, `m`, `q`, `T`, `sum n`, `sum m`, value/coordinate range,
alphabet, degree, density, recursion depth, live state/memory, and legal output
size. Cover `T=1` giant and high-`T`/aggregate regimes when they exercise
different costs. The list is problem-specific: do not invent irrelevant axes,
but do not let an LLM declare only the easiest headline maximum.

For each important discrete boundary, include the exact point and the legal
just-inside/just-outside neighbors when they exist. Treat structure, ordering,
answer regime, and scale as composable knobs. Cover a small number of proof-
coupled interactions, especially semantic hard structure combined with the
resource scale that makes its worst case observable. A collection containing
separate `n=max`, path, and hostile-order cases does not cover a failure that
requires all three. Add structural perturbations rather than counting a label
shuffle or a new seed as a distinct noise mechanism.

Write the canonical private `audit/coverage-matrix.json`. It is a compact,
machine-checked map from the six route-risk axes and named proof/contract/
resource obligations through purposeful families to concrete regression input
paths, generator or fixed-witness provenance, seed/case parameters, limit
axes, variant modes, and composed dimensions. Every qualified wrong route,
required limit tag, purposeful family, and release input must participate in
that map. This replaces neither executable regression nor semantic review; it
prevents unbound family claims. Do not restore the source mechanism/
composition/scale/noise ledger pipeline or combinatorial omission accounting.

Use selected vendored topic references only as candidate generators under
[topic-routing.md](topic-routing.md). Do not inherit their subtask obligations,
large ledgers, mandatory review counts, or implement-every-route policy.

## Run Profile-Bounded Adversarial Rounds

Create `audit/adversarial-rounds.md` as a human index and preserve one row or
section per round. It is not completion evidence by itself. Create the
corresponding JSON plan and run `scripts/record_adversarial_round.py`; only a
passed, append-only receipt under `audit/adversarial-round-receipts/` completes
the round. Run `scripts/verify_adversarial_round_chain.py` over the graded
minimum/maximum before regression. Read [artifact-contracts.md](artifact-contracts.md)
for the exact plan and receipt commands.

Every continuing run performs round 1 against the complete current
wrong-route/test matrix. P1 and P2 stop general attack work after round 1. P3
permits 1--3 rounds and may run round 2 or 3 only when the prior round records
one of these triggers:

- a named important survivor;
- an uncovered proof boundary;
- a newly discovered independent failure mechanism.

Before opening an additional round, write a concrete new attack hypothesis.
For every round record the trigger, active routes, new or changed breaker
families, killed routes, survivors, exact commands, and material result. Run
each new or changed input through the validator and reviewed model/oracle, then
run it against all retained wrong solutions rather than only its intended
target. A P3 round may finish early when 8--10 qualified routes are covered and
no readiness gap remains.

Do not copy verdicts into the receipt from this table. The recorder compiles
and runs the named private source through the production LightCPVerifier
backend, captures stdout/stderr/exit/time/memory, and derives
AC/WA/TLE/MLE/OLE/RE. The
round-chain verifier rejects test-mode or local-backend receipts. Round `N > 1`
must hash-link round `N-1` and include every prior machine-observed survivor.
Any changed route source, test, answer, plan, or previous receipt invalidates
the chain and reopens hardening.

Use `tokens`, `exact`, or source-bound `checker` comparison for production
adversarial-round plans. Checker mode requires top-level
`checker_source: package/checker.cpp`; omit `checker_command`. The recorder
compiles the checker separately and runs it through LightCPVerifier with
hash-bound input/candidate/answer copy-ins. Only the declared checker exit
contract may produce AC/WA; timeout, resource failure, launch failure, or an
unknown exit fails closed as infrastructure error. The legacy
`checker_command` form remains testing-only under
`--test-mode --execution-backend local` and cannot complete the chain.

Do not repeat an unchanged search. Escalate when one round makes no material
progress, the same important survivor remains after two focused rounds, round
3 ends with an important survivor, or the required attack is unbounded or
external-specialist. Planned adversarial rounds do not consume `repair_used`;
repairs are reserved for semantic defects in the package or harness under
[orchestration.md](orchestration.md).

## Regress Once

Write the executable plan to `audit/regression-plan.json`, then run the
canonical `scripts/run_regression_gate.py`. It compiles and actually runs the
chain, writes hash-bound `audit/regression-machine.json`, and fails if any
claimed sample/ordinary/breaker/release/differential check was not observed.
Use regression-plan schema version 3 and include its private `resource_policy`:
bind the current statement resource receipt and state the intended complexity,
maximum scale, time-limit rationale, and memory-limit rationale. This is audit
metadata, not a new release-package file.
Generate the object instead of hand-calculating its hashes:

```bash
python3 "$SKILL_ROOT/scripts/build_resource_policy.py" \
  --problem-dir "$PROBLEM_DIR" \
  --intended-complexity 'O(n log n)' \
  --maximum-scale 'n = 200000' \
  --time-limit-rationale 'Measured full-scale std with safety margin.' \
  --memory-limit-rationale 'Peak model storage remains below the stated ML.'
```

Insert the printed object unchanged as `resource_policy` in the regression
plan. The gate recomputes every hash and rejects stale statement evidence.
In ver3, the production command executes every submitted program through the
CPIdeas Program × Dataset adapter backed by LightCPVerifier. Plan semantics,
verdict classification, and receipt bindings remain owned by the regression
gate. The adapter batches ordered points (at most 128 per service request), and
missing CPIdeas/LightCPVerifier dependencies fail the gate explicitly; they do
not trigger a local subprocess fallback. The local backend is testing-only and
requires both `--test-mode` and `--execution-backend local`.
Production sends the statement-bound TL and ML unchanged to LightCPVerifier and
requires the service to attest the same effective values. The sandbox therefore
classifies TLE/MLE at the problem's limits; a fixed 2-second or 1024-MiB fallback
is not production evidence.
Require complete per-invocation chunk evidence and matching attested
service/client identities. Follow the
[bundle migration guide](../../../MIGRATION.md) to prepare the Python adapter
and service before running this stage.
The receipt must hash-bind every consumed generator, canonical sample and
answer, ordinary input, breaker, release input/answer, oracle, checker, and
qualified wrong source. `verify_completion.py` re-hashes these bindings so a
post-regression file replacement invalidates final completion.
For every qualified wrong route, include non-empty `survivability_inputs` with
at least `small`, `random`, and `structured` legal points; the machine runner
must observe AC on all of them before the breaker verdict counts. Optionally
list private `accepted_alternatives`; each is compiled once and executed on
every release test through the same comparison/checker. Comment/whitespace
copies of `std.cpp` are rejected, and the reviewer must still establish
material implementation independence in the plan-bound `independence_basis`.
With a custom checker, the alternatives
must also yield at least one checker-accepted release output whose token
sequence differs from the jury answer. A custom-checker plan with no known
alternative must carry a concrete, plan-bound
`accepted_alternative_waiver` with its basis and search scope.
Record the human-readable summary in `audit/regression.md`. Include validator, model, oracle where applicable,
accepted alternatives, retained wrong solutions, checker probes, sanitizer or
resource checks, official-test reproducibility, and a scan confirming no
private artifacts enter the release package.
Schema v3 machine-checks accepted alternative outputs, but malformed-output
checker probes are still reviewed evidence in this summary; do not describe
them as a machine-enforced matrix unless a later schema adds that field.
Read the machine receipt before accepting the data: inspect the ordered
per-case verdicts and each role's maximum observed time/memory, compare them
with the intended algorithm and full-scale cases, and revise the private data
or the explicitly declared limits when the margin is unjustified. Any such
revision invalidates the old receipt and must be rerun; never let the same LLM
replace machine evidence with its own local self-assessment.

If one concrete Light-scope package defect remains, return it for the single
targeted repair defined by orchestration. Escalate a repeated high-risk
survivor, unreliable oracle, disputed proof, exhausted P3 round budget, or
external-specialist attack instead of expanding the workflow without bound.
Do not request readiness until the clean machine rerun passes every regression
field and `verify_completion.py` independently reruns/verifies it and writes a
current receipt. An empty `run-regression.sh`, an exit-zero claim, or
frontmatter-only counters do not count.
