"""Probe Anthropic /v1/models for thinking/effort capabilities.

Run directly: python -m openprogram.providers.anthropic.probe_thinking
Or import: from openprogram.providers.anthropic.probe_thinking import probe

Read-only: returns the probed capabilities. Programs never write the
git-tracked spec — the thinking config lives under provider.json's
``thinking`` key, edited by hand. The browse/refresh path consumes the
returned dict in memory (fetchers/__init__.py::_load_probe).
"""
from __future__ import annotations


def probe() -> dict[str, dict]:
    import httpx
    from openprogram.auth.resolver import resolve_api_key_sync

    key = resolve_api_key_sync("anthropic")
    if not key:
        return {}

    if key.startswith("sk-ant-oat"):
        headers = {
            "authorization": f"Bearer {key}",
            "anthropic-version": "2023-06-01",
            "anthropic-beta": "claude-code-20250219,oauth-2025-04-20",
            "user-agent": "claude-cli/2.1.62", "x-app": "cli",
        }
    else:
        headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}

    r = httpx.get("https://api.anthropic.com/v1/models", headers=headers, timeout=15)
    r.raise_for_status()

    results = {}
    for m in r.json().get("data", []):
        mid = m.get("id", "")
        if not mid.startswith("claude"):
            continue
        try:
            det = httpx.get(f"https://api.anthropic.com/v1/models/{mid}", headers=headers, timeout=10)
            if det.status_code != 200:
                continue
            caps = det.json().get("capabilities", {})
            effort = caps.get("effort", {})
            levels = []
            for lvl in ("minimal", "low", "medium", "high", "xhigh", "max"):
                val = getattr(effort, lvl, None) if hasattr(effort, lvl) else effort.get(lvl)
                if val and (getattr(val, "supported", False) if hasattr(val, "supported") else (val.get("supported") if isinstance(val, dict) else False)):
                    levels.append(lvl)
            thinking = caps.get("thinking", {})
            types = getattr(thinking, "types", None) if hasattr(thinking, "types") else thinking.get("types", {})
            adaptive = getattr(types, "adaptive", None) if hasattr(types, "adaptive") else (types.get("adaptive", {}) if isinstance(types, dict) else {})
            adaptive_ok = getattr(adaptive, "supported", False) if hasattr(adaptive, "supported") else (adaptive.get("supported") if isinstance(adaptive, dict) else False)
            results[mid] = {"effort_levels": levels, "adaptive": adaptive_ok}
        except Exception:
            pass

    return results


if __name__ == "__main__":
    r = probe()
    for mid, info in sorted(r.items()):
        print(f"  {mid}: {info.get('effort_levels')} adaptive={info.get('adaptive')}")
