#!/usr/bin/env python3
"""Probe provider APIs for thinking/reasoning capabilities.

For each major provider, queries the models API and extracts what
thinking/effort levels each model supports. Outputs a summary and
optionally updates the provider's thinking.json with model_overrides.

Usage:
    python scripts/probe_thinking_caps.py              # probe all
    python scripts/probe_thinking_caps.py anthropic    # probe one
    python scripts/probe_thinking_caps.py --update     # probe + write thinking.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROVIDERS_DIR = ROOT / "openprogram" / "providers"


def _resolve_key(provider_id: str) -> str | None:
    try:
        from openprogram.auth.resolver import resolve_api_key_sync
        return resolve_api_key_sync(provider_id)
    except Exception:
        return None


# ── Anthropic ────────────────────────────────────────────────────────────────

def probe_anthropic() -> dict[str, dict]:
    """Probe Anthropic /v1/models/{id} for effort capabilities."""
    import httpx

    key = _resolve_key("anthropic")
    if not key:
        print("  [skip] no anthropic key")
        return {}

    if key.startswith("sk-ant-oat"):
        headers = {
            "authorization": f"Bearer {key}",
            "anthropic-version": "2023-06-01",
            "anthropic-beta": "claude-code-20250219,oauth-2025-04-20",
            "user-agent": "claude-cli/2.1.62",
            "x-app": "cli",
        }
    else:
        headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}

    r = httpx.get("https://api.anthropic.com/v1/models", headers=headers, timeout=15)
    r.raise_for_status()
    models = r.json().get("data", [])

    results = {}
    for m in models:
        mid = m.get("id", "")
        if not mid.startswith("claude"):
            continue
        try:
            det = httpx.get(
                f"https://api.anthropic.com/v1/models/{mid}",
                headers=headers, timeout=10,
            )
            if det.status_code != 200:
                continue
            dj = det.json()
            caps = dj.get("capabilities", {})

            # effort levels
            effort = caps.get("effort", {})
            effort_supported = getattr(effort, "supported", False) if hasattr(effort, "supported") else effort.get("supported", False)
            levels = []
            if effort_supported:
                for lvl in ("minimal", "low", "medium", "high", "xhigh", "max"):
                    val = getattr(effort, lvl, None) if hasattr(effort, lvl) else effort.get(lvl)
                    if val is None:
                        continue
                    sup = getattr(val, "supported", False) if hasattr(val, "supported") else (val.get("supported", False) if isinstance(val, dict) else False)
                    if sup:
                        levels.append(lvl)

            # adaptive thinking
            thinking = caps.get("thinking", {})
            types = getattr(thinking, "types", None) if hasattr(thinking, "types") else thinking.get("types", {})
            adaptive = getattr(types, "adaptive", None) if hasattr(types, "adaptive") else (types.get("adaptive", {}) if isinstance(types, dict) else {})
            adaptive_ok = getattr(adaptive, "supported", False) if hasattr(adaptive, "supported") else (adaptive.get("supported", False) if isinstance(adaptive, dict) else False)

            results[mid] = {
                "effort_levels": levels,
                "adaptive": adaptive_ok,
                "context_window": dj.get("max_input_tokens"),
                "max_tokens": dj.get("max_tokens"),
            }
        except Exception as e:
            print(f"  [warn] {mid}: {e}")

    return results


# ── OpenAI ───────────────────────────────────────────────────────────────────

def probe_openai() -> dict[str, dict]:
    """Probe OpenAI /v1/models. Very limited — only returns id."""
    import httpx

    key = _resolve_key("openai")
    if not key:
        print("  [skip] no openai key")
        return {}

    r = httpx.get(
        "https://api.openai.com/v1/models",
        headers={"Authorization": f"Bearer {key}"},
        timeout=15,
    )
    if r.status_code != 200:
        print(f"  [skip] OpenAI models API returned {r.status_code}")
        return {}

    results = {}
    for m in r.json().get("data", []):
        mid = m.get("id", "")
        # OpenAI models API returns no capabilities, but we can infer
        # reasoning from model id patterns
        reasoning = any(t in mid for t in ("o1", "o3", "o4", "gpt-5", "gpt-4o"))
        results[mid] = {"reasoning": reasoning, "effort_levels": [], "source": "inferred"}

    return results


# ── DeepSeek ─────────────────────────────────────────────────────────────────

def probe_deepseek() -> dict[str, dict]:
    """Probe DeepSeek /v1/models."""
    import httpx

    key = _resolve_key("deepseek")
    if not key:
        print("  [skip] no deepseek key")
        return {}

    r = httpx.get(
        "https://api.deepseek.com/v1/models",
        headers={"Authorization": f"Bearer {key}"},
        timeout=15,
    )
    if r.status_code != 200:
        print(f"  [skip] DeepSeek returned {r.status_code}")
        return {}

    results = {}
    for m in r.json().get("data", []):
        mid = m.get("id", "")
        # DeepSeek API returns no capabilities; infer from name
        reasoning = "reasoner" in mid or "r1" in mid or "thinking" in mid
        results[mid] = {"reasoning": reasoning, "effort_levels": [], "source": "inferred"}

    return results


# ── OpenRouter ───────────────────────────────────────────────────────────────

def probe_openrouter() -> dict[str, dict]:
    """Probe OpenRouter — has supported_parameters."""
    import httpx

    key = _resolve_key("openrouter")
    if not key:
        print("  [skip] no openrouter key")
        return {}

    r = httpx.get(
        "https://openrouter.ai/api/v1/models",
        headers={"Authorization": f"Bearer {key}"},
        timeout=15,
    )
    if r.status_code != 200:
        print(f"  [skip] OpenRouter returned {r.status_code}")
        return {}

    results = {}
    for m in r.json().get("data", []):
        mid = m.get("id", "")
        sp = m.get("supported_parameters", [])
        reasoning = "reasoning" in sp
        results[mid] = {
            "reasoning": reasoning,
            "has_include_reasoning": "include_reasoning" in sp,
            "effort_levels": [],
            "source": "supported_parameters",
        }

    return results


# ── Google Gemini ────────────────────────────────────────────────────────────

def probe_google() -> dict[str, dict]:
    """Probe Google Gemini models API."""
    import httpx

    key = _resolve_key("google")
    if not key:
        print("  [skip] no google key")
        return {}

    r = httpx.get(
        f"https://generativelanguage.googleapis.com/v1beta/models?key={key}",
        timeout=15,
    )
    if r.status_code != 200:
        print(f"  [skip] Google returned {r.status_code}")
        return {}

    results = {}
    for m in r.json().get("models", []):
        mid = m.get("name", "").replace("models/", "")
        # Gemini API has supportedGenerationMethods but no effort info
        methods = m.get("supportedGenerationMethods", [])
        # "generateContent" models that have "thinking" in name support it
        reasoning = "thinking" in mid or "pro" in mid
        results[mid] = {
            "reasoning": reasoning,
            "methods": methods,
            "effort_levels": [],
            "source": "inferred",
        }

    return results


# ── Main ─────────────────────────────────────────────────────────────────────

PROBERS = {
    "anthropic": probe_anthropic,
    "openai": probe_openai,
    "deepseek": probe_deepseek,
    "openrouter": probe_openrouter,
    "google": probe_google,
}


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    update = "--update" in sys.argv

    providers = args if args else list(PROBERS.keys())

    for pid in providers:
        prober = PROBERS.get(pid)
        if not prober:
            print(f"[{pid}] no prober available")
            continue

        print(f"\n{'='*60}")
        print(f"Probing {pid}...")
        print(f"{'='*60}")

        try:
            results = prober()
        except Exception as e:
            print(f"  [error] {e}")
            continue

        if not results:
            print("  no results")
            continue

        # Summary
        reasoning_models = {k: v for k, v in results.items() if v.get("reasoning") or v.get("effort_levels")}
        print(f"  total models: {len(results)}")
        print(f"  with thinking/reasoning: {len(reasoning_models)}")

        for mid, info in sorted(reasoning_models.items()):
            levels = info.get("effort_levels", [])
            adaptive = info.get("adaptive")
            source = info.get("source", "api")
            extra = f" adaptive={adaptive}" if adaptive is not None else ""
            extra += f" [{source}]" if source != "api" else ""
            print(f"    {mid}: {levels or '(unknown levels)'}{extra}")

        # Update thinking.json if requested
        if update and pid == "anthropic" and reasoning_models:
            _update_anthropic_thinking_json(reasoning_models)


def _update_anthropic_thinking_json(results: dict[str, dict]):
    """Update anthropic/thinking.json with per-model overrides from API."""
    path = PROVIDERS_DIR / "anthropic" / "thinking.json"
    if not path.exists():
        print(f"  [skip] {path} not found")
        return

    with path.open() as f:
        spec = json.load(f)

    provider_levels = list(spec.get("effort_map", {}).keys())
    overrides = spec.setdefault("model_overrides", {})

    changed = False
    for mid, info in results.items():
        levels = info.get("effort_levels", [])
        if not levels:
            continue
        # If model supports fewer levels than provider default, add override
        if set(levels) != set(provider_levels):
            if mid not in overrides or set(overrides[mid].get("effort_map", {}).keys()) != set(levels):
                overrides[mid] = {
                    "effort_map": {lv: lv for lv in levels}
                }
                if info.get("adaptive") is False:
                    overrides[mid]["variant"] = "budget"
                changed = True
                print(f"  [update] {mid}: {levels}")

    if changed:
        with path.open("w") as f:
            json.dump(spec, f, indent=2, ensure_ascii=False)
            f.write("\n")
        print(f"  [saved] {path}")
    else:
        print("  [no changes]")


if __name__ == "__main__":
    main()
