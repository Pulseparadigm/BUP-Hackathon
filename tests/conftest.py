import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.llm.base import LLMInterpreter
from app.llm.factory import get_interpreter
from app.main import app

DATA_PATH = Path(__file__).parent / "data" / "public_sample_cases.json"


class ScriptedInterpreter(LLMInterpreter):
    """Test double: returns a fixed directive list instead of calling a real
    provider. Lets the guardrail/optimizer/replay pipeline be tested without
    network access or an OPENROUTER_API_KEY.
    """

    def __init__(self, directives: list[dict[str, Any]]) -> None:
        self._directives = directives

    def interpret(self, operator_notes: list[str]) -> Any:
        return {"directives": self._directives}


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def use_scripted_interpreter():
    def _install(directives: list[dict[str, Any]]) -> None:
        app.dependency_overrides[get_interpreter] = lambda: ScriptedInterpreter(directives)

    yield _install
    app.dependency_overrides.pop(get_interpreter, None)


@pytest.fixture(scope="session")
def public_sample_cases() -> list[dict[str, Any]]:
    with open(DATA_PATH, encoding="utf-8") as f:
        pack = json.load(f)
    return pack["cases"]
