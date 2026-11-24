"""Get the body of an arXiv paper as sections with locators. Plain code, no model calls.

arXiv serves HTML for most papers since late 2023; ar5iv covers older ones. Both are
LaTeXML output, where every section is a <section id="S3"> with an ltx_title heading.
A paper with neither keeps its abstract only, and its read level says so.
"""

import re
from html.parser import HTMLParser

from auto_research.tasks.citations.sources import fetch

SOURCES = ("https://arxiv.org/html/{id}", "https://ar5iv.labs.arxiv.org/html/{id}")
SKIP = re.compile(r"reference|bibliograph|acknowledg|appendix|checklist|broader impact", re.I)
# Kept first, in this order, when a paper is longer than the budget.
KEEP = (re.compile(r"intro", re.I), re.compile(r"limit|discussion|conclu|future", re.I),
        re.compile(r"method|approach|model|framework|architecture|our ", re.I),
        re.compile(r"experiment|result|evaluat|analys|ablat", re.I))
PER_SECTION = 2400


class _Sections(HTMLParser):
    """Collects (locator, heading, text) for every section and subsection."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.sections: list[dict] = []
        self.heading = None          # text of the heading being read
        self.drop = None             # [tag, nesting] of the element whose text is dropped
        self.in_text = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = a.get("class") or ""
        if self.drop:
            self.drop[1] += tag == self.drop[0]
            return
        if tag == "section" and re.fullmatch(r"S\d+(\.SS\d+)?", a.get("id") or ""):
            sid = a["id"]
            self.sections.append({"locator": "§" + sid[1:].replace(".SS", "."), "heading": "", "text": []})
        elif re.fullmatch(r"h[2-4]", tag) and "ltx_title" in cls and self.sections:
            self.heading = []
        elif tag == "math":
            self.handle_data(" " + (a.get("alttext") or "") + " ")
            self.drop = [tag, 1]
        elif tag in ("script", "style", "table", "figure", "nav", "footer") or "ltx_note" in cls:
            self.drop = [tag, 1]
        elif tag == "p":
            self.in_text += 1

    def handle_endtag(self, tag):
        if self.drop:
            if tag == self.drop[0]:
                self.drop[1] -= 1
                if not self.drop[1]:
                    self.drop = None
            return
        if re.fullmatch(r"h[2-4]", tag) and self.heading is not None:
            if not self.sections[-1]["heading"]:
                self.sections[-1]["heading"] = re.sub(r"\s+", " ", "".join(self.heading)).strip()
            self.heading = None
        elif tag == "p" and self.in_text:
            self.in_text -= 1
            if self.sections:
                self.sections[-1]["text"].append("\n")

    def handle_data(self, data):
        if self.drop:
            return
        if self.heading is not None:
            self.heading.append(data)
        elif self.in_text and self.sections:
            self.sections[-1]["text"].append(data)


def sections(html: str) -> list[dict]:
    parser = _Sections()
    parser.feed(html)
    out = []
    for s in parser.sections:
        text = re.sub(r"[ \t]+", " ", "".join(s["text"]))
        text = re.sub(r"\s*\n\s*", "\n", text).strip()
        heading = re.sub(r"^(?:\d+(?:\.\d+)*|[A-Z](?:\.\d+)*)\.?\s+", "", s["heading"]).strip()
        if text and not SKIP.search(heading):
            out.append({"locator": f"{s['locator']} {heading}".strip(), "heading": heading, "text": text})
    return out


def pick(found: list[dict], budget: int) -> list[dict]:
    """The sections the reader sees, in paper order: introduction, limitations,
    method and results first, each cut to PER_SECTION characters, until the budget."""
    chosen, used = set(), 0
    for pattern in KEEP + (re.compile(""),):
        for i, s in enumerate(found):
            if i in chosen or not pattern.search(s["heading"]):
                continue
            cost = min(len(s["text"]), PER_SECTION)
            if used + cost > budget:
                continue
            chosen.add(i)
            used += cost
    return [dict(s, text=cut(s["text"], PER_SECTION)) for i, s in enumerate(found) if i in chosen]


def cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + " …"


def body(arxiv: str, budget: int = 12000) -> tuple[list[dict], str]:
    """The picked sections of one arXiv paper and the URL they came from; ([], "") when
    no HTML version could be read."""
    if not arxiv:
        return [], ""
    for pattern in SOURCES:
        url = pattern.format(id=arxiv)
        html = fetch(url, as_json=False, tries=2)
        found = sections(html) if html else []
        if len(found) >= 2:
            return pick(found, budget), url
    return [], ""


__all__ = ["body", "pick", "sections"]
