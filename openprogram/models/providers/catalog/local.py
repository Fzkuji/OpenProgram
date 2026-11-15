"""Explicit local-server identity; unrelated cloud providers retain auth requirements."""
from __future__ import annotations

LOCAL_PROVIDERS = {
    "ollama": ("Ollama", 11434),
    "lmstudio": ("LM Studio", 1234),
    "vllm": ("vLLM", 8000),
    "llamacpp": ("llama.cpp", 8080),
    "local": ("Local OpenAI-compatible server", 8000),
}


def is_local_provider(provider_id: str) -> bool:
    if provider_id in LOCAL_PROVIDERS:
        return True
    from ..settings.config_read import read_providers_config
    config = read_providers_config().get(provider_id) or {}
    return config.get("source") == "custom" and config.get("local") is True


def validate_local_url(url: str) -> None:
    from urllib.parse import urlsplit
    try:
        parts = urlsplit(url)
        port = parts.port
        if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment or (port is not None and port <= 0):
            raise ValueError
    except ValueError:
        raise ValueError("Local server URL must be an HTTP(S) URL without credentials, query or fragment") from None
