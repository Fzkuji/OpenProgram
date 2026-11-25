"""Benchmark contamination: are the paper's test items already in a pretraining corpus?
Sampled items are cut into n-gram windows and counted in infini-gram indexes
(https://infini-gram.readthedocs.io/en/latest/api.html). Plain code, no model calls."""

import csv
import io
import json
import random
import time
import urllib.error
import urllib.request
from pathlib import Path

from auto_research.trail import Trail

from ..submission import common as paper_files
from . import common

API = "https://api.infini-gram.io/"
PAUSE = 0.5            # seconds between API calls
WINDOWS = 3            # n-gram windows per item: start, middle, end
FAIL_RATE = 0.10
TIMEOUT = 30
RETRIES = 2
GIVE_UP = 5            # consecutive failed calls after which the check stops


def contamination_check(paper_dir: str, data: str, field: str = "", n: str = "13", sample: str = "200",
                        index: str = "v4_rpj_llama_s4", out_dir: str = "") -> str:
    """Check whether a benchmark's test items appear in pretraining corpora.

    data is the test file (.jsonl, .json, .csv or .tsv; relative to paper_dir or absolute);
    field names the text column, otherwise the column with the longest text is used.
    sample items are drawn (fixed seed), each cut into up to three n-gram windows of
    whitespace tokens, and every window is counted in each infini-gram index (comma-
    separated names) through https://api.infini-gram.io/. An item is contaminated in an
    index when any window occurs there. Calls are paced and stop at sample*3 per index.

    FAIL when more than 10% of the checked items are contaminated in any index;
    PASS_WITH_NOTES when any are, or when some windows could not be checked. Writes
    report.md, contamination.json (per-item counts), known_contamination.md (a datasheet
    limitations paragraph) and the trail to out_dir/<timestamp>/ (default
    <paper_dir>/drafts/contamination_check).
    """
    paper = paper_files.repo(paper_dir)
    path = Path(data).expanduser()
    path = path if path.is_absolute() else paper / path
    if not path.is_file():
        raise ValueError(f"data file {data} does not exist")
    n_tokens, k = int(n), int(sample)
    if n_tokens < 1 or k < 1:
        raise ValueError("n and sample must be positive")
    indexes = [i.strip() for i in index.split(",") if i.strip()]
    if not indexes:
        raise ValueError("name at least one infini-gram index")
    out = paper_files.run_folder(paper, out_dir, "contamination_check")
    trail = Trail("contamination_check", {"paper_dir": str(paper), "data": data, "field": field, "n": n,
                                          "sample": sample, "index": index, "out_dir": out_dir}, {})
    rows = load_rows(path)
    column = field or guess_field(rows)
    if not column:
        raise ValueError(f"{path.name}: no text column; name one with field")
    texts = [(i, text_of(r, column)) for i, r in enumerate(rows)]
    texts = [(i, t) for i, t in texts if t.strip()]
    if not texts:
        raise ValueError(f"{path.name}: column {column} is empty")
    picked = sorted(random.Random(0).sample(texts, min(k, len(texts))))
    trail.step("sample", file=str(path), rows=len(rows), field=column, sampled=len(picked))

    items, stopped = [], {}
    for name in indexes:
        budget, failures = k * WINDOWS, 0
        for pos, (row, text) in enumerate(picked):
            if len(items) <= pos:
                items.append({"row": row, "text": common.cut(text, 160), "windows": windows(text, n_tokens),
                              "counts": {}})
            item = items[pos]
            counts = []
            for w in item["windows"]:
                if budget <= 0 or name in stopped:
                    break
                budget -= 1
                count, error = query(name, w)
                failures = failures + 1 if error else 0
                counts.append({"window": w, "count": count, "error": error})
                if failures >= GIVE_UP:
                    stopped[name] = error
                if count:
                    break
            item["counts"][name] = counts
        trail.step("query", index=name, calls=k * WINDOWS - budget, stopped=stopped.get(name, ""))

    summary = {}
    for name in indexes:
        checked = [it for it in items if any(c["count"] is not None for c in it["counts"].get(name, []))]
        hits = [it for it in checked if any((c["count"] or 0) > 0 for c in it["counts"].get(name, []))]
        lo, hi = common.wilson(len(hits), len(checked))
        rate = len(hits) / len(checked) if checked else 0.0
        summary[name] = {"checked": len(checked), "contaminated": len(hits), "rate": round(rate, 4),
                         "ci95": [round(lo, 4), round(hi, 4)], "unchecked": len(items) - len(checked),
                         "stopped": stopped.get(name, "")}
    for it in items:
        it["contaminated"] = {name: any((c["count"] or 0) > 0 for c in it["counts"].get(name, [])) for name in indexes}

    findings = []
    for name, s in summary.items():
        pct = f"{s['rate']:.1%}（95% CI {s['ci95'][0]:.1%}–{s['ci95'][1]:.1%}）"
        if s["contaminated"]:
            level = "error" if s["rate"] > FAIL_RATE else "note"
            findings.append(common.finding(
                "contamination", level, name,
                f"抽查的 {s['checked']} 条测试样本里有 {s['contaminated']} 条在 {name} 中出现（{n_tokens}-gram 命中），污染率 {pct}。",
                "在数据说明的局限性里写明污染（草稿见 known_contamination.md）；超过 10% 时考虑去掉命中样本或换成新样本再报告结果。"))
        if s["unchecked"] or s["stopped"]:
            findings.append(common.finding(
                "unchecked", "note", name,
                f"{s['unchecked']} 条样本没能查询（{s['stopped'] or '调用上限或接口错误'}）。",
                "稍后重跑；infini-gram 接口不保证可用。"))
    status = common.status(findings)
    paragraph = known_paragraph(path.name, column, n_tokens, summary)
    (out / "known_contamination.md").write_text("## Known contamination\n\n" + paragraph + "\n", encoding="utf-8")
    result = {"status": status, "data": str(path), "field": column, "n": n_tokens, "sampled": len(picked),
              "indexes": indexes, "summary": summary, "items": items, "findings": findings}
    common.write_json(out / "contamination.json", result)
    table = ["## 各索引", "", "| 索引 | 已查 | 命中 | 污染率 | 95% CI |", "|---|---|---|---|---|"]
    table += [f"| {name} | {s['checked']} | {s['contaminated']} | {s['rate']:.1%} | {s['ci95'][0]:.1%}–{s['ci95'][1]:.1%} |"
              for name, s in summary.items()]
    lead = (f"数据 `{path.name}`，文本列 `{column}`，共 {len(rows)} 条，抽查 {len(picked)} 条；每条取至多 {WINDOWS} 个 "
            f"{n_tokens}-gram 窗口（按空格分词），任一窗口在语料中出现即算污染。")
    (out / "report.md").write_text(common.report("测试集污染检查", lead, findings, table + [""], status), encoding="utf-8")
    run = trail.save(out, report=out / "report.md", findings=out / "contamination.json",
                     paragraph=out / "known_contamination.md")
    return json.dumps({"status": status, "report": str(out / "report.md"), "run": str(run),
                       "findings": str(out / "contamination.json"), "paragraph": str(out / "known_contamination.md"),
                       "field": column, "sampled": len(picked), "indexes": indexes, "summary": summary},
                      ensure_ascii=False)


# Data ---------------------------------------------------------------------------------

def load_rows(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    elif suffix == ".json":
        data = json.loads(text)
        if isinstance(data, dict):
            lists = [v for v in data.values() if isinstance(v, list)]
            data = max(lists, key=len) if lists else [data]
        rows = data
    elif suffix in (".csv", ".tsv"):
        rows = list(csv.DictReader(io.StringIO(text), delimiter="\t" if suffix == ".tsv" else ","))
    else:
        raise ValueError(f"{path.name}: use .jsonl, .json, .csv or .tsv")
    return [r if isinstance(r, dict) else {"text": r} for r in rows]


def text_of(row: dict, column: str) -> str:
    value = row
    for part in column.split("."):
        value = value.get(part, "") if isinstance(value, dict) else ""
    if isinstance(value, list):
        return " ".join(str(v) for v in value if isinstance(v, (str, int, float)))
    return value if isinstance(value, str) else ""


def guess_field(rows: list[dict]) -> str:
    """The column whose string values are longest on average."""
    totals: dict[str, list[int]] = {}
    for r in rows[:500]:
        for key, value in r.items():
            if isinstance(value, str):
                totals.setdefault(key, []).append(len(value))
    return max(totals, key=lambda key: sum(totals[key]) / len(totals[key]), default="")


def windows(text: str, n: int) -> list[str]:
    """Up to WINDOWS distinct n-token windows: start, middle, end. A text shorter than n
    is one window of its own."""
    toks = text.split()
    if len(toks) <= n:
        return [" ".join(toks)] if toks else []
    starts = sorted({0, (len(toks) - n) // 2, len(toks) - n})[:WINDOWS]
    return [" ".join(toks[s:s + n]) for s in starts]


# infini-gram --------------------------------------------------------------------------

def post(payload: dict) -> dict:
    """POST payload to the infini-gram API and return its JSON."""
    req = urllib.request.Request(API, data=json.dumps(payload).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "auto-research/1.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310  fixed https endpoint
        return json.loads(resp.read().decode("utf-8"))


def query(index: str, text: str) -> tuple[int | None, str]:
    """(count, "") or (None, error) for one window, retried and paced."""
    error = ""
    for attempt in range(RETRIES + 1):
        time.sleep(PAUSE * (1 + 2 * attempt))
        try:
            data = post({"index": index, "query_type": "count", "query": text})
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
            error = f"{type(e).__name__}: {e}"
            continue
        if "error" in data:
            error = str(data["error"])
            if "index" in error.lower():
                break  # an unknown index will not answer on retry
            continue
        return int(data.get("count") or 0), ""
    return None, error


def known_paragraph(name: str, column: str, n: int, summary: dict) -> str:
    parts = []
    for index, s in summary.items():
        parts.append(f"{s['contaminated']} of {s['checked']} sampled test items "
                     f"({s['rate']:.1%}, 95% Wilson CI {s['ci95'][0]:.1%}-{s['ci95'][1]:.1%}) have at least one "
                     f"exact {n}-token span in the infini-gram index {index}")
    hit = any(s["contaminated"] for s in summary.values())
    tail = ("Models pretrained on these corpora may have seen the affected items, so their scores on them can "
            "overstate generalization; we report results on the uncontaminated subset alongside the full set. "
            "[TODO: confirm, or say how affected items were handled.]") if hit else \
        "No sampled item matched, which bounds but does not rule out contamination in corpora that were not checked."
    return (f"We checked the test split ({name}, field \"{column}\") for overlap with public pretraining corpora "
            f"using infini-gram exact n-gram counts over whitespace-token windows: " + "; ".join(parts) + ". " + tail)


__all__ = ["contamination_check"]
