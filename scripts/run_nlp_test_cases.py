"""Runs every section of nlp_test_cases.py (repo root) against a live server:

  A. CASES (48) + ERROR_NOTE_CASES (33) -- real operator notes through the live
     LLM + guardrails + optimizer, graded with nlp_test_cases.check().
  B. BAD_REQUESTS (35) -- malformed HTTP bodies against /optimize-energy.
     Reimplemented with urllib since the `requests` package isn't a project
     dependency (nlp_test_cases.run_bad_requests() needs it).
  C. BAD_LLM_OUTPUTS (25) -- raw strings fed directly into
     app.guardrails.validate_and_normalize(), no network/LLM involved. Graded
     against an expected-acceptance table derived by reading guardrails.py:
     most bad outputs must collapse to no_op, but a few (unsorted/duplicate
     hours, an extra unrecognized key) are expected to be *accepted* because
     the guardrail normalizes rather than rejects them.

Requires the server already running (uvicorn app.main:app).
"""

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

import nlp_test_cases as tc  # noqa: E402
from app.guardrails import validate_and_normalize  # noqa: E402

BASE_URL = "http://localhost:8000"

# For BAD_LLM_OUTPUTS: names expected to be ACCEPTED as a real directive
# (guardrail normalizes rather than rejects). Everything else in that list
# must collapse to no_op/applies=False for every note, and none may crash.
EXPECTED_ACCEPTED = {
    "hours_unsorted": ("no_charge_window", [14, 15]),
    "hours_duplicate": ("no_charge_window", [14, 15]),
    "extra_adjust_key": ("no_charge_window", [14]),
}


def http(method, url, body_str=None):
    data = body_str.encode("utf-8") if body_str is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")
    except Exception as e:
        return None, str(e)


def run_section_a(cases):
    results = []
    for case in cases:
        req_body = tc.base_request()
        req_body["operator_notes"] = case["notes"]
        status, text = http("POST", f"{BASE_URL}/optimize-energy", json.dumps(req_body))
        if status != 200:
            results.append({"tag": case["tag"], "notes": case["notes"], "ok": False, "detail": f"HTTP {status}: {text[:300]}"})
            continue
        body = json.loads(text)
        interpretation = body.get("directive_interpretation", [])
        try:
            ok = tc.check(interpretation, case["expected"])
        except Exception as e:
            ok = False
        detail = None if ok else {"expected": case["expected"], "actual": interpretation}
        results.append({"tag": case["tag"], "notes": case["notes"], "ok": ok, "detail": detail})
    return results


def run_section_b():
    results = []
    for name, body, ok_codes in tc.BAD_REQUESTS:
        data = body if isinstance(body, str) else json.dumps(body)
        status, text = http("POST", f"{BASE_URL}/optimize-energy", data)
        leaked = status is not None and ("Traceback" in text or "sk-" in text)
        ok = status in ok_codes and not leaked
        results.append({"name": name, "status": status, "expected": sorted(ok_codes), "ok": ok, "leaked": leaked})
    return results


def run_section_c():
    results = []
    notes_placeholder = ["placeholder note 0", "placeholder note 1"]
    for name, raw in tc.BAD_LLM_OUTPUTS:
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = None  # mirrors main.py's degrade path on an unparseable LLM response

        try:
            entries, _applied = validate_and_normalize(parsed, notes_placeholder, 500.0)
            crashed = False
        except Exception as e:
            entries, crashed = None, str(e)

        if crashed:
            results.append({"name": name, "ok": False, "detail": f"CRASHED: {crashed}"})
            continue

        if name in EXPECTED_ACCEPTED:
            want_type, want_hours = EXPECTED_ACCEPTED[name]
            got = next((e for e in entries if e["directive_type"] == want_type), None)
            ok = got is not None and got["applies"] is True and got["structured_adjustment"]["hours"] == want_hours
            detail = None if ok else {"expected_accept": EXPECTED_ACCEPTED[name], "actual": entries}
        else:
            ok = all(e["directive_type"] == "no_op" and e["applies"] is False for e in entries)
            detail = None if ok else {"expected": "all no_op", "actual": entries}

        results.append({"name": name, "ok": ok, "detail": detail})
    return results


def print_section(title, results, key_name):
    passed = sum(1 for r in results if r["ok"])
    print(f"\n=== {title}: {passed}/{len(results)} passed ===")
    for r in results:
        if not r["ok"]:
            label = r.get("tag", r.get(key_name))
            extra = {k: v for k, v in r.items() if k not in ("ok", "tag", key_name, "notes")}
            print(f"  FAIL [{label}] {r.get('notes', '')}")
            print(f"        {extra}")
    return passed, len(results)


def main():
    a_results = run_section_a(tc.CASES + tc.ERROR_NOTE_CASES)
    b_results = run_section_b()
    c_results = run_section_c()

    a_pass, a_total = print_section("Section A (CASES + ERROR_NOTE_CASES, live LLM)", a_results, "tag")
    b_pass, b_total = print_section("Section B (BAD_REQUESTS, malformed HTTP)", b_results, "name")
    c_pass, c_total = print_section("Section C (BAD_LLM_OUTPUTS, guardrail unit tests)", c_results, "name")

    total_pass = a_pass + b_pass + c_pass
    total = a_total + b_total + c_total
    print(f"\n=== TOTAL: {total_pass}/{total} passed ===")

    return 0 if total_pass == total else 1


if __name__ == "__main__":
    sys.exit(main())
