# Changelog

All notable bundle-level changes are documented here. The version in
[`VERSION`](VERSION) is the authoritative human-readable bundle version.

## 3.3.0 - 2026-07-21

### Changed

- Replaced the terminal `shortcut-confirmed` transition with an executable
  standard-route adoption transition. An independently proved correct simpler
  route is freshly written to the fixed
  `audit/private/selected-standard-route.cpp`, receives an ordinary P1/P2/P3
  data-buildability grade, and continues through package construction.
- Kept the independently verified blind solution as the safety-root
  provenance while binding draft review, materialization, solution review,
  completion, and readiness to the selected standard-route hash. `exact-copy`
  is now measured against that selected source.
- Reserved S-stop for concrete unverifiable foundations. A suspected shortcut
  remains provisional P3/escalate; a confirmed executable route is never an
  S-stop condition.
- Extended the native stage contract so the selected source is a required,
  freshly generated preclassification output and a hash-bound input to every
  downstream stage.
- Fixed build-hardening recursive receipt summaries so an optional accepted-
  solution tree cannot overwrite the current receipt path, and retained a
  bounded list of canonical completion issues in the outer handoff evidence.
- Preserved Codex JSONL terminal semantics: recoverable intermediate API error
  events do not fail a lane when the same turn ultimately completes and its
  required artifacts validate.
- Kept both blind-review and final blind-stage contamination parsing
  fail-closed while recognizing the explicit semantic Markdown forms
  `Contamination: No` and `Contamination status: uncontaminated` as equivalent
  to canonical `contamination_status: clean`. Missing, ambiguous, conflicting,
  `Yes`, and `contaminated` statuses still reject the lane, including conflicts
  later on the same field line.

### Runtime companion changes

- The production launcher recognizes `v3.3.0` as a distinct release while
  retaining historical `v3.2.0` support and its receipts.
- A fully attested 8 GiB blind-lane OOM with a nonzero container exit is a
  solver failure, not an infrastructure failure. Missing or inconsistent OOM,
  copy-out, or cleanup evidence still fails closed as infrastructure.
- Hidden evaluation now recursively snapshots `package/tests/` or
  `generated_tests/` through no-follow descriptor-relative traversal. It binds
  backend-safe test IDs to complete package-relative paths, preserves those
  paths in audit output, and rejects empty, ambiguous, unsafe, linked, special,
  over-limit, duplicate-identity, or concurrently mutated test trees.

### Compatibility

- Install 3.3 beside 3.2; do not relabel a 3.2 bundle or reuse its stage or
  completion receipts. Regenerate the entire selected-route and downstream
  receipt chain.
- The preclassification frontmatter remains schema v2. No new optional field
  spellings are introduced; the selected source has one fixed path instead.

## 3.2.0 - 2026-07-18

### Changed

- Restored ordinary-ICPC adversarial and test-data topic leaves for bitwise,
  games, invariants, optimization, permutations, deterministic search,
  simulation, and bounded P3 randomized/restart analysis, with explicit Light
  routing and escalation boundaries.
- Replaced the weak one-ordinary-case wrong-route bar with strongest-natural-
  variant, trivial-dominator, and machine-run small/random/structured
  survivability requirements.
- Added private `audit/coverage-matrix.json`: a compact machine-checked binding
  from route-risk/proof/resource obligations through families to concrete
  tests, generator/fixed provenance, seed/case parameters, scale axes, variant
  modes, and important combinations. The generated `package/` layout is
  unchanged.
- Upgraded the regression plan to schema v3. It executes accepted alternative
  implementations over every release test, rejects normalized preprocessing-
  token clones of `std.cpp`, binds a concrete implementation-independence
  basis, requires a
  checker-accepted non-jury output witness when an
  alternative is claimed for a custom-checker task, and supports an explicit
  audited no-known-alternative waiver otherwise.
- Added hash-bound `package/checker.cpp` support to production adversarial-round
  plans; arbitrary host `checker_command` remains testing-only.
- Clarified that `go` certifies technical package/data readiness rather than
  problem quality or contest placement, and made the fixed 16 MiB stream cap an
  explicit Light compatibility boundary.
- Made the build stage archive the optional private accepted-solution tree on
  every production retry, so stale alternatives cannot be inherited while its
  absence remains valid for tasks with no known alternative.
- Expanded build-stage ownership to canonical samples, the optional checker,
  and adversarial-round plans/receipts. Required outputs must be recreated;
  optional outputs are archived but may remain safely absent.
- Made the readiness handoff execute the canonical completion verifier instead
  of trusting a self-reported receipt, and made final watched-tree checks reject
  hidden entries added after completion.
- Tightened the package privacy gate for hidden/development/temporary files,
  common credential forms, operator-local paths, and private workflow names in
  metadata-bearing package text.

### Compatibility

- Existing schema-v2 regression plans and all dependent machine/completion/
  readiness receipts must be regenerated. Existing problem release files do
  not move; the new files and fields are private audit evidence only.
- The package directory layout remains compatible, but a package accepted by
  an older, narrower privacy check may now fail and must be cleaned/rechecked.
- The restored checks add bounded build/hardening work but do not restore OI
  subtasks, score bands, UOJ packaging, exhaustive route implementation,
  trajectory extraction, large multi-layer ledgers, or unbounded attack rounds.

## 3.1.0 - 2026-07-18

### Changed

- Added a fail-closed statement preflight before blind/stage artifacts: both TL
  and ML must be explicitly labelled, unambiguous, supported, and within
  100–30000 ms / 16–2048 MiB.
- Upgraded the private regression plan to schema v2 with a hash-bound resource
  design policy. Production judging forwards the statement TL/ML unchanged,
  requires matching service attestation, and preserves TLE/MLE/OLE/RE rather
  than using the prior fixed 2-second verdict and 5-second/1024-MiB sandbox
  split.
- Added ordered resource feedback to machine evidence, including per-result
  memory and role-level peak time/memory observations for the build agent to
  inspect.
- Isolated compilation by role: a submitted single translation unit receives
  no other problem-owned C/C++ source/header; fixed testlib is supplied only by
  the verifier image at `/lib/testlib`.
- Made the verifier fail closed around go-judge: `-no-fallback` is mandatory,
  readiness and execution are fixed to the same supervised local instance,
  and loss of either process makes the service unhealthy/exits the container.

### Compatibility

- The workflow stages and generated `package/` layout/payload contract are
  unchanged. Existing private regression plans/receipts must be regenerated;
  ver1 and ver2 bundles are not modified.
- No live Docker end-to-end result is claimed because the local Docker daemon
  was unavailable during this update; unit, syntax, and evidence-validation
  tests remain reproducible.

## 3.0.0 - 2026-07-17

### Changed

- Moved canonical regression program execution behind a backend-neutral
  Program × Dataset contract. The regression gate still owns plan validation,
  checker policy, AC/WA/TLE/RE/INFRA classification, artifact hashes, ordered
  case hashes, and the final machine receipt.
- Made the sandboxed LightCPVerifier backend the production default for
  `run_regression_gate.py`. It consumes CPIdeas Plus's dataset adapter and
  sends ordered datasets in deterministic requests of at most 128 cases.
- Added sandbox compilation evidence to the completion path. A production
  completion receipt must be backed by current, hash-bound LightCPVerifier
  compilation records; a host compiler result cannot certify ver3 readiness.
- Recorded the requested regression policy limits separately from the
  sandbox's effective limits. The production verdict threshold remains
  2 seconds, while the current CPIdeas generated-code profile enforces a
  5-second CPU limit, 10-second wall limit and 1024 MiB. Elapsed runtime above
  2 seconds is post-classified as TLE. The regression backend requests at most
  16 MiB per stdout/stderr stream and fails closed on truncated output.
- Added explicit execution-backend provenance to regression receipts and made
  the completion verifier require `lightcpverifier`, `sandboxed: true`, and
  `testing_only: false` for production evidence.
- Added a separately versioned execution-evidence section. Every Program ×
  Dataset invocation binds its program/source hash, case-ID sequence, compact
  CPIdeas evaluation receipt, actual chunk ranges and effective CPU/wall/
  memory/output limits, batch output budget, result hash, and invocation hash.
  Completion revalidates counts and hashes and rejects incomplete,
  infrastructure, invalid-data, validator-error, or truncated evidence.
- Bound production evidence to the exact CPIdeas dataset API revision and
  imported client-module hashes, plus LightCPVerifier API/compiler revisions,
  vendored build SHA-256, Docker image ID, go-judge/Node versions, and the
  service-reported runtime/compilation/batch policy. The matching Docker helper
  automatically rebuilds stale images and injects the actual image ID.
- Moved adversarial-round wrong-route compilation and execution to the same
  backend contract. The round-chain verifier now rejects non-production or
  non-LightCPVerifier round receipts.
- Moved concrete-std handoff compilation to LightCPVerifier compile-only
  evidence. The handoff commands accept the same service URL selection and do
  not fall back to a host compiler.

### Added

- `scripts/regression_backend.py`, the narrow adapter between the ICPC Light
  regression policy and CPIdeas Plus execution.
- An explicit local compatibility backend for tests. It is accepted only with
  both `--test-mode` and `--execution-backend local`, and its receipt is not
  production evidence.
- Dependency, migration, receipt, and rollback guidance in
  [`MIGRATION.md`](MIGRATION.md).

### Safety and compatibility

- There is no automatic fallback from LightCPVerifier to host subprocesses.
  Missing Python imports, an unhealthy service, transport failures, malformed
  batch results, and infrastructure checker failures fail closed.
- The regression-plan schema and the package/audit layout remain compatible,
  but ver2 regression/completion receipts do not contain the backend evidence
  required by ver3. ver2 adversarial receipts also lack the required backend
  provenance. Regenerate the adversarial chain, regression, and all dependent
  receipts in a separate ver3 working copy.
- `icpc-light-distilled-ver2` is unchanged and may be kept beside ver3 for
  rollback or result comparison. Do not overwrite ver2 artifacts in place.

### Known limitation

- The ver2 adversarial-round `comparison: checker` shape supplies an arbitrary
  `checker_command`, not a hash-bound checker source. ver3 rejects that mode in
  production because it cannot safely submit the checker to LightCPVerifier.
  Production round receipts currently support `tokens` and `exact` comparison.
  The legacy checker command remains available only with
  `--test-mode --execution-backend local` and cannot satisfy the round-chain
  verifier. Canonical regression custom checking through
  `package/checker.cpp` remains supported.
- The current CPIdeas custom-test transport is text-oriented. ver3 rejects
  non-UTF-8 source, stdin, or copied runtime/compile files instead of silently
  changing bytes. Ordinary ICPC textual input/output is unaffected.
- A batch request prepares each program exactly once. Datasets larger than one
  request are split into at most 128 cases per request and normally reuse the
  service program cache; the current API does not expose a durable prepared-
  program handle across chunks. A cache eviction or service restart can
  therefore recompile the same hash without changing judging semantics.
- Build/image fields prevent accidental stale or mismatched local deployments;
  they are not remote hardware attestation. The operator must still control
  and trust the configured LightCPVerifier endpoint and Docker host.

### Validation note

This bundle does not claim that a live Docker-backed end-to-end regression was
completed during release assembly. Unit and explicit local compatibility tests
do not replace one. Before relying on a production receipt, run the canonical
command against a healthy LightCPVerifier service in the target environment.
