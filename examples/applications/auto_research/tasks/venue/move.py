"""Moving a paper to another venue after a rejection: what the new template changes in
the preamble, the bibliography style, the sections, the page budget and anonymity, as a
plan the page agent carries out (it then runs submission_check). Plain code; the paper
itself is never edited.
"""

import json
import re
from pathlib import Path

from auto_research.trail import Trail

from ..submission import check as sub_check
from ..submission import common
from . import facts
from .facts import Family

PREAMBLE_LINE = re.compile(r"^\s*\\(?:documentclass|usepackage|iclrfinalcopy|setcopyright|acmConference|"
                           r"def\\paperID|workshoptitle)\b.*$", re.M)


def venue_move(paper_dir: str, target: str, out_dir: str = "", write_venue: str = "no") -> str:
    """Plan the move of the paper to another venue's template.

    paper_dir is the project's Git repository; target names the venue family (iclr,
    neurips, icml, acl/emnlp/naacl/arr, cvpr/iccv, eccv, aaai, ijcai, kdd/acm, ieee, tmlr,
    jmlr, arxiv). Reads the main .tex, its PDF page count and workflow/venue.md. The plan
    lists the preamble lines to replace, the bibliography style, the sections to add or
    drop, the page budget against the current page count, the anonymity switch and where
    to download the official kit. Never edits the paper. write_venue="yes" also writes a
    new workflow/venue.md for the target, keeping the old one as venue.<old>.md. Writes
    plan.md, plan.json and the trail to out_dir/<timestamp>/ (default
    <paper_dir>/drafts/venue_move).
    """
    if write_venue not in ("yes", "no"):
        raise ValueError("write_venue is yes or no")
    to = facts.family(target)
    paper = common.repo(paper_dir)
    main, source = facts.source(paper)
    out = common.run_folder(paper, out_dir, "venue_move")
    trail = Trail("venue_move", {"paper_dir": str(paper), "target": target, "out_dir": out_dir,
                                 "write_venue": write_venue}, {})
    current = facts.detect(source) or facts.from_venue(paper)
    if current is None and re.search(r"\\documentclass(?:\[[^\]]*\])?\{article\}", source):
        current = facts.FAMILIES["arxiv"]
    trail.step("read", main=main.name, current=current.key if current else "", target=to.key)

    plan = {"from": current.key if current else "", "from_name": current.name if current else "未识别",
            "to": to.key, "to_name": to.name, "main_tex": main.name, "template": to.template,
            "preamble": preamble(source, to), "bib_style": bib_style(source, to),
            "sections": sections(source, current, to), "pages": pages(main, to),
            "anonymity": anonymity(source, current, to), "checklist": to.checklist,
            "references": to.references, "notes": list(to.notes)}
    plan["steps"] = steps(plan)
    trail.step("plan", sections=len(plan["sections"]), page_change=plan["pages"].get("change"))

    written = ""
    if write_venue == "yes":
        written = write_venue_md(paper, current, to)
        plan["venue_md"] = written
    (out / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "plan.md").write_text(render(plan), encoding="utf-8")
    run = trail.save(out, report=out / "plan.md", plan=out / "plan.json", venue=written)
    return json.dumps({"status": "PASS", "report": str(out / "plan.md"), "plan": str(out / "plan.json"),
                       "run": str(run), "from": plan["from"], "to": to.key, "pages": plan["pages"],
                       "steps": len(plan["steps"]), "venue_md": written}, ensure_ascii=False)


def preamble(source: str, to: Family) -> dict:
    lines = [m.group(0).strip() for m in PREAMBLE_LINE.finditer(source)]
    style = [line for line in lines if re.search(r"\\documentclass|\\iclrfinalcopy|\\setcopyright|\\acmConference|"
                                                  r"paperID|workshoptitle", line)
             or any(re.search(p, line) for f in facts.FAMILIES.values() for p in f.detect)]
    add = list(to.preamble)
    keep = [line for line in style if line in add]       # already the target's line: neither removed nor added
    return {"remove": [line for line in style if line not in keep],
            "add": [line for line in add if line not in keep], "target": add, "camera_ready": list(to.camera_ready)}


def bib_style(source: str, to: Family) -> dict:
    m = re.search(r"\\bibliographystyle\s*\{([^}]*)\}", source)
    now = m.group(1).strip() if m else ("biblatex" if "\\printbibliography" in source else "")
    return {"current": now, "target": to.bib_style, "change": now != to.bib_style}


def sections(source: str, current: Family | None, to: Family) -> list[dict]:
    out = []
    for pattern, what, where in to.required_sections:
        if not re.search(pattern, source, re.I):
            out.append({"action": "add", "section": what, "where": where})
    if current:
        targets = {w for _, w, _ in to.required_sections}
        for pattern, what, _ in current.required_sections:
            if what not in targets and re.search(pattern, source, re.I):
                out.append({"action": "remove", "section": what,
                            "where": f"{to.name} 不要求；删掉或并入正文/附录"})
    if to.anonymous and re.search(r"\\section\*?\s*\{\s*Acknowledge?ments?|\\begin\{acks?\}", source, re.I):
        out.append({"action": "remove", "section": "Acknowledgments", "where": "匿名投稿不放致谢，录用后放回"})
    return out


def pages(main: Path, to: Family) -> dict:
    pdf = main.with_suffix(".pdf")
    total, _ = sub_check.count_pages(pdf) if pdf.is_file() else (None, "")
    refs = sub_check.references_page(pdf) if pdf.is_file() else None
    body = (refs - 1 if refs and refs > 1 else refs) if refs else total
    out = {"total": total, "references_page": refs, "body_estimate": body, "limit": to.main_pages,
           "camera_ready_limit": to.camera_pages, "change": None}
    if body is not None and to.main_pages is not None:
        out["change"] = to.main_pages - body
    return out


def anonymity(source: str, current: Family | None, to: Family) -> dict:
    named = bool(re.search(r"\\author\s*(?:\[[^\]]*\])?\s*\{\s*[^}\s]", source)) and not re.search(
        r"\\author\s*(?:\[[^\]]*\])?\s*\{[^}]*anonymous", source, re.I)
    review = any(p.search(source) for p in sub_check.ANONYMOUS_MODE)
    now_anonymous = review or not named
    if to.anonymous and not now_anonymous:
        todo = ["换成目标模板的匿名选项（见导言区一节）", "删掉致谢、基金号和个人主页链接", "代码链接换成匿名仓库，"
                "补充材料用 anonymous_package 打包", "第一人称的自引改成第三人称", "清空 PDF 元数据里的作者"]
    elif not to.anonymous and now_anonymous:
        todo = ["写出全部作者和单位", "放回致谢和基金号", "代码链接换成正式仓库"]
    else:
        todo = []
    return {"current": "anonymous" if now_anonymous else "named", "target": "anonymous" if to.anonymous else "named",
            "todo": todo}


def steps(plan: dict) -> list[str]:
    out = [f"下载 {plan['to_name']} 官方模板：{plan['template']}，把 .sty/.cls/.bst 放进仓库根目录。"]
    if plan["preamble"]["remove"]:
        out.append("导言区删掉旧模板的行：" + "；".join(f"`{x}`" for x in plan["preamble"]["remove"]) + "。")
    if plan["preamble"]["add"]:
        out.append("加上：" + "；".join(f"`{x}`" for x in plan["preamble"]["add"]) + "（年份以下载的模板为准）。")
    if plan["bib_style"]["change"]:
        out.append(f"参考文献样式 `{plan['bib_style']['current'] or '未设'}` 改成 `\\bibliographystyle{{{plan['bib_style']['target']}}}`，"
                   "重新运行 bibtex。")
    for s in plan["sections"]:
        out.append(("加上 " if s["action"] == "add" else "去掉 ") + f"{s['section']}（{s['where']}）。")
    if plan["checklist"]:
        out.append(f"清单：{plan['checklist']}。")
    p = plan["pages"]
    if p["change"] is not None and p["change"] < 0:
        out.append(f"正文估计 {p['body_estimate']} 页，{plan['to_name']} 上限 {p['limit']} 页：要删减约 {-p['change']} 页。")
    elif p["change"] is not None:
        out.append(f"正文估计 {p['body_estimate']} 页，上限 {p['limit']} 页：还有约 {p['change']} 页空间。")
    elif p["limit"] is None:
        out.append(f"{plan['to_name']} 没有硬性页数上限（{plan['references']}）。")
    out += [f"匿名：{t}。" for t in plan["anonymity"]["todo"]]
    out.append("改完编译，运行 submission_check，直到通过。")
    return out


def render(plan: dict) -> str:
    p, a = plan["pages"], plan["anonymity"]
    lines = [f"# 转投计划：{plan['from_name']} → {plan['to_name']}", "",
             f"主文件 `{plan['main_tex']}`。本计划不改论文；按步骤改完后运行 submission_check。", "",
             "## 步骤", ""] + [f"{i}. {s}" for i, s in enumerate(plan["steps"], 1)] + ["",
             "## 对照", "", "| 项目 | 现在 | 目标 |", "|---|---|---|",
             f"| 模板 | {plan['from_name']} | {plan['to_name']} |",
             f"| 参考文献样式 | {plan['bib_style']['current'] or '—'} | {plan['bib_style']['target']} |",
             f"| 正文页数 | {p['body_estimate'] if p['body_estimate'] is not None else '未知（没有 PDF）'} | "
             f"{p['limit'] if p['limit'] is not None else '不限'}（终稿 {p['camera_ready_limit'] or '不限'}） |",
             f"| 参考文献和附录 | — | {facts.cell(plan['references'])} |",
             f"| 匿名 | {'匿名' if a['current'] == 'anonymous' else '署名'} | {'匿名' if a['target'] == 'anonymous' else '署名'} |",
             "", "## 目标导言区", "", "投稿版：", "", "```latex", *plan["preamble"]["target"], "```", "",
             "录用后的终稿：", "", "```latex", *plan["preamble"]["camera_ready"], "```", ""]
    if plan["notes"]:
        lines += ["## 其他", ""] + [f"- {n}" for n in plan["notes"]] + [""]
    if plan.get("venue_md"):
        lines += [f"新的 venue.md 已写到 `{plan['venue_md']}`，旧的另存为 venue.{plan['from'] or 'old'}.md。", ""]
    lines += ["模板事实按各会议最近一期的征稿和作者包整理；年份、页数以当期 call for papers 为准。", ""]
    return "\n".join(lines)


def write_venue_md(paper: Path, current: Family | None, to: Family) -> str:
    folder = paper / "workflow"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "venue.md"
    keep = {k: facts.field_value(paper, k) for k in ("Authors", "Affiliations", "Funding")}
    if path.is_file():
        old = folder / f"venue.{current.key if current else 'old'}.md"
        path.rename(old)
    review = "double-blind review" if to.anonymous else "single-blind (authors named)"
    limit = f"page limit: {to.main_pages} pages" if to.main_pages else "no page limit"
    lines = ["# Venue", "", "## Venue", f"Name: {to.name}", f"{limit}, {review}.", f"References: {to.references}.",
             f"Template: {to.template}", f"Camera-ready pages: {to.camera_pages}" if to.camera_pages else "",
             *[f"{k}: {v}" for k, v in keep.items() if v], "", "## Requirements"]
    lines += [f"- {what} ({where})" for _, what, where in to.required_sections]
    lines += [f"- {to.checklist}"] if to.checklist else []
    lines += [f"- {n}" for n in to.notes]
    lines += ["", "## Submission checklist", "- [ ] Title and abstract in the submission system match the paper"]
    if to.anonymous:
        lines.insert(len(lines) - 1, "- [ ] Anonymous: no names, affiliations, acknowledgements or identifying links")
    lines += ["", "## Camera-ready checklist", "- [ ] Reviewer comments addressed or answered", ""]
    path.write_text("\n".join(x for x in lines if x is not None) + "\n", encoding="utf-8")
    return str(path)


__all__ = ["venue_move"]
