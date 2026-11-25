"""Forecast the paper's citations at one, two and three years from comparable published
works and their OpenAlex citation trajectories. Plain code, no model calls.

Learned from HLM-Cite and "newborn article" impact prediction (citation forecasting
from a paper's text and its nearest published neighbours) and from Scite's and
OpenAlex's per-year citation trajectories.

Steps: comparables (OpenAlex works published 3 to 6 years ago: a search by the title, a
search by the abstract's first sentence, and a random sample of the paper's primary
topic, which counters the citation bias of relevance ranking), similarity (plain code:
0.6 TF-IDF cosine of title and abstract scaled to the best candidate, 0.25 same primary
topic, 0.15 same venue), forecast (weighted 25/50/75% quantiles of the k nearest
comparables' cumulative citations at each horizon), factors (correlation only: code
link in the abstract, an arXiv version, number of authors), actual (when the project
names its published version, its citations so far against the band).

Horizon k of a work published in year Y and month M ends with calendar year Y+k-1 when
M is January to June and Y+k otherwise; every citation counted by OpenAlex up to that
year is included. A horizon whose last year is not over yet is left out for that work.
"""

import json
import math
import re
from datetime import date

from auto_research.tasks.citations import sources
from auto_research.trail import Trail

from ..scouting.common import cell, paper_folder, paper_text, project_field, rules, run_folder
from ..scouting.published import identify
from ..scouting.venues import JOURNALS, known, venue_name
from ..venue import facts
from .common import cosine, tfidf

HORIZONS = (1, 2, 3)
OLDEST, NEWEST = 6, 3        # comparables published this many years before today
SEARCH = 100                 # works read per OpenAlex search
SAMPLE = 100                 # works sampled from the primary topic
SEED = 42                    # OpenAlex sample seed, so reruns read the same sample
K = 30                       # nearest comparables in the forecast
LEAST = 10                   # fewer comparables than this at a horizon is a note
W_TEXT, W_TOPIC, W_VENUE = 0.6, 0.25, 0.15
GROUP = 5                    # a factor group smaller than this is not compared
TYPES = "article|preprint|conference-paper"
FIELDS = ("id,doi,title,publication_year,publication_date,primary_topic,counts_by_year,cited_by_count,"
          "authorships,locations,primary_location,abstract_inverted_index,type")
CODE = re.compile(r"github\.com|gitlab\.com|bitbucket\.org|huggingface\.co/|\bcode (?:is|are|will be|has been) "
                  r"(?:publicly |freely |openly )?(?:available|released)|\b(?:our|the) (?:code|implementation) "
                  r"(?:is|are) (?:publicly )?available|\bopen[- ]source(?:d)? (?:our |the )?(?:code|implementation)",
                  re.I)
OA = "https://api.openalex.org/works"


def impact_forecast(paper_dir: str, venue: str = "", out_dir: str = "") -> str:
    """Forecast the paper's citations at 1, 2 and 3 years from comparable published works.

    paper_dir is the project's folder; the title and abstract come from its main .tex
    (the project's claim stands in for a missing abstract). venue names the target venue
    (empty: the `Name:` or `Venue:` line of workflow/venue.md, else the venue family it
    names); a known conference or journal limits the comparables to that venue type
    when ten or more remain. Comparables are OpenAlex works published 3 to 6 years ago
    found by the title, by the abstract's first sentence and in a random sample of the
    paper's primary topic. Each gets a similarity (TF-IDF cosine of title and abstract,
    same primary topic, same venue) and the K nearest give weighted quantiles (25/50/75%)
    of their cumulative citations at each horizon. Factors that go with more citations
    among all candidates (a code link in the abstract, an arXiv version, the number of
    authors) are shown as correlations. When rules.json `published` or project.md
    `Published:` names the published version, its citations so far are compared with the
    forecast. Writes report.md (with an SVG chart of the median and band) and
    forecast.json to out_dir/<timestamp>/ (default <paper_dir>/drafts/impact_forecast).
    """
    paper = paper_folder(paper_dir)
    title, abstract = paper_text(paper)
    if not (title or abstract).strip():
        raise ValueError("the paper has no title or abstract (main .tex) and no claim in workflow/project.md")
    today = date.today()
    since, until = today.year - OLDEST, today.year - NEWEST
    target = venue_of(paper, venue)
    trail = Trail("impact_forecast", {"paper_dir": str(paper), "venue": target["name"], "out_dir": out_dir}, {})
    sources.UNAVAILABLE.clear()
    notes: list[str] = []

    published = str(rules(paper).get("published") or "") or project_field(paper, "Published")
    own = None
    if published.strip():
        try:
            own = openalex_work(identify(published))
        except ValueError as e:
            notes.append(f"发表版本 {published} 读不出：{e}")
        else:
            if own is None:
                notes.append(f"OpenAlex 没有找到发表版本 {published}")
            elif not same_paper(own.get("title"), title):
                notes.append(f"发表版本 {published} 的标题（{cell(own.get('title'), 80)}）和论文标题不一致："
                             "没有用它的主题挑可比论文，下面的实际引用请核对是不是这篇")

    first = re.split(r"(?<=[.!?])\s+", " ".join(abstract.split()), maxsplit=1)[0] if abstract else ""
    queries = [q for q in (title, " ".join(first.split()[:30])) if q.strip()]
    pool: dict[str, dict] = {}
    for q in queries:
        for w in search_works(q, since, until):
            pool.setdefault(w["id"], w)
    trail.step("search", queries=queries, found=len(pool), years=[since, until])

    skip = {(own or {}).get("id", ""), norm_title(title)}
    pool = {i: w for i, w in pool.items() if i not in skip and norm_title(w.get("title")) not in skip}
    paper_text_ = f"{title}. {abstract}"
    matched = own if own and same_paper(own.get("title"), title) else None
    topic = topic_of(matched, list(pool.values()), paper_text_)
    if topic["id"]:
        for w in topic_sample(topic["id"], since, until):
            if w["id"] not in skip and norm_title(w.get("title")) not in skip:
                pool.setdefault(w["id"], w)
    trail.step("topic", topic=topic, pool=len(pool))

    candidates = score(list(pool.values()), paper_text_, topic["id"], target)
    kept, restricted = by_type(candidates, target["type"])
    if target["type"] and not restricted:
        notes.append(f"同类型（{target['type']}）的可比论文不足 {LEAST} 篇，没有按会议/期刊类型筛选")
    near = sorted(kept, key=lambda c: -c["similarity"])[:K]
    forecast = {k: band([(c["citations"][k], c["similarity"]) for c in near if c["citations"].get(k) is not None])
                for k in HORIZONS}
    for k, b in forecast.items():
        if b["n"] < LEAST:
            notes.append(f"第 {k} 年只有 {b['n']} 篇可比论文有完整数据")
    trail.step("forecast", comparables=len(near), forecast=forecast)

    factor_rows = factors(candidates)
    mine = own_factors(paper, matched)
    actual = actual_of(own, forecast, today) if own else None
    trail.step("factors", factors=factor_rows, actual=actual)

    out = run_folder(paper, out_dir, "impact_forecast")
    record = {"title": title, "venue": target, "years": [since, until], "topic": topic, "queries": queries,
              "candidates": len(candidates), "restricted_to_type": restricted, "k": K,
              "forecast": {str(k): v for k, v in forecast.items()}, "comparables": [public(c) for c in near],
              "factors": factor_rows, "paper_factors": mine, "published": published, "actual": actual,
              "notes": notes, "unavailable_sources": sorted(sources.UNAVAILABLE)}
    (out / "forecast.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    status = "PASS" if not notes and not record["unavailable_sources"] and near else "PASS_WITH_NOTES"
    record["status"] = status
    (out / "report.md").write_text(render(record), encoding="utf-8")
    run = trail.save(out, report=out / "report.md", json=out / "forecast.json")
    return json.dumps({"status": status, "report": str(out / "report.md"), "json": str(out / "forecast.json"),
                       "run": str(run), "comparables": len(near),
                       "forecast": {str(k): {q: v[q] for q in ("q25", "q50", "q75", "n")} for k, v in forecast.items()},
                       "actual": actual, "unavailable_sources": record["unavailable_sources"]}, ensure_ascii=False)


# OpenAlex

def _key() -> dict:
    return sources._env("OPENALEX_API_KEY", "api_key")


def search_works(query: str, since: int, until: int) -> list[dict]:
    d = sources.fetch(OA, {"search": query.replace(",", " "),
                           "filter": f"publication_year:{since}-{until},type:{TYPES}",
                           "per-page": SEARCH, "select": FIELDS, **_key()}, label="OpenAlex")
    return [w for w in (d or {}).get("results") or [] if w.get("id")]


def topic_sample(topic: str, since: int, until: int) -> list[dict]:
    d = sources.fetch(OA, {"filter": f"primary_topic.id:{topic.rsplit('/', 1)[-1]},"
                                     f"publication_year:{since}-{until},type:{TYPES}",
                           "sample": SAMPLE, "seed": SEED, "per-page": SAMPLE, "select": FIELDS, **_key()},
                      label="OpenAlex")
    return [w for w in (d or {}).get("results") or [] if w.get("id")]


def openalex_work(ids: dict) -> dict | None:
    path = ids.get("openalex") or ("doi:" + ids["doi"] if ids.get("doi") else "")
    if not path:
        return None
    d = sources.fetch(f"{OA}/{path}", {"select": FIELDS, **_key()}, label="OpenAlex")
    return d if isinstance(d, dict) and d.get("id") else None


# Plain code below.

def venue_of(paper, venue: str) -> dict:
    """The target venue: its name, its known key (tasks/scouting/venues.py) and its type."""
    name = (venue or facts.field_value(paper, "Name") or facts.field_value(paper, "Venue")).strip()
    if not name:
        family = facts.from_venue(paper)
        name = family.key if family else ""
    row = known(name) if name else None
    if row:
        kind = "journal" if row[0] in JOURNALS else "conference"
    elif re.search(r"journal|transactions", name, re.I):
        kind = "journal"
    elif re.search(r"conference|proceedings|symposium|workshop", name, re.I):
        kind = "conference"
    else:
        kind = ""
    return {"name": name, "key": row[0] if row else "", "type": kind}


def abstract_of(work: dict) -> str:
    index = work.get("abstract_inverted_index") or {}
    spots = sorted((p, w) for w, ps in index.items() for p in ps)
    return " ".join(w for _, w in spots)


def norm_title(title) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(title or "").lower()).strip()


def same_paper(a, b) -> bool:
    """Two titles of one paper: equal once normalized, or TF-IDF cosine of 0.6 or more."""
    a, b = norm_title(a), norm_title(b)
    if not a or not b:
        return True             # nothing to compare: trust the project's own link
    return a == b or cosine(*tfidf([a, b])) >= 0.6


def topic_id(work: dict | None) -> str:
    return ((work or {}).get("primary_topic") or {}).get("id") or ""


def topic_of(own: dict | None, works: list[dict], text: str) -> dict:
    """The published version's primary topic, else the topic the ten candidates most
    similar to the paper share most, weighted by similarity."""
    if topic_id(own):
        t = own["primary_topic"]
        return {"id": t["id"], "name": t.get("display_name", ""), "from": "published"}
    if not works:
        return {"id": "", "name": "", "from": ""}
    vecs = tfidf([text] + [f"{w.get('title') or ''}. {abstract_of(w)}" for w in works])
    sims = sorted(((cosine(vecs[0], v), w) for v, w in zip(vecs[1:], works)), key=lambda s: -s[0])[:10]
    votes: dict[str, float] = {}
    names = {}
    for s, w in sims:
        t = topic_id(w)
        if t:
            votes[t] = votes.get(t, 0) + s
            names[t] = w["primary_topic"].get("display_name", "")
    if not votes:
        return {"id": "", "name": "", "from": ""}
    best = max(votes, key=votes.get)
    return {"id": best, "name": names[best], "from": "similar works"}


def venue_type(work: dict) -> str:
    for loc in [work.get("primary_location") or {}, *(work.get("locations") or [])]:
        src = loc.get("source") or {}
        if src.get("type") in ("conference", "journal"):
            return src["type"]
    return "repository"


def has_arxiv(work: dict) -> bool:
    for loc in [work.get("primary_location") or {}, *(work.get("locations") or [])]:
        src = (loc.get("source") or {}).get("display_name") or ""
        if "arxiv.org" in (loc.get("landing_page_url") or "") or src.lower().startswith("arxiv"):
            return True
    return (work.get("doi") or "").lower().startswith("https://doi.org/10.48550/arxiv.")


def end_year(work: dict, k: int) -> int | None:
    """The last calendar year of horizon k (module docstring)."""
    year = work.get("publication_year")
    if not year:
        return None
    day = str(work.get("publication_date") or "")
    month = int(day[5:7]) if re.fullmatch(r"\d{4}-\d{2}.*", day) else 1
    return int(year) + k - (1 if month <= 6 else 0)


def cites_until(work: dict, end: int) -> int:
    return sum(int(c.get("cited_by_count") or 0) for c in work.get("counts_by_year") or []
               if int(c.get("year") or 0) <= end)


def cumulative(work: dict, k: int, today: date) -> int | None:
    """Citations up to the end of horizon k; None when that year is not over yet."""
    end = end_year(work, k)
    return None if end is None or end >= today.year else cites_until(work, end)


def score(works: list[dict], text: str, topic: str, target: dict, today: date | None = None) -> list[dict]:
    """Each candidate with its similarity parts and its citations at each horizon."""
    today = today or date.today()
    if not works:
        return []
    vecs = tfidf([text] + [f"{w.get('title') or ''}. {abstract_of(w)}" for w in works])
    sims = [cosine(vecs[0], v) for v in vecs[1:]]
    best = max(sims) or 1.0
    out = []
    for w, s in zip(works, sims):
        name = venue_name(w)
        row = known(name) if name else None
        same_venue = bool(target["name"]) and (
            (bool(target["key"]) and bool(row) and row[0] == target["key"]) or
            (not target["key"] and norm_title(name) == norm_title(target["name"])))
        same_topic = bool(topic) and topic_id(w) == topic
        abstract = abstract_of(w)
        out.append({
            "id": w["id"], "title": w.get("title") or "", "year": w.get("publication_year"),
            "date": w.get("publication_date") or "", "venue": name, "type": venue_type(w),
            "cosine": round(s, 4), "same_topic": same_topic, "same_venue": same_venue,
            "similarity": round(W_TEXT * s / best + W_TOPIC * same_topic + W_VENUE * same_venue, 4),
            "citations": {k: cumulative(w, k, today) for k in HORIZONS},
            "cited_by_count": w.get("cited_by_count") or 0,
            "code": bool(CODE.search(abstract)), "arxiv": has_arxiv(w), "authors": len(w.get("authorships") or []),
            "has_abstract": bool(abstract)})
    return out


def by_type(candidates: list[dict], kind: str) -> tuple[list[dict], bool]:
    if not kind:
        return candidates, False
    same = [c for c in candidates if c["type"] == kind]
    return (same, True) if len(same) >= LEAST else (candidates, False)


def weighted_quantile(pairs: list[tuple[float, float]], q: float) -> float:
    """The q quantile of values with weights (midpoint rule, linear between points)."""
    pairs = sorted((v, w) for v, w in pairs if w > 0)
    if not pairs:
        return 0.0
    total = sum(w for _, w in pairs)
    spots, acc = [], 0.0
    for v, w in pairs:
        spots.append(((acc + w / 2) / total, v))
        acc += w
    if q <= spots[0][0]:
        return spots[0][1]
    for (p0, v0), (p1, v1) in zip(spots, spots[1:]):
        if q <= p1:
            return v0 + (v1 - v0) * (q - p0) / (p1 - p0) if p1 > p0 else v1
    return spots[-1][1]


def band(pairs: list[tuple[float, float]]) -> dict:
    vals = [(float(v), float(w)) for v, w in pairs]
    if not vals:
        return {"q25": None, "q50": None, "q75": None, "n": 0}
    return {"q25": round(weighted_quantile(vals, 0.25), 1), "q50": round(weighted_quantile(vals, 0.5), 1),
            "q75": round(weighted_quantile(vals, 0.75), 1), "n": len(vals)}


def ranks(xs: list[float]) -> list[float]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    out = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for t in range(i, j + 1):
            out[order[t]] = (i + j) / 2 + 1
        i = j + 1
    return out


def spearman(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3:
        return None
    rx, ry = ranks(xs), ranks(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    sx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    sy = math.sqrt(sum((b - my) ** 2 for b in ry))
    if not sx or not sy:
        return None
    return round(sum((a - mx) * (b - my) for a, b in zip(rx, ry)) / (sx * sy), 3)


def median(xs: list[float]) -> float | None:
    xs = sorted(xs)
    if not xs:
        return None
    m = len(xs) // 2
    return float(xs[m]) if len(xs) % 2 else (xs[m - 1] + xs[m]) / 2


def factors(candidates: list[dict]) -> list[dict]:
    """Among all candidates with two-year citations: the median for works with and
    without a code link or an arXiv version, and the Spearman correlation of the number
    of authors with citations. Correlation, not cause."""
    rows = [c for c in candidates if c["citations"].get(2) is not None]
    out = []
    for key, label in (("code", "摘要里有代码链接"), ("arxiv", "有 arXiv 版本")):
        pool = [c for c in rows if c["has_abstract"]] if key == "code" else rows
        yes = [c["citations"][2] for c in pool if c[key]]
        no = [c["citations"][2] for c in pool if not c[key]]
        item = {"factor": key, "label": label, "n_yes": len(yes), "n_no": len(no),
                "median_yes": median(yes), "median_no": median(no), "ratio": None}
        if len(yes) >= GROUP and len(no) >= GROUP and item["median_no"] is not None:
            item["ratio"] = round((item["median_yes"] + 1) / (item["median_no"] + 1), 2)
        out.append(item)
    xs = [c["authors"] for c in rows if c["authors"]]
    ys = [c["citations"][2] for c in rows if c["authors"]]
    out.append({"factor": "authors", "label": "作者人数", "n": len(xs),
                "spearman": spearman(xs, ys) if len(xs) >= 2 * GROUP else None})
    return out


def own_factors(paper, own: dict | None) -> dict:
    code = ((rules(paper).get("code") or {}).get("repo") or "") if isinstance(rules(paper).get("code"), dict) else ""
    return {"code": bool(code), "arxiv": has_arxiv(own) if own else None,
            "authors": len(own.get("authorships") or []) if own else None}


def actual_of(own: dict, forecast: dict, today: date) -> dict:
    """The published version's citations at each horizon that has started, against the band."""
    out = {"id": own.get("id", ""), "title": own.get("title") or "", "date": own.get("publication_date") or "",
           "cited_by_count": own.get("cited_by_count") or 0, "horizons": {}}
    for k in HORIZONS:
        end = end_year(own, k)
        if end is None or end > today.year:
            continue
        got = cites_until(own, end)
        b = forecast[k]
        where = ("" if b["q50"] is None else "below" if got < b["q25"] else "above" if got > b["q75"] else "within")
        out["horizons"][str(k)] = {"citations": got, "partial": end == today.year, "where": where}
    return out


def public(c: dict) -> dict:
    return {k: c[k] for k in ("id", "title", "year", "venue", "type", "cosine", "same_topic", "same_venue",
                              "similarity", "citations", "cited_by_count", "code", "arxiv", "authors")}


# Report

ACCENT = "#6366f1"
MARK = "#e11d48"


def chart(forecast: dict, actual: dict | None) -> str:
    """An SVG of the median trajectory (line) and the 25-75% band, with actual points."""
    pts = [(0, 0.0, 0.0, 0.0)] + [(k, forecast[k]["q25"], forecast[k]["q50"], forecast[k]["q75"])
                                  for k in HORIZONS if forecast[k]["q50"] is not None]
    if len(pts) < 2:
        return ""
    act = [(int(k), v["citations"]) for k, v in ((actual or {}).get("horizons") or {}).items()]
    top = max(p[3] for p in pts) or 1.0
    cap = top * 1.15
    if act and max(a for _, a in act) <= 4 * top:
        cap = max(cap, max(a for _, a in act) * 1.1)
    w, h, left, right, up, down = 560, 260, 56, 44, 20, 40
    x = lambda k: left + (w - left - right) * k / 3
    y = lambda v: up + (h - up - down) * (1 - min(v, cap) / cap)
    upper = " ".join(f"{x(k):.1f},{y(q75):.1f}" for k, _, _, q75 in pts)
    lower = " ".join(f"{x(k):.1f},{y(q25):.1f}" for k, q25, _, _ in reversed(pts))
    line = " ".join(f"{x(k):.1f},{y(q50):.1f}" for k, _, q50, _ in pts)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
             f'role="img" aria-label="可比论文的累计引用：中位数与 25%–75% 区间" '
             f'style="max-width:100%;font-family:sans-serif;font-size:12px">']
    for i in range(5):
        v = cap * i / 4
        parts.append(f'<line x1="{left}" x2="{w - right}" y1="{y(v):.1f}" y2="{y(v):.1f}" stroke="currentColor" '
                     f'stroke-opacity="0.12"/>')
        parts.append(f'<text x="{left - 8}" y="{y(v) + 4:.1f}" text-anchor="end" fill="currentColor" '
                     f'fill-opacity="0.65">{v:.0f}</text>')
    for k in range(4):
        parts.append(f'<text x="{x(k):.1f}" y="{h - down + 18}" text-anchor="middle" fill="currentColor" '
                     f'fill-opacity="0.65">{"发表" if k == 0 else f"第 {k} 年"}</text>')
    parts.append(f'<polygon points="{upper} {lower}" fill="{ACCENT}" fill-opacity="0.18"/>')
    parts.append(f'<polyline points="{line}" fill="none" stroke="{ACCENT}" stroke-width="2.5"/>')
    for k, _, q50, _ in pts[1:]:
        parts.append(f'<circle cx="{x(k):.1f}" cy="{y(q50):.1f}" r="3.5" fill="{ACCENT}"/>')
        parts.append(f'<text x="{x(k) + 6:.1f}" y="{y(q50) - 6:.1f}" fill="{ACCENT}">{q50:g}</text>')
    for k, v in act:
        over = v > cap
        cy = y(v)
        parts.append(f'<circle cx="{x(k):.1f}" cy="{cy:.1f}" r="4.5" fill="none" stroke="{MARK}" stroke-width="2"/>')
        parts.append(f'<text x="{x(k) - 6:.1f}" y="{cy + (14 if over else -8):.1f}" text-anchor="end" '
                     f'fill="{MARK}">{"↑ " if over else ""}实际 {v}</text>')
    parts.append("</svg>")
    return "".join(parts)


def num(v) -> str:
    return "—" if v is None else f"{v:g}"


WHERE = {"below": "低于 25%", "within": "在 25%–75% 之间", "above": "高于 75%", "": "—"}


def render(r: dict) -> str:
    f = {int(k): v for k, v in r["forecast"].items()}
    lines = ["# 引用预测", "",
             f"结论：{r['status']}　按 {len(r['comparables'])} 篇最相近的已发表论文（{r['years'][0]}–{r['years'][1]} 年发表）估计。", ""]
    lines += [f"> 注意：{n}" for n in r["notes"]]
    if r["unavailable_sources"]:
        lines.append(f"> 没有取到：{'、'.join(r['unavailable_sources'])}")
    if r["notes"] or r["unavailable_sources"]:
        lines.append("")
    lines += ["| 时间 | 25% | 中位数 | 75% | 有数据的可比论文 |", "| --- | --- | --- | --- | --- |"]
    lines += [f"| 第 {k} 年 | {num(f[k]['q25'])} | {num(f[k]['q50'])} | {num(f[k]['q75'])} | {f[k]['n']} |"
              for k in HORIZONS]
    lines.append("")
    svg = chart(f, r["actual"])
    if svg:
        lines += [svg, "", "蓝线是加权中位数，浅色带是 25%–75%" + ("，红圈是这篇论文的实际引用。" if r["actual"] and
                                                              r["actual"]["horizons"] else "。"), ""]
    a = r["actual"]
    if a:
        lines += ["## 已发表版本", "", f"[{cell(a['title'], 90)}]({a['id']})，{a['date']} 发表，OpenAlex 记录总引用 "
                  f"{a['cited_by_count']}。", ""]
        if a["horizons"]:
            lines += ["| 时间 | 实际引用 | 相对预测 |", "| --- | --- | --- |"]
            lines += [f"| 第 {k} 年{'（未满）' if v['partial'] else ''} | {v['citations']} | {WHERE[v['where']]} |"
                      for k, v in a["horizons"].items()]
            lines.append("")
        else:
            lines += ["发表还不满一年，没有可比的时间点。", ""]
    v = r["venue"]
    t = r["topic"]
    lines += ["## 怎么估的", "",
              f"- 主题：{t['name'] or '未定'}（{'来自发表版本' if t['from'] == 'published' else '来自最相近的论文'}）"
              if t["id"] else "- 主题：没有定下来，只用了文本检索结果",
              f"- 投稿目标：{v['name'] or '未指定'}" + (f"（{v['type']}，只比同类型）" if r["restricted_to_type"] else ""),
              f"- 候选 {r['candidates']} 篇：按标题、摘要首句检索，加上主题内随机抽样（抵消检索偏向高引论文）；"
              f"相似度 = {W_TEXT} × 文本 TF-IDF 余弦（相对最高者）+ {W_TOPIC} × 同主题 + {W_VENUE} × 同会议/期刊；"
              f"取最相近的 {r['k']} 篇，按相似度加权求分位数。",
              "- 第 k 年：上半年发表的算到第 Y+k−1 年末，下半年发表的算到第 Y+k 年末；还没过完的年份不算。", ""]
    lines += ["## 和引用多少相关的因素（只是相关，不是因果）", "",
              "在全部候选论文的第 2 年引用上比较：", ""]
    mine = r["paper_factors"]
    yesno = lambda b: "—" if b is None else ("有" if b else "没有")
    for item in r["factors"]:
        if item["factor"] == "authors":
            rho = item["spearman"]
            lines.append(f"- {item['label']}：{item['n']} 篇，Spearman ρ = {num(rho)}" +
                         ("（样本太少，不比较）" if rho is None else "") +
                         (f"；这篇 {mine['authors']} 人" if mine.get("authors") else ""))
        else:
            ratio = item["ratio"]
            body = (f"有的 {item['n_yes']} 篇中位数 {num(item['median_yes'])}，没有的 {item['n_no']} 篇中位数 "
                    f"{num(item['median_no'])}")
            body += f"，约 {ratio:g} 倍" if ratio is not None else "（一组不足 5 篇，不比较）"
            own = mine.get(item["factor"])
            lines.append(f"- {item['label']}：{body}；这篇{yesno(own)}")
    lines += ["", "代码链接只看摘要里有没有仓库地址或“代码已公开”一类说法；OpenAlex 不记录 arXiv 版本的上线时间，"
              "所以这里只看有没有 arXiv 版本，不看早晚。", "", "## 最相近的可比论文", "",
              "| 相似度 | 论文 | 年份 | 发表处 | 第 1/2/3 年引用 |", "| --- | --- | --- | --- | --- |"]
    for c in r["comparables"][:15]:
        cites = "/".join(num(c["citations"].get(k, c["citations"].get(str(k)))) for k in HORIZONS)
        lines.append(f"| {c['similarity']:.2f} | [{cell(c['title'], 70)}]({c['id']}) | {c['year']} | "
                     f"{cell(c['venue'] or '预印本', 40)} | {cites} |")
    lines += ["", "引用数来自 OpenAlex 的 counts_by_year，和 Google Scholar 的数字会有出入。"]
    return "\n".join(lines) + "\n"


__all__ = ["impact_forecast"]
