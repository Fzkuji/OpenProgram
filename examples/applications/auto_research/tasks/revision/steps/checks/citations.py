"""Cross-references, citations and the bibliography (AML guide section 6)."""

import re

from ...rules import latex as latex_rules
from ...rules import venue
from ..prepare.source import body, without_floats
from .base import excerpt, finding, matches

REF_TARGETS = ("fig", "figure", "tab", "table", "sec", "section", "eq", "equation",
               "alg", "algorithm", "appendix", "app")


def references(paper) -> list[dict]:
    """Section~\\ref, Figure~\\ref ... with a capital and a tie; labels with a type prefix."""
    text, found = without_floats(body(paper.draft_flat)), []
    for m in re.finditer(r"(\b[A-Za-z]+\.?)(\s*~?\s*)\\(?:ref|eqref|autoref)\{", text):
        word, gap = m.group(1), m.group(2)
        if word.lower().rstrip(".s") not in REF_TARGETS:
            continue
        if word.rstrip("s") not in latex_rules.REF_WORDS:
            found.append(finding("FAIL", "ref-form", f"Write {word} as one of {', '.join(latex_rules.REF_WORDS)}: …{excerpt(text, m.start())}…"))
        elif gap != "~":
            found.append(finding("WARN", "ref-form", f"Use ~ between {word} and \\ref: …{excerpt(text, m.start())}…"))
    for label in re.findall(r"\\label\{([^}]+)\}", paper.draft_flat):
        if not label.startswith(latex_rules.LABEL_PREFIXES):
            found.append(finding("WARN", "label-prefix", f"Label {label} lacks a sec:/fig:/tab:/equ:/alg: prefix"))
    return found


def citations(paper) -> list[dict]:
    """Text~\\citep{}; \\citep or \\citet; a citation is never the subject."""
    text = without_floats(body(paper.draft_flat))
    return (matches("FAIL", "cite-space", r"[A-Za-z0-9.,)]\\cite", text, "Put ~ before the citation")
            + matches("WARN", "cite-space", r" \\cite[pt]?\{", text, "Use ~ instead of a space before the citation")
            + matches("WARN", "cite-form", r"\\cite\{", text, "Use \\citep or \\citet")
            + matches("FAIL", "cite-subject", r"(?:^|[.!?]\s+)~?\\citep?\{", text,
                      "A citation is the subject; name the method or authors"))


def cite_keys(paper) -> list[dict]:
    keys = set(re.findall(r"@\w+\s*\{\s*([^,\s]+)", paper.bib_text))
    cited = {k.strip() for group in re.findall(r"\\cite[a-z]*\*?(?:\[[^\]]*\])*\{([^}]*)\}", paper.draft_flat)
             for k in group.split(",") if k.strip()}
    return [finding("FAIL", "cite-keys", f"Citation {k} has no entry in the .bib file") for k in sorted(cited - keys)]


def bib_entries(paper) -> list[dict]:
    """Published versions over arXiv; one entry per paper. Read only: the .bib is edited by hand."""
    found, titles = [], {}
    for m in re.finditer(r"@(\w+)\s*\{\s*([^,\s]+),(.*?)(?=\n@|\Z)", paper.bib_text, re.S):
        key, fields = m.group(2), m.group(3)
        if re.search(r"arxiv", fields, re.I):
            found.append(finding("WARN", "bib-arxiv", f"{key} cites arXiv; use the published version if one exists"))
        title = re.search(r"title\s*=\s*[{\"](.+?)[}\"]\s*,?\s*\n", fields, re.S | re.I)
        if title:
            norm = re.sub(r"[^a-z0-9]", "", title.group(1).lower())
            if norm in titles:
                found.append(finding("WARN", "bib-duplicate", f"{key} and {titles[norm]} have the same title"))
            titles[norm] = key
    return found


def bib_unchanged(paper) -> list[dict]:
    """A changed .bib file is listed; each edit is verified against the published record."""
    return [finding("WARN", "bib", f"{name} changed; run citation_check on it and keep only edits that match "
                                   "the published record.")
            for name in paper.rules.bib_files if paper.base_bib.get(name) != paper.draft_bib.get(name)]


CHECKS = [references, citations, cite_keys, bib_entries, bib_unchanged]
