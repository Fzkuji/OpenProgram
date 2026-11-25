"""The rules of one paper: venue values and wording the author set for this paper alone.

Defaults come from venue.py and writing.py. A paper overrides them in
<paper_dir>/workflow/rules.json (auto_research scaffolds the file), so the workflow
code itself holds nothing that belongs to one paper. Keys:

  version                             submission or camera_ready: the version being prepared
  main_tex, extra_tex, bib_files      the files to build and the bibliographies
  body_page_limit                     pages before the references
  required_source, forbidden_source   lines the main file must and must not contain
  required_sections                   section titles the venue asks for
  required_authors                    strings the author block must contain
  banned_terms {term: reason}         FAIL in added text
  preferred_terms {term: better}      WARN in added text
  writing_rules                       the author's own rules for this paper, free text;
                                      the reviewer reports a violation as rule 35
  code {repo, allowed_contributors}   the public code release; empty repo means none is asked for
  deliverables [{name, path}]         files the final version needs besides the paper (poster, slides)
  figure_scripts                      globs of the plotting scripts the figures task reads and runs
  figure_python                       the interpreter that runs them; empty finds a python3 with matplotlib
  notes {name: text}                  values the manual checklist items refer to
"""

import json
import re
from dataclasses import dataclass, field, fields
from pathlib import Path

from . import venue, writing

FILE = Path("workflow") / "rules.json"


@dataclass
class Rules:
    version: str = "submission"
    main_tex: str = field(default_factory=lambda: venue.MAIN_TEX)
    extra_tex: list = field(default_factory=lambda: list(venue.EXTRA_TEX))
    bib_files: list = field(default_factory=lambda: list(venue.BIB_FILES))
    body_page_limit: int = field(default_factory=lambda: venue.BODY_PAGE_LIMIT)
    required_source: list = field(default_factory=lambda: list(venue.REQUIRED_SOURCE))
    forbidden_source: list = field(default_factory=lambda: list(venue.FORBIDDEN_SOURCE))
    required_sections: list = field(default_factory=lambda: list(venue.REQUIRED_SECTIONS))
    required_authors: list = field(default_factory=lambda: list(venue.REQUIRED_AUTHORS))
    banned_terms: dict = field(default_factory=lambda: dict(writing.BANNED_TERMS))
    preferred_terms: dict = field(default_factory=lambda: dict(writing.PREFERRED_TERMS))
    writing_rules: str = ""
    code: dict = field(default_factory=dict)
    deliverables: list = field(default_factory=list)
    figure_scripts: list = field(default_factory=lambda: ["workflow/figures/plot_*.py"])
    figure_python: str = ""
    notes: dict = field(default_factory=dict)


def load(paper_dir: Path) -> Rules:
    """The defaults, with every key of <paper_dir>/workflow/rules.json applied over them."""
    rules = Rules()
    path = Path(paper_dir) / FILE
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    known = {f.name for f in fields(Rules)}
    unknown = set(data) - known - {"_doc"}
    if unknown:
        raise ValueError(f"{path}: unknown keys {sorted(unknown)}")
    for key, value in data.items():
        if key != "_doc":
            setattr(rules, key, value)
    if "main_tex" not in data and not (Path(paper_dir) / rules.main_tex).is_file():
        rules.main_tex = found_main(Path(paper_dir)) or rules.main_tex
    if "bib_files" not in data and not any((Path(paper_dir) / b).is_file() for b in rules.bib_files):
        rules.bib_files = [b.name for b in sorted(Path(paper_dir).glob("*.bib"))] or rules.bib_files
    return rules


def found_main(paper: Path) -> str:
    """The one top-level .tex that starts a document, when rules.json names none."""
    mains = [tex.name for tex in sorted(paper.glob("*.tex"))
             if re.search(r"^\s*\\documentclass", tex.read_text(encoding="utf-8", errors="ignore"), re.M)]
    return mains[0] if len(mains) == 1 else ""


__all__ = ["FILE", "Rules", "load"]
