"""The upstream prompt baseline pipeline."""

from solution.api import Solver

from .solver import PromptSolver

__all__ = ["PromptSolver", "build_solver"]


def build_solver(model: str) -> Solver:
    return PromptSolver(model)
