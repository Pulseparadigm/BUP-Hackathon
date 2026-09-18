from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.directives import NormalizedDirective
from app.schemas import Battery, HourEntry


class OptimizationInfeasibleError(Exception):
    """Raised when no schedule satisfies the given scenario + directives."""


@dataclass(frozen=True)
class OptimizationResult:
    hourly_plan: list[dict]
    total_grid_kwh: float
    total_cost_bdt: float
    peak_grid_kwh: float


class Optimizer(ABC):
    """Solves the 24-hour least-cost battery/grid/solar dispatch problem.

    Swappable via SOLVER_BACKEND (see optimizer/factory.py) so the LP model
    here can be replaced by a different formulation/solver later without
    touching main.py or guardrails.
    """

    @abstractmethod
    def solve(
        self,
        hours: list[HourEntry],
        battery: Battery,
        directives: list[NormalizedDirective],
    ) -> OptimizationResult:
        raise NotImplementedError
