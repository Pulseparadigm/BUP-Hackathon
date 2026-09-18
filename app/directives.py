from dataclasses import dataclass
from typing import Any

DIRECTIVE_TYPES = frozenset(
    {
        "solar_reduction",
        "minimum_battery_reserve",
        "no_charge_window",
        "no_discharge_window",
        "max_grid_window",
        "no_op",
    }
)

# Which structured_adjustment keys each non-no_op directive requires, beyond "hours".
REQUIRED_EXTRA_FIELDS = {
    "solar_reduction": ("factor",),
    "minimum_battery_reserve": ("minimum_energy_kwh",),
    "no_charge_window": (),
    "no_discharge_window": (),
    "max_grid_window": ("max_grid_kwh",),
}


@dataclass(frozen=True)
class NormalizedDirective:
    """One guardrail-validated, applies=true directive, ready for the optimizer."""

    note_index: int
    directive_type: str
    hours: tuple[int, ...]
    payload: dict[str, Any]
