"""Builds the effective per-hour bounds a solver needs, by folding every
applied directive into the base scenario. Kept independent of any particular
solver library so a new Optimizer backend can reuse it as-is.
"""

import math
from dataclasses import dataclass

from app.directives import NormalizedDirective
from app.schemas import Battery, HourEntry

HOURS_PER_DAY = 24


@dataclass(frozen=True)
class EffectiveModel:
    demand: list[float]
    tariff: list[float]
    effective_solar: list[float]
    reserve_floor: list[float]
    capacity: float
    charge_cap: list[float]  # upper bound on charging this hour
    discharge_cap: list[float]  # upper bound on discharge magnitude this hour
    grid_cap: list[float]  # upper bound on grid import this hour
    initial_energy: float


def build_effective_model(
    hours: list[HourEntry],
    battery: Battery,
    directives: list[NormalizedDirective],
) -> EffectiveModel:
    ordered = sorted(hours, key=lambda h: h.hour)

    demand = [h.demand_kwh for h in ordered]
    tariff = [h.tariff_bdt_per_kwh for h in ordered]
    effective_solar = [h.solar_kwh for h in ordered]
    reserve_floor = [battery.minimum_energy_kwh] * HOURS_PER_DAY
    charge_cap = [battery.max_charge_kwh_per_hour] * HOURS_PER_DAY
    discharge_cap = [battery.max_discharge_kwh_per_hour] * HOURS_PER_DAY
    grid_cap = [math.inf] * HOURS_PER_DAY

    for d in directives:
        if d.directive_type == "solar_reduction":
            factor = d.payload["factor"]
            for h in d.hours:
                effective_solar[h] *= factor
        elif d.directive_type == "minimum_battery_reserve":
            floor = d.payload["minimum_energy_kwh"]
            for h in d.hours:
                reserve_floor[h] = max(reserve_floor[h], floor)
        elif d.directive_type == "no_charge_window":
            for h in d.hours:
                charge_cap[h] = 0.0
        elif d.directive_type == "no_discharge_window":
            for h in d.hours:
                discharge_cap[h] = 0.0
        elif d.directive_type == "max_grid_window":
            cap = d.payload["max_grid_kwh"]
            for h in d.hours:
                grid_cap[h] = min(grid_cap[h], cap)

    return EffectiveModel(
        demand=demand,
        tariff=tariff,
        effective_solar=effective_solar,
        reserve_floor=reserve_floor,
        capacity=battery.capacity_kwh,
        charge_cap=charge_cap,
        discharge_cap=discharge_cap,
        grid_cap=grid_cap,
        initial_energy=battery.initial_energy_kwh,
    )
