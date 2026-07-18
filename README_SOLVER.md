# Solver abstraction

This fork keeps UOJ-Bench's datasets, prompts, UOJ client, patch application, and
scoring rules pinned to upstream commit `ce1c006`. It replaces only the step that
turns a task into a candidate artifact.

```text
UOJ-Bench task -> Solver session -> typed candidate -> original UOJ evaluation
```

`utils/solver.py` defines three entry points:

```python
solver.start_generation(task).next()
solver.start_hacking(task).next(feedback)
solver.start_repair(task).next(feedback)
```

The default `PromptSolver` sends the rendered upstream prompt to one model call
and applies the exact upstream fence parser. It returns `SolutionCandidate`,
`HackCandidate`, or `PatchCandidate`. A more complex pipeline can implement the
same `Solver` protocol and return the same candidate types without emitting
Markdown.

Direct tasks request one turn. The hacking and repair agent scripts keep their
UOJ-owned trial loops and pass parser, local validation, and judge rejection
feedback back to the session. Correct reference code and hidden error labels are
never included in solver metadata.

## Model configuration

The upstream `gpt-oss-120b` OpenRouter path remains available. These model names
use TATU's native protocols:

- `gemini-3.1-pro-preview`
- `gpt-5.5`
- `gpt-5.6-sol`
- `claude-fable-5`

Set `TATU_API_KEY`; optionally set `TATU_BASE_URL`,
`TATU_MAX_OUTPUT_TOKENS`, `TATU_MAX_RETRIES`, or `TATU_TIMEOUT_SECONDS`.

## Offline verification

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
UOJ_API_KEY=offline PYTHONDONTWRITEBYTECODE=1 \
  .venv/bin/python -m unittest discover -s tests -v
```

The boundary test compares the committed tree with `ce1c006`: the dataset,
official README, patch helper, UOJ client, and all prompt strings must remain
unchanged. This branch intentionally does not include a batch runner or durable
submission recovery.
