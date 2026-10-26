"""Shared HTTP transport for the connectors (see connectors/), plus thin dispatch functions.

Hardened against a hostile / buggy server: bounded reads, bounded replies,
control-character stripping, key redaction, tolerant JSON handling.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, AsyncIterator
from urllib.parse import urlparse

import httpx

from .providers import Provider

if TYPE_CHECKING:  # pragma: no cover
    from .connectors.base import ModelInfo

MAX_REPLY_CHARS = 200_000  # stop a runaway generation
MAX_LINE_BYTES = 1_000_000  # one SSE line
MAX_STREAM_BYTES = 8_000_000  # whole streamed response
MAX_BODY_BYTES = 5_000_000  # non-streamed JSON bodies
MAX_PAGES = 50  # model-list pagination
# C0 (except \n \t), DEL, C1, and bidi override/isolate characters
_BAD = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]")
_BEARER = re.compile(r"(?i)bearer\s+\S+")


class APIError(Exception):
    pass


def clean(text: str) -> str:
    """Strip anything that could be interpreted by a terminal."""
    return _BAD.sub("", text)


def redact(text: str, key: str | None) -> str:
    if key:
        text = text.replace(key, "[redacted]")
    return _BEARER.sub("Bearer [redacted]", clean(text))


def _check_url(url: str) -> None:
    u = urlparse(url)
    if u.scheme != "https" and u.hostname not in ("127.0.0.1", "localhost", "::1"):
        raise APIError("refusing to send an API key over a non-HTTPS connection")


def _headers(key: str, extra: dict[str, str] | None = None) -> dict[str, str]:
    return {"Content-Type": "application/json", "Authorization": f"Bearer {key}", **(extra or {})}


def check_reply_size(total_chars: int) -> None:
    """Raise once a reply is longer than we are willing to hold."""
    if total_chars > MAX_REPLY_CHARS:
        raise APIError("reply exceeded the size limit and was cut off")


def _http() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(60, read=120), follow_redirects=False, verify=True)


async def _read_limited(resp: httpx.Response, limit: int) -> bytes:
    buf = bytearray()
    async for chunk in resp.aiter_bytes():
        buf += chunk
        if len(buf) > limit:
            raise APIError("response too large")
    return bytes(buf)


def _error_message(status: int, body: bytes, key: str) -> str:
    text = body[:4000].decode(errors="replace")
    try:
        err = json.loads(text)
        if isinstance(err, list) and err:
            err = err[0]
        msg = err.get("error", err) if isinstance(err, dict) else err
        msg = msg.get("message", msg) if isinstance(msg, dict) else msg
        text = msg if isinstance(msg, str) else json.dumps(msg)
    except Exception:
        pass
    return f"HTTP {status}: {redact(text, key)[:300]}"


async def _get_json(http: httpx.AsyncClient, url: str, key: str, headers: dict[str, str], params=None) -> dict:
    _check_url(url)
    async with http.stream("GET", url, headers=headers, params=params) as r:
        body = await _read_limited(r, MAX_BODY_BYTES)
        if r.status_code >= 400:
            raise APIError(_error_message(r.status_code, body, key))
    try:
        data = json.loads(body)
    except ValueError:
        raise APIError("server returned invalid JSON") from None
    return data if isinstance(data, dict) else {}


def valid_id(x) -> bool:
    return isinstance(x, str) and 0 < len(x) <= 200


async def list_openai_models(provider: Provider, key: str, extra_headers: dict[str, str] | None = None) -> list[dict]:
    """GET {models_url} on an OpenAI-style API and return the raw `data` entries that are objects."""
    async with _http() as http:
        data = await _get_json(http, provider.models_url, key, _headers(key, extra_headers))
    items = data.get("data")
    return [m for m in items if isinstance(m, dict)] if isinstance(items, list) else []


async def stream_openai(
    provider: Provider,
    key: str,
    model: str,
    messages: list[dict],
    max_tokens: int = 2048,
    extra_headers: dict[str, str] | None = None,
) -> AsyncIterator[str]:
    """Yield sanitised text deltas from an OpenAI-style streaming chat completion."""
    payload = {"model": model, "messages": messages, "stream": True, "max_tokens": max_tokens}
    url = provider.base_url.rstrip("/") + "/chat/completions"
    _check_url(url)
    total_chars = total_bytes = 0
    buf = b""
    async with _http() as http:
        async with http.stream("POST", url, headers=_headers(key, extra_headers), json=payload) as r:
            if r.status_code >= 400:
                raise APIError(_error_message(r.status_code, await _read_limited(r, 64_000), key))
            async for raw in r.aiter_bytes():
                total_bytes += len(raw)
                buf += raw
                if total_bytes > MAX_STREAM_BYTES:
                    raise APIError("reply exceeded the size limit and was cut off")
                if b"\n" not in buf and len(buf) > MAX_LINE_BYTES:
                    raise APIError("malformed response (line too long)")
                *lines, buf = buf.split(b"\n")
                for line in lines:
                    line = line.decode(errors="replace").strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        return
                    try:
                        chunk = json.loads(data)
                    except ValueError:
                        continue
                    if isinstance(chunk, list):
                        chunk = chunk[0] if chunk else None
                    if not isinstance(chunk, dict):
                        continue
                    choices = chunk.get("choices")
                    for choice in choices if isinstance(choices, list) else []:
                        delta = choice.get("delta") if isinstance(choice, dict) else None
                        text = delta.get("content") if isinstance(delta, dict) else None
                        if isinstance(text, str) and text:
                            text = clean(text)
                            total_chars += len(text)
                            check_reply_size(total_chars)
                            yield text


async def list_models(provider: Provider, key: str) -> "list[ModelInfo]":
    """Every chat model this key can use, from the provider's own connector."""
    from . import connectors

    return await connectors.get(provider).list_models(provider, key)


async def stream_chat(
    provider: Provider, key: str, model: str, messages: list[dict], max_tokens: int = 2048
) -> AsyncIterator[str]:
    """Yield sanitised text deltas from the provider's own connector."""
    from . import connectors

    async for chunk in connectors.get(provider).stream_chat(provider, key, model, messages, max_tokens):
        yield chunk
