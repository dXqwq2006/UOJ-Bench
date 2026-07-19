# Solver abstraction

This fork keeps UOJ-Bench's datasets, prompts, UOJ client, patch application, and
scoring rules pinned to upstream commit `ce1c006`. It replaces only the step that
turns a task into a candidate artifact.

```text
UOJ-Bench task -> Solver session -> typed candidate -> original UOJ evaluation
```

`solution/api.py` defines three entry points:

```python
solver.start_generation(task).next()
solver.start_hacking(task).next(feedback)
solver.start_repair(task).next(feedback)
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

`solution/prompt/` owns the complete baseline implementation: the original
prompt text, `PromptSolver`, the official fence parser, retry-context rendering,
and its TATU/OpenRouter adapter. Benchmark task inputs contain only raw problem
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

Set `TATU_API_KEY`; optionally set `TATU_BASE_URL`,
`TATU_MAX_OUTPUT_TOKENS`, or `TATU_TIMEOUT_SECONDS`. OpenAI-protocol models also
accept `TATU_REASONING_EFFORT`; set it explicitly for formal runs (for example,
`max` for `gpt-5.6-sol`). The normalized response records this request setting.
Gemini formal runs set `TATU_GEMINI_THINKING_LEVEL=high`; this also requests
thought summaries and preserves their signatures in subsequent agent turns.

TATU generation POSTs are not retried inside the call adapter. This keeps one
recorded model turn equal to one potentially billable request. The Agent runners
retain upstream's outer-round exception and trial behavior.

Set `GPT_OSS_BASE_URL` to route `gpt-oss-120b` to a local OpenAI-compatible
server instead of OpenRouter. `GPT_OSS_API_KEY` is optional and defaults to
`local`; `GPT_OSS_MAX_OUTPUT_TOKENS` defaults to 65536.

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
