"""After the paper is out: who cites it, how, and what readers say about it. Plain code,
no model calls.

Learned from Scite's citation alerts (each citing sentence classed as supporting,
contrasting or mentioning) and alphaXiv's comments on arXiv papers.

Stance rules, on one citing sentence or comment (lower case), in this order:
  contrasting  a CONTRAST cue: however, in contrast, contrary to, fail(s/ed) to,
               could not reproduce/replicate, inconsistent with, contradict, disagree,
               challenge(s) the claim/finding/result, overestimate, does not hold, limitation of, flawed, ...
  supporting   a SUPPORT cue: consistent with, in line with, agree(s) with, confirm,
               corroborate, support(s/ed) the/their/these finding(s)/result(s)/claim(s),
               replicate/reproduce the result(s), as (also) shown/found/observed by;
               or Semantic Scholar's intent "result" with a weak cue (similar, comparable,
               as reported)
  mentioning   otherwise
A comment with an ERRATUM cue (an error in, a bug, mistake, typo, incorrect, erratum,
errata, wrong, data leakage, could not reproduce), or a contrasting citing sentence with
one, needs an erratum check; any other contrasting sentence and an unanswered comment
need a reply. Those come first.
"""

import csv
import hashlib
import io
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from auto_research import stages
from auto_research.tasks.citations import sources
from auto_research.tasks.library.resolve import clean_doi
from auto_research.trail import Trail

from .common import cell, paper_folder, project_field, rules, run_folder

COLUMNS = ("id", "kind", "stance", "quote", "handled", "title", "url", "date", "needs")
STANCES = ("supporting", "contrasting", "mentioning")
CONTRAST = re.compile(
    r"\bhowever\b|\bin contrast\b|\bcontrary to\b|\bfail(?:s|ed)? to\b|\b(?:could|can|did) ?not "
    r"(?:reproduce|replicate|confirm)\b|\bnot reproducible\b|\binconsistent with\b|\bcontradict\w*|\bdisagree\w*|"
    r"\bchalleng(?:e|es|ed|ing) (?:the|their|these|this) (?:claim|finding|result|conclusion)s?\b|\boverestimat\w*|\bunderestimat\w*|\bdoes not hold\b|\bdo not hold\b|\blimitations? of\b|"
    r"\bflawed\b|\bproblematic\b|\bnot consistent with\b|\bin conflict with\b|\bdiffers? from (?:the|their) (?:finding|result)")
SUPPORT = re.compile(
    r"\bconsistent with\b|\bin line with\b|\bagrees? with\b|\bconfirm\w*|\bcorroborat\w*|"
    r"\bsupport(?:s|ed|ing)? (?:the|their|these|this|our) (?:finding|result|claim|observation|conclusion)s?\b|"
    r"\b(?:replicat|reproduc)\w* (?:the|their) (?:result|finding)s?\b|\bas (?:also )?(?:shown|found|observed|demonstrated) (?:by|in)\b")
WEAK = re.compile(r"\bsimilar\b|\bcomparable\b|\bas reported\b")
ERRATUM = re.compile(r"\ban error in\b|\ba bug\b|\bmistakes?\b|\btypos?\b|\bincorrect\w*|\berrat(?:um|a)\b|\bwrong\b|"
                     r"\bdata leakage\b|\b(?:could|can|did) ?not reproduce\b|\bfailed to reproduce\b")
ARXIV = re.compile(r"(\d{4}\.\d{4,5})(?:v\d+)?")
ALPHAXIV = "https://api.alphaxiv.org/papers/v3/"
ORDER = {"erratum": 0, "reply": 1, "": 2}
SHOWN = 100          # rows per report table


def after_release(paper_dir: str, work: str = "", out_dir: str = "") -> str:
    """List who cites the published paper and what readers say, newest items first.

    work is the published version: an OpenAlex id, a DOI or an arXiv id (empty: the
    `published` key of workflow/rules.json, else the `Published:` line of
    workflow/project.md). Sources: OpenAlex works citing it, Semantic Scholar citation
    contexts with intents, alphaXiv comments (arXiv papers) and OpenReview public and
    official comments when rules.json `openreview` or project.md `OpenReview:` names a
    forum. Each citing sentence or comment gets a stance by the rules of this module.
    Items not seen before are added to workflow/after_release.csv (id, kind, stance,
    quote, handled, title, url, date, needs); the author's handled column is kept. The
    report lists new items, those needing an erratum check or a reply first, then
    items still unhandled. Writes report.md and after_release.json to
    out_dir/<timestamp>/ (default <paper_dir>/drafts/after_release).
    """
    paper = paper_folder(paper_dir)
    target = (work or str(rules(paper).get("published") or "") or project_field(paper, "Published")).strip()
    forum = forum_id(str(rules(paper).get("openreview") or "") or project_field(paper, "OpenReview") or target)
    if not target and not forum:
        raise ValueError("no published version: pass work, or set `published` in workflow/rules.json "
                         "or a `Published:` line in workflow/project.md")
    trail = Trail("after_release", {"paper_dir": str(paper), "work": target, "forum": forum}, {})
    sources.UNAVAILABLE.clear()
    notes = []
    ids = identify(target) if target and not target.startswith("https://openreview") else {}
    oa = openalex_work(ids) if ids else None
    if oa:
        ids.setdefault("doi", clean_doi(oa.get("doi") or ""))
        for loc in oa.get("locations") or []:
            m = ARXIV.search(loc.get("landing_page_url") or "")
            if m and "arxiv.org" in (loc.get("landing_page_url") or ""):
                ids.setdefault("arxiv", m.group(1))
    if ids.get("doi", "").startswith("10.48550/arxiv."):
        ids.setdefault("arxiv", ids["doi"].split("arxiv.", 1)[1])
    items = []
    contexts = s2_contexts(ids) if ids else []
    items += contexts
    with_context = {i["_paper"] for i in contexts}
    if oa:
        items += [i for i in citing(oa["id"]) if i["_paper"] not in with_context]
    elif target:
        notes.append("OpenAlex 没有找到这篇论文")
    if ids.get("arxiv"):
        found = alphaxiv_comments(ids["arxiv"])
        if found is None:
            notes.append("alphaXiv 评论没有取到")
        items += found or []
    if forum:
        items += openreview_comments(forum)
    trail.step("collect", work=ids, items=len(items), sources={k: sum(i["kind"] == k for i in items)
                                                               for k in ("citing", "context", "alphaxiv", "openreview")})
    rows = load(paper)
    seen = {r["id"] for r in rows}
    new = [strip(i) for i in items if i["id"] not in seen]
    rows += new
    save(paper, rows)
    new = ordered(new)
    open_rows = ordered([r for r in rows if r["needs"] and not r["handled"]])
    out = run_folder(paper, out_dir, "after_release")
    record = {"work": ids, "openalex": (oa or {}).get("id", ""), "title": (oa or {}).get("title", ""),
              "forum": forum, "new": new, "open": open_rows, "total": len(rows),
              "counts": {s: sum(r["stance"] == s for r in rows) for s in STANCES},
              "notes": notes, "unavailable_sources": sorted(sources.UNAVAILABLE)}
    (out / "after_release.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "report.md").write_text(render(record), encoding="utf-8")
    run = trail.save(out, report=out / "report.md", json=out / "after_release.json", state="workflow/after_release.csv")
    status = "PASS_WITH_NOTES" if open_rows or notes or record["unavailable_sources"] else "PASS"
    return json.dumps({"status": status, "report": str(out / "report.md"), "json": str(out / "after_release.json"),
                       "run": str(run), "state": "workflow/after_release.csv", "new": len(new),
                       "needs_action": len(open_rows), "counts": record["counts"],
                       "unavailable_sources": record["unavailable_sources"]}, ensure_ascii=False)


# Stance

def stance(text: str, intents=()) -> str:
    """The rules in the module docstring."""
    t = " ".join((text or "").lower().split())
    if CONTRAST.search(t):
        return "contrasting"
    if SUPPORT.search(t) or ("result" in [i.lower() for i in intents or ()] and WEAK.search(t)):
        return "supporting"
    return "mentioning"


def needs(text: str, kind: str, stance_: str, answered: bool = False) -> str:
    t = " ".join((text or "").lower().split())
    if ERRATUM.search(t) and (kind != "context" or stance_ == "contrasting"):
        return "erratum"
    if stance_ == "contrasting" or (kind in ("alphaxiv", "openreview") and not answered):
        return "reply"
    return ""


def ordered(rows: list[dict]) -> list[dict]:
    """Erratum checks, then replies, then the rest; contrasting before supporting before
    mentioning; newest first within each."""
    rows = sorted(rows, key=lambda r: r.get("date") or "", reverse=True)
    return sorted(rows, key=lambda r: (ORDER.get(r.get("needs", ""), 2),
                                       {"contrasting": 0, "supporting": 1}.get(r.get("stance"), 2)))


# Identifiers

def identify(work: str) -> dict:
    w = work.strip()
    m = re.search(r"(?:openalex\.org/)?\b(W\d{4,})\b", w, re.I)
    if m:
        return {"openalex": m.group(1).upper()}
    m = re.search(r"10\.\d{4,9}/\S+", w)
    if m and not m.group(0).lower().startswith("10.48550/"):
        return {"doi": clean_doi(m.group(0))}
    m = ARXIV.search(w)
    if m:
        return {"arxiv": m.group(1), "doi": f"10.48550/arxiv.{m.group(1)}"}
    raise ValueError(f"cannot read {work!r} as an OpenAlex id, a DOI or an arXiv id")


def forum_id(value: str) -> str:
    m = re.search(r"openreview\.net/(?:forum|pdf)\?id=([\w-]+)", value or "")
    if m:
        return m.group(1)
    v = (value or "").strip()
    return v if re.fullmatch(r"[A-Za-z0-9_-]{8,14}", v) and not re.fullmatch(r"W\d+", v) else ""


def openalex_work(ids: dict) -> dict | None:
    path = ids.get("openalex") or ("doi:" + ids["doi"] if ids.get("doi") else "")
    if not path:
        return None
    d = sources.fetch("https://api.openalex.org/works/" + path,
                      {"select": "id,doi,title,publication_date,locations",
                       **sources._env("OPENALEX_API_KEY", "api_key")}, label="OpenAlex")
    return d if isinstance(d, dict) and d.get("id") else None


def paper_key(title: str, doi: str = "", arxiv: str = "") -> str:
    if arxiv:
        return "arxiv:" + arxiv
    if doi:
        return "doi:" + clean_doi(doi)
    return "t:" + re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()[:80]


def short_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]


# Sources

def citing(openalex_id: str) -> list[dict]:
    """OpenAlex works that cite the paper, newest first (no citing sentence)."""
    d = sources.fetch("https://api.openalex.org/works",
                      {"filter": f"cites:{openalex_id.rsplit('/', 1)[-1]}", "sort": "publication_date:desc",
                       "per-page": 200, "select": "id,doi,title,publication_date,locations",
                       **sources._env("OPENALEX_API_KEY", "api_key")}, label="OpenAlex")
    out = []
    for w in (d or {}).get("results") or []:
        arxiv = next((m.group(1) for loc in w.get("locations") or []
                      if "arxiv.org" in (loc.get("landing_page_url") or "")
                      and (m := ARXIV.search(loc.get("landing_page_url") or ""))), "")
        doi = clean_doi(w.get("doi") or "")
        if doi.startswith("10.48550/arxiv."):
            arxiv = arxiv or doi.split("arxiv.", 1)[1]
        out.append({"id": "oa:" + w["id"].rsplit("/", 1)[-1], "kind": "citing", "stance": "mentioning", "quote": "",
                    "handled": "", "title": w.get("title") or "", "url": w["id"], "date": w.get("publication_date") or "",
                    "needs": "", "_paper": paper_key(w.get("title"), "" if arxiv else doi, arxiv)})
    return out


def s2_contexts(ids: dict) -> list[dict]:
    """Semantic Scholar's citing sentences, one item per sentence."""
    pid = f"ARXIV:{ids['arxiv']}" if ids.get("arxiv") else f"DOI:{ids['doi']}" if ids.get("doi") else ""
    if not pid:
        return []
    d = sources.fetch(f"https://api.semanticscholar.org/graph/v1/paper/{pid}/citations",
                      {"fields": "contexts,intents,isInfluential,title,year,externalIds,url,publicationDate",
                       "limit": 1000}, label="Semantic Scholar", headers=sources._env("S2_API_KEY", "x-api-key"))
    out = []
    for c in (d or {}).get("data") or []:
        p = c.get("citingPaper") or {}
        ext = p.get("externalIds") or {}
        key = paper_key(p.get("title"), "" if ext.get("ArXiv") else ext.get("DOI", ""), ext.get("ArXiv", ""))
        for text in c.get("contexts") or []:
            s = stance(text, c.get("intents") or [])
            out.append({"id": f"s2:{p.get('paperId', '')}:{short_hash(text)}", "kind": "context", "stance": s,
                        "quote": " ".join(text.split())[:500], "handled": "", "title": p.get("title") or "",
                        "url": p.get("url") or "", "date": p.get("publicationDate") or str(p.get("year") or ""),
                        "needs": needs(text, "context", s), "_paper": key})
    return out


def alphaxiv_comments(arxiv: str) -> list[dict] | None:
    """Top-level comments on alphaXiv: the paper's group id from papers/v3/<arXiv id>,
    then papers/v3/legacy/<group>/comments. None when alphaXiv does not answer."""
    meta = sources.fetch(ALPHAXIV + arxiv, label="alphaXiv")
    group = (meta or {}).get("groupId") if isinstance(meta, dict) else None
    if not group:
        return None if meta is None else []
    d = sources.fetch(f"{ALPHAXIV}legacy/{group}/comments", label="alphaXiv")
    if d is None:
        return None
    out = []
    for c in d if isinstance(d, list) else []:
        if c.get("isAuthor"):
            continue
        text = " ".join(x for x in (c.get("title"), c.get("body")) if x)
        s = stance(text)
        answered = bool(c.get("authorResponded")) or any(r.get("isAuthor") for r in c.get("responses") or [])
        out.append({"id": f"ax:{c.get('id', short_hash(text))}", "kind": "alphaxiv", "stance": s,
                    "quote": " ".join(text.split())[:500], "handled": "", "title": c.get("title") or "",
                    "url": f"https://www.alphaxiv.org/abs/{arxiv}", "date": (c.get("date") or "")[:10],
                    "needs": needs(text, "alphaxiv", s, answered), "_paper": ""})
    return out


def openreview_comments(forum: str) -> list[dict]:
    d = sources.fetch("https://api2.openreview.net/notes", {"forum": forum, "limit": 1000}, label="OpenReview")
    out = []
    for n in (d or {}).get("notes") or []:
        inv = " ".join(n.get("invitations") or [n.get("invitation") or ""])
        if n.get("id") == forum or not re.search(r"Comment", inv):
            continue
        content = n.get("content") or {}
        value = lambda k: (content.get(k) or {}).get("value") if isinstance(content.get(k), dict) else content.get(k)
        if any("Authors" in s for s in n.get("signatures") or []):
            continue
        text = " ".join(str(x) for x in (value("title"), value("comment")) if x)
        s = stance(text)
        replied = any(any("Authors" in s2 for s2 in m.get("signatures") or []) and m.get("replyto") == n.get("id")
                      for m in d.get("notes") or [])
        ms = n.get("cdate") or n.get("tcdate") or 0
        when = datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d") if ms else ""
        out.append({"id": f"or:{n.get('id')}", "kind": "openreview", "stance": s, "quote": " ".join(text.split())[:500],
                    "handled": "", "title": value("title") or "", "url": f"https://openreview.net/forum?id={forum}&noteId={n.get('id')}",
                    "date": when, "needs": needs(text, "openreview", s, replied), "_paper": ""})
    return out


# State

def path(paper: Path) -> Path:
    return stages.folder(paper) / "after_release.csv"


def load(paper: Path) -> list[dict]:
    p = path(paper)
    if not p.is_file():
        return []
    with io.StringIO(p.read_text(encoding="utf-8"), newline="") as f:
        return [{**{k: (v or "") for k, v in r.items() if k}, **{k: (r.get(k) or "").strip() for k in COLUMNS}}
                for r in csv.DictReader(f)]


def save(paper: Path, rows: list[dict]) -> None:
    p = path(paper)
    p.parent.mkdir(parents=True, exist_ok=True)
    extra = [k for r in rows for k in r if k not in COLUMNS]
    fields = list(COLUMNS) + list(dict.fromkeys(extra))
    with p.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def strip(item: dict) -> dict:
    return {k: v for k, v in item.items() if not k.startswith("_")}


LABEL = {"supporting": "支持", "contrasting": "反驳", "mentioning": "提及"}
KIND = {"citing": "引用", "context": "引用句", "alphaxiv": "alphaXiv 评论", "openreview": "OpenReview 评论"}
NEED = {"erratum": "核查是否需要勘误", "reply": "需要回应", "": ""}


def table(rows: list[dict]) -> list[str]:
    lines = ["| 需要 | 类型 | 立场 | 来源 | 原文 | 日期 |", "| --- | --- | --- | --- | --- | --- |"]
    for r in rows[:SHOWN]:
        src = f"[{cell(r['title'], 60) or r['id']}]({r['url']})" if r["url"] else cell(r["title"] or r["id"], 60)
        lines.append(f"| {NEED.get(r['needs'], '')} | {KIND.get(r['kind'], r['kind'])} | {LABEL.get(r['stance'], r['stance'])} "
                     f"| {src} | {cell(r['quote'], 220)} | {r['date']} |")
    if len(rows) > SHOWN:
        lines += ["", f"另有 {len(rows) - SHOWN} 条没列出，全部见 after_release.json 和 workflow/after_release.csv。"]
    return lines + [""]


def render(r: dict) -> str:
    c = r["counts"]
    lines = ["# 发表之后", "", f"论文：{r['title'] or r['openalex'] or r['forum'] or '—'}",
             f"累计 {r['total']} 条：支持 {c['supporting']}、反驳 {c['contrasting']}、提及 {c['mentioning']}。", ""]
    lines += ["## 本次新增", ""] + (table(r["new"]) if r["new"] else ["没有新内容。", ""])
    lines += ["## 仍待处理", ""] + (table(r["open"]) if r["open"] else ["没有待处理的。", ""])
    lines += ["处理完一条，在 workflow/after_release.csv 的 handled 列写上做了什么（回复链接、勘误或不需处理的理由）。", "",
              "立场按规则判断：含 however、in contrast、fail to、could not reproduce 等为反驳；含 consistent with、confirm、"
              "support the findings 等为支持；其余为提及。评论或反驳句里含 error in、bug、typo、incorrect 等的需要核查勘误。", ""]
    for n in r["notes"]:
        lines.append(f"- {n}")
    if r["unavailable_sources"]:
        lines.append(f"- 没能查询：{'、'.join(r['unavailable_sources'])}")
    return "\n".join(lines) + "\n"


__all__ = ["COLUMNS", "after_release", "forum_id", "identify", "needs", "stance"]
