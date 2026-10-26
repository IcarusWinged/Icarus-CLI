"""Google Gemini through the official `google-genai` SDK (pip install google-genai).

Models are listed with `client.aio.models.list()` and chat is `client.aio.models.generate_content_stream()`.

The API does not say which models have a free tier, so `FREE_TIER` below mirrors Google's pricing page
(https://ai.google.dev/gemini-api/docs/pricing, checked October 2026). Update it when that page changes;
a model that is not matched is shown under "Paid" because that is the safe assumption for billing.
"""

from __future__ import annotations

import re
from typing import AsyncIterator

from google import genai
from google.genai import errors, types

from .. import client
from ..providers import Provider
from .base import Connector, ModelInfo

MAX_MODELS = 1000

# Free-tier text models. An optional "-001" / "-latest" suffix is accepted. Gemma is open-weights and served free.
FREE_TIER = re.compile(
    r"^(?:"
    r"gemini-3\.8-flash|gemini-3\.6-flash|gemini-3\.5-flash-lite|gemini-3\.1-flash-lite(?:-preview)?|"
    r"gemini-3-flash-preview|gemini-2\.5-pro|gemini-2\.5-flash|gemini-2\.5-flash-lite|gemini-2\.0-flash(?:-lite)?"
    r")(?:-\d{3}|-latest)?$|^gemma-"
)

# generateContent also covers models that do not chat in text
NOT_TEXT_CHAT = (
    "tts", "live", "audio", "image", "embedding", "imagen", "veo", "lyria", "transcribe",
    "robotics", "computer-use", "omni", "deep-research", "antigravity", "aqa", "nano-banana", "translate",
)


def is_free(model_id: str) -> bool:
    return bool(FREE_TIER.match(model_id))


def is_text_chat(model_id: str) -> bool:
    return not any(word in model_id.lower() for word in NOT_TEXT_CHAT)


def to_contents(messages: list[dict]) -> tuple[str, list[types.Content]]:
    """OpenAI-style messages -> (system instruction, Gemini contents). Gemini calls the assistant role "model"."""
    system: list[str] = []
    contents: list[types.Content] = []
    for m in messages:
        text = m.get("content")
        if not isinstance(text, str) or not text:
            continue
        if m.get("role") == "system":
            system.append(text)
        else:
            role = "model" if m.get("role") == "assistant" else "user"
            contents.append(types.Content(role=role, parts=[types.Part(text=text)]))
    return "\n\n".join(system), contents


def chunk_texts(chunk: types.GenerateContentResponse) -> list[str]:
    """Visible text parts of one streamed chunk (thinking parts are skipped)."""
    out = []
    for cand in chunk.candidates or []:
        for part in (cand.content.parts if cand.content else None) or []:
            if isinstance(part.text, str) and part.text and not part.thought:
                out.append(client.clean(part.text))
    return out


def normalize_model(model: str) -> str:
    """Accept "models/x" as well as "x"; the SDK adds the "models/" prefix itself."""
    model = model.strip()
    return model.removeprefix("models/")


def api_error(exc: Exception, key: str) -> client.APIError:
    if isinstance(exc, errors.APIError):
        msg = client.redact(str(exc.message or exc.status or ""), key)[:300]
        if exc.code == 404:
            msg += " (model not found for this API key - it may be retired or not enabled; press F2 and pick another)"
        return client.APIError(f"HTTP {exc.code}: {msg}")
    return client.APIError(client.redact(str(exc) or type(exc).__name__, key)[:300])


class GoogleConnector(Connector):
    id = "google"

    def _sdk(self, provider: Provider, key: str) -> genai.Client:
        client._check_url(provider.base_url)
        options = types.HttpOptions(
            base_url=provider.base_url, timeout=120_000, async_client_args={"follow_redirects": False}
        )
        return genai.Client(api_key=key, http_options=options)

    async def list_models(self, provider: Provider, key: str) -> list[ModelInfo]:
        found: dict[str, ModelInfo] = {}
        sdk = self._sdk(provider, key)  # keep a reference: if the Client is garbage-collected it closes its HTTP session
        try:
            pager = await sdk.aio.models.list(config=types.ListModelsConfig(page_size=100))
            async for m in pager:
                name = m.name.removeprefix("models/") if isinstance(m.name, str) else ""
                if "generateContent" not in (m.supported_actions or []):
                    continue
                if client.valid_id(name) and is_text_chat(name):
                    label = m.display_name if isinstance(m.display_name, str) else ""
                    found[client.clean(name)] = ModelInfo(client.clean(name), client.clean(label)[:120], is_free(name))
                if len(found) >= MAX_MODELS:
                    break
        except client.APIError:
            raise
        except Exception as exc:
            raise api_error(exc, key) from None
        return [found[i] for i in sorted(found) if i]

    async def stream_chat(
        self, provider: Provider, key: str, model: str, messages: list[dict], max_tokens: int = 2048
    ) -> AsyncIterator[str]:
        system, contents = to_contents(messages)
        config = types.GenerateContentConfig(max_output_tokens=max_tokens, system_instruction=system or None)
        model = normalize_model(model)
        sdk = self._sdk(provider, key)
        total = 0
        try:
            try:
                stream = await sdk.aio.models.generate_content_stream(model=model, contents=contents, config=config)
                async for chunk in stream:
                    for text in chunk_texts(chunk):
                        total += len(text)
                        client.check_reply_size(total)
                        yield text
            except errors.APIError as exc:
                # Some models/keys reject the streaming endpoint with 404; retry once without streaming.
                if exc.code != 404 or total:
                    raise
                resp = await sdk.aio.models.generate_content(model=model, contents=contents, config=config)
                for text in chunk_texts(resp):
                    total += len(text)
                    client.check_reply_size(total)
                    yield text
        except client.APIError:
            raise
        except Exception as exc:
            raise api_error(exc, key) from None
