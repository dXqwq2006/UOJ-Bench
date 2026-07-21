"""Pinned TestCase-Eval Task 2 direct-output prompt."""

from solution.api import FaultExposureInput


TASK2_DIRECT = """**Task:**
Generate a challenging test input that exposes the bug in the buggy code of the algorithm problem:
Algorithm Problem:
{problem}

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


Only output the test input, no explanations."""


def fault_exposure(task: FaultExposureInput) -> str:
    return TASK2_DIRECT.format(
        problem=task.problem_statement,
        code=task.submission_code,
    )
