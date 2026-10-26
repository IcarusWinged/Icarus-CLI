"""OpenRouter - OpenAI-compatible API at https://openrouter.ai/api/v1.

`GET /models` returns each model's `pricing` (USD per token as strings), so free models are exactly the ones
whose prompt and completion prices are both zero (these ids usually end in `:free`).
"""

from __future__ import annotations

from typing import AsyncIterator

from .. import client
from ..providers import Provider
from .base import Connector, ModelInfo

APP_HEADERS = {"X-Title": "icarus-cli"}  # optional attribution header OpenRouter shows in its dashboard


def _zero(value) -> bool:
    try:
        return float(value) == 0.0
    except (TypeError, ValueError):
        return False


def _is_free(item: dict) -> bool:
    pricing = item.get("pricing")
    if isinstance(pricing, dict) and "prompt" in pricing and "completion" in pricing:
        return _zero(pricing["prompt"]) and _zero(pricing["completion"])
    return str(item.get("id", "")).endswith(":free")


def _outputs_text(item: dict) -> bool:
    arch = item.get("architecture")
    outputs = arch.get("output_modalities") if isinstance(arch, dict) else None
    return "text" in outputs if isinstance(outputs, list) else True


class OpenRouterConnector(Connector):
    id = "openrouter"

    async def list_models(self, provider: Provider, key: str) -> list[ModelInfo]:
        items = await client.list_openai_models(provider, key, APP_HEADERS)
        found: dict[str, ModelInfo] = {}
        for m in items:
            if client.valid_id(m.get("id")) and _outputs_text(m):
                name = m.get("name")
                label = client.clean(name)[:120] if isinstance(name, str) else ""
                found[client.clean(m["id"])] = ModelInfo(client.clean(m["id"]), label, _is_free(m))
        return [found[i] for i in sorted(found) if i]

    def stream_chat(
        self, provider: Provider, key: str, model: str, messages: list[dict], max_tokens: int = 2048
    ) -> AsyncIterator[str]:
        return client.stream_openai(provider, key, model, messages, max_tokens, APP_HEADERS)
