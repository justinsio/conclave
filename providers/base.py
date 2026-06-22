from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class Completion:
    text: str
    prompt_tokens: int
    completion_tokens: int
    model: str


class LLMProvider(ABC):
    @abstractmethod
    async def complete(self, system: str, user: str) -> "Completion":
        """Return the model's completion (text + token usage) for a system+user prompt."""


class FakeProvider(LLMProvider):
    """Test double — returns queued responses in order, records calls."""
    def __init__(self, responses: list[str], *, prompt_tokens: int = 0,
                 completion_tokens: int = 0, model: str = "fake"):
        self._responses = list(responses)
        self._prompt_tokens = prompt_tokens
        self._completion_tokens = completion_tokens
        self._model = model
        self.calls: list[tuple[str, str]] = []

    async def complete(self, system: str, user: str) -> Completion:
        self.calls.append((system, user))
        text = self._responses.pop(0) if self._responses else "{}"
        return Completion(text=text, prompt_tokens=self._prompt_tokens,
                          completion_tokens=self._completion_tokens, model=self._model)
