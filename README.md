## Directory Structure

```
submit/
├── scripts/          # Python test scripts for different tasks
├── utils/            # Utility modules
└── dataset/          # Input data files for testing
```

## Dataset Files

Located in `dataset/` directory:

- `problems.json` - Problem statements from UOJ
- `hacks.json` - Incorrect code samples for hack testing (Hard version)
- `small_submission_pairs.json` - Hard version dataset of similar code pairs for debugging
- `sampled_large_submission_pairs.json` - Easy version dataset of similar code pairs for debugging and hack testing
- `sampled_ac_submissions.json` - Some ac submissions of uoj (open hack task)
- `sampled_*.json` - Sampled versions of the above datasets

## Scripts Overview

### 1. `test_problem.py`
**Purpose**: Test LLM's ability to generate correct C++20 solutions for given problems.

**Arguments**:
- `model` (str): Model name
- `problem_idx` (int): Index of the problem in the dataset
- `chinese` (bool): Whether to use Chinese prompts

**Returns**: `(score, result, prompt, return_message, usage)`
- `score`: Points earned (0-100)
- `result`: Submission result from UOJ
- `prompt`: Prompt used
- `return_message`: Full API response message
- `usage`: Token usage statistics

### 2. `test_hack.py`
**Purpose**: Test LLM's ability to generate failing test cases (hacks) for buggy code in a single attempt.

**Arguments**:
- `file` (str): The path to the dataset
- `model` (str): Model name
- `hack_idx` (int): Index of hack in the dataset
- `chinese` (bool): Whether to use Chinese prompts

**Returns**: Same as `test_problem.py`

---

### 3. `test_hack_agent.py`
**Purpose**: Test LLM's ability to generate failing test cases through iterative multi-turn conversations (agent mode).

**Arguments**:
- `file` (str): The path to the dataset
- `model` (str): Model name
- `hack_idx` (int): Index of hack in the dataset
- `max_trials` (int): Maximum conversation turns allowed

**Returns**: `(hack_score, results, prompt, full_msgs, usages)`
- `score`: 1 if hack successful, 0 if all trials exhausted
- `message`: Full conversation history with assistant
- `results`: List of submission results from each trial
- `full_msgs`: List of full API response messages from each turn
- `usages`: List of token usage from each turn

---

### 4. `test_debug.py` and `test_debug_agent.py`

Similar as `test_hack.py` and `test_hack_agent.py`

---

## Custom Configuration for Testing

When using your own LLM or API:

1. Modify `utils/call_llm.py`, and replace the API call logic in the `call_api()` function to support your custom LLM endpoint.
2. Handle Reasoning Content in Agent Scripts. The agent scripts (`test_hack_agent.py` and `test_debug_agent.py`) extract reasoning content from the API response. If the results your api returns do not match the scripts, please change it.
3. Set environment variable `UOJ_API_KEY` to the api key of UOJ.
