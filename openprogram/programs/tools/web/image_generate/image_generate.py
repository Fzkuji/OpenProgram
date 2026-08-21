"""image_generate tool — prompt → PNG saved to disk.

Per-backend differences (model IDs, size enums, response shapes) live
entirely in ``providers/*``. The tool itself handles:

  1. Picking a backend via registry.select().
  2. Asking that backend for ``GeneratedImage``s (bytes or URL).
  3. Writing each image to ``<output_dir>/<timestamp>_<i>.<ext>`` and
     returning absolute paths + metadata.

``output_dir`` defaults to ``$OPENPROGRAM_IMAGE_DIR`` (if set) or
``./generated_images/`` under the current working directory. Agents
can override per-call with the ``output_dir`` arg.
"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx

from openprogram.security.safe_http import safe_client
from openprogram.security.url_policy import normalize_origin

from openprogram.programs._helpers import read_int_param, read_string_param
from openprogram.programs._runtime import function
from ..image_analyze._encode import detect_raster_mime
from . import providers as _  # registers builtins  # noqa: F401
from .registry import GeneratedImage, registry


NAME = "image_generate"

DESCRIPTION = (
    "Generate one or more images from a text prompt and save them to "
    "disk. Pass `provider=openai|gemini|fal` to force a backend, else "
    "auto-selects by priority + availability. `size` accepts "
    "`1024x1024` / `512x512` / `1024x1792` / `1792x1024` / `768x1024` / "
    "`1024x768`; non-supporting providers map to the nearest aspect. "
    "Returns the saved file paths and the provider's echo of the prompt."
)


SPEC: dict[str, Any] = {
    "name": NAME,
    "description": DESCRIPTION,
    "parameters": {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "Text description of the image."},
            "model": {
                "type": "string",
                "description": "Provider-specific model id (e.g. dall-e-3, imagen-3.0-generate-002, fal-ai/flux/schnell). Omit to use the provider default.",
            },
            "size": {
                "type": "string",
                "description": "WxH like 1024x1024. Providers that only support aspect ratios map automatically.",
            },
            "n": {
                "type": "integer",
                "description": "Number of images to generate (default 1, max 4).",
            },
            "provider": {
                "type": "string",
                "description": "Force a specific backend: openai | gemini | fal.",
            },
            "output_dir": {
                "type": "string",
                "description": "Absolute directory to save to. Defaults to $OPENPROGRAM_IMAGE_DIR or ./generated_images/.",
            },
        },
        "required": ["prompt"],
    },
}


def _resolve_output_dir(override: str | None) -> Path:
    raw = override or os.environ.get("OPENPROGRAM_IMAGE_DIR") or "./generated_images"
    p = Path(raw).expanduser().resolve()
    from openprogram.sandbox import validate_write_path
    violation = validate_write_path(p)
    if violation:
        raise PermissionError(f"sandbox policy: {violation}")
    p.mkdir(parents=True, exist_ok=True)
    return p


def _ext_for_mime(mime: str) -> str:
    mime = (mime or "").lower()
    if "jpeg" in mime or "jpg" in mime:
        return ".jpg"
    if "webp" in mime:
        return ".webp"
    if "gif" in mime:
        return ".gif"
    if "bmp" in mime:
        return ".bmp"
    return ".png"


def _save(img: GeneratedImage, out_dir: Path, stem: str, idx: int) -> Path:
    from openprogram.sandbox import validate_write_path
    if img.data:
        mime = detect_raster_mime(img.data)
        if mime is None:
            raise RuntimeError("unsupported raster image bytes")
        target = out_dir / f"{stem}_{idx}{_ext_for_mime(mime)}"
        violation = validate_write_path(target)
        if violation:
            raise PermissionError(f"sandbox policy: {violation}")
        target.write_bytes(img.data)
    elif img.url:
        safe_error: RuntimeError | None = None
        with tempfile.TemporaryDirectory(
            prefix=f".{stem}_{idx}.", dir=out_dir
        ) as temporary_dir:
            staging = Path(temporary_dir) / "download"
            try:
                with safe_client("tool.image_result.download") as client:
                    client.download(img.url, staging)
            except httpx.HTTPStatusError as e:
                safe_error = RuntimeError(
                    "failed to download image result: "
                    f"HTTP {e.response.status_code} {e.response.reason_phrase} "
                    f"for {normalize_origin(img.url)}"
                )
            except httpx.RequestError as e:
                safe_error = RuntimeError(
                    "failed to download image result: "
                    f"{type(e).__name__} for {normalize_origin(img.url)}"
                )
            except Exception as e:
                raise RuntimeError(
                    f"failed to download image result: {type(e).__name__}: {e}"
                ) from e
            if safe_error is not None:
                raise safe_error from None
            with staging.open("rb") as downloaded:
                mime = detect_raster_mime(downloaded.read(12))
            if mime is None:
                raise RuntimeError("unsupported raster image bytes")
            target = out_dir / f"{stem}_{idx}{_ext_for_mime(mime)}"
            violation = validate_write_path(target)
            if violation:
                raise PermissionError(f"sandbox policy: {violation}")
            os.replace(staging, target)
    else:
        raise RuntimeError("GeneratedImage had neither bytes nor URL")
    return target


def _tool_check_fn() -> bool:
    return bool(registry.available())


def execute(
    prompt: str | None = None,
    model: str | None = None,
    size: str = "1024x1024",
    n: int = 1,
    provider: str | None = None,
    output_dir: str | None = None,
    **kw: Any,
) -> str:
    prompt = prompt or read_string_param(kw, "prompt", "text")
    model = model or read_string_param(kw, "model", "modelId")
    provider = provider or read_string_param(kw, "provider", "backend")
    size = read_string_param(kw, "size", default=size) or size
    n = read_int_param(kw, "n", "numImages", "count", default=n) or n
    output_dir = output_dir or read_string_param(kw, "output_dir", "outputDir")

    if not prompt:
        return "Error: `prompt` is required."

    try:
        backend = registry.select(prefer=provider)
    except LookupError as e:
        return f"Error: {e}"

    try:
        images = backend.generate(prompt, model=model, size=size, n=max(1, min(int(n), 4)))
    except Exception as e:
        return f"Error: {backend.name} generation failed: {type(e).__name__}: {e}"

    if not images:
        return f"Error: {backend.name} returned no images for prompt {prompt!r}."

    try:
        out_dir = _resolve_output_dir(output_dir)
    except PermissionError as e:
        return f"Error: {e}"
    stem = time.strftime("%Y%m%d_%H%M%S")
    saved: list[Path] = []
    for i, img in enumerate(images, 1):
        try:
            saved.append(_save(img, out_dir, stem, i))
        except Exception as e:
            return f"Error: {backend.name} save failed at image {i}: {e}"

    lines = [f"# image_generate (via {backend.name}, {len(saved)} image{'s' if len(saved) != 1 else ''})"]
    lines.append(f"output_dir: {out_dir}")
    if images[0].revised_prompt and images[0].revised_prompt != prompt:
        lines.append(f"revised_prompt: {images[0].revised_prompt!r}")
    lines.append("")
    for p in saved:
        lines.append(f"- {p}")
    return "\n".join(lines)



# Register as an AgentTool. ``execute`` stays a plain callable so any
# existing import-and-call sites keep working; the return value (an
# AgentTool) is discarded — it's already in the registry.
function(
    name=NAME,
    description=DESCRIPTION,
    parameters=SPEC["parameters"],
    toolset=['core'],
    check_fn=_tool_check_fn,
)(execute)

__all__ = ["NAME", "SPEC", "execute", "DESCRIPTION", "_tool_check_fn"]
