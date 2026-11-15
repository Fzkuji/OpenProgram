"""Combined web_search with the zero-key DuckDuckGo backend."""
from __future__ import annotations

import sys

from openprogram.programs.tools.web.web_search import web_search
from openprogram.programs.tools.web.web_search.providers import duckduckgo


_PAGE = b"""
<div class="result results_links results_links_deep web-result ">
  <h2 class="result__title"><a class="result__a"
    href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fgithub.com%2Fexample-org%2Fctxpress">example-org/ctxpress</a></h2>
  <a class="result__snippet" href="#">Context compression.</a>
</div>
"""


def test_race_with_only_duckduckgo_returns_results_without_ddgs(monkeypatch):
    # The reported call: provider=duckduckgo, combine=race, providers=[duckduckgo].
    monkeypatch.setitem(sys.modules, "ddgs", None)
    monkeypatch.setattr(duckduckgo, "get_bytes", lambda _url, **_kwargs: _PAGE)

    text = web_search.execute(
        query="https://github.com/example-org/ctxpress",
        num_results=5,
        provider="duckduckgo",
        combine="race",
        providers=["duckduckgo"],
    )

    assert text.startswith("# Web search:"), text
    assert "via race: duckduckgo, 1 results" in text
    assert "https://github.com/example-org/ctxpress" in text


def test_rrf_reports_duckduckgo_challenge_as_failure(monkeypatch):
    monkeypatch.setattr(
        duckduckgo,
        "get_bytes",
        lambda _url, **_kwargs: b'<div class="anomaly-modal">bots</div>',
    )

    text = web_search.execute(query="ctxpress", combine="rrf", providers=["duckduckgo"])

    assert text.startswith("Error: combine (rrf) failed:")
    assert "duckduckgo: DuckDuckGoPageError (DuckDuckGo answered with a bot challenge" in text
