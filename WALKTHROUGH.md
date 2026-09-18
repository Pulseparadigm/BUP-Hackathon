# GridWise LLM — 3-Minute Video Walkthrough Script

This is a recording script/outline for the submission's required 3-minute solution video, not
the video itself. Record screen + voice following this, then upload the video and put its link
in the submission form's "3 Min Video Demonstration" field.

Target: ~450-480 spoken words total (~150 wpm), so it fits in 3 minutes with room to breathe.
Each section below has a suggested on-screen cue and a suggested spoken script -- adjust to your
own voice, but keep the content, since it maps directly to what's graded (problem understanding,
architecture, LLM->guardrail->optimizer flow, key choices, run/test).

---

## 0:00-0:20 — The problem (~50 words)

**Show:** Problem Statement PDF title page, or the README's opening paragraph.

**Say:**
> "GridWise is a smart-campus energy scheduler. Every day we get 24 hours of demand, solar
> forecast, and grid tariff, plus one to three natural-language notes from campus operators --
> things like 'panels are being washed, solar drops to 25% at noon' or 'don't charge the battery
> between 2 and 4 PM.' Our service has to understand those notes with an LLM, and then produce
> the cheapest 24-hour grid/solar/battery schedule that still obeys them."

## 0:20-0:55 — Architecture overview (~90 words)

**Show:** The architecture diagram from the README (`Energy Data + Operator Notes -> LLM
Interpreter -> Guardrail Validator -> Math Optimizer -> Final Validator -> API Response`).

**Say:**
> "The pipeline is five stages. First, an LLM -- we're using Qwen 27B via OpenRouter -- reads
> the operator notes and turns them into structured directives: things like solar_reduction or
> no_charge_window. Second, a deterministic guardrail validator checks every field of that
> output -- unknown directive types, out-of-range values, bad hour ranges -- anything that
> doesn't pass gets downgraded to a safe no_op instead of reaching the optimizer. Third, the
> validated directives go into a linear program that finds the actual least-cost schedule.
> Fourth, before we ever respond, we independently replay the schedule to check it's really
> valid. Only then does the API respond."

## 0:55-1:40 — LLM -> guardrail -> optimizer flow, concretely (~110 words)

**Show:** A live `curl` call to `/optimize-energy` with a real public sample case (e.g.
`SAMPLE-01`), and its response -- specifically the `directive_interpretation` and
`total_cost_bdt` fields.

**Say:**
> "Here's a real example. The note says panel washing will leave solar at roughly 25% from noon
> to 2 PM, plus an unrelated note about a registration deadline. The LLM extracts a
> solar_reduction directive for hours 12 and 13 with factor 0.2, and correctly marks the second
> note as no_op since it doesn't affect today's schedule. The guardrail confirms both entries are
> well-formed. The optimizer then solves the LP -- it's a real linear program, not a heuristic,
> so given correct directives it finds the exact cost-optimal schedule, not just a good one. This
> response matches the organizer's own expected cost exactly."

## 1:40-2:25 — Key implementation choices (~110 words)

**Show:** `app/llm/base.py` / `app/optimizer/base.py` interfaces, or just talk over the
architecture diagram again.

**Say:**
> "A few choices worth calling out. Every stage is a swappable interface on purpose -- swapping
> the LLM model is just an environment variable, swapping providers or the solver means adding
> one class, no changes to the API layer. The battery uses a single signed variable per hour
> instead of separate charge and discharge variables -- since there's no round-trip efficiency
> loss, the LP relaxation never wants to do both in the same hour, so we get a clean, fast linear
> program without binary variables. And the whole system is designed for safe failure: malformed
> LLM output never crashes the service, and a total provider outage still returns a valid,
> correctly-optimized schedule -- it just treats every note as no_op instead of failing the
> request."

## 2:25-3:00 — How it's run and tested (~70 words)

**Show:** Terminal running `pytest`, and the README's "Setup & local run" section.

**Say:**
> "Locally, it's `pip install`, copy `.env.example` to `.env`, set an OpenRouter key, and
> `uvicorn app.main:app`. `pytest` runs the full guardrail-to-optimizer-to-replay pipeline
> against all ten of the organizer's public sample cases with a scripted stand-in for the LLM, so
> it needs no network access. We also built a live test runner that hits the real deployed
> endpoint with the real LLM to check interpretation accuracy end to end. It's deployed and
> reachable right now at the URL in the submission form."

---

## Recording checklist

- [ ] Keep it under 3:00 total -- the rubric only reviews the video for tie-breaks and does not
      score production quality, but it must be watchable within the time limit.
- [ ] Show, don't just narrate: a real `curl` request/response and the passing test suite are
      worth more than slides.
- [ ] Don't show real secret values (`OPENROUTER_API_KEY`) on screen -- use a redacted or fake
      value in `.env` if you show that file.
- [ ] Upload as MP4 or an organizer-accessible link, and confirm it's actually public before
      pasting the link into the submission form.
