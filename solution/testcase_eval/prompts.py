"""Official TestCase-Eval chain-of-thought prompts."""

from solution.api import FaultCoverageInput, FaultExposureInput, HackingInput


TASK1_COT = """**Task:**
Generate a challenging test input for the algorithm problem:
{problem}

**Instructions:**
- Focus on edge cases or scenarios that maximize the failure probability in faulty solutions.
- Due to the output length limit, you should generate a **small-scale** test input that is **complete and valid**.
- Output the test input directly, not code to generate it.


**Output format:**
```plaintext
{{test input}}
```


Think step by step.
"""


TASK2_COT = """**Task:**
Generate a challenging test input that exposes the bug in the buggy code of the algorithm problem:
Algorithm Problem:
{title}{problem}

Buggy Code:
{code}

**Instructions:**
- Focus on edge cases or scenarios that maximize the failure probability in faulty solutions.
- Due to the output length limit, you should generate a **small-scale** test input that is **complete and valid**.
- Output the test input directly, not code to generate it.

**Output format:**
```plaintext
{{test input}}
```

Think step by step.
"""


def fault_coverage(task: FaultCoverageInput) -> str:
    return TASK1_COT.format(problem=task.problem_statement)


def fault_exposure(task: FaultExposureInput) -> str:
    return TASK2_COT.format(
        title="",
        problem=task.problem_statement,
        code=task.submission_code,
    )


def hacking(task: HackingInput) -> str:
    if task.chinese:
        raise NotImplementedError("TestCase-Eval Task 2 supports English problem statements only")

    title = task.metadata.get("title_en")
    title_line = f"Title: {title}\n" if isinstance(title, str) and title.strip() else ""
    return TASK2_COT.format(
        title=title_line,
        problem=task.problem_statement,
        code=task.submission_code,
    )
