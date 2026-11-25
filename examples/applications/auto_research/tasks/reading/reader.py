"""Read the paper and the works it cites: LaTeX to plain text, citations, sentences,
the arXiv full text of a cited work with its tables, and quote validation. Plain code,
no model calls.

Every GET goes through citations.sources.fetch and the one POST through post below,
so tests replace those two alone.
"""

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

from auto_research.tasks.citations import sources
from auto_research.tasks.citations.refs import AUTHENTIC_TITLE, title_sim
from auto_research.tasks.library import resolve
from auto_research.tasks.revision.rules import paper as paper_rules
from auto_research.tasks.revision.steps.prepare.source import flatten
from auto_research.tasks.survey import fulltext, search

CITE = re.compile(r"\\[a-zA-Z]*cite[a-zA-Z]*\*?(?:\s*\[[^\]]*\]){0,2}\s*\{([^}]*)\}")
REFS = re.compile(r"\\(?:label|ref|eqref|autoref|cref|Cref|pageref|url|footnote)\*?\{[^}]*\}")
KEEP_ARG = re.compile(r"\\(?:textbf|textit|emph|underline|textsc|texttt|mathbf|mathrm|mathit|text|boldsymbol"
                      r"|textrm|mbox|hbox|textsf|bm|uline|ul)\s*\{([^{}]*)\}")
TEXT_CHARS = 24000          # of a cited work's body, besides its tables
TABLE_CHARS = 16000         # of a cited work's tables


def open_paper(paper_dir: str) -> tuple[Path, object, str]:
    """(repository, rules, the main file flattened as LaTeX reads it)."""
    paper = Path(paper_dir).expanduser().resolve()
    if not paper.is_dir():
        raise ValueError(f"paper_dir is not a folder: {paper}")
    rules = paper_rules.load(paper)
    if not (paper / rules.main_tex).is_file():
        raise ValueError(f"{paper / rules.main_tex} does not exist")
    return paper, rules, flatten(paper, rules.main_tex)


def cite_keys(tex: str) -> list[str]:
    out = []
    for group in CITE.findall(tex):
        for key in group.split(","):
            if key.strip() and key.strip() not in out:
                out.append(key.strip())
    return out


def plain(tex: str) -> str:
    """LaTeX as a reader sees it, roughly: citations and labels dropped, formatting
    commands unwrapped, math kept as written without the dollars."""
    s = CITE.sub("", tex or "")
    s = REFS.sub("", s)
    s = s.replace("\\pm", "±").replace("\\%", "%").replace("\\&", "&").replace("~", " ").replace("\\,", " ")
    for _ in range(4):
        s = KEEP_ARG.sub(r"\1", s)
    s = re.sub(r"\\[a-zA-Z@]+\*?", " ", s)
    s = s.replace("$", "").replace("{", "").replace("}", "").replace("\\", "")
    s = re.sub(r"\s+([,.;:)])", r"\1", s)
    return " ".join(s.split())


def norm(text: str) -> str:
    text = (text or "").replace("\u00a0", " ").replace("’", "'").replace("‘", "'")
    text = text.replace("“", '"').replace("”", '"').replace("–", "-").replace("—", "-")
    return " ".join(text.split())


def quoted(quote: str, text: str) -> bool:
    """Whether quote occurs in text verbatim, up to whitespace and typographic quotes."""
    q = norm(quote)
    return len(q) >= 3 and q in norm(text)


TITLE = re.compile(r"\\title(?:\[[^\]]*\])?\{((?:[^{}]|\{[^{}]*\})*)\}")
ABSTRACT = re.compile(r"\\begin\{abstract\}(.*?)\\end\{abstract\}", re.S)


def title_of(full: str) -> str:
    m = TITLE.search(full)
    return plain(m.group(1)) if m else ""


def abstract_of(full: str) -> str:
    m = ABSTRACT.search(full)
    return plain(m.group(1)) if m else ""


# Cited works: arXiv id and full text

def arxiv_of(ref: dict) -> tuple[str, str]:
    """The arXiv id of one bibliography entry and how it was found: the entry's eprint
    or arXiv DOI, else the OpenAlex work the library resolves it to, else an arXiv title
    search whose best hit matches the title closely. ("", "") when none."""
    if ref.get("arxiv"):
        return re.sub(r"v\d+$", "", ref["arxiv"]), "entry"
    doi = resolve.clean_doi(ref.get("doi") or "")
    if doi.startswith("10.48550/arxiv."):
        return doi.split("arxiv.", 1)[1], "entry"
    work, _ = resolve.resolve(dict(ref, doi=doi, arxiv=""))
    doi = resolve.work_doi(work)
    if doi.startswith("10.48550/arxiv."):
        return doi.split("arxiv.", 1)[1], "openalex"
    if ref.get("title"):
        hits = search.arxiv_search(ref["title"], 3)
        best = max(hits, key=lambda c: title_sim(ref["title"], c["title"]), default=None)
        if best and title_sim(ref["title"], best["title"]) >= AUTHENTIC_TITLE:
            return best["arxiv"], "title"
    return "", ""


class _Tables(HTMLParser):
    """Every LaTeXML table: its caption and its rows of cells. A table figure may hold
    panels (subtables), each a figure with its own caption: the outer figure's caption
    is the table's, and each panel's caption becomes a row of its own before the panel's
    rows, so a row is never read under a sibling panel's caption. A math element is read
    once, from its TeX alttext; its MathML and annotation text are skipped."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables: list[dict] = []
        self.depth = 0            # nesting inside the current table figure
        self.caption = None
        self.caption_depth = 0
        self.cell = None
        self.math = 0             # nesting inside a math element
        self.panels: list[int] = []   # row index where each open panel starts

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "figure" and "ltx_table" in (a.get("class") or "") and not self.depth:
            self.tables.append({"caption": "", "rows": []})
            self.depth, self.panels = 1, []
            return
        if not self.depth:
            return
        if tag == "math":
            if not self.math:
                self.handle_data(" " + (a.get("alttext") or "") + " ")
            self.math += 1
        elif self.math:
            return
        elif tag == "figure":
            self.depth += 1
            self.panels.append(len(self.tables[-1]["rows"]))
        elif tag == "figcaption":
            self.caption, self.caption_depth = [], self.depth
        elif tag == "tr":
            self.tables[-1]["rows"].append([])
        elif tag in ("td", "th") and self.tables[-1]["rows"]:
            self.cell = []

    def handle_endtag(self, tag):
        if not self.depth:
            return
        if tag == "math":
            self.math = max(0, self.math - 1)
        elif self.math:
            return
        elif tag == "figure":
            self.depth -= 1
            if self.panels and self.depth:
                self.panels.pop()
        elif tag == "figcaption" and self.caption is not None:
            text = " ".join("".join(self.caption).split())
            if self.caption_depth == 1:
                self.tables[-1]["caption"] = text
            elif text:
                # before the panel's first row, wherever the caption sits in the source
                at = self.panels[-1] if self.panels else len(self.tables[-1]["rows"])
                self.tables[-1]["rows"].insert(at, [f"[{text}]"])
            self.caption = None
        elif tag in ("td", "th") and self.cell is not None:
            self.tables[-1]["rows"][-1].append(" ".join("".join(self.cell).split()))
            self.cell = None

    def handle_data(self, data):
        if self.math:
            return
        if self.caption is not None:
            self.caption.append(data)
        elif self.cell is not None:
            self.cell.append(data)


def html_tables(html: str) -> list[str]:
    """Each table as text: its caption, then one line per row with cells joined by " | "."""
    parser = _Tables()
    parser.feed(html)
    tables = []
    for t in parser.tables:
        panel = [i for i, row in enumerate(t["rows"]) if len(row) == 1 and row[0].startswith("[")]
        if t["caption"] or not panel:
            tables.append(t)
            continue
        # a figure of side-by-side tables with no caption of its own: each panel is a table
        bounds = panel + [len(t["rows"])]
        if bounds[0]:
            tables.append({"caption": "", "rows": t["rows"][:bounds[0]]})
        for a, b in zip(bounds, bounds[1:]):
            tables.append({"caption": t["rows"][a][0][1:-1], "rows": t["rows"][a + 1:b]})
    out = []
    for t in tables:
        rows = [" | ".join(c for c in row) for row in t["rows"] if any(c for c in row)]
        if rows:
            out.append("\n".join([t["caption"] or "Table"] + rows))
    return out


def work_text(arxiv: str) -> tuple[str, str]:
    """The text of one arXiv paper as the extractor reads it: its tables first, then
    its sections, results first; and the URL it came from. ("", "") when no HTML
    version could be read."""
    for pattern in fulltext.SOURCES:
        url = pattern.format(id=arxiv)
        html = sources.fetch(url, as_json=False, tries=2)
        if not html:
            continue
        found, tables = fulltext.sections(html), html_tables(html)
        if len(found) < 2 and not tables:
            continue
        parts, used = [], 0
        for t in tables:
            if used + len(t) > TABLE_CHARS:
                break
            parts.append(t)
            used += len(t)
        order = sorted(range(len(found)), key=lambda i: (not fulltext.KEEP[3].search(found[i]["heading"]), i))
        chosen, used = set(), 0
        for i in order:
            if used + len(found[i]["text"]) <= TEXT_CHARS:
                chosen.add(i)
                used += len(found[i]["text"])
        parts += [f"{s['locator']}\n{s['text']}" for i, s in enumerate(found) if i in chosen]
        return "\n\n".join(parts), url
    return "", ""


def post(url: str, params: dict, payload: dict, label: str = "", headers: dict | None = None, tries: int = 3):
    """A JSON POST with the retries and failure record of sources.fetch."""
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(payload).encode()
    for attempt in range(tries):
        try:
            request = urllib.request.Request(url, data=data, method="POST", headers={
                **sources.UA, "Content-Type": "application/json", **(headers or {})})
            with urllib.request.urlopen(request, timeout=sources.TIMEOUT) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            if e.code in (400, 404):
                return None
        except Exception:  # noqa: S110  a timeout is retried below
            pass
        time.sleep(1.5 * (attempt + 1))
    if label:
        sources.UNAVAILABLE.add(label)
    return None


__all__ = ["abstract_of", "arxiv_of", "cite_keys", "html_tables", "norm", "open_paper", "plain", "post", "quoted",
           "title_of", "work_text"]
