"""ArXiv academic paper search provider.

ArXiv's public API at ``export.arxiv.org/api/query`` returns an Atom
feed of paper entries — no key needed, just rate-limited (the docs
recommend a 3-second sleep between requests, which we ignore at this
volume).

Pulled into OpenProgram because every other search backend skips
papers — Tavily / Brave / Google PSE all index ArXiv pages but
return citation snippets rather than abstract + author / metadata.
For ``research_agent`` and any literature-search workflow, hitting
ArXiv directly is the higher-signal source.

Borrowed concept from xynehq/websearch's Rust implementation.

Docs: https://info.arxiv.org/help/api/index.html
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass

from .._http import get_bytes
from ..registry import SearchResult


API_URL = "https://export.arxiv.org/api/query"
TIMEOUT = 25.0

# Atom feed namespaces (the ArXiv response uses both Atom and a custom
# arXiv-specific extension for primary_category / journal_ref).
_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
}


@dataclass
class ArxivProvider:
    name: str = "arxiv"
    # Below the general-web providers — ArXiv is only useful for
    # academic queries, but agents researching papers should be able
    # to flip ``provider=arxiv`` and skip the generalists entirely.
    priority: int = 30
    # No key required. Empty tuple keeps the catalog UI's "requires
    # env" chip from rendering a misleading row.
    requires_env: tuple = ()

    def is_available(self) -> bool:
        return True

    def search(self, query: str, *, num_results: int = 8) -> list[SearchResult]:
        body = get_bytes(
            API_URL,
            params={
                # ``all:`` searches title + abstract + authors. Without
                # a field qualifier ArXiv interprets the query as a
                # free-text match across everything.
                "search_query": f"all:{query}",
                "start": 0,
                "max_results": max(1, min(int(num_results), 50)),
                # Most-relevant first; alternative is
                # ``submittedDate desc``.
                "sortBy": "relevance",
                "sortOrder": "descending",
            },
            headers={"Accept": "application/atom+xml"},
            timeout=TIMEOUT,
            provider_label="ArXiv",
        )
        try:
            root = ET.fromstring(body)
        except ET.ParseError as e:
            raise RuntimeError(f"ArXiv response is not valid XML: {e}") from e

        results: list[SearchResult] = []
        for entry in root.findall("atom:entry", _NS):
            title_el = entry.find("atom:title", _NS)
            summary_el = entry.find("atom:summary", _NS)
            id_el = entry.find("atom:id", _NS)
            primary_cat_el = entry.find("arxiv:primary_category", _NS)
            published_el = entry.find("atom:published", _NS)

            authors = [
                (a.findtext("atom:name", default="", namespaces=_NS) or "").strip()
                for a in entry.findall("atom:author", _NS)
            ]
            authors = [a for a in authors if a]

            # ``<id>http://arxiv.org/abs/2401.01234v1</id>`` — keep the
            # version suffix as ArXiv serves it; the abs/<id> page
            # auto-redirects to the latest version anyway.
            url = (id_el.text if id_el is not None else "") or ""
            url = url.strip().split(" ")[0]

            title = " ".join((title_el.text or "").split()) if title_el is not None else ""
            summary = " ".join((summary_el.text or "").split()) if summary_el is not None else ""

            results.append(SearchResult(
                title=title,
                url=url,
                snippet=summary[:600],
                extras={
                    "authors": authors,
                    "primary_category": (
                        primary_cat_el.get("term") if primary_cat_el is not None else None
                    ),
                    "published": (published_el.text or "").strip() if published_el is not None else None,
                },
            ))
        return results
