"""Pinned TestCase-Eval Task 1 chain-of-thought prompt."""

from solution.api import FaultCoverageInput


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


Think step by step."""


def fault_coverage(task: FaultCoverageInput) -> str:
    return TASK1_COT.format(problem=task.problem_statement)
