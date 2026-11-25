"""What the venue tools share: the venue families with their template facts, telling which
family a paper's source is written for, and the `Key: value` fields of workflow/venue.md.
Plain code.

The template facts follow each venue's latest call for papers and author kit at the time
of writing; names carry the year (iclr2026_conference, neurips_2025), so the plan says
to download the current kit rather than trust the year printed here.
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..submission import common

AREAS_FILE = Path(__file__).with_name("areas.json")


@dataclass(frozen=True)
class Family:
    key: str
    name: str
    aliases: tuple
    detect: tuple                    # regexes on the comment-free source
    preamble: tuple                  # the submission preamble lines
    camera_ready: tuple              # the camera-ready preamble lines
    bib_style: str
    main_pages: int | None           # pages of the main text at submission; None: no limit
    camera_pages: int | None
    references: str                  # how references and appendices count
    anonymous: bool                  # double-blind submission
    required_sections: tuple = ()    # (heading regex, what it is, where it goes)
    checklist: str = ""              # a checklist the submission needs, and where
    template: str = ""               # where to download the official kit
    notes: tuple = field(default_factory=tuple)


SECTION = r"\\(?:section|subsection|paragraph)\*?\s*\{\s*"

FAMILIES: dict[str, Family] = {f.key: f for f in (
    Family("iclr", "ICLR", ("iclr",), (r"\\usepackage(?:\[[^\]]*\])?\{iclr\d{4}_conference",),
           (r"\documentclass{article}", r"\usepackage{iclr2026_conference,times}"),
           (r"\documentclass{article}", r"\usepackage{iclr2026_conference,times}", r"\iclrfinalcopy"),
           "iclr2026_conference", 9, 10, "references and appendix unlimited, after the main text", True,
           ((SECTION + r"Ethics Statement", "Ethics statement (encouraged, not counted)", "after the conclusion"),
            (SECTION + r"Reproducibility Statement", "Reproducibility statement (encouraged, not counted)",
             "after the conclusion")),
           "", "https://github.com/ICLR/Master-Template",
           ("LLM use must be disclosed (see ai_disclosure).", "Submitted through OpenReview; reviews are public.")),
    Family("neurips", "NeurIPS", ("neurips", "nips"), (r"\\usepackage(?:\[[^\]]*\])?\{neurips_\d{4}\}",),
           (r"\documentclass{article}", r"\usepackage{neurips_2025}"),
           (r"\documentclass{article}", r"\usepackage[final]{neurips_2025}"),
           "plainnat", 9, 10, "references, checklist and appendix unlimited, after the main text", True,
           ((SECTION + r"(?:NeurIPS )?Paper Checklist", "NeurIPS Paper Checklist (required, desk reject without it)",
             "after the references and appendix"),),
           "NeurIPS Paper Checklist inside the PDF", "https://neurips.cc/Conferences/2025/CallForPapers",
           ("Style files: https://media.neurips.cc/Conferences/NeurIPS2025/Styles.zip",)),
    Family("icml", "ICML", ("icml",), (r"\\usepackage(?:\[[^\]]*\])?\{icml\d{4}\}",),
           (r"\documentclass{article}", r"\usepackage{icml2025}"),
           (r"\documentclass{article}", r"\usepackage[accepted]{icml2025}"),
           "icml2025", 8, 9, "references, impact statement and appendix unlimited", True,
           ((SECTION + r"Impact Statement", "Impact Statement (required, not counted)", "after the conclusion"),),
           "", "https://icml.cc/Conferences/2025/AuthorInstructions",
           ("Style files: https://media.icml.cc/Conferences/ICML2025/Styles/icml2025.zip",)),
    Family("arr", "ACL Rolling Review (ACL / EMNLP / NAACL)", ("acl", "emnlp", "naacl", "eacl", "arr"),
           (r"\\usepackage(?:\[[^\]]*\])?\{(?:acl|acl2023|naaclhlt\d*|emnlp\d*)\}",),
           (r"\documentclass[11pt]{article}", r"\usepackage[review]{acl}"),
           (r"\documentclass[11pt]{article}", r"\usepackage[final]{acl}"),
           "acl_natbib", 8, 9, "long paper 8 pages (short 4); references, Limitations and appendix unlimited", True,
           ((SECTION + r"Limitations", "Limitations (required, desk reject without it)", "after the conclusion, "
             "before the references"),
            (SECTION + r"Ethic(?:s|al) (?:Statement|Considerations)", "Ethics statement (optional, not counted)",
             "after Limitations")),
           "Responsible NLP checklist in the ARR submission form", "https://github.com/acl-org/acl-style-files",
           ("Short papers: 4 pages, camera-ready 5.",)),
    Family("cvpr", "CVPR / ICCV", ("cvpr", "iccv"), (r"\\usepackage(?:\[[^\]]*\])?\{(?:cvpr|iccv)\}",),
           (r"\documentclass[10pt,twocolumn,letterpaper]{article}", r"\usepackage[review]{cvpr}"),
           (r"\documentclass[10pt,twocolumn,letterpaper]{article}", r"\usepackage{cvpr}"),
           "ieeenat_fullname", 8, 8, "references unlimited; appendix as a separate supplementary PDF", True,
           (), "", "https://github.com/cvpr-org/author-kit",
           ("Set the paper ID with \\def\\paperID{...}; no extra camera-ready page.",)),
    Family("eccv", "ECCV", ("eccv",), (r"\\usepackage(?:\[[^\]]*\])?\{eccv\}", r"\\documentclass(?:\[[^\]]*\])?\{llncs\}"),
           (r"\documentclass[runningheads]{llncs}", r"\usepackage[review,year=2026,ID=*****]{eccv}"),
           (r"\documentclass[runningheads]{llncs}", r"\usepackage[year=2026]{eccv}"),
           "splncs04", 14, 14, "references unlimited; appendix as supplementary", True,
           (), "", "https://github.com/paolo-favaro/paper-template"),
    Family("aaai", "AAAI", ("aaai",), (r"\\usepackage(?:\[[^\]]*\])?\{aaai\d{2,4}\}",),
           (r"\documentclass[letterpaper]{article}", r"\usepackage[submission]{aaai2026}"),
           (r"\documentclass[letterpaper]{article}", r"\usepackage{aaai2026}"),
           "aaai2026", 7, 7, "7 pages of technical content plus pages for references only", True,
           ((r"[Rr]eproducibility [Cc]hecklist", "Reproducibility checklist (required)", "after the references"),),
           "AAAI reproducibility checklist", "https://aaai.org/authorkit26/",
           ("Camera-ready may buy up to two extra pages.",)),
    Family("ijcai", "IJCAI", ("ijcai",), (r"\\usepackage(?:\[[^\]]*\])?\{ijcai\d{2}\}",),
           (r"\documentclass{article}", r"\usepackage{ijcai25}"),
           (r"\documentclass{article}", r"\usepackage{ijcai25}"),
           "named", 7, 7, "7 pages plus up to 2 pages of references only", True,
           (), "", "https://www.ijcai.org/authors_kit"),
    Family("acm", "ACM (KDD, WWW, SIGIR, CHI, ... via acmart)", ("acm", "kdd", "www", "sigir", "cikm", "wsdm",
           "chi", "mm", "acmart"), (r"\\documentclass(?:\[[^\]]*\])?\{acmart\}",),
           (r"\documentclass[sigconf,review,anonymous]{acmart}",),
           (r"\documentclass[sigconf]{acmart}", r"\setcopyright{acmlicensed}", r"\acmConference[...]{...}{...}{...}"),
           "ACM-Reference-Format", 8, 9, "KDD: 8 pages plus references and appendix; check the track", True,
           ((r"\\begin\{CCSXML\}|\\ccsdesc", "CCS concepts (\\begin{CCSXML} and \\ccsdesc)", "preamble, before \\maketitle"),
            (r"\\keywords\s*\{", "\\keywords{...}", "preamble, before \\maketitle")),
           "", "https://www.acm.org/publications/proceedings-template",
           ("Every figure needs \\Description{...} (TAPS rejects figures without alt text).",)),
    Family("ieee", "IEEE conference / journal (IEEEtran)", ("ieee", "ieeetran", "icassp", "icra", "iros"),
           (r"\\documentclass(?:\[[^\]]*\])?\{IEEEtran\}",),
           (r"\documentclass[conference]{IEEEtran}",),
           (r"\documentclass[conference]{IEEEtran}",),
           "IEEEtran", 6, 6, "venue-specific; ICASSP 4+1, ICRA 6+2", False,
           (), "", "https://www.ieee.org/conferences/publishing/templates.html",
           ("Check the final PDF with IEEE PDF eXpress.",)),
    Family("tmlr", "TMLR", ("tmlr",), (r"\\usepackage(?:\[[^\]]*\])?\{tmlr\}",),
           (r"\documentclass[10pt]{article}", r"\usepackage{tmlr}"),
           (r"\documentclass[10pt]{article}", r"\usepackage[accepted]{tmlr}"),
           "tmlr", 12, None, "12 pages for a regular submission (longer gets a longer review); no hard limit", True,
           ((SECTION + r"Broader Impact Statement", "Broader Impact Statement (when there are risks)",
             "after the conclusion"),),
           "", "https://github.com/JmlrOrg/tmlr-style-file"),
    Family("jmlr", "JMLR", ("jmlr",), (r"\\usepackage(?:\[[^\]]*\])?\{jmlr2e\}",),
           (r"\documentclass[twoside,11pt]{article}", r"\usepackage{jmlr2e}"),
           (r"\documentclass[twoside,11pt]{article}", r"\usepackage{jmlr2e}"),
           "plainnat", None, None, "no page limit", False,
           (), "", "https://www.jmlr.org/format/format.html",
           ("Single-blind: authors are named at submission.",)),
    Family("arxiv", "arXiv preprint", ("arxiv", "preprint"), (),
           (r"\documentclass{article}",), (r"\documentclass{article}",),
           "plainnat", None, None, "no page limit", False,
           (), "", "https://info.arxiv.org/help/submit_tex.html",
           ("Build the source bundle with arxiv_package.",)),
)}


def family(name: str) -> Family:
    key = name.strip().lower()
    for f in FAMILIES.values():
        if key == f.key or key in f.aliases:
            return f
    raise ValueError(f"unknown venue {name!r}; known: {', '.join(sorted(FAMILIES))} "
                     f"(also {', '.join(sorted(a for f in FAMILIES.values() for a in f.aliases if a != f.key))})")


def detect(source: str) -> Family | None:
    """The family whose style the comment-free source loads; None for a plain article."""
    for f in FAMILIES.values():
        if any(re.search(p, source) for p in f.detect):
            return f
    return None


def from_venue(paper: Path) -> Family | None:
    """The family workflow/venue.md names (its `Name:` field or its first heading lines)."""
    path = paper / "workflow" / "venue.md"
    text = path.read_text(encoding="utf-8", errors="ignore") if path.is_file() else ""
    name = field_value(paper, "Name") or field_value(paper, "Venue") or text[:600]
    for f in FAMILIES.values():
        for alias in (f.key,) + f.aliases:
            if alias not in ("arxiv", "preprint", "mm", "www", "chi") and re.search(rf"\b{re.escape(alias)}\b", name, re.I):
                return f
    return None


def field_value(paper: Path, key: str) -> str:
    """The value of a `Key: value` line in workflow/venue.md (a list bullet allowed), or ""."""
    path = paper / "workflow" / "venue.md"
    if not path.is_file():
        return ""
    m = re.search(rf"^\s*(?:[-*]\s*)?{re.escape(key)}\s*:[ \t]*(.+)$", path.read_text(encoding="utf-8", errors="ignore"),
                  re.M | re.I)
    return m.group(1).strip() if m else ""


def field_list(paper: Path, key: str) -> list[str]:
    return [p.strip() for p in re.split(r"[;,]", field_value(paper, key)) if p.strip()]


def rules_json(paper: Path) -> dict:
    """workflow/rules.json as written, without the workflow defaults."""
    path = paper / "workflow" / "rules.json"
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except ValueError:
        return {}


def areas() -> dict[str, list[str]]:
    data = json.loads(AREAS_FILE.read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if not k.startswith("_")}


def source(paper: Path) -> tuple[Path, str]:
    """The main .tex and the comment-free text of every .tex it reaches."""
    main = common.main_tex(paper)
    if main is None:
        raise ValueError(f"{paper}: no main .tex (set main_tex in workflow/rules.json)")
    return main, common.sources(paper, main)


def raw_sources(paper: Path, main: Path) -> dict[str, str]:
    """{relative path: text with comments} of the main .tex and the .tex files it inputs."""
    used, _ = common.used_files(paper, main)
    return {p: (paper / p).read_text(encoding="utf-8", errors="ignore")
            for p, kind in used.items() if p.endswith(".tex") and kind in ("main", "tex")}


def finding(kind: str, level: str, where: str, problem: str, fix: str) -> dict:
    return {"kind": kind, "level": level, "where": where, "problem": problem, "fix": fix}


def cell(text: str) -> str:
    return " ".join(str(text).split()).replace("|", "\\|")


__all__ = ["FAMILIES", "Family", "areas", "detect", "family", "field_list", "field_value", "from_venue", "source"]
