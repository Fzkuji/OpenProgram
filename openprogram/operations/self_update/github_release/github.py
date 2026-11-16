"""Validated metadata and files from formal GitHub Releases.

Where the latest release is found, in order:

1. the GitHub API, which also gives the release notes;
2. when the API refuses (403, 429, 5xx) or does not answer, the
   ``releases/latest`` redirect plus ``release-manifest.json``, which give the
   same version and files without the notes;
3. when GitHub has nothing at all, the signed update source below.

Update source
-------------

``update-source.json`` on the project website (``UPDATE_SOURCE_URL`` in
``openprogram.operations.project_site``) is optional, and exists so that
versions already installed can still find releases if the repository moves::

    {"schema": 1, "repository": "Owner/Name", "hosts": ["github.com", "..."]}

``update-source.json.sig`` beside it is the hex Ed25519 signature of the
file's exact bytes, by a key in ``official.assets.RELEASE_PUBLIC_KEYS``, the
same form as ``update-manifest.json.sig``.
``scripts/release/create-update-assets.py sign-file`` writes it.

``hosts[0]`` is asked for ``https://<host>/<repository>/releases/latest`` and
``https://<host>/<repository>/releases/download/v<version>/<file>``, the layout
GitHub uses. Every entry may answer or be redirected to. The file is read only
after GitHub has failed, and it is ignored when absent, unsigned, signed by
another key or malformed. Release files are still checked against their own
signature, so the file only decides where to look.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path, PurePosixPath
from typing import Optional
from urllib.parse import urljoin, urlparse
import urllib.request

from openprogram.host.security import safe_http
from openprogram.operations import project_site
from openprogram.host.security.url_policy import URLPolicyError
from openprogram.host._platform import filesystem


HTTP_TIMEOUT = 5.0
DEFAULT_OWNER = "Fzkuji"
DEFAULT_REPO = "OpenProgram"
API_ROOT = "https://api.github.com"
WEB_ROOT = "https://github.com"
RAW_ROOT = "https://raw.githubusercontent.com"
UPDATE_SOURCE_URL = project_site.UPDATE_SOURCE_URL
UPDATE_SOURCE_SCHEMA = 1
VIA_API = "github-api"
VIA_REDIRECT = "github-redirect"
VIA_UPDATE_SOURCE = "update-source"
DOWNLOAD_ATTEMPTS = 4
DOWNLOAD_RETRY_SECONDS = 2.0
_SMALL_FILE_BYTES = 2 * 1024 * 1024
_REDIRECTS = {301, 302, 303, 307, 308}
_VERSION_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
_RELEASE_PAGE = re.compile(r"/releases/tag/(v\d+\.\d+\.\d+)/?$")
_REPOSITORY = re.compile(r"([A-Za-z0-9][A-Za-z0-9._-]{0,99})/([A-Za-z0-9][A-Za-z0-9._-]{0,99})")
_HOST = re.compile(r"[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?")
_RELEASE_HOSTS = {
    "github.com",
    "raw.githubusercontent.com",
    "release-assets.githubusercontent.com",
    # Serves update-source.json, which is read through the same fetcher.
    project_site.SITE_HOST,
}
_URL_SCHEMES = {"https"}
_CURL_PROTOCOLS = "=https"
# Filled only from an update source whose signature passed.
_signed_hosts: set[str] = set()


def _release_url(owner: str, repo: str) -> str:
    return f"{API_ROOT}/repos/{owner}/{repo}/releases/latest"


def _curl_environment() -> dict[str, str]:
    """Curl's environment, with the system proxy when no variable names one.

    Curl reads proxy variables only, while macOS and Windows keep the proxy in
    system settings, so a user behind one would otherwise reach the API here
    and fail on the download.
    """
    environment = dict(os.environ)
    if any(environment.get(name) for name in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy")):
        return environment
    try:
        proxies = urllib.request.getproxies()
    except Exception:
        return environment
    if proxies.get("https"):
        environment["HTTPS_PROXY"] = proxies["https"]
    if proxies.get("http"):
        environment["http_proxy"] = proxies["http"]
    return environment


def _curl_api_read(url: str) -> tuple[Optional[dict], Optional[int]]:
    try:
        completed = subprocess.run(
            [
                "curl",
                "--disable",
                "--proto", _CURL_PROTOCOLS,
                "--tlsv1.2",
                "--silent",
                "--show-error",
                "--connect-timeout", "5",
                "--max-time", "10",
                "--header", "User-Agent: openprogram-updater",
                "--header", "Accept: application/vnd.github+json",
                "--write-out", "\n%{http_code}",
                url,
            ],
            capture_output=True,
            check=False,
            timeout=12,
            env=_curl_environment(),
        )
        if completed.returncode != 0 or len(completed.stdout) > _SMALL_FILE_BYTES:
            return None, None
        body, _, code = completed.stdout.rpartition(b"\n")
        status = int(code)
        if status != 200:
            return None, status
        payload = json.loads(body)
        return (payload if isinstance(payload, dict) else None), status
    except Exception:
        return None, None


def _api_read(url: str) -> tuple[Optional[dict], Optional[int]]:
    """Read fixed GitHub API JSON as ``(payload, HTTP status)``.

    A status of ``None`` means nothing answered. ``safe_http`` is preferred.
    Some managed networks resolve public hosts to their HTTPS proxy address,
    which the direct-DNS SSRF guard correctly refuses; curl then uses the
    already configured system proxy. The URL is still fixed by the caller,
    HTTPS-only, and redirects are not followed.
    """
    status: Optional[int] = None
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
            status = response.status_code
            if status != 200:
                return None, status
            safe_http.require_json_mime(response)
            payload = response.json()
        return (payload if isinstance(payload, dict) else None), status
    except URLPolicyError as exc:
        if "NON_GLOBAL_ADDRESS" not in str(exc):
            return None, status
        return _curl_api_read(url)
    except Exception:
        # An answer that was not JSON is a refusal of the content, not of the request.
        return None, status


def _source(owner: str, repo: str, web_root: str, via: str) -> dict:
    return {"owner": owner, "repo": repo, "web_root": web_root, "via": via}


def _api_refused(status: Optional[int]) -> bool:
    return status is None or status in {403, 429} or 500 <= status < 600


def _release_from_redirect(owner: str, repo: str, web_root: str, via: str) -> Optional[dict]:
    """The latest release as the web page names it. Only the notes are missing."""
    answer = _curl_once(f"{web_root}/{owner}/{repo}/releases/latest")
    if answer is None or answer[0] not in _REDIRECTS or not answer[1]:
        return None
    match = _RELEASE_PAGE.search(urlparse(answer[1]).path)
    if match is None:
        return None
    tag = match.group(1)
    release: dict = {"tag_name": tag, "draft": False, "prerelease": False, "body": "", "assets": []}
    manifest = release_manifest(tag[1:], owner, repo, web_root=web_root)
    if isinstance(manifest, dict) and isinstance(manifest.get("files"), list):
        release["assets"] = [{"name": "release-manifest.json", "size": 0}] + [
            {"name": PurePosixPath(item["path"].replace("\\", "/")).name, "size": item["bytes"]}
            for item in manifest["files"]
            if isinstance(item, dict) and isinstance(item.get("path"), str) and isinstance(item.get("bytes"), int)
        ]
    release["release_source"] = _source(owner, repo, web_root, via)
    return release


def _release_on_github(owner: str, repo: str) -> Optional[dict]:
    payload, status = _api_read(_release_url(owner, repo))
    if payload is None:
        return _release_from_redirect(owner, repo, WEB_ROOT, VIA_REDIRECT) if _api_refused(status) else None
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


@dataclass(frozen=True)
class UpdateSource:
    """A signed statement of where releases live now."""

    owner: str
    repo: str
    hosts: tuple[str, ...]

    @property
    def web_root(self) -> str:
        return _web_root(self.hosts[0])


def _web_root(host: str) -> str:
    return f"https://{host}"


def update_source() -> Optional[UpdateSource]:
    """The verified update source, or ``None`` when there is no usable one."""
    from openprogram.operations.self_update.official import assets

    if not assets.RELEASE_PUBLIC_KEYS:
        return None
    data = _curl_release_bytes(UPDATE_SOURCE_URL)
    signature = _curl_release_bytes(f"{UPDATE_SOURCE_URL}.sig") if data else None
    if not data or not signature:
        return None
    try:
        assets.verify_signature(data, signature.decode("ascii"))
        payload = json.loads(data)
    except (assets.ReleaseError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("schema") != UPDATE_SOURCE_SCHEMA:
        return None
    repository, hosts = payload.get("repository"), payload.get("hosts")
    match = _REPOSITORY.fullmatch(repository) if isinstance(repository, str) else None
    if (
        match is None
        or not isinstance(hosts, list)
        or not 0 < len(hosts) <= 16
        or not all(isinstance(host, str) and _HOST.fullmatch(host) for host in hosts)
    ):
        return None
    _signed_hosts.update(hosts)
    return UpdateSource(match.group(1), match.group(2), tuple(hosts))


def latest_release(
    owner: str = DEFAULT_OWNER,
    repo: str = DEFAULT_REPO,
) -> Optional[dict]:
    """Return a validated latest stable Release payload, or ``None``.

    GitHub's latest endpoint excludes draft and prerelease entries; validate
    those fields again so malformed metadata never becomes an update.
    Use :func:`release_source` to learn where it was found and where its files
    are.
    """
    found = _release_on_github(owner, repo)
    if found is not None or (owner, repo) != (DEFAULT_OWNER, DEFAULT_REPO):
        return found
    source = update_source()
    if source is None:
        return None
    return _release_from_redirect(source.owner, source.repo, source.web_root, VIA_UPDATE_SOURCE)


def release_source(release: dict) -> dict:
    """Where a release from :func:`latest_release` was found and its files are."""
    return release.get("release_source") or _source(DEFAULT_OWNER, DEFAULT_REPO, WEB_ROOT, VIA_API)


def moved_repository(release: dict) -> tuple[str, ...]:
    """``(owner, repo)`` when the release came from an update source, else ``()``."""
    source = release_source(release)
    return (source["owner"], source["repo"]) if source["via"] == VIA_UPDATE_SOURCE else ()


def release_file_url(source: dict, version: str, name: str) -> str:
    """Where a file of the release described by ``release_source`` is."""
    return f"{source['web_root']}/{source['owner']}/{source['repo']}/releases/download/v{version}/{name}"


def release_file(source: dict, version: str, name: str) -> Optional[bytes]:
    """A small file of the release, or ``None``."""
    return _curl_release_bytes(release_file_url(source, version, name))


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
    url = f"{RAW_ROOT}/{owner}/{repo}/v{version}/scripts/{script_name}"
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
        parsed.scheme not in _URL_SCHEMES
        or (parsed.hostname not in _RELEASE_HOSTS and parsed.hostname not in _signed_hosts)
        or parsed.username
        or parsed.password
    ):
        raise ValueError("release URL is not allowed")
    return value


def _curl_once(url: str, *, probe: bool = False) -> Optional[tuple[int, Optional[str], bytes]]:
    """One request with no redirect followed: ``(status, Location, body)``.

    ``probe`` asks for one byte and accepts the status even if curl stopped
    early, because a server that ignores ranges would send the whole file.
    """
    body_path = ""
    try:
        current = _validated_release_url(url)
        with tempfile.NamedTemporaryFile(prefix="openprogram-release-", delete=False) as body:
            body_path = body.name
        completed = subprocess.run(
            [
                "curl",
                "--disable",
                "--proto", _CURL_PROTOCOLS,
                "--tlsv1.2",
                "--silent",
                "--show-error",
                "--connect-timeout", "5",
                "--max-time", "15",
                "--max-filesize", str(_SMALL_FILE_BYTES),
                *(("--range", "0-0") if probe else ()),
                "--dump-header", "-",
                "--output", body_path,
                current,
            ],
            capture_output=True,
            check=False,
            timeout=17,
            env=_curl_environment(),
        )
        if completed.returncode != 0 and not probe:
            return None
        blocks = [part for part in re.split(br"\r?\n\r?\n", completed.stdout) if part.startswith(b"HTTP/")]
        if not blocks:
            return None
        header = blocks[-1].decode("iso-8859-1")
        status_match = re.match(r"HTTP/\S+\s+(\d{3})", header)
        if status_match is None:
            return None
        location = next(
            (line.split(":", 1)[1].strip() for line in header.splitlines() if line.lower().startswith("location:")),
            None,
        )
        path = Path(body_path)
        if path.stat().st_size > _SMALL_FILE_BYTES:
            return None
        return int(status_match.group(1)), location, path.read_bytes()
    except Exception:
        return None
    finally:
        if body_path:
            Path(body_path).unlink(missing_ok=True)


def _curl_release_bytes(initial_url: str) -> Optional[bytes]:
    """Download a small Release file while validating every redirect hop."""
    try:
        current = _validated_release_url(initial_url)
    except ValueError:
        return None
    for _ in range(6):
        answer = _curl_once(current)
        if answer is None:
            return None
        status, location, content = answer
        if status in _REDIRECTS:
            if not location:
                return None
            try:
                current = _validated_release_url(urljoin(current, location))
            except ValueError:
                return None
            continue
        return content if 200 <= status < 300 else None
    return None


def release_manifest(
    version: str,
    owner: str = DEFAULT_OWNER,
    repo: str = DEFAULT_REPO,
    *,
    web_root: Optional[str] = None,
) -> Optional[dict]:
    """Read the small generated manifest for an immutable Release."""
    if re.fullmatch(r"\d+\.\d+\.\d+", version) is None:
        return None
    url = f"{web_root or WEB_ROOT}/{owner}/{repo}/releases/download/v{version}/release-manifest.json"
    try:
        raw = _curl_release_bytes(url)
        if raw is None:
            return None
        payload = json.loads(raw)
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None


# --- Large files ------------------------------------------------------------


class DownloadFailed(RuntimeError):
    """A file did not arrive. ``permanent`` means trying again cannot help."""

    def __init__(self, message: str, *, permanent: bool = False) -> None:
        super().__init__(message)
        self.permanent = permanent


def _final_download_url(url: str, name: str) -> str:
    """Follow the redirects, checking each hop, and return where the file is."""
    try:
        current = _validated_release_url(url)
    except ValueError as exc:
        raise DownloadFailed(f"{name} is at an address this update does not trust", permanent=True) from exc
    for _ in range(6):
        answer = _curl_once(current, probe=True)
        if answer is None:
            raise DownloadFailed(f"{name} could not be reached")
        status, location, _content = answer
        if status in _REDIRECTS and location:
            try:
                current = _validated_release_url(urljoin(current, location))
            except ValueError as exc:
                raise DownloadFailed(f"{name} redirected to an address this update does not trust", permanent=True) from exc
            continue
        if 200 <= status < 300:
            return current
        raise DownloadFailed(f"{name} answered HTTP {status}", permanent=status in {401, 404, 410})
    raise DownloadFailed(f"{name} redirected too many times", permanent=True)


def _curl_download(url: str, part: Path, size: int, *, progress: bool) -> tuple[int, str]:
    """Fetch or resume into ``part``. Returns curl's exit code and the HTTP status."""
    try:
        completed = subprocess.run(
            [
                "curl",
                "--disable",
                "--proto", _CURL_PROTOCOLS,
                "--tlsv1.2",
                "--fail",
                "--connect-timeout", "15",
                # A link slower than 1 KB/s for a minute is retried from where it stopped.
                "--speed-limit", "1024",
                "--speed-time", "60",
                "--max-filesize", str(size),
                "--continue-at", "-",
                "--write-out", "%{http_code}",
                "--output", str(part),
                *(("--progress-bar",) if progress else ("--silent", "--show-error")),
                url,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=None if progress else subprocess.PIPE,
            check=False,
            env=_curl_environment(),
        )
    except FileNotFoundError as exc:
        raise DownloadFailed("this update needs the curl command, and it is not installed", permanent=True) from exc
    return completed.returncode, completed.stdout.decode("ascii", "replace").strip()


def download_release_file(url: str, target: Path, *, size: int, progress: bool = False) -> None:
    """Download exactly ``size`` bytes to ``target``, resuming what is already here.

    Raises :class:`DownloadFailed`. A failure that retrying could fix leaves
    the partial file for the next run.
    """
    name = target.name
    part = target.with_name(f"{name}.part")
    problem = "it did not finish"
    for attempt in range(DOWNLOAD_ATTEMPTS):
        if attempt:
            time.sleep(DOWNLOAD_RETRY_SECONDS * attempt)
        if part.is_file() and part.stat().st_size > size:
            part.unlink()
        if not part.is_file() or part.stat().st_size < size:
            try:
                final = _final_download_url(url, name)
            except DownloadFailed as exc:
                if exc.permanent:
                    raise
                problem = str(exc)
                continue
            code, status = _curl_download(final, part, size, progress=progress)
            if code == 63:
                part.unlink(missing_ok=True)
                raise DownloadFailed(f"{name} is larger than the signed manifest says", permanent=True)
            if code in {33, 36} or status == "416":
                part.unlink(missing_ok=True)  # The server cannot continue this file; start it again.
                problem = "the server could not resume it"
                continue
            if code != 0:
                problem = f"curl stopped with code {code}"
                continue
        actual = part.stat().st_size if part.is_file() else 0
        if actual != size:
            part.unlink(missing_ok=True)
            raise DownloadFailed(f"{name} is {actual} bytes, and the signed manifest says {size}", permanent=True)
        filesystem.replace_file(part, target)
        return
    raise DownloadFailed(f"{name} could not be downloaded: {problem}")
