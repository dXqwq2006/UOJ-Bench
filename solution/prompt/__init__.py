"""The upstream prompt and parser as a directory-based solver."""

from utils.solver import PromptSolver, Solver


def build_solver(model: str) -> Solver:
    return PromptSolver(model)
