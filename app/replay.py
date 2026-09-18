"""Final Validator stage: independently replays a produced hourly_plan
against the effective model (base rules + every applied directive) to
confirm it is actually valid, mirroring the judge's own replay described in
Problem Statement Sec 08/09/11. Used as an internal safety net before a
response leaves the service, and reused by tests to check solver output.

Returns a list of human-readable violation strings; empty means valid.
"""

from app.directives import NormalizedDirective
from app.optimizer.model import HOURS_PER_DAY, build_effective_model
from app.schemas import Battery, HourEntry

TOLERANCE = 0.01


def replay(
    hours: list[HourEntry],
    battery: Battery,
    directives: list[NormalizedDirective],
    hourly_plan: list[dict],
    total_grid_kwh: float,
    total_cost_bdt: float,
    peak_grid_kwh: float,
) -> list[str]:
    violations: list[str] = []
    m = build_effective_model(hours, battery, directives)

    if len(hourly_plan) != HOURS_PER_DAY:
        return [f"hourly_plan must have exactly {HOURS_PER_DAY} entries, got {len(hourly_plan)}"]

    by_hour = {entry["hour"]: entry for entry in hourly_plan}
    if sorted(by_hour.keys()) != list(range(HOURS_PER_DAY)):
        return ["hourly_plan hours must be the unique integers 0..23"]

    running = m.initial_energy
    recalculated_grid_sum = 0.0
    recalculated_cost_sum = 0.0
    recalculated_peak = 0.0

    for h in range(HOURS_PER_DAY):
        entry = by_hour[h]
        grid = entry["grid_kwh"]
        solar_used = entry["solar_used_kwh"]
        action = entry["battery_action"]
        battery_kwh = entry["battery_kwh"]
        after = entry["battery_energy_after_kwh"]

        if grid < -TOLERANCE:
            violations.append(f"hour {h}: negative grid_kwh")
        if solar_used < -TOLERANCE:
            violations.append(f"hour {h}: negative solar_used_kwh")
        if battery_kwh < -TOLERANCE:
            violations.append(f"hour {h}: negative battery_kwh")

        if action not in ("charge", "discharge", "idle"):
            violations.append(f"hour {h}: invalid battery_action {action!r}")
            continue

        if action == "idle" and abs(battery_kwh) > TOLERANCE:
            violations.append(f"hour {h}: idle hour must have battery_kwh == 0")

        if action == "charge" and battery_kwh > m.charge_cap[h] + TOLERANCE:
            violations.append(f"hour {h}: charge {battery_kwh} exceeds cap {m.charge_cap[h]}")
        if action == "discharge" and battery_kwh > m.discharge_cap[h] + TOLERANCE:
            violations.append(f"hour {h}: discharge {battery_kwh} exceeds cap {m.discharge_cap[h]}")

        if solar_used > m.effective_solar[h] + TOLERANCE:
            violations.append(
                f"hour {h}: solar_used {solar_used} exceeds effective solar {m.effective_solar[h]}"
            )

        if grid > m.grid_cap[h] + TOLERANCE:
            violations.append(f"hour {h}: grid {grid} exceeds max_grid_window cap {m.grid_cap[h]}")

        net = battery_kwh if action == "charge" else (-battery_kwh if action == "discharge" else 0.0)
        balance_lhs = grid + solar_used + (battery_kwh if action == "discharge" else 0.0)
        balance_rhs = m.demand[h] + (battery_kwh if action == "charge" else 0.0)
        if abs(balance_lhs - balance_rhs) > TOLERANCE:
            violations.append(
                f"hour {h}: energy balance violated ({balance_lhs} != {balance_rhs})"
            )

        running += net
        if abs(running - after) > TOLERANCE:
            violations.append(
                f"hour {h}: battery_energy_after_kwh {after} inconsistent with running total {running}"
            )
        running = after  # trust the reported trajectory for subsequent-hour checks

        if after < m.reserve_floor[h] - TOLERANCE or after > m.capacity + TOLERANCE:
            violations.append(
                f"hour {h}: battery_energy_after_kwh {after} outside "
                f"[{m.reserve_floor[h]}, {m.capacity}]"
            )

        recalculated_grid_sum += grid
        recalculated_cost_sum += grid * m.tariff[h]
        recalculated_peak = max(recalculated_peak, grid)

    final_after = by_hour[HOURS_PER_DAY - 1]["battery_energy_after_kwh"]
    if abs(final_after - m.initial_energy) > TOLERANCE:
        violations.append(
            f"end-of-day battery energy {final_after} != initial_energy_kwh {m.initial_energy}"
        )

    if abs(recalculated_grid_sum - total_grid_kwh) > TOLERANCE:
        violations.append(
            f"total_grid_kwh {total_grid_kwh} != recalculated {recalculated_grid_sum}"
        )
    if abs(recalculated_cost_sum - total_cost_bdt) > TOLERANCE:
        violations.append(
            f"total_cost_bdt {total_cost_bdt} != recalculated {recalculated_cost_sum}"
        )
    if abs(recalculated_peak - peak_grid_kwh) > TOLERANCE:
        violations.append(f"peak_grid_kwh {peak_grid_kwh} != recalculated {recalculated_peak}")

    return violations
