"""HardTestGen paper pipeline entry point.

The generic one-candidate runner cannot represent a variable-size test suite. Use
``scripts.test_paper_hardtestgen`` for generation; ``build_solver`` exists so the
shared benchmark scorer can discover the policy capability.
"""

from solution.api import SolverCapabilities

from .api import HardTestGenInput, OracleProgram, SuiteResult, TestCaseKit
from .pipeline import HardTestGenPipeline


UPSTREAM_COMMIT = "03553153e9fcd2b94eefe600c20e42ef8c4dcef5"
CAPABILITIES = SolverCapabilities(
    generation=False,
    hacking=False,
    repair=False,
    fault_coverage=True,
    fault_exposure=False,
)


class HardTestGenSolver:
    capabilities = CAPABILITIES

    def __init__(self, model: str):
        self.pipeline = HardTestGenPipeline(model)

    def start_fault_coverage(self, task):
        raise RuntimeError(
            "HardTestGen returns a suite; use scripts.test_paper_hardtestgen"
        )


def build_solver(model: str) -> HardTestGenSolver:
    return HardTestGenSolver(model)


__all__ = [
    "CAPABILITIES",
    "HardTestGenInput",
    "HardTestGenPipeline",
    "OracleProgram",
    "SuiteResult",
    "TestCaseKit",
    "UPSTREAM_COMMIT",
    "build_solver",
]
