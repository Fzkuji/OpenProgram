"""DuckDuckGo web search provider — zero-key fallback.

DuckDuckGo has no official web-results API. This provider reads the
no-JavaScript results page (``html.duckduckgo.com/html/``) through the
managed Runtime HTTP client, so it gets the same URL policy, proxy routing,
size and time limits as every key-based backend, and parses the page with the
standard library. It needs no key and no optional package, so it is always
available. One request returns the first results page (about ten hits).

The page is scraped, so treat it as best-effort: a layout change or a bot
challenge is reported as a provider error rather than as an empty search.
"""

from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

from .._http import get_bytes
from ..registry import SearchResult


API_URL = "https://html.duckduckgo.com/html/"
TIMEOUT = 20.0
# The no-JavaScript endpoint serves its normal page to an ordinary browser
# user agent; an unfamiliar agent is more likely to get the bot challenge.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"
)
_CHALLENGE_MARKERS = ("anomaly-modal", "/anomaly.js", "challenge-form")


class DuckDuckGoPageError(RuntimeError):
    """The results page was a bot challenge or could not be read."""

    # The message is a fixed sentence without peer text, safe to surface.
    safe_message = True


@dataclass
class DuckDuckGoProvider:
    name: str = "duckduckgo"
    priority: int = 10  # zero-key fallback, lowest quality of the builtins
    requires_env: tuple = ()

    def is_available(self) -> bool:
        return True

    def search(self, query: str, *, num_results: int = 8) -> list[SearchResult]:
        body = get_bytes(
            API_URL,
            params={"q": query},
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html",
                "Accept-Language": "en-US,en;q=0.9",
            },
            timeout=TIMEOUT,
            provider_label="DuckDuckGo",
        )
        return parse_results(
            body.decode("utf-8", errors="replace"),
            max(1, min(int(num_results), 25)),
        )


def parse_results(page: str, limit: int) -> list[SearchResult]:
    """Organic hits from a DuckDuckGo HTML results page, ads removed."""
    parser = _ResultsParser()
    parser.feed(page)
    parser.close()
    results: list[SearchResult] = []
    seen: set[str] = set()
    for hit in parser.hits:
        if hit.ad:
            continue
        url = _target_url(hit.href)
        title = _text(hit.title)
        if not url or not title or url in seen:
            continue
        seen.add(url)
        results.append(SearchResult(title=title, url=url, snippet=_text(hit.snippet)))
        if len(results) >= limit:
            break
    if results or parser.saw_no_results:
        return results
    lowered = page.lower()
    if any(marker in lowered for marker in _CHALLENGE_MARKERS):
        raise DuckDuckGoPageError(
            "DuckDuckGo answered with a bot challenge instead of results; "
            "use a key-based provider (Settings > Search)"
        )
    if not parser.saw_result_block:
        raise DuckDuckGoPageError(
            "DuckDuckGo returned a page without a results list; "
            "its layout may have changed"
        )
    return results


@dataclass
class _Hit:
    ad: bool
    href: str = ""
    title: list[str] | None = None
    snippet: list[str] | None = None


class _ResultsParser(HTMLParser):
    """Collects ``div.result`` blocks with their title link and snippet."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hits: list[_Hit] = []
        self.saw_result_block = False
        self.saw_no_results = False
        self._hit: _Hit | None = None
        self._field: str | None = None
        self._field_tag = ""
        self._nested = 0

    def handle_starttag(self, tag, attrs) -> None:
        values = dict(attrs)
        classes = set((values.get("class") or "").split())
        if self._field is not None:
            if tag == self._field_tag:
                self._nested += 1
            return
        if tag == "div" and "result" in classes and (
            "results_links" in classes or "web-result" in classes
        ):
            self.saw_result_block = True
            self._hit = _Hit(ad="result--ad" in classes, title=[], snippet=[])
            self.hits.append(self._hit)
            return
        if "no-results" in classes:
            self.saw_no_results = True
        if self._hit is None:
            return
        if tag == "a" and "result__a" in classes:
            self._hit.href = values.get("href") or ""
            self._begin("title", tag)
        elif "result__snippet" in classes:
            self._begin("snippet", tag)

    def handle_endtag(self, tag) -> None:
        if self._field is None or tag != self._field_tag:
            return
        if self._nested:
            self._nested -= 1
            return
        self._field = None

    def handle_data(self, data) -> None:
        if self._field is not None and self._hit is not None:
            getattr(self._hit, self._field).append(data)

    def _begin(self, field: str, tag: str) -> None:
        self._field = field
        self._field_tag = tag
        self._nested = 0


def _target_url(href: str) -> str:
    """Resolve DuckDuckGo's ``/l/?uddg=`` redirect; drop ads and internal links."""
    if href.startswith("//"):
        href = "https:" + href
    try:
        parts = urlsplit(href)
        host = (parts.hostname or "").lower()
        if host == "duckduckgo.com" or host.endswith(".duckduckgo.com"):
            if not parts.path.startswith("/l/"):
                return ""
            href = (parse_qs(parts.query).get("uddg") or [""])[0]
        if urlsplit(href).scheme.lower() not in {"http", "https"}:
            return ""
    except ValueError:
        return ""
    return href


def _text(parts: list[str] | None) -> str:
    return " ".join("".join(parts or ()).split())
