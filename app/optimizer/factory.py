from functools import lru_cache

from app.config import Settings, settings
from app.optimizer.base import Optimizer
from app.optimizer.highs_solver import HighsOptimizer


def build_optimizer(cfg: Settings) -> Optimizer:
    if cfg.solver_backend == "highs":
        return HighsOptimizer()

    # To try a different solving method (e.g. OR-Tools, a MILP with explicit
    # charge/discharge binaries, a heuristic): implement Optimizer in
    # app/optimizer/, add an elif branch here, and switch SOLVER_BACKEND.
    # main.py never needs to change.
    raise ValueError(f"Unknown SOLVER_BACKEND: {cfg.solver_backend!r}")


@lru_cache(maxsize=1)
def get_optimizer() -> Optimizer:
    return build_optimizer(settings)
