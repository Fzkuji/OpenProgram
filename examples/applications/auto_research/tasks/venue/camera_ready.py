"""The camera-ready check: what ACM TAPS, IEEE PDF eXpress and a publisher's production
editor send back on a final version. The review switches are off, the authors and
acknowledgments are back, the publisher's metadata is there, every figure has alt text,
nothing of the draft is left and the final page limit holds. Plain code, no model calls.
"""

import json
import re
from pathlib import Path

from auto_research.trail import Trail

from ..submission import check as sub_check
from ..submission import common
from . import facts
from .facts import finding

REVIEW_OPTIONS = ("review", "anonymous", "peerreview", "submission", "blind", "doubleblind")
VENUE_STYLES = r"(?:acl|acl2023|naaclhlt\d*|emnlp\d*|cvpr|iccv|eccv|aaai\d{2,4}|ijcai\d{2}|neurips_\d{4}|icml\d{4}|tmlr|iclr\d{4}_conference)"
DEFINITION = re.compile(r"\\(?:newcommand|renewcommand|providecommand|DeclareRobustCommand|def|let)\b")
DRAFT_MARK = re.compile(r"\bTODO\b|\bFIXME\b|\bXXX\b|\\todo\b|\\fixme\b|\\missingfigure\b|\\red\s*\{|\\hl\s*\{|"
                        r"\\textcolor\s*\{\s*red\s*\}|\\color\s*\{\s*red\s*\}")
ACKS = re.compile(r"\\section\*?\s*\{\s*Acknowledge?ments?\s*\}|\\paragraph\*?\s*\{\s*Acknowledge?ments?|"
                  r"\\begin\{acks?\}|\\acksection\b|\\acknowledgments\b", re.I)
AUTHOR = re.compile(r"\\author\s*(?:\[[^\]]*\])?\s*\{((?:[^{}]|\{(?:[^{}]|\{[^{}]*\})*\})*)\}")
FIGURE = re.compile(r"\\begin\{(figure\*?|wrapfigure|teaserfigure)\}(.*?)\\end\{\1\}", re.S)
ALT = re.compile(r"\\Description\s*(?:\[[^\]]*\])?\s*\{|\\alt\s*\{|alt\s*=\s*\{|^\s*%\s*alt\s*:", re.M | re.I)
ALT_CONVENTION = {
    "acm": "ACM：每个 figure 里写 \\Description{一两句描述图的内容}（TAPS 会拒收没有 \\Description 的图）。",
    "ieee": "IEEE：PDF eXpress 不要求，建议在 \\includegraphics[alt={...}] 写替代文字。",
}
ALT_DEFAULT = ("在 \\includegraphics[alt={...}] 里写替代文字（LaTeX 标注 PDF 用它），"
               "或至少在 figure 里加一行 “% alt: 描述”，录入出版系统时照抄。")


def camera_ready_check(paper_dir: str, out_dir: str = "") -> str:
    """Check the de-anonymized final version of the paper before it goes to the publisher.

    paper_dir is the project's Git repository; the main .tex comes from rules.json, the
    PDF is expected next to it. Checks: \\author filled and not Anonymous; review and
    anonymous options gone (review/anonymous in \\documentclass or a venue style, NeurIPS
    final, ICML accepted, ICLR \\iclrfinalcopy, ACL without review, TMLR accepted);
    acknowledgments present (note when missing) and naming the grants of venue.md's
    `Funding:` line; for acmart the CCS concepts and the rights block; alt text in every
    figure (\\Description for ACM, an error there; alt= or a `% alt:` comment elsewhere, a
    note); no Anonymous, TODO/FIXME, \\todo or red-text macros; line numbers off; the page
    count against venue.md's `Camera-ready pages:`. FAIL on any error. Writes report.md,
    camera_ready.json and the trail to out_dir/<timestamp>/ (default
    <paper_dir>/drafts/camera_ready_check).
    """
    paper = common.repo(paper_dir)
    main, source = facts.source(paper)
    raw = facts.raw_sources(paper, main)
    out = common.run_folder(paper, out_dir, "camera_ready_check")
    trail = Trail("camera_ready_check", {"paper_dir": str(paper), "out_dir": out_dir}, {})
    fam = facts.detect(source) or facts.from_venue(paper)
    key = fam.key if fam else ""
    findings: list[dict] = []
    findings += author_findings(source, main.name)
    findings += switch_findings(source, key, main.name)
    trail.step("switches", family=key, found=len(findings))
    findings += acks_findings(source, facts.field_list(paper, "Funding"), main.name)
    if key == "acm":
        findings += acm_findings(source, main.name)
    findings += figure_findings(raw, key)
    findings += draft_findings(raw)
    trail.step("source", files=sorted(raw), found=len(findings))
    pages, limit, page_found = page_findings(paper, main)
    findings += page_found
    trail.step("pages", pages=pages, limit=limit)

    errors = [f for f in findings if f["level"] == "error"]
    status = "FAIL" if errors else "PASS_WITH_NOTES" if findings else "PASS"
    data = {"status": status, "main_tex": main.name, "family": key, "pages": pages, "page_limit": limit,
            "findings": findings}
    (out / "camera_ready.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "report.md").write_text(report(data), encoding="utf-8")
    run = trail.save(out, report=out / "report.md", findings=out / "camera_ready.json")
    return json.dumps({"status": status, "report": str(out / "report.md"), "run": str(run),
                       "findings": str(out / "camera_ready.json"), "errors": len(errors),
                       "notes": len(findings) - len(errors), "family": key, "pages": pages, "page_limit": limit},
                      ensure_ascii=False)


def author_findings(source: str, main: str) -> list[dict]:
    blocks = AUTHOR.findall(source)
    if not blocks:
        return [finding("authors", "error", main, "没有 \\author：终稿必须写出全部作者。",
                        "在导言区按模板写 \\author{...}，包括单位和邮箱。")]
    text = " ".join(blocks)
    names = re.sub(r"\\[A-Za-z]+|[{}\\$^_~,&]", " ", text)
    if re.search(r"anonymous", names, re.I):
        return [finding("authors", "error", main, "\\author 里还是 Anonymous。", "换成真实作者、单位和邮箱。")]
    if not re.search(r"[A-Za-z]{2,}", names):
        return [finding("authors", "error", main, "\\author 是空的。", "写出全部作者、单位和邮箱。")]
    return []


def _options(source: str, command: str) -> list[tuple[str, list[str]]]:
    return [(m.group(2), [o.strip().split("=")[0] for o in m.group(1).split(",")])
            for m in re.finditer(rf"\\{command}\s*\[([^\]]*)\]\s*\{{([^}}]*)\}}", source)]


def switch_findings(source: str, key: str, main: str) -> list[dict]:
    """The review switches a final version must not carry, and the final switches it needs."""
    found = []
    for cls, opts in _options(source, "documentclass"):
        bad = [o for o in opts if o.lower() in REVIEW_OPTIONS]
        if bad:
            found.append(finding("review_mode", "error", main, f"\\documentclass{{{cls}}} 还带着审稿选项 {', '.join(bad)}"
                                 "（匿名、行号或审稿版式）。", f"去掉 {', '.join(bad)}，如 acmart 改成 [sigconf]。"))
    for pkg, opts in _options(source, "usepackage"):
        bad = [o for o in opts if o.lower() in REVIEW_OPTIONS]
        if bad and re.fullmatch(VENUE_STYLES, pkg.strip()):
            fix = "改成 [final]" if re.match(r"acl|naacl|emnlp|cvpr|iccv", pkg) else "去掉这个选项"
            found.append(finding("review_mode", "error", main, f"\\usepackage{{{pkg}}} 还带着 {', '.join(bad)} 选项。",
                                 f"{fix}，重新编译。"))
    styles = {pkg.strip(): [o.lower() for o in opts] for pkg, opts in _options(source, "usepackage")}
    for m in re.finditer(r"\\usepackage\s*\{([^}]*)\}", source):
        for pkg in m.group(1).split(","):
            styles.setdefault(pkg.strip(), [])
    for pkg, opts in styles.items():
        if re.fullmatch(r"neurips_\d{4}", pkg):
            if "preprint" in opts:
                found.append(finding("review_mode", "note", main, f"{pkg} 用的是 preprint 选项（arXiv 版式）。",
                                     f"会议终稿用 \\usepackage[final]{{{pkg}}}。"))
            elif "final" not in opts:
                found.append(finding("review_mode", "error", main, f"{pkg} 没有 final 选项，仍是匿名审稿版。",
                                     f"改成 \\usepackage[final]{{{pkg}}}。"))
        if re.fullmatch(r"icml\d{4}", pkg) and "accepted" not in opts:
            found.append(finding("review_mode", "error", main, f"{pkg} 没有 accepted 选项，仍是匿名审稿版。",
                                 f"改成 \\usepackage[accepted]{{{pkg}}}。"))
        if pkg == "tmlr" and "accepted" not in opts:
            found.append(finding("review_mode", "error", main, "tmlr 没有 accepted 选项，仍是匿名审稿版。",
                                 "改成 \\usepackage[accepted]{tmlr}。"))
        if pkg == "todonotes" and "disable" not in opts:
            found.append(finding("draft", "note", main, "加载了 todonotes 且没有 disable。",
                                 "删掉 todonotes，或改成 \\usepackage[disable]{todonotes}。"))
    if key == "iclr" and not re.search(r"^\s*\\iclrfinalcopy\b", source, re.M):
        found.append(finding("review_mode", "error", main, "没有 \\iclrfinalcopy，作者不会显示。",
                             "在 \\begin{document} 之前加一行 \\iclrfinalcopy。"))
    for m in re.finditer(r"^[^\n]*\\linenumbers\b[^\n]*$", source, re.M):
        if not DEFINITION.search(m.group(0)):
            found.append(finding("line_numbers", "error", main, "还开着 \\linenumbers。", "删掉 \\linenumbers（和 lineno 宏包）。"))
            break
    return found


def acks_text(source: str) -> str:
    m = ACKS.search(source)
    if not m:
        return ""
    rest = source[m.end():]
    end = re.search(r"\\section\b|\\end\{acks?\}|\\bibliography\b|\\printbibliography|\\appendix|\\end\{document\}", rest)
    return rest[:end.start() if end else 3000]


def acks_findings(source: str, funding: list[str], main: str) -> list[dict]:
    text = acks_text(source)
    if not ACKS.search(source):
        found = [finding("acknowledgments", "note", main, "没有致谢节（投稿时删掉的可能没放回）。",
                         "终稿把致谢放回：资助、帮助过的人；ACM 用 \\begin{acks}，NeurIPS 用 \\begin{ack}。")]
        return found + [finding("funding", "error", main, f"venue.md 列出资助 {item}，终稿里没有致谢写明它。",
                                f"在致谢里写明 {item}（含基金号）。") for item in funding]
    flat = " ".join(text.split()).lower()
    found = []
    for item in funding:
        numbers = re.findall(r"[A-Za-z]*\d[\w/-]*", item)
        missing = [n for n in numbers if n.lower() not in flat] if numbers else ([] if item.lower() in flat else [item])
        if missing:
            found.append(finding("funding", "error", main, f"致谢里没有写资助 {item}（缺 {', '.join(missing)}）。",
                                 f"按资助方要求在致谢里写明 {item}，基金号逐字照抄。"))
    return found


def acm_findings(source: str, main: str) -> list[dict]:
    found = []
    if not re.search(r"\\begin\{CCSXML\}", source):
        found.append(finding("acm_metadata", "error", main, "没有 \\begin{CCSXML} 块。",
                             "在 https://dl.acm.org/ccs 选概念，生成 CCSXML 块贴到导言区。"))
    if not re.search(r"\\ccsdesc\b", source):
        found.append(finding("acm_metadata", "error", main, "没有 \\ccsdesc。", "和 CCSXML 一起生成的 \\ccsdesc[500]{...} 行贴到导言区。"))
    if not re.search(r"\\setcopyright\s*\{", source):
        found.append(finding("acm_rights", "error", main, "没有 \\setcopyright（版权块）。",
                             "完成 ACM 版权表后，把邮件里的 \\setcopyright、\\copyrightyear、\\acmYear、\\acmDOI、"
                             "\\acmConference、\\acmISBN 原样贴到导言区。"))
    elif not re.search(r"\\acmDOI\s*\{|\\acmConference\s*[\[{]", source):
        found.append(finding("acm_rights", "note", main, "有 \\setcopyright，但没有 \\acmDOI 或 \\acmConference。",
                             "把版权邮件里的整块权利信息都贴进去。"))
    if not re.search(r"\\keywords\s*\{", source):
        found.append(finding("acm_metadata", "note", main, "没有 \\keywords。", "在导言区加 \\keywords{...}。"))
    return found


def _line_of(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def _commented(text: str, index: int) -> bool:
    start = text.rfind("\n", 0, index) + 1
    cut = common._comment_at(text[start:index + 1])
    return 0 <= cut <= index - start


def figure_findings(raw: dict[str, str], key: str) -> list[dict]:
    missing = []
    for path, text in raw.items():
        for m in FIGURE.finditer(text):
            if _commented(text, m.start()):
                continue
            body = m.group(2)
            if "\\includegraphics" not in body and "\\begin{tikzpicture}" not in body and "\\input" not in body:
                continue
            alt = re.search(r"\\Description\s*(?:\[[^\]]*\])?\s*\{", common.strip_comments(body)) if key == "acm" \
                else ALT.search(body)
            if not alt:
                missing.append(f"{path}:{_line_of(text, m.start())}")
    if not missing:
        return []
    level = "error" if key == "acm" else "note"
    return [finding("alt_text", level, where, "这个图没有替代文字。", ALT_CONVENTION.get(key, ALT_DEFAULT))
            for where in missing]


def draft_findings(raw: dict[str, str]) -> list[dict]:
    found = []
    for path, text in raw.items():
        for n, line in enumerate(text.splitlines(), 1):
            cut = common._comment_at(line)
            code = line if cut < 0 else line[:cut]
            if not code.strip() or DEFINITION.search(code):
                continue
            if re.search(r"\banonymous\b", code, re.I) and not re.search(r"\\(?:documentclass|usepackage|author)\b", code):
                found.append(finding("anonymous_left", "error", f"{path}:{n}", f"正文还有 “{code.strip()[:80]}”。",
                                     "换成真实信息（如匿名仓库链接换成正式仓库）。"))
            mark = DRAFT_MARK.search(code)
            if mark:
                found.append(finding("draft", "error", f"{path}:{n}", f"还留着草稿标记 {mark.group(0).strip()}："
                                     f"“{code.strip()[:80]}”。", "处理完删掉这个标记，或删掉整句。"))
    return found


def page_findings(paper: Path, main: Path) -> tuple[int | None, int | None, list[dict]]:
    value = facts.field_value(paper, "Camera-ready pages")
    limit = int(re.search(r"\d+", value).group(0)) if re.search(r"\d+", value) else None
    pdf = main.with_suffix(".pdf")
    if not pdf.is_file():
        return None, limit, [finding("pages", "note", pdf.name, f"没有编译好的 {pdf.name}，没有数页数。",
                                     f"编译 {main.name} 后重跑。")]
    pages, _ = sub_check.count_pages(pdf)
    if pages is None:
        return None, limit, [finding("pages", "note", pdf.name, "pypdf 和 pdfinfo 都不可用，没有数页数。",
                                     "安装 pypdf 或 poppler 后重跑。")]
    if limit is None:
        return pages, None, [finding("pages", "note", "workflow/venue.md", f"共 {pages} 页；venue.md 没有写 "
                                     "“Camera-ready pages:”，没有比较。", "在 venue.md 加一行 “Camera-ready pages: 9”。")]
    if pages <= limit:
        return pages, limit, []
    refs = sub_check.references_page(pdf)
    if refs is not None and refs <= limit:
        return pages, limit, [finding("pages", "note", pdf.name, f"总页数 {pages} 超过终稿上限 {limit}，"
                                      f"但参考文献从第 {refs} 页开始。", "确认会议不把参考文献和附录计入终稿页数。")]
    return pages, limit, [finding("pages", "error", pdf.name, f"共 {pages} 页，超过终稿上限 {limit} 页"
                                  + (f"（参考文献从第 {refs} 页开始）" if refs else "") + "。",
                                  f"把正文压到 {limit} 页以内。")]


def report(data: dict) -> str:
    title = {"PASS": "可以提交", "PASS_WITH_NOTES": "可以提交，有提示", "FAIL": "不能提交"}[data["status"]]
    lines = [f"# 终稿检查：{title}", "",
             f"主文件 `{data['main_tex']}`，模板 {data['family'] or '未识别'}，"
             f"页数 {data['pages'] if data['pages'] is not None else '未知'}，"
             f"终稿上限 {data['page_limit'] if data['page_limit'] is not None else '未写'}。", ""]
    for level, name in (("error", "必须修改"), ("note", "提示")):
        rows = [f for f in data["findings"] if f["level"] == level]
        if rows:
            lines += [f"## {name}（{len(rows)}）", "", "| 类型 | 位置 | 问题 | 修法 |", "|---|---|---|---|"]
            lines += [f"| {f['kind']} | `{f['where']}` | {facts.cell(f['problem'])} | {facts.cell(f['fix'])} |"
                      for f in rows]
            lines.append("")
    if not data["findings"]:
        lines.append("没有发现问题。")
    return "\n".join(lines) + "\n"


__all__ = ["camera_ready_check"]
