"""The compiled output: build log, page limit, appendix build, fonts, comparison PDF."""

import re

from .base import finding


def build(paper) -> list[dict]:
    return log_findings(paper.log, paper.built, "build")


def log_findings(log: str, ok: bool, check: str) -> list[dict]:
    if not ok:
        return [finding("FAIL", check, "LaTeX build failed; see the build log.")]
    found = []
    overfull = len(re.findall(r"^Overfull \\hbox", log, re.M))
    if overfull:
        found.append(finding("FAIL", "overfull" if check == "build" else check, f"{overfull} overfull boxes."))
    undefined = sorted(set(re.findall(r"(?:Reference|Citation) `([^']+)' on page \d+ undefined", log)))
    if undefined:
        found.append(finding("FAIL", "references" if check == "build" else check, "Undefined: " + ", ".join(undefined)))
    return found


def pages(paper) -> list[dict]:
    """The body ends before the page that starts the references."""
    if not paper.built:
        return []
    for number, text in enumerate(paper.page_texts, 1):
        if re.search(r"^\s*References\s*$", text, re.M):
            body_pages = number - 1 if text.strip().startswith("References") else number
            if body_pages > paper.rules.body_page_limit:
                return [finding("FAIL", "pages", f"Body has {body_pages} pages; the limit is {paper.rules.body_page_limit}.")]
            return []
    return [finding("FAIL", "pages", "No References heading found in the PDF.")]


def appendix(paper) -> list[dict]:
    found = []
    for extra in paper.extras:
        if not extra["built"]:
            found.append(finding("FAIL", "appendix-build", f"{extra['name']} does not compile"))
        else:
            found += log_findings(extra["log"], True, "appendix-build")
    return found


def type3(paper) -> list[dict]:
    return [finding("FAIL", "type3", f"{pdf} uses Type 3 font {font}")
            for pdf, fonts in paper.type3.items() for font in fonts]


def diff(paper) -> list[dict]:
    return [] if paper.diff_ok else [finding("WARN", "diff", paper.diff_message)]


CHECKS = [build, pages, appendix, type3, diff]
