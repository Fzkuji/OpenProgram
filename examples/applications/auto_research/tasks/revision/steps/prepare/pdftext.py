"""Read a compiled PDF with Poppler: page text, line boxes, fonts."""

import re
import subprocess
from pathlib import Path

LINE = re.compile(r'<line xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</line>', re.S)
WORD = re.compile(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>')
PAGE = re.compile(r'<page width="([\d.]+)" height="([\d.]+)">(.*?)</page>', re.S)


def run(args: list[str]) -> str:
    result = subprocess.run(args, capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise RuntimeError(f"{args[0]} failed: {result.stderr.strip()}")
    return result.stdout


def page_texts(pdf: Path) -> list[str]:
    return run(["pdftotext", "-layout", str(pdf), "-"]).split("\f")


def bbox(pdf: Path, first: int = 0, last: int = 0) -> list[dict]:
    """Pages with their lines and words, sorted top to bottom."""
    args = ["pdftotext", "-bbox-layout"]
    if first:
        args += ["-f", str(first), "-l", str(last or first)]
    pages = []
    for width, height, content in PAGE.findall(run(args + [str(pdf), "-"])):
        lines = []
        for x0, y0, x1, y1, inner in LINE.findall(content):
            words = [(float(a), float(b), float(c), float(d), w) for a, b, c, d, w in WORD.findall(inner)]
            lines.append({"x0": float(x0), "y0": float(y0), "x1": float(x1), "y1": float(y1), "words": words})
        pages.append({"width": float(width), "height": float(height),
                      "lines": sorted(lines, key=lambda l: (l["y0"], l["x0"]))})
    return pages


def type3_fonts(pdf: Path) -> list[str]:
    return [line.split()[0] for line in run(["pdffonts", str(pdf)]).splitlines()[2:] if "Type 3" in line]


def word_heights(pages: list[dict]) -> list[float]:
    return [w[3] - w[1] for page in pages for line in page["lines"] for w in line["words"]]
