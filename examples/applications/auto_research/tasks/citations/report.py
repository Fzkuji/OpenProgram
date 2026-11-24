"""Save the check as report.md and report.json, and return the run summary."""

import json
from datetime import datetime
from pathlib import Path

LABEL = {"authentic": "真实", "unsure": "存疑", "inauthentic": "未找到", "invalid": "无法解析"}
ICON = {"authentic": "✅", "unsure": "⚠️", "inauthentic": "❌", "invalid": "➖"}
NOTICE = {
    "doi_incorrect": "DOI 不存在或指向另一篇文献",
    "year": "年份与记录相差两年及以上",
    "authors": "部分作者不在记录的作者列表里",
    "venue_unconfirmed": "只找到预印本，未找到所写会议或期刊的版本",
}
ORDER = ("inauthentic", "unsure", "invalid", "authentic")


def save(base: Path, refs: list[dict], results: list[dict], source: str, unavailable: list[str] = (),
         trail=None) -> str:
    out = base / datetime.now().strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    if trail is not None:
        trail.step("report", flagged=[ref["key"] for ref, res in zip(refs, results)
                                      if res["assessment"] != "authentic" or res["notices"]])
        trail.save(out, report=out / "report.md", json=out / "report.json")
    rows = [dict(key=ref["key"], title=ref["title"], authors=ref["authors"], year=ref["year"], venue=ref["venue"],
                 **{k: v for k, v in res.items() if k != "records"})
            for ref, res in zip(refs, results)]
    counts = {k: sum(r["assessment"] == k for r in rows) for k in ORDER}
    (out / "report.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    (out / "report.md").write_text(render(rows, counts, source, unavailable), encoding="utf-8")
    flagged = [r["key"] for r in rows if r["assessment"] != "authentic" or r["notices"]]
    return json.dumps({"status": "FAIL" if counts["inauthentic"] else "PASS_WITH_NOTES" if flagged or unavailable else "PASS",
                       "report": str(out / "report.md"), "counts": counts, "flagged": flagged,
                       "unavailable_sources": list(unavailable),
                       "run": str(out / "run.json") if trail is not None else None},
                      ensure_ascii=False)


def render(rows: list[dict], counts: dict, source: str, unavailable: list[str] = ()) -> str:
    lines = [f"# 引用预检查", "", f"来源：`{source}`　共 {len(rows)} 条　" +
             "　".join(f"{ICON[k]} {LABEL[k]} {counts[k]}" for k in ORDER), "",
             "只查了免费数据源（OpenAlex、Semantic Scholar、Crossref、arXiv、OpenReview、Open Library）。"
             "没找到不等于是假的，需要人工再确认。", ""]
    if unavailable:
        lines += [f"> **注意：{'、'.join(unavailable)} 这次有请求失败或被限流**，"
                  "“未找到”的条目可能只是没查到。设置免费的 OPENALEX_API_KEY / S2_API_KEY 后重跑。", ""]
    flagged = [r for r in rows if r["assessment"] != "authentic" or r["notices"]]
    if flagged:
        lines += ["## 需要看的条目", ""]
        for r in sorted(flagged, key=lambda r: ORDER.index(r["assessment"])):
            lines += entry(r)
    lines += ["## 全部条目", "", "| 结果 | key | 置信度 | 匹配记录 | 来源 | 提醒 |", "|---|---|---|---|---|---|"]
    for r in rows:
        m = r.get("match") or {}
        found = f"{m.get('title', '')[:60]} ({m.get('year', '')})" if m else ""
        lines.append(f"| {ICON[r['assessment']]} | {r['key']} | {r['confidence']:.2f} | {cell(found)} | "
                     f"{', '.join(r.get('sources') or [])} | {', '.join(r['notices'])} |")
    return "\n".join(lines) + "\n"


def entry(r: dict) -> list[str]:
    m = r.get("match") or {}
    out = [f"### {ICON[r['assessment']]} {r['key']}：{LABEL[r['assessment']]}", "",
           f"- 引用：{r['title']}（{', '.join(r['authors'][:3])}{' 等' if len(r['authors']) > 3 else ''}，{r['year']}，{r['venue']}）"]
    if m:
        out.append(f"- 最接近的记录：{m['title']}（{', '.join(m['authors'][:3])}，{m['year']}，{m['venue']}，{m['source']}）"
                   + (f" {m['url']}" if m.get("url") else ""))
    out += [f"- 提醒：{NOTICE.get(n, n)}" for n in r["notices"]]
    if r.get("note"):
        out.append(f"- 说明：{r['note']}" + ("（模型复查）" if r.get("investigated") else ""))
    return out + [""]


def cell(s: str) -> str:
    return s.replace("|", "/")
