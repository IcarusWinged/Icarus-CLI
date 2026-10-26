"""Provider definitions."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Provider:
    id: str  # internal id
    name: str  # display name
    key_alias: str  # name used in keys.json (shared with the `llm` CLI)
    env_var: str  # fallback environment variable
    base_url: str  # API root used for chat (OpenAI-style root, or Google's host for the google-genai SDK)
    models_url: str = ""  # OpenAI-style model list URL (unused by the Google connector)
    key_url: str = ""  # where a user creates a key
    key_prefixes: tuple = ()  # known key prefixes (used to stop keys being sent as chat)


PROVIDERS: dict[str, Provider] = {
    p.id: p
    for p in (
        Provider(
            "inception",
            "Inception Labs",
            "inception",
            "INCEPTION_API_KEY",
            "https://api.inceptionlabs.ai/v1",
            "https://api.inceptionlabs.ai/v1/models",
            key_url="platform.inceptionlabs.ai/dashboard/api-keys",
            key_prefixes=("sk-", "sk_"),
        ),
        Provider(
            "nvidia",
            "NVIDIA",
            "nvidia",
            "NVIDIA_API_KEY",
            "https://integrate.api.nvidia.com/v1",
            "https://integrate.api.nvidia.com/v1/models",
            key_url="build.nvidia.com/settings/api-keys",
            key_prefixes=("nvapi-",),
        ),
        Provider(
            "google",
            "Google AI",
            "gemini",
            "GEMINI_API_KEY",
            "https://generativelanguage.googleapis.com",
            key_url="aistudio.google.com/apikey",
            key_prefixes=("AIza",),
        ),
        Provider(
            "openrouter",
            "OpenRouter",
            "openrouter",
            "OPENROUTER_API_KEY",
            "https://openrouter.ai/api/v1",
            "https://openrouter.ai/api/v1/models",
            key_url="openrouter.ai/keys",
            key_prefixes=("sk-or-",),
        ),
    )
}
