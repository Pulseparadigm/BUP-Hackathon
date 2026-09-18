"""Runs the organizer's public sample cases through a *live* running server
(real LLM calls via OpenRouter, not the scripted test double), and appends a
before/after OpenRouter balance snapshot to balance_log.jsonl.

Requires the server already running (see README: uvicorn app.main:app).

Usage:
    .venv/Scripts/python.exe scripts/test_live_samples.py [--port 8000]
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
DATA_PATH = PROJECT_DIR / "BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json"
ENV_PATH = PROJECT_DIR / ".env"
BALANCE_LOG_PATH = PROJECT_DIR / "balance_log.jsonl"
COST_TOLERANCE_BDT = 0.05


def read_env_var(name: str) -> str | None:
    with open(ENV_PATH, encoding="utf-8") as f:
        for line in f:
            if line.strip().startswith(f"{name}="):
                return line.strip().split("=", 1)[1]
    return None


def http_get_json(url: str, headers: dict) -> dict:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def http_post_json(url: str, payload: dict) -> tuple[int, dict]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def fetch_balance(api_key: str) -> dict:
    headers = {"Authorization": f"Bearer {api_key}"}
    key_info = http_get_json("https://openrouter.ai/api/v1/auth/key", headers=headers)["data"]
    credits_info = http_get_json("https://openrouter.ai/api/v1/credits", headers=headers)["data"]
    return {
        "key_limit_usd": key_info.get("limit"),
        "key_usage_usd": key_info.get("usage"),
        "key_limit_remaining_usd": key_info.get("limit_remaining"),
        "account_total_credits_usd": credits_info.get("total_credits"),
        "account_total_usage_usd": credits_info.get("total_usage"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=None, help="Overrides PORT from .env")
    args = parser.parse_args()

    api_key = read_env_var("OPENROUTER_API_KEY")
    model = read_env_var("OPENROUTER_MODEL")
    port = args.port or int(read_env_var("PORT") or "8000")
    url = f"http://localhost:{port}/optimize-energy"

    with open(DATA_PATH, encoding="utf-8") as f:
        cases = json.load(f)["cases"]

    balance_before = fetch_balance(api_key) if api_key else None

    results = []
    for case in cases:
        t0 = time.time()
        status, body = http_post_json(url, case["input"])
        elapsed = time.time() - t0

        issues = []
        if status != 200:
            issues.append(f"HTTP {status}: {body}")
            results.append((case["id"], case["label"], issues, elapsed))
            continue

        if body.get("scenario_id") != case["input"]["scenario_id"]:
            issues.append("scenario_id mismatch")
        if len(body.get("hourly_plan", [])) != 24:
            issues.append(f"hourly_plan len {len(body.get('hourly_plan', []))} != 24")

        expected_di = case["expected_output"]["directive_interpretation"]
        actual_di = body.get("directive_interpretation", [])
        if len(actual_di) != len(expected_di):
            issues.append(f"directive_interpretation len {len(actual_di)} != {len(expected_di)}")

        di_mismatches = []
        for exp, act in zip(expected_di, actual_di):
            if (
                act.get("note_index") != exp["note_index"]
                or act.get("applies") != exp["applies"]
                or act.get("directive_type") != exp["directive_type"]
            ):
                di_mismatches.append(
                    {
                        "note_index": exp["note_index"],
                        "expected": {"applies": exp["applies"], "type": exp["directive_type"]},
                        "actual": {"applies": act.get("applies"), "type": act.get("directive_type")},
                    }
                )
        if di_mismatches:
            issues.append(f"directive mismatches: {di_mismatches}")

        expected_cost = case["expected_output"]["total_cost_bdt"]
        actual_cost = body.get("total_cost_bdt")
        if actual_cost is None or abs(actual_cost - expected_cost) > COST_TOLERANCE_BDT:
            issues.append(f"cost expected~{expected_cost} got {actual_cost}")

        results.append((case["id"], case["label"], issues, elapsed))

    passed = sum(1 for r in results if not r[2])

    print(f"\n{passed}/{len(results)} cases passed\n")
    for case_id, label, issues, elapsed in results:
        status_str = "PASS" if not issues else "FAIL"
        print(f"[{status_str}] {case_id} - {label} ({elapsed:.1f}s)")
        for issue in issues:
            print(f"         {issue}")

    if api_key:
        balance_after = fetch_balance(api_key)
        usd_spent = round(balance_after["key_usage_usd"] - balance_before["key_usage_usd"], 6)
        print(f"\nKey usage before: ${balance_before['key_usage_usd']:.6f} / limit ${balance_before['key_limit_usd']}")
        print(f"Key usage after:  ${balance_after['key_usage_usd']:.6f} / limit ${balance_after['key_limit_usd']}")
        print(f"Spent this run:   ${usd_spent}")
        print(f"Key remaining:    ${balance_after['key_limit_remaining_usd']:.6f}")

        log_entry = {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "model": model,
            "cases_run": len(results),
            "cases_passed": passed,
            "balance_before": balance_before,
            "balance_after": balance_after,
            "usd_spent_this_run": usd_spent,
        }
        with open(BALANCE_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry) + "\n")
        print(f"\nBalance log appended to: {BALANCE_LOG_PATH}")

    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
