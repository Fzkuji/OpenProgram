"""Bring the author's highlights and notes into the project. Plain code, no model calls.

Sources: Zotero 7's local API (http://localhost:23119/api/, user 0, read only; the
author turns it on under Settings → Advanced → "Allow other applications on this
computer to communicate with Zotero"), or an export file: Zotero API JSON, a JSON or
CSV list of annotations (Zotero, ReadCube Papers and most plugins write one), or the
Markdown of a Zotero note made from annotations. Each annotated item is matched to a
.bib entry by DOI, arXiv id or title; workflow/annotations.csv gets one row per
annotation, and the drafts get candidate claim_support rows with the claim left empty.
claim_support.csv is never written.
"""

import csv
import io
import json
import re
from datetime import datetime
from pathlib import Path

from auto_research import stages
from auto_research.trail import Trail

from ..citations import sources
from ..citations.refs import ARXIV_ID, AUTHENTIC_TITLE, surname, title_sim
from ..library import resolve

ZOTERO = "http://localhost:23119/api/users/0"
PAGE = 100
COLUMNS = ("key", "page", "quote", "comment", "color")
SUPPORT_COLUMNS = ("claim", "work", "stance", "study", "quote", "note")
FILE = "annotations.csv"
# Column and field names an export may use, lower case with spaces as underscores.
ALIASES = {
    "quote": ("annotationtext", "annotation_text", "quote", "text", "highlight", "highlighted_text", "highlights",
              "selected_text", "excerpt", "annotation"),
    "comment": ("annotationcomment", "annotation_comment", "comment", "note", "notes", "annotation_note"),
    "page": ("annotationpagelabel", "annotation_page_label", "page", "page_label", "page_number", "pagelabel"),
    "color": ("annotationcolor", "annotation_color", "color", "colour", "highlight_color"),
    "doi": ("doi", "item_doi", "parent_doi"),
    "title": ("title", "item_title", "parent_title", "document_title", "article_title", "publication_title_item",
              "reference", "document"),
    "authors": ("authors", "author", "creators", "first_author"),
    "year": ("year", "date", "publication_year"),
    "arxiv": ("arxiv", "arxiv_id", "eprint", "url", "extra"),
}


def import_annotations(paper_dir: str, source: str = "zotero") -> str:
    """Read the author's highlights and notes and match them to the bibliography.

    source is "zotero" (Zotero 7's local API on this computer) or the path of an
    export: .json (Zotero API items, or a list of annotation records), .csv (one
    annotation per row with its item's title or DOI) or .md (a Zotero note made from
    annotations). Each annotation's item is matched to a .bib entry by DOI, arXiv id,
    then title (or, in Markdown, first author and year). workflow/annotations.csv gets
    key, page, quote, comment and color per matched annotation, keeping rows already
    there; unmatched items are listed in the report. Candidate claim_support rows (claim
    and stance left for the author) and the report go to
    <paper_dir>/drafts/import_annotations/<timestamp>/. claim_support.csv is never written.
    """
    paper = Path(paper_dir).expanduser().resolve()
    if not paper.is_dir():
        raise ValueError(f"paper_dir is not a folder: {paper}")
    trail = Trail("import_annotations", {"paper_dir": str(paper), "source": source}, {})
    if source.strip().lower() == "zotero":
        found = from_zotero()
    else:
        path = Path(source).expanduser()
        if not path.is_file():
            raise ValueError(f"no such file: {path}")
        found = from_file(path)
    found = [a for a in found if a["quote"] or a["comment"]]
    trail.step("read", source=source, annotations=len(found))
    refs = resolve.entries(paper)
    matched, unmatched = [], {}
    for a in found:
        key, how = match(a["item"], refs)
        if key:
            matched.append(dict(a, key=key, how=how))
        else:
            unmatched.setdefault(a["item"].get("title") or a["item"].get("doi") or "?", []).append(a)
    trail.step("match", matched=len(matched), unmatched=sorted(unmatched))
    return save(paper, matched, unmatched, source, trail)


# Zotero's local API

def zotero(path: str, params: dict | None = None):
    return sources.fetch(f"{ZOTERO}/{path}", params, tries=1, label="Zotero local API")


def from_zotero() -> list[dict]:
    """Every annotation in the Zotero library with its parent item: annotation →
    attachment (its parentItem) → regular item (title, DOI, extra, url)."""
    sources.UNAVAILABLE.discard("Zotero local API")
    items, start = [], 0
    while True:
        page = zotero("items", {"itemType": "annotation", "format": "json", "limit": PAGE, "start": start})
        if page is None:
            raise ValueError("Zotero's local API did not answer at http://localhost:23119/api/: start Zotero 7 and "
                             "turn on Settings → Advanced → \"Allow other applications on this computer to "
                             "communicate with Zotero\", or pass an export file as source")
        items += page
        if len(page) < PAGE:
            break
        start += PAGE
    cache: dict[str, dict] = {}

    def get(key: str) -> dict:
        if key not in cache:
            d = zotero(f"items/{key}", {"format": "json"})
            cache[key] = (d or {}).get("data") or {}
        return cache[key]

    out = []
    for it in items:
        d = it.get("data") or it
        parent = get(d["parentItem"]) if d.get("parentItem") else {}
        if parent.get("itemType") == "attachment" and parent.get("parentItem"):
            parent = get(parent["parentItem"])
        out.append(annotation(d, item_of(parent)))
    return out


def item_of(data: dict) -> dict:
    blob = " ".join(str(data.get(k) or "") for k in ("extra", "url", "archiveID", "DOI"))
    arxiv = ARXIV_ID.search(blob) if "arxiv" in blob.lower() else None
    creators = [c.get("lastName") or c.get("name") or "" for c in data.get("creators") or []]
    year = re.search(r"\d{4}", str(data.get("date") or ""))
    return {"title": data.get("title") or "", "doi": resolve.clean_doi(data.get("DOI") or ""),
            "arxiv": arxiv.group(1) if arxiv else "", "authors": [c for c in creators if c],
            "year": year.group(0) if year else ""}


def annotation(d: dict, item: dict) -> dict:
    return {"quote": " ".join(str(d.get("annotationText") or "").split()),
            "comment": " ".join(str(d.get("annotationComment") or "").split()),
            "page": str(d.get("annotationPageLabel") or ""), "color": str(d.get("annotationColor") or ""),
            "item": item}


# Export files

def from_file(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    suffix = path.suffix.lower()
    if suffix == ".json":
        return from_json(json.loads(text))
    if suffix == ".csv":
        with io.StringIO(text, newline="") as f:
            return [record(r) for r in csv.DictReader(f)]
    if suffix in (".md", ".markdown", ".txt"):
        return from_markdown(text)
    raise ValueError(f"unknown export format {suffix}: use .json, .csv or .md")


def from_json(data) -> list[dict]:
    items = data.get("items") or data.get("annotations") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ValueError("a JSON export is a list of items or annotations")
    datas = [i.get("data") if isinstance(i, dict) and isinstance(i.get("data"), dict) else i for i in items]
    if any(isinstance(d, dict) and d.get("itemType") == "annotation" for d in datas):     # Zotero API items
        by_key = {d.get("key"): d for d in datas if isinstance(d, dict)}
        out = []
        for d in datas:
            if not isinstance(d, dict) or d.get("itemType") != "annotation":
                continue
            parent = by_key.get(d.get("parentItem")) or {}
            if parent.get("itemType") == "attachment" and parent.get("parentItem"):
                parent = by_key.get(parent["parentItem"]) or parent
            out.append(annotation(d, item_of(parent)))
        return out
    out = []
    for d in datas:
        if not isinstance(d, dict):
            continue
        nested = d.get("annotations")
        if isinstance(nested, list):                        # an item with its annotations
            out += [record({**{k: v for k, v in d.items() if k != "annotations"}, **a}) for a in nested
                    if isinstance(a, dict)]
        else:
            out.append(record(d))
    return out


def pick(row: dict, name: str) -> str:
    low = {re.sub(r"[\s\-]+", "_", str(k).strip().lower()): v for k, v in row.items() if k}
    for alias in ALIASES[name]:
        value = low.get(alias)
        if isinstance(value, list):
            value = " and ".join(v.get("lastName") or v.get("family") or v.get("name") or "" if isinstance(v, dict)
                                 else str(v) for v in value)
        if value not in (None, ""):
            return " ".join(str(value).split())
    return ""


def record(row: dict) -> dict:
    blob = pick(row, "arxiv")
    arxiv = ARXIV_ID.search(blob) if "arxiv" in blob.lower() else None
    year = re.search(r"\d{4}", pick(row, "year"))
    return {"quote": pick(row, "quote"), "comment": pick(row, "comment"), "page": pick(row, "page"),
            "color": pick(row, "color"),
            "item": {"title": pick(row, "title"), "doi": resolve.clean_doi(pick(row, "doi")),
                     "arxiv": arxiv.group(1) if arxiv else "",
                     "authors": [a.strip() for a in re.split(r"\s+and\s+|;", pick(row, "authors")) if a.strip()],
                     "year": year.group(0) if year else ""}}


# “quoted text” ([Vaswani et al., 2017, p. 3](zotero://open-pdf/...)) comment
MD_QUOTE = re.compile(r"^[>\s]*[“\"](?P<quote>.+?)[”\"]\s*\((?:\[(?P<cite>[^\]]*)\]\([^)]*\)|(?P<plain>[^()]*))\)"
                      r"\s*(?P<comment>.*)$")


def from_markdown(text: str) -> list[dict]:
    """A Zotero note from annotations: one quoted highlight per line followed by its
    citation (author, year, page) and the comment. A "# heading" names the item."""
    out, title = [], ""
    for line in text.splitlines():
        heading = re.match(r"^#{1,3}\s+(.*\S)", line)
        if heading and heading.group(1).strip().lower() not in ("annotations", "notes", "highlights"):
            title = re.sub(r"^Annotations\s*[:：]?\s*", "", heading.group(1).strip())
            continue
        m = MD_QUOTE.match(line.strip())
        if not m:
            continue
        cite = m.group("cite") or m.group("plain") or ""
        page = re.search(r"\bpp?\.\s*([\w\-–]+)", cite)
        year = re.search(r"\b(1[89]\d\d|20\d\d)\b", cite)
        first = re.split(r"\s+(?:et al\.?|and|&)\s*|,", cite)[0].strip()
        out.append({"quote": " ".join(m.group("quote").split()), "comment": " ".join(m.group("comment").split()),
                    "page": page.group(1) if page else "", "color": "",
                    "item": {"title": title, "doi": "", "arxiv": "", "authors": [first] if first else [],
                             "year": year.group(1) if year else ""}})
    return out


# Matching

def match(item: dict, refs: list[dict]) -> tuple[str, str]:
    """The .bib key of an annotated item and how it was matched: doi, arxiv, title, or
    author_year (one entry with that first author's surname and year)."""
    if item.get("doi"):
        hit = next((r for r in refs if r["doi"] and r["doi"] == item["doi"]), None)
        if hit:
            return hit["key"], "doi"
        if item["doi"].startswith("10.48550/arxiv."):
            item = dict(item, arxiv=item["doi"].split("arxiv.", 1)[1])
    if item.get("arxiv"):
        arxiv = re.sub(r"v\d+$", "", item["arxiv"])
        hit = next((r for r in refs if ref_arxiv(r) == arxiv), None)
        if hit:
            return hit["key"], "arxiv"
    if item.get("title"):
        best = max(refs, key=lambda r: title_sim(item["title"], r["title"]), default=None)
        if best and title_sim(item["title"], best["title"]) >= AUTHENTIC_TITLE:
            return best["key"], "title"
    if item.get("authors") and item.get("year"):
        name = surname(item["authors"][0])
        hits = [r for r in refs if r["year"] == item["year"] and r["authors"] and surname(r["authors"][0]) == name]
        if name and len(hits) == 1:
            return hits[0]["key"], "author_year"
    return "", ""


def ref_arxiv(ref: dict) -> str:
    doi = resolve.clean_doi(ref.get("doi") or "")
    arxiv = ref.get("arxiv") or (doi.split("arxiv.", 1)[1] if doi.startswith("10.48550/arxiv.") else "")
    return re.sub(r"v\d+$", "", arxiv)


# Output

def merge_csv(path: Path, rows: list[dict]) -> tuple[int, int]:
    """Append the rows not already there (same key, page and quote); existing rows stay."""
    existing = []
    if path.is_file():
        with io.StringIO(path.read_text(encoding="utf-8"), newline="") as f:
            existing = [{c: (r.get(c) or "").strip() for c in COLUMNS} for r in csv.DictReader(f)]
    seen = {(r["key"], r["page"], r["quote"]) for r in existing}
    new = []
    for r in rows:
        ident = (r["key"], r["page"], r["quote"])
        if ident not in seen:
            seen.add(ident)
            new.append({c: r[c] for c in COLUMNS})
    write_csv(path, COLUMNS, existing + new)
    return len(new), len(existing)


def write_csv(path: Path, columns, rows: list[dict]) -> None:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=columns, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(buf.getvalue(), encoding="utf-8")


def save(paper: Path, matched: list[dict], unmatched: dict, source: str, trail: Trail) -> str:
    csv_path = stages.folder(paper) / FILE
    added, kept = merge_csv(csv_path, matched)
    out = paper / "drafts" / "import_annotations" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    out.mkdir(parents=True, exist_ok=True)
    support = [{"claim": "", "work": a["key"], "stance": "", "study": "", "quote": a["quote"],
                "note": "; ".join(x for x in (f"p. {a['page']}" if a["page"] else "", a["comment"]) if x)}
               for a in matched if a["quote"]]
    write_csv(out / "claim_support_candidates.csv", SUPPORT_COLUMNS, support)
    status = "PASS_WITH_NOTES" if unmatched else "PASS"
    (out / "report.md").write_text(render(status, matched, unmatched, added, kept, source), encoding="utf-8")
    trail.step("save", status=status, added=added, kept=kept, candidates=len(support))
    run = trail.save(out, report=out / "report.md", csv=csv_path, candidates=out / "claim_support_candidates.csv")
    return json.dumps({"status": status, "annotations": str(csv_path), "report": str(out / "report.md"),
                       "candidates": str(out / "claim_support_candidates.csv"), "run": str(run),
                       "matched": len(matched), "added": added, "kept": kept,
                       "unmatched": {k: len(v) for k, v in unmatched.items()}}, ensure_ascii=False)


def render(status, matched, unmatched, added, kept, source) -> str:
    by_key: dict[str, list[dict]] = {}
    for a in matched:
        by_key.setdefault(a["key"], []).append(a)
    lines = ["# 导入批注", "", f"结论：{status}　来源 `{source}`，{len(matched)} 条批注对上了 .bib，"
             f"新加 {added} 条到 workflow/annotations.csv（原有 {kept} 条保留）", "",
             "`claim_support_candidates.csv` 是候选的 claim_support 行：claim 和 stance 留给作者填，"
             "挑好后复制到 workflow/claim_support.csv。", ""]
    for key, group in by_key.items():
        lines += [f"## `{key}`（{len(group)}）", ""]
        for a in group:
            page = f"p. {a['page']}　" if a["page"] else ""
            lines.append(f"- {page}{a['quote'] or '（无高亮文字）'}" + (f"　—　{a['comment']}" if a["comment"] else ""))
        lines.append("")
    if unmatched:
        lines += ["## 没对上 .bib 的条目", ""]
        lines += [f"- {title}（{len(group)} 条批注）" for title, group in unmatched.items()]
        lines += ["", "先用 import_library 把这些文献加进 .bib，再导入一次。", ""]
    return "\n".join(lines)


__all__ = ["from_file", "from_zotero", "import_annotations", "match"]
