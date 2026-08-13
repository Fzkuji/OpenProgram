"""image_generate provider registry + contract.

Every backend returns one or more ``GeneratedImage`` objects — either
inline bytes (which we'll write to disk) or a URL (which we'll fetch
and save). That way the tool has a uniform file-path return no matter
which provider ran.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from ..._providers import ProviderRegistry


@dataclass
class GeneratedImage:
    """One image emitted by a provider.

    Exactly one of ``data`` or ``url`` is set; the tool then writes the
    bytes or fetches the URL into the user's output directory.
    """

    # png/jpeg bytes if the provider returned them inline (OpenAI b64,
    # Imagen bytesBase64Encoded). Empty when provider returned a URL.
    data: bytes = b""
    # Remote URL when the provider hosts the image; the tool downloads it.
    url: str = ""
    # Best-guess format from the provider response, used to pick an
    # extension when saving. Default to png.
    mime: str = "image/png"
    # Echo of the prompt that was actually sent (some providers rewrite
    # it). Useful for debugging / saving alongside the file.
    revised_prompt: str = ""
    # Free-form provider-specific metadata (model id, size, seed, …).
    extras: dict = field(default_factory=dict)


@runtime_checkable
class ImageGenerateProvider(Protocol):
    name: str
    priority: int
    requires_env: list[str]
    # Display-only list of model ids the provider supports — helps
    # agents pick a sensible model arg. Not enforced.
    supported_models: list[str]

    def is_available(self) -> bool: ...
    def generate(
        self,
        prompt: str,
        *,
        model: str | None = None,
        size: str = "1024x1024",
        n: int = 1,
    ) -> list[GeneratedImage]: ...


registry: ProviderRegistry[ImageGenerateProvider] = ProviderRegistry[ImageGenerateProvider](
    "image_generate"
)


__all__ = ["GeneratedImage", "ImageGenerateProvider", "registry"]
