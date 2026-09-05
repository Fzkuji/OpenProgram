"""Validated metadata and small files from formal GitHub Releases."""
from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode, urljoin, urlparse

from openprogram.security import safe_http
from openprogram.security.url_policy import URLPolicyError


HTTP_TIMEOUT = 5.0
DEFAULT_OWNER = "Fzkuji"
DEFAULT_REPO = "OpenProgram"
_VERSION_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
_RELEASE_HOSTS = {
    "github.com",
    "raw.githubusercontent.com",
    "release-assets.githubusercontent.com",
}


def _release_url(owner: str, repo: str) -> str:
    return f"https://api.github.com/repos/{owner}/{repo}/releases/latest"


def _api_json(url: str, *, params: Optional[dict[str, str]] = None) -> Optional[dict]:
    """Read fixed GitHub API JSON, with a proxy-aware curl fallback.

    ``safe_http`` is preferred. Some managed networks resolve public hosts to
    their HTTPS proxy address, which the direct-DNS SSRF guard correctly
    refuses; curl then uses the already configured system proxy. The URL is
    still fixed by the caller, HTTPS-only, and redirects are not followed.
    """
    try:
        with safe_http.safe_client("updater.github") as client:
            response = client.get(
                url,
                params=params,
                headers={
                    "User-Agent": "openprogram-updater",
                    "Accept": "application/vnd.github+json",
                },
                timeout=HTTP_TIMEOUT,
            )
            response.raise_for_status()
            safe_http.require_json_mime(response)
            payload = response.json()
        return payload if isinstance(payload, dict) else None
    except URLPolicyError as exc:
        if "NON_GLOBAL_ADDRESS" not in str(exc):
            return None
        query = f"?{urlencode(params)}" if params else ""
        try:
            completed = subprocess.run(
                [
                    "curl",
                    "--disable",
                    "--proto", "=https",
                    "--tlsv1.2",
                    "--fail",
                    "--silent",
                    "--show-error",
                    "--connect-timeout", "5",
                    "--max-time", "10",
                    "--header", "User-Agent: openprogram-updater",
                    "--header", "Accept: application/vnd.github+json",
                    f"{url}{query}",
                ],
                capture_output=True,
                check=False,
                timeout=12,
            )
            if completed.returncode != 0 or len(completed.stdout) > 2 * 1024 * 1024:
                return None
            payload = json.loads(completed.stdout)
            return payload if isinstance(payload, dict) else None
        except Exception:
            return None
    except Exception:
        return None


def latest_release(
    owner: str = DEFAULT_OWNER,
    repo: str = DEFAULT_REPO,
) -> Optional[dict]:
    """Return a validated latest stable Release payload, or ``None``.

    GitHub's latest endpoint excludes draft and prerelease entries; validate
    those fields again so malformed metadata never becomes an update.
    """
    url = _release_url(owner, repo)
    payload = _api_json(url)
    if payload is None:
        return None
    tag = payload.get("tag_name")
    if (
        not isinstance(tag, str)
        or _VERSION_TAG.fullmatch(tag) is None
        or payload.get("draft") is not False
        or payload.get("prerelease") is not False
        or not isinstance(payload.get("assets"), list)
    ):
        return None
    return payload


def release_installer(
    version: str,
    owner: str = DEFAULT_OWNER,
    repo: str = DEFAULT_REPO,
    *,
    script_name: str = "install-release.sh",
) -> Optional[bytes]:
    """Read the versioned installer from the immutable tag."""
    if (
        re.fullmatch(r"\d+\.\d+\.\d+", version) is None
        or script_name not in {"install-release.sh", "install-release.ps1"}
    ):
        return None
    url = (
        f"https://raw.githubusercontent.com/{owner}/{repo}/"
        f"v{version}/scripts/{script_name}"
    )
    content = _curl_release_bytes(url)
    expected = (
        b'$ErrorActionPreference = "Stop"\n'
        if script_name.endswith(".ps1")
        else b"#!/usr/bin/env sh\n"
    )
    return content if content and content.startswith(expected) else None


def _validated_release_url(value: str) -> str:
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in _RELEASE_HOSTS
        or parsed.username
        or parsed.password
    ):
        raise ValueError("release URL is not allowed")
    return value


def _curl_release_bytes(initial_url: str) -> Optional[bytes]:
    """Download a small Release file while validating every redirect hop."""
    current = _validated_release_url(initial_url)
    for redirect_count in range(6):
        body_path = ""
        try:
            with tempfile.NamedTemporaryFile(prefix="openprogram-release-", delete=False) as body:
                body_path = body.name
            completed = subprocess.run(
                [
                    "curl",
                    "--disable",
                    "--proto", "=https",
                    "--tlsv1.2",
                    "--silent",
                    "--show-error",
                    "--connect-timeout", "5",
                    "--max-time", "15",
                    "--max-filesize", str(2 * 1024 * 1024),
                    "--dump-header", "-",
                    "--output", body_path,
                    current,
                ],
                capture_output=True,
                check=False,
                timeout=17,
            )
            if completed.returncode != 0:
                return None
            blocks = [part for part in re.split(br"\r?\n\r?\n", completed.stdout) if part.startswith(b"HTTP/")]
            if not blocks:
                return None
            header = blocks[-1].decode("iso-8859-1")
            status_match = re.match(r"HTTP/\S+\s+(\d{3})", header)
            if status_match is None:
                return None
            status = int(status_match.group(1))
            if status in {301, 302, 303, 307, 308}:
                if redirect_count == 5:
                    return None
                location = next(
                    (line.split(":", 1)[1].strip() for line in header.splitlines() if line.lower().startswith("location:")),
                    None,
                )
                if not location:
                    return None
                current = _validated_release_url(urljoin(current, location))
                continue
            if status < 200 or status >= 300:
                return None
            path = Path(body_path)
            if path.stat().st_size > 2 * 1024 * 1024:
                return None
            return path.read_bytes()
        except Exception:
            return None
        finally:
            if body_path:
                Path(body_path).unlink(missing_ok=True)
    return None

def release_manifest(
    version: str,
    owner: str = DEFAULT_OWNER,
    repo: str = DEFAULT_REPO,
) -> Optional[dict]:
    """Read the small generated manifest for an immutable Release."""
    if re.fullmatch(r"\d+\.\d+\.\d+", version) is None:
        return None
    url = (
        f"https://github.com/{owner}/{repo}/releases/download/"
        f"v{version}/release-manifest.json"
    )
    try:
        raw = _curl_release_bytes(url)
        if raw is None:
            return None
        payload = json.loads(raw)
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None
