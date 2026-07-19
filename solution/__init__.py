"""Load solver pipelines from subdirectories."""

from importlib import import_module

from utils.solver import Solver


def load_solver(name: str, model: str) -> Solver:
    if not name.isidentifier() or name.startswith("_"):
        raise ValueError(f"invalid solver name: {name!r}")

    module = import_module(f"solution.{name}")
    factory = getattr(module, "build_solver", None)
    if not callable(factory):
        raise TypeError(f"solution.{name} must export build_solver(model)")
    return factory(model)
