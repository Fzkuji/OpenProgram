"""Recommend venues for the paper from where its references and similar works were
published, with the next deadline and the venue's facts. Plain code, no model calls.

Learned from Elsevier JournalFinder, Springer Journal Suggester and Web of Science's
manuscript matcher: match the manuscript's text against published works and count
their venues; here the paper's own reference list counts too.
"""

import json
import re
from collections import defaultdict
from datetime import date, datetime

from auto_research.tasks.citations import sources
from auto_research.tasks.library import resolve as lib
from auto_research.tasks.venue import facts
from auto_research.trail import Trail

from . import yamlite
from .common import cell, paper_folder, paper_text, run_folder

HALF_LIFE = 5            # years: a reference this much older counts half
SIMILAR = 100            # similar works read from OpenAlex
SIMILAR_YEARS = 5        # how far back similar works go
KEEP = 5                 # venues recommended at most
LEAST = 3                # and at least, when that many are found
JOURNALS = {"tacl", "tmlr", "jmlr"}
CCFDDL = "https://raw.githubusercontent.com/ccfddl/ccf-deadlines/main/conference/{}.yml"
REPOSITORY = re.compile(r"arxiv|biorxiv|medrxiv|ssrn|zenodo|research square|preprints|openreview|hal\b|figshare", re.I)

# Known venues: key, display name, venue family of tasks/venue/facts.py ("" when none),
# ccfddl file (area/name, "" for journals with rolling submission), name patterns on
# the OpenAlex source name. Earlier rows win, so the narrower patterns come first.
VENUES = (
    ("naacl", "NAACL", "arr", "AI/naacl", r"north american chapter|\bnaacl\b"),
    ("eacl", "EACL", "arr", "AI/eacl", r"european chapter of the association for computational|\beacl\b"),
    ("tacl", "TACL", "", "", r"transactions of the association for computational"),
    ("emnlp", "EMNLP", "arr", "AI/emnlp", r"empirical methods in natural language|\bemnlp\b"),
    ("acl", "ACL", "arr", "AI/acl", r"association for computational linguistics|\bacl\b"),
    ("coling", "COLING", "", "AI/coling", r"computational linguistics.*coling|\bcoling\b|international conference on computational linguistics"),
    ("colm", "COLM", "", "AI/colm", r"conference on language model|\bcolm\b"),
    ("neurips", "NeurIPS", "neurips", "AI/nips", r"neural information processing systems|\bneurips\b|\bnips\b"),
    ("iclr", "ICLR", "iclr", "AI/iclr", r"learning representations|\biclr\b"),
    ("icml", "ICML", "icml", "AI/icml", r"international conference on machine learning|\bicml\b"),
    ("aistats", "AISTATS", "", "AI/aistats", r"artificial intelligence and statistics|\baistats\b"),
    ("uai", "UAI", "", "AI/uai", r"uncertainty in artificial intelligence|\buai\b"),
    ("cvpr", "CVPR", "cvpr", "AI/cvpr", r"computer vision and pattern recognition|\bcvpr\b"),
    ("eccv", "ECCV", "eccv", "AI/eccv", r"european conference on computer vision|\beccv\b"),
    ("iccv", "ICCV", "cvpr", "AI/iccv", r"international conference on computer vision|\biccv\b"),
    ("wacv", "WACV", "", "AI/wacv", r"winter conference on applications of computer vision|\bwacv\b"),
    ("aaai", "AAAI", "aaai", "AI/aaai", r"\baaai\b"),
    ("ijcai", "IJCAI", "ijcai", "AI/ijcai", r"international joint conference on artificial intelligence|\bijcai\b"),
    ("kdd", "KDD", "acm", "DB/sigkdd", r"knowledge discovery (?:and|&) data mining|\bkdd\b"),
    ("sigir", "SIGIR", "acm", "DB/sigir", r"research and development in information retrieval|\bsigir\b"),
    ("wsdm", "WSDM", "acm", "DB/wsdm", r"web search and data mining|\bwsdm\b"),
    ("cikm", "CIKM", "acm", "DB/cikm", r"information and knowledge management|\bcikm\b"),
    ("www", "The Web Conference (WWW)", "acm", "", r"the web conference|world wide web conference"),
    ("chi", "CHI", "acm", "HI/chi", r"human factors in computing systems|\bchi\b"),
    ("icra", "ICRA", "ieee", "AI/icra", r"international conference on robotics and automation|\bicra\b"),
    ("iros", "IROS", "ieee", "AI/iros", r"intelligent robots and systems|\biros\b"),
    ("tmlr", "TMLR", "tmlr", "", r"transactions on machine learning research"),
    ("jmlr", "JMLR", "jmlr", "", r"journal of machine learning research"),
)


def venue_pick(paper_dir: str, out_dir: str = "") -> str:
    """Recommend three to five venues for the paper.

    paper_dir is the project's repository. Two counts are merged: the venues of the
    paper's references (each .bib entry resolved to an OpenAlex work, a reference
    weighted by recency, half weight every five years) and the venues of the hundred
    works OpenAlex ranks most relevant to the paper's title and abstract (last five
    years). Preprint servers are left out. Each venue is matched to a family of
    tasks/venue/facts.py when one fits (page limit, double-blind), else keeps its
    OpenAlex name; the next deadline after today comes from ccfddl. Writes report.md
    and venue_pick.json to out_dir/<timestamp>/ (default <paper_dir>/drafts/venue_pick);
    workflow/venue.md is never changed.
    """
    paper = paper_folder(paper_dir)
    trail = Trail("venue_pick", {"paper_dir": str(paper)}, {})
    sources.UNAVAILABLE.clear()
    today = date.today()
    title, abstract = paper_text(paper)

    refs = defaultdict(float)
    entries = lib.entries(paper)
    resolved, works = 0, []
    for ref in entries:
        work, _ = lib.resolve(ref)
        if work:
            works.append(work)
    places = locations([w["id"] for w in works if w.get("id")])
    for work in works:
        resolved += 1
        name = venue_name(dict(work, locations=places.get(work.get("id"), []))) or published_as(work.get("title"))
        if name:
            year = work.get("publication_year") or today.year
            refs[name] += 0.5 ** (max(0, today.year - int(year)) / HALF_LIFE)
    trail.step("references", entries=len(entries), resolved=resolved, venues=len(refs))

    similar = defaultdict(float)
    # OpenAlex's search wants every word of a long text, so a whole abstract finds
    # nothing: search the title and the abstract's first sentence apart and merge.
    first = re.split(r"(?<=[.!?])\s+", " ".join(abstract.split()), maxsplit=1)[0] if abstract else ""
    text = " | ".join(q for q in (title, " ".join(first.split()[:30])) if q)
    found, ids = [], set()
    for q in (title, " ".join(first.split()[:30])):
        for w in similar_works(q, today.year - SIMILAR_YEARS) if q else []:
            if w["id"] not in ids:
                ids.add(w["id"])
                found.append(w)
    for w in found:
        name = venue_name(w)
        if name:
            similar[name] += 1
    trail.step("similar", query=text[:200], found=len(found), venues=len(similar))

    picks = merge(refs, similar)
    deadlines = {}
    for p in picks:
        if p["ccfddl"] and p["ccfddl"] not in deadlines:
            deadlines[p["ccfddl"]] = next_deadline(p["ccfddl"], today)
        p["next_deadline"] = deadlines.get(p["ccfddl"]) or None
        p["why"] = why(p)
    out = run_folder(paper, out_dir, "venue_pick")
    record = {"title": title, "references": len(entries), "resolved": resolved, "similar_works": len(found),
              "candidates": picks, "unavailable_sources": sorted(sources.UNAVAILABLE)}
    (out / "venue_pick.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "report.md").write_text(render(record), encoding="utf-8")
    trail.step("pick", venues=[p["name"] for p in picks])
    run = trail.save(out, report=out / "report.md", json=out / "venue_pick.json")
    status = "PASS" if len(picks) >= LEAST and not record["unavailable_sources"] else "PASS_WITH_NOTES"
    return json.dumps({"status": status, "report": str(out / "report.md"), "json": str(out / "venue_pick.json"),
                       "run": str(run), "venues": [p["name"] for p in picks],
                       "unavailable_sources": record["unavailable_sources"]}, ensure_ascii=False)


def venue_name(work: dict) -> str:
    """Where a work was published: its primary source, or when that is a preprint server
    (an arXiv-first paper), the first other location that is a journal or conference;
    "" when it only exists as a preprint."""
    for loc in [work.get("primary_location") or {}, *(work.get("locations") or [])]:
        src = loc.get("source") or {}
        name = (src.get("display_name") or "").strip()
        if name and src.get("type") != "repository" and not REPOSITORY.search(name):
            return name
    return ""


def published_as(title: str | None) -> str:
    """The venue of the published record of a preprint: OpenAlex often keeps the arXiv
    version and the conference version as two works with the same title."""
    if not title:
        return ""
    d = sources.fetch("https://api.openalex.org/works",
                      {"filter": f"title.search:{title.replace(',', ' ')}", "per-page": 10,
                       "select": "id,title,primary_location,locations",
                       **sources._env("OPENALEX_API_KEY", "api_key")}, label="OpenAlex")
    want = re.sub(r"\W+", " ", title.lower()).strip()
    for w in (d or {}).get("results") or []:
        if re.sub(r"\W+", " ", (w.get("title") or "").lower()).strip() == want:
            name = venue_name(w)
            if name:
                return name
    return ""


def locations(ids: list[str]) -> dict[str, list]:
    """Every location of these OpenAlex works, fifty per request."""
    out = {}
    keys = [i.rsplit("/", 1)[-1] for i in ids]
    for k in range(0, len(keys), 50):
        d = sources.fetch("https://api.openalex.org/works",
                          {"filter": "openalex:" + "|".join(keys[k:k + 50]), "per-page": 50, "select": "id,locations",
                           **sources._env("OPENALEX_API_KEY", "api_key")}, label="OpenAlex")
        for w in (d or {}).get("results") or []:
            out[w["id"]] = w.get("locations") or []
    return out


def similar_works(text: str, since: int) -> list[dict]:
    d = sources.fetch("https://api.openalex.org/works",
                      {"search": text, "filter": f"from_publication_date:{since}-01-01", "per-page": SIMILAR,
                       "select": "id,title,publication_year,primary_location,locations",
                       **sources._env("OPENALEX_API_KEY", "api_key")}, label="OpenAlex")
    return [w for w in (d or {}).get("results") or [] if w.get("id")]


def known(name: str) -> tuple | None:
    for row in VENUES:
        if re.search(row[4], name, re.I):
            return row
    return None


def merge(refs: dict, similar: dict) -> list[dict]:
    """Known venues merge across their OpenAlex names; the score is the mean of the two
    shares. The top five by score, at least three when found."""
    total_r, total_s = sum(refs.values()) or 1, sum(similar.values()) or 1
    groups: dict[str, dict] = {}
    for counts, field, total in ((refs, "reference_share", total_r), (similar, "similar_share", total_s)):
        for name, n in counts.items():
            row = known(name)
            key = row[0] if row else name.lower()
            g = groups.setdefault(key, {"key": key, "name": row[1] if row else name, "openalex_names": [],
                                        "family": row[2] if row else "", "ccfddl": row[3] if row else "",
                                        "reference_share": 0.0, "similar_share": 0.0,
                                        "known": bool(row), "journal": key in JOURNALS,
                                        "reference_weight": 0.0, "similar_count": 0})
            if name not in g["openalex_names"]:
                g["openalex_names"].append(name)
            g[field] += n / total
            g["reference_weight" if field == "reference_share" else "similar_count"] += n
    for g in groups.values():
        g["reference_share"] = round(g["reference_share"], 3)
        g["similar_share"] = round(g["similar_share"], 3)
        g["reference_weight"] = round(g["reference_weight"], 2)
        g["similar_count"] = int(g["similar_count"])
        g["score"] = round((g["reference_share"] + g["similar_share"]) / 2, 3)
        f = facts.FAMILIES.get(g["family"]) if g["family"] else None
        g["page_limit"] = f.main_pages if f else None
        g["pages_note"] = f.references if f else ""
        g["double_blind"] = f.anonymous if f else None
        g["template"] = f.template if f else ""
    ranked = sorted(groups.values(), key=lambda g: (-g["score"], g["name"]))
    return ranked[:KEEP]


def next_deadline(path: str, today: date) -> dict | None:
    """The first full-paper deadline on or after today in ccfddl's file of one venue."""
    body = sources.fetch(CCFDDL.format(path), as_json=False, label="ccfddl")
    if not body:
        return None
    try:
        return upcoming(yamlite.load(body), today)
    except ValueError:
        return None


def upcoming(data, today: date) -> dict | None:
    best = None
    for conf in data if isinstance(data, list) else [data]:
        for edition in (conf or {}).get("confs") or []:
            for step in edition.get("timeline") or []:
                when = str(step.get("deadline") or "")
                m = re.match(r"(\d{4})-(\d{2})-(\d{2})", when)
                if not m:
                    continue
                day = date(*map(int, m.groups()))
                if day >= today and (best is None or day < best[0]):
                    best = (day, {"deadline": when, "abstract_deadline": str(step.get("abstract_deadline") or ""),
                                  "timezone": str(edition.get("timezone") or ""), "year": edition.get("year"),
                                  "link": edition.get("link") or "", "comment": step.get("comment") or ""})
    return best[1] if best else None


def why(p: dict) -> str:
    parts = []
    if p["reference_share"]:
        parts.append(f"参考文献按新近加权 {p['reference_share']:.0%} 发表在这里")
    if p["similar_share"]:
        parts.append(f"最相似的近期工作中 {p['similar_share']:.0%}（{p['similar_count']} 篇）发表在这里")
    if p["journal"]:
        parts.append("期刊滚动投稿，没有统一截稿日")
    elif not p["known"]:
        parts.append("不在已知去向表中，截稿和格式请到官网确认")
    elif not p["next_deadline"]:
        parts.append("没查到今天之后的截稿日期")
    return "；".join(parts)


def render(r: dict) -> str:
    lines = ["# 投稿去向推荐", "", f"论文：{r['title'] or '（未读到标题）'}",
             f"参考文献 {r['references']} 条，解析到 OpenAlex {r['resolved']} 条；相似工作 {r['similar_works']} 篇。", ""]
    if not r["candidates"]:
        lines += ["没有找到可推荐的去向：参考文献没能解析，相似工作也没有查到。", ""]
    else:
        lines += ["| 去向 | 得分 | 参考文献占比 | 相似工作占比 | 下一截稿 | 正文页数 | 双盲 | 理由 |",
                  "| --- | --- | --- | --- | --- | --- | --- | --- |"]
        for p in r["candidates"]:
            d = p["next_deadline"]
            dl = f"{d['deadline']} {d['timezone']}".strip() if d else "—"
            pages = str(p["page_limit"]) if p["page_limit"] else ("不限" if p["family"] else "—")
            blind = {True: "是", False: "否", None: "—"}[p["double_blind"]]
            lines.append(f"| {cell(p['name'])} | {p['score']:.2f} | {p['reference_share']:.0%} | {p['similar_share']:.0%} "
                         f"| {cell(dl)} | {pages} | {blind} | {cell(p['why'])} |")
        lines.append("")
    if r["unavailable_sources"]:
        lines += [f"没能查询：{'、'.join(r['unavailable_sources'])}。", ""]
    lines += ["截稿来自 ccfddl/ccf-deadlines，页数和双盲来自 venue 任务的模板事实，投稿前以官网为准。本报告不改 workflow/venue.md。", ""]
    return "\n".join(lines)


__all__ = ["VENUES", "merge", "next_deadline", "upcoming", "venue_name", "venue_pick"]
