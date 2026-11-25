"""The measurable side of a writing style: read papers into paragraphs of plain prose and
count what a style is made of. Plain code, no model calls.

A paper (.tex, .pdf, .txt or .md) becomes {name, file, kind, paragraphs, intro}: running
prose only (LaTeX commands, math, floats and captions dropped; for a PDF, text blocks
of twelve words or more before the references). metrics() measures sentence length,
passive voice, first person, hedges, nominalisations, paragraph length and connectives;
the intro's first sentences show how the author opens.
"""

import re
import statistics
import subprocess
from collections import Counter
from pathlib import Path
from shutil import which

from ..overlap.prose import _strip as strip_latex
from ..revision.steps.prepare.source import body, flatten

ABBREV = re.compile(r"\b(e\.g|i\.e|et al|etc|Fig|Figs|Sec|Eq|Eqs|Tab|vs|cf|resp|approx|No|Ref|Refs|Thm|Def)\.", re.I)
SENTENCE_END = re.compile(r"(?<=[.!?])[\"')\]]*\s+(?=[\"'(\[]?[A-Z0-9])")
WORD = re.compile(r"[A-Za-z][A-Za-z'’-]*")
BE = r"\b(?:is|are|was|were|be|been|being|am)\b"
IRREGULAR = ("built made done shown known given taken found set put held kept left led meant run seen sent spent "
             "taught thought told won written chosen drawn grown hidden begun broken driven proven sought brought "
             "bought caught fed felt heard laid lost paid read said sold understood").split()
NOT_PARTICIPLE = {"indeed", "need", "speed", "seed", "embed", "red", "bed", "feed", "hundred", "ten", "often", "even",
                  "between", "token", "open", "seven", "eleven", "used"}
PASSIVE = re.compile(BE + r"(?:\s+(?:not|also|then|often|usually|typically|further|\w+ly)){0,2}\s+"
                     r"(\w+ed|\w+en|" + "|".join(IRREGULAR) + r")\b", re.I)
FIRST_PERSON = {"we", "our", "ours", "us", "my", "me", "ourselves"}
HEDGES = ("may might could possibly perhaps likely unlikely suggest suggests suggesting appear appears seem seems "
          "somewhat relatively potentially arguably presumably tend tends probably").split()
NOMINAL = re.compile(r"\b[a-z]{3,}(?:tion|sion|ment|ness|ance|ence|ity|ization|isation)s?\b")
NOT_NOMINAL = set(("section sections equation equations dimension dimensions question questions function functions "
                   "community university quantity quantities entity entities sentence sentences evidence conference "
                   "references reference science sciences since once instance instances distance distances balance "
                   "experience sequence sequences variance identity city fence hence whence silence moment moments "
                   "comment comments element elements segment segments document documents environment environments "
                   "government department business ness").split())
CONNECTIVES = ("however|moreover|furthermore|therefore|thus|hence|in contrast|in addition|specifically|"
               "in particular|notably|consequently|nevertheless|meanwhile|to this end|as a result|for example|"
               "for instance|instead|indeed|overall|finally|although|whereas|importantly|similarly|"
               "on the other hand|in practice|in summary|to address").split("|")
CONNECTIVE = re.compile(r"\b(" + "|".join(sorted(CONNECTIVES, key=len, reverse=True)) + r")\b", re.I)
HEADING = re.compile(r"\\(?:section|chapter)\*?(?:\[[^\]]*\])?\{([^}]*)\}")
SUFFIXES = (".tex", ".pdf", ".txt", ".md")
MIN_PDF_WORDS = 12
LONG = 30                    # words: a long sentence


def words(text: str) -> list[str]:
    return WORD.findall(text)


def sentences(paragraph: str) -> list[str]:
    """The prose sentences of a plain-text paragraph (four words or more, mostly words)."""
    protected = ABBREV.sub(lambda m: m.group(0).replace(".", "<dot>"), paragraph)
    out = []
    for s in SENTENCE_END.split(protected):
        s = s.replace("<dot>", ".").strip()
        tokens = s.split()
        if len(tokens) >= 4 and len(words(s)) >= 0.6 * len(tokens):
            out.append(s)
    return out


def plain(latex: str) -> str:
    """One LaTeX paragraph as plain prose."""
    text = " ".join(strip_latex(latex).replace("\x01", " ").replace("\x02", " ").split())
    return re.sub(r"\s+([.,;:!?)])", r"\1", text)


# Reading papers

def tex_paper(path: Path) -> dict:
    """A .tex paper (its \\input files inlined) as paragraphs, raw and plain, by section."""
    text = body(flatten(path.parent, path.name))
    paragraphs, intro, section = [], [], ""
    for chunk in re.split(r"\n[ \t]*\n", text):
        head = HEADING.search(chunk)
        if head:
            section = head.group(1).strip()
        prose = plain(chunk)
        if len(prose.split()) >= 8:
            paragraphs.append(prose)
            if "introduction" in section.lower():
                intro.append(prose)
    return {"paragraphs": paragraphs, "intro": intro}


PDF_HEADING = re.compile(r"(?:\d+(?:\.\d+)*\.?\s+|[IVX]+\.\s+)?([A-Z][A-Za-z ,:&-]{2,60})")


def pdf_blocks(path: Path) -> list[str]:
    """The text blocks of a PDF: pymupdf's blocks, else pdftotext's paragraphs."""
    try:
        import fitz
    except ImportError:
        tool = which("pdftotext")
        if not tool:
            raise ImportError("reading a PDF needs pymupdf or pdftotext, and neither is installed") from None
        done = subprocess.run([tool, "-enc", "UTF-8", str(path), "-"], capture_output=True, text=True, timeout=120)
        if done.returncode:
            raise ValueError(f"pdftotext failed: {done.stderr.strip()[:120]}")
        return re.split(r"\n\s*\n|\f", done.stdout)
    with fitz.open(str(path)) as doc:
        return [b[4] for page in doc for b in page.get_text("blocks") if b[6] == 0]


def pdf_paper(path: Path) -> dict:
    """A PDF's text blocks of twelve or more words before the references."""
    paragraphs, intro, in_intro = [], [], False
    for raw in pdf_blocks(path):
        text = " ".join(re.sub(r"(\w)-\n(\w)", r"\1\2", raw).split())
        if re.fullmatch(r"(?:\d+\.?\s*)?(?:References|Bibliography)", text, re.I):
            break
        n = len(text.split())
        if n <= 8 and PDF_HEADING.fullmatch(text):
            in_intro = "introduction" in text.lower()
            continue
        m = re.match(r"(?:1\.?|I\.)\s*Introduction\s+(.*)", text)
        if m:
            in_intro, text = True, m.group(1)
        if n < MIN_PDF_WORDS or re.match(r"(?:Figure|Fig\.|Table)\s*\d", text):
            continue
        paragraphs.append(text)
        if in_intro:
            intro.append(text)
    return {"paragraphs": paragraphs, "intro": intro}


def text_paper(path: Path) -> dict:
    paragraphs = [" ".join(p.split()) for p in re.split(r"\n\s*\n", path.read_text(encoding="utf-8", errors="ignore"))]
    paragraphs = [p for p in paragraphs if len(p.split()) >= 8]
    start = next((i for i, p in enumerate(paragraphs) if re.match(r"#*\s*(?:\d+\.?\s*)?Introduction\b", p, re.I)), -1)
    intro = paragraphs[start:start + 6] if start >= 0 else paragraphs[:4]
    return {"paragraphs": paragraphs, "intro": intro}


def read_paper(path: Path) -> dict:
    suffix = path.suffix.lower()
    read = {".tex": tex_paper, ".pdf": pdf_paper}.get(suffix, text_paper)
    paper = read(path)
    return {"name": path.stem, "file": str(path), "kind": suffix.lstrip("."), **paper}


def paper_files(paths: list[Path]) -> list[Path]:
    """Each paper once: a folder's main .tex (the one with \\documentclass) stands for the
    other files of its folder tree, which are its sections and figures; any other .tex,
    .pdf, .txt or .md file counts on its own."""
    tex = [p for p in paths if p.suffix.lower() == ".tex"]
    mains = [p for p in tex if "\\documentclass" in p.read_text(encoding="utf-8", errors="ignore")]
    roots = [p.parent for p in mains]
    return [p for p in paths if p in mains or not any(p.is_relative_to(r) for r in roots)]


# Measuring

def rate(count: int, total: int, per: int) -> float:
    return round(per * count / total, 2) if total else 0.0


def metrics(paragraphs: list[str]) -> dict:
    """The style numbers of a body of prose."""
    sents = [s for p in paragraphs for s in sentences(p)]
    lengths = [len(words(s)) for s in sents]
    all_words = [w.lower() for p in paragraphs for w in words(p)]
    n = len(all_words)
    para_words = [len(words(p)) for p in paragraphs]
    connectives = Counter(m.group(1).lower() for p in paragraphs for m in CONNECTIVE.finditer(p))
    nominal = [w for w in (m.group(0) for p in paragraphs for m in NOMINAL.finditer(p.lower())) if w not in NOT_NOMINAL]
    return {
        "words": n,
        "sentences": len(sents),
        "mean_sentence_words": round(statistics.mean(lengths), 1) if lengths else 0.0,
        "median_sentence_words": statistics.median(lengths) if lengths else 0,
        "p90_sentence_words": quantile(lengths, 0.9),
        "long_sentence_share": round(sum(x > LONG for x in lengths) / len(lengths), 2) if lengths else 0.0,
        "passive_share": round(sum(bool(passive(s)) for s in sents) / len(sents), 2) if sents else 0.0,
        "first_person_per_1k": rate(sum(w in FIRST_PERSON for w in all_words), n, 1000),
        "we_sentence_share": round(sum(bool(re.search(r"\b[Ww]e\b|\b[Oo]ur\b", s)) for s in sents) / len(sents), 2)
        if sents else 0.0,
        "hedges_per_1k": rate(sum(w in HEDGES for w in all_words), n, 1000),
        "nominalisations_per_100": rate(len(nominal), n, 100),
        "median_paragraph_words": statistics.median(para_words) if para_words else 0,
        "median_paragraph_sentences": statistics.median([len(sentences(p)) for p in paragraphs]) if paragraphs else 0,
        "connectives_per_1k": {k: rate(v, n, 1000) for k, v in connectives.most_common(8)},
        "top_nominalisations": [w for w, _ in Counter(nominal).most_common(8)],
    }


def quantile(values: list[int], q: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


def passive(sentence: str) -> str:
    """The passive construction of a sentence ("is evaluated"), or empty."""
    for m in PASSIVE.finditer(sentence):
        if m.group(1).lower() not in NOT_PARTICIPLE:
            return m.group(0)
    return ""


def openings(papers: list[dict]) -> list[dict]:
    """The first sentence of each paper's introduction."""
    out = []
    for p in papers:
        first = next((s for para in p["intro"] for s in sentences(para)), "")
        if first:
            out.append({"paper": p["name"], "sentence": first})
    return out


CONTRIBUTION = re.compile(r"\bcontribution|\bwe (?:propose|present|introduce|show)\b|\bin this (?:paper|work)\b", re.I)


def contribution_passages(papers: list[dict], most: int = 2) -> list[dict]:
    out = []
    for p in papers:
        found = [x for x in p["intro"] if CONTRIBUTION.search(x)][:most]
        out += [{"paper": p["name"], "text": x} for x in found]
    return out


# What the paper is compared against

# Writing-in-the-Sciences defaults when there is no profile to compare with.
RULE_TARGETS = {"mean_sentence_words": 22.0, "long_sentence_share": 0.15, "passive_share": 0.25,
                "nominalisations_per_100": 4.0, "hedges_per_1k": 8.0, "median_paragraph_words": 150}
COMPARED = ("mean_sentence_words", "long_sentence_share", "passive_share", "first_person_per_1k",
            "hedges_per_1k", "nominalisations_per_100", "median_paragraph_words")


def deviations(current: dict, target: dict) -> list[dict]:
    """Where the paper differs from the target noticeably: more than 25% (and a set
    absolute amount) away from it."""
    margin = {"mean_sentence_words": 3, "long_sentence_share": 0.08, "passive_share": 0.08,
              "first_person_per_1k": 4, "hedges_per_1k": 3, "nominalisations_per_100": 1,
              "median_paragraph_words": 30}
    out = []
    for key in COMPARED:
        if key not in target or key not in current:
            continue
        a, b = float(current[key]), float(target[key])
        if abs(a - b) > margin[key] and abs(a - b) > 0.25 * max(abs(b), 1e-9):
            out.append({"measure": key, "paper": a, "target": b, "direction": "higher" if a > b else "lower"})
    return out


CLUTTER = {
    "in order to": "to", "due to the fact that": "because", "it is worth noting that": "(cut)",
    "it should be noted that": "(cut)", "it is important to note that": "(cut)", "a number of": "several",
    "a large number of": "many", "has the ability to": "can", "is able to": "can", "are able to": "can",
    "at the present time": "now", "in terms of": "(rephrase)", "in the case of": "for", "with respect to": "for",
    "for the purpose of": "to", "in spite of the fact that": "although", "the fact that": "(rephrase)",
    "on the basis of": "from", "prior to": "before", "in the event that": "if", "it is clear that": "(cut)",
    "it can be seen that": "(cut)", "basically": "(cut)", "very": "(cut)", "quite": "(cut)", "really": "(cut)",
    "various different": "various", "each and every": "each", "first and foremost": "first",
}
CLUTTER_RE = re.compile(r"\b(" + "|".join(re.escape(k) for k in sorted(CLUTTER, key=len, reverse=True)) + r")\b", re.I)
NOMINAL_PHRASE = re.compile(r"\b(?:perform|make|conduct|carry out|provide|give|achieve|have|take|obtain)(?:s|ed|ing)?"
                            r"\s+(?:an?\s+|the\s+)?(?:\w+\s+)?\w+(?:tion|sion|ment|ance|ence|sis)\s+of\b", re.I)


def paragraph_flags(text: str, long_limit: int, words_limit: int) -> list[str]:
    """What code sees in one paragraph of plain prose: long sentences, passives,
    clutter phrases, nominalised verbs, paragraph length."""
    flags = []
    sents = sentences(text)
    for s in sents:
        n = len(words(s))
        if n > long_limit:
            flags.append(f"long sentence ({n} words): {s[:90]}")
        p = passive(s)
        if p:
            flags.append(f"passive: '{p}'")
    for m in CLUTTER_RE.finditer(text):
        flags.append(f"clutter: '{m.group(1)}' -> {CLUTTER[m.group(1).lower()]}")
    for m in NOMINAL_PHRASE.finditer(text):
        flags.append(f"nominalisation: '{m.group(0)}'")
    n = len(words(text))
    if n > words_limit:
        flags.append(f"long paragraph ({n} words, {len(sents)} sentences)")
    return flags
