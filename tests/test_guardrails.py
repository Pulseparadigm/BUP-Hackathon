from app.guardrails import validate_and_normalize


def test_malformed_output_falls_back_to_no_op_without_crashing():
    entries, applied = validate_and_normalize(
        raw_llm_output={"unexpected": "shape"},
        operator_notes=["note a", "note b"],
        battery_capacity_kwh=500,
    )
    assert len(entries) == 2
    assert all(e["directive_type"] == "no_op" and e["applies"] is False for e in entries)
    assert applied == []


def test_unsupported_directive_type_falls_back_to_no_op():
    raw = {
        "directives": [
            {
                "note_index": 0,
                "applies": True,
                "directive_type": "shutdown_grid",
                "structured_adjustment": {"hours": [1, 2]},
                "explanation": "hallucinated directive",
            }
        ]
    }
    entries, applied = validate_and_normalize(raw, ["note a"], battery_capacity_kwh=500)
    assert entries[0]["directive_type"] == "no_op"
    assert applied == []


def test_out_of_order_hours_are_normalized():
    raw = {
        "directives": [
            {
                "note_index": 0,
                "applies": True,
                "directive_type": "no_charge_window",
                "structured_adjustment": {"hours": [15, 14]},
                "explanation": "no charging in the afternoon",
            }
        ]
    }
    entries, applied = validate_and_normalize(raw, ["note a"], battery_capacity_kwh=500)
    assert entries[0]["structured_adjustment"]["hours"] == [14, 15]
    assert applied[0].hours == (14, 15)


def test_solar_factor_out_of_range_falls_back_to_no_op():
    raw = {
        "directives": [
            {
                "note_index": 0,
                "applies": True,
                "directive_type": "solar_reduction",
                "structured_adjustment": {"hours": [1], "factor": 1.5},
                "explanation": "invalid factor",
            }
        ]
    }
    entries, applied = validate_and_normalize(raw, ["note a"], battery_capacity_kwh=500)
    assert entries[0]["directive_type"] == "no_op"
    assert applied == []


def test_valid_no_op_passes_through():
    raw = {
        "directives": [
            {
                "note_index": 0,
                "applies": False,
                "directive_type": "no_op",
                "structured_adjustment": None,
                "explanation": "irrelevant note",
            }
        ]
    }
    entries, applied = validate_and_normalize(raw, ["note a"], battery_capacity_kwh=500)
    assert entries[0]["applies"] is False
    assert applied == []
