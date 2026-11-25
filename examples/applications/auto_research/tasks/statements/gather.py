"""The facts a paper's back-matter statements may state, gathered in plain code: where the
code and data live, their licenses, the grants, the authors with what Git says each did,
human-data signals, and which statements the venue requires. No model calls."""

import json
import re
import subprocess
from pathlib import Path

from ..feedback.people import paper_authors, same_name
from ..scouting.common import project_field
from ..submission import common as tex
from ..submission.disclosure import AI_NAME, CODE_EXTS, FIGURE_EXTS, WRITING_EXTS
from ..venue import facts as venue
from ..venue.camera_ready import acks_text

KINDS = ("availability", "credit", "interests", "funding", "ethics")
HEADINGS = {"availability": "Code and Data Availability", "credit": "Author Contributions",
            "interests": "Competing Interests", "funding": "Funding", "ethics": "Ethics Statement"}
# The headings a paper may already use for each statement.
EXISTING = {
    "availability": r"(?:Code|Data|Code and Data|Data and Code|Software)\s+Availability|Reproducibility Statement",
    "credit": r"Author(?:s'?|'s)?\s+Contributions?|CRediT",
    "interests": r"Competing Interests?|Conflicts? of Interests?|Declaration of (?:Competing )?Interests?",
    "funding": r"Funding|Financial Support",
    "ethics": r"Ethic(?:s|al)\s+(?:Statement|Considerations)|(?:Broader\s+)?Impact Statement|Societal Impact",
}
# What a venue.md requirement line asks for.
ASKS = {
    "availability": r"(?:data|code|software)(?: and (?:data|code))? availability|availability statement",
    "credit": r"author(?:s'?|'s)? contributions?|credit taxonomy|\bcredit\b",
    "interests": r"competing interests?|conflicts? of interests?|declaration of interests?",
    "funding": r"funding statement|funding (?:information|sources?)|financial (?:support|disclosure)",
    "ethics": r"ethic|broader impact|impact statement|societal impact",
}
CREDIT_ROLES = ("Conceptualization", "Data curation", "Formal analysis", "Funding acquisition", "Investigation",
                "Methodology", "Project administration", "Resources", "Software", "Supervision", "Validation",
                "Visualization", "Writing – original draft", "Writing – review & editing")
# What a share of commits in an area suggests (the author confirms everything else).
HINTS = {"code": ("Software",), "paper": ("Writing – original draft",),
         "experiments": ("Investigation", "Formal analysis"), "data": ("Data curation",),
         "figures": ("Visualization",)}
EXPERIMENT_FILES = {"results.tsv", "evidence.csv", "experiment.md", "findings.md", "baselines.csv",
                    "experiment_plan.csv", "hypotheses.csv", "settings.csv"}
DATA_EXTS = {".csv", ".tsv", ".jsonl", ".parquet", ".arrow", ".npy", ".npz", ".h5", ".hdf5", ".pkl"}
SKIP_DIRS = {".git", "drafts", "node_modules", ".venv", "venv", "__pycache__", ".lake", "lean", "figures"}
HOSTS = r"(?:github\.com|gitlab\.com|bitbucket\.org|huggingface\.co|zenodo\.org|doi\.org|osf\.io|figshare\.com|" \
        r"kaggle\.com|codeocean\.com|dataverse\.[\w.]+|anonymous\.4open\.science)"
URL = re.compile(r"https?://[^\s{}\\,;\"'<>]+")
HOST_URL = re.compile(rf"(?<![/\w.])(?:www\.)?{HOSTS}/[^\s{{}}\\,;\"'<>]+")
HUMAN = re.compile(r"interview|transcript|participant|annotator|respondent|questionnaire|survey_responses|"
                   r"consent|crowd|mturk|prolific|subjects?\b", re.I)
ETHICS_TEXT = re.compile(r"\bIRB\b|institutional review board|ethics (?:committee|board|approval)|informed consent|"
                         r"approved by|exempt", re.I)
FUNDING_TEXT = re.compile(r"fund|grant|support(?:ed)? by|award|fellowship|sponsor|foundation|council", re.I)
GRANT_ID = re.compile(r"(?<![\w/.-])[A-Z0-9]+(?:[-/][A-Z0-9]+)*(?![\w/-])")
YEAR = re.compile(r"(?:19|20)\d{2}")
SENTENCE = re.compile(r"(?<!\bNo\.)(?<!\bNos\.)(?<!\bnos\.)(?<!\bno\.)(?<!\bDr\.)(?<!\bProf\.)(?<!\be\.g\.)"
                      r"(?<!\bi\.e\.)(?<!\bvs\.)(?<!\bFig\.)(?<!\bInc\.)(?<!\bLtd\.)(?<!\bCo\.)(?<!\bSt\.)"
                      r"(?<=[.!?])\s+(?=[A-Z\\])")

# License families: (regex on a statement or license text, SPDX id, full name).
LICENSES = (
    (r"\bCC[- ]?0\b|CC0[- ]1\.0|Creative Commons Zero|public domain dedication", "CC0-1.0", "Creative Commons Zero v1.0"),
    (r"\bCC[- ]BY[- ]NC[- ]SA[- ]?4\.0|Attribution[- ]NonCommercial[- ]ShareAlike 4\.0", "CC-BY-NC-SA-4.0",
     "Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International"),
    (r"\bCC[- ]BY[- ]NC[- ]4\.0|Attribution[- ]NonCommercial 4\.0", "CC-BY-NC-4.0",
     "Creative Commons Attribution-NonCommercial 4.0 International"),
    (r"\bCC[- ]BY[- ]SA[- ]?4\.0|Attribution[- ]ShareAlike 4\.0", "CC-BY-SA-4.0",
     "Creative Commons Attribution-ShareAlike 4.0 International"),
    (r"\bCC[- ]BY[- ]?4\.0|Creative Commons Attribution 4\.0|Attribution 4\.0 International", "CC-BY-4.0",
     "Creative Commons Attribution 4.0 International"),
    (r"Apache(?: License)?,?[- ](?:Version |v)?2\.0|\bApache-2\.0", "Apache-2.0", "Apache License 2.0"),
    (r"\bMIT\b(?: License)?|Permission is hereby granted, free of charge", "MIT", "MIT License"),
    (r"BSD[- ]3|3-Clause BSD|Neither the name of", "BSD-3-Clause", "BSD 3-Clause License"),
    (r"BSD[- ]2|2-Clause BSD|Simplified BSD", "BSD-2-Clause", "BSD 2-Clause License"),
    (r"\bAGPL|Affero General Public License", "AGPL-3.0", "GNU Affero General Public License v3.0"),
    (r"\bLGPL|Lesser General Public License", "LGPL-3.0", "GNU Lesser General Public License v3.0"),
    (r"\bGPL[- ]?v?3|GNU General Public License,? (?:v|version )?3|GENERAL PUBLIC LICENSE\s+Version 3", "GPL-3.0",
     "GNU General Public License v3.0"),
    (r"\bGPL[- ]?v?2|GNU General Public License,? (?:v|version )?2|GENERAL PUBLIC LICENSE\s+Version 2", "GPL-2.0",
     "GNU General Public License v2.0"),
    (r"Mozilla Public License|\bMPL[- ]?2\.0", "MPL-2.0", "Mozilla Public License 2.0"),
    (r"\bUnlicense\b", "Unlicense", "The Unlicense"),
    (r"\bODbL\b|Open Database License", "ODbL-1.0", "Open Data Commons Open Database License v1.0"),
    (r"OpenRAIL", "OpenRAIL", "Open Responsible AI License"),
)
LICENSE_FILES = ("LICENSE", "LICENSE.md", "LICENSE.txt", "LICENCE", "COPYING", "COPYING.md")


def spdx(text: str) -> list[str]:
    """The SPDX ids of every license a text names (an SPDX-License-Identifier line wins)."""
    m = re.search(r"SPDX-License-Identifier:\s*([\w.+-]+)", text)
    if m:
        return [m.group(1)]
    found = []
    for pattern, sid, _ in LICENSES:
        if re.search(pattern, text, re.I if sid not in ("MIT",) else 0) and sid not in found:
            if sid == "BSD-2-Clause" and "BSD-3-Clause" in found:
                continue
            found.append(sid)
    return found


def license_name(sid: str) -> str:
    return next((name for _, s, name in LICENSES if s == sid), sid)


def https(url: str) -> str:
    """A clone URL as the web address a reader opens."""
    url = url.strip()
    m = re.match(r"git@([^:]+):(.+)$", url) or re.match(r"ssh://git@([^/]+)/(.+)$", url)
    if m:
        url = f"https://{m.group(1)}/{m.group(2)}"
    url = re.sub(r"^http://", "https://", url)
    return re.sub(r"\.git$", "", url.rstrip("/"))


def norm_url(url: str) -> str:
    url = re.sub(r"^https?://(?:www\.)?", "", url.strip().rstrip(".)/").casefold())
    return re.sub(r"\.git$", "", url).rstrip("/")


def urls_in(text: str) -> list[str]:
    found = []
    for m in URL.finditer(text):
        found.append(m.group().rstrip(".)"))
    for m in HOST_URL.finditer(text):
        found.append("https://" + m.group().rstrip(".)"))
    seen, out = set(), []
    for u in found:
        if norm_url(u) not in seen:
            seen.add(norm_url(u))
            out.append(u)
    return out


def git(paper: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(paper), *args], capture_output=True, text=True)
    return done.stdout if done.returncode == 0 else ""


def remotes(repo: Path) -> list[str]:
    urls = []
    for line in git(repo, "remote", "-v").splitlines():
        parts = line.split()
        if len(parts) >= 2 and https(parts[1]) not in urls and "://" in https(parts[1]):
            urls.append(https(parts[1]))
    return urls


def licenses_of(folder: Path, where: str) -> list[dict]:
    out = []
    for name in LICENSE_FILES:
        path = folder / name
        if path.is_file():
            for sid in spdx(path.read_text(encoding="utf-8", errors="ignore")[:4000]):
                out.append({"id": sid, "name": license_name(sid), "source": f"{where}{name}"})
            break
    return out


def area_of(path: str) -> str:
    """Which part of the work a changed file belongs to, for the CRediT hints."""
    p = Path(path)
    if p.parts and p.parts[0] == "drafts":
        return ""
    if p.name in EXPERIMENT_FILES:
        return "experiments"
    if p.parts and p.parts[0] == "workflow":
        return ""
    ext = p.suffix.lower()
    if ext in WRITING_EXTS:
        return "paper"
    if ext in DATA_EXTS or (p.parts and p.parts[0] in ("data", "datasets")):
        return "data"
    if ext in FIGURE_EXTS and (ext != ".pdf" or re.search(r"fig|plot|image|img", path, re.I)):
        return "figures"
    if ext in CODE_EXTS:
        return "code"
    return ""


def contributors(paper: Path) -> list[dict]:
    """Each human commit author with commits per area (AI co-authors and bots skipped)."""
    out: dict[str, dict] = {}
    for block in git(paper, "log", "--no-merges", "--name-only", "--format=\x1e%an\x1f%ae\x1f").split("\x1e")[1:]:
        parts = block.split("\x1f")
        if len(parts) < 3:
            continue
        name, email = parts[0].strip(), parts[1].strip()
        if not name or AI_NAME.search(name) or "[bot]" in name or "noreply@anthropic" in email:
            continue
        person = out.setdefault(name.casefold(), {"name": name, "emails": [], "commits": 0, "areas": {}})
        if email and email not in person["emails"]:
            person["emails"].append(email)
        person["commits"] += 1
        for area in sorted({area_of(f.strip()) for f in parts[2].splitlines() if f.strip()} - {""}):
            person["areas"][area] = person["areas"].get(area, 0) + 1
    for person in out.values():
        person["areas"] = dict(sorted(person["areas"].items()))
    return sorted(out.values(), key=lambda p: -p["commits"])


def credit_hints(authors: list[str], people: list[dict]) -> tuple[list[dict], list[dict]]:
    """Per paper author: the matched Git identities, commits per area and suggested roles;
    and the Git contributors that match no author."""
    rows, used = [], set()
    for a in authors:
        mine = [p for p in people if same_name(a, p["name"]) or p["name"].casefold() == a.casefold()
                or any(e.split("@")[0].casefold().replace(".", " ") == a.casefold() for e in p["emails"])]
        used |= {id(p) for p in mine}
        areas: dict[str, int] = {}
        for p in mine:
            for k, n in p["areas"].items():
                areas[k] = areas.get(k, 0) + n
        roles = [r for k in HINTS if areas.get(k) for r in HINTS[k]]
        rows.append({"author": a, "git": [p["name"] for p in mine], "commits": sum(p["commits"] for p in mine),
                     "areas": areas, "suggested_roles": roles})
    return rows, [p for p in people if id(p) not in used]


def data_files(paper: Path, limit: int = 30) -> list[str]:
    """Data files and folders in the repository (paths), outside drafts, workflow and code folders."""
    out = []
    for path in sorted(paper.rglob("*")):
        rel = path.relative_to(paper)
        if any(part in SKIP_DIRS or part.startswith(".") for part in rel.parts) or rel.parts[0] == "workflow":
            continue
        if path.is_dir() and rel.parts[0] in ("data", "datasets") and len(rel.parts) == 1:
            out.append(rel.as_posix() + "/")
        elif path.is_file() and path.suffix.lower() in DATA_EXTS and rel.parts[0] not in ("data", "datasets"):
            out.append(rel.as_posix())
        if len(out) >= limit:
            break
    return out


def sentences(text: str) -> list[str]:
    text = " ".join(re.sub(r"(?<!\\)%.*", "", text).split())
    return [s.strip() for s in SENTENCE.split(text) if s.strip()]


def grant_ids(sentence: str) -> list[str]:
    ids = []
    for m in GRANT_ID.finditer(sentence):
        token = m.group().strip()
        if YEAR.fullmatch(token) or len(re.sub(r"\D", "", token)) < 4 or len(token) < 5:
            continue
        ids.append(token)
    return ids


def grants(paper: Path, source: str, rules: dict) -> tuple[list[dict], list[str]]:
    """Grant ids with the sentence naming each, and every funding sentence: from the
    acknowledgments of the paper, venue.md and project.md `Funding:`, rules.json notes."""
    found, said = [], []
    places = [("main.tex acknowledgments", acks_text(source))]
    places += [("venue.md Funding", venue.field_value(paper, "Funding")),
               ("project.md Funding", project_field(paper, "Funding"))]
    notes = rules.get("notes") if isinstance(rules.get("notes"), dict) else {}
    places += [(f"rules.json notes.{k}", str(v)) for k, v in notes.items() if re.search(r"fund|grant", k, re.I)]
    for where, text in places:
        for s in sentences(text or ""):
            if not FUNDING_TEXT.search(s) and not where.endswith("Funding") and "notes." not in where:
                continue
            said.append(s if s not in said else "")
            for gid in grant_ids(s):
                if gid not in [g["id"] for g in found]:
                    found.append({"id": gid, "sentence": s, "source": where})
    return found, [s for s in said if s]


def field_any(paper: Path, rules: dict, *keys: str) -> list[dict]:
    """`Key: value` lines of venue.md and project.md, and rules.json notes, for these keys."""
    out = []
    notes = rules.get("notes") if isinstance(rules.get("notes"), dict) else {}
    for key in keys:
        for where, value in (("venue.md", venue.field_value(paper, key)), ("project.md", project_field(paper, key))):
            if value:
                out.append({"field": key, "value": value, "source": where})
        for k, v in notes.items():
            if k.replace("_", " ").casefold() == key.casefold() and str(v).strip():
                out.append({"field": key, "value": str(v).strip(), "source": f"rules.json notes.{k}"})
    return out


def datasheet(paper: Path) -> dict:
    path = paper / "workflow" / "datasheet.md"
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8", errors="ignore")
    head = {k: (re.search(rf"^{k}:[ \t]*(.*)$", text, re.M) or [None, ""])[1].strip() for k in ("License", "Location")}
    parts = {m.group(1).strip(): m.group(2).strip()[:800]
             for m in re.finditer(r"^##\s+(.+?)\n(.*?)(?=^##\s|\Z)", text, re.M | re.S) if m.group(2).strip()}
    return {"license": head["License"], "location": head["Location"],
            "parts": {k: v for k, v in parts.items() if k in ("Collection process", "Uses", "Distribution",
                                                               "Composition", "Motivation")}}


def croissant(paper: Path) -> dict:
    path = paper / "workflow" / "croissant.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except ValueError:
        return {}
    files = [d.get("contentUrl", "") for d in data.get("distribution") or [] if isinstance(d, dict)]
    return {"name": str(data.get("name", "")), "url": str(data.get("url", "")),
            "license": str(data.get("license", "")), "files": [f for f in files if f]}


def latest(paper: Path, workflow: str, name: str) -> str:
    found = sorted((paper / "drafts" / workflow).glob(f"*/{name}"))
    return found[-1].relative_to(paper).as_posix() if found else ""


def section_text(source: str, title: str, limit: int = 1500) -> str:
    m = re.search(r"\\section\*?\s*\{\s*" + title + r"[^}]*\}", source, re.I)
    if not m:
        return ""
    rest = source[m.end():]
    end = re.search(r"\\section\b|\\bibliography\b|\\appendix|\\end\{document\}", rest)
    return " ".join(rest[:end.start() if end else limit].split())[:limit]


def venue_needs(paper: Path, source: str) -> dict:
    """The venue family and the statements it requires, each with its heading and why."""
    fam = venue.from_venue(paper) or venue.detect(source)
    needs: dict[str, dict] = {}
    for _, what, where in (fam.required_sections if fam else ()):
        if re.search(r"optional", what, re.I):
            continue
        for kind, pattern in (("ethics", ASKS["ethics"]), ("availability", r"reproducibility")):
            if re.search(pattern, what, re.I):
                heading = re.sub(r"\s*\(.*$", "", what).strip()
                needs.setdefault(kind, {"heading": heading.title() if heading.islower() else heading,
                                        "why": f"{fam.name}: {what}", "where": where})
    path = paper / "workflow" / "venue.md"
    text = path.read_text(encoding="utf-8", errors="ignore") if path.is_file() else ""
    for line in text.splitlines():
        if re.match(r"^\s*(?:[-*]\s*)?(?:Funding|Authors|Affiliations|Competing interests|Name)\s*:", line, re.I):
            continue
        for kind, pattern in ASKS.items():
            if re.search(pattern, line, re.I) and kind not in needs:
                needs[kind] = {"heading": "", "why": "venue.md: " + " ".join(line.strip(" -*").split())[:200],
                               "where": ""}
    if "availability" in needs and re.search(r"reproducibility", needs["availability"]["heading"], re.I):
        needs["availability"]["heading"] = "Reproducibility Statement"
    return {"venue": fam.name if fam else "", "family": fam.key if fam else "", "needs": needs}


def existing(source: str) -> dict[str, str]:
    """The statements the paper already has, by the heading it uses."""
    out = {}
    for kind, pattern in EXISTING.items():
        m = re.search(r"\\(?:section|subsection|paragraph)\*?\s*\{\s*(" + pattern + r")[^}]*\}", source, re.I)
        if m:
            out[kind] = m.group(1)
    return out


def gather(paper: Path) -> tuple[dict, list[str]]:
    """Every fact a statement may use, and notes about what could not be read."""
    notes: list[str] = []
    main = tex.main_tex(paper)
    source = tex.sources(paper, main) if main else ""
    if not main:
        notes.append("no main .tex: authors, links and acknowledgments were not read")
    rules = venue.rules_json(paper)
    code = rules.get("code") if isinstance(rules.get("code"), dict) else {}
    repo = str(code.get("repo") or "").strip()
    code_urls = [{"url": https(repo) if "://" in repo or repo.startswith("git@") else repo,
                  "source": "rules.json code.repo"}] if repo else []
    for u in remotes(paper):
        code_urls.append({"url": u, "source": "git remote of the paper repository"})
    licenses = licenses_of(paper, "")
    local = Path(repo).expanduser() if repo and not re.match(r"\w+://|git@", repo) else None
    if local is not None:
        local = local if local.is_absolute() else paper / local
        if local.is_dir():
            licenses += licenses_of(local, f"{repo}/")
            code_urls += [{"url": u, "source": f"git remote of {repo}"} for u in remotes(local)]
    links = [{"url": u, "source": "main.tex"} for u in urls_in(source)]
    sheet, crois = datasheet(paper), croissant(paper)
    data_urls = []
    if sheet.get("location"):
        data_urls.append({"url": sheet["location"], "source": "datasheet.md Location"})
    if crois.get("url"):
        data_urls.append({"url": crois["url"], "source": "croissant.json url"})
    data_urls += [{"url": f, "source": "croissant.json distribution"} for f in crois.get("files", [])]
    for where, value in (("datasheet.md License", sheet.get("license", "")), ("croissant.json license",
                                                                             crois.get("license", ""))):
        for sid in spdx(value):
            licenses.append({"id": sid, "name": license_name(sid), "source": where})
        if value and not spdx(value):
            licenses.append({"id": value, "name": value, "source": where})
    seen, uniq = set(), []
    for item in code_urls + data_urls + links:
        key = norm_url(item["url"])
        if key not in seen:
            seen.add(key)
            uniq.append(item)
    names, origins = paper_authors(paper)
    people = contributors(paper)
    hints, others = credit_hints(names, people)
    grant_list, funding = grants(paper, source, rules)
    human = sorted({p.relative_to(paper).as_posix() for p in paper.rglob("*")
                    if HUMAN.search(p.name) and not any(x in SKIP_DIRS or x.startswith(".")
                                                       for x in p.relative_to(paper).parts)})[:20]
    if (paper / "workflow" / "codebook.md").is_file():
        human.append("workflow/codebook.md (qualitative coding of transcripts)")
    ethics_said = [s for s in sentences(source) if ETHICS_TEXT.search(s)][:10]
    kind = project_field(paper, "Kind")
    facts = {
        "title": (re.search(r"\\title\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}", source) or [None, ""])[1].strip(),
        "kind": kind, "claim": project_field(paper, "Claim"),
        "double_blind": tex.double_blind(paper),
        "code": [u for u in uniq if u in code_urls],
        "data": [u for u in uniq if u in data_urls],
        "links_in_paper": [u for u in uniq if u in links],
        "data_files": data_files(paper),
        "licenses": licenses,
        "supplementary": latest(paper, "anonymous_package", "supplementary.zip"),
        "authors": [{"name": n, "source": o} for n, o in zip(names, origins)],
        "affiliations": field_any(paper, rules, "Affiliations"),
        "credit_hints": hints,
        "contributions_declared": field_any(paper, rules, "Contributions", "Author contributions"),
        "other_contributors": [{"name": p["name"], "commits": p["commits"], "areas": p["areas"]} for p in others],
        "grants": grant_list, "funding_sentences": funding,
        "interests_declared": field_any(paper, rules, "Competing interests", "Conflicts of interest", "Conflicts"),
        "human_data": human, "ethics_sentences": ethics_said,
        "ethics_declared": field_any(paper, rules, "Ethics", "IRB", "Ethics approval"),
        "datasheet": sheet.get("parts", {}),
        "limitations": section_text(source, "Limitations"),
        "ai_disclosure": latest(paper, "ai_disclosure", "ai_disclosure.md"),
    }
    facts.update(venue_needs(paper, source))
    facts["existing"] = existing(source)
    return facts, notes


__all__ = ["CREDIT_ROLES", "HEADINGS", "KINDS", "gather", "license_name", "norm_url", "spdx", "urls_in"]
