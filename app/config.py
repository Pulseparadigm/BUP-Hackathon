import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    llm_provider: str
    openrouter_api_key: str
    openrouter_base_url: str
    openrouter_model: str
    openrouter_site_url: str
    openrouter_site_name: str
    llm_timeout_seconds: float
    llm_failure_mode: str
    solver_backend: str
    port: int


def _bool_env(name: str, default: str) -> str:
    return os.getenv(name, default).strip().lower()


def load_settings() -> Settings:
    return Settings(
        llm_provider=os.getenv("LLM_PROVIDER", "openrouter").strip().lower(),
        openrouter_api_key=os.getenv("OPENROUTER_API_KEY", ""),
        openrouter_base_url=os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
        openrouter_model=os.getenv("OPENROUTER_MODEL", ""),
        openrouter_site_url=os.getenv("OPENROUTER_SITE_URL", ""),
        openrouter_site_name=os.getenv("OPENROUTER_SITE_NAME", "GridWise LLM"),
        llm_timeout_seconds=float(os.getenv("LLM_TIMEOUT_SECONDS", "15")),
        llm_failure_mode=_bool_env("LLM_FAILURE_MODE", "degrade"),
        solver_backend=os.getenv("SOLVER_BACKEND", "highs").strip().lower(),
        port=int(os.getenv("PORT", "8000")),
    )


settings = load_settings()
