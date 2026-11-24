"""Running text: wording in added paragraphs, new numbers, deletions, and paper-wide wording."""

import re

from ...rules import latex as latex_rules
from ...rules import venue, writing
from ..prepare.source import body, paragraphs, sentences, without_floats
from .base import excerpt, finding, matches, prose


def style(paper) -> list[dict]:
    """The author's wording rules, applied to the paragraphs under review."""
    found = []
    for paragraph in paper.target:
        lowered = paragraph.lower()
        for term, reason in paper.rules.banned_terms.items():
            if term.lower() in lowered:
                found.append(finding("FAIL", "term", f'"{term}" in: {paragraph[:80]}… — {reason}'))
        for term, better in paper.rules.preferred_terms.items():
            if re.search(r"\b" + re.escape(term) + r"\b", paragraph, re.I):
                found.append(finding("WARN", "term-preferred", f'"{term}" → {better}: {paragraph[:80]}…'))
        for sentence in sentences(paragraph):
            found += sentence_style(sentence)
    return found


def sentence_style(sentence: str) -> list[dict]:
    found, head = [], sentence[:80]
    if re.match(r"(Table|Figure)~?\s*(\\ref|\d)", sentence.lstrip("\\textbf{} ")):
        found.append(finding("FAIL", "opening", f"Starts with a table or figure: {head}…"))
    commas = sentence.count(",")
    if commas > writing.MAX_COMMAS_PER_SENTENCE:
        found.append(finding("WARN", "commas", f"{commas} commas: {head}…"))
    for word in writing.DISCOURAGED_WORDS:
        if re.search(r"\b" + word + r"\b", sentence, re.I):
            found.append(finding("WARN", "wording", f'"{word}" in: {head}…'))
    for pattern in writing.NEGATIONS:
        if re.search(pattern, sentence, re.I):
            found.append(finding("WARN", "negation", f"Defines the method by what it is not; say what it does: {head}…"))
    if sentence.split(" ", 1)[0].strip(",") in writing.DISCOURAGED_OPENERS:
        found.append(finding("WARN", "opener", f"Boilerplate opening: {head}…"))
    if re.search(r";|---|(?<![\d~]):(?!\d)", re.sub(r"\\[a-z]+\{[^}]*\}", "", sentence)):
        found.append(finding("WARN", "wording", f"Colon, semicolon or dash used to explain: {head}…"))
    return found


def expression(paper) -> list[dict]:
    """Expression rules from public writing guides, on the paragraphs under review."""
    found = []
    for paragraph in paper.target:
        sents = sentences(paragraph)
        for sentence in sents:
            found += sentence_expression(sentence)
        found += sentence_flow(sents)
    return found


def sentence_expression(sentence: str) -> list[dict]:
    found, head = [], sentence[:80]
    plain = re.sub(r"\\[a-zA-Z]+\*?(?:\[[^\]]*\])?(?:\{[^}]*\})?|\$[^$]*\$", " ", sentence)
    for check, (reason, phrases) in writing.WORDING.items():
        hits = [p for p in phrases if re.search(r"\b" + re.escape(p) + r"\b", plain, re.I)]
        if hits:
            found.append(finding("WARN", check, f"{reason} ({', '.join(hits)}): {head}…"))
    if any(plain.lstrip().startswith(o) for o in writing.FORMULAIC_OPENERS):
        found.append(finding("WARN", "formulaic-opener", f"Generic opening; start with the specific point: {head}…"))
    if re.match(writing.BARE_DEMONSTRATIVE, plain.strip()):
        found.append(finding("WARN", "vague-this", f"Name what \"this\" refers to (\"This result shows\"): {head}…"))
    words = len(plain.split())
    if words > writing.MAX_SENTENCE_WORDS:
        found.append(finding("WARN", "long-sentence", f"{words} words; split it: {head}…"))
    return found


def sentence_flow(sents: list[str]) -> list[dict]:
    """Two connective openers in a row, or three sentences opening with the same word."""
    found = []
    starts = [s.split(" ", 1)[0].strip(",") for s in sents]
    for i in range(1, len(sents)):
        if starts[i] in writing.CONNECTIVE_OPENERS and starts[i - 1] in writing.CONNECTIVE_OPENERS:
            found.append(finding("WARN", "connectives", f"Consecutive connective openers; let the logic carry: {sents[i][:80]}…"))
    for i in range(2, len(sents)):
        if starts[i] == starts[i - 1] == starts[i - 2]:
            found.append(finding("WARN", "repeated-opener", f"Three sentences open with \"{starts[i]}\": {sents[i][:80]}…"))
    return found


def numbers(paper) -> list[dict]:
    """Numbers new to the paper need a verified source; list them for the author."""
    known = set(re.findall(r"\d[\d,.]*\d|\d", paper.base_flat))
    new = []
    for paragraph in paper.added:
        text = re.sub(r"\\(?:ref|label|cite[pt]?)\{[^}]*\}|\\[a-zA-Z]+", " ", paragraph)
        for number in re.findall(r"\d[\d,.]*\d|\d", text):
            if number not in known and number not in new:
                new.append(number)
    return [finding("WARN", "numbers", "New numbers need a verified source: " + ", ".join(new))] if new else []


def deleted(paper) -> list[dict]:
    """Paragraphs of the baseline that no longer appear; the author approves each."""
    new = set(paragraphs(paper.draft_flat))
    return [finding("WARN", "deleted", f"Removed or rewritten paragraph: {p[:100]}…")
            for p in paragraphs(paper.base_flat) if p not in new]


def placeholders(paper) -> list[dict]:
    text, found = body(paper.draft_flat), []
    for pattern in latex_rules.PLACEHOLDERS:
        found += matches("FAIL", "placeholder", pattern, text, "Placeholder or review note left in the paper")
    return found


def absolute(paper) -> list[dict]:
    text, found = prose(paper.draft_flat), []
    for word, better in writing.ABSOLUTE_WORDS.items():
        for m in re.finditer(r"\b" + word + r"\b", text, re.I):
            found.append(finding("WARN", "absolute", f"\"{word}\" → {better}: …{excerpt(text, m.start())}…"))
    return found


def claims(paper) -> list[dict]:
    text = prose(paper.draft_flat)
    return (matches("WARN", "sota", r"state[- ]of[- ]the[- ]art|\bSOTA\b", text, "Do not claim SOTA", re.I)
            + matches("WARN", "previous-work", r"\bour (?:previous|prior|earlier) work", text,
                      "Avoid referring to our previous work", re.I))


def bold(paper) -> list[dict]:
    return [finding("WARN", "bold", f"Bold covers a whole phrase: {m.group(1)[:60]}")
            for m in re.finditer(r"\\textbf\{([^{}]*)\}", without_floats(body(paper.draft_flat)))
            if len(m.group(1).split()) > writing.MAX_BOLD_WORDS]


def headings(paper) -> list[dict]:
    """Subsection titles capitalize main words; a section has few \\paragraph headings."""
    found, text = [], body(paper.draft_flat)
    for m in re.finditer(r"\\(?:sub)+section\*?\{([^}]*)\}", text):
        title = re.sub(r"\\[a-zA-Z]+|[{}()]", " ", m.group(1))
        words = [w for w in re.split(r"[\s-]+", title) if w and w[0].isalpha()]
        lower = [w for i, w in enumerate(words)
                 if w[0].islower() and (i == 0 or w.lower() not in latex_rules.SMALL_WORDS)]
        if lower:
            found.append(finding("WARN", "heading-case", f"Capitalize main words in \"{m.group(1)}\": {', '.join(lower)}"))
    for part in re.split(r"\\(?:sub)*section\*?\{", text)[1:]:
        count = len(re.findall(r"\\paragraph\{", part))
        if count > writing.MAX_PARAGRAPH_HEADINGS:
            found.append(finding("WARN", "paragraph-count", f"\"{part.split('}', 1)[0]}\" has {count} \\paragraph headings"))
    return found


def last_lines(paper) -> list[dict]:
    """The last line of every paragraph, list item and caption in the body has four words
    and fills half a line. A last line is a short line that continues a full-width line
    at the same or a deeper indent (list items are indented)."""
    body_pages = next((i for i, t in enumerate(paper.page_texts) if "References" in t), len(paper.page_texts))
    found = []
    for number, page in enumerate(paper.lines[:body_pages], 1):
        left = (page["width"] - venue.TEXT_WIDTH_PT) / 2
        right = left + venue.TEXT_WIDTH_PT
        lines = [l for l in page["lines"] if l["y0"] < page["height"] - 70]
        for prev, line in zip(lines, lines[1:]):
            full = left - 2 < prev["x0"] < left + 25 and prev["x1"] > right - 3
            continues = line["x0"] > prev["x0"] - 3 and line["x0"] < left + 25 and 0 <= line["y0"] - prev["y1"] < 4
            if not (full and continues and line["x1"] < right - 10):
                continue
            words = [w[4] for w in line["words"]]
            share, text = (line["x1"] - line["x0"]) / venue.TEXT_WIDTH_PT, " ".join(words)
            if len(words) < latex_rules.MIN_LAST_LINE_WORDS or share < latex_rules.MIN_LAST_LINE_SHARE:
                found.append(finding("FAIL", "last-line",
                                     f"Page {number}: last line has {len(words)} words and fills {share:.0%}: \"{text}\""))
    return found


CHECKS = [style, expression, numbers, deleted, placeholders, absolute, claims, bold, headings, last_lines]
