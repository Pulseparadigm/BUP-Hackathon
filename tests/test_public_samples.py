"""End-to-end pipeline tests against the organizer's public sample pack.

Each case's ground-truth directive_interpretation is fed in through a
ScriptedInterpreter standing in for the real LLM, so these tests exercise
guardrails -> optimizer -> internal replay validator -> response schema,
without needing network access or real credentials. They do NOT test LLM
interpretation accuracy itself -- that needs a real model, see README.
"""

import pytest

COST_TOLERANCE_BDT = 0.05


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_public_sample_cases(client, use_scripted_interpreter, public_sample_cases):
    assert public_sample_cases, "expected at least one public sample case"

    for case in public_sample_cases:
        use_scripted_interpreter(case["expected_output"]["directive_interpretation"])

        resp = client.post("/optimize-energy", json=case["input"])
        assert resp.status_code == 200, f"{case['id']}: {resp.status_code} {resp.text}"

        body = resp.json()
        assert body["scenario_id"] == case["input"]["scenario_id"]
        assert len(body["hourly_plan"]) == 24
        assert len(body["directive_interpretation"]) == len(case["input"]["operator_notes"])

        expected_cost = case["expected_output"]["total_cost_bdt"]
        assert body["total_cost_bdt"] == pytest.approx(expected_cost, abs=COST_TOLERANCE_BDT), (
            f"{case['id']}: expected near-optimal cost {expected_cost}, got {body['total_cost_bdt']}"
        )

        for expected_entry, actual_entry in zip(
            case["expected_output"]["directive_interpretation"], body["directive_interpretation"]
        ):
            assert actual_entry["note_index"] == expected_entry["note_index"]
            assert actual_entry["applies"] == expected_entry["applies"]
            assert actual_entry["directive_type"] == expected_entry["directive_type"]
