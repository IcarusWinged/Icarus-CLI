"""NVIDIA API catalog (build.nvidia.com) - OpenAI-compatible API at https://integrate.api.nvidia.com/v1."""

from __future__ import annotations

from typing import AsyncIterator

from .. import client
from ..providers import Provider
from .base import Connector, ModelInfo

# the catalog also serves embedding / reranking / retrieval models that cannot chat
_NOT_CHAT = ("embed", "rerank", "retriever", "reward", "nvclip", "-clip", "parse", "guard", "safety")


class NvidiaConnector(Connector):
    id = "nvidia"

    async def list_models(self, provider: Provider, key: str) -> list[ModelInfo]:
        items = await client.list_openai_models(provider, key)
        ids = {client.clean(m["id"]) for m in items if client.valid_id(m.get("id"))}
        chat = [i for i in sorted(ids) if i and not any(word in i.lower() for word in _NOT_CHAT)]
        return [ModelInfo(i) for i in chat]  # the catalog does not say which models are free

    def stream_chat(
        self, provider: Provider, key: str, model: str, messages: list[dict], max_tokens: int = 2048
    ) -> AsyncIterator[str]:
        return client.stream_openai(provider, key, model, messages, max_tokens)
