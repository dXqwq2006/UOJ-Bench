"""TestCase-Eval Task 1 chain-of-thought policy."""


def build_solver(model: str):
    from solution.testcase_eval.solver import TestCaseEvalTask1Solver

    from .prompts import fault_coverage

    return TestCaseEvalTask1Solver(model, fault_coverage)
