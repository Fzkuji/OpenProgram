"""Probe DeepSeek models for reasoning capability.

DeepSeek API returns id-only rows. Reasoning inferred from model name:
  - deepseek-v4-* → reasoning (5 levels: minimal/low/medium/high/max)
  - *reasoner* / *r1* → reasoning (no effort control)
  - deepseek-chat (V3) → no reasoning
"""
from __future__ import annotations


def probe() -> dict[str, dict]:
    import httpx
    from openprogram.providers.env_api_keys import resolve_provider_key

    key = resolve_provider_key("deepseek")
    if not key:
        return {}
    try:
        r = httpx.get(
            "https://api.deepseek.com/v1/models",
            headers={"Authorization": f"Bearer {key}"},
            timeout=15,
        )
        if r.status_code != 200:
            return {}
        results = {}
        for m in r.json().get("data", []):
            mid = m.get("id", "")
            reasoning = (
                "v4" in mid
                or "reasoner" in mid
                or "r1" in mid
                or "thinking" in mid
            )
            results[mid] = {"reasoning": reasoning, "source": "inferred"}
        return results
    except Exception:
        return {}


if __name__ == "__main__":
    r = probe()
    for mid, info in sorted(r.items()):
        tag = " [reasoning]" if info["reasoning"] else ""
        print(f"    {mid}{tag}")
