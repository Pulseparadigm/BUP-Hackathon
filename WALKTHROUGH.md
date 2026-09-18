# GridWise LLM — Project Walkthrough

A technical tour of how this service actually works, traced through a real request rather than
described in the abstract. For setup/run instructions see `README.md`; this document explains
the mechanics.

## 1. The scenario in one paragraph

Every request describes one campus's next 24 hours: hourly electricity demand, hourly solar
forecast, hourly grid tariff, and a battery's capacity/reserve/rate limits — plus 1-3
natural-language notes from a campus operator ("solar drops to 25% at noon for panel washing,"
"don't charge the battery 2-4 PM"). The service has to turn those notes into precise rules,
apply only the rules that actually matter, and hand back the cheapest 24-hour grid/solar/battery
schedule that still obeys every rule and every physical constraint. Some notes are distractors
("the cafeteria menu changes tomorrow") and must be recognized as irrelevant, not forced into a
directive they don't describe.

## 2. The five stages, at a glance

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

The core design idea, stated directly in the Problem Statement: **human notes are never trusted
as math.** They're converted to a fixed structured format, checked by deterministic code, and
only *then* fed to the solver. The LLM never sees demand/tariff/battery numbers and never touches
the optimization; the optimizer never sees natural language.

## 3. Tracing a real request end to end

This traces `SAMPLE-01` from the organizer's public sample pack, using the actual verified
response from the running service.

**Request** (`app/schemas.py: OptimizeRequest`):

```json
{
  "scenario_id": "SAMPLE-01",
  "operator_notes": [
    "Facilities will wash the rooftop solar panels from noon until 2 PM. During cleaning, usable solar should be treated as roughly 25% of the forecast.",
    "The sports office moved next month's registration deadline."
  ],
  "hours": [ /* 24 entries: demand_kwh, solar_kwh, tariff_bdt_per_kwh per hour */ ],
  "battery": {"capacity_kwh": 220, "initial_energy_kwh": 110, "minimum_energy_kwh": 40,
              "max_charge_kwh_per_hour": 50, "max_discharge_kwh_per_hour": 50}
}
```

### Stage 1 — LLM interpretation (`app/llm/openrouter_client.py`, `app/llm/prompt.py`)

`main.py` calls `interpreter.interpret(payload.operator_notes)` — note carefully, *only* the
notes are passed in, nothing else about the scenario. The system prompt (`app/llm/prompt.py`)
gives the model exactly six directive types it's allowed to use, the exact JSON shape each one
requires, the hour convention (start-inclusive, end-exclusive, 0-23), explicit numeric bounds
(e.g. `solar_reduction.factor` must be 0-1 inclusive), and an instruction to mark a note `no_op`
rather than guess when it's genuinely ambiguous. The call uses `temperature=0` and
`response_format={"type": "json_object"}` for determinism and valid JSON.

For this request, the model returns (paraphrased to the required shape):

```json
{"directives": [
  {"note_index": 0, "applies": true, "directive_type": "solar_reduction",
   "structured_adjustment": {"hours": [12, 13], "factor": 0.25},
   "explanation": "Panel washing from noon to 2 PM reduces usable solar to 25%..."},
  {"note_index": 1, "applies": false, "directive_type": "no_op",
   "structured_adjustment": null,
   "explanation": "Unrelated campus announcement; no effect on today's schedule."}
]}
```

Note hours `[12, 13]`, not `[12, 13, 14]` — "noon until 2 PM" is start-inclusive/end-exclusive
per the spec, so it covers hours 12 and 13 only.

One operational detail that matters here: the configured model (`qwen/qwen3.8-27b`) is a
*reasoning* model — it spends some of its token budget on invisible chain-of-thought before
writing the JSON answer. `openrouter_client.py` sends `extra_body={"reasoning": {"max_tokens":
N}}` to cap that separately from the visible response budget (`LLM_REASONING_MAX_TOKENS` in
`.env`); without this, an ambiguous note could exhaust the entire token budget on deliberation
and return empty content, which looks identical to a genuine provider failure. The OpenAI client
is also constructed with `max_retries=0` — the SDK's default of 2 automatic retries could
silently triple the effective latency of a single slow call, risking the judge's 30-second hard
timeout for no real benefit (a failed attempt already degrades safely, see Stage "failure path"
below).

### Stage 2 — Guardrail validation (`app/guardrails.py`)

The LLM's JSON is untrusted until this stage passes it. `validate_and_normalize()` walks every
`note_index` from `0` to `len(operator_notes) - 1` and, for each one:

- Confirms `directive_type` is one of the six known types.
- Confirms `applies` is `True` for real directives and `False` only for `no_op`.
- For non-`no_op` directives, confirms `structured_adjustment` is a dict, normalizes `hours` into
  a sorted set of unique integers 0-23 (silently fixing an unsorted or duplicated list — this is
  a deliberate normalization, not a rejection, since the same information is recoverable), and
  checks the type-specific required field is a finite number in its valid range (`factor` in
  `[0,1]`, `minimum_energy_kwh` in `[0, battery_capacity_kwh]`, `max_grid_kwh >= 0`).
- Anything that fails any check — wrong type, missing field, out-of-range value, a `note_index`
  the model never returned at all — becomes a safe `no_op` entry for *that specific note*,
  tagged with a guardrail-fallback explanation. One bad note never invalidates the others.

For `SAMPLE-01`, both entries pass validation unchanged: `solar_reduction` on hours `[12, 13]`
with `factor=0.25`, and `no_op` for note 1.

### Stage 3 — Building the LP's bounds (`app/optimizer/model.py`)

`build_effective_model()` folds the validated directives into per-hour arrays the solver can use
directly:

```python
effective_solar[12] *= 0.25   # solar_reduction hours 12-13, factor 0.25
effective_solar[13] *= 0.25
# reserve_floor, charge_cap, discharge_cap, grid_cap default to the battery's own limits
# and math.inf respectively, adjusted per-hour by any other applied directives
```

Nothing here is LLM-driven — every applied `NormalizedDirective` maps to one deterministic
per-hour bound update. `no_op` directives never reach this stage at all (they're filtered out in
Stage 2, since `validate_and_normalize` only returns `NormalizedDirective`s for real directives).

### Stage 4 — Solving the LP (`app/optimizer/highs_solver.py`)

The scenario becomes a linear program with 3 variables per hour (72 total for 24 hours):
`grid_kwh[h]`, `solar_used_kwh[h]`, and a single **signed** `net[h]` for the battery (positive =
charge, negative = discharge). The objective is `minimize sum(grid_kwh[h] * tariff[h])`. The
constraints are:

- **Energy balance per hour**: `grid[h] + solar_used[h] - net[h] = demand[h]`
- **Battery bounds**: the running sum of `net[0..h]` is bounded so `initial_energy + sum <=
  capacity` and `>= reserve_floor[h]` for every hour up to `h`
- **End-of-day neutrality**: `sum(net[0..23]) = 0` — the battery can shift energy between hours
  but can't be drained as a free one-time source by ending the day lower than it started
- **Rate limits**: `net[h]` is bounded by `[-discharge_cap[h], +charge_cap[h]]`
- **Solar cap**: `solar_used[h] <= effective_solar[h]`

This is solved to *global* optimality by `scipy.optimize.linprog(method="highs")` — not a
heuristic search. Why a single signed variable is enough: since there's no round-trip efficiency
loss in this challenge, charging and discharging in the same hour can never lower cost, so the LP
relaxation never wants to do both at once. That means no binary "charge XOR discharge" variable
is needed, and the whole problem stays a clean, fast continuous LP.

For `SAMPLE-01`, the solve returns `total_cost_bdt = 38365.0`, `total_grid_kwh = 2692.5`,
`peak_grid_kwh = 187.5` — an exact match to the organizer's own expected output for this case.

### Stage 5 — Independent replay (`app/replay.py`)

Before the response is ever sent, `replay.replay()` recomputes energy balance, battery bounds and
transitions, effective solar usage, and the reported totals *independently* from the solver's own
output, and checks every applied directive was actually honored in the final `hourly_plan`. Under
a correctly-modeled LP and organizer-guaranteed-feasible input this should never disagree with
the solver — if it ever does, the service returns a controlled `500` rather than shipping a
schedule it can't verify itself.

### The failure path

If the LLM call fails outright (timeout, malformed JSON, empty content, provider outage),
`main.py` catches the `LLMInterpreterError`, and with the default `LLM_FAILURE_MODE=degrade`,
every note for that request is treated as `no_op` — the service still returns a valid,
correctly-optimized `200` for the base schedule (no directives applied), trading that one
request's interpretation score for a passing reliability score. Malformed *request* input (bad
JSON, out-of-range values, `Infinity`/`NaN`, a self-contradictory battery config) is rejected at
the schema layer with a clean `400`, never reaching the LLM or the optimizer at all.

## 4. Module tour

```
app/
  main.py              FastAPI app: GET /health, POST /optimize-energy; wires the 4 stages
                        together; the only place that decides HTTP status codes.
  schemas.py            Pydantic request/response models -- the exact API contract, including
                        cross-field validation (e.g. battery minimum <= initial <= capacity)
                        and allow_inf_nan=False on every numeric field.
  directives.py         The 6 directive-type constants and NormalizedDirective, the internal
                        shape a validated directive takes before reaching the optimizer.
  guardrails.py         Stage 2: deterministic validation of raw LLM output (Sec 08 of the
                        Problem Statement).
  replay.py             Stage 5: independently replays a finished plan to verify it.
  llm/
    base.py             LLMInterpreter interface -- one method, interpret(notes) -> raw JSON.
    prompt.py           The system/user prompt that drives directive extraction.
    openrouter_client.py   The only implementation today: calls OpenRouter's OpenAI-compatible
                        chat API, handles reasoning-token budgeting for thinking models.
    factory.py           LLM_PROVIDER env var -> concrete interpreter class.
  optimizer/
    base.py             Optimizer interface -- one method, solve(hours, battery, directives).
    model.py            Turns a scenario + directives into per-hour bounds (Stage 3).
    highs_solver.py     The only implementation today: builds and solves the LP with
                        scipy/HiGHS (Stage 4).
    factory.py           SOLVER_BACKEND env var -> concrete optimizer class.
tests/
  data/public_sample_cases.json   mirror of the organizer's 10 public cases
  conftest.py            TestClient + ScriptedInterpreter (a fake LLMInterpreter that replays a
                        fixed, known-correct directive list -- no network needed)
  test_public_samples.py   all 10 public cases through the real guardrail/optimizer/replay
                        pipeline, via the scripted interpreter
  test_guardrails.py     guardrail fallback/normalization unit tests
scripts/
  test_live_samples.py   runs the 10 public cases against a *running* server with the *real*
                        LLM, logging OpenRouter balance before/after (balance_log.jsonl)
  run_nlp_test_cases.py  drives the larger nlp_test_cases.py case bank (directive accuracy,
                        malformed requests, guardrail edge cases) against a running server
```

## 5. Why the "swappable interface" design matters here

Both the model provider and the solving method are things the team expected to change during the
event, so both are behind a one-method interface (`LLMInterpreter.interpret`,
`Optimizer.solve`) selected by an env var (`LLM_PROVIDER`, `SOLVER_BACKEND`) through a small
factory function. Concretely: changing `OPENROUTER_MODEL` in `.env` is a zero-code-change model
swap (this project moved from `anthropic/claude-haiku-4.5` to `qwen/qwen3.8-27b` this way).
Swapping to a different provider entirely, or a different solving library, means writing one new
class and adding one `elif` branch in the relevant `factory.py` — `main.py` and `guardrails.py`
never change either way.

## 6. Testing strategy — two layers, deliberately

- **`pytest` (scripted, offline)**: `ScriptedInterpreter` in `conftest.py` stands in for the real
  LLM and replays each sample case's *known-correct* ground-truth interpretation. This tests
  everything downstream of interpretation — guardrail normalization, optimizer correctness,
  replay validation, response schema — without needing network access or an API key. It's what
  proves the optimizer itself reaches the organizer's exact expected cost on all 10 public
  samples, isolated from any LLM variance.
- **`scripts/test_live_samples.py` / `scripts/run_nlp_test_cases.py` (live, real LLM)**: hits a
  *running* server with the real model over the real network, which is the only way to actually
  measure interpretation accuracy, latency, and end-to-end reliability. These are the tools used
  to catch and verify fixes for issues that only show up with a real model in the loop (e.g. a
  reasoning model exhausting its token budget on an ambiguous note, or latency variance from SDK
  auto-retries).

## 7. Known limitations (honest, not hidden)

- **Battery-capacity-as-percentage notes** (e.g. "keep 50% of battery capacity in reserve") can't
  currently resolve to the correct absolute kWh value, because `interpret()` is only ever given
  the operator notes — never the battery's `capacity_kwh` — so the LLM has no number to compute
  50% *of*. This is a real, reproducible gap (confirmed against the organizer's own
  `SAMPLE-03` case), not a model-quality issue; fixing it means extending the `LLMInterpreter`
  interface to also receive battery capacity.
- LLM interpretation accuracy depends on the configured model; this repo ships a clear
  instruction + schema, not a fine-tuned or heavily few-shot prompt.
- The LP assumes no battery round-trip efficiency loss, matching the Problem Statement's stated
  energy-balance equation exactly; if that assumption changes, `net[h]` would need to split back
  into separate charge/discharge variables with a small efficiency penalty.
