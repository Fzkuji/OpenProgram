"""Probe OpenAI Codex (ChatGPT backend) models for reasoning capability.

OpenAI's models API returns id-only rows. Reasoning inferred from model name:
  - o1/o3/o4* → reasoning
  - gpt-5* → reasoning
  - everything else → no reasoning
"""
from __future__ import annotations


def probe() -> dict[str, dict]:
    from openprogram.providers.env_api_keys import resolve_provider_key
    from openprogram.security.safe_http import safe_client

    key = resolve_provider_key("openai-codex")
    if not key:
        return {}
    try:
        with safe_client("provider.fixed_api") as client:
            r = client.get(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {key}"},
                timeout=15,
            )
        if r.status_code != 200:
            return {}
        results = {}
        for m in r.json().get("data", []):
            mid = m.get("id", "")
            reasoning = any(mid.startswith(p) for p in ("o1", "o3", "o4", "gpt-5"))
            results[mid] = {"reasoning": reasoning, "source": "inferred"}
        return results
    except Exception:
        return {}


if __name__ == "__main__":
    r = probe()
    for mid, info in sorted(r.items()):
        tag = " [reasoning]" if info["reasoning"] else ""
        print(f"    {mid}{tag}")
