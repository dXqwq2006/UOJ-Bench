# Solver abstraction

This fork keeps UOJ-Bench's datasets, prompts, UOJ client, patch application, and
scoring rules pinned to upstream commit `ce1c006`. It replaces only the step that
turns a task into a candidate artifact.

```text
UOJ-Bench task -> Solver session -> typed candidate -> original UOJ evaluation
```

`solution/api.py` defines five entry points:

```python
solver.start_generation(task).next()
solver.start_hacking(task).next(feedback)
solver.start_repair(task).next(feedback)
solver.start_fault_coverage(task).next()
solver.start_fault_exposure(task).next()
```

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

All five task CLIs accept `--solver` and default to `prompt`. A custom pipeline
can use any model stack without adding model code to `utils/` or the runners.

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

TATU generation POSTs are not retried inside the call adapter. This keeps one
recorded model turn equal to one potentially billable request. The Agent runners
retain upstream's outer-round exception and trial behavior.

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

## TC-Bench

`scripts.run_tc_bench` adapts the pinned public TC-Bench snapshot to the same
fault-coverage solver interface. It validates 877 problems, 9,347 wrong
programs, 6,991 correct programs, and the dataset parquet SHA-256. Each problem
gets `5 * rank` independent generations. Generated inputs must produce one
consistent output across every accepted solution; invalid candidates remain in
the PassRate denominator. Valid candidates are then scored against all wrong
solutions using TC-Bench's whitespace and decimal comparator.

```bash
RESULT=results/tc-bench/gpt-5.6-sol-smoke
python -m scripts.run_tc_bench --phase prepare --result-dir "$RESULT" \
  --dataset-parquet /path/to/test-00000-of-00001.parquet \
  --row-index 0 --row-index 677 --row-index 792
python -m scripts.run_tc_bench --phase audit --result-dir "$RESULT" --workers 32
python -m scripts.run_tc_bench --phase preflight --result-dir "$RESULT" \
  --model gpt-5.6-sol --paper
python -m scripts.run_tc_bench --phase generate --result-dir "$RESULT" \
  --model gpt-5.6-sol --paper --workers 16 --max-generations-per-problem 2
python -m scripts.run_tc_bench --phase judge --result-dir "$RESULT" --workers 64
python -m scripts.run_tc_bench --phase stats --result-dir "$RESULT"
python -m scripts.run_tc_bench --phase export --result-dir "$RESULT"
```

The three-row smoke uses 6 calls and 200 program executions per model. A full
run uses 46,735 calls and 970,615 program executions per model. The public
parquet omits the original per-submission `compileAndRunOptions`; compilation
therefore audits the documented C++20/17/14/11 standards in a fixed order and
records the selected profile. Results must be labeled as a public-snapshot
adaptation, not an exact replay of the unpublished per-program configuration.

On H100, unprivileged namespaces are disabled. The deployed fallback uses
`chroot`, UID/GID isolation, `no-new-privs`, `prlimit`, output caps, and wall
timeouts, and its identity is included in the evaluator fingerprint. It is
weaker than go-judge namespace isolation and must run only the public benchmark
sources. Use the Docker/go-judge evaluator on a host that permits namespaces
for an isolation-equivalent final run.

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
