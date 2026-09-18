from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class HourEntry(BaseModel):
    hour: int = Field(ge=0, le=23)
    demand_kwh: float = Field(ge=0, allow_inf_nan=False)
    solar_kwh: float = Field(ge=0, allow_inf_nan=False)
    tariff_bdt_per_kwh: float = Field(ge=0, allow_inf_nan=False)


class Battery(BaseModel):
    # capacity_kwh allows 0: a zero-capacity battery is a valid degenerate
    # case (the optimizer forces net flow to 0 every hour and just solves
    # grid+solar dispatch), not a malformed request.
    capacity_kwh: float = Field(ge=0, allow_inf_nan=False)
    initial_energy_kwh: float = Field(ge=0, allow_inf_nan=False)
    minimum_energy_kwh: float = Field(ge=0, allow_inf_nan=False)
    max_charge_kwh_per_hour: float = Field(ge=0, allow_inf_nan=False)
    max_discharge_kwh_per_hour: float = Field(ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def _consistent_energy_bounds(self) -> "Battery":
        # Without this, a self-contradictory battery (e.g. minimum reserve
        # above capacity) isn't rejected here -- it reaches the LP as an
        # infeasible problem, and OptimizationInfeasibleError turns it into
        # a 500 instead of a clean 400 for what is actually a bad request.
        if self.minimum_energy_kwh > self.capacity_kwh:
            raise ValueError("minimum_energy_kwh must not exceed capacity_kwh")
        if not (self.minimum_energy_kwh <= self.initial_energy_kwh <= self.capacity_kwh):
            raise ValueError("initial_energy_kwh must be between minimum_energy_kwh and capacity_kwh")
        return self


class OptimizeRequest(BaseModel):
    scenario_id: str = Field(min_length=1)
    operator_notes: list[str] = Field(min_length=1, max_length=3)
    hours: list[HourEntry] = Field(min_length=24, max_length=24)
    battery: Battery

    @field_validator("operator_notes")
    @classmethod
    def notes_non_empty(cls, v: list[str]) -> list[str]:
        if any(not note.strip() for note in v):
            raise ValueError("operator_notes entries must be non-empty strings")
        return v

    @field_validator("hours")
    @classmethod
    def hours_cover_0_23(cls, v: list[HourEntry]) -> list[HourEntry]:
        seen = sorted(h.hour for h in v)
        if seen != list(range(24)):
            raise ValueError("hours must contain exactly one entry for each hour 0-23")
        return v


class DirectiveInterpretation(BaseModel):
    note_index: int
    applies: bool
    directive_type: Literal[
        "solar_reduction",
        "minimum_battery_reserve",
        "no_charge_window",
        "no_discharge_window",
        "max_grid_window",
        "no_op",
    ]
    structured_adjustment: Optional[dict[str, Any]]
    explanation: str


class HourlyPlanEntry(BaseModel):
    hour: int
    grid_kwh: float
    solar_used_kwh: float
    battery_action: Literal["charge", "discharge", "idle"]
    battery_kwh: float
    battery_energy_after_kwh: float


class OptimizeResponse(BaseModel):
    scenario_id: str
    directive_interpretation: list[DirectiveInterpretation]
    hourly_plan: list[HourlyPlanEntry]
    total_grid_kwh: float
    total_cost_bdt: float
    peak_grid_kwh: float
    plan_summary: str


class HealthResponse(BaseModel):
    status: Literal["ok"]
