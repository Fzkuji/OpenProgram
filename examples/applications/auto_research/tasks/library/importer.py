"""Merge an exported library (Zotero, Mendeley, any .bib, or Zotero's CSL-JSON) into
the paper's bibliography. Plain code, no network, no model calls. Only appends."""

import json
import re
from pathlib import Path

from auto_research.tasks.citations.refs import ARXIV_ID, parse_bib
from auto_research.tasks.survey.search import norm_title

from .resolve import bib_files, clean_doi

CSL_TYPES = {"article-journal": "article", "article": "article", "article-magazine": "article",
             "article-newspaper": "article", "paper-conference": "inproceedings", "book": "book",
             "chapter": "incollection", "report": "techreport", "thesis": "phdthesis"}


def import_library(paper_dir: str, bib_path: str, target: str = "") -> str:
    """Add the entries of an exported library that the paper's bibliography lacks.

    bib_path is a .bib export or a CSL-JSON export (.json, converted to minimal BibTeX).
    target is the .bib to add to, relative to paper_dir or absolute; empty uses the first
    bibliography the paper uses (workflow/rules.json bib_files, else the first *.bib in
    the root, else references.bib, created). An entry is a duplicate when its DOI, arXiv
    id or normalized title is already there; duplicates are skipped. A new entry whose
    key is taken gets a suffix (_2, _3, ...). Added entries keep their original text;
    existing entries are never changed or removed. Returns JSON {added, skipped, renamed, target}.
    """
    paper = Path(paper_dir).expanduser().resolve()
    if not paper.is_dir():
        raise ValueError(f"paper_dir is not a folder: {paper}")
    source = Path(bib_path).expanduser()
    if not source.is_file():
        raise ValueError(f"no such file: {source}")
    raw = source.read_text(encoding="utf-8", errors="replace")
    text = csl_to_bib(json.loads(raw)) if source.suffix.lower() == ".json" else raw
    incoming = parse_bib(text)
    if not incoming:
        raise ValueError(f"no entry could be read from {source}")
    dest = target_file(paper, target)
    existing_text = dest.read_text(encoding="utf-8", errors="replace") if dest.is_file() else ""
    existing = parse_bib(existing_text)

    keys = {r["key"].lower() for r in existing}
    index = {}
    for r in existing:
        for ident in identities(r):
            index.setdefault(ident, r["key"])
    added, skipped, renamed, blocks = [], [], {}, []
    for r in incoming:
        hit = next(((ident, index[ident]) for ident in identities(r) if ident in index), None)
        if hit:
            skipped.append({"key": r["key"], "duplicate_of": hit[1], "by": hit[0].split(":", 1)[0]})
            continue
        key, entry = r["key"], r["text"]
        if key.lower() in keys:
            n = 2
            while f"{key}_{n}".lower() in keys:
                n += 1
            key = f"{key}_{n}"
            entry = re.sub(r"(@\w+\s*\{\s*)" + re.escape(r["key"]) + r"(\s*,)",
                           lambda m: m.group(1) + key + m.group(2), entry, count=1)
            renamed[r["key"]] = key
        keys.add(key.lower())
        for ident in identities(r):
            index.setdefault(ident, key)
        added.append(key)
        blocks.append(entry)
    if blocks:
        dest.parent.mkdir(parents=True, exist_ok=True)
        joint = ("" if not existing_text.strip() or existing_text.endswith("\n\n") else
                 "\n" if existing_text.endswith("\n") else "\n\n")
        dest.write_text(existing_text + joint + "\n\n".join(blocks) + "\n", encoding="utf-8")
    return json.dumps({"added": added, "skipped": skipped, "renamed": renamed, "target": str(dest)},
                      ensure_ascii=False)


def target_file(paper: Path, target: str) -> Path:
    if target:
        path = Path(target).expanduser()
        return path if path.is_absolute() else paper / path
    found = bib_files(paper)
    return found[0] if found else paper / "references.bib"


def identities(ref: dict) -> list[str]:
    doi = clean_doi(ref["doi"])
    arxiv = ref["arxiv"]
    if not arxiv and doi.startswith("10.48550/arxiv."):
        arxiv = doi.split("arxiv.", 1)[1]
    out = []
    if doi:
        out.append("doi:" + doi)
    if arxiv:
        out.append("arxiv:" + re.sub(r"v\d+$", "", arxiv))
    title = norm_title(ref["title"])
    if title:
        out.append("title:" + title)
    return out


# CSL-JSON -> BibTeX

def csl_to_bib(data) -> str:
    items = data.get("items") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ValueError("a CSL-JSON export is a list of items")
    used, out = set(), []
    for item in items:
        if isinstance(item, dict) and item.get("title"):
            key = csl_key(item, used)
            used.add(key.lower())
            out.append(csl_entry(item, key))
    return "\n\n".join(out)


def csl_key(item: dict, used: set) -> str:
    raw = str(item.get("citation-key") or item.get("id") or "")
    if re.fullmatch(r"[A-Za-z][\w:\-.]*", raw):
        key = raw
    else:
        authors = item.get("author") or []
        family = (authors[0].get("family") or authors[0].get("literal") or "") if authors else ""
        word = next((w for w in re.findall(r"[A-Za-z]+", item["title"]) if len(w) > 3), "work")
        key = re.sub(r"[^A-Za-z0-9]", "", family.lower()) + str(csl_year(item)) + word.lower()
        key = key if key[:1].isalpha() else "ref" + key
    base, n = key, 2
    while key.lower() in used:
        key, n = f"{base}_{n}", n + 1
    return key


def csl_year(item: dict) -> str:
    for field in ("issued", "published-print", "published-online"):
        parts = (item.get(field) or {}).get("date-parts") or [[]]
        if parts and parts[0] and str(parts[0][0]).isdigit():
            return str(parts[0][0])
        raw = str((item.get(field) or {}).get("raw") or "")
        if re.match(r"\d{4}", raw):
            return raw[:4]
    return ""


def csl_entry(item: dict, key: str) -> str:
    kind = CSL_TYPES.get(item.get("type", ""), "misc")
    names = []
    for a in item.get("author") or []:
        if a.get("literal"):
            names.append("{" + a["literal"] + "}")
        elif a.get("family"):
            names.append(a["family"] + (", " + a["given"] if a.get("given") else ""))
    container = item.get("container-title") or ""
    fields = [("title", item.get("title")), ("author", " and ".join(names)), ("year", csl_year(item)),
              ({"article": "journal", "inproceedings": "booktitle", "incollection": "booktitle"}.get(kind, "howpublished"),
               container),
              ("volume", item.get("volume")), ("number", item.get("issue")), ("pages", item.get("page")),
              ("publisher", item.get("publisher")), ("doi", item.get("DOI")), ("url", item.get("URL"))]
    blob = " ".join(str(item.get(k) or "") for k in ("URL", "number", "note", "DOI", "publisher"))
    arxiv = ARXIV_ID.search(blob)
    if arxiv and "arxiv" in blob.lower():
        fields += [("eprint", arxiv.group(1)), ("archivePrefix", "arXiv")]
    body = ",\n".join(f"  {name} = {{{str(value).replace('{', '').replace('}', '')}}}"
                      if name != "author" else f"  {name} = {{{value}}}"
                      for name, value in fields if value not in (None, ""))
    return f"@{kind}{{{key},\n{body}\n}}"


__all__ = ["csl_to_bib", "import_library"]
