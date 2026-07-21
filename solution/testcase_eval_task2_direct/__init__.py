"""TestCase-Eval Task 2 direct-output policy."""


def build_solver(model: str):
    from solution.testcase_eval.solver import TestCaseEvalTask2Solver

    from .prompts import fault_exposure

    return TestCaseEvalTask2Solver(model, fault_exposure)
