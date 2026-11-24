"""Release checks in plain code: the code release and the deliverables (poster, slides,
video). The code release is checked on GitHub's public API; GH_TOKEN, when set, raises
its rate limit.
"""

import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

GITHUB = re.compile(r"https?://github\.com/([^/\s]+)/([^/\s#?]+?)(?:\.git)?/?$")


def head(paper: Path) -> str:
    done = subprocess.run(["git", "-C", str(paper), "rev-parse", "HEAD"], capture_output=True, text=True)
    return done.stdout.strip() if done.returncode == 0 else ""


def github(url: str, path: str = ""):
    owner, repo = GITHUB.match(url).groups()
    request = urllib.request.Request(f"https://api.github.com/repos/{owner}/{repo}{path}",
                                     headers={"Accept": "application/vnd.github+json",
                                              "User-Agent": "openprogram-auto-research",
                                              **({"Authorization": f"Bearer {os.environ['GH_TOKEN']}"}
                                                 if os.environ.get("GH_TOKEN") else {})})
    with urllib.request.urlopen(request, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def code_release(paper: Path, code: dict) -> list[str]:
    """The repository named in rules.json is public, has a README, is linked from the paper,
    and has no contributor outside allowed_contributors when that list is given."""
    url = (code or {}).get("repo", "").strip()
    if not url:
        return []                                   # the venue asks for no code release
    if not GITHUB.match(url):
        return [f"code.repo {url} is not a GitHub repository URL"]
    tex = " ".join(p.read_text(encoding="utf-8", errors="ignore") for p in paper.glob("*.tex"))
    found = [] if url.rstrip("/") in tex else [f"the paper does not link {url}"]
    try:
        info = github(url)
    except urllib.error.HTTPError as e:
        return found + [f"{url}: GitHub answers {e.code}; the repository is missing or private"]
    except Exception as e:
        return found + [f"{url}: GitHub could not be reached ({type(e).__name__}); check again"]
    if info.get("private"):
        found.append(f"{url} is private")
    try:
        github(url, "/readme")
    except urllib.error.HTTPError:
        found.append(f"{url} has no README")
    allowed = set(code.get("allowed_contributors") or [])
    if allowed:
        try:
            people = {c["login"] for c in github(url, "/contributors") if c.get("login")}
        except Exception as e:
            people = set()
            found.append(f"{url}: contributors could not be read ({type(e).__name__})")
        found += [f"{url}: contributor {login} is not allowed" for login in sorted(people - allowed)]
    return found


def deliverables(paper: Path, items: list) -> list[str]:
    """Every deliverable named in rules.json exists and is not empty."""
    found = []
    for item in items or []:
        path = paper / item.get("path", "")
        if not item.get("path") or not path.is_file() or path.stat().st_size == 0:
            found.append(f"{item.get('name', 'deliverable')}: {item.get('path') or 'no path'} is missing")
    return found


__all__ = ["code_release", "deliverables", "head"]
