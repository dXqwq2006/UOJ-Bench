"""UOJ-Bench adapter for the ICPC Light v3.3 pipeline bridge."""

from solution.api import Solver

from .solver import BRIDGE_CONFIG_ENV, ICPC_LIGHT_MODEL, ICLightBridgeSolver

__all__ = [
    "BRIDGE_CONFIG_ENV",
    "ICPC_LIGHT_MODEL",
    "ICLightBridgeSolver",
    "build_solver",
]


def build_solver(model: str) -> Solver:
    return ICLightBridgeSolver(model)
