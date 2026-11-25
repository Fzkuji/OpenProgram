"""How much a topic is written about, year by year, against all of OpenAlex, with a
label: 升温, 拥挤, 冷门 or 平稳. Plain code, no model calls.

Learned from 知网 AI 研学's topic trend analysis: publication counts per year for a
topic, normalized so a growing database does not read as a growing topic, and the
topics that co-occur.

Label rules, on the last three complete years (the current year is partial and only
drawn): n is the topic's works in the last complete year, g the yearly growth of its
share of all works over the three years, (share_last / share_first) ** (1/2) - 1.
  冷门  n < COLD
  升温  g >= RISING
  拥挤  n >= CROWDED
  平稳  otherwise
The rules apply in this order, so a large topic that still grows fast reads 升温.
"""

import json
import re
from datetime import date
from pathlib import Path

from auto_research import brief, stages
from auto_research.tasks.citations import sources
from auto_research.tasks.survey import search
from auto_research.trail import Trail

from .common import cell, paper_folder, run_folder

COLD = 50            # works in the last complete year below which a topic is cold
RISING = 0.25        # yearly growth of the share from which a topic is rising
CROWDED = 1000       # works in the last complete year from which a topic is crowded
TOPICS = 8           # co-occurring topics kept per query
MAX_QUERIES = 6
LABELS = ("升温", "拥挤", "冷门", "平稳")


def topic_trends(paper_dir: str, queries: str = "", since: str = "2016", out_dir: str = "") -> str:
    """Count the works on each query per year in OpenAlex and label the trend.

    paper_dir is the project's repository. queries are separated by ; (empty: the
    queries of workflow/watch.md, else the idea titles of the newest idea_generation
    run, else the field survey directions, else the ## headings of
    workflow/literature.md, else the project's claim; six at most). since is the first
    year counted. Per query: works per year (title and abstract search), the share of
    all OpenAlex works of that year, growth over the last three complete years, the
    topics that co-occur most in those years, and a label by the rules of this module.
    Writes trends.md (an SVG line chart per query) and trends.json to
    out_dir/<timestamp>/ (default <paper_dir>/drafts/topic_trends).
    """
    paper = paper_folder(paper_dir)
    if not str(since).strip().isdigit() or not 1900 <= int(since) <= date.today().year - 3:
        raise ValueError(f"since must be a year from 1900 to {date.today().year - 3}")
    start = int(since)
    asked = [q.strip() for q in queries.split(";") if q.strip()]
    chosen, origin = (asked, "given") if asked else default_queries(paper)
    if not chosen:
        raise ValueError("no query: pass queries, or write workflow/watch.md queries, ideas, literature or the claim")
    chosen = chosen[:MAX_QUERIES]
    trail = Trail("topic_trends", {"paper_dir": str(paper), "queries": chosen, "since": start}, {})
    sources.UNAVAILABLE.clear()
    today = date.today()
    last = today.year - 1
    totals = all_works(start)
    results = []
    for q in chosen:
        topic = as_topic(q) if len(q.split()) > PHRASE_WORDS else None
        where = f"primary_topic.id:{topic['id']}" if topic else f"title_and_abstract.search:{q.replace(',', ' ')}"
        counts = yearly(where, start)
        counts = {y: c for y, c in counts.items() if y <= today.year}
        co = co_topics(where, last - 2, last)
        results.append(dict(trend(q, counts, totals, last, co), topic=topic))
    trail.step("count", queries=chosen, origin=origin, labels={r["query"]: r["label"] for r in results})
    out = run_folder(paper, out_dir, "topic_trends")
    record = {"since": start, "last_complete_year": last, "origin": origin, "rules":
              {"cold": COLD, "rising": RISING, "crowded": CROWDED}, "queries": results,
              "unavailable_sources": sorted(sources.UNAVAILABLE)}
    (out / "trends.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "trends.md").write_text(render(record), encoding="utf-8")
    run = trail.save(out, report=out / "trends.md", json=out / "trends.json")
    return json.dumps({"status": "PASS_WITH_NOTES" if record["unavailable_sources"] else "PASS",
                       "report": str(out / "trends.md"), "json": str(out / "trends.json"), "run": str(run),
                       "labels": {r["query"]: r["label"] for r in results},
                       "unavailable_sources": record["unavailable_sources"]}, ensure_ascii=False)


def default_queries(paper: Path) -> tuple[list[str], str]:
    folder = stages.folder(paper)
    watch = folder / "watch.md"
    if watch.is_file():
        m = re.search(r"^Queries:[ \t]*(.*)$", watch.read_text(encoding="utf-8"), re.M)
        found = [q.strip() for q in (m.group(1) if m else "").split(";") if q.strip()]
        if found:
            return found, "watch.md"
    ideas = sorted(folder.glob("ideas.json")) + sorted((paper / "drafts" / "idea_generation").glob("*/ideas.json"))
    if ideas:
        try:
            data = json.loads(ideas[-1].read_text(encoding="utf-8"))
            found = [i["title"] for i in data.get("ideas", []) if i.get("title")]
            if found:
                return found, "ideas"
        except (ValueError, KeyError, TypeError):
            pass
    surveys = folder / "surveys"
    if surveys.is_dir():
        found = []
        for d in sorted(p for p in surveys.iterdir() if p.is_dir()):
            runs = sorted(d.glob("*/survey.json"))
            if not runs:
                continue
            try:
                found.append(json.loads(runs[-1].read_text(encoding="utf-8")).get("direction") or d.name)
            except ValueError:
                found.append(d.name)
        if found:
            return found, "surveys"
    literature = folder / "literature.md"
    if literature.is_file():
        found = [h.strip() for h in re.findall(r"^#{2,3}\s+(.+)$", literature.read_text(encoding="utf-8"), re.M)]
        if found:
            return found, "literature.md"
    claim = brief.read(paper)["claim"]
    return ([claim] if claim else []), "claim"


def all_works(since: int) -> dict[int, int]:
    d = sources.fetch("https://api.openalex.org/works",
                      {"filter": f"from_publication_date:{since}-01-01", "group_by": "publication_year",
                       **sources._env("OPENALEX_API_KEY", "api_key")}, label="OpenAlex")
    return {int(g["key"]): g["count"] for g in (d or {}).get("group_by") or [] if str(g.get("key")).isdigit()}


PHRASE_WORDS = 5     # a longer query (a claim, an idea title) is counted by its OpenAlex topic
TOPIC_SAMPLE = 25    # the most relevant works whose most common primary topic stands for it


def as_topic(text: str) -> dict | None:
    """The OpenAlex topic a sentence is about: the most common primary topic of the works
    OpenAlex ranks most relevant to it. A sentence searched word for word matches
    almost nothing, so its trend is the trend of its topic."""
    d = sources.fetch("https://api.openalex.org/works",
                      {"search": " ".join(text.split())[:400], "per-page": TOPIC_SAMPLE, "select": "id,primary_topic",
                       **sources._env("OPENALEX_API_KEY", "api_key")}, label="OpenAlex")
    seen = {}
    for w in (d or {}).get("results") or []:
        t = w.get("primary_topic") or {}
        if t.get("id"):
            key = t["id"].rsplit("/", 1)[-1]
            seen.setdefault(key, {"id": key, "name": t.get("display_name") or key, "works": 0})["works"] += 1
    return max(seen.values(), key=lambda t: t["works"]) if seen else None


def yearly(where: str, since: int) -> dict[int, int]:
    d = sources.fetch("https://api.openalex.org/works",
                      {"filter": f"{where},from_publication_date:{since}-01-01", "group_by": "publication_year",
                       **sources._env("OPENALEX_API_KEY", "api_key")}, label="OpenAlex")
    return {int(g["key"]): g["count"] for g in (d or {}).get("group_by") or [] if str(g.get("key")).isdigit()}


def co_topics(where: str, first: int, last: int) -> list[dict]:
    d = sources.fetch("https://api.openalex.org/works",
                      {"filter": f"{where},"
                                 f"from_publication_date:{first}-01-01,to_publication_date:{last}-12-31",
                       "group_by": "topics.id", **sources._env("OPENALEX_API_KEY", "api_key")}, label="OpenAlex")
    return [{"topic": g.get("key_display_name") or g.get("key"), "id": str(g.get("key", "")).rsplit("/", 1)[-1],
             "count": g.get("count", 0)} for g in ((d or {}).get("group_by") or [])[:TOPICS]]


def trend(query: str, counts: dict, totals: dict, last: int, co: list) -> dict:
    years = sorted(y for y in set(counts) | set(totals) if y <= last + 1)
    series = [{"year": y, "works": counts.get(y, 0), "all": totals.get(y, 0),
               "per_10k": round(counts.get(y, 0) / totals[y] * 10_000, 3) if totals.get(y) else None,
               "partial": y > last} for y in years]
    n = counts.get(last, 0)
    first = share(counts, totals, last - 2)
    end = share(counts, totals, last)
    growth = round((end / first) ** 0.5 - 1, 3) if first and end is not None else None
    return {"query": query, "series": series, "last_year_works": n, "works_3y": sum(counts.get(y, 0) for y in
            range(last - 2, last + 1)), "growth": growth, "label": label(n, growth), "co_topics": co}


def share(counts: dict, totals: dict, year: int) -> float | None:
    return counts.get(year, 0) / totals[year] if totals.get(year) else None


def label(works_last_year: int, growth: float | None) -> str:
    """The rules in the module docstring, in order."""
    if works_last_year < COLD:
        return "冷门"
    if growth is not None and growth >= RISING:
        return "升温"
    if works_last_year >= CROWDED:
        return "拥挤"
    return "平稳"


def chart(series: list[dict], width: int = 560, height: int = 200) -> str:
    """An inline SVG line of works per 10,000 OpenAlex works by year; the partial
    current year is a hollow point on a dashed segment."""
    pts = [s for s in series if s["per_10k"] is not None]
    if len(pts) < 2:
        return ""
    left, right, top, bottom = 48, 12, 16, 28
    top_value = max(s["per_10k"] for s in pts) or 1
    y0, y1 = pts[0]["year"], pts[-1]["year"]
    x = lambda y: left + (y - y0) / max(1, y1 - y0) * (width - left - right)
    yv = lambda v: top + (1 - v / top_value) * (height - top - bottom)
    full = [s for s in pts if not s["partial"]]
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
           f'font-family="sans-serif" font-size="11">',
           f'<line x1="{left}" y1="{height - bottom}" x2="{width - right}" y2="{height - bottom}" stroke="#999"/>',
           f'<line x1="{left}" y1="{top}" x2="{left}" y2="{height - bottom}" stroke="#999"/>',
           f'<text x="{left - 4}" y="{top + 4}" text-anchor="end" fill="#666">{top_value:.3g}</text>',
           f'<text x="{left - 4}" y="{height - bottom}" text-anchor="end" fill="#666">0</text>']
    if len(full) >= 2:
        line = " ".join(f"{x(s['year']):.1f},{yv(s['per_10k']):.1f}" for s in full)
        out.append(f'<polyline points="{line}" fill="none" stroke="#4f46e5" stroke-width="2"/>')
    partial = [s for s in pts if s["partial"]]
    if full and partial:
        a, b = full[-1], partial[0]
        out.append(f'<line x1="{x(a["year"]):.1f}" y1="{yv(a["per_10k"]):.1f}" x2="{x(b["year"]):.1f}" '
                   f'y2="{yv(b["per_10k"]):.1f}" stroke="#4f46e5" stroke-width="2" stroke-dasharray="4 3"/>')
    for s in pts:
        fill = "white" if s["partial"] else "#4f46e5"
        out.append(f'<circle cx="{x(s["year"]):.1f}" cy="{yv(s["per_10k"]):.1f}" r="3" fill="{fill}" stroke="#4f46e5">'
                   f'<title>{s["year"]}: {s["works"]} 篇，每万篇 {s["per_10k"]}</title></circle>')
    step = max(1, (y1 - y0 + 1) // 8)
    for y in range(y0, y1 + 1, step):
        out.append(f'<text x="{x(y):.1f}" y="{height - bottom + 16}" text-anchor="middle" fill="#666">{y}</text>')
    out.append("</svg>")
    return "".join(out)


def render(r: dict) -> str:
    lines = ["# 主题趋势", "", f"从 {r['since']} 年起，OpenAlex 标题和摘要检索（长句按主题统计）；纵轴是每万篇作品中的篇数，"
             f"空心点是还没过完的 {r['last_complete_year'] + 1} 年。", "",
             "| 主题 | 标签 | 去年篇数 | 近三年篇数 | 占比年增长 |", "| --- | --- | --- | --- | --- |"]
    for q in r["queries"]:
        g = f"{q['growth']:+.0%}" if q["growth"] is not None else "—"
        name = cell(q["query"]) + (f"（按主题：{q['topic']['name']}）" if q.get("topic") else "")
        lines.append(f"| {name} | {q['label']} | {q['last_year_works']} | {q['works_3y']} | {g} |")
    lines.append("")
    for q in r["queries"]:
        lines += [f"## {q['query']}", "", f"标签：**{q['label']}**", ""]
        if q.get("topic"):
            lines += [f"这句话太长，逐词检索几乎查不到，按它的 OpenAlex 主题统计：**{q['topic']['name']}**"
                      f"（`{q['topic']['id']}`，最相关的 {TOPIC_SAMPLE} 篇里有 {q['topic']['works']} 篇属于它）。", ""]
        svg = chart(q["series"])
        lines += [svg, ""] if svg else ["数据太少，画不出折线。", ""]
        if q["co_topics"]:
            lines.append("一起出现最多的主题：" + "、".join(f"{t['topic']}（{t['count']}）" for t in q["co_topics"]))
            lines.append("")
    rules = r["rules"]
    lines += ["## 标签规则", "",
              f"按顺序判断，n 是去年的篇数，g 是近三个完整年份里占比的年增长：n < {rules['cold']} 为冷门；"
              f"g ≥ {rules['rising']:.0%} 为升温；n ≥ {rules['crowded']} 为拥挤；其余为平稳。", ""]
    if r["unavailable_sources"]:
        lines += [f"没能查询：{'、'.join(r['unavailable_sources'])}。", ""]
    return "\n".join(lines)


__all__ = ["LABELS", "chart", "default_queries", "label", "topic_trends", "trend"]
