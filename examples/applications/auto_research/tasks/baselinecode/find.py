"""Find the code of one baseline: its arXiv id and authors, the repositories its paper
links (abstract, comments, full text), Hugging Face Papers' githubRepo, and a GitHub
search for its title; then each repository's metadata and whether it is official.
Plain code, no model calls.

Every GET outside GitHub goes through citations.sources.fetch, and every GitHub call
through github() below, so tests replace those two alone. GitHub goes through
`gh api` when the gh CLI is installed and logged in (5000 requests an hour, 30
searches a minute), else plain HTTPS (60 an hour, 10 searches a minute; GH_TOKEN or
GITHUB_TOKEN raises it). A source that fails is named in sources.UNAVAILABLE.
"""

import html as htmllib
import json
import os
import re
import shutil
import subprocess
import time
import unicodedata
import urllib.parse

from auto_research.tasks.citations import sources
from auto_research.tasks.citations.refs import AUTHENTIC_TITLE, title_sim
from auto_research.tasks.survey import fulltext, search

GITHUB_LINK = re.compile(r"github\.com/([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/([A-Za-z0-9_.-]{1,100})", re.I)
# Owners whose links are the page's own machinery, not a paper's code.
INFRA = {"dginev", "brucemiller", "arxiv", "ar5iv", "huggingface", "paperswithcode", "github", "topics",
         "features", "orgs", "sponsors", "about", "login", "settings", "marketplace"}
# A sentence that releases the paper's own code: "The code and pre-trained models are
# available at", "our implementation is publicly available", "we release our code".
RELEASE = re.compile(
    r"\b(?:our|the|all)\s+(?:source[-\s]?)?(?:code|codes|implementation|models?|pre-?trained\s+models?|checkpoints?)\b"
    r"[^.]{0,60}?\b(?:is|are|will\s+be|has\s+been|have\s+been)\s+(?:made\s+)?(?:publicly\s+|freely\s+|open-?ly\s+)?"
    r"(?:available|released|open-?sourced?)"
    r"|\bcode\s+(?:is|are)\s+(?:publicly\s+)?available"
    r"|\bwe\s+(?:release|open-?source|make\s+(?:our|the)\s+code)", re.I)
SEARCH_GAP_AUTH = 2.2       # seconds between two searches: 30 a minute with a token
SEARCH_GAP_ANON = 6.5       # 10 a minute without
_last_search = [0.0]
_gh: list = []              # [bool] once the gh CLI was probed


# GitHub access -----------------------------------------------------------------------------

def gh_ready() -> bool:
    """Whether the gh CLI is installed and logged in (probed once)."""
    if not _gh:
        ok = False
        if shutil.which("gh"):
            try:
                ok = subprocess.run(["gh", "auth", "status"], capture_output=True, timeout=15).returncode == 0
            except (OSError, subprocess.SubprocessError):
                ok = False
        _gh.append(ok)
    return _gh[0]


def github(path: str, params: dict | None = None, raw: bool = False):
    """GET api.github.com/<path>: parsed JSON (text when raw), None on 404 or failure."""
    if path.startswith("search/"):
        gap = SEARCH_GAP_AUTH if gh_ready() or token() else SEARCH_GAP_ANON
        wait = _last_search[0] + gap - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_search[0] = time.monotonic()
    accept = "application/vnd.github.raw" if raw else "application/vnd.github+json"
    if gh_ready():
        cmd = ["gh", "api", "-X", "GET", "-H", f"Accept: {accept}", path]
        for k, v in (params or {}).items():
            cmd += ["-f", f"{k}={v}"]
        for attempt in range(2):
            try:
                done = subprocess.run(cmd, capture_output=True, text=True, timeout=40)
            except (OSError, subprocess.SubprocessError):
                done = None
            if done is not None and done.returncode == 0:
                if raw:
                    return done.stdout
                try:
                    return json.loads(done.stdout)
                except ValueError:
                    return None
            err = (done.stderr if done is not None else "") or ""
            if "404" in err or "Not Found" in err:
                return None
            time.sleep(3 * (attempt + 1))
        sources.UNAVAILABLE.add("GitHub")
        return None
    headers = {"Accept": accept, **({"Authorization": f"Bearer {token()}"} if token() else {})}
    return sources.fetch("https://api.github.com/" + path, params, as_json=not raw, tries=2,
                         label="GitHub", headers=headers)


def token() -> str:
    return os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""


# The paper ---------------------------------------------------------------------------------

def paper_record(title: str, arxiv: str, authors: list[str]) -> dict:
    """arXiv id, authors and Hugging Face logins of one baseline paper, and how the id
    was found: given, Semantic Scholar's externalIds, or an arXiv title search."""
    found = {"arxiv": arxiv, "how": "given" if arxiv else "", "authors": list(authors), "logins": [],
             "github_repo": ""}
    if not arxiv and title:
        d = sources.fetch("https://api.semanticscholar.org/graph/v1/paper/search/match",
                          {"query": title, "fields": "title,externalIds,authors"}, label="Semantic Scholar",
                          headers=sources._env("S2_API_KEY", "x-api-key"))
        hit = ((d or {}).get("data") or [None])[0]
        if hit and title_sim(title, hit.get("title") or "") >= AUTHENTIC_TITLE:
            found["arxiv"] = (hit.get("externalIds") or {}).get("ArXiv") or ""
            found["how"] = "semantic scholar" if found["arxiv"] else ""
            found["authors"] = found["authors"] or [a.get("name", "") for a in hit.get("authors") or []]
        if not found["arxiv"]:
            hits = search.arxiv_search(title, 3)
            best = max(hits, key=lambda c: title_sim(title, c["title"]), default=None)
            if best and title_sim(title, best["title"]) >= AUTHENTIC_TITLE:
                found["arxiv"], found["how"] = best["arxiv"], "arxiv search"
                found["authors"] = found["authors"] or list(best.get("authors") or [])
    found["arxiv"] = re.sub(r"v\d+$", "", found["arxiv"] or "")
    if found["arxiv"]:
        d = sources.fetch(f"https://huggingface.co/api/papers/{found['arxiv']}", label="Hugging Face Papers")
        if isinstance(d, dict):
            names = [a.get("name") or "" for a in d.get("authors") or []]
            if len(names) > len(found["authors"]):
                found["authors"] = [n for n in names if n]
            found["logins"] = [((a.get("user") or {}).get("user") or "") for a in d.get("authors") or []
                               if (a.get("user") or {}).get("user")]
            found["github_repo"] = d.get("githubRepo") or ""
    return found


def links(text: str) -> list[str]:
    """owner/repo of every GitHub repository link in text, in order, once each."""
    out = []
    for owner, name in GITHUB_LINK.findall(text or ""):
        name = re.sub(r"(\.git|[.,;:)\]]+)$", "", name)
        full = f"{owner}/{name}"
        if owner.lower() in INFRA or not name or name.startswith(".") or full.lower() in (o.lower() for o in out):
            continue
        out.append(full)
    return out


def abstract_links(arxiv: str) -> list[str]:
    """Repositories linked from the arXiv abstract or its comments field."""
    page = sources.fetch(f"https://arxiv.org/abs/{arxiv}", as_json=False, label="arXiv")
    if not page:
        return []
    parts = re.findall(r'<blockquote class="abstract[^"]*">(.*?)</blockquote>', page, re.S)
    parts += re.findall(r'<td class="tablecell comments[^"]*">(.*?)</td>', page, re.S)
    return links(" ".join(parts))


def fulltext_links(arxiv: str) -> list[dict]:
    """Repositories the paper's full text links (arXiv HTML, else ar5iv), each with
    whether the sentence around the link releases the paper's own code."""
    for pattern in fulltext.SOURCES:
        page = sources.fetch(pattern.format(id=arxiv), as_json=False, tries=2)
        if not page or "ltx_document" not in page:
            continue
        out: dict[str, dict] = {}
        for m in GITHUB_LINK.finditer(page):
            got = links(m.group(0))
            if not got:
                continue
            around = plain_html(page[max(0, m.start() - 1500):m.start()])[-300:]
            entry = out.setdefault(got[0].lower(), {"repo": got[0], "release": False, "context": ""})
            hit = RELEASE.search(around)
            if hit and not entry["release"]:
                start = max(around.rfind(". ", 0, hit.start()) + 2, 0)
                entry["release"], entry["context"] = True, around[start:].strip()[:160]
        return list(out.values())
    return []


def plain_html(fragment: str) -> str:
    fragment = re.sub(r"<[^>]*$", "", re.sub(r"^[^<]*>", "", fragment))
    text = re.sub(r"<[^>]*>", " ", fragment)
    return " ".join(htmllib.unescape(text).split())


def title_search(title: str, limit: int = 5) -> list[str]:
    """Repositories whose name, description or topics hold the exact title, most stars first."""
    if not title:
        return []
    d = github("search/repositories", {"q": f'"{title[:200]}"', "sort": "stars", "order": "desc",
                                       "per_page": str(limit)})
    return [i["full_name"] for i in (d or {}).get("items") or [] if i.get("full_name")][:limit]


# Repositories ------------------------------------------------------------------------------

def repo_meta(full: str) -> dict | None:
    """license, last push, stars, archived, owner and description of owner/repo; None
    when GitHub does not know it. A renamed repository answers with its new name."""
    d = github(f"repos/{full}")
    if not isinstance(d, dict) or not d.get("full_name"):
        return None
    lic = (d.get("license") or {}).get("spdx_id") or ""
    return {"repo": d["full_name"], "url": d.get("html_url") or f"https://github.com/{d['full_name']}",
            "license": "" if lic == "NOASSERTION" else lic, "license_name": (d.get("license") or {}).get("name") or "",
            "last_push": (d.get("pushed_at") or "")[:10], "stars": int(d.get("stargazers_count") or 0),
            "archived": bool(d.get("archived")), "fork": bool(d.get("fork")),
            "owner": (d.get("owner") or {}).get("login") or d["full_name"].split("/")[0],
            "owner_type": (d.get("owner") or {}).get("type") or "", "description": d.get("description") or ""}


def owner_name(login: str) -> str:
    d = github(f"users/{login}")
    return (d or {}).get("name") or "" if isinstance(d, dict) else ""


def readme(full: str, limit: int = 4000) -> str:
    text = github(f"repos/{full}/readme", raw=True)
    return text[:limit] if isinstance(text, str) else ""


def fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", s.lower())


def author_keys(name: str) -> set[str]:
    """Logins an author plausibly uses: kaiminghe, hekaiming, khe, kaimingh."""
    parts = [fold(p) for p in re.split(r"[\s\-]+", name or "") if fold(p)]
    if len(parts) < 2:
        return {parts[0]} if parts and len(parts[0]) >= 4 else set()
    first, last = parts[0], parts[-1]
    keys = {first + last, last + first, "".join(parts), first[0] + last, first + last[0]}
    return {k for k in keys if len(k) >= 4}


def owner_match(owner: str, display: str, authors: list[str], logins: list[str]) -> str:
    """The author the owner is, by login or by the account's display name; "" when none."""
    login = fold(owner)
    for login_hint in logins:
        if login and fold(login_hint) == login:
            return login_hint
    for a in authors:
        if login in author_keys(a) or (display and fold(display) == fold(a) and fold(a)):
            return a
    return ""


def repo_url(text: str) -> str:
    """owner/repo of a GitHub URL or owner/repo string; "" otherwise."""
    got = links(text) if "github.com" in (text or "").lower() else []
    if got:
        return got[0]
    m = re.fullmatch(r"\s*([A-Za-z0-9-]{1,39})/([A-Za-z0-9_.-]{1,100})\s*", text or "")
    return f"{m.group(1)}/{m.group(2)}" if m and not re.fullmatch(r"\d{4}\.\d{4,5}", text.strip()) else ""


def arxiv_url(text: str) -> str:
    m = re.search(r"(?:arxiv\.org/(?:abs|pdf)/|arxiv:\s*)?(\d{4}\.\d{4,5})(?:v\d+)?", text or "", re.I)
    return m.group(1) if m else ""


__all__ = ["abstract_links", "fulltext_links", "github", "owner_match", "owner_name", "paper_record", "readme",
           "repo_meta", "repo_url", "title_search"]
