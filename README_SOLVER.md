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

`solution/prompt/` owns the complete baseline implementation: `PromptSolver`,
the official fence parser, retry-context rendering, and its TATU/OpenRouter
adapter. It returns `SolutionCandidate`, `HackCandidate`, or `PatchCandidate`.
New pipelines need no central registry entry; the CLI imports the directory by
name and passes `--model` to its factory.

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

TATU generation POSTs are not retried inside the call adapter. This keeps one
recorded model turn equal to one potentially billable request. The Agent runners
retain upstream's outer-round exception and trial behavior.

## Offline verification

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
UOJ_API_KEY=offline PYTHONDONTWRITEBYTECODE=1 \
  .venv/bin/python -m unittest discover -s tests -v
```

The boundary test compares the working tree with `ce1c006`: the dataset,
official README, patch helper, UOJ client, and all prompt strings must remain
unchanged. Outside `solution/`, the fork contains only benchmark runners,
benchmark utilities, tests, and documentation. This branch intentionally does
not include a batch runner or durable submission recovery.
