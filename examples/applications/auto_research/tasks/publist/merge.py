"""One entry per work from the records of every source, and the list as BibTeX and as a
static page. Plain code, no model calls.

Records of the same work are joined by DOI, then arXiv id, then normalized title (a
preprint and its published version, or a conference paper and its journal version,
become one entry with every version and link kept).
"""

import html
import re
from collections import Counter

from auto_research.tasks.feedback.people import same_name
from auto_research.tasks.survey.search import norm_title

from .profiles import is_preprint

SKIP_KINDS = {"erratum", "paratext", "peer-review", "retraction", "supplementary-materials", "dataset", "grant",
              "libguides", "standard"}
SKIP_TITLE = re.compile(r"^\s*(?:erratum|errata|correction(?: to)?|corrigendum|retraction(?: note)?|"
                        r"supplementary material|author correction|publisher correction)\b", re.I)
GENERIC = {"introduction", "editorial", "preface", "foreword", "conclusion", "conclusions", "abstract", "index",
           "contents", "front matter", "back matter", "discussion", "reply", "response", "acknowledgments"}
SHOWN_AUTHORS = 12
SOURCE_ORDER = {"openalex": 0, "dblp": 1, "orcid": 2}


def excluded_reason(r: dict) -> str:
    if r["type"] in SKIP_KINDS:
        return f"type {r['type']}"
    if SKIP_TITLE.search(r["title"]):
        return "correction or supplement"
    return ""


CONTAINED = 40               # a title holding another this long at its start or end is the same work


def title_key(title: str) -> str:
    """The title with case, punctuation and spaces dropped ("WO 3" is "WO3"); "" for a
    one-word or generic title, which is never matched on."""
    t = norm_title(title)
    return t.replace(" ", "") if len(t.split()) >= 2 and t not in GENERIC else ""


def venue_key(venue: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (venue or "").lower())


def group(records: list[dict]) -> tuple[list[list[dict]], list[dict]]:
    """Records joined into works (union by DOI, arXiv id, normalized title), and the
    records left out with their reason."""
    kept, skipped = [], []
    for r in records:
        why = excluded_reason(r)
        if why:
            skipped.append({"rid": r["rid"], "source": r["source"], "title": r["title"], "year": r["year"],
                            "reason": why})
        else:
            kept.append(r)
    parent = list(range(len(kept)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    seen: dict[str, int] = {}
    for i, r in enumerate(kept):
        for key in (f"doi:{r['doi']}" if r["doi"] else "", f"arxiv:{r['arxiv']}" if r["arxiv"] else "",
                    f"title:{title_key(r['title'])}" if title_key(r["title"]) else ""):
            if not key:
                continue
            if key in seen:
                parent[find(i)] = find(seen[key])
            else:
                seen[key] = i
    # a title one source prefixes with a section name ("Instrumentation ... Technology-Fabrication of ...")
    keys = [title_key(r["title"]) for r in kept]
    for i, a in enumerate(keys):
        for j in range(i + 1, len(keys)):
            b = keys[j]
            short, long = (a, b) if len(a) <= len(b) else (b, a)
            if len(short) >= CONTAINED and short != long and (long.startswith(short) or long.endswith(short)) \
                    and abs((kept[i]["year"] or 0) - (kept[j]["year"] or 0)) <= 1:
                parent[find(j)] = find(i)
    groups: dict[int, list[dict]] = {}
    for i, r in enumerate(kept):
        groups.setdefault(find(i), []).append(r)
    return list(groups.values()), skipped


def entry(records: list[dict]) -> dict:
    """One work from its records: the first published version leads, every other version
    is kept, and each field comes from the record that has it."""
    records = sorted(records, key=lambda r: SOURCE_ORDER.get(r["source"], 9))
    published = [r for r in records if not is_preprint(r) and (r["venue"] or r["doi"] or r["type"] == "patent")]
    lead = min(published, key=lambda r: (r["year"] or 9999, SOURCE_ORDER.get(r["source"], 9))) if published \
        else min(records, key=lambda r: (r["year"] or 9999, SOURCE_ORDER.get(r["source"], 9)))
    versions, seen = [], set()
    for r in sorted(records, key=lambda r: (r["year"] or 9999)):
        key = venue_key(r["venue"]) or ("arxiv" if r["arxiv"] and is_preprint(r) else "")
        # one DOI, or one venue (online-first and issue year, spellings of a name), is one version
        if not key or key in seen or (r["doi"] and "doi:" + r["doi"] in seen):
            continue
        seen |= {key, "doi:" + r["doi"]} if r["doi"] else {key}
        versions.append({"venue": r["venue"] or "arXiv", "year": r["year"], "doi": r["doi"],
                         "preprint": is_preprint(r)})
    with_authors = sorted((r for r in records if r["authors"]),
                          key=lambda r: (SOURCE_ORDER.get(r["source"], 9), -len(r["authors"])))
    authors = with_authors[0]["authors"] if with_authors else []
    title = next((r["title"] for r in records if r["source"] == "openalex"), records[0]["title"])
    pick = lambda field: next((r[field] for r in [lead] + records if r.get(field)), "")
    arxiv = pick("arxiv")
    return {"title": title, "year": lead["year"] or max((r["year"] for r in records), default=0),
            "venue": "" if is_preprint(lead) and not published else lead["venue"],
            "kind": lead["type"], "preprint": not published,
            "doi": next((r["doi"] for r in published if r["doi"]), "") or pick("doi"),
            "arxiv": arxiv, "authors": authors, "versions": versions,
            "abstract": max((r["abstract"] for r in records), key=len, default=""),
            "pdf": pick("pdf") or (f"https://arxiv.org/pdf/{arxiv}" if arxiv else ""),
            "url": pick("url"), "openalex": [r["openalex"] for r in records if r.get("openalex")],
            "sources": sorted({r["source"] for r in records}, key=lambda s: SOURCE_ORDER.get(s, 9)),
            "records": [r["rid"] for r in records],
            "merged_versions": len(versions) > 1,
            "orcid": any(r["source"] == "orcid" for r in records),
            "places": {k: v for r in records for k, v in (r.get("places") or {}).items()}}


def build(records: list[dict]) -> tuple[list[dict], list[dict]]:
    groups, skipped = group(records)
    entries = sorted((entry(g) for g in groups), key=lambda e: (-(e["year"] or 0), e["title"].lower()))
    for i, e in enumerate(entries, 1):
        e["id"] = f"P{i}"
    return entries, skipped


def coverage(records: list[dict], entries: list[dict], skipped: list[dict]) -> list[str]:
    """Problems with "every source work appears exactly once": a record in no entry or in
    two, and two entries sharing a DOI, an arXiv id or a title."""
    where = Counter(rid for e in entries for rid in e["records"]) + Counter(s["rid"] for s in skipped)
    problems = [f"{r['rid']} ({r['title'][:60]}) appears {where[r['rid']]} times" for r in records
                if where[r["rid"]] != 1]
    for field, norm in (("doi", str), ("arxiv", str), ("title", title_key)):
        counts = Counter(norm(e[field]) for e in entries if norm(e[field]))
        problems += [f"two entries share {field} {k}" for k, n in counts.items() if n > 1]
    return problems


# BibTeX ------------------------------------------------------------------------------------

def bib_escape(text: str) -> str:
    return re.sub(r"([&%$#_])", r"\\\1", text or "")


def bib_key(e: dict, used: set[str]) -> str:
    first = (e["authors"] or ["anon"])[0]
    last = re.sub(r"[^a-z]", "", first.split()[-1].lower()) if first.split() else "anon"
    word = next((w for w in norm_title(e["title"]).split() if len(w) > 3 and w not in ("with", "from", "towards")), "work")
    base = f"{last or 'anon'}{e['year'] or ''}{word}"
    key, n = base, 0
    while key in used:
        n += 1
        key = base + "abcdefghijklmnopqrstuvwxyz"[(n - 1) % 26]
    used.add(key)
    return key


def bibtex(entries: list[dict]) -> str:
    used: set[str] = set()
    out = []
    for e in entries:
        key = bib_key(e, used)
        e["bibkey"] = key
        fields = [("title", "{" + bib_escape(e["title"]) + "}"),
                  ("author", " and ".join(bib_escape(a) for a in e["authors"]) or "Anonymous")]
        if e["preprint"]:
            kind = "misc"
            if e["arxiv"]:
                fields += [("eprint", e["arxiv"]), ("archivePrefix", "arXiv")]
            elif e["versions"]:
                fields.append(("howpublished", bib_escape(e["versions"][0]["venue"])))
        elif e["kind"] in ("article", "journal-article", "review"):
            kind = "article"
            fields.append(("journal", bib_escape(e["venue"])))
        elif e["kind"] in ("book-chapter",):
            kind = "incollection"
            fields.append(("booktitle", bib_escape(e["venue"])))
        elif e["kind"] == "patent":
            kind = "misc"
            fields.append(("howpublished", "Patent"))
        elif e["kind"] in ("book",):
            kind = "book"
        elif e["kind"] in ("dissertation",):
            kind = "phdthesis"
            fields.append(("school", bib_escape(e["venue"])))
        else:
            kind = "inproceedings"
            fields.append(("booktitle", bib_escape(e["venue"])))
        if e["year"]:
            fields.append(("year", str(e["year"])))
        if e["doi"]:
            fields.append(("doi", e["doi"]))
        if e["arxiv"] and not e["preprint"]:
            fields += [("eprint", e["arxiv"]), ("archivePrefix", "arXiv")]
        body = ",\n".join(f"  {k} = {v if v.startswith('{') else '{' + v + '}'}" for k, v in fields if v)
        out.append(f"@{kind}{{{key},\n{body}\n}}\n")
    return "\n".join(out)


# The page ----------------------------------------------------------------------------------

CSS = """
:root{--bg:#fbfbfa;--fg:#1d1d1f;--muted:#5f6368;--line:#e3e3e0;--accent:#3b4cca;--chip:#eef0fb}
@media (prefers-color-scheme:dark){:root{--bg:#141416;--fg:#ececec;--muted:#a0a3a8;--line:#2c2c30;--accent:#9aa5ff;--chip:#23253a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif}
main{max-width:860px;margin:0 auto;padding:40px 16px 64px}
h1{font-size:28px;margin:0 0 4px}header p{margin:2px 0;color:var(--muted)}header a{color:var(--accent)}
h2{font-size:20px;margin:36px 0 8px;padding-bottom:4px;border-bottom:1px solid var(--line)}
ol{list-style:none;padding:0;margin:0}li{padding:12px 0;border-bottom:1px solid var(--line)}li:last-child{border-bottom:0}
.t{font-weight:600}.a,.v{color:var(--muted);font-size:14px}.a b{color:var(--fg)}
.v i{font-style:normal}.s{margin:6px 0 0;font-size:15px}
.l{margin-top:6px;display:flex;flex-wrap:wrap;gap:6px}.l a{font-size:13px;text-decoration:none;color:var(--accent);background:var(--chip);border-radius:4px;padding:1px 8px}
footer{margin-top:40px;color:var(--muted);font-size:13px}
"""


def author_line(authors: list[str], name: str) -> str:
    shown, more = authors[:SHOWN_AUTHORS], len(authors) - SHOWN_AUTHORS
    mine = next((i for i, a in enumerate(authors) if same_name(name, a)), None)
    if mine is not None and mine >= SHOWN_AUTHORS:
        shown = shown[:SHOWN_AUTHORS - 1] + [authors[mine]]
        more = len(authors) - SHOWN_AUTHORS
    parts = [f"<b>{html.escape(a)}</b>" if same_name(name, a) else html.escape(a) for a in shown]
    return ", ".join(parts) + (f", and {more} more" if more > 0 else "")


def entry_links(e: dict) -> list[tuple[str, str]]:
    out = []
    if e["arxiv"]:
        out.append(("arXiv", f"https://arxiv.org/abs/{e['arxiv']}"))
    if e["doi"]:
        out.append(("DOI", f"https://doi.org/{e['doi']}"))
    if e["pdf"]:
        out.append(("PDF", e["pdf"]))
    if e.get("code"):
        out.append(("Code", e["code"]))
    if e.get("project"):
        out.append(("Project page", e["project"]))
    if not out and e["url"]:
        out.append(("Link", e["url"]))
    return out


def venue_line(e: dict) -> str:
    first = e["versions"][0]["venue"] if e["preprint"] and e["versions"] else ""
    main = e["venue"] or first or ("Patent" if e["kind"] == "patent" else "Preprint" if e["preprint"] else "")
    others = [v for v in e["versions"] if venue_key(v["venue"]) != venue_key(main)]
    text = html.escape(main) + (f" {e['year']}" if e["year"] and main else "")
    if others:
        text += " · <i>also " + ", ".join(html.escape(f"{v['venue']} {v['year'] or ''}".strip()) for v in others) + "</i>"
    return text


def page(person: dict, entries: list[dict], generated: str) -> str:
    name = person["name"]
    profile = [(label, url) for label, url in (("ORCID", f"https://orcid.org/{person['orcid']}" if person["orcid"] else ""),
                                               ("OpenAlex", f"https://openalex.org/{person['openalex'][0]}"
                                                if person["openalex"] else ""),
                                               ("DBLP", f"https://dblp.org/pid/{person['dblp']}" if person["dblp"] else ""))
               if url]
    years = Counter(e["year"] for e in entries)
    parts = ["<!doctype html>", '<html lang="en"><head><meta charset="utf-8">',
             '<meta name="viewport" content="width=device-width, initial-scale=1">',
             f"<title>{html.escape(name)} · Publications</title><style>{CSS}</style></head><body><main>",
             f"<header><h1>{html.escape(name)}</h1><p>{len(entries)} publications</p>"]
    if profile:
        parts.append("<p>" + " · ".join(f'<a href="{html.escape(u)}">{html.escape(l)}</a>' for l, u in profile) + "</p>")
    parts.append("</header>")
    for year in sorted(years, reverse=True):
        parts.append(f'<h2 id="y{year or "unknown"}">{year or "Year unknown"}</h2><ol>')
        for e in (x for x in entries if x["year"] == year):
            parts.append(f'<li id="{e["id"]}"><div class="t">{html.escape(e["title"])}</div>')
            if e["authors"]:
                parts.append(f'<div class="a">{author_line(e["authors"], name)}</div>')
            line = venue_line(e)
            if line:
                parts.append(f'<div class="v">{line}</div>')
            if e.get("summary"):
                parts.append(f'<p class="s">{html.escape(e["summary"])}</p>')
            links = entry_links(e)
            if links:
                parts.append('<div class="l">' + "".join(f'<a href="{html.escape(u)}">{html.escape(l)}</a>'
                                                         for l, u in links) + "</div>")
            parts.append("</li>")
        parts.append("</ol>")
    parts.append(f"<footer>Generated {html.escape(generated)} from "
                 f"{html.escape(', '.join(person['sources']))}.</footer></main></body></html>")
    return "\n".join(parts)


__all__ = ["bibtex", "build", "coverage", "page"]
