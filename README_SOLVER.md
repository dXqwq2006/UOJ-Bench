# Solver abstraction

This fork keeps UOJ-Bench's datasets, prompts, UOJ client, patch application, and
scoring rules pinned to upstream commit `ce1c006`. It replaces only the step that
turns a task into a candidate artifact.

See [docs/BENCHMARK_MATRIX.md](docs/BENCHMARK_MATRIX.md) for the current
benchmark, task, solver/competitor, evaluator, and upstream-source inventory.

```text
UOJ-Bench task -> Solver session -> typed candidate -> original UOJ evaluation
```

`solution/api.py` defines six entry points:

```python
solver.start_generation(task).next()
solver.start_hacking(task).next(feedback)
solver.start_repair(task).next(feedback)
solver.start_fault_coverage(task).next()
solver.start_fault_exposure(task).next()
```
solver.start_test_package(task).next()

Agent runners record feedback as soon as it is produced, then request the next
turn. Passing feedback directly to `next()` remains supported for custom use.

Benchmark runners only construct task inputs, call these entry points, and
evaluate typed candidates. They do not import a model client or a concrete
solver.

## Pipeline directories

Each immediate subdirectory of `solution/` is one solver pipeline. It exports a
single factory from `__init__.py`:

```python
def build_solver(model: str) -> Solver:
    ...
```

`solution/prompt/` owns the complete baseline policy: the original prompt text,
`PromptSolver`, the official fence parser, and retry-context rendering.
`solution/llm/` owns the shared TATU/OpenRouter transports so prompt-compatible
pipelines can compare policies under identical model settings. A pipeline may
still supply its own model stack. Benchmark task inputs contain only raw problem
data and public metadata. New pipelines need no central registry entry; the CLI
imports the directory by name and passes `--model` to its factory.

```bash
python -m scripts.test_hack_agent \
  --solver prompt \
  --model gpt-5.5 \
  --hack_idx 0 \
  --max_trials 5
```

The original five task CLIs accept `--solver` and default to `prompt`. A custom pipeline
can use any model stack without adding model code to `utils/` or the runners.

## ICPC Light v3.3 bridge

`solution/icpc_light_v33_bridge/` connects the frozen ICPC Light v3.3 skills
pipeline through a separate JSON process boundary. It supports one-shot UOJ
Generation and Hacking, TestCase-Eval Task 2 Fault Exposure, and public-only
Fault Coverage for TestCase-Eval Task 1 and CodeContests+ Verified. Its
statement-only Test Package entry runs the full ICPC Light workflow and returns
the ordered release package, capped at 50 inputs. It requires the exact model
contract `gpt-5.6-sol` with reasoning effort `xhigh`, an explicit port from the
vendored upstream `ultra` setting, and fails closed for Repair and feedback rounds. The bridge config pins
the complete skill tree hash and records a hash-bound pipeline identity with
every candidate; TestCase-Eval result databases also bind its stable pipeline
signature before persisting a completed generation. The shared offline
fault-coverage loop applies the same binding to CodeContests+ results.

Run the credential-free integration smoke from the repository root:

```bash
env -u UOJ_API_KEY -u TATU_API_KEY -u OPENAI_API_KEY \
  PYTHONDONTWRITEBYTECODE=1 \
  python -m scripts.smoke_icpc_light_v33_bridge \
  --uoj-root "$PWD" \
  --output-root /absolute/system-filesystem/root/icpc-light-v33-smoke
```

This deterministic test executes the real v3.3 sweep/review scripts, the native
UOJ Hacking rollout runner, typed Fault Coverage/Fault Exposure jobs, and an
ordered statement-only package job with injected workers. It makes no model or
UOJ request and is not a benchmark score. See
[the integration guide](docs/ICPC_LIGHT_V33_BRIDGE.zh-CN.md) and
[zero-mount server handoff](docs/ICPC_LIGHT_V33_ZERO_MOUNT_HANDOFF.zh-CN.md)
before configuring a production agent command.

Direct tasks request one turn. The hacking and repair agent scripts keep their
UOJ-owned trial loops and pass parser, local validation, and judge rejection
feedback back to the session. Correct reference code and hidden error labels are
never included in solver metadata.

## Prompt baseline configuration

The upstream `gpt-oss-120b` OpenRouter path remains available. These model names
use TATU's native protocols:

- `gemini-3.1-pro-preview`
- `gpt-5.5`
- `gpt-5.6-sol`
- `claude-fable-5`

Set `TATU_API_KEY`; optionally set `TATU_BASE_URL`, `TATU_TEMPERATURE`,
`TATU_MAX_OUTPUT_TOKENS`, or `TATU_TIMEOUT_SECONDS`. OpenAI-protocol models also
accept `TATU_REASONING_EFFORT`; use `xhigh` for formal `gpt-5.6-sol` runs and
label their configuration accordingly. The adapter also normalizes the legacy
value `max` to the transmitted value `xhigh` and uses `max_completion_tokens`.
The normalized response records both the requested and transmitted settings.

TATU's discounted Coding deployer uses the Responses API rather than the
ordinary chat-completions route. Configure all of its routing fields together:

```bash
export TATU_OPENAI_TRANSPORT=responses
export TATU_BASE_URL=https://maas.tatucloud.com/deployer/coding_tatu/v1
export TATU_DEPLOYER=CODING_TATU
export TATU_REASONING_EFFORT=xhigh
export TATU_TIMEOUT_SECONDS=1200
```

This sends `gpt-5.6-sol@CODING_TATU` to `POST <base>/responses`, records the
effective route in `request_config`, and preserves provider-native Responses
output items between agent rounds. Treat this as a distinct deployment when
comparing or resuming runs; do not mix its records into a chat-completions
result directory. Confirm the discounted rate in TATU's billing records.

Gemini routing also honors `TATU_DEPLOYER`. For the Google caller, set
`TATU_DEPLOYER=GOOGLE`; the adapter sends
`gemini-3.1-pro-preview@GOOGLE` through the native `generateContent` endpoint
and records the effective route in `request_config`.

TATU generation POSTs are not retried inside the call adapter. This keeps one
recorded model turn equal to one potentially billable request. The Agent runners
retain upstream's outer-round exception and trial behavior.

To fill the UOJ Hacking rollout queue while UOJ itself is unavailable, persist
the first agent turn separately:

```bash
python -m scripts.run_hack_rollout_batch \
  --split all \
  --solver prompt \
  --model gemini-3.1-pro-preview \
  --workers 128 \
  --dataset-dir dataset \
  --result-dir /path/to/result
```

The runner never calls UOJ. It writes each raw response, parsed generator,
provider-native transcript, usage, and non-secret request configuration before
moving to the next sample, and `--resume` retries only missing or request-error
records. `--seed-agent-result-dir` imports first turns from a compatible
completed agent run. This is intentionally round one only: every later Hacking
turn depends on the previous UOJ response and cannot be generated ahead without
changing benchmark semantics.

Set `GPT_OSS_BASE_URL` to route `gpt-oss-120b` to a local OpenAI-compatible
server instead of OpenRouter. `GPT_OSS_API_KEY` is optional and defaults to
`local`; `GPT_OSS_MAX_OUTPUT_TOKENS` defaults to 65536.

## TestCase-Eval

Task 1 has two independent solver directories. `testcase_eval_task1_cot` is the
paper baseline; `testcase_eval_task1_direct` is the direct-output prompt from
the separately published Task1-DO snapshot. Both use their published strict
regex extractor and never fall back to another model. The older
`testcase_eval` policy remains available for Task 2 Fault Exposure, including
its fixed `gpt-4.1-mini` extraction fallback. Data revisions, prompt snapshots,
oracle consensus, comparator, and scoring are pinned to TestCase-Eval commit
`45275c6`.

The reproduction is offline after dataset download. `--dataset-snapshot-root`
accepts an HF Hub cache root and verifies the six parquet SHA-256 values before
loading them. It runs generated inputs
against the Codeforces submissions in a network-disabled, non-root Docker
container and never calls UOJ. SQLite stores every prompt, raw response,
candidate, usage record, materialized input, and execution result with stable
resume keys. Request failures are recorded and are retried only with
`--retry-errors`.

`icpc_light_v33_bridge` is an additional non-paper Task 2 competitor. After
preparing its bridge config as described below, run it through the same native
TestCase-Eval generation and judge phases:

```bash
export ICPC_LIGHT_UOJ_BRIDGE="$PWD/integrations/icpc_light_v33/bin/icpc-light-uoj-bridge"
export ICPC_LIGHT_UOJ_BRIDGE_CONFIG=/absolute/control/bridge-config.json
python -m scripts.test_testcase_eval_task2 --phase generate \
  --result-dir "$RESULT" --model gpt-5.6-sol --workers 16 \
  --policy icpc_light_v33_bridge
```

Do not pass `--paper` to that competitor run: the solver intentionally uses the
ICPC Light prompt/pipeline, while Task 2's dataset, candidate materialization,
local evaluator, and score remain unchanged.

For a two-problem GPT-5.6 Task 1 smoke using both published prompts:

```bash
export TATU_API_KEY=...
export TATU_OPENAI_TRANSPORT=responses
export TATU_BASE_URL=https://maas.tatucloud.com/deployer/coding_tatu/v1
export TATU_DEPLOYER=CODING_TATU
export TATU_REASONING_EFFORT=xhigh
export TATU_MAX_OUTPUT_TOKENS=18000
export TATU_TEMPERATURE=1.0
export TATU_TIMEOUT_SECONDS=1200
export TESTCASE_EVAL_EXTRACTOR_API_KEY=...
export TESTCASE_EVAL_EXTRACTOR_BASE_URL=https://api.openai.com/v1

RESULT=results/testcase-eval-task1/gpt-5.6-sol-smoke
python -m scripts.test_testcase_eval_task1 --phase prepare \
  --result-dir "$RESULT" --problem-id 2000D --problem-id 2005E1 \
  --dataset-snapshot-root "$HF_HOME/hub"
python -m scripts.test_testcase_eval_task1 --phase preflight \
  --result-dir "$RESULT" --model gpt-5.6-sol --paper
python -m scripts.test_testcase_eval_task1 --phase generate \
  --result-dir "$RESULT" --model gpt-5.6-sol --paper --workers 16
python -m scripts.test_testcase_eval_task1 --phase judge \
  --result-dir "$RESULT" --workers 64 --judge-backend lightcp
python -m scripts.test_testcase_eval_task1 --phase stats \
  --result-dir "$RESULT"
```

The judge phase defaults to the pinned container evaluator. A running
LightCPVerifier with the `testcase-eval` profile can replace only that layer:

```bash
python -m scripts.run_testcase_eval_batch --phase judge \
  --result-dir "$RESULT" --workers 64 --judge-backend lightcp \
  --lightcp-url http://127.0.0.1:8082
```

For a full run, start the LightCP container with at least `--shm-size=8g`;
go-judge stores cached programs in `/dev/shm`, and Docker's 64 MiB default is
insufficient.

The selected judge backend and its toolchain fingerprint are stored in the result
manifest. Use a fresh result directory when changing evaluator builds so execution
rows cannot be mixed.

The extractor endpoint must support `POST /responses`, structured JSON output,
and the exact `gpt-4.1-mini` model. Preflight fails closed when that model is
unavailable; do not substitute another extractor in a paper-labeled run.

This smoke makes 80 main-model calls and 21,520 submission executions per
model. Full Task 1 is 10,000 calls and 2,427,720 executions for one policy, or
20,000 calls and 4,855,440 executions for CoT plus Direct. Use a fresh result
directory for each model and evaluator fingerprint.

## CodeContests+ Verified

`scripts.run_codecontests_plus` adapts the pinned public CodeContests+ `1x`
split to the same fault-coverage interface as TestCase-Eval task 1. It selects
rows whose published true-positive and true-negative rates are both at least
`0.9`, then gives each problem 20 independent generations. The dataset's
validator decides whether a generated input is legal. A published correct
submission produces the answer, and the dataset's checker judges every
sampled correct and incorrect submission. Following the paper's 100-per-class
cap, the adapter uses a reproducible SHA-256 ordering and samples at most 100
of each role. The resulting metrics are valid-input rate, true-positive rate,
and true-negative rate.

`icpc_light_v33_bridge` can run as a non-paper competitor without changing the
CodeContests+ dataset or evaluator. Its Fault Coverage request contains only
the public statement and allowlisted public metadata; no correct/incorrect
program, validator, checker, or oracle crosses the solver boundary. Each bridge
job returns one `TestCaseCandidate`, while this runner still controls the 20
independent generations and all downstream scoring.

```bash
RESULT=results/codecontests-plus/gpt-5.6-sol-smoke
python -m scripts.run_codecontests_plus --phase prepare --result-dir "$RESULT" \
  --smoke-problems 1
python -m scripts.run_codecontests_plus --phase audit --result-dir "$RESULT" --workers 32
python -m scripts.run_codecontests_plus --phase preflight --result-dir "$RESULT" \
  --model gpt-5.6-sol
python -m scripts.run_codecontests_plus --phase generate --result-dir "$RESULT" \
  --model gpt-5.6-sol --policy icpc_light_v33_bridge \
  --workers 16 --max-generations-per-problem 1
python -m scripts.run_codecontests_plus --phase judge --result-dir "$RESULT" --workers 64
python -m scripts.run_codecontests_plus --phase stats --result-dir "$RESULT"
python -m scripts.run_codecontests_plus --phase export --result-dir "$RESULT"
```

Do not label this competitor run `--paper`: the benchmark harness is unchanged,
but the ICPC Light pipeline is not the published TestCase-Eval Task 1 prompt.

The evaluator requires the `codecontests-plus` LightCP profile advertised by
`/health`. The adapter audits the sampled C++17, Python 2/3, and Java 21
programs before generation and excludes compilation failures from scoring.
The public `UNKNOWN` label mixes several languages and is excluded rather than
guessed. For an official run, omit `--smoke-problems` and
`--max-generations-per-problem`; the manifest records the dataset revision,
Verified thresholds, sample rule, selected row indices, compiler audit,
evaluator fingerprint, and generation budget. Repeated `--dataset-parquet`
arguments can pin downloaded `1x` shards by SHA-256.

For a deterministic uniform subset, use `--sample-problems 500`. Selection is
performed after the Verified filter by SHA-256 min-hash over each stable problem
key with the fixed `codecontests-plus-verified-v1` seed. The method, seed,
Verified population, sample size, and selected keys are stored in the manifest.
Use `--sample-seed` only when intentionally defining a different benchmark
subset.

On H100, unprivileged namespaces are disabled. The deployed fallback uses
`chroot`, UID/GID isolation, `no-new-privs`, `prlimit`, output caps, and wall
timeouts, and its identity is included in the evaluator fingerprint. It is
weaker than go-judge namespace isolation and must run only the public benchmark
sources. Use the Docker/go-judge evaluator on a host that permits namespaces
for an isolation-equivalent final run.

## Statement-only Test Package benchmark

`utils/test_package_benchmark.py` defines the shared experiment unit: a solver
receives a public problem statement and returns one ordered package. The final
package contains at most 50 inputs; overflow rejects the whole package. Hidden
accepted/wrong programs, validators, checkers, and execution assets remain in
the benchmark jury. Scoring reports valid rate, Coverage@1/5/10/20/50, and
whole-package union coverage. The package is mirrored into the existing jury
tables only after it passes the package contract, so TCE and CodeContests+
reuse their established execution backends.

The TestCase-Eval Task 1 baseline keeps its published behavior byte-for-byte:
20 independent calls, one input per call. `sync-native` aggregates those 20
slots after generation without changing a prompt or model request. The same
method on CodeContests+ is explicitly an adapter, not a native paper result.

ICPC Light and HardTestGen instead implement their native complex unit: one
pipeline invocation returns the complete ordered package. They are never
projected into 20 independent pseudo-calls, and the ICPC package is not sent to
UOJ Hacking or TestCase-Eval Task 2.

Use `scripts.run_test_packages` for native aggregation, ICPC generation, hidden
jury execution, and package statistics. Each competitor uses a fresh prepared
result directory.

## HardTestGen

`solution/hardtestgen/` ports the two-stage pipeline from LeiLiLab/HardTestGen
commit `0355315`. The first model call writes the Python input validator and
optional output-judging function. The second writes direct inputs plus
RPGen/SPGen and HackGen Python generators. Generated inputs are filtered by the
generated validator and stable-deduplicated in source order.

The paper implementation receives reference programs and uses them to create
and cross-check outputs. This benchmark adapter deliberately removes reference
programs from both prompt stages. It supplies only the statement, publishes
inputs without answer files, and delegates all validation and scoring to the
hidden TCE or CodeContests+ jury. The two prompt changes and hidden-jury
replacement are recorded in `test_package_contract.deltas`.

The complete variable-size suite is published as one ordered package with a
hard limit of 50 inputs. There is no category round-robin projection and no
padding with `ERROR` rows. If generation produces more than 50 inputs, the
whole package is marked `over_limit` and contributes no scoreable test.

Generated Python runs through the existing LightCP profiles because the H100
host cannot run upstream's `bwrap` setup. Raw model calls, kits, suites, package
calls, package tests, and jury executions are separately checkpointed.

Start with a fresh database prepared by either existing benchmark runner. For
CodeContests+, run its compile `audit` phase before this sequence.

```bash
export TATU_API_KEY=...
export TATU_TEMPERATURE=0.1
export TATU_MAX_OUTPUT_TOKENS=5120

RESULT=results/testcase-eval-hardtestgen/smoke
python -m scripts.test_paper_hardtestgen --phase preflight \
  --result-dir "$RESULT"
python -m scripts.test_paper_hardtestgen --phase generate-kits \
  --result-dir "$RESULT" --model gpt-5.6-sol --workers 16 --github-settings
python -m scripts.test_paper_hardtestgen --phase generate-suites \
  --result-dir "$RESULT" --workers 32
python -m scripts.test_paper_hardtestgen --phase judge \
  --result-dir "$RESULT" --workers 64
python -m scripts.test_paper_hardtestgen --phase stats \
  --result-dir "$RESULT"
```

`--retry-errors` retries failed kit calls or suite execution while preserving
completed checkpoints. Once hidden jury executions exist, the package is
immutable; use a fresh result directory to change it.

## Hacking batches

The batch runner uses the official Hacking Easy and Hard inputs. Easy is the
479 hackable entries from `sampled_large_submission_pairs.json`; Hard is all
1046 entries from `hacks.json`.

```bash
python -m scripts.run_hack_agent_batch \
  --split all --split-schedule interleaved \
  --solver prompt --model gpt-oss-120b \
  --max-trials 10 --workers 24 \
  --result-dir /path/to/results
```

Add `--smoke-per-split 5` for the deterministic 5 Easy + 5 Hard smoke set.
Each sample is written atomically with the complete transcript, model messages,
usage, and UOJ results. Re-run with the same arguments and `--resume` to skip
completed samples and retry interrupted ones. `summary.json` reports Pass@1
through Pass@10 for each split and problem difficulty.

UOJ `APIError` responses are infrastructure failures: agent runners do not turn
them into model feedback or consume a trial. Batch runs persist the sample as
`retryable_error`, and `--resume` evaluates it again when UOJ is available.

Use `--split-schedule interleaved` for full runs so a slow tail in one official
split does not prevent the other split from using available workers. The
default remains the upstream-style Easy-then-Hard order.

Paid runs accept `--budget-usd` and `--stop-at-usd`. The latter stops new work;
already-running workers still finish, so leave a guard band based on smoke-run
costs and worker count.

## Offline verification

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
UOJ_API_KEY=offline PYTHONDONTWRITEBYTECODE=1 \
  .venv/bin/python -m unittest discover -s tests -v
```

The boundary test compares the working tree with `ce1c006`: the dataset,
official README, patch helper, UOJ client, and all prompt strings must remain
unchanged. Differential tests execute the five upstream runners with fixed LLM
and UOJ tapes and require identical prompts, parsing, feedback histories,
submissions, and scores. Outside `solution/`, the fork contains only benchmark
runners, benchmark utilities, tests, and documentation.
