"""Inception Labs (Mercury diffusion LLMs) - OpenAI-compatible API at https://api.inceptionlabs.ai/v1."""

from __future__ import annotations

from typing import AsyncIterator

from .. import client
from ..providers import Provider
from .base import Connector, ModelInfo


class InceptionConnector(Connector):
    id = "inception"

    async def list_models(self, provider: Provider, key: str) -> list[ModelInfo]:
        items = await client.list_openai_models(provider, key)
        ids = {client.clean(m["id"]) for m in items if client.valid_id(m.get("id"))}
        return [ModelInfo(i) for i in sorted(ids) if i]  # Inception does not publish a free tier in the API

    def stream_chat(
        self, provider: Provider, key: str, model: str, messages: list[dict], max_tokens: int = 2048
    ) -> AsyncIterator[str]:
        return client.stream_openai(provider, key, model, messages, max_tokens)
