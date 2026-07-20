"""TestCase-Eval Task 2 one-shot solver."""

from solution.api import Solver

from .solver import TestCaseEvalSolver

CAPABILITIES = TestCaseEvalSolver.capabilities
__all__ = ["CAPABILITIES", "TestCaseEvalSolver", "build_solver"]


def build_solver(model: str) -> Solver:
    return TestCaseEvalSolver(model)
