"""What every connector provides: a model list and a streaming chat."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import AsyncIterator

from ..providers import Provider


@dataclass(frozen=True)
class ModelInfo:
    id: str  # the exact string sent to the API
    name: str = ""  # friendly name, if the provider gives one
    free: bool | None = None  # True = free tier, False = paid only, None = provider does not say


class Connector(ABC):
    """One file per provider (inception.py, nvidia.py, google.py, openrouter.py)."""

    id: str  # matches Provider.id

    @abstractmethod
    async def list_models(self, provider: Provider, key: str) -> list[ModelInfo]:
        """Every chat-capable model the key can use, sorted by id."""

    @abstractmethod
    def stream_chat(
        self, provider: Provider, key: str, model: str, messages: list[dict], max_tokens: int = 2048
    ) -> AsyncIterator[str]:
        """Yield sanitised text deltas. `messages` are {"role": system|user|assistant, "content": str}."""
