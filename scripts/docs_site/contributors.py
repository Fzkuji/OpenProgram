"""Build a contributor avatar sheet from the GitHub API.

The README embeds the published SVG. Automated accounts (bots and AI
coding users such as ``claude``) are omitted. A failed fetch must not
fail the documentation build: write a short fallback sheet instead.
"""

from __future__ import annotations

import base64
import json
import os
import ssl
import urllib.error
import urllib.request
import xml.sax.saxutils as xml
from pathlib import Path
from typing import Any

REPO = os.environ.get("OPENPROGRAM_GITHUB_REPO", "Fzkuji/OpenProgram")
CONTRIBUTORS_URL = f"https://api.github.com/repos/{REPO}/contributors?per_page=100"
AVATAR_SIZE = 48
CELL_W = 88
CELL_H = 68
COLUMNS = 8
RADIUS = 16
TIMEOUT_S = 20

# GitHub logins that are coding agents or bots, not people.
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
    if lower in AI_LOGINS:
        return False
    return True


def _headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "OpenProgram-docs",
    }
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def fetch_contributors() -> list[dict[str, Any]]:
    request = urllib.request.Request(CONTRIBUTORS_URL, headers=_headers())
    with urllib.request.urlopen(request, timeout=TIMEOUT_S, context=ssl.create_default_context()) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, list):
        return []
    return [row for row in payload if isinstance(row, dict) and is_listed_contributor(row)]


def _avatar_data_uri(entry: dict[str, Any]) -> str | None:
    url = str(entry.get("avatar_url") or "").strip()
    if not url:
        return None
    sep = "&" if "?" in url else "?"
    request = urllib.request.Request(f"{url}{sep}s={AVATAR_SIZE}", headers=_headers())
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S, context=ssl.create_default_context()) as response:
            data = response.read()
            content_type = response.headers.get_content_type() or "image/png"
    except (urllib.error.URLError, TimeoutError, OSError):
        return None
    if not data:
        return None
    if content_type not in {"image/png", "image/jpeg", "image/jpg", "image/webp", "image/gif"}:
        content_type = "image/png"
    return f"data:{content_type};base64,{base64.b64encode(data).decode('ascii')}"


def render_svg(people: list[dict[str, Any]], avatars: list[str | None]) -> str:
    count = len(people)
    cols = min(COLUMNS, max(count, 1))
    rows = max((count + cols - 1) // cols, 1) if count else 1
    width = cols * CELL_W
    height = rows * CELL_H if count else 48
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-label="OpenProgram contributors">',
        "<style>text{font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;"
        "font-size:10px;fill:#57606a}</style>",
    ]
    if not people:
        parts.append(
            '<text x="8" y="28">See github.com/Fzkuji/OpenProgram/graphs/contributors</text>'
        )
    for index, (person, avatar) in enumerate(zip(people, avatars)):
        col, row = index % cols, index // cols
        cx = col * CELL_W + CELL_W // 2
        cy = row * CELL_H + 22
        login = str(person.get("login") or "")
        label = login if len(login) <= 14 else login[:12] + "…"
        clip = f"c{index}"
        parts.append(
            f'<clipPath id="{clip}"><circle cx="{cx}" cy="{cy}" r="{RADIUS}"/></clipPath>'
        )
        if avatar:
            parts.append(
                f'<image href="{avatar}" x="{cx - RADIUS}" y="{cy - RADIUS}" '
                f'width="{RADIUS * 2}" height="{RADIUS * 2}" clip-path="url(#{clip})"/>'
            )
        else:
            initial = xml.escape((login[:1] or "?").upper())
            parts.append(f'<circle cx="{cx}" cy="{cy}" r="{RADIUS}" fill="#d0d7de"/>')
            parts.append(
                f'<text x="{cx}" y="{cy + 4}" text-anchor="middle" '
                f'style="font-size:12px;fill:#57606a">{initial}</text>'
            )
        parts.append(
            f'<text x="{cx}" y="{cy + RADIUS + 14}" text-anchor="middle">{xml.escape(label)}</text>'
        )
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def write_contributors_svg(path: Path) -> None:
    try:
        people = fetch_contributors()
        avatars = [_avatar_data_uri(person) for person in people]
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, ValueError):
        people, avatars = [], []
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_svg(people, avatars), encoding="utf-8")
