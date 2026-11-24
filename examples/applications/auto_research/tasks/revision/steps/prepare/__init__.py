"""Step 1: copy the baseline and the draft, build them, and read everything the checks need."""

import shutil
import statistics
from dataclasses import dataclass, field
from pathlib import Path

from ...rules.paper import Rules, load as load_rules
from . import build, pdftext
from .source import added_paragraphs, flatten, floats, paragraphs, sectioned_paragraphs


# Baseline value that means the paper's current working tree, including uncommitted edits.
WORKTREE = "WORKTREE"


@dataclass
class Paper:
    """One baseline and draft pair, built and read. The checks use only this object."""
    base_flat: str = ""
    draft_flat: str = ""
    main_source: str = ""
    root: Path | None = None
    base_bib: dict = field(default_factory=dict)
    draft_bib: dict = field(default_factory=dict)
    approved_removals: list = field(default_factory=list)
    scope: str = "changes"
    built: bool = True
    log: str = ""
    page_texts: list = field(default_factory=list)
    lines: list = field(default_factory=list)
    aux: str = ""
    type3: dict = field(default_factory=dict)
    extras: list = field(default_factory=list)
    body_word_height: float = 0.0
    figures: dict = field(default_factory=dict)
    diff_ok: bool = True
    diff_message: str = ""
    rules: Rules = field(default_factory=Rules)
    sections: list = field(default_factory=list)      # section titles to check; empty means all

    @property
    def added(self) -> list[str]:
        return self.in_sections(added_paragraphs(self.base_flat, self.draft_flat))

    @property
    def target(self) -> list[str]:
        """The paragraphs the wording rules apply to: added ones, or all with scope "paper",
        within the chosen sections."""
        return self.in_sections(paragraphs(self.draft_flat)) if self.scope == "paper" else self.added

    def in_sections(self, items: list[str]) -> list[str]:
        if not self.sections:
            return items
        # paragraphs() keeps a heading on the paragraph after it; sectioned_paragraphs() does not,
        # so a paragraph is in a chosen section when that section's text is part of it
        keep = [p for title, p in sectioned_paragraphs(self.draft_flat) if section_matches(title, self.sections)]
        return [p for p in items if any(k in p for k in keep)]

    @property
    def bib_text(self) -> str:
        return "\n".join((v or b"").decode("utf-8", "replace") for v in self.draft_bib.values())


def prepare(paper_dir: Path, baseline: str, draft_source: Path, tmp: Path, out: Path,
            approved_removals: list[str], scope: str = "changes") -> Paper:
    base, draft = tmp / "base", tmp / "draft"
    if baseline == WORKTREE:
        build.copy_tree(paper_dir, base)
    else:
        build.export_baseline(paper_dir, baseline, base)
    build.copy_tree(draft_source, draft)
    rules = load_rules(paper_dir)
    main = rules.main_tex
    paper = Paper(base_flat=flatten(base, main), draft_flat=flatten(draft, main),
                  main_source=(draft / main).read_text(), root=draft,
                  base_bib=build.read_files(base, rules.bib_files),
                  draft_bib=build.read_files(draft, rules.bib_files),
                  approved_removals=approved_removals, scope=scope, rules=rules)

    paper.built, paper.log = build.compile_pdf(draft, main)
    if paper.built:
        pdf = draft / Path(main).with_suffix(".pdf").name
        read_pdf(paper, pdf)
        aux = pdf.with_suffix(".aux")
        paper.aux = aux.read_text(errors="replace") if aux.exists() else ""
        shutil.copy(pdf, out / "draft.pdf")

    for extra in rules.extra_tex:
        if (draft / extra).exists():
            ok, log = build.compile_pdf(draft, extra)
            pdf = draft / Path(extra).with_suffix(".pdf").name
            paper.extras.append({"name": extra, "built": ok, "log": log})
            if ok:
                paper.type3[pdf.name] = pdftext.type3_fonts(pdf)
                shutil.copy(pdf, out / pdf.name)

    build.compile_pdf(base, main)
    paper.diff_ok, paper.diff_message = build.render_diff(base, draft, main, out / "diff.pdf")
    return paper


def read_pdf(paper: Paper, pdf: Path) -> None:
    """Page text, line boxes, fonts, and the text size inside each figure file."""
    paper.page_texts = pdftext.page_texts(pdf)
    paper.lines = pdftext.bbox(pdf)
    paper.type3[pdf.name] = pdftext.type3_fonts(pdf)
    heights = pdftext.word_heights(paper.lines[:3])
    paper.body_word_height = statistics.median(heights) if heights else 0.0
    for item in floats(paper.draft_flat):
        for name in item["graphics"]:
            path = paper.root / name
            path = path if path.suffix else path.with_suffix(".pdf")
            if path.suffix.lower() == ".pdf" and path.exists():
                pages = pdftext.bbox(path, 1, 1)
                if pages:
                    paper.figures[name] = {"width": pages[0]["width"], "heights": pdftext.word_heights(pages)}


def section_key(title: str) -> str:
    return " ".join(title.lower().split())


def section_matches(title: str, chosen: list[str]) -> bool:
    """A title matches a chosen section by its start: "Workflow Evolution" matches
    "Workflow Evolution (RQ2)"."""
    return any(section_key(title).startswith(section_key(c)) for c in chosen)


def section_titles(text: str) -> list[str]:
    return list(dict.fromkeys(title for title, _ in sectioned_paragraphs(text)))
