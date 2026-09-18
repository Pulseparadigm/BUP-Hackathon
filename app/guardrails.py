"""Deterministic guardrail validator.

Sits between the LLM interpreter and the optimizer. LLM output is untrusted
structured data until it passes every check here (Problem Statement, Sec 08).
Anything that fails is downgraded to a safe no_op entry instead of crashing
or silently inventing a rule (Problem Statement, "SAFE FAILURE").
"""

import math
from typing import Any

from app.directives import DIRECTIVE_TYPES, REQUIRED_EXTRA_FIELDS, NormalizedDirective


def _safe_no_op(note_index: int, reason: str) -> dict[str, Any]:
    return {
        "note_index": note_index,
        "applies": False,
        "directive_type": "no_op",
        "structured_adjustment": None,
        "explanation": f"Guardrail fallback: {reason}",
    }


def _extract_candidates(raw: Any) -> dict[int, dict[str, Any]]:
    """Best-effort extraction of a note_index -> candidate map from raw LLM output."""
    if isinstance(raw, dict) and isinstance(raw.get("directives"), list):
        items = raw["directives"]
    elif isinstance(raw, list):
        items = raw
    else:
        return {}

    candidates: dict[int, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        idx = item.get("note_index")
        if isinstance(idx, bool) or not isinstance(idx, int):
            continue
        if idx not in candidates:
            candidates[idx] = item
    return candidates


def _is_finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _normalize_hours(raw_hours: Any) -> list[int] | None:
    if not isinstance(raw_hours, list) or not raw_hours:
        return None
    hours: set[int] = set()
    for h in raw_hours:
        if isinstance(h, bool) or not isinstance(h, int):
            return None
        if not (0 <= h <= 23):
            return None
        hours.add(h)
    return sorted(hours)


def _validate_one(
    candidate: dict[str, Any], note_index: int, battery_capacity_kwh: float
) -> tuple[dict[str, Any], NormalizedDirective | None] | None:
    """Returns (response_entry, normalized_directive_or_None) or None if unsalvageable."""

    directive_type = candidate.get("directive_type")
    if directive_type not in DIRECTIVE_TYPES:
        return None

    applies = candidate.get("applies")
    if not isinstance(applies, bool):
        return None

    explanation = candidate.get("explanation")
    if not isinstance(explanation, str) or not explanation.strip():
        explanation = f"Interpreted as {directive_type}."

    if directive_type == "no_op":
        if applies is not False:
            return None
        entry = {
            "note_index": note_index,
            "applies": False,
            "directive_type": "no_op",
            "structured_adjustment": None,
            "explanation": explanation,
        }
        return entry, None

    if applies is not True:
        return None

    adjustment = candidate.get("structured_adjustment")
    if not isinstance(adjustment, dict):
        return None

    hours = _normalize_hours(adjustment.get("hours"))
    if hours is None:
        return None

    payload: dict[str, Any] = {"hours": hours}

    for field in REQUIRED_EXTRA_FIELDS[directive_type]:
        value = adjustment.get(field)
        if not _is_finite_number(value):
            return None
        value = float(value)

        if field == "factor" and not (0.0 <= value <= 1.0):
            return None
        if field == "minimum_energy_kwh" and not (0.0 <= value <= battery_capacity_kwh):
            return None
        if field == "max_grid_kwh" and value < 0.0:
            return None

        payload[field] = value

    structured_adjustment = {"hours": hours, **{k: v for k, v in payload.items() if k != "hours"}}

    entry = {
        "note_index": note_index,
        "applies": True,
        "directive_type": directive_type,
        "structured_adjustment": structured_adjustment,
        "explanation": explanation,
    }
    normalized = NormalizedDirective(
        note_index=note_index,
        directive_type=directive_type,
        hours=tuple(hours),
        payload=payload,
    )
    return entry, normalized


def validate_and_normalize(
    raw_llm_output: Any,
    operator_notes: list[str],
    battery_capacity_kwh: float,
) -> tuple[list[dict[str, Any]], list[NormalizedDirective]]:
    candidates = _extract_candidates(raw_llm_output)

    response_entries: list[dict[str, Any]] = []
    applied_directives: list[NormalizedDirective] = []

    for note_index in range(len(operator_notes)):
        candidate = candidates.get(note_index)
        validated = None
        if candidate is not None:
            validated = _validate_one(candidate, note_index, battery_capacity_kwh)

        if validated is None:
            response_entries.append(
                _safe_no_op(
                    note_index,
                    "model output missing or did not pass validation for this note; "
                    "treated as no_op to avoid inventing an energy rule.",
                )
            )
            continue

        entry, normalized = validated
        response_entries.append(entry)
        if normalized is not None:
            applied_directives.append(normalized)

    return response_entries, applied_directives
