from functools import lru_cache

from app.config import Settings, settings
from app.llm.base import LLMInterpreter
from app.llm.openrouter_client import OpenRouterInterpreter


def build_interpreter(cfg: Settings) -> LLMInterpreter:
    if cfg.llm_provider == "openrouter":
        return OpenRouterInterpreter(
            api_key=cfg.openrouter_api_key,
            base_url=cfg.openrouter_base_url,
            model=cfg.openrouter_model,
            timeout_seconds=cfg.llm_timeout_seconds,
            site_url=cfg.openrouter_site_url,
            site_name=cfg.openrouter_site_name,
        )

    # To add another provider: implement LLMInterpreter in app/llm/, then add
    # an elif branch here keyed off LLM_PROVIDER. main.py and guardrails
    # never need to change.
    raise ValueError(f"Unknown LLM_PROVIDER: {cfg.llm_provider!r}")


@lru_cache(maxsize=1)
def get_interpreter() -> LLMInterpreter:
    return build_interpreter(settings)
