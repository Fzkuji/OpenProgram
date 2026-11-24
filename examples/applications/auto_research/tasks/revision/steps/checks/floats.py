"""Figures and tables: placement, order, captions, graphics, removals, booktabs, page, text size."""

import re

from ...rules import latex as latex_rules
from ...rules import venue, writing
from ..prepare.source import body, first_mentions, floats, main_body
from .base import finding


def placement(paper) -> list[dict]:
    """Floats in the main body sit at the top of the page. The appendix is free."""
    found = []
    for item in floats(main_body(paper.draft_flat)):
        option = (item["placement"] or "").replace(" ", "")
        if option not in latex_rules.TOP_PLACEMENTS:
            name = item["label"] or item["kind"]
            found.append(finding("FAIL", "placement", f"{name} uses [{option or 'default'}]; figures and tables go at the top ([t])."))
    return found


def order(paper) -> list[dict]:
    """Floats appear in the order the text first mentions them, and each is mentioned."""
    flat, found = paper.draft_flat, []
    alias = {other: f["label"] for f in floats(flat) for other in f["labels"]}
    mentioned = list(dict.fromkeys(alias.get(m, m) for m in first_mentions(flat)))
    for kind in ("figure", "table"):
        labels = [f["label"] for f in floats(flat) if f["kind"] == kind and f["label"]]
        for label in labels:
            if label not in mentioned:
                found.append(finding("FAIL", "mention", f"{label} is never referred to in the text."))
        expected = [m for m in mentioned if m in labels]
        placed = [label for label in labels if label in expected]
        if placed != expected:
            found.append(finding("FAIL", "order", f"{kind.title()} order {placed} differs from first mentions {expected}."))
    return found


def captions(paper) -> list[dict]:
    found = []
    for item in floats(paper.draft_flat):
        text = re.sub(r"^\([a-z]\)\s*", "", item["caption"])
        name = item["label"] or item["kind"]
        if text and not (text[0].isupper() or text[0] in "\\$"):
            found.append(finding("WARN", "caption", f"{name}: caption should start with a capital."))
        if text and not text.rstrip("} ").endswith("."):
            found.append(finding("WARN", "caption", f"{name}: caption should end with a period."))
    return found


def caption_content(paper) -> list[dict]:
    """Captions name what is shown: no numbers, model names or pointers to other parts."""
    found = []
    for item in floats(paper.draft_flat):
        text = re.sub(r"\\(?:ref|label|cite[pt]?)\{[^}]*\}|\\[a-zA-Z]+\*?", " ", item["caption"])
        text = re.sub(r"^\s*\([a-z]\)\s*", "", text)
        name = item["label"] or item["kind"]
        if re.search(r"\d", text):
            found.append(finding("WARN", "caption-numbers", f"{name}: caption carries numbers; state them in the text: {text[:80]}…"))
        models = [m for m in writing.CAPTION_MODEL_NAMES if re.search(r"\b" + re.escape(m) + r"\b", text)]
        if models:
            found.append(finding("WARN", "caption-model", f"{name}: caption names a model ({', '.join(models)}); name the role instead."))
        pointers = [p for p in writing.CAPTION_POINTERS if p.lower() in text.lower()]
        if pointers:
            found.append(finding("WARN", "caption-content", f"{name}: caption points elsewhere ({', '.join(pointers)}); captions stand alone."))
    return found


def caption_position(paper) -> list[dict]:
    """Table captions above the table, figure captions below the figure."""
    found = []
    for m in re.finditer(r"\\begin\{(table|figure)\*?\}(.*?)\\end\{\1\*?\}", body(paper.draft_flat), re.S):
        kind, block = m.group(1), m.group(2)
        label = (re.findall(r"\\label\{([^}]+)\}", block) or [kind])[0]
        cap = block.find("\\caption")
        content = re.search(r"\\begin\{tabular|\\includegraphics|\\input|\\begin\{tikzpicture|\\resizebox", block)
        if cap < 0 or not content:
            continue
        if kind == "table" and cap > content.start():
            found.append(finding("FAIL", "caption-position", f"{label}: table caption goes above the table"))
        if kind == "figure" and cap < content.start():
            found.append(finding("FAIL", "caption-position", f"{label}: figure caption goes below the figure"))
    return found


def graphics(paper) -> list[dict]:
    found = []
    for item in floats(paper.draft_flat):
        for name in item["graphics"]:
            path = paper.root / name
            if path.suffix.lower() in (".png", ".jpg", ".jpeg"):
                found.append(finding("FAIL", "vector", f"{name} is raster; use a PDF figure."))
            elif not path.suffix and not path.with_suffix(".pdf").exists():
                found.append(finding("FAIL", "vector", f"{name} has no PDF version."))
    return found


def removed(paper) -> list[dict]:
    """Original figures and tables stay unless the author approved removing them."""
    found = []
    draft = floats(paper.draft_flat)
    draft_labels = {f["label"] for f in draft}
    draft_graphics = {g for f in draft for g in f["graphics"]}
    for item in floats(paper.base_flat):
        gone = [g for g in item["graphics"] if g not in draft_graphics]
        name = item["label"] or ", ".join(item["graphics"]) or item["kind"]
        if name in paper.approved_removals:
            continue
        if item["label"] and item["label"] not in draft_labels:
            found.append(finding("FAIL", "removed", f"Original {item['kind']} {name} was removed."))
        elif gone:
            found.append(finding("FAIL", "removed", f"Original graphics removed from {name}: {', '.join(gone)}"))
    return found


def tables(paper) -> list[dict]:
    """Booktabs rules, no vertical lines, default font size."""
    found = []
    for m in re.finditer(r"\\begin\{table\*?\}.*?\\end\{table\*?\}", body(paper.draft_flat), re.S):
        block = m.group(0)
        label = (re.findall(r"\\label\{([^}]+)\}", block) or ["table"])[0]
        specs = re.findall(r"\\begin\{tabular\*?\}(?:\{[^}]*\})?\{([^}]*)\}", block)
        if any("|" in spec for spec in specs) or "\\hline" in block:
            found.append(finding("WARN", "booktabs", f"{label}: use booktabs rules without vertical lines"))
        if specs and "\\toprule" not in block:
            found.append(finding("WARN", "booktabs", f"{label}: use \\toprule, \\midrule and \\bottomrule"))
        for size in re.findall(r"\\(small|footnotesize|scriptsize|tiny)\b", block):
            found.append(finding("WARN", "table-font", f"{label}: \\{size} changes the table font size"))
    return found


def same_page(paper) -> list[dict]:
    """A figure or table sits on the page where the text first mentions it."""
    found = []
    for item in floats(paper.draft_flat):
        if not item["label"]:
            continue
        m = re.search(r"\\newlabel\{" + re.escape(item["label"]) + r"\}\{\{([^}]*)\}\{(\d+)\}", paper.aux)
        if not m:
            continue
        number, page = m.group(1), int(m.group(2))
        word = "Figure" if item["kind"] == "figure" else "Table"
        pattern = re.compile(word + r"s?\s+(?:[\d,\s]*and\s+)?" + re.escape(number) + r"(?![\d:])")
        mention = next((i + 1 for i, text in enumerate(paper.page_texts) if pattern.search(text)), None)
        if mention and mention != page:
            found.append(finding("WARN", "same-page",
                                 f"{word} {number} is on page {page}; the text first mentions it on page {mention}"))
    return found


def figure_fonts(paper) -> list[dict]:
    """Text inside figures, at its printed size, is not smaller than caption text."""
    if not paper.body_word_height:
        return []
    ratio = paper.body_word_height / venue.BODY_FONT_PT
    found = []
    for block in re.findall(r"\\begin\{figure\*?\}.*?\\end\{figure\*?\}", paper.draft_flat, re.S):
        for m in re.finditer(r"\\includegraphics(?:\[([^\]]*)\])?\{([^}]+)\}", block):
            figure = paper.figures.get(m.group(2))
            shown = shown_width(m.group(1) or "", block[:m.start()])
            if not figure or not figure["heights"] or not shown:
                continue
            scale = shown / figure["width"]
            sizes = sorted(h * scale / ratio for h in figure["heights"])
            smallest = sizes[len(sizes) // 10]
            if smallest < latex_rules.MIN_FIGURE_FONT_PT:
                found.append(finding("WARN", "figure-font", f"{m.group(2)}: much of its text prints at about "
                                                             f"{smallest:.1f}pt (captions are 9pt, body 10pt)"))
    return found


def shown_width(options: str, before: str) -> float:
    """Printed width in points of an \\includegraphics, inside its minipage if any."""
    absolute = re.search(r"width\s*=\s*([\d.]+)\s*(pt|in|cm)", options)
    if absolute:
        return float(absolute.group(1)) * {"pt": 1, "in": 72.27, "cm": 28.45}[absolute.group(2)]
    width = re.search(r"width\s*=\s*([\d.]*)\s*\\(linewidth|textwidth|columnwidth)", options)
    if not width:
        return 0.0
    factor = float(width.group(1) or 1)
    box = re.findall(r"\\begin\{(?:minipage|subfigure)\}(?:\[[^\]]*\])?\{([\d.]*)\s*\\(?:linewidth|textwidth)\}", before)
    if box and width.group(2) == "linewidth":
        factor *= float(box[-1] or 1)
    return factor * venue.TEXT_WIDTH_PT


CHECKS = [placement, order, captions, caption_content, caption_position, graphics, removed, tables, same_page, figure_fonts]
