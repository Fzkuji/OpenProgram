"""Render a survey as one offline HTML page, a Chinese report, and LaTeX related work.
Plain code, no model calls. Every text from a role or a source is escaped; citations
[p12] become links whose card shows the paper's record.
"""

import html
import json
import math
import re
from pathlib import Path

from .replies import CITE, cites

TEMPLATE = Path(__file__).parent / "templates" / "page.html"
COLORS = 8

WORDS = {
    "zh": {"toc": "目录", "overview": "领域全貌", "lines": "研究线", "trends": "近期动态", "directions": "可跟的方向",
           "papers": "论文列表", "scope": "检索与阅读范围", "prereq": "读者需要的基础：", "core": "篇核心论文",
           "full": "篇读过正文", "since": "检索自", "coverage": "覆盖度",
           "coverage_about": "按检索源之间的重合估计找到了相关论文的多少；各源不独立，这是上限。", "map": "各研究线的论文分布，点的大小表示被引次数，点击打开论文。",
           "counts": "每年标题或摘要匹配该研究线首个检索词的论文数（OpenAlex）。当年数据不完整。",
           "rising": "增长明显的研究线", "new": "新出现的设定、基准与方法", "saturated": "进展放缓的问题",
           "problem": "问题", "active": "在做的工作", "crowding": "近期篇数", "cost": "入手成本", "first": "第一个实验",
           "all": "全部", "read_full": "读过正文", "read_abstract": "仅读摘要", "adjacent": "相邻", "survey": "综述",
           "queries": "检索词", "sources": "来源与条数", "unavailable": "未能访问的来源", "baseline": "对照的已有综述",
           "gaps": "据此补检的缺口", "candidates": "候选总数", "kinds": {"setting": "设定", "benchmark": "基准",
                                                                         "method": "方法", "finding": "发现"},
           "none": "无"},
    "en": {"toc": "Contents", "overview": "The field", "lines": "Research lines", "trends": "Recent work",
           "directions": "Open directions", "papers": "Papers", "scope": "Search and reading scope",
           "prereq": "Assumed background: ", "core": "core papers", "full": "read in full", "since": "searched from", "coverage": "coverage",
           "coverage_about": "How much of the relevant work the search found, estimated from the overlap of the sources; an upper bound.",
           "map": "Papers of each research line; dot size shows citations. Click to open a paper.",
           "counts": "Works per year whose title or abstract matches the line's first query (OpenAlex). The current year is partial.",
           "rising": "Growing lines", "new": "New settings, benchmarks and methods", "saturated": "Slowing problems",
           "problem": "Problem", "active": "Active work", "crowding": "Recent papers", "cost": "Entry cost",
           "first": "First experiment", "all": "All", "read_full": "body read", "read_abstract": "abstract only",
           "adjacent": "adjacent", "survey": "survey", "queries": "Queries", "sources": "Records per source",
           "unavailable": "Sources that failed", "baseline": "Existing surveys compared", "gaps": "Gaps searched again",
           "candidates": "Candidates", "kinds": {"setting": "setting", "benchmark": "benchmark", "method": "method",
                                                 "finding": "finding"}, "none": "none"},
}


def esc(text) -> str:
    return html.escape(str(text or ""), quote=True)


def prose(text: str, papers: dict) -> str:
    """Escaped text with every [p12, p7] turned into linked labels."""
    def link(m):
        ids = [p.strip() for p in re.split(r"[,，;；]", m.group(1))]
        parts = [f'<a class="cite" tabindex="0" data-p="{pid}">{esc(papers[pid]["label"])}</a>'
                 for pid in ids if pid in papers]
        return "（" + "；".join(parts) + "）" if parts else ""
    out, last = [], 0
    for m in CITE.finditer(text):
        out.append(esc(text[last:m.start()]))
        out.append(link(m))
        last = m.end()
    out.append(esc(text[last:]))
    return "".join(out)


def colors(lines: list[dict]) -> dict[str, str]:
    return {l["id"]: f"var(--c{i % COLORS + 1})" for i, l in enumerate(lines)}


def field_map(survey: dict, w: dict) -> str:
    """One row per line: its papers by date, dot size by log citations."""
    lines, papers = survey["lines"], [p for p in survey["papers"] if p["decision"] != "survey" and p["year"]]
    if not papers:
        return ""
    color = colors(lines)
    y0 = min(p["year"] for p in papers)
    y1 = max(p["year"] for p in papers) + 1
    left, width, row = 150, 560, 34
    height = row * len(lines) + 30
    x = lambda d: left + width * ((int(d[:4]) + (int(d[5:7] or 6) - 1) / 12) - y0) / max(1, y1 - y0)
    chapter_of = {c["line"]: i for i, c in enumerate(survey["chapters"], 1)}
    titles = {c["line"]: c["title"] for c in survey["chapters"]}
    out = [f'<svg viewBox="0 0 {left + width + 20} {height}" width="100%" role="img" aria-label="{esc(w["map"])}">']
    for year in range(y0, y1 + 1):
        xx = left + width * (year - y0) / max(1, y1 - y0)
        out.append(f'<line class="axis" x1="{xx:.1f}" x2="{xx:.1f}" y1="0" y2="{height - 22}"/>'
                   f'<text class="muted" x="{xx:.1f}" y="{height - 6}" text-anchor="middle">{year}</text>')
    for i, l in enumerate(lines):
        cy = row * i + row / 2 + 4
        name = esc(titles.get(l["id"], l["name"]))
        label = (f'<a href="#ch-{l["id"]}"><text x="0" y="{cy + 4:.1f}">{name}</text></a>' if l["id"] in chapter_of
                 else f'<text class="muted" x="0" y="{cy + 4:.1f}">{name}</text>')
        out.append(label)
        for p in (p for p in papers if p["line"] == l["id"]):
            r = 3 + 1.4 * math.log1p(max(p["citations"] or 0, 0)) ** 0.9
            fill = color[l["id"]] if p["decision"] == "core" else "var(--surface)"
            out.append(f'<circle class="dot" tabindex="0" data-p="{p["pid"]}" cx="{x(p["date"] or str(p["year"])):.1f}" '
                       f'cy="{cy:.1f}" r="{min(r, 11):.1f}" fill="{fill}" fill-opacity=".75" stroke="{color[l["id"]]}"/>')
    out.append("</svg>")
    names = {l["id"]: titles.get(l["id"], l["name"]) for l in lines}
    edges = "".join(f'<li>{esc(names[e["from"]])} → {esc(names[e["to"]])}：{esc(e["label"])}</li>'
                    for e in survey["overview"]["edges"] if e["from"] in names and e["to"] in names)
    return (f'<figure>{"".join(out)}<figcaption>{esc(w["map"])}</figcaption>'
            + (f'<ul class="edges">{edges}</ul>' if edges else "") + "</figure>")


def bars(counts: dict, color: str) -> str:
    """A small bar chart of works per year, one hue, with the value on hover."""
    if not counts:
        return '<p class="note">—</p>'
    ys = sorted(int(y) for y in counts)
    top = max(counts.values()) or 1
    w, h, gap = 220, 80, 2
    bw = (w - gap * (len(ys) - 1)) / len(ys)
    out = [f'<svg viewBox="0 0 {w} {h + 16}" width="100%">']
    for i, y in enumerate(ys):
        v = counts.get(y, counts.get(str(y), 0))
        bh = max(1, h * v / top)
        xx = i * (bw + gap)
        out.append(f'<rect x="{xx:.1f}" y="{h - bh:.1f}" width="{bw:.1f}" height="{bh:.1f}" rx="2" fill="{color}">'
                   f'<title>{y}: {v}</title></rect>')
    out.append(f'<text class="muted" x="0" y="{h + 13}">{ys[0]}</text>'
               f'<text class="muted" x="{w}" y="{h + 13}" text-anchor="end">{ys[-1]}</text></svg>')
    return "".join(out)


def chapter(i: int, ch: dict, papers: dict) -> str:
    out = [f'<h3 id="ch-{ch["line"]}"><span class="no">{i}</span>{esc(ch["title"])}</h3>']
    out += [f"<p>{prose(t, papers)}</p>" for t in ch["paragraphs"]]
    t = ch["table"]
    if t["rows"]:
        head = "".join(f"<th>{esc(c)}</th>" for c in [""] + t["columns"])
        rows = "".join(f'<tr><td><a class="cite" tabindex="0" data-p="{r["paper"]}">{esc(papers[r["paper"]]["label"])}</a></td>'
                       + "".join(f"<td>{prose(c, papers)}</td>" for c in r["cells"]) + "</tr>"
                       for r in t["rows"] if r["paper"] in papers)
        out.append(f'<div class="tablebox"><table><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table>'
                   + (f'<div class="caption">{esc(t["caption"])}</div>' if t["caption"] else "") + "</div>")
    return "\n".join(out)


def covered(survey: dict, w: dict) -> str:
    """The coverage estimate in the page's meta line, when there is one."""
    est = (survey.get("retrieval") or {}).get("coverage") or {}
    if est.get("coverage") is None:
        return ""
    return f'<span title="{esc(w["coverage_about"])}">{esc(w["coverage"])} ~{round(est["coverage"] * 100)}%</span>'


def page(survey: dict) -> str:
    lang = survey["language"]
    w = WORDS[lang]
    plan, lines = survey["plan"], survey["lines"]
    papers = {p["pid"]: p for p in survey["papers"]}
    notes = survey["notes"]
    color = colors(lines)
    names = {l["id"]: l["name"] for l in lines}
    core = sum(p["decision"] == "core" for p in survey["papers"])
    full = sum(p.get("read_level") == "full" for p in survey["papers"])
    toc = [f'<a class="part" href="#overview">{esc(w["overview"])}</a>']
    toc += [f'<a href="#ch-{c["line"]}">{esc(c["title"])}</a>' for c in survey["chapters"]]
    toc += [f'<a class="part" href="#trends">{esc(w["trends"])}</a>',
            f'<a class="part" href="#directions">{esc(w["directions"])}</a>',
            f'<a class="part" href="#papers">{esc(w["papers"])}</a>']

    body = [f"<header><h1>{esc(plan['title'])}</h1>",
            f'<p class="prereq">{esc(w["prereq"])}{esc(plan["prerequisites"])}</p>',
            f'<p class="meta"><span>{esc(survey["date"])}</span><span>{core} {esc(w["core"])}</span>'
            f'<span>{full} {esc(w["full"])}</span><span>{esc(w["since"])} {survey["since"]}</span>{covered(survey, w)}</p></header>']

    body.append(f'<h2 class="part" id="overview">{esc(w["overview"])}</h2>')
    body += [f"<p>{prose(t, papers)}</p>" for t in survey["overview"]["paragraphs"]]
    body.append(field_map(survey, w))
    body += [chapter(i, ch, papers) for i, ch in enumerate(survey["chapters"], 1)]

    tr = survey["trends"]
    body.append(f'<h2 class="part" id="trends">{esc(w["trends"])}</h2>')
    body.append(f"<p>{prose(tr['summary'], papers)}</p>")
    multiples = "".join(
        f'<div><h5><span class="swatch" style="background:{color[l["id"]]}"></span>{esc(l["name"])}'
        f'<span class="total">{sum(survey["counts"].get(l["id"], {}).values())}</span></h5>'
        f'{bars(survey["counts"].get(l["id"], {}), color[l["id"]])}</div>' for l in lines)
    body.append(f'<figure><div class="multiples">{multiples}</div><figcaption>{esc(w["counts"])}</figcaption></figure>')
    for key in ("rising", "new", "saturated"):
        if not tr[key]:
            continue
        items = []
        for it in tr[key]:
            head = esc(names.get(it.get("line"), it.get("name", "")))
            kind = f'<span class="kind">{esc(w["kinds"].get(it.get("kind"), ""))}</span>' if it.get("kind") else ""
            said = set(cites(it["text"]))
            refs = "；".join(f'<a class="cite" tabindex="0" data-p="{p}">{esc(papers[p]["label"])}</a>'
                            for p in it["papers"] if p in papers and p not in said)
            items.append(f"<li>{kind}<b>{head}</b>{prose(it['text'], papers)}{f'（{refs}）' if refs else ''}</li>")
        body.append(f'<h4>{esc(w[key])}</h4><ul class="items">{"".join(items)}</ul>')

    body.append(f'<h2 class="part" id="directions">{esc(w["directions"])}</h2>')
    if not survey["directions"]["cards"]:
        body.append(f'<p class="note">{esc(w["none"])}</p>')
    for c in survey["directions"]["cards"]:
        evidence = []
        for e in c["evidence"]:
            pid, k = e.split(".")
            lim = notes.get(pid, {}).get("limitations", [])
            text = lim[int(k) - 1]["text"] if int(k) <= len(lim) else ""
            evidence.append(f'<li>{esc(text)}（<a class="cite" tabindex="0" data-p="{pid}">'
                            f'{esc(papers[pid]["label"])}</a>）</li>' if pid in papers else "")
        active = "；".join(f'<a class="cite" tabindex="0" data-p="{p}">{esc(papers[p]["label"])}</a>'
                          for p in c["active"] if p in papers) or esc(w["none"])
        body.append(f'<section class="dir"><h4>{esc(c["title"])}</h4><p>{prose(c["problem"], papers)}</p>'
                    f'<ul class="edges">{"".join(evidence)}</ul><dl>'
                    f'<dt>{esc(w["active"])}</dt><dd>{active}（{esc(w["crowding"])} {c.get("crowding", 0)}）</dd>'
                    f'<dt>{esc(w["cost"])}</dt><dd>{esc(c["cost"])}</dd>'
                    f'<dt>{esc(w["first"])}</dt><dd>{esc(c["first_step"])}</dd></dl></section>')

    body.append(f'<h2 class="part" id="papers">{esc(w["papers"])}</h2>')
    buttons = [f'<button type="button" data-f="all" aria-pressed="true">{esc(w["all"])}</button>']
    buttons += [f'<button type="button" data-f="{l["id"]}" aria-pressed="false">{esc(l["name"])}</button>' for l in lines]
    buttons += [f'<button type="button" data-f="full" aria-pressed="false">{esc(w["read_full"])}</button>']
    items = []
    for p in survey["papers"]:
        read = p.get("read_level") or "none"
        tag = {"full": f'<span class="badge full">{esc(w["read_full"])}</span>',
               "abstract": f'<span class="badge">{esc(w["read_abstract"])}</span>'}.get(read, "")
        role = {"adjacent": w["adjacent"], "survey": w["survey"]}.get(p["decision"])
        items.append(f'<li data-line="{esc(p["line"])}" data-read="{read}"><span class="swatch" '
                     f'style="background:{color.get(p["line"], "var(--muted)")}"></span>'
                     f'<a class="cite" tabindex="0" data-p="{p["pid"]}">{esc(p["label"])}</a> {esc(p["title"])}'
                     f'{tag}{f"<span class=badge>{esc(role)}</span>" if role else ""}'
                     f' <span class="dim">{esc(p["venue"])}</span></li>')
    body.append(f'<div class="filters" data-for="plist">{"".join(buttons)}</div><ul class="plist" id="plist">{"".join(items)}</ul>')

    r, b = survey["retrieval"], survey["baseline"]
    sources = {}
    for per in r["receipts"].values():
        for k, v in per.items():
            sources[k] = sources.get(k, 0) + v
    scope = [f'<p><b>{esc(w["candidates"])}</b>：{r["candidates"]}　<b>{esc(w["sources"])}</b>：'
             + "，".join(f"{esc(k)} {v}" for k, v in sources.items()) + "</p>",
             f'<p><b>{esc(w["queries"])}</b>：' + "；".join(esc(q) for q in r["queries"]) + "</p>"]
    if r["unavailable"]:
        scope.append(f'<p><b>{esc(w["unavailable"])}</b>：{esc("、".join(r["unavailable"]))}</p>')
    if b["surveys"]:
        scope.append(f'<p><b>{esc(w["baseline"])}</b>：' + "；".join(esc(s["title"]) for s in b["surveys"]) + "</p>")
    if b["gaps"]:
        scope.append(f'<p><b>{esc(w["gaps"])}</b>：' + "；".join(esc(g["heading"]) for g in b["gaps"]) + "</p>")
    body.append(f'<details id="scope"><summary>{esc(w["scope"])}</summary>{"".join(scope)}</details>')

    cards = {pid: {"label": p["label"], "title": p["title"], "date": p["date"], "venue": p["venue"], "url": p["url"],
                   "read": p.get("read_level") or "abstract",
                   "summary": notes[pid]["problem"] if pid in notes else cut(p["abstract"], 220)}
             for pid, p in papers.items()}
    fill = {"lang": "zh-CN" if lang == "zh" else "en", "title": esc(plan["title"]), "toc_label": esc(w["toc"]),
            "toc": "".join(toc), "body": "\n".join(body), "read_full": w["read_full"], "read_abstract": w["read_abstract"],
            "papers_json": json.dumps(cards, ensure_ascii=False).replace("</", "<\\/")}
    text = TEMPLATE.read_text(encoding="utf-8")
    for key, value in fill.items():
        text = text.replace("{" + key + "}", value)
    return text


def cut(text: str, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "…"


def bib_key(p: dict) -> str:
    surname = re.sub(r"[^a-z]", "", p["label"].split()[0].lower()) or "anon"
    word = next((w for w in re.findall(r"[a-z]+", p["title"].lower()) if len(w) > 3), "work")
    return f"{surname}{p['year'] or ''}{word}"


def related_work(survey: dict) -> tuple[str, str]:
    """The related-work paragraphs with \\cite keys, and a .bib of every cited paper."""
    papers = {p["pid"]: p for p in survey["papers"]}
    keys, used = {}, set()
    for pid, p in papers.items():
        k, n = bib_key(p), 1
        while k in used:
            n += 1
            k = f"{bib_key(p)}{n}"
        keys[pid], used = k, used | {k}
    cited = []

    def cite(m):
        ids = [x.strip() for x in re.split(r"[,，;；]", m.group(1)) if x.strip() in papers]
        cited.extend(i for i in ids if i not in cited)
        return "~\\cite{" + ",".join(keys[i] for i in ids) + "}"

    names = {l["id"]: l["name"] for l in survey["lines"]}
    tex = ["\\section{Related Work}", ""]
    for para in survey["related_work"]["paragraphs"]:
        text = CITE.sub(cite, para["text"]).replace(" ~\\cite", "~\\cite")
        tex += [f"\\paragraph{{{latex(names.get(para['line'], ''))}}}", latex_keep_cites(text), ""]
    bib = []
    for pid in cited:
        p = papers[pid]
        fields = {"title": "{" + p["title"] + "}", "author": " and ".join(p["authors"]) or "Unknown",
                  "year": str(p["year"] or "")}
        if p["arxiv"]:
            fields.update(journal=f"arXiv preprint arXiv:{p['arxiv']}", eprint=p["arxiv"])
        elif p["venue"]:
            fields["journal"] = p["venue"]
        if p["doi"]:
            fields["doi"] = p["doi"]
        bib.append("@article{" + keys[pid] + ",\n" + ",\n".join(f"  {k} = {{{v}}}" for k, v in fields.items() if v)
                   + "\n}\n")
    return "\n".join(tex), "\n".join(bib)


def latex(text: str) -> str:
    return re.sub(r"([&%$#_{}])", r"\\\1", text or "")


def latex_keep_cites(text: str) -> str:
    parts = re.split(r"(~\\cite\{[^}]*\})", text)
    return "".join(p if p.startswith("~\\cite") else latex(p) for p in parts)


def coverage_line(est: dict | None) -> str:
    if not est:
        return "- 覆盖度：没有估计"
    if est["coverage"] is None:
        return f"- 覆盖度：各检索源没有共同找到的相关论文，无法估计（找到 {est['found']} 篇）"
    return (f"- 覆盖度：约 {round(est['coverage'] * 100)}%（找到 {est['found']} 篇，按检索源重合估计相关论文约 "
            f"{est['estimated']} 篇；各源不独立，这是上限）")


def prisma(f: dict | None) -> list[str]:
    """The screening flow as PRISMA 2020 reports it, for a systematic review's method section."""
    if not f:
        return []
    inc = f["included"]
    return ["## 筛选流程（PRISMA 2020）", "",
            "| 步骤 | 篇数 |", "|---|---|",
            f"| 首轮关键词检索返回（含重复） | {f['returned']} |",
            f"| 去重后（含补检和引文扩展） | {f['unique']} |",
            f"| 筛选过 | {f['screened']} |",
            f"| 排除 | {f['excluded']} |",
            f"| 没有筛选（超出预算） | {f['not_screened']} |",
            f"| 纳入：核心 / 相邻 / 综述 | {inc['core']} / {inc['adjacent']} / {inc['survey']} |",
            f"| 读过正文 | {f['read_full']} |", "",
            "每篇候选的筛选决定和理由在 candidates.json。", ""]


def report(survey: dict, status: str, notes: list[str], issues: list[dict]) -> str:
    papers = survey["papers"]
    count = lambda k: sum(p["decision"] == k for p in papers)
    lines = [f"# 领域综述：{survey['plan']['title']}", "",
             f"状态：**{status}**", "",
             f"- 方向：{survey['direction']}（用途 {survey['purpose']}）",
             f"- 候选 {survey['retrieval']['candidates']} 篇；核心 {count('core')}、相邻 {count('adjacent')}、"
             f"综述 {count('survey')}",
             coverage_line(survey["retrieval"].get("coverage")),
             f"- 读过正文 {sum(p.get('read_level') == 'full' for p in papers)} 篇，仅读摘要 "
             f"{sum(p.get('read_level') == 'abstract' for p in papers)} 篇",
             f"- 章节 {len(survey['chapters'])}，可跟方向 {len(survey['directions']['cards'])}", ""]
    if notes:
        lines += ["## 需要注意", ""] + [f"- {n}" for n in notes] + [""]
    if issues:
        lines += ["## 改写一次后仍未解决的核查问题", "", "| 章 | 段 | 问题 | 原句 | 说明 |", "|---|---|---|---|---|"]
        lines += [f"| {i['chapter']} | {i['paragraph']} | {i['problem']} | {i['quote']} | {i['note']} |" for i in issues]
        lines.append("")
    lines += prisma(survey["retrieval"].get("flow"))
    lines += ["## 研究线", ""] + [f"- {l['id']} {l['name']}：{sum(p['line'] == l['id'] for p in papers)} 篇"
                                for l in survey["lines"]] + [""]
    return "\n".join(lines)
