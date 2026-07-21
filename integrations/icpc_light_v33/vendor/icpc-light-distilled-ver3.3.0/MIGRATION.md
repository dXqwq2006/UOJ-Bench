# Migrating to ver3

ver3 changes the execution trust boundary, not the ICPC Light judging policy.
The canonical regression gate still decides what must be compiled, which data
must be run, how outputs are judged, which wrong routes qualify, and what gets
hash-bound. LightCPVerifier now supplies the bottom layer that compiles and
executes submitted C++ programs in a sandbox.

## From bundle 3.2 to 3.3

Install 3.3 in a separate directory and select it as `v3.3.0` in the
production launcher. Keep the 3.2 bundle and historical results unchanged for
rollback and audit; changing only a `VERSION` label is not a migration.

Preclassification must now freshly create
`audit/private/selected-standard-route.cpp`. With `scam_status: none` or
`suspected`, this file must be byte-identical to one active independently
verified blind source. With `scam_status: confirmed`, it contains the
independently proved and executable simpler solution, while the report uses a
non-provisional continuing P1/P2/P3 grade and `stop_reason: none`. A suspected
shortcut remains provisional P3/escalate. S-stop is valid only for a concrete
unverifiable foundation and uses `scam_status: none`.

The solution draft retains its verified blind safety-root frontmatter and adds
a substantive `## Standard Route Adoption` section. Materialization and the
fresh solution review bind their standard provenance to the fixed selected
source. Regenerate preclassification, draft, materialization, solution review,
build, adversarial, completion, and readiness receipts; no 3.2 receipt after
preclassification is reusable.

The companion launcher also treats an exactly attested 8 GiB blind-container
OOM as a solver failure. This does not raise the memory limit or soften any
skill gate. Ambiguous exits, missing OOM markers, failed copy-out, or failed
cleanup remain infrastructure failures.

## From bundle 3.1 to 3.2

Bundle 3.2 keeps the generated `package/` layout unchanged but upgrades the
private regression plan from schema v2 to v3. For each qualified wrong route,
add at least one `small`, `random`, and `structured` entry under
`survivability_inputs`; all are machine-run and must AC independently of the
route's breaker. Add `audit/coverage-matrix.json` and the strengthening,
trivial-dominator, and survivability columns required by
`audit/wrong-solutions.md`.

Put known correct alternative implementations below
`audit/private/accepted-solutions/` and list them in `accepted_alternatives` so
the regression gate executes each on every release input. A normalized
preprocessing-token clone of `std.cpp` no longer qualifies; this is a
trivial-clone heuristic, not an algorithmic-independence proof. For a custom
checker, the alternatives
must each add a concrete `independence_basis` and collectively demonstrate at
least one accepted release output whose token
sequence differs from the jury answer. A custom-checker problem with no known
alternative must instead provide the explicit audited
`accepted_alternative_waiver`; the waiver is not available for ordinary exact
comparison. Production adversarial-round plans may now bind
`checker_source: package/checker.cpp`; arbitrary `checker_command` remains
local-test-only.

Regenerate the adversarial-round chain, regression receipt, completion receipt,
and readiness evidence. Do not copy old `passed` fields into the new artifacts.

## What stays compatible

- Keep the existing problem directory and `audit/regression-plan.json`.
- Keep the canonical sample manifest, release tests, wrong-route sources,
  checker exit-code contract, and package layout. Package admissibility is
  stricter in 3.2: hidden/development/temporary files, common credential forms,
  operator-local paths, and private-workflow names in metadata-bearing text now
  fail the privacy scan. Re-scan an old package rather than assuming its prior
  privacy result remains valid.
- Upgrade `audit/regression-plan.json` to schema version 3 by adding the
  statement-bound private `resource_policy` and the 3.2 private audit fields;
  use
  `scripts/build_resource_policy.py` rather than hand-calculating hashes.
- Keep the 5,000-case production minimum, the same ordered case/input hash
  algorithms, with exact AC/WA/TLE/MLE/OLE/RE/INFRA verdict preservation.
- Keep ver2 installed separately. Migration does not modify or invalidate the
  ver2 bundle itself.

ver2 machine receipts are historical evidence only. They lack the production
backend provenance required by ver3. Start from a separate ver3 problem working
copy, regenerate its adversarial-round receipt chain, rerun the canonical
regression, and regenerate `audit/completion-gate.json` and readiness evidence.
Do not overwrite or relabel the ver2 receipts.

## New production dependencies

The ver3 scripts import the CPIdeas Plus Python package at runtime. Either
install that package in the active Python environment or expose its `src`
directory on `PYTHONPATH`. The selected checkout must include
`cpideas_plus.evaluation.dataset`. Use a Python version supported by that
checkout; the current CPIdeas Plus project declares Python 3.13 or newer.

One source-checkout setup is:

```bash
export CPIDEAS_ROOT=/absolute/path/to/CPIdeas-Plus
export PYTHONPATH="$CPIDEAS_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
```

Start the matching vendored LightCPVerifier service from that checkout:

```bash
cd "$CPIDEAS_ROOT"
scripts/lightcpverifier-docker.sh start
curl -fsS http://127.0.0.1:8081/health
```

The health payload must be a JSON object with `"ok": true` and the matching
`service.apiRevision`, `compilerProfile`, SHA-256-shaped `buildId` / `imageId`,
and execution policy. Use the root helper above: it hashes the vendored build
inputs, rebuilds when the image label is stale, and injects the actual Docker
image ID. Direct Compose defaults are deliberately unattested and production
ver3 rejects them. A compatible health response is still only a prerequisite,
not proof that a full regression will pass.

Each production receipt records the dataset API revision, hashes of the exact
imported CPIdeas evaluation modules, the ver3 adapter hash, full service
identity/policy, and hash-bound per-invocation chunk evidence. The bundle's
`RELEASE.json` and `MANIFEST.sha256` additionally identify the release-time
checkout. These identities detect accidental stale or mismatched deployments;
they do not make a hostile remote service or Docker host trustworthy. The
operator remains responsible for controlling the configured endpoint.

The default service URL is `http://127.0.0.1:8081`. Regression, adversarial,
and concrete-std handoff commands expose `--lightcpverifier-url URL`; all of
them also read `ICPC_LIGHT_LIGHTCPVERIFIER_URL`. Use the environment variable
when calling `verify_completion_handoff.py` (or the underlying
`verify_completion.py`), because completion launches the regression gate
internally. An explicit command-line option takes precedence where available.

## Resource-limit semantics

The statement is the production source of truth. Do not substitute a bundle
default for these values:

| fact | production ver3 value | meaning |
| --- | ---: | --- |
| requested/verdict program limit | explicit statement TL (100–30000 ms) | exact sandbox CPU limit used to classify TLE |
| effective sandbox CPU limit | same statement TL | must be echoed unchanged by LightCPVerifier |
| effective sandbox wall limit | twice the statement TL | go-judge wall guard reported by the service |
| requested/effective memory limit | explicit statement ML (16–2048 MiB) | exact sandbox memory limit used to classify MLE |
| effective output limit | 16 MiB per stream | truncation is incomplete infrastructure evidence, never an accepted comparison |
| effective batch output budget | 32 MiB per request | actual value is returned by the service and bound per chunk |
| C++ compilation policy | 10 seconds CPU / 512 MiB / 50 processes | fixed service policy, independent of the requested 120-second orchestration timeout |

The requested and effective TL/ML must match exactly. Out-of-range requests,
service-side clamping, missing limit echoes, or any mismatch fail as
infrastructure errors. The regression receipt records the values separately
under `configuration` and `configuration.execution_backend` so completion can
recheck that equality.

The Program × Dataset adapter preserves test order and splits a large dataset
into deterministic service requests of at most 128 cases. Every chunk records
the actual effective limits, wall limit, captured-output count, batch budget,
range, status, and truncation flag; policy drift fails closed. The service may
reuse its program cache between chunks, but cache reuse does not weaken source
hash binding or per-case result validation. It is a performance cache rather
than a durable prepared-program handle, so eviction can cause recompilation.

The current custom-test adapter transports source, stdin, stdout/stderr, and
copy-in files as UTF-8 text. ver3 fails explicitly on non-UTF-8 program input
or files. This is appropriate for ordinary ICPC text protocols; binary or
communication protocols remain outside this adapter's current scope.

## Run the migrated regression

From `skills/icpc-light-problem-builder/`, with `PROBLEM` set to the absolute
problem directory:

```bash
python3 scripts/verify_statement_resources.py --problem-dir "$PROBLEM"

python3 scripts/build_resource_policy.py \
  --problem-dir "$PROBLEM" \
  --intended-complexity 'O(n log n)' \
  --maximum-scale 'n = 200000' \
  --time-limit-rationale 'measured full-scale margin' \
  --memory-limit-rationale 'measured peak below ML'

python3 scripts/run_regression_gate.py --problem-dir "$PROBLEM"
```

Production defaults to `--execution-backend lightcpverifier`. The command
fails if CPIdeas Plus cannot be imported, the service is unhealthy, transport
or batch ordering fails, or compilation/execution evidence is incomplete.
There is no automatic local fallback.

For an explicit compatibility smoke test only:

```bash
python3 scripts/run_regression_gate.py \
  --problem-dir "$PROBLEM" \
  --test-mode \
  --execution-backend local \
  --min-random-count 1
```

This route runs host subprocesses and produces a non-production receipt. It
cannot satisfy `verify_completion.py` or certify readiness. Never remove
`--test-mode`, relabel the receipt, or copy its results into a production
receipt.

## Rebuild adversarial-round evidence

`record_adversarial_round.py` now defaults to LightCPVerifier too. For each
round, run the existing production command without backend flags:

```bash
python3 scripts/record_adversarial_round.py \
  --problem-dir "$PROBLEM" \
  --plan audit/adversarial-round-plans/round-01.json
```

The recorder compiles each wrong route and runs its breaker through the
backend, then writes `execution_mode: production`, `production: true`, and the
backend configuration into the append-only receipt. The chain verifier requires
`lightcpverifier`, `sandboxed: true`, and `testing_only: false`. A production
round cannot extend a ver2 or test-mode previous receipt, so rebuild the ver3
chain from round 1 in its own problem working copy.

A ver2 adversarial test with `"comparison": "checker"` may name an arbitrary
host `checker_command`; that command is not production evidence. In bundle 3.2,
replace it with top-level `"checker_source": "package/checker.cpp"`, remove the
per-test command, and regenerate the chain from round 1. The recorder compiles
the checker as an isolated role and runs it in the same sandbox with explicit
input/candidate/answer copy-ins. The legacy command works only under
`--test-mode --execution-backend local`, whose receipt the chain verifier
rejects.

## Inspect the new evidence

After the production regression passes, check
`audit/regression-machine.json` for at least:

```json
{
  "execution_mode": "production",
  "production": true,
  "status": "passed",
  "configuration": {
    "execution_backend": {
      "name": "lightcpverifier",
      "sandboxed": true,
      "testing_only": false,
      "dataset_batch_size": 128,
      "dataset_api_revision": "cpideas-program-dataset-v1",
      "execution_evidence_schema_version": 1,
      "service_identity": {
        "apiRevision": "cpideas-custom-test-batch-v3",
        "compilerProfile": "gnu++17-O2-pipe-online-judge-I-dot-package-testlib-v4"
      }
    },
    "verdict_time_limit_seconds": 3.5,
    "sandbox_effective_time_limit_seconds": 3.5,
    "sandbox_effective_memory_limit_mb": 512
  }
}
```

The numeric values above illustrate a statement declaring 3.5 seconds and
512 MiB; real receipts must equal their own statement. Also inspect top-level
`resource_policy` and `execution_backend_evidence`: their statement/design
binding, invocation count/hash,
program and case-ID bindings, compact evaluation receipts, chunks and per-case
result bindings must pass completion validation. Treat the numbers above as
the current CPIdeas profile, not permission to trust an arbitrary remote
service merely because it reports the same values.

After rebuilding and verifying the adversarial chain, launch readiness through
the stage runner. Its trusted handoff performs the authoritative completion
replay and refreshes both the canonical regression and completion receipts
before the readiness agent starts. For a standalone handoff check, run:

```bash
python3 scripts/verify_completion_handoff.py --problem-dir "$PROBLEM"
```

The completion verifier reruns the canonical regression and requires current
hash-bound LightCPVerifier compilation evidence. Compilation is role-isolated:
the submitted translation unit cannot receive other problem-owned C/C++
sources/headers, while fixed testlib comes from `/lib/testlib`. A failed or
interrupted rerun invalidates an old passing receipt.

After the readiness stage has produced its current execution receipt and
`audit/readiness.md`, run `verify_readiness.py`. It verifies that the readiness
stage consumed the exact completion receipt refreshed by the trusted handoff;
do not hand-author or replace that receipt between the two gates.

## Rollback and comparison

Rollback means selecting the untouched ver2 bundle, not changing ver3 to
silently use the local backend. Keep separate output/problem copies when
comparing versions because both workflows write canonical files under the
problem's `audit/` directory. Record the bundle version and regression receipt
hash with every comparison.

If production LightCPVerifier is temporarily unavailable, preserve the failed
receipt and service diagnostics, restore the service, and rerun ver3. Use the
local backend only to diagnose workflow compatibility; it is not a production
substitute.
