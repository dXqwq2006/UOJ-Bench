# ICPC Light Distilled Bundle ver3.3

This directory contains a versioned ICPC problem-building workflow. Its policy,
agents, references, and gate scripts are self-contained: it does not load files
from `src copy`, `src copy zh`, the legacy OI orchestrator, or the root
`orchestrator.skills`. ver3 intentionally has one external production runtime
dependency: submitted-program compilation and execution use CPIdeas Plus's
Program × Dataset adapter and a healthy LightCPVerifier service.

The bundle version is [`3.3.0`](VERSION). See [CHANGELOG.md](CHANGELOG.md) for
release changes and [MIGRATION.md](MIGRATION.md) before moving a ver2 problem or
receipt chain to ver3. The ver2 directory remains a separate, unchanged bundle.
See [ISOLATION.md](ISOLATION.md) for the exact enforced and still-trusted
boundaries; in particular, submitted-code sandboxing does not imply that blind
LLM lanes are already VM-isolated.

## Contents

- `skills/icpc-light-problem-builder/`: workflow, machine gates, runners, and a
  vendored subset of relevant topic knowledge;
- `skills/grade-test-data-buildability/`: replaceable formal P1/P2/P3
  preclassifier and its schema validator;
- `skills/using-testlib/`: vendored testlib guidance used by the builder;
- `agents/`: thin entry points. They contain no independent workflow rules and
  route back to the builder references;
- `VERSION`, `CHANGELOG.md`, and `MIGRATION.md`: bundle identity, release notes,
  production dependencies, and side-by-side migration guidance.

All agent work must use exactly `gpt-5.6-sol` with reasoning effort `ultra`.
The launchers record and verify that pair; another model or a fallback does not
satisfy a production gate.

## Workflow

1. **Audit resources, then initialize.** Before writing any run artifact,
   require explicit, unambiguous TL and ML declarations in `statement.md`.
   Then locate the problem root, create `audit/run-state.md`, inventory only
   contestant-visible files, and hash them into the blind public manifest.
2. **Run real public-only blind solves.** Start the deterministic initial 2
   neutral + 2 deceptive lanes in parallel. A neutral lane must leave
   `main.cpp` and `final-status.md`; a deceptive lane must leave
   `final-status.md`. Every workspace, prompt, stdout/stderr log, exit status,
   hash, failure, and contamination result is retained.
3. **Persist until there is a verified solution.** A fresh reviewer executes
   against each credible neutral `main.cpp`. Failed, incomplete, contaminated,
   or disproved routes never count. Create fresh replacement/focused waves and
   keep waiting until the executable blind gate sees at least two clean lanes
   of each kind and one active independently verified full solution. All waves
   and reviewers share the 7,200-second clock established by the first
   production launch. At expiry, live process groups are terminated, evidence
   is preserved, the stage is marked failed/escalated, and no downstream stage
   starts.
4. **Formally preclassify data construction and select the standard route.**
   Only after the blind gate passes, assign P1, P2, P3, or S-stop. P1 requires
   3–5 qualified wrong solutions; P2 requires 5–8; P3 requires 8–10 and permits
   1–3 material adversarial rounds. Every run freshly writes the fixed,
   hash-bound `audit/private/selected-standard-route.cpp`. A simpler route that
   is independently proved correct and made executable becomes that selected
   route and continues through P1/P2/P3; merely suspected shortcuts still
   escalate. S-stop is reserved for an unverifiable contract, oracle,
   generator, checker, protocol, or numeric foundation.
5. **Freeze the contract and establish the canonical source.** A fresh review
   keeps an active verified blind source as the safety root while explicitly
   reviewing adoption of the selected standard route. A separate stage
   materializes `package/std.cpp` from that selected source and records
   exact-copy or reviewed semantic deltas. Another fresh stage reviews that
   exact hash. A
   stage cannot pass by reusing an old file or merely returning a success
   message; its execution receipt binds the prompt, prerequisites, newly
   produced outputs, logs, model, and terminal Codex event.
6. **Build the executable package.** Produce a distinct oracle/brute,
   validator, optional checker, reproducible generators, canonical public
   samples, release tests and answers. All generated/release inputs must pass
   the validator. Private blind work, audits, and wrong-source code may not be
   copied into `package/`.
7. **Differentiate and attack.** Exhaust tiny cases or complete at least 5,000
   consecutive seeded comparisons in a production run. Generate 10–15 wrong
   candidates, retain the profile quota of plausible distinct implementations,
   strengthen each retained route to its strongest natural still-wrong form,
   and reject obvious dominated variants. Require each qualified route to
   compile, pass every canonical sample plus machine-run small/random/structured
   survivability cases, then receive its expected WA/TLE/MLE/OLE/RE on a package
   breaker.
   For every adversarial round, the machine runner executes routes through the
   production backend and writes an append-only hash-linked receipt; a Markdown
   rounds table cannot prove a round happened.
8. **Bind coverage and replay the full regression.** Keep a compact private
   matrix from route-risk/proof/resource obligations through purposeful
   families to every concrete release input, generator/fixed provenance, seed
   or case parameters, scale axes, and important interactions. The completion
   gate checks that mapping and runs the canonical
   regression executor itself. It compiles current sources, runs validation,
   oracle comparison, samples, release tests, wrong routes and privacy checks,
   verifies actual upper-limit tags, and binds every consumed file by hash.
   Known accepted alternatives run over every release test; a custom checker
   must either accept a token-distinct non-jury witness or carry the explicit
   no-known-alternative audit waiver.
   Prose such as `status: passed` is never accepted without this machine
   receipt.
9. **Perform independent readiness review.** Only a fresh exact-model
   readiness stage may write `audit/readiness.md`. `go` requires current blind,
   stage-execution, std provenance, adversarial-round, regression and completion
   receipts, no survivor/blocker, no stale hash and no private-material leak.
   Otherwise the verdict is `hold` or `escalate`, with the owning failed stage.

`go` is deliberately a technical package/data verdict. It does not certify the
problem's novelty, interest, contestant difficulty, contest slot, or set-level
suitability; perform that quality review upstream.

## Main commands

Run commands from the builder skill directory or use their absolute paths.
`PROBLEM` below is an absolute problem directory.

Before a production judging gate, install CPIdeas Plus in the active Python
environment or expose its `src` directory, then start and check its matching
LightCPVerifier service. Use a Python version supported by that checkout (the
current CPIdeas Plus project requires Python 3.13 or newer). For a source
checkout:

```bash
export CPIDEAS_ROOT=/absolute/path/to/CPIdeas-Plus
export PYTHONPATH="$CPIDEAS_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
"$CPIDEAS_ROOT/scripts/lightcpverifier-docker.sh" start
curl -fsS http://127.0.0.1:8081/health
```

The health JSON must contain `"ok": true`, the matching API/compiler revisions,
SHA-256 build/image identities, and the expected execution policy. The helper
automatically rebuilds a stale vendored image and injects its actual Docker
image ID. This check is a prerequisite, not a replacement for a real
service-backed regression.

```bash
python3 scripts/verify_statement_resources.py \
  --problem-dir "$PROBLEM"

python3 scripts/build_sweep.py \
  --problem-dir "$PROBLEM" --model gpt-5.6-sol \
  --neutral-count 2 --deceptive-count 2 --dry-run

python3 scripts/run_sweep.py \
  --problem-dir "$PROBLEM" \
  --plan blind-solves/icpc-light/sweep-plan.json \
  --public-manifest blind-solves/icpc-light/public-manifest.json

python3 scripts/verify_blind_stage.py --problem-dir "$PROBLEM"
python3 scripts/verify_preclassification.py --problem-dir "$PROBLEM"
python3 scripts/verify_solution_handoff.py --problem-dir "$PROBLEM"
python3 scripts/run_regression_gate.py --problem-dir "$PROBLEM"
python3 scripts/verify_completion_handoff.py --problem-dir "$PROBLEM"
python3 scripts/verify_readiness.py --problem-dir "$PROBLEM"
```

## ver3 judge execution backend

`run_regression_gate.py` now keeps the regression plan,
AC/WA/TLE/MLE/OLE/RE/INFRA
classification, artifact bindings, and receipt hashes in this bundle while
delegating program execution to a Program × Dataset backend. Production uses
the sandboxed LightCPVerifier backend by default. It requires a compatible
`CPIdeas-Plus` Python package on `PYTHONPATH` (or installed in the active
environment), including `cpideas_plus.evaluation.dataset`, and a healthy
LightCPVerifier service at
`http://127.0.0.1:8081`. Override the URL with
`--lightcpverifier-url` or `ICPC_LIGHT_LIGHTCPVERIFIER_URL`.

The backend sends generator, validator, std, brute, checker, release, and wrong
solution inputs as ordered datasets. CPIdeas splits requests into deterministic
batches of at most 128 and verifies the returned index/ID order. The regression
receipt still records only the earliest sequential failure and retains the same
ordered input/case hash algorithms as ver2.

In addition, `execution_backend_evidence` binds every dataset invocation to the
program/source hash, requested case-ID sequence, CPIdeas evaluation status,
actual chunk ranges, CPU/wall/memory/output limits, batch output budget, and
result hashes. Completion recomputes those bindings and rejects incomplete,
invalid-data, validator-error, infrastructure, truncation, order, or policy
evidence even if a raw process exit code looks successful. The receipt also
binds the dataset API revision, imported CPIdeas module hashes, adapter hash,
and LightCPVerifier build/image/compiler/go-judge identity.

Production reads TL/ML from the current statement-bound resource policy, sends
those exact values to LightCPVerifier, and requires the service to attest that
it applied them unchanged. Thus TLE/MLE are sandbox verdicts at the problem's
limits, not post-classification against a fixed 2-second/1024-MiB profile. The
120-second requested orchestration timeout remains separate from the service's
fixed C++ compilation policy (10-second CPU, 512 MiB, 50 processes).

The current Light transport fixes each stdout/stderr stream cap at 16 MiB; it
does not yet derive a problem-specific output limit from the statement. A task
whose legal intended output can approach that cap is outside the automatic
profile and must escalate instead of treating an OLE as valid contestant
evidence.

The regression plan is schema v3. Generate its private `resource_policy` with
`scripts/build_resource_policy.py`, supplying intended complexity, maximum
scale, and TL/ML rationales; do not hand-calculate the hashes. The machine
receipt reports ordered per-case results and peak observed time/memory so the
build agent can read the judge feedback before accepting or revising data.

There is no automatic host-process fallback. The old local subprocess backend
is available only for explicit compatibility tests:

```bash
python3 scripts/run_regression_gate.py \
  --problem-dir "$PROBLEM" \
  --test-mode \
  --execution-backend local \
  --min-random-count 1
```

Such a test-mode receipt cannot certify production readiness.

One HTTP batch prepares a source once. A dataset larger than 128 points uses
multiple requests and normally reuses the source-keyed program cache. The
current service has no durable prepared-program handle across requests, so a
cache eviction or service restart may recompile the same hash; this affects
performance, not the bound source or judging result.

Each role is compiled as one translation unit without copying any other
problem-owned C/C++ source or header into its compiler sandbox; only the fixed
image-owned `/lib/testlib` include is available. The service starts go-judge in
no-fallback mode and reports unhealthy if that sandbox is unavailable. These
changes do not add files to or rearrange the generated `package/` tree.

The adversarial-round recorder also defaults to LightCPVerifier, and the chain
verifier requires production sandbox backend evidence. Production round plans
support `tokens`, `exact`, and source-bound `checker` comparison. For the last
mode, bind top-level `checker_source` to exactly `package/checker.cpp`; it is
compiled as an isolated role and receives only the input, candidate output, and
answer copy-ins for that invocation. A legacy arbitrary `checker_command` is
accepted only by the explicit local test backend and cannot certify readiness.

Concrete-std materialization and solution handoff checks use LightCPVerifier
compile-only evidence as well. They accept `--lightcpverifier-url`; set
`ICPC_LIGHT_LIGHTCPVERIFIER_URL` for a non-default endpoint when running
`verify_completion_handoff.py` (or its underlying `verify_completion.py`), whose
internal regression replay has no separate URL flag.

No live Docker-backed end-to-end claim is implied by the bundled tests or
release metadata. Run the production command against the target service before
relying on its receipt. See [MIGRATION.md](MIGRATION.md) for dependency setup,
receipt fields, failure behavior, and rollback guidance.

The stage-agent and blind-review command details, prompt contracts, retry
commands, receipt schemas, and escalation rules live under
`skills/icpc-light-problem-builder/references/`. The stable preclassifier
interface can be checked independently with:

```bash
python3 skills/grade-test-data-buildability/scripts/validate_report.py --self-test
```
