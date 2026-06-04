"""claude-code pins a fixed Meridian account (profile) via the
``x-meridian-profile`` header, so OpenProgram's Claude subscription is
decoupled from whatever the terminal ``claude auth login`` last logged
in. See docs/design/claude-code-meridian-profile.md.

The injection lives in ``openai_completions.stream_simple`` — the single
chokepoint every claude-code request passes through (the
``providers/stream.py`` wrapper is bypassed by some callers, e.g. memory
summarization). Tests cover the pure resolver/injector plus an
integration check that the header actually reaches the openai client.

Note: several provider modules are shadowed as package attributes, so we
grab the real module objects from ``sys.modules`` and patch by object.
"""
from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

import openprogram.providers.openai_completions.openai_completions  # noqa: F401
import openprogram.providers.anthropic._claude_max_proxy_registry  # noqa: F401
import openprogram.setup  # noqa: F401
from openprogram.providers.types import Context, Model, SimpleStreamOptions

_REG = sys.modules["openprogram.providers.anthropic._claude_max_proxy_registry"]
_OC = sys.modules["openprogram.providers.openai_completions.openai_completions"]
_SETUP = sys.modules["openprogram.setup"]


def _model(provider: str, api: str = "openai-completions") -> Model:
    return Model(
        id="claude-sonnet-4", name="x", api=api, provider=provider,
        base_url="http://localhost:3456/v1",
    )


# ── inject_profile_header (pure) ──────────────────────────────────────────

def test_injects_for_claude_code_when_pinned(monkeypatch):
    monkeypatch.setattr(_REG, "meridian_profile", lambda: "experiment")
    out = _REG.inject_profile_header(_model("claude-code"), {"a": "1"})
    assert out["x-meridian-profile"] == "experiment"
    assert out["a"] == "1"  # existing headers preserved


def test_no_header_when_no_profile(monkeypatch):
    monkeypatch.setattr(_REG, "meridian_profile", lambda: None)
    out = _REG.inject_profile_header(_model("claude-code"), None)
    assert "x-meridian-profile" not in out


def test_other_provider_never_injected(monkeypatch):
    # Even with a profile configured, a non-claude-code model must not get it.
    monkeypatch.setattr(_REG, "meridian_profile", lambda: "experiment")
    out = _REG.inject_profile_header(_model("openai"), None)
    assert "x-meridian-profile" not in out


def test_caller_header_wins(monkeypatch):
    monkeypatch.setattr(_REG, "meridian_profile", lambda: "experiment")
    out = _REG.inject_profile_header(
        _model("claude-code"), {"x-meridian-profile": "adhoc"},
    )
    assert out["x-meridian-profile"] == "adhoc"


def test_returns_fresh_dict(monkeypatch):
    monkeypatch.setattr(_REG, "meridian_profile", lambda: "experiment")
    src = {"a": "1"}
    out = _REG.inject_profile_header(_model("claude-code"), src)
    assert out is not src and "x-meridian-profile" not in src


# ── meridian_profile() resolution ─────────────────────────────────────────

def _cfg(profile):
    return {"providers": {"claude-code": {"meridian_profile": profile}}}


def test_resolve_config_wins_over_env(monkeypatch):
    monkeypatch.setattr(_SETUP, "_read_config", lambda: _cfg("acctA"))
    monkeypatch.setenv("CLAUDE_MAX_PROXY_PROFILE", "envP")
    assert _REG.meridian_profile() == "acctA"


def test_resolve_env_fallback(monkeypatch):
    monkeypatch.setattr(_SETUP, "_read_config", lambda: {"providers": {}})
    monkeypatch.delenv("MERIDIAN_PROFILE", raising=False)
    monkeypatch.setenv("CLAUDE_MAX_PROXY_PROFILE", "envP")
    assert _REG.meridian_profile() == "envP"


def test_resolve_none_when_unset(monkeypatch):
    monkeypatch.setattr(_SETUP, "_read_config", lambda: {})
    monkeypatch.delenv("CLAUDE_MAX_PROXY_PROFILE", raising=False)
    monkeypatch.delenv("MERIDIAN_PROFILE", raising=False)
    assert _REG.meridian_profile() is None


def test_resolve_non_string_config_does_not_crash(monkeypatch):
    # A hand-edited non-string value must not raise (and get swallowed).
    monkeypatch.setattr(_SETUP, "_read_config", lambda: _cfg(123))
    monkeypatch.delenv("CLAUDE_MAX_PROXY_PROFILE", raising=False)
    monkeypatch.delenv("MERIDIAN_PROFILE", raising=False)
    assert _REG.meridian_profile() == "123"  # coerced, not crashed


# ── integration: openai_completions actually sends the header ─────────────

def test_openai_completions_sends_header_for_claude_code(monkeypatch):
    captured: dict = {}

    class _FakeClient:
        def __init__(self, **kwargs):
            captured["default_headers"] = kwargs.get("default_headers")

            async def _create(**_):
                raise RuntimeError("stop-after-client-built")

            self.chat = SimpleNamespace(
                completions=SimpleNamespace(create=_create),
            )

    monkeypatch.setattr(_OC._openai, "AsyncOpenAI", _FakeClient)
    monkeypatch.setattr(_REG, "meridian_profile", lambda: "experiment")

    async def go() -> None:
        try:
            async for _ in _OC.stream_simple(
                _model("claude-code"), Context(messages=[]),
                SimpleStreamOptions(api_key="x"),
            ):
                pass
        except Exception:
            pass  # create aborts on purpose; client (+headers) already built

    asyncio.run(go())
    assert (captured.get("default_headers") or {}).get(
        "x-meridian-profile"
    ) == "experiment"
