"""image_analyze provider registry + contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from .._providers import ProviderRegistry


@dataclass
class ImageInput:
    """One image to analyse. Exactly one of ``path`` or ``url`` is set."""

    path: str = ""
    url: str = ""


@runtime_checkable
class ImageAnalyzeProvider(Protocol):
    name: str
    priority: int
    requires_env: list[str]

    def is_available(self) -> bool: ...
    def analyze(
        self,
        images: list[ImageInput],
        prompt: str,
        *,
        model: str | None = None,
    ) -> str: ...


registry: ProviderRegistry[ImageAnalyzeProvider] = ProviderRegistry[ImageAnalyzeProvider](
    "image_analyze"
)


__all__ = ["ImageInput", "ImageAnalyzeProvider", "registry"]
