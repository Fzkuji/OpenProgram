"""Probe OpenAI models API.

OpenAI's /v1/models returns only id — no capabilities. Reasoning
support is inferred from model id patterns (o1/o3/o4/gpt-5).
"""
from __future__ import annotations

_REASONING_PATTERNS = ("o1", "o3", "o4", "gpt-5")


def probe() -> dict[str, dict]:
    import httpx
    from openprogram.auth.resolver import resolve_api_key_sync

    key = resolve_api_key_sync("openai")
    if not key:
        return {}
    r = httpx.get("https://api.openai.com/v1/models", headers={"Authorization": f"Bearer {key}"}, timeout=15)
    if r.status_code != 200:
        return {}
    results = {}
    for m in r.json().get("data", []):
        mid = m.get("id", "")
        reasoning = any(t in mid for t in _REASONING_PATTERNS)
        results[mid] = {"reasoning": reasoning, "source": "inferred"}
    return results


if __name__ == "__main__":
    r = probe()
    reasoning = {k: v for k, v in r.items() if v.get("reasoning")}
    print(f"  {len(reasoning)}/{len(r)} models support reasoning (inferred)")
    for mid in sorted(reasoning)[:20]:
        print(f"    {mid}")
