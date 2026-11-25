"""The facts a software citation may state, gathered in plain code, and the public
look-ups behind them: ORCID iDs (ORCID public API), an archive DOI for the repository
(DataCite and Zenodo) and whether that DOI resolves (the doi.org handle API). No model
calls. Every network call goes through citations.sources.fetch; a source that fails is
named in sources.UNAVAILABLE and never raises."""

import re
import subprocess
import urllib.error
import urllib.parse
from datetime import date
from pathlib import Path

from auto_research.release import github

from ..citations import sources
from ..feedback.people import AUTHOR, paper_authors, plausible
from ..scouting.common import latex_text, project_field
from ..statements.gather import LICENSE_FILES, croissant, datasheet, https, license_name, norm_url, remotes, spdx, urls_in
from ..submission import common as tex
from ..venue import facts as venue

ORCID = "https://pub.orcid.org/v3.0"
DATACITE = "https://api.datacite.org/dois"
ZENODO = "https://zenodo.org/api/records"
HANDLE = "https://doi.org/api/handles/"
JSON = {"Accept": "application/json"}
ORCID_ID = re.compile(r"\d{4}-\d{4}-\d{4}-\d{3}[\dX]")
DOI = re.compile(r"10\.\d{4,9}(?:\.\d+)?/[^\s\"'<>{}]+")
ARXIV = re.compile(r"(?:arxiv[:/ ]\s*|abs/)?(\d{4}\.\d{4,5})(?:v\d+)?", re.I)
PARTICLES = {"van", "von", "de", "der", "den", "da", "di", "del", "della", "du", "la", "le", "dos", "das", "ter",
             "ten", "bin", "al"}
CODE_LANGS = {".py": "Python", ".r": "R", ".jl": "Julia", ".cpp": "C++", ".cc": "C++", ".c": "C", ".rs": "Rust",
              ".go": "Go", ".java": "Java", ".js": "JavaScript", ".ts": "TypeScript", ".m": "MATLAB",
              ".lean": "Lean", ".scala": "Scala", ".sh": "Shell"}
SKIP = {".git", "drafts", "node_modules", ".venv", "venv", "__pycache__", ".lake", "workflow"}
CANDIDATES = 5          # ORCID candidates whose works are read
STOP = set("the of and for in at on a an to with from by university institute department school college "
           "laboratory lab center centre".split())


def git(repo: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    return done.stdout.strip() if done.returncode == 0 else ""


def paper_source(paper: Path) -> str:
    try:
        main = tex.main_tex(paper)
        return tex.sources(paper, main) if main else ""
    except (OSError, ValueError):
        return ""


def clean(text: str) -> str:
    text = text.replace("\\%", "%").replace("\\&", "&").replace("\\_", "_").replace("{,}", ",")
    return latex_text(text).replace("\\", "").strip()


def title_of(source: str) -> str:
    m = re.search(r"\\title\s*(?:\[[^\]]*\])?\s*\{((?:[^{}]|\{[^{}]*\})*)\}", source)
    return clean(m.group(1).replace("\\\\", " ")) if m else ""


def abstract_of(source: str) -> str:
    m = re.search(r"\\begin\{abstract\}(.*?)\\end\{abstract\}", source, re.S)
    return clean(m.group(1)) if m else ""


def tex_keywords(source: str) -> list[str]:
    m = re.search(r"\\(?:keywords|begin\{keywords\}|IEEEkeywords)\s*\{?(.*?)(?:\}|\\end\{(?:keywords|IEEEkeywords)\})",
                  source, re.S)
    return [clean(k) for k in re.split(r"[;,]|\\sep", m.group(1)) if clean(k)] if m else []


def split_name(name: str) -> dict:
    """CFF given-names and family-names; lowercase particles start the family name."""
    parts = name.split()
    if len(parts) == 1:
        return {"given-names": "", "family-names": parts[0]}
    cut = len(parts) - 1
    for i in range(1, len(parts) - 1):
        if parts[i].casefold() in PARTICLES:
            cut = i
            break
    return {"given-names": " ".join(parts[:cut]), "family-names": " ".join(parts[cut:])}


def affiliations(source: str) -> dict[str, list[str]]:
    """Each author's affiliations as the \\author block writes them: the lines after the
    name, \\affiliation{\\institution{..}}, or a single \\affil{..} for everyone."""
    out: dict[str, list[str]] = {}
    for m in AUTHOR.finditer(source):
        for part in re.split(r"\\(?:and|And|AND)\b", m.group(1)):
            explicit = [clean(re.sub(r"\\(?:city|country|state|postcode|streetaddress)\s*\{[^}]*\}", "", a))
                        for a in re.findall(r"\\(?:affiliation|institution|affil)\s*(?:\[[^\]]*\])?\s*"
                                            r"\{((?:[^{}]|\{[^{}]*\})*)\}", part)]
            body = re.sub(r"\\(?:thanks|footnote|email|affiliation|affil|orcid|href|url)\s*(?:\[[^\]]*\])?\s*"
                          r"\{(?:[^{}]|\{[^{}]*\})*\}", " ", part)
            lines = [clean(x) for x in re.split(r"\\\\|\\newline|\\par\b", body)]
            lines = [x for x in lines if x]
            if not lines:
                continue
            names = [n.strip() for n in re.split(r",|;|\band\b", lines[0].replace("*", " ")) if plausible(n.strip())]
            places = [x for x in lines[1:] if "@" not in x and not re.fullmatch(r"[\W\d]*", x)] + explicit
            places = [p for p in places if p]
            for n in names:
                out.setdefault(" ".join(n.split()), [])
                out[" ".join(n.split())] += [p for p in places if p not in out[" ".join(n.split())]]
    shared = re.findall(r"\\affil\s*(?:\[[^\]]*\])?\s*\{((?:[^{}]|\{[^{}]*\})*)\}", source)
    if len(shared) == 1:
        for n in out:
            out[n] = out[n] or [clean(shared[0])]
    return out


def orcids_written(source: str) -> dict[str, str]:
    """ORCID iDs the paper itself writes next to a name (\\orcid{..} or an orcid.org link)."""
    out = {}
    for m in AUTHOR.finditer(source):
        for part in re.split(r"\\(?:and|And|AND)\b", m.group(1)):
            found = ORCID_ID.search(part) if "orcid" in part.lower() else None
            first = clean(re.split(r"\\\\|\\newline", re.sub(r"\\(?:thanks|orcid|href)\s*\{[^}]*\}", " ", part))[0])
            if found and plausible(first):
                out[first] = found.group()
    return out


def code_folder(paper: Path, code_dir: str, rules: dict) -> tuple[Path, str]:
    """code_dir; else rules.json code.repo when it is a local folder; else the paper repository."""
    if code_dir:
        path = Path(code_dir).expanduser()
        path = path if path.is_absolute() else paper / path
        if not path.is_dir():
            raise ValueError(f"code_dir {path} is not a folder")
        return path.resolve(), "code_dir"
    repo = str(((rules.get("code") or {}) if isinstance(rules.get("code"), dict) else {}).get("repo") or "").strip()
    if repo and not re.match(r"\w+://|git@", repo):
        path = Path(repo).expanduser()
        path = path if path.is_absolute() else paper / path
        if path.is_dir():
            return path.resolve(), "rules.json code.repo"
    return paper, "paper_dir"


def licenses_of(folder: Path, where: str) -> list[dict]:
    """The license of the folder's LICENSE file (whitespace folded, so a title broken
    across lines still reads)."""
    for name in LICENSE_FILES:
        path = folder / name
        if path.is_file():
            text = " ".join(path.read_text(encoding="utf-8", errors="ignore")[:4000].split())
            return [{"id": sid, "name": license_name(sid), "source": f"{where}{name}"} for sid in spdx(text)]
    return []


def version_of(code: Path) -> tuple[str, str, str]:
    """The latest Git tag (without a leading v) and its date, else 0.1.0 and today."""
    tag = git(code, "describe", "--tags", "--abbrev=0")
    if tag:
        when = git(code, "log", "-1", "--format=%cs", tag)
        return re.sub(r"^v(?=\d)", "", tag), when or date.today().isoformat(), f"git tag {tag}"
    return "0.1.0", date.today().isoformat(), "no git tag: 0.1.0"


def languages(code: Path) -> list[str]:
    counts: dict[str, int] = {}
    for path in code.rglob("*"):
        rel = path.relative_to(code).parts
        if path.is_file() and not any(p in SKIP or p.startswith(".") for p in rel[:-1]):
            lang = CODE_LANGS.get(path.suffix.lower())
            if lang:
                counts[lang] = counts.get(lang, 0) + 1
    return sorted(counts, key=lambda k: -counts[k])[:3]


def own_ids(paper: Path, rules: dict) -> dict:
    """The paper's own arXiv id and DOI: rules.json `published`/`doi`/`arxiv`, project.md
    `Published:`/`DOI:`/`arXiv:`."""
    values = [str(rules.get(k) or "") for k in ("published", "doi", "arxiv")]
    values += [project_field(paper, k) for k in ("Published", "DOI", "arXiv")]
    out = {}
    for value in values:
        for token in value.split():
            m = DOI.search(token)
            if m and m.group().lower().startswith("10.48550/arxiv."):
                out.setdefault("arxiv", re.sub(r"v\d+$", "", m.group().lower().split("arxiv.", 1)[1]))
            elif m:
                out.setdefault("doi", m.group().rstrip(".,;"))
            elif "arxiv" in token.lower() or re.fullmatch(r"\d{4}\.\d{4,5}(?:v\d+)?", token):
                a = ARXIV.search(token)
                if a:
                    out.setdefault("arxiv", a.group(1))
    return out


def gather(paper: Path, code_dir: str) -> tuple[dict, list[str]]:
    """Every fact the citation files may state, each with its source, and notes."""
    notes: list[str] = []
    rules = venue.rules_json(paper)
    code, code_from = code_folder(paper, code_dir, rules)
    source = paper_source(paper)
    if not source:
        notes.append("no main .tex: title, authors and abstract were not read from the paper")
    names, origins = paper_authors(paper)
    names = [n for n in names if not re.fullmatch(r"A\d+", n)]
    places = affiliations(source)
    written = orcids_written(source)
    authors = []
    for n in names:
        a = {"name": n, **split_name(n), "affiliation": "; ".join(places.get(n, [])[:2]), "orcid": ""}
        if n in written:
            a["orcid"], a["orcid_source"] = written[n], "paper"
        authors.append(a)
    repo_rule = str(((rules.get("code") or {}) if isinstance(rules.get("code"), dict) else {}).get("repo") or "")
    repo_urls = []
    if re.match(r"\w+://|git@", repo_rule.strip()):
        repo_urls.append({"url": https(repo_rule), "source": "rules.json code.repo"})
    repo_urls += [{"url": u, "source": f"git remote of {code.name}"} for u in remotes(code)]
    seen, uniq = set(), []
    for r in repo_urls:
        if norm_url(r["url"]) not in seen:
            seen.add(norm_url(r["url"]))
            uniq.append(r)
    licenses = licenses_of(code, "") or (licenses_of(paper, "") if code != paper else [])
    sheet, crois = datasheet(paper), croissant(paper)
    data = []
    for url, where in ((sheet.get("location", ""), "datasheet.md Location"), (crois.get("url", ""), "croissant.json url")):
        if url and url.startswith(("http://", "https://")) and all(norm_url(url) != norm_url(d["url"]) for d in data):
            data.append({"url": url, "source": where, "license": sheet.get("license") or crois.get("license") or ""})
    version, released, version_from = version_of(code)
    title = title_of(source)
    facts = {
        "title": title, "abstract": abstract_of(source), "tex_keywords": tex_keywords(source),
        "authors": authors, "author_sources": sorted(set(origins)),
        "code_dir": str(code), "code_from": code_from,
        "repository": uniq[0]["url"] if uniq else "", "repositories": uniq,
        "license": licenses[0]["id"] if licenses else "", "license_source": licenses[0]["source"] if licenses else "",
        "version": version, "date_released": released, "version_from": version_from,
        "paper": own_ids(paper, rules), "datasets": data, "languages": languages(code),
        "links_in_paper": urls_in(source),
    }
    if not authors:
        notes.append("no author found in the \\author block, project.md or venue.md `Authors:`")
    if not uniq:
        notes.append("no repository URL: no git remote and no URL in rules.json code.repo")
    if not licenses:
        notes.append(f"no LICENSE file in {code}: the files carry no license")
    return facts, notes


# ORCID ----------------------------------------------------------------------------------

def words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]{3,}", text.casefold()) if w not in STOP}


def same_place(a: str, b: str) -> bool:
    """Two institution names share their distinctive words (half of the shorter one)."""
    x, y = words(a), words(b)
    return bool(x and y) and len(x & y) >= max(1, (min(len(x), len(y)) + 1) // 2)


def same_title(a: str, b: str) -> bool:
    x, y = words(a), words(b)
    return bool(x and y) and len(x & y) / max(len(x), len(y)) >= 0.8


def orcid_works(oid: str) -> list[str]:
    data = sources.fetch(f"{ORCID}/{oid}/works", headers=JSON, label="ORCID")
    titles = []
    for group in (data or {}).get("group") or []:
        for s in group.get("work-summary") or []:
            t = ((s.get("title") or {}).get("title") or {}).get("value")
            if t:
                titles.append(t)
    return titles


def find_orcid(author: dict, paper_title: str, ref_titles: list[str]) -> tuple[str, str]:
    """(iD, why) for the one ORCID record of this name whose institution matches the
    paper's affiliation or whose works include the paper or one of its references; else
    ("", why not)."""
    given, family = author["given-names"], author["family-names"]
    if not given or not family:
        return "", "no given and family name to search"
    q = f'given-names:"{given}" AND family-name:"{family}"'
    data = sources.fetch(f"{ORCID}/expanded-search/", {"q": q, "rows": 20}, headers=JSON, label="ORCID")
    if data is None:
        return "", "ORCID could not be reached"
    found = data.get("expanded-result") or []
    if not found:
        return "", "no ORCID record of this name"
    matches = []
    for r in found:
        places = r.get("institution-name") or []
        if author["affiliation"] and any(same_place(author["affiliation"], p) for p in places):
            matches.append((r["orcid-id"], f"institution {next(p for p in places if same_place(author['affiliation'], p))}"))
    if not matches:
        for r in found[:CANDIDATES]:
            titles = orcid_works(r["orcid-id"])
            hit = next((t for t in titles if same_title(t, paper_title)), "") if paper_title else ""
            hit = hit or next((t for t in titles for ref in ref_titles if same_title(t, ref)), "")
            if hit:
                matches.append((r["orcid-id"], f"work {hit}"))
    if len(matches) == 1:
        return matches[0][0], matches[0][1]
    if len(matches) > 1:
        return "", f"{len(matches)} ORCID records match; not chosen"
    return "", f"{len(found)} ORCID record(s) of this name, none with the paper's institution or works"


# Archive DOI ----------------------------------------------------------------------------

_canonical: dict[str, str] = {}


REPO_PATH = re.compile(r"^(?:www\.)?((?:github|gitlab)\.com/[^/\s]+/[^/\s#?]+)")


def repo_key(url: str) -> str:
    """github.com/owner/name of any URL inside a GitHub or GitLab repository, else the URL."""
    m = REPO_PATH.match(norm_url(https(url))) if url else None
    return re.sub(r"\.git$", "", m.group(1)) if m else norm_url(url)


def canonical(key: str) -> str:
    """The repository's current github.com/owner/name (GitHub follows renames), else key."""
    if not key.startswith("github.com/") or key.count("/") < 2:
        return key
    if key not in _canonical:
        try:
            info = github("https://" + key)
            _canonical[key] = f"github.com/{info['full_name']}".casefold() if info.get("full_name") else key
        except urllib.error.HTTPError:
            _canonical[key] = key                       # missing or private: the name as written
        except Exception:  # noqa: BLE001  unreachable: the name as written, and say so
            sources.UNAVAILABLE.add("GitHub")
            _canonical[key] = key
    return _canonical[key]


def points_to(urls: list[str], keys: set[str]) -> bool:
    """One of these URLs is in the repository (by its name then, or its current name)."""
    for u in urls:
        k = repo_key(u)
        if not k:
            continue
        if k in keys or any(norm_url(u).startswith(x + "/") for x in keys):
            return True
        if k.startswith("github.com/") and k.split("/")[1] in {x.split("/")[1] for x in keys if x.count("/") >= 2}:
            if canonical(k) in keys:
                return True
    return False


def related_urls(values: list[str]) -> list[str]:
    return [v if "://" in v else "https://" + v for v in values if v and ("github" in v or "gitlab" in v or "://" in v)]


def datacite_records(keys: set[str], names: list[str], title: str) -> list[dict]:
    """DataCite DOIs whose related identifiers point to the repository; with no repository,
    the ones titled like the paper."""
    queries = [f'"{k.split("/", 1)[1]}"' for k in sorted(keys) if k.startswith("github.com/")]
    queries += [f'titles.title:"{n}"' for n in names]
    if not keys and title:
        queries.append(f'titles.title:"{title}"')
    out = []
    for query in dict.fromkeys(queries):
        data = sources.fetch(DATACITE, {"query": query, "page[size]": 25}, headers=JSON, label="DataCite")
        for d in (data or {}).get("data") or []:
            a = d.get("attributes") or {}
            rel = a.get("relatedIdentifiers") or []
            titles = [t.get("title", "") for t in a.get("titles") or []]
            hit = points_to(related_urls([r.get("relatedIdentifier", "") for r in rel] + [a.get("url") or ""]), keys) \
                if keys else any(same_title(t, title) for t in titles)
            if hit:
                concept = next((r.get("relatedIdentifier", "") for r in rel if r.get("relationType") == "IsVersionOf"
                                and r.get("relatedIdentifierType") == "DOI"), "")
                out.append({"doi": a.get("doi", "").lower(), "concept": concept.lower(),
                            "title": titles[0] if titles else "", "version": a.get("version") or "",
                            "year": a.get("publicationYear"), "source": "DataCite"})
    return out


def zenodo_records(keys: set[str], names: list[str]) -> list[dict]:
    """Zenodo records whose related identifiers point to the repository."""
    out = []
    full = [k.split("/", 1)[1] for k in sorted(keys) if k.startswith("github.com/")]
    for query in dict.fromkeys([f'title:"{f}"' for f in full] + [f'title:"{n}"' for n in names]):
        data = sources.fetch(ZENODO, {"q": query, "size": 25, "sort": "mostrecent"}, headers=JSON, label="Zenodo")
        for r in ((data or {}).get("hits") or {}).get("hits") or []:
            meta = r.get("metadata") or {}
            if points_to(related_urls([x.get("identifier", "") for x in meta.get("related_identifiers") or []]), keys):
                out.append({"doi": (r.get("doi") or "").lower(), "concept": (r.get("conceptdoi") or "").lower(),
                            "title": meta.get("title", ""), "version": meta.get("version") or "",
                            "year": (meta.get("publication_date") or "")[:4], "source": "Zenodo"})
    return out


def resolves(doi: str) -> bool | None:
    """True when doi.org knows the DOI, False when it does not, None when unreachable."""
    data = sources.fetch(HANDLE + urllib.parse.quote(doi, safe="/"), label="doi.org")
    if data is None:
        return None if "doi.org" in sources.UNAVAILABLE else False
    return data.get("responseCode") == 1


def archive(repo: str, title: str) -> dict:
    """The archive record of the repository: the concept DOI (all versions) when there is
    one, the newest version DOI otherwise, with whether it resolves."""
    key = repo_key(repo) if repo else ""
    keys = {k for k in (key, canonical(key)) if k}
    names = list(dict.fromkeys(k.rsplit("/", 1)[-1] for k in keys if k.count("/") >= 2))
    found = zenodo_records(keys, names) if keys else []
    found += datacite_records(keys, names, title)
    found = [f for f in found if f["doi"]]
    if not found:
        return {}
    best = found[0]
    doi = best["concept"] or best["doi"]
    versions = sorted({f["doi"] for f in found} - {doi})
    return {"doi": doi, "version_doi": best["doi"] if best["doi"] != doi else "", "title": best["title"],
            "version": best["version"], "year": best["year"], "source": best["source"], "versions": versions,
            "resolves": resolves(doi)}


def cited(paper: Path, source: str, dois: list[str]) -> str:
    """The file (main.tex or a .bib) that names one of these DOIs, or ""."""
    places = [("main.tex", source)] + [(p.name, p.read_text(encoding="utf-8", errors="ignore"))
                                       for p in sorted(paper.glob("*.bib"))]
    for name, text in places:
        low = text.casefold()
        if any(d and d.casefold() in low for d in dois):
            return name
    return ""


def bib_titles(paper: Path) -> list[str]:
    titles = []
    for p in sorted(paper.glob("*.bib")):
        titles += [clean(t) for t in re.findall(r"\btitle\s*=\s*[{\"]((?:[^{}]|\{[^{}]*\})*)[}\"]",
                                                p.read_text(encoding="utf-8", errors="ignore"), re.I)]
    return titles[:40]


def own_record(ids: dict, title: str) -> tuple[dict, str]:
    """The paper's own arXiv id or DOI, kept only when its record carries the paper's title."""
    if not ids:
        return {}, ""
    found = sources.arxiv_id(ids["arxiv"]) if ids.get("arxiv") else sources.crossref_doi(ids["doi"])
    if not found:
        return ids, "the paper's own record could not be read; its id is kept as written"
    if title and not same_title(found[0]["title"], title):
        return {}, (f"{'arXiv:' + ids['arxiv'] if ids.get('arxiv') else ids['doi']} is the record of "
                    f"\"{found[0]['title']}\", not of this paper; it was left out")
    return {**ids, "year": found[0].get("year")}, ""


__all__ = ["archive", "bib_titles", "cited", "find_orcid", "gather", "own_record", "resolves", "split_name"]
