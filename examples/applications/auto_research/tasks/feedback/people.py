"""Who could read the paper before submission: the paper's authors, the candidates from
similar works, their scores and the conflict-of-interest check. Plain code, no model calls."""

import re
import unicodedata
from collections import Counter
from pathlib import Path

from auto_research.tasks.scouting.common import latex_text, project_field, rules
from auto_research.tasks.submission import common as tex
from auto_research.tasks.venue import facts

from .openalex import short

AUTHOR = re.compile(r"\\(?:author|IEEEauthorblockN)\s*(?:\[[^\]]*\])?\s*\{((?:[^{}]|\{(?:[^{}]|\{[^{}]*\})*\})*)\}")
DROP_CMD = re.compile(r"\\(?:thanks|footnote|footnotemark|textsuperscript|affiliation|affil|email|inst|orcid|"
                      r"IEEEauthorrefmark|href|url)\s*(?:\[[^\]]*\])?\s*(?:\{(?:[^{}]|\{[^{}]*\})*\})?")
NOT_A_NAME = re.compile(r"anonymous|universit|institut|department|school|college|laborator|inc\b|corp|"
                        r"@|\d", re.I)
OPENALEX_ID = re.compile(r"(?:openalex\.org/)?(A\d+)\b", re.I)
STOP = set("""a an and are as at be been but by can cheapest do does for from has have how in into is it its
more most much new not of on or our over show shows so such than that the their them these this those to
under up use used using via was we were what when where which while who why will with without yet way ways
make makes made both each other only very well""".split())

# Score weights: closeness to the paper matters most; seniority and recent activity say
# whether the person can and will give useful feedback now.
WEIGHTS = {"closeness": 0.45, "recency": 0.15, "seniority": 0.20, "activity": 0.20}
CLOSE_FULL = 3.0             # three similar works make closeness 1
RECENCY_YEARS = 10
H_FULL, WORKS_FULL, ACTIVE_FULL = 30, 100, 15
ACTIVE_YEARS = 3
COAUTHOR_YEARS = 5
SMALL_TEAM = 10              # a work with more authors gives each of them 10/n of a work


def norm(name: str) -> str:
    s = unicodedata.normalize("NFKD", name or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).casefold()
    return " ".join(re.sub(r"[^a-z\s-]", " ", s).replace("-", " ").split())


def same_name(a: str, b: str) -> bool:
    """Equal names, or the same surname and first initial (J. Devlin is Jacob Devlin)."""
    a, b = norm(a).split(), norm(b).split()
    if not a or not b:
        return False
    return a == b or (len(a) > 1 and len(b) > 1 and a[-1] == b[-1] and a[0][0] == b[0][0]
                      and (len(a[0]) == 1 or len(b[0]) == 1))


def latex_authors(source: str) -> list[str]:
    """The names of the \\author blocks (and IEEE author blocks) of the LaTeX source."""
    names = []
    for m in AUTHOR.finditer(source):
        block = DROP_CMD.sub(" ", m.group(1))
        block = re.sub(r"\$[^$]*\$", " ", block)
        for part in re.split(r"\\(?:and|And|AND)\b|\\quad|\\qquad|\\hspace\*?\{[^}]*\}", block):
            first = re.split(r"\\\\|\\newline|\\par\b", part)[0]
            for name in re.split(r",|;|\band\b", latex_text(first)):
                name = " ".join(name.replace("*", " ").split())
                if plausible(name):
                    names.append(name)
    return names


def plausible(name: str) -> bool:
    return 2 <= len(name.split()) <= 5 and len(name) <= 60 and not NOT_A_NAME.search(name)


def paper_authors(paper: Path) -> tuple[list[str], list[str]]:
    """The paper's authors as names or OpenAlex ids, and where they were read from: the
    LaTeX \\author block, project.md `Authors:`, venue.md `Authors:`, rules.json `required_authors`."""
    found: list[tuple[str, str]] = []
    try:
        main = tex.main_tex(paper)
        source = tex.sources(paper, main) if main else ""
    except (OSError, ValueError):
        source = ""
    found += [(n, "LaTeX \\author") for n in latex_authors(source)]
    for where, value in (("project.md Authors", project_field(paper, "Authors")),
                         ("venue.md Authors", facts.field_value(paper, "Authors"))):
        found += [(n.strip(), where) for n in re.split(r"[;,]|\band\b", value) if n.strip()]
    found += [(str(n).strip(), "rules.json required_authors") for n in rules(paper).get("required_authors") or []
              if "@" not in str(n)]
    names, origins, seen = [], [], set()
    for name, where in found:
        m = OPENALEX_ID.fullmatch(name.strip())
        key = m.group(1).upper() if m else norm(name)
        if not key or key in seen or not (m or plausible(name)):
            continue
        seen.add(key)
        names.append(m.group(1).upper() if m else name)
        origins.append(where)
    return names, origins


def resolve_author(name: str, hits: list[dict], pool: set[str]) -> tuple[dict | None, str]:
    """The OpenAlex profile of a paper author among search hits, and a note when unsure.
    A namesake who wrote one of the gathered works wins; otherwise the most prolific."""
    matches = [h for h in hits if same_name(name, h["name"]) or any(same_name(name, n) for n in h["alternatives"])]
    if not matches:
        return None, f"{name}: no OpenAlex author with this name"
    known = [h for h in matches if h["id"] in pool]
    chosen = max(known or matches, key=lambda h: (h["works_count"], h["cited_by_count"]))
    note = ""
    if len(matches) > 1 and not known:
        note = (f"{name}: {len(matches)} OpenAlex authors share this name; took {chosen['id']} "
                f"({chosen['works_count']} works). Write the OpenAlex id in Authors: to choose another")
    return chosen, note


def key_phrases(title: str, abstract: str, most: int = 3) -> list[str]:
    """Runs of two or three content words of the abstract, scored by how often their words
    occur in the title and abstract."""
    text = f"{title}. {abstract}".lower()
    words = re.findall(r"[a-z][a-z-]+", text)
    freq = Counter(w for w in words if w not in STOP)
    phrases: dict[str, float] = {}
    for chunk in re.split(r"[^a-z\s-]+", abstract.lower()):
        run: list[str] = []
        for w in chunk.split() + [""]:
            if w and w not in STOP and len(w) > 2:
                run.append(w)
                continue
            for i in range(len(run)):
                for n in (2, 3):
                    if i + n <= len(run):
                        p = " ".join(run[i:i + n])
                        phrases[p] = max(phrases.get(p, 0), sum(freq[x] for x in run[i:i + n]) / n)
            run = []
    out: list[str] = []
    for p, _ in sorted(phrases.items(), key=lambda kv: (-kv[1], -len(kv[0].split()))):
        if not any(set(p.split()) <= set(q.split()) or set(q.split()) <= set(p.split()) for q in out):
            out.append(p)
        if len(out) == most:
            break
    return out


def pool(works: list[dict], skip_title: str) -> dict[str, dict]:
    """Every author of the gathered works with the works they wrote and their weight: one
    work counts 1 (0.8 for a reference of the paper), split when the team is large."""
    out: dict[str, dict] = {}
    seen = set()
    for w in works:
        if w["id"] in seen or (skip_title and norm(w["title"]) == norm(skip_title)):
            continue
        seen.add(w["id"])
        n = len(w["authors"])
        weight = (0.8 if w["how"] == "reference" else 1.0) * (1.0 if n <= SMALL_TEAM else SMALL_TEAM / n)
        for a in w["authors"]:
            c = out.setdefault(a["id"], {"id": a["id"], "name": a["name"], "works": [], "weight": 0.0})
            if all(x["id"] != w["id"] for x in c["works"]):
                c["works"].append({k: w[k] for k in ("id", "title", "year", "how", "abstract")} | {"team": n})
                c["weight"] += weight
    return out


def shortlist(people: dict[str, dict], most: int) -> list[str]:
    ranked = sorted(people.values(), key=lambda c: (-c["weight"], -max((w["year"] for w in c["works"]), default=0)))
    return [c["id"] for c in ranked[:most]]


def score(c: dict, profile: dict, year: int) -> dict:
    newest = max((w["year"] for w in c["works"]), default=0)
    recent = sum(n for y, n in profile.get("by_year", {}).items() if year - ACTIVE_YEARS < y <= year)
    parts = {
        "closeness": min(c["weight"], CLOSE_FULL) / CLOSE_FULL,
        "recency": max(0.0, 1 - (year - newest) / RECENCY_YEARS) if newest else 0.0,
        "seniority": 0.7 * min(profile.get("h_index", 0), H_FULL) / H_FULL
                     + 0.3 * min(profile.get("works_count", 0), WORKS_FULL) / WORKS_FULL,
        "activity": min(recent, ACTIVE_FULL) / ACTIVE_FULL,
    }
    total = sum(WEIGHTS[k] * v for k, v in parts.items())
    return {"score": round(total, 3), "parts": {k: round(v, 3) for k, v in parts.items()},
            "similar_works": round(c["weight"], 2), "newest": newest, "recent_works": recent,
            "h_index": profile.get("h_index", 0), "works_count": profile.get("works_count", 0)}


def exclusions(exclude: str) -> tuple[set[str], list[str]]:
    ids, names = set(), []
    for item in re.split(r"[,;\n]", exclude or ""):
        item = item.strip()
        if not item:
            continue
        m = OPENALEX_ID.fullmatch(item) or (OPENALEX_ID.search(item) if "openalex.org" in item.lower() else None)
        if m:
            ids.add(m.group(1).upper())
        else:
            names.append(item)
    return ids, names


def conflicts(c: dict, profile: dict, authors: list[dict], coauthored: dict[str, list[dict]],
              exclude_ids: set[str], exclude_names: list[str], declined: set[str]) -> list[str]:
    """Why this person may not review the paper; empty when nothing stands in the way."""
    reasons = []
    if any(a["id"] == c["id"] for a in authors):
        return ["an author of the paper"]
    shared = coauthored.get(c["id"]) or []
    if shared:
        w = max(shared, key=lambda s: s["year"])
        more = f" and {len(shared) - 1} more" if len(shared) > 1 else ""
        reasons.append(f"co-authored with {w['with']} in the last {COAUTHOR_YEARS} years: "
                       f"\"{w['title']}\" ({w['year']}){more}")
    for a in authors:                              # one institution reason per paper author, the closest
        found = sorted((why != "same current institution", j["id"] != i["id"], f"{why} as {a['name']}: {j['name']}"
                        + (f" ({a['name']}: {i['name']})" if j["id"] != i["id"] else ""))
                       for i in a.get("institutions", []) for j in profile.get("institutions", [])
                       if (why := same_place(i, j)))
        if found:
            reasons.append(found[0][2])
    if c["id"] in exclude_ids:
        reasons.append("named in exclude")
    elif any(same_name(n, c["name"]) or any(same_name(n, x) for x in profile.get("alternatives", []))
             for n in exclude_names):
        reasons.append("named in exclude")
    if c["id"] in declined:
        reasons.append("declined earlier (workflow/feedback.csv)")
    return list(dict.fromkeys(reasons))


def same_place(a: dict, b: dict) -> str:
    """Whether two current institutions are one employer: the same OpenAlex institution, one
    inside the other (a lab of a university), or two companies of one group (by lineage)."""
    if a["id"] == b["id"]:
        return "same current institution"
    if a["id"] in b.get("lineage", []) or b["id"] in a.get("lineage", []):
        return "same current institution"
    if a.get("type") == b.get("type") == "company" and \
            (set(a.get("lineage", [])) - {a["id"]}) & (set(b.get("lineage", [])) - {b["id"]}):
        return "same company group"
    return ""


def coauthor_map(works: list[dict], authors: list[dict]) -> dict[str, list[dict]]:
    """Each co-author of a paper author (in the recent works) with the works they share."""
    mine = {a["id"]: a["name"] for a in authors}
    out: dict[str, list[dict]] = {}
    for w in works:
        ids = {a["id"] for a in w["authors"]}
        ours = [mine[i] for i in ids if i in mine]
        if not ours:
            continue
        for i in ids - set(mine):
            out.setdefault(i, []).append({"title": w["title"], "year": w["year"], "with": ", ".join(ours)})
    return out


def slug(name: str) -> str:
    s = "-".join(norm(name).split())
    return s or "reader"


def parse_count(count) -> int:
    try:
        n = int(float(str(count).strip() or "5"))
    except ValueError:
        raise ValueError("count must be a whole number from 1 to 20") from None
    if not 1 <= n <= 20:
        raise ValueError("count must be a whole number from 1 to 20")
    return n


__all__ = ["coauthor_map", "conflicts", "exclusions", "key_phrases", "latex_authors", "norm", "paper_authors",
           "parse_count", "pool", "resolve_author", "same_name", "score", "shortlist", "slug"]
