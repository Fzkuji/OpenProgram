"""web_search backend availability, DuckDuckGo parsing and unusable-provider errors."""
from __future__ import annotations

import sys

import pytest

from openprogram.programs.tools.web.web_search import web_search
from openprogram.programs.tools.web.web_search.combine import _resolve_provider_names
from openprogram.programs.tools.web.web_search.providers import duckduckgo
from openprogram.programs.tools.web.web_search.registry import registry


_RESULTS_PAGE = """
<html><body><div class="serp__results"><div class="results">
<div class="result results_links results_links_deep result--ad ">
  <div class="links_main links_deep result__body">
    <h2 class="result__title"><a class="result__a"
      href="https://duckduckgo.com/y.js?ad_domain=shop.example&amp;u3=x">Sponsored laptop</a></h2>
    <a class="result__snippet" href="https://duckduckgo.com/y.js?u3=x">Buy now</a>
  </div>
</div>
<div class="result results_links results_links_deep web-result ">
  <div class="links_main links_deep result__body">
    <h2 class="result__title"><a rel="nofollow" class="result__a"
      href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fgithub.com%2Fexample-org%2Fctxpress&amp;rut=abc">example-org/<b>ctxpress</b> &amp; friends</a></h2>
    <a class="result__url" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fgithub.com%2Fexample-org%2Fctxpress">github.com</a>
    <a class="result__snippet" href="//duckduckgo.com/l/?uddg=x">Context <b>compression</b>
      for   agents.</a>
  </div>
</div>
<div class="result results_links results_links_deep web-result ">
  <div class="links_main links_deep result__body">
    <h2 class="result__title"><a class="result__a" href="https://docs.example.org/guide">Guide</a></h2>
    <a class="result__snippet" href="https://docs.example.org/guide">Plain link.</a>
  </div>
</div>
<div class="result results_links results_links_deep web-result ">
  <h2 class="result__title"><a class="result__a"
    href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fgithub.com%2Fexample-org%2Fctxpress">Duplicate</a></h2>
</div>
<div class="result results_links results_links_deep web-result ">
  <h2 class="result__title"><a class="result__a"
    href="//duckduckgo.com/l/?uddg=javascript%3Aalert(1)">Script</a></h2>
</div>
</div></div></body></html>
"""


def test_duckduckgo_is_available_without_optional_package(monkeypatch):
    monkeypatch.setitem(sys.modules, "ddgs", None)

    assert duckduckgo.DuckDuckGoProvider().is_available()
    assert registry.availability_problem("duckduckgo") is None
    assert "duckduckgo" in [p.name for p in registry.available()]


def test_duckduckgo_parser_keeps_organic_hits_and_decodes_redirects():
    results = duckduckgo.parse_results(_RESULTS_PAGE, 10)

    assert [(r.title, r.url) for r in results] == [
        ("example-org/ctxpress & friends", "https://github.com/example-org/ctxpress"),
        ("Guide", "https://docs.example.org/guide"),
    ]
    assert results[0].snippet == "Context compression for agents."
    assert duckduckgo.parse_results(_RESULTS_PAGE, 1)[0].title.startswith("example-org")


def test_duckduckgo_parser_distinguishes_empty_challenge_and_unknown_pages():
    assert duckduckgo.parse_results('<div class="no-results">No results.</div>', 5) == []
    with pytest.raises(duckduckgo.DuckDuckGoPageError, match="bot challenge"):
        duckduckgo.parse_results('<div class="anomaly-modal__title">Select all ducks</div>', 5)
    with pytest.raises(duckduckgo.DuckDuckGoPageError, match="layout"):
        duckduckgo.parse_results("<html><body>Something else</body></html>", 5)


def test_duckduckgo_search_uses_the_managed_fixed_endpoint(monkeypatch):
    calls = []

    def get_bytes(url, **kwargs):
        calls.append((url, kwargs))
        return _RESULTS_PAGE.encode()

    monkeypatch.setattr(duckduckgo, "get_bytes", get_bytes)

    results = duckduckgo.DuckDuckGoProvider().search("ctxpress", num_results=3)

    assert [r.url for r in results][0] == "https://github.com/example-org/ctxpress"
    assert calls[0][0] == "https://html.duckduckgo.com/html/"
    assert calls[0][1]["params"] == {"q": "ctxpress"}


def test_explicit_unusable_providers_report_each_reason(monkeypatch):
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    monkeypatch.delenv("KIMI_API_KEY", raising=False)
    monkeypatch.delenv("MOONSHOT_API_KEY", raising=False)

    text = web_search.execute(
        query="ctxpress", combine="race", providers=["exa", "moonshot", "nope"]
    )

    assert text.startswith("Error: No usable web_search provider for combined search")
    assert "exa: not configured (needs EXA_API_KEY)" in text
    assert "moonshot: not configured (needs one of KIMI_API_KEY / MOONSHOT_API_KEY)" in text
    assert "nope: not a registered provider" in text
    assert "Usable now:" in text and "duckduckgo" in text
    assert "Settings > Search" in text


def test_explicit_provider_list_keeps_usable_duckduckgo(monkeypatch):
    monkeypatch.delenv("EXA_API_KEY", raising=False)

    assert _resolve_provider_names(["duckduckgo"]) == ["duckduckgo"]
    assert _resolve_provider_names(["exa", "duckduckgo"]) == ["duckduckgo"]


def test_explicit_single_provider_error_names_reason_and_alternatives(monkeypatch):
    monkeypatch.delenv("EXA_API_KEY", raising=False)

    text = web_search.execute(query="ctxpress", provider="exa")

    assert text.startswith("Error: web_search provider 'exa' is registered but not available")
    assert "not configured (needs EXA_API_KEY)" in text
    assert "Usable now:" in text and "duckduckgo" in text


class _Backend:
    priority = 1
    requires_env = ()

    def __init__(self, name, outcome):
        self.name = name
        self._outcome = outcome
        self.calls = 0

    def is_available(self):
        return True

    def search(self, query, num_results=8):
        self.calls += 1
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


def _hit(name):
    return [web_search.SearchResult(title=f"{name} hit", url=f"https://{name}.example/", snippet="s")]


def test_unpinned_search_falls_through_to_the_next_backend(monkeypatch):
    broken = _Backend("broken", RuntimeError("bot challenge"))
    working = _Backend("working", _hit("working"))
    monkeypatch.setattr(web_search.registry, "available", lambda: [broken, working])
    monkeypatch.setattr(
        "openprogram.state.preferences.read_search_default_provider", lambda: "broken"
    )

    text = web_search.execute(query="ctxpress")

    assert "via working" in text
    assert "working hit" in text
    assert "skipped: broken: RuntimeError: bot challenge" in text
    assert (broken.calls, working.calls) == (1, 1)


def test_unpinned_search_reports_when_every_backend_fails(monkeypatch):
    a = _Backend("a", RuntimeError("down"))
    b = _Backend("b", ValueError("422"))
    monkeypatch.setattr(web_search.registry, "available", lambda: [a, b])
    monkeypatch.setattr(
        "openprogram.state.preferences.read_search_default_provider", lambda: None
    )

    text = web_search.execute(query="ctxpress")

    assert text.startswith("Error: every web search backend failed")
    assert "a: RuntimeError: down" in text and "b: ValueError: 422" in text


def test_saved_default_goes_first_then_priority_order(monkeypatch):
    high = _Backend("high", _hit("high"))
    low = _Backend("low", _hit("low"))
    monkeypatch.setattr(web_search.registry, "available", lambda: [high, low])
    monkeypatch.setattr(
        "openprogram.state.preferences.read_search_default_provider", lambda: "low"
    )

    assert [p.name for p in web_search._fallback_order()] == ["low", "high"]
    assert "via low" in web_search.execute(query="ctxpress")
    assert high.calls == 0


def test_pinned_backend_does_not_fall_back(monkeypatch):
    broken = _Backend("broken", RuntimeError("bot challenge"))
    working = _Backend("working", _hit("working"))
    monkeypatch.setattr(web_search.registry, "available", lambda: [broken, working])
    monkeypatch.setattr(web_search.registry, "select", lambda prefer=None: broken)

    text = web_search.execute(query="ctxpress", provider="broken")

    assert text == "Error: broken search failed: RuntimeError: bot challenge"
    assert working.calls == 0


class _KeyBackend(_Backend):
    requires_env = ("BRAVE_API_KEY",)


class _Specialized(_Backend):
    specialized = True


def test_every_zero_key_backend_failing_says_how_to_fix_it(monkeypatch):
    ddg = _Backend("duckduckgo", RuntimeError("bot challenge"))
    monkeypatch.setattr(web_search.registry, "available", lambda: [ddg])
    monkeypatch.setattr(
        "openprogram.state.preferences.read_search_default_provider", lambda: None
    )

    text = web_search.execute(query="ctxpress")

    assert text.startswith("Error: every web search backend failed: duckduckgo: RuntimeError: bot challenge")
    assert text.endswith(
        "No search key is configured: add one in Settings > Search (Brave and "
        "Tavily have free tiers), or open a search page with your browser tools "
        "if you have them."
    )
    assert "retry later" not in text


def test_failing_key_backend_gets_no_missing_key_hint(monkeypatch):
    brave = _KeyBackend("brave", RuntimeError("401"))
    ddg = _Backend("duckduckgo", RuntimeError("bot challenge"))
    monkeypatch.setattr(web_search.registry, "available", lambda: [brave, ddg])
    monkeypatch.setattr(
        "openprogram.state.preferences.read_search_default_provider", lambda: None
    )

    text = web_search.execute(query="ctxpress")

    assert "brave: RuntimeError: 401" in text and "duckduckgo: RuntimeError" in text
    assert "No search key is configured" not in text


def test_pinned_failure_has_no_missing_key_hint(monkeypatch):
    broken = _Backend("duckduckgo", RuntimeError("bot challenge"))
    monkeypatch.setattr(web_search.registry, "select", lambda prefer=None: broken)

    text = web_search.execute(query="ctxpress", provider="duckduckgo")

    assert text == "Error: duckduckgo search failed: RuntimeError: bot challenge"


def test_unpinned_fallback_skips_arxiv_but_a_pin_reaches_it(monkeypatch):
    from openprogram.programs.tools.web.web_search.providers.arxiv import ArxivProvider

    assert ArxivProvider().specialized is True
    arxiv = _Specialized("arxiv", _hit("arxiv"))
    ddg = _Backend("duckduckgo", _hit("duckduckgo"))
    monkeypatch.setattr(web_search.registry, "available", lambda: [arxiv, ddg])
    monkeypatch.setattr(web_search.registry, "select", lambda prefer=None: arxiv)
    monkeypatch.setattr(
        "openprogram.state.preferences.read_search_default_provider", lambda: None
    )

    assert [p.name for p in web_search._fallback_order()] == ["duckduckgo"]
    assert "via duckduckgo" in web_search.execute(query="ctxpress")
    assert "via arxiv" in web_search.execute(query="ctxpress", provider="arxiv")


def test_registered_arxiv_is_not_in_the_real_fallback_order(monkeypatch):
    monkeypatch.setattr(
        "openprogram.state.preferences.read_search_default_provider", lambda: None
    )
    assert "arxiv" in [p.name for p in registry.available()]
    assert "arxiv" not in [p.name for p in web_search._fallback_order()]


def test_duckduckgo_challenge_points_at_settings():
    with pytest.raises(duckduckgo.DuckDuckGoPageError) as info:
        duckduckgo.parse_results('<div class="anomaly-modal__title">ducks</div>', 5)
    assert "retry later" not in str(info.value)
    assert "use a key-based provider (Settings > Search)" in str(info.value)
