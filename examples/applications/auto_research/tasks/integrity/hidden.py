"""Hidden text in the paper: what a human reader cannot see but a model reading the PDF
or the source does. Since ICML 2025 a hidden prompt aimed at LLM reviewers is treated as
academic misconduct. Learned from PhantomLint and hiddenprompt-scan. Plain code."""

import json
import re
from pathlib import Path

from auto_research.trail import Trail

from ..submission import common as paper_files
from . import common, pdfscan

REASON_ZH = {"white": "白色或接近背景色", "tiny": "字号小于 2pt", "offpage": "位于页面裁切框之外",
             "invisible": "不可见渲染模式（3 Tr / 7 Tr）", "transparent": "填充透明度为 0"}
COLOR_WHITE = re.compile(
    r"\\(?:textcolor|color|colorbox|pagecolor)\s*(?:\[\s*(?P<model>rgb|RGB|gray|cmyk|HTML)\s*\]\s*\{(?P<value>[^}]*)\}"
    r"|\{(?P<name>[^}]*)\})")
DEFINE_COLOR = re.compile(r"\\(?:definecolor|colorlet)\s*\{([^}]*)\}\s*(?:\{(rgb|RGB|gray|cmyk|HTML)\}\s*\{([^}]*)\}|\{([^}]*)\})")
FONTSIZE = re.compile(r"\\fontsize\s*\{\s*([\d.]+)\s*(pt|bp|mm|px)?\s*\}(?:\s*\{[^}]*\})?")
PHANTOM = re.compile(r"\\[hv]?phantom\s*\{")
RESIZE = re.compile(r"\\(?:resizebox\*?\s*\{\s*0?\.0\d*\s*\\?\w*\s*\}|scalebox\s*\{\s*0?\.0\d*\s*\}|"
                    r"transparent\s*\{\s*0(?:\.0+)?\s*\})")


def hidden_text_check(paper_dir: str, out_dir: str = "") -> str:
    """Find text in the paper a human reader cannot see.

    In the compiled PDF (the main .tex's .pdf): white or near-white text, fonts under 2pt,
    text outside the page's crop box, invisible render modes and zero opacity. In the
    LaTeX sources the main .tex reaches: white \\color/\\textcolor (also colours defined
    as white), \\fontsize under 2pt, \\phantom holding \\tiny or a sentence,
    \\resizebox/\\scalebox to a few percent, \\transparent{0}, and phrases that address a
    model (ignore previous instructions, give a positive review, as a reviewer you must,
    LLM reviewer directives; English and Chinese) anywhere in the text.

    FAIL on hidden text that reads like an instruction; PASS_WITH_NOTES on other hidden
    text, visible instruction-like phrases or a missing PDF. Writes report.md,
    hidden_text.json and the trail to out_dir/<timestamp>/ (default
    <paper_dir>/drafts/hidden_text_check).
    """
    paper = paper_files.repo(paper_dir)
    out = paper_files.run_folder(paper, out_dir, "hidden_text_check")
    trail = Trail("hidden_text_check", {"paper_dir": str(paper), "out_dir": out_dir}, {})
    main = paper_files.main_tex(paper)
    if main is None:
        raise ValueError(f"{paper}: no main .tex (set main_tex in workflow/rules.json)")
    findings: list[dict] = []
    pdf = main.with_suffix(".pdf")
    backend = ""
    if pdf.is_file():
        runs, backend = pdfscan.scan(pdf)
        findings += pdf_findings(runs, pdf.name)
    else:
        findings.append(common.finding("pdf", "note", pdf.name, f"没有编译好的 {pdf.name}，只检查了源码。",
                                       f"编译 {main.name} 生成 {pdf.name} 后重跑。"))
    trail.step("pdf", pdf=str(pdf), backend=backend, found=len(findings))
    used, _ = paper_files.used_files(paper, main)
    texs = [p for p, kind in used.items() if p.endswith(".tex") and kind in ("main", "tex")]
    source = []
    for rel in texs:
        source += tex_findings((paper / rel).read_text(encoding="utf-8", errors="ignore"), rel)
    findings += source
    trail.step("source", files=texs, found=len(source))

    status = common.status(findings)
    data = {"status": status, "main_tex": main.name, "pdf": pdf.name if pdf.is_file() else "",
            "backend": backend, "sources": texs, "findings": findings}
    common.write_json(out / "hidden_text.json", data)
    summary = (f"主文件 `{main.name}`；PDF {'`' + pdf.name + '`，后端 ' + backend if backend else '缺失'}；"
               f"检查了 {len(texs)} 个 .tex 文件。隐藏且像指令的文字会被视为对审稿模型的提示注入（ICML 等会议按学术不端处理）。")
    (out / "report.md").write_text(common.report("隐藏文字检查", summary, findings), encoding="utf-8")
    run = trail.save(out, report=out / "report.md", findings=out / "hidden_text.json")
    errors = sum(1 for f in findings if f["level"] == "error")
    return json.dumps({"status": status, "report": str(out / "report.md"), "run": str(run),
                       "findings": str(out / "hidden_text.json"), "errors": errors,
                       "notes": len(findings) - errors, "backend": backend}, ensure_ascii=False)


def pdf_findings(runs: list[dict], pdf_name: str) -> list[dict]:
    out = []
    for r in runs:
        why = "、".join(REASON_ZH.get(x, x) for x in r["reasons"])
        phrase = common.instruction_like(r["text"])
        where = f"{pdf_name} 第 {r['page']} 页" if r["page"] else f"{pdf_name}（页码未知）"
        if phrase:
            out.append(common.finding(
                "hidden_instruction", "error", where, f"读者看不见的文字（{why}）里有对模型的指令：“{phrase}”。",
                "删除这段文字。隐藏的审稿指令属于提示注入，会议按学术不端处理。", r["text"],
                reasons=r["reasons"], page=r["page"], x=r.get("x"), y=r.get("y"), size=r.get("size")))
        else:
            out.append(common.finding(
                "hidden_text", "note", where, f"读者看不见的文字（{why}）。",
                "确认这是排版需要（如对齐占位、彩色底上的白字）；不需要就删掉或改成可见。", r["text"],
                reasons=r["reasons"], page=r["page"], x=r.get("x"), y=r.get("y"), size=r.get("size")))
    return out


# LaTeX sources ------------------------------------------------------------------------

def white_value(model: str, value: str) -> bool:
    try:
        parts = [float(x) for x in re.split(r"[,\s]+", value.strip()) if x]
    except ValueError:
        return value.strip().upper() in ("FFFFFF", "FFF") if model == "HTML" else False
    if model == "HTML":
        return False
    if model == "RGB":
        parts = [p / 255 for p in parts]
    rgb = pdfscan.to_rgb(parts)
    return rgb is not None and pdfscan.white_distance(rgb) <= pdfscan.WHITE_DISTANCE


def white_names(text: str) -> set[str]:
    names = {"white"}
    for name, model, value, alias in DEFINE_COLOR.findall(text):
        if (model and white_value(model, value)) or (alias and alias.split("!")[0].strip() in names
                                                     and "!" not in alias):
            names.add(name.strip())
    return names


def group_after(text: str, start: int, limit: int = 400) -> str:
    """The text from start to the end of the enclosing group, a blank line or limit chars."""
    depth, i = 0, start
    while i < len(text) and i - start < limit:
        ch = text[i]
        if ch == "{" and text[i - 1:i] != "\\":
            depth += 1
        elif ch == "}" and text[i - 1:i] != "\\":
            depth -= 1
            if depth < 0:
                break
        elif text.startswith("\n\n", i) or text.startswith("\\par", i):
            break
        i += 1
    return text[start:i]


def balanced(text: str, open_at: int) -> str:
    """The argument of a {...} group starting at open_at (the brace)."""
    depth = 0
    for i in range(open_at, min(len(text), open_at + 2000)):
        if text[i] == "{" and text[i - 1:i] != "\\":
            depth += 1
        elif text[i] == "}" and text[i - 1:i] != "\\":
            depth -= 1
            if not depth:
                return text[open_at + 1:i]
    return text[open_at + 1:open_at + 400]


def plain(tex: str) -> str:
    """Rough reading text of a LaTeX fragment."""
    tex = re.sub(r"\\(?:textcolor|color)\s*(?:\[[^\]]*\])?\s*\{[^}]*\}", " ", tex)
    tex = re.sub(r"\\[a-zA-Z@]+\*?", " ", tex)
    return " ".join(re.sub(r"[{}$~]", " ", tex).split())


def tex_findings(text: str, rel: str) -> list[dict]:
    """Hidden constructs and instruction-like phrases in one .tex file, with file:line.
    Comments are not typeset: an instruction-like phrase in a comment is a note."""
    lines = text.splitlines()
    code_lines, comment_lines = [], []
    for line in lines:
        cut = paper_files._comment_at(line)
        code_lines.append(line if cut < 0 else line[:cut])
        comment_lines.append("" if cut < 0 else line[cut + 1:])
    code = "\n".join(code_lines)
    starts = [0]
    for line in code_lines:
        starts.append(starts[-1] + len(line) + 1)

    def line_of(pos: int) -> int:
        lo, hi = 0, len(starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if starts[mid] <= pos:
                lo = mid
            else:
                hi = mid - 1
        return lo + 1

    found: list[dict] = []
    hidden_spans: list[tuple[int, int]] = []

    def add(pos: int, end: int, kind: str, construct: str, fragment: str):
        words = plain(fragment)
        if not words:
            return
        hidden_spans.append((pos, end))
        phrase = common.instruction_like(words)
        where = f"{rel}:{line_of(pos)}"
        if phrase:
            found.append(common.finding("hidden_instruction", "error", where,
                                        f"{construct} 让这段文字读者看不见，里面有对模型的指令：“{phrase}”。",
                                        "删除这段文字。隐藏的审稿指令属于提示注入，会议按学术不端处理。", words))
        else:
            found.append(common.finding(kind, "note", where, f"{construct} 让这段文字读者看不见。",
                                        "确认这是排版需要；不需要就删掉或改成可见。", words))

    names = white_names(code)
    for m in COLOR_WHITE.finditer(code):
        name = (m.group("name") or "").strip()
        white = (name in names or re.fullmatch(r"white(?:!(?:9\d|100))?", name or "-")) if name else \
            white_value(m.group("model"), m.group("value") or "")
        if not white:
            continue
        cmd = m.group(0).split("{")[0].split("[")[0]
        if cmd.startswith("\\pagecolor"):
            continue
        rest = code[m.end():]
        if cmd.startswith("\\textcolor") or cmd.startswith("\\colorbox"):
            brace = len(rest) - len(rest.lstrip())
            fragment = balanced(code, m.end() + brace) if rest.lstrip().startswith("{") else ""
        else:
            fragment = group_after(code, m.end())
        if cmd.startswith("\\colorbox"):
            continue  # a white box behind text is not hidden text
        add(m.start(), m.end() + len(fragment), "hidden_color", f"白色文字（{cmd}）", fragment)
    for m in FONTSIZE.finditer(code):
        size = float(m.group(1)) * {"mm": 2.845, "px": 0.75}.get(m.group(2) or "pt", 1.0)
        if size < pdfscan.TINY_PT:
            fragment = group_after(code, m.end())
            add(m.start(), m.end() + len(fragment), "hidden_tiny", f"\\fontsize{{{m.group(1)}}}（小于 2pt）", fragment)
    for m in PHANTOM.finditer(code):
        arg = balanced(code, m.end() - 1)
        if "\\tiny" in arg or len(plain(arg).split()) >= 5 or common.instruction_like(plain(arg)):
            add(m.start(), m.end() + len(arg), "hidden_phantom", "\\phantom（占位不显示）", arg)
    for m in RESIZE.finditer(code):
        rest = code[m.end():]
        brace = len(rest) - len(rest.lstrip())
        fragment = balanced(code, m.end() + brace) if rest.lstrip().startswith("{") else group_after(code, m.end())
        add(m.start(), m.end() + len(fragment), "hidden_scaled", f"{m.group(0).split('{')[0]}（缩到几乎看不见）",
            fragment)

    # instruction-like phrases outside the hidden constructs: visible, or in comments
    for m in common.INSTRUCTION.finditer(code):
        if any(a <= m.start() < b for a, b in hidden_spans):
            continue
        found.append(common.finding("instruction_phrase", "note", f"{rel}:{line_of(m.start())}",
                                    f"正文里有像对模型下指令的话：“{m.group(0)}”（读者看得见）。",
                                    "如果是在讨论提示注入，保留即可；否则删掉。",
                                    code_lines[line_of(m.start()) - 1]))
    for i, comment in enumerate(comment_lines, 1):
        m = common.INSTRUCTION.search(comment)
        if m:
            found.append(common.finding("comment_instruction", "note", f"{rel}:{i}",
                                        f"注释里有像对模型下指令的话：“{m.group(0)}”。PDF 里看不到，但源码包（arXiv）里会带上。",
                                        "删掉这条注释；用 arxiv_package 打包会去掉注释。", comment))
    return found


__all__ = ["hidden_text_check"]
