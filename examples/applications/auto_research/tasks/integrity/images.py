"""Figure reuse: the paper's figures compared by perceptual hash against the author's
earlier papers and against each other (duplicated panels). Learned from Proofig and
ImageTwin. Plain code; Pillow decodes images, poppler extracts them from PDFs."""

import json
import re
import shutil
import subprocess
from pathlib import Path

from auto_research.trail import Trail

from ..submission import common as paper_files
from . import common

MAX_DISTANCE = 6        # of 64 bits, for both hashes
FLAT = 2.0              # an 8x8 thumbnail whose grey levels vary less than this says nothing
RASTER = (".png", ".jpg", ".jpeg")
CREDIT = re.compile(r"(?:reproduced|adapted|reprinted|taken|redrawn|modified)\s+(?:with\s+permission\s+)?from"
                    r"|\bfrom[~\s]+\\cite[a-zA-Z]*\*?\s*(?:\[[^\]]*\]\s*)*\{|\bsource:\s"
                    r"|引自|改编自|转载自|摘自|来源[:：]", re.I)
FIGURE_ENV = re.compile(r"\\begin\{(figure\*?|wrapfigure|subfigure|SCfigure)\}(.*?)\\end\{\1\}", re.S)
GRAPHIC = re.compile(r"\\includegraphics\*?\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}")


def image_reuse(paper_dir: str, own_papers: str = "", out_dir: str = "") -> str:
    """Compare the paper's figures with the author's earlier papers and with each other.

    The figures are the PNG/JPG/PDF files the main .tex includes (PDF figures rasterized
    with pdftoppm). own_papers: comma-separated folders of images or PDFs (embedded images
    extracted with pdfimages), relative to paper_dir or absolute; workflow/own_papers/ is
    always included. Each image gets an average hash and a difference hash (64 bits); a
    pair matches when both differ in at most 6 bits. A match is credited when the
    figure's caption says reproduced/adapted from or cites a source (also 引自/改编自).

    FAIL on an uncredited match with an earlier paper; PASS_WITH_NOTES on duplicated
    figures inside the paper, credited reuse or images that could not be read; Pillow
    missing makes the check unsupported (PASS_WITH_NOTES, supported false). Writes
    report.md, image_reuse.json and the trail to out_dir/<timestamp>/ (default
    <paper_dir>/drafts/image_reuse).
    """
    paper = paper_files.repo(paper_dir)
    main = paper_files.main_tex(paper)
    if main is None:
        raise ValueError(f"{paper}: no main .tex (set main_tex in workflow/rules.json)")
    roots = own_roots(paper, own_papers)
    out = paper_files.run_folder(paper, out_dir, "image_reuse")
    trail = Trail("image_reuse", {"paper_dir": str(paper), "own_papers": own_papers, "out_dir": out_dir}, {})
    tools = {"pillow": _pil() is not None, "pdftoppm": bool(shutil.which("pdftoppm")),
             "pdfimages": bool(shutil.which("pdfimages"))}
    findings: list[dict] = []
    figures = paper_figures(paper, main)
    captions = figure_captions(paper, main)
    if not tools["pillow"]:
        findings.append(common.finding("unsupported", "note", "Pillow",
                                       "没有安装 Pillow，无法计算图片哈希，没有比较。",
                                       "安装 Pillow（pip install pillow）后重跑。"))
        return finish(out, trail, paper, main, findings, tools, [], [], [], supported=False)

    work = out / "images"
    mine, skipped = [], []
    for rel in figures:
        for path, label in expand(paper / rel, work / "paper", tools):
            mine.append({"path": path, "label": label or rel, "figure": rel})
        if not any(m["figure"] == rel for m in mine):
            skipped.append(rel)
    theirs = []
    for root in roots:
        for f in ([root] if root.is_file() else sorted(p for p in root.rglob("*") if p.is_file())):
            if f.suffix.lower() in RASTER + (".pdf",):
                for path, label in expand(f, work / "own", tools, embedded=True):
                    theirs.append({"path": path, "label": label or str(f)})
    for entry in mine + theirs:
        entry["hash"] = hashes(entry["path"])
    unreadable = [e["label"] for e in mine + theirs if e["hash"] is None]
    trail.step("hash", figures=len(mine), own=len(theirs), unreadable=len(unreadable), skipped=skipped)

    matches = []
    for a in (m for m in mine if m["hash"]):
        for b in (t for t in theirs if t["hash"]):
            d = distance(a["hash"], b["hash"])
            if d:
                matches.append(match("own_paper", a, b, d, captions.get(a["figure"], {})))
    hashed = [m for m in mine if m["hash"]]
    for i, a in enumerate(hashed):
        for b in hashed[i + 1:]:
            if a["figure"] == b["figure"] and a["path"] == b["path"]:
                continue
            d = distance(a["hash"], b["hash"])
            if d:
                matches.append(match("inside_paper", a, b, d, captions.get(a["figure"], {})))
    for m in matches:
        where = f"{m['figure']} ↔ {m['other']}"
        if m["scope"] == "own_paper" and not m["credited"]:
            findings.append(common.finding("reused_figure", "error", where,
                                           f"图与旧论文里的图几乎相同（aHash 差 {m['ahash']} 位，dHash 差 {m['dhash']} 位），图注没有注明来源。",
                                           "在图注里写明 “Reproduced from \\cite{…}” 或 “Adapted from …”，或换成新图。",
                                           m["caption"], **m))
        elif m["scope"] == "own_paper":
            findings.append(common.finding("credited_reuse", "note", where, "图与旧论文里的图几乎相同，图注已注明来源。",
                                           "确认版权方允许转载。", m["caption"], **m))
        else:
            findings.append(common.finding("duplicate_figure", "note", where,
                                           f"论文里两张图几乎相同（aHash 差 {m['ahash']} 位，dHash 差 {m['dhash']} 位）。",
                                           "确认不是同一张图用于两个不同的实验或条件；有意重复就在图注里说明。", m["caption"], **m))
    for label in unreadable:
        findings.append(common.finding("unreadable", "note", label, "这张图打不开，没有比较。", "检查文件是否损坏。"))
    for rel in skipped:
        findings.append(common.finding("skipped", "note", rel, "这种格式没有比较（EPS/SVG，或没有 pdftoppm 时的 PDF 图）。",
                                       "导出一份 PNG 或安装 poppler 后重跑。"))
    return finish(out, trail, paper, main, findings, tools, mine, theirs, matches, supported=True)


def finish(out, trail, paper, main, findings, tools, mine, theirs, matches, supported) -> str:
    status = common.status(findings)
    data = {"status": status, "supported": supported, "main_tex": main.name, "tools": tools,
            "figures": [{"label": m["label"], "figure": m["figure"], "hash": m.get("hash")} for m in mine],
            "own_images": len(theirs), "matches": matches, "findings": findings}
    common.write_json(out / "image_reuse.json", data)
    lead = (f"主文件 `{main.name}`，比较了论文的 {len(mine)} 张图和旧论文的 {len(theirs)} 张图；"
            f"aHash 与 dHash 都相差不超过 {MAX_DISTANCE}/64 位算相同。"
            + ("" if supported else "没有 Pillow，本次没有比较。"))
    (out / "report.md").write_text(common.report("图片复用检查", lead, findings), encoding="utf-8")
    run = trail.save(out, report=out / "report.md", findings=out / "image_reuse.json")
    errors = sum(1 for f in findings if f["level"] == "error")
    return json.dumps({"status": status, "supported": supported, "report": str(out / "report.md"), "run": str(run),
                       "findings": str(out / "image_reuse.json"), "errors": errors, "notes": len(findings) - errors,
                       "figures": len(mine), "own_images": len(theirs), "matches": len(matches), "tools": tools},
                      ensure_ascii=False)


def match(scope: str, a: dict, b: dict, d: tuple[int, int], caption: dict) -> dict:
    text = caption.get("caption", "")
    return {"scope": scope, "figure": a["label"], "other": b["label"], "ahash": d[0], "dhash": d[1],
            "caption": common.cut(text), "where_tex": caption.get("where", ""), "credited": bool(CREDIT.search(text))}


# Files --------------------------------------------------------------------------------

def own_roots(paper: Path, own_papers: str) -> list[Path]:
    roots = []
    for part in (p.strip() for p in (own_papers or "").split(",")):
        if not part:
            continue
        path = Path(part).expanduser()
        path = path if path.is_absolute() else paper / path
        if not path.exists():
            raise ValueError(f"own paper {part} does not exist")
        roots.append(path)
    folder = paper / "workflow" / "own_papers"
    if folder.is_dir():
        roots.append(folder)
    return roots


def paper_figures(paper: Path, main: Path) -> list[str]:
    used, _ = paper_files.used_files(paper, main)
    return [p for p, kind in used.items() if kind == "figure"]


def expand(path: Path, work: Path, tools: dict, embedded: bool = False) -> list[tuple[Path, str]]:
    """Image files to hash for one file: itself, or the PDF's rendering (a figure) or
    its embedded images (an earlier paper)."""
    suffix = path.suffix.lower()
    if suffix in RASTER:
        return [(path, "")]
    if suffix != ".pdf":
        return []
    target = work / re.sub(r"[^\w.-]+", "_", path.stem)
    target.mkdir(parents=True, exist_ok=True)
    if embedded:
        if not tools["pdfimages"]:
            return []
        subprocess.run(["pdfimages", "-png", str(path), str(target / "img")], capture_output=True)
        return [(p, f"{path.name}#{p.stem}") for p in sorted(target.glob("img*.png"))]
    if not tools["pdftoppm"]:
        return []
    subprocess.run(["pdftoppm", "-png", "-r", "72", "-singlefile", str(path), str(target / "page")], capture_output=True)
    page = target / "page.png"
    return [(page, "")] if page.is_file() else []


def figure_captions(paper: Path, main: Path) -> dict[str, dict]:
    """{figure file: {caption, where}} for figures inside a figure environment."""
    used, _ = paper_files.used_files(paper, main)
    figures = [p for p, kind in used.items() if kind == "figure"]
    out: dict[str, dict] = {}
    for rel, kind in used.items():
        if not rel.endswith(".tex") or kind not in ("main", "tex"):
            continue
        text = paper_files.strip_comments((paper / rel).read_text(encoding="utf-8", errors="ignore"))
        for env in FIGURE_ENV.finditer(text):
            body = env.group(2)
            caption = " ".join(captions_in(body))
            line = text.count("\n", 0, env.start()) + 1
            for name in GRAPHIC.findall(body):
                for fig in figures:
                    if same_graphic(fig, name.strip()):
                        out.setdefault(fig, {"caption": caption, "where": f"{rel}:{line}"})
    return out


def captions_in(body: str) -> list[str]:
    out = []
    for m in re.finditer(r"\\(?:sub)?caption\*?\s*(?:\[[^\]]*\])?\s*\{", body):
        depth, i = 0, m.end() - 1
        for i in range(m.end() - 1, len(body)):
            if body[i] == "{":
                depth += 1
            elif body[i] == "}":
                depth -= 1
                if not depth:
                    break
        out.append(body[m.end():i])
    return out


def same_graphic(rel: str, name: str) -> bool:
    stem = rel.rsplit(".", 1)[0] if Path(rel).suffix.lower() in paper_files.GRAPHIC_EXTS else rel
    name = name[:-len(Path(name).suffix)] if Path(name).suffix.lower() in paper_files.GRAPHIC_EXTS else name
    return stem == name or stem.endswith("/" + name.lstrip("./"))


# Hashes -------------------------------------------------------------------------------

def _pil():
    try:
        from PIL import Image
        return Image
    except ImportError:
        return None


def grids(path: Path) -> tuple[list[list[float]], list[list[float]]] | None:
    """Grey 8x8 and 9x8 thumbnails of an image, or None when it cannot be read."""
    Image = _pil()
    try:
        with Image.open(path) as im:
            im = im.convert("RGBA")
            flat = Image.new("RGBA", im.size, (255, 255, 255, 255))
            flat.alpha_composite(im)
            grey = flat.convert("L")
            resample = getattr(Image, "Resampling", Image).LANCZOS
            small = list(grey.resize((8, 8), resample).tobytes())
            wide = list(grey.resize((9, 8), resample).tobytes())
    except Exception:
        return None
    return [small[r * 8:(r + 1) * 8] for r in range(8)], [wide[r * 9:(r + 1) * 9] for r in range(8)]


def ahash(grid: list[list[float]]) -> int:
    """Average hash: bit set where the 8x8 grey pixel is above the mean."""
    cells = [v for row in grid for v in row]
    mean = sum(cells) / len(cells)
    bits = 0
    for v in cells:
        bits = (bits << 1) | (v > mean)
    return bits


def dhash(grid: list[list[float]]) -> int:
    """Difference hash: bit set where a 9x8 grey pixel is brighter than its right neighbour."""
    bits = 0
    for row in grid:
        for a, b in zip(row, row[1:]):
            bits = (bits << 1) | (a > b)
    return bits


def flat(grid: list[list[float]]) -> bool:
    cells = [v for row in grid for v in row]
    mean = sum(cells) / len(cells)
    return (sum((v - mean) ** 2 for v in cells) / len(cells)) ** 0.5 < FLAT


def hashes(path: Path) -> dict | None:
    g = grids(path)
    if g is None:
        return None
    small, wide = g
    return {"ahash": f"{ahash(small):016x}", "dhash": f"{dhash(wide):016x}", "flat": flat(small)}


def hamming(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def distance(a: dict, b: dict) -> tuple[int, int] | None:
    """(aHash, dHash) distances when the two images match, else None. Flat images never match."""
    if a.get("flat") or b.get("flat"):
        return None
    da, dd = hamming(a["ahash"], b["ahash"]), hamming(a["dhash"], b["dhash"])
    return (da, dd) if da <= MAX_DISTANCE and dd <= MAX_DISTANCE else None


__all__ = ["image_reuse"]
