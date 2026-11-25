"""The commercial plagiarism database check of text_overlap ("商业查重库"). Plain code,
no model calls.

Runs only when a provider has a key (providers.py) and the author opted in with the line
`Send to plagiarism service: yes` in workflow/text_overlap.md; the line is added with
`no` when a key is set and the line is missing. Winston AI and Originality.ai get only the
prose (never the bibliography, comments, math or citation keys); Turnitin (TCA) gets the
compiled PDF of the main .tex, else the prose, and its EULA must be accepted by the author
(turnitin.py). FAIL: the provider's overall similarity at
least TOTAL_LIMIT %, or one source the paper does not cite at least SOURCE_LIMIT % of the
words sent. The paper's own earlier version (same title) is shown but never fails.
"""

import re
from pathlib import Path

from auto_research.tasks.citations.refs import AUTHENTIC_TITLE, title_sim
from auto_research.tasks.library import resolve

from . import providers
from .turnitin import EulaRequired

TOTAL_LIMIT = 25.0       # % of the paper similar to the provider's database
SOURCE_LIMIT = 5.0       # % of the paper from one source it does not cite
NOTES = Path("workflow") / "text_overlap.md"
OPT_IN = re.compile(r"^[ \t]*[-*]?[ \t]*send to plagiarism service[ \t]*:[ \t]*(\S*)", re.I | re.M)
TEMPLATE = "Send to plagiarism service: no"


def opted_in(paper: Path) -> bool:
    """Whether workflow/text_overlap.md says yes; the line is added with no when absent."""
    path = paper / NOTES
    text = path.read_text(encoding="utf-8", errors="ignore") if path.is_file() else ""
    m = OPT_IN.search(text)
    if m:
        return m.group(1).strip().lower().rstrip(".") == "yes"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + ("" if not text or text.endswith("\n") else "\n") + TEMPLATE + "\n", encoding="utf-8")
    return False


def paper_text(sentences: list[dict]) -> str:
    """The prose as sent: every sentence as written, the bibliography already left out
    by prose.document."""
    return " ".join(" ".join(s["words"]) for s in sentences)


def cited(match: dict, refs: list[dict]) -> bool:
    if match["cited"]:
        return True
    url = (match["url"] or "").lower()
    doi = resolve.clean_doi(match.get("doi") or "")
    for r in refs:
        arxiv = re.sub(r"v\d+$", "", r.get("arxiv") or "").lower()
        if arxiv and arxiv in url or r.get("doi") and (r["doi"] in url or r["doi"] == doi):
            return True
        if r.get("title") and match["title"] and title_sim(r["title"], match["title"]) >= AUTHENTIC_TITLE:
            return True
    return False


def check(paper: Path, sentences: list[dict], title: str, choice: str, main_tex: str = "") -> dict:
    """The section of overlap.json: status is done, not_configured, off, not_opted_in,
    eula_required or error; done carries score, matches and problems. main_tex names the
    compiled PDF a file-based provider sends."""
    provider, missing = providers.select(choice)
    base = {"provider": provider.name if provider else "", "label": provider.label if provider else "",
            "doc": provider.doc if provider else "", "sends": provider.sends if provider else "",
            "not_configured": missing, "limits": {"total": TOTAL_LIMIT, "source": SOURCE_LIMIT},
            "score": None, "words": 0, "chunks": 0, "matches": [], "problems": [], "note": "",
            "submission_id": "", "viewer_url": "", "sent_file": "", "repositories": [], "by_repository": {},
            "notes": [], "eula_url": "", "eula_version": ""}
    if (choice or "").strip().lower() in ("none", "off", "no"):
        return base | {"status": "off", "note": "notes.plagiarism_provider is off"}
    if not provider:
        return base | {"status": "not_configured"}
    if not opted_in(paper):
        return base | {"status": "not_opted_in",
                       "note": f"{provider.label} has a key, but workflow/text_overlap.md does not say "
                               f"`Send to plagiarism service: yes`"}
    notes = paper / NOTES
    context = {"pdf": paper / Path(main_tex).with_suffix(".pdf") if main_tex else None,
               "notes": notes.read_text(encoding="utf-8", errors="ignore"), "notes_time": notes.stat().st_mtime}
    try:
        found = provider.scan(paper_text(sentences), title or paper.name, context)
    except EulaRequired as e:
        return base | {k: v for k, v in e.extra.items() if k in base} | {
            "status": "eula_required", "note": str(e), "eula_url": e.url, "eula_version": e.version}
    except providers.ProviderError as e:
        return base | {k: v for k, v in e.extra.items() if k in base} | {"status": "error", "note": str(e)}
    refs = resolve.entries(paper)
    problems = []
    for m in found["matches"]:
        m["same_paper"] = bool(title and m["title"] and title_sim(title, m["title"]) >= AUTHENTIC_TITLE)
        m["cited"] = cited(m, refs)
        if m["percent"] >= SOURCE_LIMIT and not m["cited"] and not m["same_paper"]:
            where = f" ({m['url']})" if m["url"] and m["title"] else ""
            problems.append(f"{provider.label}: {m['percent']}% of the paper matches {m['title'] or m['url']}"
                            f"{where}, which the paper does not cite")
    if found["score"] >= TOTAL_LIMIT:
        problems.insert(0, f"{provider.label}: overall similarity {found['score']}% is at least {TOTAL_LIMIT:g}%")
    return base | {k: v for k, v in found.items() if k in base} | {
        "status": "done", "score": found["score"], "words": found["words"], "chunks": found["chunks"],
        "matches": found["matches"], "problems": problems}


STATUS = {"done": "已比对", "not_configured": "未配置", "off": "已关闭", "not_opted_in": "未同意发送",
          "eula_required": "待同意 EULA", "error": "调用失败"}


def report(c: dict) -> list[str]:
    lines = ["## 商业查重库", ""]
    if c["status"] == "done":
        sent = (f"送出 {c['sent_file']}（{c['words']} 词）" if c.get("sent_file") else
                f"送出 {c['words']} 词，分 {c['chunks']} 段")
        lines += [f"{c['label']}（{c['doc']}）：整体相似度 **{c['score']}%**，{sent}。"
                  f"不通过的条件：整体 ≥ {c['limits']['total']:g}%，或单个未引用来源 ≥ {c['limits']['source']:g}%。", ""]
        lines += turnitin_lines(c)
        if c["matches"]:
            lines += ["| 来源 | 占比 | 词数 | 引用 | 片段 |", "| --- | --- | --- | --- | --- |"]
            for m in c["matches"]:
                mark = "本文早先版本" if m.get("same_paper") else "已引用" if m["cited"] else "未引用"
                excerpt = (m["excerpt"] or "—").replace("|", "\\|")
                kind = f"（{m['source_type']}）" if m.get("source_type") else ""
                lines.append(f"| {m['title'] or '—'} {m['url']}{kind} | {m['percent']}% | {m['matched_words']} | "
                             f"{mark} | {excerpt} |")
        else:
            lines.append("没有找到相似来源。")
        lines.append("")
    elif c["status"] == "not_opted_in":
        lines += [f"{c['label']} 已配置密钥，但没有发送：论文会离开本机交给第三方服务。同意的话，把 "
                  "`workflow/text_overlap.md` 里的 `Send to plagiarism service: no` 改成 "
                  f"`Send to plagiarism service: yes`（{c['sends']}）。", ""]
    elif c["status"] == "eula_required":
        lines += [f"{c['label']}：所在机构要求先同意 Turnitin 最终用户许可协议（EULA，版本 "
                  f"`{c['eula_version']}`），本程序不会代为同意，所以没有发送论文。请先阅读 "
                  f"{c['eula_url'] or '（Turnitin 没有给出链接，请向机构管理员索取）'}；同意的话，在 "
                  f"`workflow/text_overlap.md` 加一行 `Turnitin EULA accepted: {c['eula_version']}`，再运行一次。", ""]
        lines += turnitin_lines(c)
    elif c["status"] == "error":
        lines += [f"{c['label']} 没有给出结果：{c['note']}。本地比对照常完成。", ""]
        lines += turnitin_lines(c)
    elif c["status"] == "off":
        lines += ["`workflow/rules.json` 的 `notes.plagiarism_provider` 设为关闭，没有查询。", ""]
    else:
        lines += ["没有配置任何商业查重服务，没有查询。", ""]
    if c["not_configured"] and c["status"] != "off":
        lines += ["未配置（not configured）：" + "、".join(c["not_configured"]), ""]
    return lines


def turnitin_lines(c: dict) -> list[str]:
    """The Turnitin submission id, viewer link and repositories, when there are any."""
    out = []
    if c.get("submission_id"):
        out.append(f"- Turnitin 提交号：`{c['submission_id']}`（只建了这一份提交，没有加入比对库，也不会被删除或修改）")
    if c.get("viewer_url"):
        out.append(f"- 报告查看链接（很快过期，过期后重新运行生成）：{c['viewer_url']}")
    if c.get("repositories"):
        out.append("- 比对库：" + "、".join(c["repositories"]) + "；排除引号内文字和参考文献")
    if c.get("by_repository"):
        out.append("- 分库相似度：" + "，".join(f"{k} {v}%" for k, v in c["by_repository"].items()))
    out += [f"- {n}" for n in c.get("notes") or []]
    return out + [""] if out else []


__all__ = ["SOURCE_LIMIT", "TOTAL_LIMIT", "check", "report"]
