"""Suggest related work paragraph by paragraph. Plain code, no model calls.

Steps: paragraphs (the introduction, related work, background and method paragraphs
of the paper), queries (per paragraph, its most frequent content words and the
two-word phrases they form), search (each query in Semantic Scholar's paper search),
recommend (Semantic Scholar's recommendations for the paper's own arXiv id, else for
the works it cites), rank (per paragraph, the records not in the .bib by shared terms,
then recency), report (Markdown per paragraph and a candidates.bib for import_library).
"""

import json
import math
import re
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

from auto_research.trail import Trail

from ..citations import sources
from ..library import resolve
from ..library.importer import identities
from ..revision.steps.prepare.source import sectioned_paragraphs
from ..survey import search
from . import reader

S2_REC = "https://api.semanticscholar.org/recommendations/v1/papers"
SECTIONS = re.compile(r"intro|related|background|prior|preliminar|method|approach|framework|overview|our ", re.I)
MIN_WORDS = 25           # a shorter paragraph gets no query
MAX_PARAGRAPHS = 30
PER_QUERY = 10           # search records per paragraph
RECOMMENDED = 100        # recommendations for the whole paper
MAX_POSITIVE = 50        # cited works sent as positive examples
PER_PARAGRAPH = 5        # suggestions kept per paragraph
MIN_SHARED = 2           # shared terms a suggestion needs
QUERY_TERMS = 6
PHRASES = 3              # two-word phrases searched on their own per paragraph
PER_PHRASE = 15          # records per phrase search, from each source
S2_GAP = 1.1             # seconds between Semantic Scholar calls without a key (1 per second)
STOP = set("""a about above after again against all also although among an and any are as at be because been
before being below between both but by can could did do does doing done each either et al etc for from further
had has have having here how however i if in into is it its itself just less more most much many must no nor not
of off on once only or other our ours out over own per same several should since so some such than that the their
them then there these they this those through thus to too toward towards under until up upon us use used uses using
very via was we well were what when where whether which while who whom why will with within without would yet
paper papers work works approach approaches method methods propose proposed show shows shown present presents
result results based new novel first second recent recently existing prior previous existing e.g i.e eg ie fig
figure table section sec eq equation one two three can may might also often however thus hence""".split())


def related_by_paragraph(paper_dir: str, out_dir: str = "", arxiv_id: str = "") -> str:
    """Suggest works to cite for each paragraph of the introduction, related work and
    method sections.

    paper_dir is the paper's repository; workflow/rules.json names the main file and
    the .bib files. Each paragraph of at least 25 words gets a query from its own words
    (no model): its three key two-word phrases and one query of its top words, each run
    in Semantic Scholar's and OpenAlex's relevance search; Semantic Scholar's
    recommendations come from the paper's own arXiv id (arxiv_id) when given, else from
    the works it cites. Works already in the .bib are left out; each paragraph keeps the
    five scoring highest on shared terms, search rank and citations. The report
    and candidates.bib (for import_library) go to out_dir/<timestamp>/ (default
    <paper_dir>/drafts/related_by_paragraph). The paper is never modified.
    """
    paper, rules, full = reader.open_paper(paper_dir)
    base = Path(out_dir).expanduser() if out_dir else paper / "drafts" / "related_by_paragraph"
    trail = Trail("related_by_paragraph", {"paper_dir": str(paper), "out_dir": str(base), "arxiv_id": arxiv_id}, {})
    paragraphs = pick_paragraphs(full)
    if not paragraphs:
        raise ValueError("no introduction, related work or method paragraph to search for")
    refs = resolve.entries(paper)
    known = {i for r in refs for i in identities(r)}
    trail.step("paragraphs", paragraphs=[p["id"] for p in paragraphs], bib_entries=len(refs))

    sources.UNAVAILABLE.clear()
    search.UNAVAILABLE.clear()
    recommended, seeds = recommendations(arxiv_id, refs)
    trail.step("recommend", seeds=seeds, records=len(recommended))
    for p in paragraphs:
        p["records"] = paragraph_records(p)
        p["suggestions"] = rank(p, p["records"] + recommended, known)
    unavailable = sorted(sources.UNAVAILABLE | search.UNAVAILABLE)
    trail.step("search", queries=[p["query"] for p in paragraphs],
               suggestions=sum(len(p["suggestions"]) for p in paragraphs), unavailable=unavailable)
    return save(paper, base, paragraphs, {r["key"].lower() for r in refs}, seeds, unavailable, trail)


def pick_paragraphs(full: str) -> list[dict]:
    out, count = [], Counter()
    for section, para in sectioned_paragraphs(full):
        if not SECTIONS.search(section + " "):
            continue
        count[section] += 1
        text = reader.plain(para)
        if len(text.split()) < MIN_WORDS:
            continue
        query, terms = make_query(text)
        if query:
            out.append({"id": f"P{len(out) + 1}", "section": section, "index": count[section], "text": text,
                        "query": query, "terms": terms, "phrases": key_phrases(text)})
        if len(out) >= MAX_PARAGRAPHS:
            break
    return out


def tokens(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z][a-z0-9\-]+", (text or "").lower()) if w not in STOP and len(w) > 2]


def make_query(text: str) -> tuple[str, list[str]]:
    """The paragraph's query and its terms. The query is its two most frequent two-word
    phrases of adjacent content words, then its most frequent content words, six terms
    in all; the terms are its twelve most frequent content words."""
    words = re.findall(r"[a-z][a-z0-9\-]+|[^\sa-z]", text.lower())
    freq = Counter(w for w in words if w not in STOP and len(w) > 2 and w[0].isalpha())
    order = {w: i for i, w in reversed(list(enumerate(words)))}
    ranked = sorted(freq, key=lambda w: (-freq[w], order[w]))
    pairs = Counter()
    for a, b in zip(words, words[1:]):
        if a in freq and b in freq and a != b:
            pairs[(a, b)] += 1
    phrases = sorted(pairs, key=lambda p: (-pairs[p], -(freq[p[0]] + freq[p[1]]), order[p[0]]))[:2]
    parts, used = [" ".join(p) for p in phrases], {w for p in phrases for w in p}
    for w in ranked:
        if len(parts) >= QUERY_TERMS:
            break
        if w not in used:
            parts.append(w)
            used.add(w)
    return " ".join(parts), ranked[:12]


def key_phrases(text: str) -> list[str]:
    """The paragraph's most frequent two-word phrases of adjacent content words: the
    searches that find its topic, where one long query of single words finds noise."""
    words = re.findall(r"[a-z][a-z0-9\-]+|[^\sa-z]", text.lower())
    good = lambda w: w not in STOP and len(w) > 2 and w[0].isalpha()     # noqa: E731
    pairs, first = Counter(), {}
    for i, (a, b) in enumerate(zip(words, words[1:])):
        if good(a) and good(b) and a != b:
            pairs[(a, b)] += 1
            first.setdefault((a, b), i)
    ranked = sorted(pairs, key=lambda p: (-pairs[p], first[p]))
    return [" ".join(p) for p in ranked[:PHRASES]]


_last_s2 = [0.0]


def s2_paced(query: str, limit: int) -> list[dict]:
    """Semantic Scholar search, at most one call per S2_GAP without an API key."""
    if not sources._env("S2_API_KEY", "x-api-key"):
        wait = _last_s2[0] + S2_GAP - time.monotonic()
        if wait > 0:
            time.sleep(wait)
    try:
        return search.s2_search(query, limit)
    finally:
        _last_s2[0] = time.monotonic()


def paragraph_records(p: dict) -> list[dict]:
    """Search results for a paragraph: each key phrase in Semantic Scholar and OpenAlex
    (both by relevance), then the whole query; each record keeps its best search rank."""
    found = []
    for query in [*p.get("phrases", []), p["query"]]:
        groups = [s2_paced(query, PER_PHRASE),
                  search._openalex({"search": query, "per-page": PER_PHRASE, "select": search.OA_FIELDS}, "openalex")]
        for group in groups:
            for i, rec in enumerate(group):
                found.append(dict(rec, rank=i))
    return found


def seed_ids(refs: list[dict]) -> list[str]:
    out = []
    for r in refs:
        arxiv = r.get("arxiv") or ""
        doi = resolve.clean_doi(r.get("doi") or "")
        if not arxiv and doi.startswith("10.48550/arxiv."):
            arxiv = doi.split("arxiv.", 1)[1]
        if arxiv:
            out.append("ArXiv:" + re.sub(r"v\d+$", "", arxiv))
        elif doi:
            out.append("DOI:" + doi)
    return out[:MAX_POSITIVE]


def recommendations(arxiv_id: str, refs: list[dict]) -> tuple[list[dict], list[str]]:
    """Semantic Scholar's recommendations: for the paper itself when it is on arXiv
    (GET .../forpaper/ArXiv:<id>, from all of CS), else for its cited works as positive
    examples (POST .../papers). Returns the records and the ids sent."""
    params = {"fields": search.S2_FIELDS, "limit": RECOMMENDED}
    key = sources._env("S2_API_KEY", "x-api-key")
    if arxiv_id.strip():
        seeds = ["ArXiv:" + re.sub(r"v\d+$", "", arxiv_id.strip().removeprefix("arXiv:").removeprefix("ArXiv:"))]
        d = sources.fetch(f"{S2_REC}/forpaper/{seeds[0]}", {**params, "from": "all-cs"},
                          label="Semantic Scholar recommendations", headers=key)
    else:
        seeds = seed_ids(refs)
        if not seeds:
            return [], []
        d = reader.post(S2_REC, params, {"positivePaperIds": seeds}, label="Semantic Scholar recommendations",
                        headers=key)
    return [search._s2(p, "s2 recommendations") for p in (d or {}).get("recommendedPapers") or []
            if p.get("title")], seeds


def rank(p: dict, records: list[dict], known: set[str]) -> list[dict]:
    best_rank = {}
    for r in records:
        if "rank" in r:
            for i in identities({"doi": r.get("doi", ""), "arxiv": r.get("arxiv", ""), "title": r.get("title", "")}):
                best_rank[i] = min(best_rank.get(i, 99), r["rank"])
    seen, out = set(), []
    for rec in search.merge([[dict(r, found_by=list(r["found_by"])) for r in records]]):
        if any(i in known for i in identities({"doi": rec["doi"], "arxiv": rec["arxiv"], "title": rec["title"]})):
            continue
        shared = [t for t in p["terms"] if t in set(tokens(rec["title"] + " " + rec["abstract"]))]
        if len(shared) < MIN_SHARED or rec["id"] in seen:
            continue
        seen.add(rec["id"])
        ids = identities({"doi": rec["doi"], "arxiv": rec["arxiv"], "title": rec["title"]})
        rank_ = min([best_rank.get(i, 99) for i in ids] or [99])
        out.append(dict(rec, shared=shared, score=score(len(shared), rank_, rec.get("citations"))))
    out.sort(key=lambda r: (-r["score"], -(r["year"] or 0)))
    return out[:PER_PARAGRAPH]


def score(shared: int, rank_: int, citations) -> float:
    """Shared terms, how high a search ranked the work (a phrase search's top hits are on
    the topic), and how established it is (log of its citations)."""
    relevance = max(0.0, 3.0 - rank_ / 5) if rank_ < 99 else 0.0
    return shared + relevance + 0.7 * math.log10(1 + (citations or 0))


def bib_key(rec: dict, taken: set[str]) -> str:
    last = re.sub(r"[^a-z]", "", (rec["authors"][0].split()[-1] if rec["authors"] and rec["authors"][0].split()
                                  else "anon").lower()) or "anon"
    first = next((w for w in tokens(rec["title"])), "work")
    key = base = f"{last}{rec['year'] or ''}{re.sub(r'[^a-z0-9]', '', first)}"
    n = 2
    while key.lower() in taken:
        key, n = f"{base}_{n}", n + 1
    taken.add(key.lower())
    return key


def bib_entry(rec: dict, key: str) -> str:
    def esc(s):
        return str(s).replace("{", "").replace("}", "")
    venue = rec["venue"] if rec["venue"] and rec["venue"].lower() != "arxiv" else ""
    fields = [("title", rec["title"]), ("author", " and ".join(rec["authors"])), ("year", rec["year"] or ""),
              ("journal" if venue else "howpublished", venue or (f"arXiv:{rec['arxiv']}" if rec["arxiv"] else "")),
              ("doi", "" if rec["doi"].startswith("10.48550/") else rec["doi"]), ("eprint", rec["arxiv"]),
              ("archivePrefix", "arXiv" if rec["arxiv"] else ""), ("url", rec["url"])]
    lines = ",\n".join(f"  {k} = {{{esc(v)}}}" for k, v in fields if v)
    return f"@{'article' if venue else 'misc'}{{{key},\n{lines}\n}}"


def save(paper: Path, base: Path, paragraphs: list[dict], taken: set[str], seeds: list[str],
         unavailable: list[str], trail: Trail) -> str:
    out = base / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    out.mkdir(parents=True, exist_ok=True)
    keys, entries = {}, []
    for p in paragraphs:
        for s in p["suggestions"]:
            if s["id"] not in keys:
                keys[s["id"]] = bib_key(s, taken)
                entries.append(bib_entry(s, keys[s["id"]]))
            s["key"] = keys[s["id"]]
    (out / "candidates.bib").write_text("\n\n".join(entries) + ("\n" if entries else ""), encoding="utf-8")
    status = "PASS_WITH_NOTES" if unavailable else "PASS"
    record = {"status": status, "seeds": seeds, "unavailable_sources": unavailable,
              "paragraphs": [{k: p[k] for k in ("id", "section", "index", "query", "phrases", "terms")} | {
                  "suggestions": [{k: s[k] for k in ("key", "title", "year", "venue", "url", "shared", "found_by")}
                                  for s in p["suggestions"]]} for p in paragraphs]}
    (out / "related.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "report.md").write_text(render(record, paragraphs), encoding="utf-8")
    trail.step("report", status=status, candidates=len(entries))
    run = trail.save(out, report=out / "report.md", json=out / "related.json", bib=out / "candidates.bib")
    return json.dumps({"status": status, "report": str(out / "report.md"), "bib": str(out / "candidates.bib"),
                       "json": str(out / "related.json"), "run": str(run), "paragraphs": len(paragraphs),
                       "candidates": len(entries), "unavailable_sources": unavailable}, ensure_ascii=False)


def render(r: dict, paragraphs: list[dict]) -> str:
    total = len({s["key"] for p in r["paragraphs"] for s in p["suggestions"]})
    lines = ["# 按段落推荐相关工作", "", f"结论：{r['status']}　{len(paragraphs)} 段，{total} 篇候选（都不在 .bib 里）", "",
             "候选都在 `candidates.bib` 里；挑好后用 `import_library(paper_dir, \"<这个文件夹>/candidates.bib\")` 导入，"
             "再删掉不要的条目。", ""]
    if r["unavailable_sources"]:
        lines += [f"> **注意：{'、'.join(r['unavailable_sources'])} 这次有请求失败**，候选可能不全。", ""]
    for p, rec in zip(paragraphs, r["paragraphs"]):
        lines += [f"## {p['section']} 第 {p['index']} 段", "", f"> {' '.join(p['text'].split()[:40])} …", "",
                  f"检索词：`{p['query']}`", ""]
        if not rec["suggestions"]:
            lines += ["没有共享两个以上术语的候选。", ""]
            continue
        for s in rec["suggestions"]:
            lines.append(f"- {s['title']}（{s['year'] or '?'}，{s['venue'] or '—'}）`{s['key']}`"
                         + (f" {s['url']}" if s["url"] else "") + f"　共享术语：{', '.join(s['shared'])}")
        lines.append("")
    return "\n".join(lines)


__all__ = ["make_query", "related_by_paragraph"]
