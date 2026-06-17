"""Probe DeepSeek models API.

DeepSeek's /v1/models returns only id — no capabilities.
Reasoning inferred from model name.
"""
from __future__ import annotations


def probe() -> dict[str, dict]:
    import httpx
    from openprogram.auth.resolver import resolve_api_key_sync

    key = resolve_api_key_sync("deepseek")
    if not key:
        return {}
    r = httpx.get("https://api.deepseek.com/v1/models", headers={"Authorization": f"Bearer {key}"}, timeout=15)
    if r.status_code != 200:
        return {}
    results = {}
    for m in r.json().get("data", []):
        mid = m.get("id", "")
        reasoning = "reasoner" in mid or "r1" in mid or "thinking" in mid
        results[mid] = {"reasoning": reasoning, "source": "inferred"}
    return results


if __name__ == "__main__":
    r = probe()
    for mid, info in sorted(r.items()):
        tag = " [reasoning]" if info["reasoning"] else ""
        print(f"    {mid}{tag}")
