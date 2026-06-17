"""Probe OpenRouter for thinking support via supported_parameters.

OpenRouter's models API returns supported_parameters per model — if
"reasoning" is in the list, the model supports thinking.
"""
from __future__ import annotations


def probe() -> dict[str, dict]:
    import httpx
    from openprogram.auth.resolver import resolve_api_key_sync

    key = resolve_api_key_sync("openrouter")
    if not key:
        return {}
    r = httpx.get("https://openrouter.ai/api/v1/models", headers={"Authorization": f"Bearer {key}"}, timeout=15)
    if r.status_code != 200:
        return {}
    results = {}
    for m in r.json().get("data", []):
        sp = m.get("supported_parameters", [])
        results[m["id"]] = {"reasoning": "reasoning" in sp, "source": "supported_parameters"}
    return results


if __name__ == "__main__":
    r = probe()
    reasoning = {k: v for k, v in r.items() if v.get("reasoning")}
    print(f"  {len(reasoning)}/{len(r)} models support reasoning")
    for mid in sorted(reasoning)[:20]:
        print(f"    {mid}")
