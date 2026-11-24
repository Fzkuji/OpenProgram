"""References: parse a .bib file, normalize text, and score a candidate record.

Plain code: no model calls, no network.
"""

import difflib
import re
import unicodedata

# A reference is a plain dict: key, text, title, authors (surnames or full names),
# year (str), venue, doi, arxiv.


def parse_bib(text: str) -> list[dict]:
    refs = []
    for m in re.finditer(r"@(\w+)\s*\{\s*([^,\s]+)\s*,", text):
        if m.group(1).lower() in ("comment", "string", "preamble"):
            continue
        end = _close(text, m.end())
        fields, body = {}, text[m.end():end - 1]
        for f in re.finditer(r"(\w+)\s*=\s*", body):
            j = f.end()
            if j < len(body) and body[j] == "{":
                value = body[j + 1:_close(body, j + 1) - 1]
            elif j < len(body) and body[j] == '"':
                value = body[j + 1:body.find('"', j + 1)]
            else:
                value = re.match(r"[^,}\s]*", body[j:]).group(0)
            fields.setdefault(f.group(1).lower(), value)
        blob = " ".join(fields.get(k, "") for k in ("journal", "eprint", "url", "note", "volume"))
        arxiv = ARXIV_ID.search(blob)
        refs.append({
            "key": m.group(2), "text": text[m.start():end].strip(),
            "title": clean(fields.get("title", "")),
            "authors": bib_authors(fields.get("author", "")),
            "year": clean(fields.get("year", "")),
            "venue": clean(fields.get("booktitle") or fields.get("journal") or fields.get("publisher") or ""),
            "doi": clean(fields.get("doi", "")).lower(),
            "arxiv": arxiv.group(1) if arxiv else "",
        })
    return refs


def _close(text: str, i: int) -> int:
    depth = 1
    while i < len(text) and depth:
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        i += 1
    return i


ARXIV_ID = re.compile(r"(\d{4}\.\d{4,5})(?:v\d+)?")
DOI = re.compile(r"\b(10\.\d{4,9}/[^\s,;\"'<>]+[^\s,;\"'<>.])")


def clean(s: str) -> str:
    s = re.sub(r"\\[a-zA-Z]+\s*", "", s or "")
    return re.sub(r"\s+", " ", s.replace("{", "").replace("}", "")).strip()


def words(s: str) -> list[str]:
    s = re.sub(r"[‐-―−]", "-", clean(s))          # Unicode dashes split words too
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9 ]", " ", s.lower()).split()


def bib_authors(field: str) -> list[str]:
    """'Last, First and First Last and others' -> full names, 'others' dropped."""
    names = []
    for a in re.split(r"\s+and\s+", clean(field)):
        a = a.strip()
        if a and a.lower() != "others":
            names.append(" ".join(reversed([p.strip() for p in a.split(",", 1)])) if "," in a else a)
    return names


ET_AL = re.compile(r"^\s*(et\.?\s*al\.?|others|and others)\s*$", re.I)


def surname(name: str) -> str:
    w = words(name.split(",")[0] if "," in name else name)    # "Wang, Z." or "Zora Wang"
    return w[-1] if w else ""


# ---------- matching ----------

AUTHENTIC_TITLE = 0.93
UNSURE_TITLE = 0.80


def title_sim(a: str, b: str) -> float:
    a, b = " ".join(words(a)), " ".join(words(b))
    return difflib.SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def author_overlap(cited: list[str], found: list[str]) -> float | None:
    """Share of cited surnames that appear among the record's author names."""
    cited = [s for s in (surname(n) for n in cited if not ET_AL.match(n)) if s]
    if not cited:
        return None
    pool = {w for name in found for w in words(name)}
    return sum(s in pool for s in cited) / len(cited)


def year_gap(ref: dict, rec: dict) -> int | None:
    y, r = str(ref.get("year") or ""), rec.get("year")
    return abs(int(y[:4]) - int(r)) if y[:4].isdigit() and r else None


def score(ref: dict, rec: dict) -> dict:
    t = title_sim(ref["title"], rec["title"])
    a = author_overlap(ref["authors"], rec["authors"])
    gap = year_gap(ref, rec)
    total = t + 0.25 * (0.5 if a is None else a) - (0.05 if gap and gap >= 2 else 0)
    return {"total": total, "title": t, "authors": a, "year_gap": gap}


def assess(ref: dict, records: list[dict]) -> dict:
    """Pick the best record and give the deterministic verdict with its notices."""
    if not ref["title"]:
        return {"assessment": "invalid", "confidence": 0.0, "notices": [], "match": None,
                "note": "No title could be read from this reference."}
    ranked = sorted(records, key=lambda r: score(ref, r)["total"], reverse=True)
    if not ranked:
        return {"assessment": "inauthentic", "confidence": 0.0, "notices": [], "match": None,
                "note": "No source returned a candidate."}
    best, s = ranked[0], score(ref, ranked[0])
    if s["title"] >= AUTHENTIC_TITLE and (s["authors"] is None or s["authors"] >= 0.5):
        verdict = "authentic"
        conf = s["title"] * (0.7 + 0.3 * (1 if s["authors"] is None else s["authors"]))
    elif s["title"] >= UNSURE_TITLE:
        verdict, conf = "unsure", 0.8 * s["title"]
    else:
        verdict, conf = "inauthentic", 0.0
    return {"assessment": verdict, "confidence": round(min(conf, 1.0), 2),
            "notices": notices(ref, ranked, s) if verdict != "inauthentic" else [],
            "match": dict(best, title_sim=round(s["title"], 3),
                          author_overlap=None if s["authors"] is None else round(s["authors"], 2)),
            "sources": sorted({r["source"] for r in ranked if title_sim(ref["title"], r["title"]) >= AUTHENTIC_TITLE}),
            "note": ""}


def notices(ref: dict, ranked: list[dict], s: dict) -> list[str]:
    out = []
    if s["year_gap"] is not None and s["year_gap"] >= 2:
        out.append("year")
    if s["authors"] is not None and s["authors"] < 1:
        out.append("authors")
    venue = ref["venue"].lower()
    same = [r for r in ranked if title_sim(ref["title"], r["title"]) >= AUTHENTIC_TITLE]
    if venue and "arxiv" not in venue and any(is_preprint(r) for r in same) \
            and all(is_preprint(r) or not r.get("venue") for r in same):
        out.append("venue_unconfirmed")
    return out


def is_preprint(rec: dict) -> bool:
    """An arXiv or unaccepted OpenReview record. A record without a venue (books,
    reports) says nothing either way and does not count as a preprint."""
    v = (rec.get("venue") or "").lower()
    return rec["source"] == "arxiv" or "arxiv" in v or "corr" == v.split(" ")[0] or "submitted" in v or "rejected" in v
