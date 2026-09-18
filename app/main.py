import logging

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app import guardrails, replay
from app.config import settings
from app.llm.base import LLMInterpreter, LLMInterpreterError
from app.llm.factory import get_interpreter
from app.optimizer.base import Optimizer, OptimizationInfeasibleError
from app.optimizer.factory import get_optimizer
from app.schemas import HealthResponse, OptimizeRequest, OptimizeResponse

logger = logging.getLogger("gridwise")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="GridWise LLM Energy Optimizer")


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"error": "malformed_request", "detail": exc.errors()})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"error": "internal_error"})


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok")


def _build_plan_summary(
    applied_directive_types: list[str], total_cost_bdt: float, peak_grid_kwh: float
) -> str:
    if applied_directive_types:
        directive_note = f"applying {', '.join(sorted(set(applied_directive_types)))}"
    else:
        directive_note = "with no active operator directives"
    return (
        f"Least-cost 24h schedule {directive_note}: total grid cost {total_cost_bdt:.2f} BDT, "
        f"peak hourly grid draw {peak_grid_kwh:.2f} kWh."
    )


@app.post("/optimize-energy", response_model=OptimizeResponse)
async def optimize_energy(
    payload: OptimizeRequest,
    interpreter: LLMInterpreter = Depends(get_interpreter),
    optimizer: Optimizer = Depends(get_optimizer),
) -> OptimizeResponse:
    raw_llm_output = None
    try:
        raw_llm_output = interpreter.interpret(payload.operator_notes)
    except LLMInterpreterError as exc:
        logger.warning("LLM interpreter failed: %s", exc)
        if settings.llm_failure_mode == "error":
            return JSONResponse(status_code=500, content={"error": "llm_provider_unavailable"})
        # degrade: fall through with raw_llm_output=None -> guardrails treats
        # every note as no_op, so the service still returns a valid schedule.

    directive_interpretation, applied_directives = guardrails.validate_and_normalize(
        raw_llm_output, payload.operator_notes, payload.battery.capacity_kwh
    )

    try:
        result = optimizer.solve(payload.hours, payload.battery, applied_directives)
    except OptimizationInfeasibleError as exc:
        logger.error("Optimization infeasible for scenario %s: %s", payload.scenario_id, exc)
        return JSONResponse(status_code=500, content={"error": "infeasible_scenario"})

    violations = replay.replay(
        payload.hours,
        payload.battery,
        applied_directives,
        result.hourly_plan,
        result.total_grid_kwh,
        result.total_cost_bdt,
        result.peak_grid_kwh,
    )
    if violations:
        logger.error("Internal replay check failed for scenario %s: %s", payload.scenario_id, violations)
        return JSONResponse(status_code=500, content={"error": "internal_validation_failed"})

    applied_types = [d.directive_type for d in applied_directives]
    plan_summary = _build_plan_summary(applied_types, result.total_cost_bdt, result.peak_grid_kwh)

    return OptimizeResponse(
        scenario_id=payload.scenario_id,
        directive_interpretation=directive_interpretation,
        hourly_plan=result.hourly_plan,
        total_grid_kwh=result.total_grid_kwh,
        total_cost_bdt=result.total_cost_bdt,
        peak_grid_kwh=result.peak_grid_kwh,
        plan_summary=plan_summary,
    )
