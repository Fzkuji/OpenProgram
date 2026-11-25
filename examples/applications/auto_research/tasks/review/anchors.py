"""Real reviews from OpenReview as calibration anchors for paper_review.

CycleReviewer and OpenReviewer are reviewer models trained on OpenReview reviews;
this task cannot train one, so it grounds the reviewers and the acceptance estimate in
real reviews instead: for the paper's title and abstract keywords it finds similar
submissions of the target venue family on OpenReview (API v2, public, no key) and keeps
their official reviews and decisions.

API shape (v2): every content field is {"value": ...}. /notes/search with
source=forum returns submissions; their content.venueid is the group itself when
accepted and <group>/Rejected_Submission when rejected (Withdrawn_Submission and
Desk_Rejected_Submission are left out). The same search with source=reply returns
replies, each with its forum; official reviews have an invitation ending in
/-/Official_Review and decisions one ending in /-/Decision. /notes?forum=<id> answers
scripted requests with a bot challenge (403), so replies are taken from the reply search
and joined to the submissions by forum. Only ICLR publishes the reviews of rejected
papers; NeurIPS and ICML publish those of accepted ones, so ICLR is always in the pool.

Plain code, no model calls. Any failure gives no anchors and names the source in
"unavailable"; the review never stops for it.
"""

import hashlib
import json
import math
import re
import statistics
from datetime import datetime
from pathlib import Path

from ..citations import sources

API = "https://api2.openreview.net/notes/search"
HEADERS = {"User-Agent": "openprogram-paper-review/0.1 (review calibration)"}
PER_GROUP = 5            # similar submissions kept per venue-year
YEARS = 2                # latest years with reviews and decisions, per conference
SUBMISSIONS = 25         # submissions searched per venue-year
REPLIES = 200            # replies searched per venue-year
TEXT = 500               # characters kept of each review field
# Venue families with public reviews on OpenReview, and their conferences.
CONFERENCES = {"ICLR/NeurIPS/ICML": ("ICLR", "NeurIPS", "ICML")}
PREFIX = {"ICLR": "ICLR.cc", "NeurIPS": "NeurIPS.cc", "ICML": "ICML.cc"}
STOP = set("""a an and are as at be been but by can do does for from has have how in into is it its more
most new not of on or our over such than that the their these this those through to towards under
using via we what when which while with without paper propose proposed show shows method methods approach
results based model models""".split())


class Unavailable(Exception):
    pass


def fetch(url: str, params: dict):
    """One API request; None when it fails. Tests replace this."""
    return sources.fetch(url, params, tries=2, headers=HEADERS)


def group_scale(conference: str, year: int) -> tuple[int, int]:
    """The rating scale of one venue-year's review form."""
    if conference == "NeurIPS" and year >= 2025:
        return 1, 6
    if conference == "ICML" and year >= 2025:
        return 1, 5                       # overall_recommendation
    if conference == "ICLR" and year >= 2026:
        return 0, 10
    return 1, 10


# ---- the query ---------------------------------------------------------------

def _braced(text: str, start: int) -> str:
    depth = 1
    for i in range(start, len(text)):
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        if depth == 0:
            return text[start:i]
    return text[start:]


def _plain(latex: str) -> str:
    latex = re.sub(r"\\(?:cite\w*|ref|label|footnote|thanks|url)\{[^}]*\}", " ", latex)
    latex = re.sub(r"\\[A-Za-z]+\*?", " ", latex)
    return re.sub(r"\s+", " ", re.sub(r"[{}$~\\]", " ", latex)).strip()


def title_and_abstract(source: str) -> tuple[str, str]:
    """The \\title and the abstract environment of the flattened LaTeX source, as plain text."""
    m = re.search(r"\\title(?:\[[^\]]*\])?\{", source)
    title = _plain(_braced(source, m.end())) if m else ""
    m = re.search(r"\\begin\{abstract\}(.*?)\\end\{abstract\}", source, re.S)
    return title, _plain(m.group(1)) if m else ""


def query_terms(title: str, abstract: str, most: int = 10) -> str:
    """The title's content words, then the abstract's most frequent ones."""
    words = lambda t: [w.lower() for w in re.findall(r"[A-Za-z][A-Za-z0-9-]{2,}", t) if w.lower() not in STOP]
    kept = list(dict.fromkeys(words(title)))
    counts: dict[str, int] = {}
    for w in words(abstract):
        counts[w] = counts.get(w, 0) + 1
    kept += [w for w, _ in sorted(counts.items(), key=lambda kv: -kv[1]) if w not in kept]
    return " ".join(kept[:most])


# ---- parsing the API ---------------------------------------------------------

def _value(note: dict, *keys):
    content = note.get("content") or {}
    for key in keys:
        field = content.get(key)
        value = field.get("value") if isinstance(field, dict) else field
        if value not in (None, ""):
            return value
    return None


def _number(value) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    m = re.match(r"\s*(-?\d+(?:\.\d+)?)", str(value or ""))
    return float(m.group(1)) if m else None


def _cut(text) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text if len(text) <= TEXT else text[:TEXT].rstrip() + "…"


def _kind(note: dict) -> str:
    return ((note.get("invitations") or [""])[0]).rsplit("/-/", 1)[-1]


def parse_review(note: dict) -> dict | None:
    rating = _number(_value(note, "rating", "overall_recommendation"))
    if rating is None:
        return None
    combined = _value(note, "strengths_and_weaknesses", "other_strengths_and_weaknesses")
    return {"rating": rating, "confidence": _number(_value(note, "confidence")),
            "summary": _cut(_value(note, "summary")),
            "strengths": _cut(_value(note, "strengths") or combined),
            "weaknesses": _cut(_value(note, "weaknesses") or ("(see strengths)" if combined else ""))}


def parse_group(forums: dict, replies: dict, group: str, scale: tuple[int, int],
                family_scale: tuple[int, int], most: int = PER_GROUP) -> list[dict]:
    """The anchors of one venue-year from its submission and reply searches: similar
    submissions in search order with at least one rated review and a decision."""
    reviews: dict[str, list] = {}
    decisions: dict[str, str] = {}
    for note in replies.get("notes") or []:
        kind, forum = _kind(note), note.get("forum")
        if kind == "Official_Review" and (r := parse_review(note)):
            reviews.setdefault(forum, []).append(r)
        elif kind == "Decision":
            decisions[forum] = str(_value(note, "decision") or "")
    lo, hi = scale
    flo, fhi = family_scale
    out = []
    for note in forums.get("notes") or []:
        forum = note.get("forum") or note.get("id")
        venueid = str(_value(note, "venueid") or "")
        if decisions.get(forum):
            decision, source = ("accept" if decisions[forum].lower().startswith("accept") else "reject"), "decision"
        elif venueid == group:
            decision, source = "accept", "venueid"
        elif venueid.endswith("/Rejected_Submission"):
            decision, source = "reject", "venueid"
        else:
            continue                       # withdrawn, desk rejected, or not decided yet
        if not reviews.get(forum):
            continue
        ratings = [r["rating"] for r in reviews[forum]]
        mean = statistics.mean(ratings)
        out.append({"title": _plain(str(_value(note, "title") or "")), "venue": str(_value(note, "venue") or group),
                    "group": group, "forum": forum, "link": f"https://openreview.net/forum?id={forum}",
                    "decision": decision, "decision_from": source, "scale": [lo, hi], "ratings": ratings,
                    "mean_rating": round(mean, 2),
                    "mean_on_family": round(flo + (mean - lo) * (fhi - flo) / (hi - lo), 2),
                    "reviews": reviews[forum]})
        if len(out) >= most:
            break
    return out


# ---- collecting --------------------------------------------------------------

def conferences(venue_text: str, family: str) -> list[str]:
    """The family's conferences to search: the one the venue names first, then ICLR,
    the only one that publishes the reviews of rejected papers."""
    known = CONFERENCES.get(family, ())
    m = re.search(r"(?<![A-Za-z])(ICLR|NeurIPS|NIPS|ICML)(?![A-Za-z])", venue_text or "", re.I)
    named = {"ICLR": "ICLR", "NEURIPS": "NeurIPS", "NIPS": "NeurIPS", "ICML": "ICML"}[m.group(1).upper()] if m else None
    order = [named] if named in known else []
    return list(dict.fromkeys(order + (["ICLR"] if "ICLR" in known else [])))


def search(group: str, query: str, source: str, limit: int) -> dict:
    found = fetch(API, {"term": query, "group": group, "source": source, "limit": limit})
    if not isinstance(found, dict):
        raise Unavailable(f"OpenReview ({group})")
    return found


def collect(query: str, venue_text: str, family: str, family_scale: tuple[int, int], cache_dir: Path,
            year: int | None = None) -> dict:
    """Anchors for the paper: up to PER_GROUP similar submissions from each of the latest
    YEARS venue-years with reviews and decisions, per conference. Cached under cache_dir
    by query; a failure gives no anchors and names the source in unavailable."""
    confs = conferences(venue_text, family)
    result = {"query": query, "family": family, "conferences": confs, "groups": [], "anchors": [],
              "unavailable": [], "cached": False}
    if not confs:
        result["skipped"] = f"OpenReview has no public reviews for {family or 'this venue'}"
        return result
    if not query:
        result["skipped"] = "the paper has no title or abstract to search with"
        return result
    key = hashlib.sha1(json.dumps([family, confs, query]).encode()).hexdigest()[:16]
    path = Path(cache_dir) / f"{key}.json"
    try:
        if path.exists():
            return dict(json.loads(path.read_text(encoding="utf-8")), cached=True)
    except (OSError, ValueError):
        pass
    this_year = year or datetime.now().year
    try:
        for conf in confs:
            used = 0
            for y in range(this_year + 1, this_year - 4, -1):
                if used == YEARS:
                    break
                group = f"{PREFIX[conf]}/{y}/Conference"
                forums = search(group, query, "forum", SUBMISSIONS)
                if not forums.get("notes"):
                    continue
                found = parse_group(forums, search(group, query, "reply", REPLIES), group,
                                    group_scale(conf, y), family_scale)
                if found:
                    used += 1
                    result["groups"].append(group)
                    result["anchors"] += found
    except Unavailable as e:
        return dict(result, groups=[], anchors=[], unavailable=[str(e)])
    except Exception as e:  # noqa: BLE001  a malformed answer must not stop the review
        return dict(result, groups=[], anchors=[], unavailable=[f"OpenReview ({type(e).__name__})"])
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass
    return result


def examples(anchors: list[dict], most: int = 3) -> list[dict]:
    """Two or three anchor reviews for a reviewer's context: one of an accepted paper, one of
    a rejected paper when there is one, then one more from another paper."""
    picked = []
    for want in ("accept", "reject", None):
        for a in anchors:
            if a not in picked and (want is None or a["decision"] == want):
                picked.append(a)
                break
    shown = []
    for a in picked[:most]:
        r = a["reviews"][0]
        lo, hi = a["scale"]
        shown.append({"paper": a["title"], "venue": a["venue"], "decision": a["decision"],
                      "rating": f"{r['rating']:g} on {lo}-{hi}", "confidence": r["confidence"],
                      "summary": r["summary"], "strengths": r["strengths"], "weaknesses": r["weaknesses"]})
    return shown


# ---- the empirical estimate --------------------------------------------------

def fit_logistic(xs: list[float], ys: list[int], ridge: float = 0.5, steps: int = 30) -> tuple[float, float]:
    """P(y=1 | x) = 1 / (1 + exp(-(a + b x))) by Newton's method on standardized x, with a
    small ridge on the slope so separable samples stay finite. Returns (a, b) on raw x."""
    m = statistics.mean(xs)
    s = statistics.pstdev(xs) or 1.0
    z = [(x - m) / s for x in xs]
    w0 = w1 = 0.0
    for _ in range(steps):
        p = [1 / (1 + math.exp(-(w0 + w1 * zi))) for zi in z]
        g0 = sum(pi - yi for pi, yi in zip(p, ys))
        g1 = sum((pi - yi) * zi for pi, yi, zi in zip(p, ys, z)) + ridge * w1
        h = [pi * (1 - pi) for pi in p]
        h00 = sum(h) + 1e-9
        h01 = sum(hi * zi for hi, zi in zip(h, z))
        h11 = sum(hi * zi * zi for hi, zi in zip(h, z)) + ridge
        det = h00 * h11 - h01 * h01
        d0, d1 = (h11 * g0 - h01 * g1) / det, (h00 * g1 - h01 * g0) / det
        w0, w1 = w0 - d0, w1 - d1
        if abs(d0) + abs(d1) < 1e-10:
            break
    return w0 - w1 * m / s, w1 / s
