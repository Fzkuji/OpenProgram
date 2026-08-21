"""FAL image-generation provider (Flux, Recraft, Ideogram, …).

FAL exposes dozens of community-hosted image models under a uniform
queue API. We default to flux-schnell (fast + free-tier-friendly);
agents can pass ``model="fal-ai/flux/dev"`` or any other route.

Docs: https://docs.fal.ai/model-endpoints/
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

from openprogram.programs.tools.web.web_search._http import get_json, post_json

from ..registry import GeneratedImage


QUEUE_BASE = "https://queue.fal.run"
TIMEOUT = 120.0
POLL_INTERVAL = 1.5
DEFAULT_MODEL = "fal-ai/flux/schnell"


@dataclass
class FalProvider:
    name: str = "fal"
    priority: int = 70
    requires_env: tuple = ("FAL_KEY",)
    supported_models: list[str] = field(default_factory=lambda: [
        "fal-ai/flux/schnell",
        "fal-ai/flux/dev",
        "fal-ai/flux-pro",
        "fal-ai/ideogram/v2",
        "fal-ai/recraft-v3",
    ])

    def is_available(self) -> bool:
        return bool(os.environ.get("FAL_KEY"))

    def generate(
        self,
        prompt: str,
        *,
        model: str | None = None,
        size: str = "1024x1024",
        n: int = 1,
    ) -> list[GeneratedImage]:
        key = os.environ.get("FAL_KEY", "")
        if not key:
            raise RuntimeError("FAL_KEY not set")
        mdl = model or DEFAULT_MODEL
        w, h = _parse_size(size)
        payload = {
            "prompt": prompt,
            "image_size": {"width": w, "height": h},
            "num_images": max(1, min(int(n), 4)),
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Key {key}",
        }

        # Queue submit → poll until completed → fetch response
        submit = post_json(
            f"{QUEUE_BASE}/{mdl}",
            body=payload,
            headers=headers,
            timeout=TIMEOUT,
            provider_label="FAL submit",
            consumer="tool.image_api.fixed",
        )

        status_url = submit.get("status_url")
        response_url = submit.get("response_url")
        if not status_url or not response_url:
            raise RuntimeError("FAL response missing queue URLs") from None

        # Poll
        deadline = time.time() + TIMEOUT
        while time.time() < deadline:
            time.sleep(POLL_INTERVAL)
            status = get_json(
                status_url,
                headers=headers,
                timeout=30,
                provider_label="FAL status",
                consumer="tool.image_api.fixed",
            )
            st = status.get("status")
            if st == "COMPLETED":
                break
            if st in ("FAILED", "CANCELLED"):
                raise RuntimeError(f"FAL job {st}") from None
        else:
            raise RuntimeError("FAL job timed out")

        # Fetch final result
        result = get_json(
            response_url,
            headers=headers,
            timeout=TIMEOUT,
            provider_label="FAL result",
            consumer="tool.image_api.fixed",
        )

        out: list[GeneratedImage] = []
        for img in result.get("images", []):
            url = str(img.get("url") or "")
            if not url:
                continue
            out.append(GeneratedImage(
                url=url,
                mime=str(img.get("content_type", "image/png")),
                revised_prompt=prompt,
                extras={"model": mdl, "width": img.get("width"), "height": img.get("height")},
            ))
        return out


def _parse_size(size: str) -> tuple[int, int]:
    try:
        w, h = size.lower().split("x")
        return max(256, int(w)), max(256, int(h))
    except Exception:
        return 1024, 1024
