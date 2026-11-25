"""Text overlap: find passages of the paper that repeat the author's earlier papers, the
works it cites, or the closest works on its topic, as Turnitin, iThenticate and
Paperpal's similarity checks do, and optionally a commercial plagiarism database.
Plain code, no model calls.

Steps: read (the paper's prose as sentences with file:line), sources (own earlier
papers, the arXiv works of the bibliography, a search on the title), match (word
8-gram shingles, adjacent sentences merged into passages), commercial (a plagiarism
service, only with a key and the author's opt-in; commercial.py), report.
"""

import json
from datetime import datetime
from pathlib import Path

from auto_research.trail import Trail

from ..revision.rules import paper as paper_rules
from . import commercial
from . import gather as gathering
from . import match as matching
from . import prose

OWN_LIMIT = 100          # words of an own-paper match that need a note
NOTES = Path("workflow") / "text_overlap.md"


def text_overlap(paper_dir: str, own_papers: str = "", out_dir: str = "") -> str:
    """Find passages of the paper that repeat its sources.

    paper_dir is the paper's Git repository; workflow/rules.json names the main .tex.
    own_papers: comma-separated .tex/.pdf/.txt/.md files or folders of the author's
    earlier papers, added to workflow/own_papers/. Also compared: every bibliography
    entry on arXiv (full text, else abstract; at most 30) and the abstracts of the top
    results of a search on the paper's title. FAIL: an uncited match with another work,
    or more than 100 words from an own paper with neither a \\cite in the passage nor a
    line in workflow/text_overlap.md naming the file. PASS_WITH_NOTES: other matches,
    or a source that could not be read. With a plagiarism service key (WINSTONAI_API_KEY,
    ORIGINALITY_API_KEY, or TCA_BASE_URL + TCA_API_KEY for Turnitin / iThenticate) and
    "Send to plagiarism service: yes" in workflow/text_overlap.md, the paper is also checked
    by that service (Turnitin gets the compiled PDF and needs the author's EULA line); FAIL
    at an overall similarity of 25% or one uncited source of 5%. Results go to out_dir/<timestamp>/ (default
    <paper_dir>/drafts/text_overlap); downloads are cached in drafts/text_overlap/cache.
    """
    paper = Path(paper_dir).expanduser().resolve()
    if not paper.is_dir():
        raise ValueError(f"{paper} is not a folder")
    rules = paper_rules.load(paper)
    main_tex = rules.main_tex
    if not (paper / main_tex).is_file():
        raise ValueError(f"{paper / main_tex} does not exist")
    base = Path(out_dir).expanduser() if out_dir else paper / "drafts" / "text_overlap"
    trail = Trail("text_overlap", {"paper_dir": str(paper), "own_papers": own_papers}, {})

    sentences = prose.paper_sentences(paper, main_tex)
    if not sentences:
        raise ValueError(f"{paper / main_tex} has no prose")
    title = prose.title(paper, main_tex)
    trail.step("read", main_tex=main_tex, title=title, sentences=len(sentences),
               words=sum(len(s["norm"]) for s in sentences), files=sorted({s["file"] for s in sentences}))

    found, unavailable = gathering.gather(paper, own_papers, title, paper / "drafts" / "text_overlap" / "cache")
    trail.step("sources", own=sum(s["kind"] == "own" for s in found),
               reference=sum(s["kind"] == "reference" for s in found),
               search=sum(s["kind"] == "search" for s in found), unavailable=unavailable)

    raw, matched, total = matching.find(sentences, found)
    by_id = {s["id"]: s for s in found}
    notes = read_notes(paper)
    matches = [describe(m, sentences, by_id[m["source"]], notes) for m in raw]
    index = matched / total if total else 0.0
    status, problems = verdict(matches, unavailable)
    trail.step("match", matches=len(matches), matched_words=matched, total_words=total,
               index=round(index, 4), problems=problems)

    db = commercial.check(paper, sentences, title, str((rules.notes or {}).get("plagiarism_provider") or ""),
                          main_tex)
    status, problems = merge(status, problems, db)
    trail.step("commercial", provider=db["provider"], state=db["status"], score=db["score"],
               matches=len(db["matches"]), problems=db["problems"], not_configured=db["not_configured"],
               note=db["note"], submission_id=db["submission_id"])

    data = {"status": status, "paper_dir": str(paper), "main_tex": main_tex, "title": title,
            "index": round(index, 4), "matched_words": matched, "total_words": total,
            "matches": matches, "problems": problems,
            "sources": [{k: v for k, v in s.items() if k != "words"} | {"words": len(s["words"])} for s in found],
            "unavailable_sources": unavailable, "commercial": db}
    out = base / datetime.now().strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    (out / "overlap.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "report.md").write_text(report(data), encoding="utf-8")
    trail.step("report", status=status)
    run = trail.save(out, report=out / "report.md", json=out / "overlap.json")
    return json.dumps({"status": status, "report": str(out / "report.md"), "json": str(out / "overlap.json"),
                       "run": str(run), "index": round(index, 4), "matches": [short(m) for m in matches],
                       "sources": len(found), "unavailable_sources": unavailable,
                       "commercial": {"provider": db["provider"], "status": db["status"], "score": db["score"],
                                      "matches": len(db["matches"]), "not_configured": db["not_configured"],
                                      "submission_id": db["submission_id"]}},
                      ensure_ascii=False)


def read_notes(paper: Path) -> str:
    path = paper / NOTES
    return path.read_text(encoding="utf-8", errors="ignore") if path.is_file() else ""


def describe(m: dict, sentences: list[dict], src: dict, notes: str) -> dict:
    first = sentences[m["first"]]
    last = sentences[m["last"]]
    where = f"{first['file']}:{first['line']}" + (f"-{last['line']}" if last["line"] != first["line"] and
                                                  last["file"] == first["file"] else "")
    own = src["kind"] == "own"
    noted = own and (m["cited"] or bool(src["file"]) and (src["file"] in notes or Path(src["file"]).name in notes))
    return {"location": where, "file": first["file"], "line": first["line"],
            "passage": " ".join(w for s in sentences[m["first"]:m["last"] + 1] for w in s["words"]),
            "words": m["words"], "overlap": round(m["overlap"] * 100, 1),
            "source": {"id": src["id"], "kind": src["kind"], "title": src["title"], "url": src["url"],
                       "file": src["file"], "level": src["level"]},
            "quoted": m["quoted"], "cited": m["cited"], "own": own, "noted": noted}


def verdict(matches: list[dict], unavailable: list[str]) -> tuple[str, list[str]]:
    problems = []
    for m in matches:
        if not m["own"] and not m["cited"]:
            problems.append(f"{m['location']}: {m['words']} words repeat {m['source']['title']} without a citation")
        elif m["own"] and m["words"] > OWN_LIMIT and not m["noted"]:
            problems.append(f"{m['location']}: {m['words']} words repeat own paper {m['source']['file']} "
                            f"without a note")
    status = "FAIL" if problems else "PASS_WITH_NOTES" if matches or unavailable else "PASS"
    return status, problems


def merge(status: str, problems: list[str], db: dict) -> tuple[str, list[str]]:
    """The verdict with the commercial check: its problems FAIL, a failed request or a
    pending EULA is a note."""
    problems = problems + db["problems"]
    if problems:
        return "FAIL", problems
    if db["status"] in ("error", "eula_required") or db["status"] == "done" and db["matches"]:
        return "PASS_WITH_NOTES", problems
    return status, problems


def short(m: dict) -> dict:
    cut = m["passage"] if len(m["passage"]) <= 300 else m["passage"][:300].rsplit(" ", 1)[0] + " …"
    return {"location": m["location"], "words": m["words"], "overlap": m["overlap"], "source": m["source"]["title"],
            "kind": m["source"]["kind"], "url": m["source"]["url"] or m["source"]["file"],
            "quoted": m["quoted"], "cited": m["cited"], "noted": m["noted"], "passage": cut}


KINDS = {"own": "作者早先的论文", "reference": "参考文献", "search": "标题检索结果"}
LEVELS = {"full": "全文", "abstract": "仅摘要", "text": "文件", "local": "全文（你下载的 PDF）",
          "open_access": "全文（开放获取 PDF）"}


def report(d: dict) -> str:
    lines = [f"# 文本重合检查：{d['title'] or Path(d['paper_dir']).name}", "",
             f"结论：**{d['status']}**。相似度指数 {d['index'] * 100:.1f}%"
             f"（{d['matched_words']} / {d['total_words']} 词落在重合段落里）。", "",
             "> 这是投稿前的自查，不能代替会议或期刊用 iThenticate 做的正式查重：这里只比对下面列出的来源，"
             "全文只来自 arXiv，其余只有摘要。", ""]
    if d["problems"]:
        lines += ["## 必须处理", ""] + [f"- {p}" for p in d["problems"]] + [""]
    lines += ["## 重合段落", ""]
    if not d["matches"]:
        lines += ["没有达到阈值（≥ 25 词且 ≥ 60% 的 8 词片段相同）的重合段落。", ""]
    for i, m in enumerate(d["matches"], 1):
        src = m["source"]
        where = src["url"] or src["file"]
        flags = [("引号内" if m["quoted"] else "不在引号内"), ("句内有 \\cite" if m["cited"] else "句内没有 \\cite")]
        if m["own"]:
            flags.append("已说明" if m["noted"] else "未说明")
        lines += [f"### {i}. `{m['location']}`：{m['words']} 词，重合 {m['overlap']}%", "",
                  f"- 来源：{KINDS[src['kind']]}《{src['title']}》（{where}，{LEVELS.get(src['level'], src['level'])}）",
                  f"- {'；'.join(flags)}", "", f"> {m['passage']}", ""]
    lines += ["## 比对的来源", "", "| 类型 | 来源 | 读到 | 词数 |", "| --- | --- | --- | --- |"]
    for s in d["sources"]:
        lines.append(f"| {KINDS[s['kind']]} | {s['title']} {s['url'] or s['file']} | "
                     f"{LEVELS.get(s['level'], s['level'])} | {s['words']} |")
    if not d["sources"]:
        lines.append("| — | 没有可比对的来源 | — | 0 |")
    lines.append("")
    lines += commercial.report(d["commercial"])
    if d["unavailable_sources"]:
        lines += ["## 没能读取", ""] + [f"- {u}" for u in d["unavailable_sources"]] + [""]
    lines += ["## 怎么处理", "",
              "- 与他人工作重合：改写成自己的话，或加引号并在句内引用。",
              "- 与自己早先论文重合超过 100 词：引用早先论文，或在 `workflow/text_overlap.md` 写一行说明"
              "（写出文件名和原因，例如已向会议说明这是扩展版）。",
              "- 商业查重库的未引用来源：引用它，或改写重合的句子。", ""]
    return "\n".join(lines)


__all__ = ["text_overlap"]
