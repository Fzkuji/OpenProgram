"""The pre-submission check: what a venue's format checker, Overleaf's error assist and a
pre-submission proofreader would stop on, read from the compiled PDF, its .log and the
source. Plain code, no model calls.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

from auto_research.trail import Trail

from . import common

PDF_LIMIT = 50_000_000
SUPPLEMENT_LIMIT = 100_000_000
OVERFULL_PT = 10.0
SELF_CITE = re.compile(r"\b(?:our|my)\s+(?:previous|prior|earlier|recent|own)\s+(?:work|paper|study|studies|"
                       r"papers|works|method|approach)\b|\bin\s+our\s+(?:earlier|previous|prior)\s+\w+|"
                       r"\bwe\s+(?:previously|have\s+previously|earlier)\s+(?:proposed|showed|introduced|"
                       r"presented|published)\b", re.I)
REPO_URL = re.compile(r"https?://(?:www\.)?(?:github|gitlab)\.com/[^\s}{\\]+", re.I)
HOMEPAGE = re.compile(r"https?://(?:[\w-]+\.github\.io|sites\.google\.com/[^\s}]+|[\w.-]+/~[\w.-]+)[^\s}{\\]*", re.I)
ANONYMOUS_MODE = (re.compile(r"\\documentclass\[[^\]]*\b(?:anonymous|review)\b"),
                  re.compile(r"\\usepackage\[[^\]]*\breview\b[^\]]*\]\{(?:acl|naaclhlt|emnlp|coling|cvpr|iccv|eccv)"),
                  re.compile(r"\\usepackage(?:\[(?![^\]]*\b(?:final|preprint)\b)[^\]]*\])?\{neurips_\d+\}"),
                  re.compile(r"\\usepackage(?:\[(?![^\]]*\baccepted\b)[^\]]*\])?\{icml\d+\}"))


def submission_check(paper_dir: str, out_dir: str = "") -> str:
    """Check the compiled paper before it is submitted: LaTeX errors, undefined references
    and citations, duplicate labels and overfull boxes from the .log; the page count
    against the limit in workflow/venue.md; anonymity when the venue is double-blind;
    embedded fonts and file sizes.

    paper_dir is the project's Git repository; the main .tex comes from rules.json and the
    PDF and .log are expected next to it. FAIL on a LaTeX error, an undefined reference or
    citation, a page overrun or an anonymity breach; PASS_WITH_NOTES on notes or skipped
    checks. Writes report.md, submission.json and the trail to out_dir/<timestamp>/
    (default <paper_dir>/drafts/submission_check).
    """
    paper = common.repo(paper_dir)
    out = common.run_folder(paper, out_dir, "submission_check")
    trail = Trail("submission_check", {"paper_dir": str(paper), "out_dir": out_dir}, {})
    findings: list[dict] = []
    main = common.main_tex(paper)
    if main is None:
        raise ValueError(f"{paper}: no main .tex (set main_tex in workflow/rules.json)")
    pdf, log = main.with_suffix(".pdf"), main.with_suffix(".log")
    tools = {"pypdf": _pypdf() is not None, "pdfinfo": bool(shutil.which("pdfinfo")),
             "pdffonts": bool(shutil.which("pdffonts"))}

    if log.is_file():
        findings += log_findings(log.read_text(encoding="utf-8", errors="ignore"), main.name)
    else:
        findings.append(_f("log", "note", log.name, f"没有 {log.name}，无法检查编译错误和未定义引用。",
                           f"在仓库根目录编译一次 {main.name}（如 latexmk -pdf {main.name}），保留 .log。"))
    trail.step("log", log=str(log), found=len(findings))

    pages = None
    if not pdf.is_file():
        findings.append(_f("pdf", "error", pdf.name, f"没有编译好的 {pdf.name}。",
                           f"编译 {main.name} 生成 {pdf.name} 后再检查。"))
    else:
        pages, page_findings = page_check(paper, pdf)
        findings += page_findings
        findings += font_findings(pdf)
        findings += size_findings(paper, pdf)
    trail.step("pdf", pdf=str(pdf), pages=pages, tools=tools)

    blind = common.double_blind(paper)
    if blind:
        findings += anonymity_findings(common.sources(paper, main), pdf if pdf.is_file() else None, main.name)
    trail.step("anonymity", double_blind=blind,
               breaches=sum(1 for f in findings if f["kind"] == "anonymity" and f["level"] == "error"))

    errors = [f for f in findings if f["level"] == "error"]
    status = "FAIL" if errors else "PASS_WITH_NOTES" if findings else "PASS"
    data = {"status": status, "main_tex": main.name, "pdf": pdf.name, "pages": pages,
            "page_limit": common.page_limit(paper), "double_blind": blind, "tools": tools, "findings": findings}
    (out / "submission.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "report.md").write_text(report(data), encoding="utf-8")
    run = trail.save(out, report=out / "report.md", findings=out / "submission.json")
    return json.dumps({"status": status, "report": str(out / "report.md"), "run": str(run),
                       "findings": str(out / "submission.json"), "errors": len(errors),
                       "notes": len(findings) - len(errors), "pages": pages, "page_limit": data["page_limit"],
                       "double_blind": blind, "tools": tools}, ensure_ascii=False)


def _f(kind: str, level: str, where: str, problem: str, fix: str) -> dict:
    return {"kind": kind, "level": level, "where": where, "problem": problem, "fix": fix}


# The .log ---------------------------------------------------------------------------

def unwrap(text: str) -> list[str]:
    """TeX breaks log lines at 79 characters; join them back."""
    lines, buf = [], ""
    for line in text.splitlines():
        buf += line
        if len(line) != 79:
            lines.append(buf)
            buf = ""
    if buf:
        lines.append(buf)
    return lines


FILE_LINE = re.compile(r"^(\.{0,2}/?[^\s:()]+\.(?:tex|sty|cls|bbl)):(\d+): (.+)$")
OPENED = re.compile(r"\(\./([^\s()]+\.(?:tex|bbl))")
UNDEFINED = re.compile(r"(?:LaTeX|Package natbib|Package biblatex) Warning: (Reference|Citation) [`']([^']+)' "
                       r"(?:on page \S+ )?undefined(?: on input line (\d+))?")
MULTIPLE = re.compile(r"LaTeX Warning: Label [`']([^']+)' multiply defined")
OVERFULL = re.compile(r"Overfull \\hbox \(([\d.]+)pt too wide\) (?:in paragraph |detected )?at lines? (\d+)")


def log_findings(text: str, main: str) -> list[dict]:
    lines, found, seen = unwrap(text), [], set()
    current = main

    def add(f):
        key = (f["kind"], f["where"], f["problem"])
        if key not in seen:
            seen.add(key)
            found.append(f)

    for i, line in enumerate(lines):
        for name in OPENED.findall(line):
            current = name.lstrip("./")
        m = FILE_LINE.match(line)
        if m:
            add(_f("latex_error", "error", f"{m.group(1).lstrip('./')}:{m.group(2)}", f"LaTeX 错误：{m.group(3)}",
                   _error_fix(m.group(3))))
            continue
        if line.startswith("! "):
            message = line[2:].strip()
            where = current
            for follow in lines[i + 1:i + 12]:
                ctx = re.match(r"^l\.(\d+)\s?(.*)$", follow)
                if ctx:
                    where = f"{current}:{ctx.group(1)}"
                    message += f"（附近：{ctx.group(2).strip()[:60]}）" if ctx.group(2).strip() else ""
                    break
            add(_f("latex_error", "error", where, f"LaTeX 错误：{message}", _error_fix(message)))
            continue
        m = UNDEFINED.search(line)
        if m:
            kind, key, at = m.groups()
            where = f"{current}:{at}" if at else current
            if kind == "Reference":
                add(_f("undefined_reference", "error", where, f"\\ref{{{key}}} 指向的标签 {key} 不存在。",
                       f"检查 \\label{{{key}}} 是否拼写一致、是否被注释掉；改好后再编译两次。"))
            else:
                add(_f("undefined_citation", "error", where, f"引用 {key} 在 .bib 里没有对应条目。",
                       f"在 .bib 中加入 {key} 条目或改正 \\cite 的键名，然后依次运行 bibtex/biber 和两次 LaTeX。"))
            continue
        m = MULTIPLE.search(line)
        if m:
            add(_f("duplicate_label", "note", current, f"标签 {m.group(1)} 定义了不止一次，引用可能指错位置。",
                   f"搜索 \\label{{{m.group(1)}}}，给重复的那个改名并更新对应的 \\ref。"))
            continue
        m = OVERFULL.search(line)
        if m and float(m.group(1)) > OVERFULL_PT:
            add(_f("overfull", "note", f"{current}:{m.group(2)}", f"一行超出版心 {float(m.group(1)):.1f}pt。",
                   "改写这句或加 \\allowbreak / 连字提示（如 data\\-set）；表格可用 \\resizebox 或缩小列宽。"))
    return found


def _error_fix(message: str) -> str:
    m = message.lower()
    if "undefined control sequence" in m:
        return "这个命令没有定义：检查拼写，或在导言区加载提供它的宏包。"
    if "file" in m and "not found" in m:
        return "找不到文件：确认文件已提交到仓库、路径和大小写正确。"
    if "missing $" in m:
        return "数学符号写在了正文模式：把公式放进 $...$，或转义 _、^ 等字符。"
    if "runaway argument" in m or "paragraph ended" in m or "missing } inserted" in m:
        return "括号没有配对：检查报错行附近的 { } 是否成对。"
    if "environment" in m and "undefined" in m:
        return "环境没有定义：检查 \\begin{...} 的拼写或加载对应宏包。"
    return "打开报错位置附近的源码，按错误信息改正后重新编译，直到 .log 里没有以 ! 开头的行。"


# The PDF ----------------------------------------------------------------------------

def _pypdf():
    try:
        import pypdf
        return pypdf
    except ImportError:
        return None


def count_pages(pdf: Path) -> tuple[int | None, str]:
    pypdf = _pypdf()
    if pypdf is not None:
        try:
            return len(pypdf.PdfReader(str(pdf)).pages), "pypdf"
        except Exception:  # noqa: S110  a broken PDF falls back to pdfinfo
            pass
    if shutil.which("pdfinfo"):
        done = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True)
        m = re.search(r"^Pages:\s+(\d+)", done.stdout, re.M)
        if m:
            return int(m.group(1)), "pdfinfo"
    return None, ""


def references_page(pdf: Path) -> int | None:
    """The 1-based page where a References / Bibliography heading starts, if pypdf reads one."""
    pypdf = _pypdf()
    if pypdf is None:
        return None
    try:
        for i, page in enumerate(pypdf.PdfReader(str(pdf)).pages, 1):
            if re.search(r"^\s*(?:\d+\s*)?(?:References|Bibliography|REFERENCES)\s*$", page.extract_text() or "", re.M):
                return i
    except Exception:
        return None
    return None


def page_check(paper: Path, pdf: Path) -> tuple[int | None, list[dict]]:
    pages, tool = count_pages(pdf)
    if pages is None:
        return None, [_f("pages", "note", pdf.name, "pypdf 和 pdfinfo 都不可用，没有数页数。",
                         "安装 pypdf（pip install pypdf）或 poppler（brew install poppler）后重跑。")]
    limit = common.page_limit(paper)
    if limit is None:
        return pages, [_f("pages", "note", "workflow/venue.md", f"共 {pages} 页；venue.md 里没有写页数上限，没有比较。",
                          "在 workflow/venue.md 的 Venue 一节写明页数上限，如 “page limit: 8 pages”。")]
    if pages <= limit:
        return pages, []
    refs = references_page(pdf)
    caveat = "工具无法可靠区分正文和参考文献页，以下按总页数与参考文献标题所在页估计。"
    if refs is not None and refs <= limit:
        return pages, [_f("pages", "note", pdf.name,
                          f"总页数 {pages} 超过上限 {limit}，但参考文献从第 {refs} 页开始，正文估计在上限内。{caveat}",
                          f"打开 PDF 确认第 {limit} 页之后只有参考文献或附录，且会议不把它们计入页数。")]
    body = f"参考文献从第 {refs} 页开始，正文约 {refs} 页" if refs else "没有找到参考文献标题"
    return pages, [_f("pages", "error", pdf.name, f"共 {pages} 页，超过上限 {limit} 页；{body}。{caveat}",
                      f"把正文压到 {limit} 页以内：精简文字、缩小或合并图表、把次要内容移到附录。")]


def font_findings(pdf: Path) -> list[dict]:
    if not shutil.which("pdffonts"):
        return [_f("fonts", "note", pdf.name, "pdffonts 不可用，没有检查字体是否嵌入。",
                   "安装 poppler（brew install poppler）后重跑。")]
    done = subprocess.run(["pdffonts", str(pdf)], capture_output=True, text=True)
    rows = done.stdout.splitlines()
    start = next((i + 1 for i, r in enumerate(rows) if r.startswith("----")), len(rows))
    bad = []
    for row in rows[start:]:
        tokens = row.split()
        if len(tokens) >= 6 and tokens[-5] == "no":
            bad.append(tokens[0])
    if not bad:
        return []
    return [_f("fonts", "note", pdf.name, f"{len(bad)} 个字体没有嵌入：{', '.join(sorted(set(bad))[:8])}。",
               "大多数会议要求嵌入全部字体：用 pdflatex 编译，图用嵌入字体导出（matplotlib 设 pdf.fonttype=42），"
               "或用 gs -dPDFSETTINGS=/prepress -dEmbedAllFonts=true 重新生成 PDF。")]


def size_findings(paper: Path, pdf: Path) -> list[dict]:
    found = []
    if pdf.stat().st_size > PDF_LIMIT:
        found.append(_f("size", "note", pdf.name, f"PDF 有 {common.mb(pdf.stat().st_size)}，超过常见的 50 MB 上限。",
                        "压缩图片（位图降到 300 dpi、矢量图简化），或用 gs -dPDFSETTINGS=/printer 重新生成。"))
    for zip_path in sorted(paper.glob("*.zip")):
        if zip_path.stat().st_size > SUPPLEMENT_LIMIT:
            found.append(_f("size", "note", zip_path.name,
                            f"补充材料 {zip_path.name} 有 {common.mb(zip_path.stat().st_size)}，超过常见的 100 MB 上限。",
                            "去掉检查点和原始数据，只留复现所需的代码和小样例，大文件改为匿名链接。"))
    return found


def anonymity_findings(source: str, pdf: Path | None, main: str) -> list[dict]:
    found = []
    anonymous_mode = any(p.search(source) for p in ANONYMOUS_MODE)
    authors = re.search(r"\\author\s*(?:\[[^\]]*\])?\s*\{((?:[^{}]|\{[^{}]*\})*)\}", source)
    if authors and not anonymous_mode:
        names = re.sub(r"\\[A-Za-z]+|[{}\\$^_]", " ", authors.group(1))
        if re.search(r"[A-Za-z]{2,}", names) and not re.search(r"anonymous", names, re.I):
            found.append(_f("anonymity", "error", main, "\\author 里写了作者信息，样式没有处于匿名模式。",
                            "换成匿名模式（如去掉 neurips 的 final 选项、acl 加 review 选项），或把 \\author 改成 Anonymous。"))
    if re.search(r"\\section\*?\s*\{\s*Acknowledge?ments?", source, re.I):
        found.append(_f("anonymity", "error", main, "有致谢节，可能暴露作者或基金信息。",
                        "投稿版删掉致谢节，或用 \\iffalse ... \\fi 包住，录用后再放回。"))
    for url in sorted(set(REPO_URL.findall(source))):
        found.append(_f("anonymity", "error", main, f"正文链接 {url} 可能暴露作者身份。",
                        "改用 anonymous.4open.science 等匿名仓库链接，或写“代码将在录用后公开”。"))
    for url in sorted(set(HOMEPAGE.findall(source))):
        found.append(_f("anonymity", "error", main, f"正文链接 {url} 像是个人主页。", "投稿版删掉这个链接。"))
    for phrase in sorted({m.group(0) for m in SELF_CITE.finditer(source)}):
        found.append(_f("anonymity", "error", main, f"“{phrase}”以第一人称指向自己的已有工作。",
                        "改成第三人称引用，如 “Smith et al. [x] showed ...”。"))
    pypdf = _pypdf()
    if pdf is not None and pypdf is not None:
        try:
            meta = pypdf.PdfReader(str(pdf)).metadata or {}
            author = str(meta.get("/Author") or "").strip()
        except Exception:
            author = ""
        if author:
            found.append(_f("anonymity", "error", pdf.name, f"PDF 元数据的 Author 字段是“{author}”。",
                            "在导言区加 \\hypersetup{pdfauthor={}}，或删掉设置 pdfauthor 的地方，重新编译。"))
    elif pdf is not None:
        found.append(_f("anonymity", "note", pdf.name, "pypdf 不可用，没有检查 PDF 元数据里的作者。",
                        "安装 pypdf 后重跑，或用 pdfinfo 看 Author 一栏是否为空。"))
    return found


def report(data: dict) -> str:
    title = {"PASS": "通过", "PASS_WITH_NOTES": "通过，有提示", "FAIL": "不通过"}[data["status"]]
    tools = "、".join(f"{k}{'可用' if v else '不可用'}" for k, v in data["tools"].items())
    lines = [f"# 投稿前检查：{title}", "",
             f"主文件 `{data['main_tex']}`，PDF `{data['pdf']}`，"
             f"页数 {data['pages'] if data['pages'] is not None else '未知'}，"
             f"上限 {data['page_limit'] if data['page_limit'] is not None else '未写'}，"
             f"{'双盲' if data['double_blind'] else '非双盲（未检查匿名）'}。工具：{tools}。", ""]
    for level, name in (("error", "必须修改"), ("note", "提示")):
        rows = [f for f in data["findings"] if f["level"] == level]
        if rows:
            lines += [f"## {name}（{len(rows)}）", "", "| 类型 | 位置 | 问题 | 修法 |", "|---|---|---|---|"]
            lines += [f"| {f['kind']} | `{f['where']}` | {_cell(f['problem'])} | {_cell(f['fix'])} |" for f in rows]
            lines.append("")
    if not data["findings"]:
        lines.append("没有发现问题。")
    return "\n".join(lines) + "\n"


def _cell(text: str) -> str:
    return " ".join(text.split()).replace("|", "\\|")


__all__ = ["log_findings", "submission_check"]
