# GridWise LLM — Energy Optimization Service

HTTP API for the BUP CSE Fest 2026 "Smart Campus Energy Optimization" preliminary. Interprets
1-3 natural-language operator notes per scenario with an LLM, validates the interpretation
deterministically, and solves the 24-hour least-cost grid/solar/battery schedule with a linear
program.

## Architecture

```
Energy Data + Operator Notes
        |
        v
  LLM Interpreter        (app/llm)         -- OpenRouter call, notes -> raw candidate JSON
        |
        v
  Guardrail Validator    (app/guardrails)  -- deterministic checks; unsafe output -> no_op
        |
        v
  Math Optimizer         (app/optimizer)   -- LP (scipy/HiGHS): least-cost 24h dispatch
        |
        v
  Final Validator        (app/replay)      -- independently replays the plan before it ships
        |
        v
  API Response            (app/main.py)
```

Every stage is a swappable interface, on purpose, because both the model provider and the
solving method are expected to change during the event:

- **LLM provider/model**: `app/llm/base.py` defines `LLMInterpreter`. The only implementation
  today is `OpenRouterInterpreter` (`app/llm/openrouter_client.py`), selected by
  `LLM_PROVIDER=openrouter` in `app/llm/factory.py`. Changing the *model* is just
  `OPENROUTER_MODEL` in `.env` -- no code change. Changing the *provider* (e.g. calling OpenAI
  or Anthropic directly instead of via OpenRouter) means adding one new class that implements
  `interpret()` and one `elif` branch in `factory.py`; `main.py` and `guardrails.py` never change.
- **Solving method**: `app/optimizer/base.py` defines `Optimizer`. The only implementation today
  is `HighsOptimizer` (`app/optimizer/highs_solver.py`), selected by `SOLVER_BACKEND=highs` in
  `app/optimizer/factory.py`. Swapping to a different formulation or library means adding one new
  `Optimizer` subclass and one `elif` branch; nothing else in the service changes.

## The optimization model

The scenario is a small **linear program**, not a combinatorial/QUBO problem: every decision
(`grid_kwh`, `solar_used_kwh`, net battery flow) is a continuous, bounded real number, the
objective (`sum(grid_kwh[h] * tariff[h])`) is linear, and every rule (energy balance, battery
capacity/reserve, charge/discharge rate limits, solar cap, end-of-day neutrality, and every
operator directive) is a linear equality or inequality. `app/optimizer/model.py` folds every
applied directive into per-hour bounds; `app/optimizer/highs_solver.py` builds and solves the LP
with `scipy.optimize.linprog(method="highs")`.

One simplification worth knowing: each hour uses a single signed battery variable `net[h]`
(positive = charging, negative = discharging) instead of separate charge/discharge variables.
Because GridWise has no round-trip efficiency loss, charging and discharging in the same hour
can never help the objective, so the LP relaxation never wants to do both at once -- no binary
"charge XOR discharge" variables are needed, and the whole problem stays a clean, fast LP.

## Project layout

```
app/
  main.py              FastAPI app: GET /health, POST /optimize-energy
  schemas.py           Pydantic request/response models (exact API contract)
  directives.py        Directive type constants + NormalizedDirective
  guardrails.py        Deterministic validation of LLM output (Sec 08)
  replay.py            Final Validator: independently replays a plan
  llm/
    base.py            LLMInterpreter interface
    prompt.py          System/user prompt for directive extraction
    openrouter_client.py   OpenRouter implementation
    factory.py         LLM_PROVIDER -> concrete interpreter
  optimizer/
    base.py            Optimizer interface
    model.py           Scenario + directives -> effective per-hour bounds
    highs_solver.py     scipy/HiGHS LP implementation
    factory.py         SOLVER_BACKEND -> concrete optimizer
tests/
  data/public_sample_cases.json   organizer's public sample pack
  conftest.py           TestClient + ScriptedInterpreter (no network needed)
  test_public_samples.py   runs all 10 public cases through the real pipeline
  test_guardrails.py       guardrail fallback/normalization unit tests
```

## Setup & local run

Requires Python 3.10+.

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# edit .env: set OPENROUTER_API_KEY and OPENROUTER_MODEL

uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Check readiness:

```bash
curl http://localhost:8000/health
# {"status":"ok"}
```

Sample request (see `tests/data/public_sample_cases.json` for full worked cases):

```bash
curl -X POST http://localhost:8000/optimize-energy \
  -H "Content-Type: application/json" \
  -d '{
    "scenario_id": "GRID-101",
    "operator_notes": [
      "Solar output will drop to about 20% from 1 PM to 3 PM.",
      "Do not charge the battery between 2 PM and 4 PM.",
      "The cafeteria menu changes tomorrow."
    ],
    "hours": [ {"hour": 0, "demand_kwh": 180, "solar_kwh": 0, "tariff_bdt_per_kwh": 7}, "... 23 more ..." ],
    "battery": {
      "capacity_kwh": 500, "initial_energy_kwh": 200, "minimum_energy_kwh": 50,
      "max_charge_kwh_per_hour": 100, "max_discharge_kwh_per_hour": 100
    }
  }'
```

Response follows the exact `directive_interpretation` / `hourly_plan` schema from the Problem
Statement (Sec 10).

## Configuration (`.env`, see `.env.example`)

| Variable | Meaning |
|---|---|
| `LLM_PROVIDER` | `openrouter` (only implementation today) |
| `OPENROUTER_API_KEY` | Your OpenRouter key |
| `OPENROUTER_BASE_URL` | Default `https://openrouter.ai/api/v1` |
| `OPENROUTER_MODEL` | Any OpenRouter model slug, e.g. `anthropic/claude-haiku-4.5`, `openai/gpt-4.1-mini`, `google/gemini-2.5-flash` |
| `LLM_TIMEOUT_SECONDS` | Per-call timeout; keep well under the judge's 30s hard cutoff |
| `LLM_FAILURE_MODE` | `degrade` (default: provider failure -> treat notes as no_op, still return a valid 200 schedule) or `error` (return 500) |
| `SOLVER_BACKEND` | `highs` (only implementation today) |
| `PORT` | Server port, default 8000 |

## Design decisions worth knowing about

- **Malformed/hallucinated LLM output never crashes the service.** `app/guardrails.py`
  validates every field of every directive; anything that doesn't pass (unknown directive type,
  bad hour range, out-of-range factor, wrong `applies` semantics, ...) is downgraded to a safe
  `no_op` entry for that note rather than being forwarded to the optimizer or causing a 5xx.
- **A total LLM/provider outage degrades gracefully by default** (`LLM_FAILURE_MODE=degrade`):
  every note is treated as `no_op` and the service still returns a valid, correctly-optimized
  200 response for the base schedule. This trades that request's interpretation-accuracy score
  for a passing Performance & Reliability score. Flip to `LLM_FAILURE_MODE=error` if you'd rather
  fail loudly instead.
- **`plan_summary` is generated deterministically**, not by the LLM. The Problem Statement
  requires the LLM in the *operator-note interpretation* path, not in cosmetic text, so keeping
  `plan_summary` deterministic avoids an unnecessary extra model call (and its latency) without
  affecting the LLM requirement.
- **The Final Validator (`app/replay.py`) always runs before a response is sent**, independently
  recomputing energy balance, battery bounds, directive compliance, and the reported totals from
  the LP's own output. Under a correct LP model and organizer-guaranteed-feasible input this
  should never fail; if it ever does, the service returns a controlled 500 instead of shipping a
  wrong schedule.

## Testing

```bash
pytest
```

`tests/test_public_samples.py` runs the full guardrails -> optimizer -> replay pipeline against
all 10 organizer public sample cases, using a scripted stand-in for the LLM (so it needs no API
key/network) that replays each case's known-correct ground-truth interpretation. It checks the
returned schedule is valid and near-optimal-cost, matching the sample pack's
"equivalent optimal schedules are accepted" rule -- it does not check byte-for-byte equality with
the reference schedule. It also does **not** test LLM interpretation accuracy itself (that needs
a real model call); test that manually against `/optimize-energy` with `OPENROUTER_API_KEY` set,
or by pointing paraphrased notes at the running service.

`tests/test_guardrails.py` unit-tests the fallback/normalization logic directly (unknown
directive types, out-of-range values, unordered hours, etc).

## Docker

```bash
docker build -t gridwise-llm .
docker run -p 8000:8000 --env-file .env gridwise-llm
```

The image binds `0.0.0.0:${PORT}` (default 8000) and contains no baked-in secrets -- pass them
at run time via `--env-file` or `-e`.

## Known limitations

- LLM interpretation accuracy depends entirely on the configured model; this repo does not ship
  a fine-tuned or few-shot-heavy prompt, just a clear instruction + schema (`app/llm/prompt.py`).
- The LP assumes no battery round-trip efficiency loss, matching the Problem Statement's stated
  energy-balance equation exactly; if that assumption ever changes, `net[h]` would need to split
  back into separate charge/discharge variables with a small efficiency penalty.
