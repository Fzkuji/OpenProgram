"""Rank new papers by what this author found relevant before: a per-project logistic
regression on SPECTER2 embeddings, learned from the author's decisions in watch.csv.
Plain code, no model calls.

Learned from Scholar Inbox: a personal classifier on paper embeddings trained from
the reader's thumbs up and down, scoring each day's new papers.

Labels, per watch.csv row (the author's columns; the watch task's verdict is the
model's guess and is not a label):
  1. a `relevance`, `label`, `relevant` or `rating` column, when the author added one:
     1, yes, y, +, relevant, 相关, up, true, keep, a positive number -> relevant;
     0, no, n, -, irrelevant, 无关, 不相关, down, false, a number <= 0 -> not relevant.
  2. else the `handled` note: one saying irrelevant, not relevant, unrelated, skip,
     ignore, 无关, 不相关, 忽略, 跳过 or 不用 -> not relevant; any other note (cited it,
     narrowed the claim, noted how it differs) -> relevant.
  3. else unlabeled: ranked.
"""

import csv
import io
import json
import math
import re
import time
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from pathlib import Path

from auto_research import stages
from auto_research.tasks.citations import sources
from auto_research.tasks.survey import search
from auto_research.tasks.watch.workflow import COLUMNS as WATCH_COLUMNS
from auto_research.trail import Trail

from .common import cell, paper_folder, post, rules, run_folder

LEAST = 5                # labels of each class needed
LABEL_COLUMNS = ("relevance", "label", "relevant", "rating")
YES = {"1", "yes", "y", "+", "+1", "relevant", "相关", "up", "true", "keep", "👍"}
NO = {"0", "no", "n", "-", "-1", "irrelevant", "无关", "不相关", "down", "false", "👎"}
NOT_RELEVANT = re.compile(r"irrelevant|not relevant|unrelated|\bskip|\bignore|无关|不相关|忽略|跳过|不用", re.I)
S2_BATCH = "https://api.semanticscholar.org/graph/v1/paper/batch"
LISTING_DAYS = 2
LISTING_MAX = 300
STOP = set("a an the of for and or in on to with by from via is are we our this that as at be its into using based "
           "towards toward new can".split())
ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV_NS = "{http://arxiv.org/schemas/atom}"


def personal_rank(paper_dir: str, out_dir: str = "") -> str:
    """Rank unhandled watch.csv rows and today's arXiv listing by learned relevance.

    paper_dir is the project's repository. The labels are the author's decisions in
    workflow/watch.csv (rules in this module); at least five relevant and five not
    relevant are needed, else the result says how many more. Each paper's SPECTER2
    embedding comes from Semantic Scholar's batch API; a logistic regression (plain
    Python, L2-regularised, classes balanced) learns from the labeled rows and scores
    the unlabeled rows and the arXiv papers of the project's categories from the last
    two days (watch.md `Categories:`, else rules.json `arxiv_categories`, else the
    primary categories of the relevant arXiv rows). Papers without an embedding are
    scored by the same regression on TF-IDF of title and abstract. Writes ranked.md and
    ranked.json to out_dir/<timestamp>/ (default <paper_dir>/drafts/personal_rank) and
    a `score` column into watch.csv, keeping every column the author has.
    """
    paper = paper_folder(paper_dir)
    trail = Trail("personal_rank", {"paper_dir": str(paper)}, {})
    sources.UNAVAILABLE.clear()
    header, rows = read_watch(paper)
    labeled = [(r, label(r)) for r in rows]
    pos = sum(1 for _, y in labeled if y == 1)
    neg = sum(1 for _, y in labeled if y == 0)
    out = run_folder(paper, out_dir, "personal_rank")
    if pos < LEAST or neg < LEAST:
        more = {"relevant": max(0, LEAST - pos), "not_relevant": max(0, LEAST - neg)}
        text = (f"# 个性化排序\n\n标注不够：现在 {pos} 个相关、{neg} 个不相关，还需要 {more['relevant']} 个相关、"
                f"{more['not_relevant']} 个不相关。\n\n在 workflow/watch.csv 的 handled 列写处理结果（写“无关”算不相关，"
                f"其他说明算相关），或加一列 relevance 填 1 / 0。\n")
        (out / "ranked.md").write_text(text, encoding="utf-8")
        run = trail.save(out, report=out / "ranked.md")
        return json.dumps({"status": "PASS_WITH_NOTES", "report": str(out / "ranked.md"), "run": str(run),
                           "labels": {"relevant": pos, "not_relevant": neg}, "need_more": more}, ensure_ascii=False)

    papers = [{"key": r.get("id") or r.get("title"), "source": "watch", "title": r.get("title", ""),
               "url": r.get("url", ""), "date": r.get("date", ""), "abstract": "", "s2": s2_id(r.get("id", ""), r.get("url", "")),
               "label": y, "row": r} for r, y in labeled]
    cats, cat_origin = categories(paper, [p for p in papers if p["label"] == 1])
    seen = {p["s2"] for p in papers if p["s2"]}
    listing = [p for p in arxiv_listing(cats) if p["s2"] not in seen] if cats else []
    papers += listing
    embed(papers)
    trail.step("collect", labeled={"relevant": pos, "not_relevant": neg}, watch=len(rows), categories=cats,
               listing=len(listing), embedded=sum(1 for p in papers if p.get("vector")))

    train = [p for p in papers if p["label"] is not None]
    todo = [p for p in papers if p["label"] is None]
    with_vec = [p for p in train if p.get("vector")]
    use_specter = (sum(p["label"] == 1 for p in with_vec) >= LEAST and sum(p["label"] == 0 for p in with_vec) >= LEAST)
    if use_specter:
        model = fit([p["vector"] for p in with_vec], [p["label"] for p in with_vec])
        for p in todo:
            if p.get("vector"):
                p["score"], p["method"] = predict(model, p["vector"]), "specter2"
    rest = [p for p in todo if "score" not in p]
    if rest:
        docs = [text_of(p) for p in train + rest]
        vecs = tfidf(docs)
        model = fit(vecs[:len(train)], [p["label"] for p in train])
        for p, v in zip(rest, vecs[len(train):]):
            p["score"], p["method"] = predict(model, v), "tfidf"
    todo.sort(key=lambda p: -p["score"])
    for p in todo:
        if p["source"] == "watch":
            p["row"]["score"] = f"{p['score']:.3f}"
    for p in train:
        p["row"]["score"] = ""
    write_watch(paper, header, rows)
    methods = {m: sum(p.get("method") == m for p in todo) for m in ("specter2", "tfidf")}
    trail.step("rank", ranked=len(todo), methods=methods)
    record = {"labels": {"relevant": pos, "not_relevant": neg}, "categories": cats, "categories_from": cat_origin,
              "methods": methods, "ranked": [{k: p.get(k) for k in ("source", "key", "title", "url", "date", "score", "method")}
                                             for p in todo],
              "unavailable_sources": sorted(sources.UNAVAILABLE)}
    (out / "ranked.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "ranked.md").write_text(render(record), encoding="utf-8")
    run = trail.save(out, report=out / "ranked.md", json=out / "ranked.json")
    return json.dumps({"status": "PASS_WITH_NOTES" if record["unavailable_sources"] else "PASS",
                       "report": str(out / "ranked.md"), "json": str(out / "ranked.json"), "run": str(run),
                       "labels": record["labels"], "ranked": len(todo), "methods": methods,
                       "top": [p["title"] for p in todo[:5]], "unavailable_sources": record["unavailable_sources"]},
                      ensure_ascii=False)


# Labels and watch.csv

def label(row: dict) -> int | None:
    """1 relevant, 0 not relevant, None unlabeled: the rules in the module docstring."""
    for col in LABEL_COLUMNS:
        v = (row.get(col) or "").strip().lower()
        if not v:
            continue
        if v in YES:
            return 1
        if v in NO:
            return 0
        try:
            return 1 if float(v) > 0 else 0
        except ValueError:
            pass
    note = (row.get("handled") or "").strip()
    if not note:
        return None
    return 0 if NOT_RELEVANT.search(note) else 1


def watch_path(paper: Path) -> Path:
    return stages.folder(paper) / "watch.csv"


def read_watch(paper: Path) -> tuple[list[str], list[dict]]:
    p = watch_path(paper)
    if not p.is_file():
        raise ValueError("no workflow/watch.csv: run related_work_watch first")
    with io.StringIO(p.read_text(encoding="utf-8"), newline="") as f:
        reader = csv.DictReader(f)
        rows = [{k: v or "" for k, v in r.items() if k} for r in reader]
        header = list(reader.fieldnames or WATCH_COLUMNS)
    return header, rows


def write_watch(paper: Path, header: list[str], rows: list[dict]) -> None:
    fields = header + ([] if "score" in header else ["score"])
    with watch_path(paper).open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


ARXIV_BARE = re.compile(r"^(\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})(v\d+)?$")
ARXIV_URL = re.compile(r"arxiv\.org/(?:abs|pdf)/([^?#\s]+?)(?:v\d+)?(?:\.pdf)?(?:[?#]|$)")
DOI_URL = re.compile(r"doi\.org/(10\.[^?#\s]+)")


def s2_id(pid: str, url: str = "") -> str:
    """The Semantic Scholar id of a watch.csv row: arXiv:/doi: ids as the watch task writes
    them, a bare arXiv id, DOI or S2 paper id an author typed, else the arXiv or doi.org url."""
    pid = (pid or "").strip()
    low = pid.lower()
    if low.startswith("arxiv:"):
        return "ARXIV:" + re.sub(r"v\d+$", "", pid.split(":", 1)[1])
    if low.startswith("doi:"):
        return "DOI:" + pid.split(":", 1)[1]
    m = ARXIV_BARE.match(pid)
    if m:
        return "ARXIV:" + m.group(1)
    if pid.startswith("10.") and "/" in pid:
        return "DOI:" + pid
    if re.fullmatch(r"[0-9a-f]{40}", pid):
        return pid
    m = ARXIV_URL.search(url or "")
    if m:
        return "ARXIV:" + m.group(1)
    m = DOI_URL.search(url or "")
    return "DOI:" + m.group(1) if m else ""


# arXiv

def categories(paper: Path, relevant: list[dict]) -> tuple[list[str], str]:
    watch = stages.folder(paper) / "watch.md"
    if watch.is_file():
        m = re.search(r"^Categories:[ \t]*(.*)$", watch.read_text(encoding="utf-8"), re.M)
        found = [c for c in re.split(r"[;,\s]+", m.group(1) if m else "") if c]
        if found:
            return found, "watch.md"
    found = rules(paper).get("arxiv_categories") or []
    if isinstance(found, str):
        found = re.split(r"[;,\s]+", found)
    if [c for c in found if c]:
        return [c for c in found if c], "rules.json"
    ids = [p["s2"].split(":", 1)[1] for p in relevant if p["s2"].startswith("ARXIV:")]
    if not ids:
        return [], ""
    counts: dict[str, int] = {}
    for e in arxiv_entries({"id_list": ",".join(ids[:50]), "max_results": 50}):
        if e["category"]:
            counts[e["category"]] = counts.get(e["category"], 0) + 1
    return sorted(counts, key=lambda c: -counts[c])[:3], "relevant rows"


def arxiv_listing(cats: list[str], today: date | None = None) -> list[dict]:
    """The newest papers of the categories, submitted in the last LISTING_DAYS days."""
    since = ((today or date.today()) - timedelta(days=LISTING_DAYS)).isoformat()
    query = " OR ".join(f"cat:{c}" for c in cats)
    found = arxiv_entries({"search_query": query, "sortBy": "submittedDate", "sortOrder": "descending",
                           "max_results": LISTING_MAX})
    return [{"key": "arXiv:" + e["arxiv"], "source": "arxiv", "title": e["title"], "url": f"https://arxiv.org/abs/{e['arxiv']}",
             "date": e["date"], "abstract": e["abstract"], "s2": "ARXIV:" + e["arxiv"], "label": None, "row": None}
            for e in found if e["date"] >= since]


def arxiv_entries(params: dict) -> list[dict]:
    wait = search._last_arxiv[0] + search.ARXIV_GAP - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    body = sources.fetch("https://export.arxiv.org/api/query", params, as_json=False, label="arXiv")
    search._last_arxiv[0] = time.monotonic()
    try:
        entries = list(ET.fromstring(body).iter(ATOM + "entry")) if body else []
    except ET.ParseError:
        return []
    out = []
    for e in entries:
        m = search.ARXIV_ID.search(e.findtext(ATOM + "id") or "")
        title = " ".join((e.findtext(ATOM + "title") or "").split())
        if not m or not title or title == "Error":
            continue
        prim = e.find(ARXIV_NS + "primary_category")
        out.append({"arxiv": re.sub(r"v\d+$", "", m.group(1)), "title": title,
                    "abstract": " ".join((e.findtext(ATOM + "summary") or "").split()),
                    "date": (e.findtext(ATOM + "published") or "")[:10],
                    "category": prim.get("term") if prim is not None else ""})
    return out


# Embeddings

def embed(papers: list[dict]) -> None:
    """SPECTER2 vectors (and abstracts) from Semantic Scholar, 500 ids per call."""
    ids = [p["s2"] for p in papers if p["s2"]]
    found: dict[str, dict] = {}
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        d = post(S2_BATCH, {"fields": "title,abstract,embedding.specter_v2"}, {"ids": chunk},
                 label="Semantic Scholar", headers=sources._env("S2_API_KEY", "x-api-key"))
        for pid, rec in zip(chunk, d if isinstance(d, list) else []):
            if rec:
                found[pid] = rec
    raw = {}
    for i, p in enumerate(papers):
        rec = found.get(p["s2"]) or {}
        vec = (rec.get("embedding") or {}).get("vector")
        if vec:
            raw[i] = [float(x) for x in vec]
        if rec.get("abstract") and not p["abstract"]:
            p["abstract"] = rec["abstract"]
    for i, v in centered(raw).items():
        papers[i]["vector"] = unit(v)


def centered(vectors: dict) -> dict:
    """The vectors less their mean: SPECTER2 vectors of one field all point much the same
    way, and without centering that shared direction drowns what tells them apart."""
    if len(vectors) < 2:
        return vectors
    rows = list(vectors.values())
    mean = [sum(col) / len(rows) for col in zip(*rows)]
    return {k: [a - m for a, m in zip(v, mean)] for k, v in vectors.items()}


# Learning, plain Python

def unit(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def sigmoid(z: float) -> float:
    return 1 / (1 + math.exp(-z)) if z >= 0 else math.exp(z) / (1 + math.exp(z))


def fit(X: list[list[float]], y: list[int], l2: float = 0.01, rate: float = 1.0, epochs: int = 300) -> tuple:
    """Logistic regression by full-batch gradient descent with an L2 penalty; each class
    weighs half of the loss, so an unbalanced label set does not tilt the scores."""
    n, d = len(X), len(X[0]) if X else 0
    npos = sum(y) or 1
    nneg = (n - sum(y)) or 1
    weight = [n / (2 * npos) if t else n / (2 * nneg) for t in y]
    w, b = [0.0] * d, 0.0
    for _ in range(epochs):
        gw, gb = [0.0] * d, 0.0
        for xi, yi, si in zip(X, y, weight):
            e = (sigmoid(sum(a * c for a, c in zip(w, xi)) + b) - yi) * si
            if e:
                for j, v in enumerate(xi):
                    if v:
                        gw[j] += e * v
                gb += e
        w = [wj - rate * (g / n + l2 * wj) for wj, g in zip(w, gw)]
        b -= rate * gb / n
    return w, b


def predict(model: tuple, x: list[float]) -> float:
    w, b = model
    return round(sigmoid(sum(a * c for a, c in zip(w, x)) + b), 4)


def tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z][a-z0-9\-]+", (text or "").lower()) if t not in STOP and len(t) > 2]


def text_of(p: dict) -> str:
    return f"{p['title']} {p['title']} {p.get('abstract') or ''}"


def tfidf(docs: list[str], vocab_size: int = 3000) -> list[list[float]]:
    toks = [tokens(d) for d in docs]
    df: dict[str, int] = {}
    for t in toks:
        for w in set(t):
            df[w] = df.get(w, 0) + 1
    vocab = sorted(df, key=lambda w: (-df[w], w))[:vocab_size]
    index = {w: i for i, w in enumerate(vocab)}
    n = len(docs)
    out = []
    for t in toks:
        v = [0.0] * len(vocab)
        for w in t:
            if w in index:
                v[index[w]] += 1
        for w, i in index.items():
            if v[i]:
                v[i] *= math.log((1 + n) / (1 + df[w])) + 1
        out.append(unit(v))
    return out


def render(r: dict) -> str:
    lab = r["labels"]
    lines = ["# 个性化排序", "", f"从 watch.csv 学到 {lab['relevant']} 个相关、{lab['not_relevant']} 个不相关；"
             f"SPECTER2 打分 {r['methods']['specter2']} 篇，TF-IDF 打分 {r['methods']['tfidf']} 篇。",
             f"arXiv 分类：{'、'.join(r['categories']) or '—'}" + (f"（来自 {r['categories_from']}）" if r["categories_from"] else ""), ""]
    if not r["ranked"]:
        lines += ["没有待排序的论文。", ""]
    else:
        lines += ["| 分数 | 来源 | 论文 | 日期 | 方法 |", "| --- | --- | --- | --- | --- |"]
        for p in r["ranked"]:
            title = f"[{cell(p['title'], 100)}]({p['url']})" if p["url"] else cell(p["title"], 100)
            lines.append(f"| {p['score']:.2f} | {'待处理' if p['source'] == 'watch' else 'arXiv 新论文'} | {title} "
                         f"| {p['date']} | {p['method']} |")
        lines.append("")
    lines += ["分数是按你过去的判断估计的相关概率；watch.csv 待处理的行已写入 score 列。", ""]
    if r["unavailable_sources"]:
        lines += [f"没能查询：{'、'.join(r['unavailable_sources'])}。", ""]
    return "\n".join(lines)


__all__ = ["fit", "label", "personal_rank", "predict", "tfidf"]
