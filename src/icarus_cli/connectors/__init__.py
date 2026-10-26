"""One connector per provider: inception.py, nvidia.py, google.py, openrouter.py."""

from __future__ import annotations

from ..providers import Provider
from .base import Connector, ModelInfo
from .google import GoogleConnector
from .inception import InceptionConnector
from .nvidia import NvidiaConnector
from .openrouter import OpenRouterConnector

CONNECTORS: dict[str, Connector] = {
    c.id: c for c in (InceptionConnector(), NvidiaConnector(), GoogleConnector(), OpenRouterConnector())
}


def get(provider: Provider) -> Connector:
    return CONNECTORS[provider.id]


__all__ = ["CONNECTORS", "Connector", "ModelInfo", "get"]
