"""Reference Optimizer backend: a small LP solved with scipy's HiGHS.

Per hour h we use ONE signed battery variable, net[h] (positive = charging,
negative = discharging), instead of separate charge/discharge variables.
Since GridWise has no round-trip efficiency loss, charging and discharging
in the same hour can never lower cost, so the LP relaxation never wants to
do both at once -- no binary "charge XOR discharge" variables are needed and
the whole problem stays a clean continuous LP.

Battery energy after hour h is initial_energy + prefix_sum(net[0..h]), so
capacity/reserve bounds become linear inequalities on prefix sums, and
end-of-day neutrality is just sum(net) == 0.
"""

import math

import numpy as np
from scipy.optimize import linprog

from app.directives import NormalizedDirective
from app.optimizer.base import Optimizer, OptimizationInfeasibleError, OptimizationResult
from app.optimizer.model import HOURS_PER_DAY, build_effective_model
from app.schemas import Battery, HourEntry

_ACTION_TOL = 1e-6
_ROUND_DP = 6


def _grid_idx(h: int) -> int:
    return h


def _solar_idx(h: int) -> int:
    return HOURS_PER_DAY + h


def _batt_idx(h: int) -> int:
    return 2 * HOURS_PER_DAY + h


class HighsOptimizer(Optimizer):
    def solve(
        self,
        hours: list[HourEntry],
        battery: Battery,
        directives: list[NormalizedDirective],
    ) -> OptimizationResult:
        m = build_effective_model(hours, battery, directives)
        n = HOURS_PER_DAY
        num_vars = 3 * n

        c = np.zeros(num_vars)
        for h in range(n):
            c[_grid_idx(h)] = m.tariff[h]

        bounds: list[tuple[float, float | None]] = [(0.0, None)] * num_vars
        for h in range(n):
            grid_upper = None if math.isinf(m.grid_cap[h]) else m.grid_cap[h]
            bounds[_grid_idx(h)] = (0.0, grid_upper)
            bounds[_solar_idx(h)] = (0.0, max(0.0, m.effective_solar[h]))
            bounds[_batt_idx(h)] = (-m.discharge_cap[h], m.charge_cap[h])

        A_eq = []
        b_eq = []
        for h in range(n):
            row = np.zeros(num_vars)
            row[_grid_idx(h)] = 1.0
            row[_solar_idx(h)] = 1.0
            row[_batt_idx(h)] = -1.0
            A_eq.append(row)
            b_eq.append(m.demand[h])

        neutrality = np.zeros(num_vars)
        for h in range(n):
            neutrality[_batt_idx(h)] = 1.0
        A_eq.append(neutrality)
        b_eq.append(0.0)

        A_ub = []
        b_ub = []
        for h in range(n):
            upper_row = np.zeros(num_vars)
            upper_row[[_batt_idx(k) for k in range(h + 1)]] = 1.0
            A_ub.append(upper_row)
            b_ub.append(m.capacity - m.initial_energy)

            lower_row = np.zeros(num_vars)
            lower_row[[_batt_idx(k) for k in range(h + 1)]] = -1.0
            A_ub.append(lower_row)
            b_ub.append(m.initial_energy - m.reserve_floor[h])

        result = linprog(
            c=c,
            A_ub=np.array(A_ub),
            b_ub=np.array(b_ub),
            A_eq=np.array(A_eq),
            b_eq=np.array(b_eq),
            bounds=bounds,
            method="highs",
        )

        if not result.success:
            raise OptimizationInfeasibleError(result.message)

        x = result.x
        grid = [max(0.0, x[_grid_idx(h)]) for h in range(n)]
        solar_used = [max(0.0, x[_solar_idx(h)]) for h in range(n)]
        net = [x[_batt_idx(h)] for h in range(n)]

        battery_after = []
        running = m.initial_energy
        for h in range(n):
            running += net[h]
            battery_after.append(running)

        hourly_plan = []
        for h in range(n):
            net_h = net[h]
            if net_h > _ACTION_TOL:
                action, magnitude = "charge", net_h
            elif net_h < -_ACTION_TOL:
                action, magnitude = "discharge", -net_h
            else:
                action, magnitude = "idle", 0.0

            hourly_plan.append(
                {
                    "hour": h,
                    "grid_kwh": round(grid[h], _ROUND_DP),
                    "solar_used_kwh": round(solar_used[h], _ROUND_DP),
                    "battery_action": action,
                    "battery_kwh": round(magnitude, _ROUND_DP),
                    "battery_energy_after_kwh": round(battery_after[h], _ROUND_DP),
                }
            )

        total_grid_kwh = round(sum(grid), _ROUND_DP)
        total_cost_bdt = round(sum(g * t for g, t in zip(grid, m.tariff)), _ROUND_DP)
        peak_grid_kwh = round(max(grid), _ROUND_DP)

        return OptimizationResult(
            hourly_plan=hourly_plan,
            total_grid_kwh=total_grid_kwh,
            total_cost_bdt=total_cost_bdt,
            peak_grid_kwh=peak_grid_kwh,
        )
