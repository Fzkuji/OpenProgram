"""Rewrite README contributor avatars from the GitHub contributors API.

The README embeds GitHub's own profile PNGs so the image host is the
same site that renders the file. Bots and AI coding accounts are omitted.
"""

from __future__ import annotations

import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO = os.environ.get("OPENPROGRAM_GITHUB_REPO", "Fzkuji/OpenProgram")
CONTRIBUTORS_URL = f"https://api.github.com/repos/{REPO}/contributors?per_page=100"
README_PATH = Path(__file__).resolve().parents[1] / "README.md"
START = "<!-- contributors-avatars -->"
END = "<!-- /contributors-avatars -->"
TIMEOUT_S = 20
AVATAR_PX = 24
PNG_SIZE = 48

AI_LOGINS = frozenset({
    "claude",
    "chatgpt",
    "openai",
    "copilot",
    "github-copilot",
    "cursor",
    "cursoragent",
    "devin-ai",
})


def is_listed_contributor(entry: dict[str, Any]) -> bool:
    login = str(entry.get("login") or "").strip()
    if not login:
        return False
    if entry.get("type") == "Bot":
        return False
    lower = login.lower()
    if lower.endswith("[bot]") or lower.endswith("-bot") or lower.endswith("_bot"):
        return False
    return lower not in AI_LOGINS


def render_block(logins: list[str]) -> str:
    imgs = "\n".join(
        f'<a href="https://github.com/{login}">'
        f'<img src="https://github.com/{login}.png?size={PNG_SIZE}" '
        f'width="{AVATAR_PX}" height="{AVATAR_PX}" alt="{login}" /></a>'
        for login in logins
    )
    return f"{START}\n<p>\n{imgs}\n</p>\n{END}"


def replace_block(text: str, block: str) -> str:
    pattern = re.compile(re.escape(START) + r".*?" + re.escape(END), re.S)
    if not pattern.search(text):
        raise SystemExit("README contributor markers missing")
    return pattern.sub(block, text, count=1)


def _headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "OpenProgram-readme-contributors",
    }
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def fetch_contributors() -> list[dict[str, Any]]:
    request = urllib.request.Request(CONTRIBUTORS_URL, headers=_headers())
    with urllib.request.urlopen(
        request, timeout=TIMEOUT_S, context=ssl.create_default_context()
    ) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, list):
        return []
    return [row for row in payload if isinstance(row, dict)]


def main() -> int:
    try:
        rows = fetch_contributors()
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        print(f"contributor fetch failed: {exc}", file=sys.stderr)
        return 1
    people = [row for row in rows if is_listed_contributor(row)]
    if not people:
        print("contributor list was empty; leaving README unchanged", file=sys.stderr)
        return 0
    logins = [str(row["login"]) for row in people]
    text = README_PATH.read_text(encoding="utf-8")
    README_PATH.write_text(
        replace_block(text, render_block(logins)), encoding="utf-8"
    )
    print(f"wrote {len(logins)} contributor avatars")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
