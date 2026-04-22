"""image_analyze tool — describe / answer questions about image(s).

Takes a local path or remote URL (or a list of them) plus a prompt,
routes to a vision-capable provider, returns the answer as text.

This is an **auxiliary** vision call: the agent gets an answer
independent of its own chat model, which lets a text-only agent use
vision without switching providers. If the agent's chat model IS
vision-capable, passing images in the normal chat stream is cheaper.
"""

from __future__ import annotations

from typing import Any

from .._helpers import read_string_param
from . import providers as _  # registers builtins  # noqa: F401
from .registry import ImageInput, registry


NAME = "image_analyze"

DESCRIPTION = (
    "Analyse one or more images with a vision LLM. Pass `image_paths` "
    "(list of absolute paths) and/or `image_urls` (list of https URLs) "
    "plus a natural-language `prompt` describing what you want to know. "
    "Auto-picks openai | anthropic | gemini by priority + availability; "
    "override with `provider=`."
)


SPEC: dict[str, Any] = {
    "name": NAME,
    "description": DESCRIPTION,
    "parameters": {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "What to ask about the image(s). 'Describe this picture' / 'Extract the text' / 'Is there a cat?'",
            },
            "image_paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Absolute paths to local image files (png/jpg/gif/webp). Either this or image_urls is required.",
            },
            "image_urls": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Public HTTPS URLs pointing at images. Either this or image_paths is required.",
            },
            "provider": {
                "type": "string",
                "description": "Force a backend: openai | anthropic | gemini.",
            },
            "model": {
                "type": "string",
                "description": "Provider-specific model id (e.g. gpt-4o-mini, claude-3-5-haiku-20241022, gemini-1.5-flash). Omit for the provider default.",
            },
        },
        "required": ["prompt"],
    },
}


def _tool_check_fn() -> bool:
    return bool(registry.available())


def _coerce_list(v: Any) -> list[str]:
    if not v:
        return []
    if isinstance(v, str):
        return [v]
    if isinstance(v, (list, tuple)):
        return [str(x) for x in v if x]
    return []


def execute(
    prompt: str | None = None,
    image_paths: list[str] | str | None = None,
    image_urls: list[str] | str | None = None,
    provider: str | None = None,
    model: str | None = None,
    **kw: Any,
) -> str:
    prompt = prompt or read_string_param(kw, "prompt", "question", "text")
    provider = provider or read_string_param(kw, "provider", "backend")
    model = model or read_string_param(kw, "model")
    paths = _coerce_list(image_paths) + _coerce_list(kw.get("imagePaths") or kw.get("imagepaths"))
    urls = _coerce_list(image_urls) + _coerce_list(kw.get("imageUrls") or kw.get("imageurls"))

    if not prompt:
        return "Error: `prompt` is required."
    if not paths and not urls:
        return "Error: at least one of `image_paths` / `image_urls` must be provided."

    images: list[ImageInput] = [ImageInput(path=p) for p in paths] + [ImageInput(url=u) for u in urls]

    try:
        backend = registry.select(prefer=provider)
    except LookupError as e:
        return f"Error: {e}"

    try:
        answer = backend.analyze(images, prompt, model=model)
    except Exception as e:
        return f"Error: {backend.name} vision failed: {type(e).__name__}: {e}"

    header = f"# image_analyze (via {backend.name}, {len(images)} image{'s' if len(images) != 1 else ''})\n\n"
    return header + (answer or "(empty response)")


__all__ = ["NAME", "SPEC", "execute", "DESCRIPTION", "_tool_check_fn"]
