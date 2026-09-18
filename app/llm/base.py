from abc import ABC, abstractmethod
from typing import Any


class LLMInterpreterError(Exception):
    """Raised when the provider call itself fails (timeout, auth, 5xx, ...)."""


class LLMInterpreter(ABC):
    """Turns operator notes into raw candidate directive JSON.

    Implementations must NOT validate business rules (allowed types, hour
    ranges, applies-semantics, ...) -- that is app.guardrails' job. This
    layer only has to get *a* response out of the model and hand back
    whatever structured content it produced, so guardrails can decide what
    to trust.
    """

    @abstractmethod
    def interpret(self, operator_notes: list[str]) -> Any:
        """Return provider output describing one directive per note.

        Expected shape (loosely -- guardrails re-validates everything):
        a list of dicts, or a dict with a "directives" list, each entry
        roughly matching the directive_interpretation schema.

        Raises LLMInterpreterError on provider/transport failure.
        """
        raise NotImplementedError
