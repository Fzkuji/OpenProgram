"""GitHub Releases lookup — primary "is there a new version" source.

Works whether or not the package is published to PyPI. The repo URL is
read from the openprogram package metadata (``Project-URL``) so a fork
that publishes its own releases needs no code change here.

Returns the tag name without the leading ``v`` (``v0.5.0`` → ``0.5.0``)
so callers can compare against ``importlib.metadata.version`` output
without an extra normalisation step.
"""
from __future__ import annotations

from typing import Optional

from openprogram.security import safe_http


HTTP_TIMEOUT = 5.0
DEFAULT_OWNER = "Fzkuji"
DEFAULT_REPO = "OpenProgram"


def _release_url(owner: str, repo: str) -> str:
    return f"https://api.github.com/repos/{owner}/{repo}/releases/latest"


def latest_release_tag(
    owner: str = DEFAULT_OWNER,
    repo: str = DEFAULT_REPO,
) -> Optional[str]:
    """Return the latest release's tag name (``v`` stripped), or None.

    None covers all failure modes: no releases yet (404), network
    unreachable, rate-limit hit, malformed payload. Auto-update treats
    None as "no update info available", not "we're up to date".
    """
    url = _release_url(owner, repo)
    try:
        with safe_http.safe_client("updater.github") as client:
            response = client.get(
                url,
                headers={
                "User-Agent": "openprogram-updater",
                "Accept": "application/vnd.github+json",
                },
                timeout=HTTP_TIMEOUT,
            )
            response.raise_for_status()
            payload = response.json()
    except Exception:
        return None
    tag = payload.get("tag_name")
    if not isinstance(tag, str) or not tag:
        return None
    return tag[1:] if tag.lower().startswith("v") else tag


def asset_for(
    asset_name: str,
    owner: str = DEFAULT_OWNER,
    repo: str = DEFAULT_REPO,
) -> Optional[str]:
    """Return the download URL for a named asset in the latest release.

    Used by the binary-install path to pick the right artefact for the
    current platform.
    """
    url = _release_url(owner, repo)
    try:
        with safe_http.safe_client("updater.github") as client:
            response = client.get(
                url,
                headers={
                "User-Agent": "openprogram-updater",
                "Accept": "application/vnd.github+json",
                },
                timeout=HTTP_TIMEOUT,
            )
            response.raise_for_status()
            payload = response.json()
    except Exception:
        return None
    for asset in payload.get("assets") or []:
        if isinstance(asset, dict) and asset.get("name") == asset_name:
            dl = asset.get("browser_download_url")
            if isinstance(dl, str):
                return dl
    return None
