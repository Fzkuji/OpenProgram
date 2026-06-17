"""Probe Google Gemini models API.

Gemini's models endpoint has no effort/thinking capabilities.
Reasoning inferred from model name patterns.
"""
from __future__ import annotations


def probe() -> dict[str, dict]:
    import httpx
    from openprogram.auth.resolver import resolve_api_key_sync

    key = resolve_api_key_sync("google")
    if not key:
        return {}
    r = httpx.get(f"https://generativelanguage.googleapis.com/v1beta/models?key={key}", timeout=15)
    if r.status_code != 200:
        return {}
    results = {}
    for m in r.json().get("models", []):
        mid = m.get("name", "").replace("models/", "")
        methods = m.get("supportedGenerationMethods", [])
        reasoning = "thinking" in mid or ("pro" in mid and "generateContent" in methods)
        results[mid] = {"reasoning": reasoning, "methods": methods, "source": "inferred"}
    return results


if __name__ == "__main__":
    r = probe()
    reasoning = {k: v for k, v in r.items() if v.get("reasoning")}
    print(f"  {len(reasoning)}/{len(r)} models support reasoning (inferred)")
    for mid in sorted(reasoning):
        print(f"    {mid}")
