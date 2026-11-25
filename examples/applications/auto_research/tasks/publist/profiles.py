"""Who the author is and what each source lists for them: OpenAlex, ORCID's public works
and DBLP; then the links (PDF, code) and abstracts the entries need. Plain code, no
model calls.

Every request goes through citations.sources.fetch, so a source that fails is named in
sources.UNAVAILABLE (the run then reports PASS_WITH_NOTES) and tests replace `fetch`
alone. DBLP is read through its SPARQL endpoint, the interface it offers scripts (its web
pages and search API answer scripts with a bot check); if that ever answers with a bot
check too, DBLP is skipped for the run and the report says so; it is never bypassed.
"""

import json
import re
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

from auto_research.tasks.baselinecode.find import links as github_links
from auto_research.tasks.citations import sources
from auto_research.tasks.citations.refs import parse_bib
from auto_research.tasks.feedback import openalex
from auto_research.tasks.feedback.people import norm, same_name
from auto_research.tasks.survey.search import ARXIV_ID, abstract_text, norm_title

API = openalex.API
WORK_FIELDS = ("id,doi,title,publication_year,type,primary_location,best_oa_location,locations,authorships,"
               "abstract_inverted_index")
ORCID_ID = re.compile(r"(\d{4}-\d{4}-\d{4}-\d{3}[\dX])", re.I)
OPENALEX_ID = re.compile(r"(?:https?://openalex\.org/)?(A\d{4,})", re.I)
DBLP_PID = re.compile(r"(?:https?://dblp\.org/pid/)?([a-z0-9]+/[A-Za-z0-9-]+?)(?:\.html|\.bib|\.xml)?", re.I)
BOT_CHECK = re.compile(r"not a bot|anubis|captcha|challenge-platform|cf-chl", re.I)
WORK_PAGES = 10             # 200 works a page
SAMPLE = 100                # works read per candidate profile to know its coauthors and places
CANDIDATES = 5              # name matches whose coauthors are read
DBLP_CANDIDATES = 3
CODE_LOOKUPS = 200          # arXiv ids whose Hugging Face Papers page is read
ARXIV_GAP = 3.1
THREADS = 8
PREPRINT = re.compile(r"arxiv|\bcorr\b|ssrn|research square|biorxiv|medrxiv|chemrxiv|techrxiv|preprints\.org|"
                      r"openreview|engrxiv|psyarxiv|osf preprints", re.I)


# The author --------------------------------------------------------------------------------

def kind_of(author: str) -> tuple[str, str]:
    """('orcid' | 'openalex' | 'dblp' | 'name', the value) for an author input."""
    text = author.strip()
    m = ORCID_ID.search(text)
    if m and (len(text) <= 40 or "orcid.org" in text):
        return "orcid", m.group(1).upper()
    m = OPENALEX_ID.fullmatch(text)
    if m:
        return "openalex", m.group(1).upper()
    m = DBLP_PID.fullmatch(text)
    if m and " " not in text and "/" in text:
        return "dblp", m.group(1)
    return "name", " ".join(text.split())


def orcid_name(orcid: str) -> str:
    d = sources.fetch(f"https://pub.orcid.org/v3.0/{orcid}/person", headers={"Accept": "application/json"},
                      label="ORCID")
    name = (d or {}).get("name") or {}
    credit = ((name.get("credit-name") or {}).get("value") or "").strip()
    given = ((name.get("given-names") or {}).get("value") or "").strip()
    family = ((name.get("family-name") or {}).get("value") or "").strip()
    return credit or " ".join(p for p in (given, family) if p)


def profiles_by_orcid(orcid: str) -> list[dict]:
    d = sources.fetch(API + "/authors", {"filter": f"orcid:{orcid}", "per-page": 10,
                                         "select": openalex.AUTHOR_FIELDS, **openalex._key()}, label="OpenAlex")
    return [openalex._author(a) for a in (d or {}).get("results") or [] if a.get("id")]


def coauthor_sample(author_id: str) -> dict:
    """Coauthor names and institution names on up to SAMPLE works of one profile."""
    d = sources.fetch(API + "/works", {"filter": f"author.id:{author_id}", "per-page": SAMPLE,
                                       "select": "id,title,authorships", **openalex._key()}, label="OpenAlex")
    names, places, titles = set(), set(), set()
    for w in (d or {}).get("results") or []:
        titles.add(norm_title(w.get("title") or ""))
        for a in w.get("authorships") or []:
            person = a.get("author") or {}
            if openalex.short(person.get("id")) == author_id:
                places |= {i.get("display_name") or "" for i in a.get("institutions") or []}
            elif person.get("display_name"):
                names.add(person["display_name"])
    return {"coauthors": sorted(names), "places": sorted(p for p in places if p), "titles": titles}


def overlap(candidate: dict, sample: dict, coauthors: list[str], source_text: str, titles: set[str]) -> dict:
    """What a candidate profile shares with the current paper: coauthors, an affiliation
    named in the paper's source, and (for a DBLP pid) titles of the DBLP works."""
    shared = sorted({c for c in coauthors for n in sample["coauthors"] if same_name(c, n)})
    text = norm(source_text)
    places = sorted({p for p in sample["places"] + [i["name"] for i in candidate["institutions"]]
                     if len(norm(p)) >= 6 and norm(p) in text})
    common = len(titles & sample["titles"]) if titles else 0
    return {"coauthors": shared, "places": places, "titles": common,
            "score": 2 * len(shared) + len(places) + common}


def choose_profile(name: str, hits: list[dict], coauthors: list[str], source_text: str,
                   titles: set[str] | None = None) -> tuple[list[dict], dict]:
    """The OpenAlex profile for a name: the candidate sharing most with the current paper
    (coauthors, affiliation), else the one with most works. Returns ([chosen], decision)."""
    matches = [h for h in hits if same_name(name, h["name"]) or any(same_name(name, n) for n in h["alternatives"])]
    if not matches:
        return [], {"basis": "none", "alternatives": [], "uncertain": False}
    matches.sort(key=lambda h: (-h["works_count"], -h["cited_by_count"]))
    scored = []
    look = coauthors or source_text.strip() or titles
    for h in matches[:CANDIDATES]:
        shared = overlap(h, coauthor_sample(h["id"]), coauthors, source_text, titles or set()) if look else \
            {"coauthors": [], "places": [], "titles": 0, "score": 0}
        scored.append((h, shared))
    best_score = max(s["score"] for _, s in scored)
    if best_score > 0:
        chosen, why = max(scored, key=lambda x: (x[1]["score"], x[0]["works_count"]))
        basis = "overlap"
    else:
        chosen, why = scored[0]
        basis = "most works"
    others = [h for h in matches if h["id"] != chosen["id"]]
    # unsure when nothing tied the choice to the paper and another profile is not much smaller
    uncertain = basis == "most works" and any(h["works_count"] * 3 >= max(chosen["works_count"], 1) for h in others)
    alternatives = [{"id": h["id"], "name": h["name"], "works": h["works_count"], "orcid": h["orcid"],
                     "institutions": [i["name"] for i in h["institutions"]][:2]} for h in others]
    return [chosen], {"basis": basis, "shared": why, "alternatives": alternatives, "uncertain": uncertain}


# Works -------------------------------------------------------------------------------------

def clean_doi(doi: str) -> str:
    doi = (doi or "").strip().lower()
    return re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:)", "", doi)


def arxiv_of(*texts: str) -> str:
    for t in texts:
        m = ARXIV_ID.search(t or "")
        if m:
            return re.sub(r"v\d+$", "", m.group(1))
    return ""


def split_doi(doi: str) -> tuple[str, str]:
    """(doi, arxiv): an arXiv DOI (10.48550/arxiv.X) is an arXiv id, not a DOI."""
    doi = clean_doi(doi)
    if doi.startswith("10.48550/arxiv."):
        return "", re.sub(r"v\d+$", "", doi.split("arxiv.", 1)[1])
    return doi, ""


def record(source: str, rid: str, title: str, year, venue: str = "", kind: str = "", doi: str = "",
           arxiv: str = "", authors=(), url: str = "", pdf: str = "", abstract: str = "", oa: str = "") -> dict:
    doi, from_doi = split_doi(doi)
    return {"source": source, "rid": rid, "title": " ".join(str(title or "").split()).rstrip("."),
            "year": int(year) if str(year or "").isdigit() else 0, "venue": tidy_venue(venue), "type": kind or "",
            "doi": doi, "arxiv": arxiv or from_doi, "authors": [a for a in authors if a], "url": url or "",
            "pdf": pdf or "", "abstract": " ".join((abstract or "").split()), "openalex": oa}


def tidy_venue(venue: str) -> str:
    venue = " ".join(str(venue or "").split())
    if "/" in venue and " - " in venue:          # "IEEE ... Recognition/Proceedings - IEEE ..."
        venue = venue.split("/", 1)[0]
    return venue


def is_preprint(r: dict) -> bool:
    return r["type"] in ("preprint", "posted-content", "working-paper") or bool(PREPRINT.search(r["venue"])) \
        or (not r["venue"] and bool(r["arxiv"]) and not r["doi"])


def openalex_record(w: dict) -> dict:
    urls = [(l or {}).get("landing_page_url") or "" for l in w.get("locations") or []]
    loc = w.get("primary_location") or {}
    best = w.get("best_oa_location") or {}
    venue = (loc.get("source") or {}).get("display_name") or ""
    authors = [(a.get("author") or {}).get("display_name") or "" for a in w.get("authorships") or []]
    oa = openalex.short(w.get("id"))
    r = record("openalex", "openalex:" + oa, w.get("title"), w.get("publication_year"), venue, w.get("type") or "",
               w.get("doi") or "", "", authors, loc.get("landing_page_url") or "", best.get("pdf_url") or "",
               abstract_text(w.get("abstract_inverted_index")), oa)
    r["arxiv"] = r["arxiv"] or arxiv_of(*urls)
    r["places"] = {openalex.short((a.get("author") or {}).get("id")):
                   [i.get("display_name") or "" for i in a.get("institutions") or [] if i.get("display_name")]
                   for a in w.get("authorships") or [] if (a.get("author") or {}).get("id")}
    return r


def openalex_works(author_ids: list[str]) -> list[dict]:
    """Every work of these OpenAlex profiles."""
    out, cursor = [], "*"
    for _ in range(WORK_PAGES):
        d = sources.fetch(API + "/works", {"filter": "author.id:" + "|".join(author_ids), "per-page": 200,
                                           "cursor": cursor, "select": WORK_FIELDS, **openalex._key()},
                          label="OpenAlex")
        if not d:
            break
        out += [openalex_record(w) for w in d.get("results") or [] if w.get("id") and w.get("title")]
        cursor = (d.get("meta") or {}).get("next_cursor")
        if not cursor or not d.get("results"):
            break
    return out


def by_doi(dois: list[str]) -> dict[str, dict]:
    """OpenAlex records by DOI, fifty per request: authors, abstract and PDF for works
    another source lists."""
    out = {}
    for i in range(0, len(dois), 50):
        part = dois[i:i + 50]
        d = sources.fetch(API + "/works", {"filter": "doi:" + "|".join(part), "per-page": 50,
                                           "select": WORK_FIELDS, **openalex._key()}, label="OpenAlex")
        for w in (d or {}).get("results") or []:
            r = openalex_record(w)
            if r["doi"]:
                out[r["doi"]] = r
    return out


def orcid_works(orcid: str) -> list[dict] | None:
    """The public works of an ORCID record (one per group); None when ORCID did not answer."""
    d = sources.fetch(f"https://pub.orcid.org/v3.0/{orcid}/works", headers={"Accept": "application/json"},
                      label="ORCID")
    if d is None:
        return None
    out = []
    for g in d.get("group") or []:
        s = (g.get("work-summary") or [{}])[0]
        ids = {}
        for e in ((g.get("external-ids") or {}).get("external-id") or []):
            ids.setdefault((e.get("external-id-type") or "").lower(), e.get("external-id-value") or "")
        title = (((s.get("title") or {}).get("title") or {}).get("value")) or ""
        year = (((s.get("publication-date") or {}).get("year") or {}).get("value")) or ""
        venue = ((s.get("journal-title") or {}).get("value")) or ""
        url = ((s.get("url") or {}).get("value")) or ""
        arxiv = re.sub(r"^arxiv:", "", ids.get("arxiv", ""), flags=re.I)
        out.append(record("orcid", f"orcid:{s.get('put-code', len(out))}", title, year, venue,
                          ORCID_KINDS.get(s.get("type") or "", s.get("type") or ""), ids.get("doi", ""),
                          arxiv_of(arxiv, url) if (arxiv or "arxiv" in url) else "", [], url))
    return [r for r in out if r["title"]]


ORCID_KINDS = {"journal-article": "article", "conference-paper": "conference-paper", "preprint": "preprint",
               "book-chapter": "book-chapter", "book": "book", "dissertation-thesis": "dissertation",
               "data-set": "dataset", "review": "review", "other": "other"}


class DblpBlocked(Exception):
    """DBLP answered with a bot check."""


SPARQL = "https://sparql.dblp.org/sparql"
DBLP_TYPES = {"Article": "article", "Inproceedings": "conference-paper", "Informal": "preprint",
              "Incollection": "book-chapter", "Book": "book", "Editorship": "book"}
PREFIX = "PREFIX dblp: <https://dblp.org/rdf/schema#>\n"


def sparql(query: str) -> list[dict]:
    """Rows of a query to DBLP's SPARQL endpoint (its official API for scripts; the web
    pages and search API answer scripts with a bot check), each {variable: value}."""
    body = sources.fetch(SPARQL, {"query": PREFIX + query}, as_json=False, tries=2, label="DBLP",
                         headers={"Accept": "application/sparql-results+json"})
    if body and not body.lstrip().startswith("{"):
        if BOT_CHECK.search(body[:4000]):
            sources.UNAVAILABLE.discard("DBLP")
            raise DblpBlocked()
        return []
    try:
        rows = ((json.loads(body) if body else {}).get("results") or {}).get("bindings") or []
    except ValueError:
        return []
    return [{k: v.get("value", "") for k, v in row.items()} for row in rows]


def literal(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def dblp_name(pid: str) -> str:
    rows = sparql(f"SELECT ?name WHERE {{ <https://dblp.org/pid/{pid}> dblp:primaryCreatorName ?name }} LIMIT 1")
    return re.sub(r"\s+\d{4}$", "", rows[0]["name"]) if rows else ""


def dblp_works(pid: str) -> list[dict]:
    rows = sparql(f"""SELECT ?publ (SAMPLE(?ti) AS ?title) (SAMPLE(?y) AS ?year) (MIN(STR(?d)) AS ?doi)
        (SAMPLE(?v) AS ?venue) (GROUP_CONCAT(DISTINCT STR(?t); separator=" ") AS ?types)
        (GROUP_CONCAT(DISTINCT CONCAT(STR(?o), ":", ?an); separator="|") AS ?authors)
        WHERE {{ ?publ dblp:authoredBy <https://dblp.org/pid/{pid}> ; dblp:title ?ti ; dblp:yearOfPublication ?y ;
                 a ?t .
          OPTIONAL {{ ?publ dblp:doi ?d }} OPTIONAL {{ ?publ dblp:publishedIn ?v }}
          OPTIONAL {{ ?publ dblp:hasSignature ?s . ?s dblp:signatureOrdinal ?o ; dblp:signatureDblpName ?an }} }}
        GROUP BY ?publ""")
    # one row per record: a record with two DOIs or venues is still one work
    out = []
    for row in rows:
        kinds = [DBLP_TYPES[t.rsplit("#", 1)[-1]] for t in row.get("types", "").split()
                 if t.rsplit("#", 1)[-1] in DBLP_TYPES]
        ordered = sorted((part.split(":", 1) for part in row.get("authors", "").split("|") if ":" in part),
                         key=lambda p: int(p[0]) if p[0].isdigit() else 0)
        authors = [re.sub(r"\s+\d{4}$", "", name) for _, name in ordered]
        doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", row.get("doi", ""), flags=re.I)
        key = row["publ"].rsplit("/rec/", 1)[-1]
        out.append(record("dblp", "dblp:" + key, row["title"], row["year"], row.get("venue", ""),
                          kinds[0] if kinds else "other", doi, "", authors, row["publ"]))
    return [r for r in out if r["title"]]


def dblp_search(name: str) -> list[dict]:
    rows = sparql(f"SELECT DISTINCT ?p ?name WHERE {{ ?p dblp:creatorName {literal(name)} ; "
                  "dblp:primaryCreatorName ?name } LIMIT 20")
    out = []
    for row in rows:
        m = re.search(r"dblp\.org/pid/(.+)$", row.get("p", ""))
        if m:
            out.append({"pid": m.group(1), "name": re.sub(r"\s+\d{4}$", "", row.get("name", ""))})
    return out


def dblp_for(name: str, pid: str, titles: set[str], notes: list[str]) -> tuple[list[dict] | None, str]:
    """The DBLP works of the author: the pid given, else the name match whose works share
    most titles with the other sources. None when DBLP was skipped."""
    try:
        if pid:
            return dblp_works(pid), pid
        hits = [h for h in dblp_search(name) if same_name(name, h["name"])]
        best, best_works, best_common = "", [], -1
        for h in hits[:DBLP_CANDIDATES]:
            works = dblp_works(h["pid"])
            common = len({norm_title(w["title"]) for w in works} & titles)
            if common > best_common:
                best, best_works, best_common = h["pid"], works, common
        if not hits:
            notes.append(f"DBLP has no author named {name}")
            return [], ""
        if titles and best_common == 0:
            notes.append(f"DBLP: none of the {len(hits)} authors named {name} shares a title with OpenAlex or ORCID; "
                         "DBLP was not used")
            return [], ""
        if len(hits) > 1:
            notes.append(f"DBLP: {len(hits)} authors named {name}; took {best} ({best_common} shared titles)")
        return best_works, best
    except DblpBlocked:
        notes.append("DBLP answered with a bot check, so DBLP was skipped (not bypassed)")
        return None, ""


# Abstracts and links -----------------------------------------------------------------------

def arxiv_abstracts(ids: list[str]) -> dict[str, str]:
    """Abstracts of arXiv ids from the arXiv API, fifty per request."""
    out = {}
    atom = "{http://www.w3.org/2005/Atom}"
    for i in range(0, len(ids), 50):
        if i:
            time.sleep(ARXIV_GAP)
        body = sources.fetch("https://export.arxiv.org/api/query",
                             {"id_list": ",".join(ids[i:i + 50]), "max_results": 50}, as_json=False, label="arXiv")
        try:
            entries = list(ET.fromstring(body).iter(atom + "entry")) if body else []
        except ET.ParseError:
            continue
        for e in entries:
            aid = arxiv_of(e.findtext(atom + "id") or "")
            summary = " ".join((e.findtext(atom + "summary") or "").split())
            if aid and summary:
                out[aid] = summary
    return out


def hf_repo(arxiv: str) -> str:
    d = sources.fetch(f"https://huggingface.co/api/papers/{arxiv}", tries=2, label="Hugging Face Papers")
    repo = (d or {}).get("githubRepo") if isinstance(d, dict) else ""
    return repo or ""


def code_links(entries: list[dict]) -> None:
    """entry["code"]: Hugging Face Papers' githubRepo for its arXiv id, else a GitHub
    repository its abstract links."""
    ids = [e["arxiv"] for e in entries if e["arxiv"]][:CODE_LOOKUPS]
    with ThreadPoolExecutor(THREADS) as pool:
        repos = dict(zip(ids, pool.map(hf_repo, ids)))
    for e in entries:
        repo = repos.get(e["arxiv"], "")
        if not repo:
            found = github_links(e["abstract"])
            repo = f"https://github.com/{found[0]}" if found else ""
        e["code"] = repo


__all__ = ["choose_profile", "code_links", "dblp_for", "dblp_name", "kind_of", "openalex_works", "orcid_name",
           "orcid_works", "profiles_by_orcid"]
