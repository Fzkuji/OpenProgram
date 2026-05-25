"""Remote skill discovery — pull skills from one of three URL shapes.

Supported sources
-----------------

1. **Explicit JSON index** (opencode-compatible)::

       {
         "skills": [
           { "name": "deploy", "files": ["SKILL.md", "references/x.md"] }
         ]
       }

   Any URL ending in ``.json``. Files resolve relative to the index URL.

2. **GitHub repository auto-discovery** — preferred for real-world
   claude-code style skill collections. Accepts either::

       https://github.com/<owner>/<repo>[/tree/<ref>[/<subdir>]]
       github://<owner>/<repo>[/<subdir>][@<ref>]

   The tree API is queried, every ``*/SKILL.md`` is treated as one
   skill (its containing directory becomes the skill's hierarchical
   name), and the SKILL.md plus any sibling ``references/`` /
   ``templates/`` / ``scripts/`` files are pulled.

3. **Generic .json URL with auto-fallback** — if a ``.json`` URL 404s
   but its host is github.com, we transparently re-resolve as case 2.

Files are written to ``~/.openprogram/cache/skills/<name>/...`` so the
loader picks them up under the ``remote-cache`` source.
"""
from __future__ import annotations

import asyncio
import io
import re
import time
import zipfile
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import httpx
from pydantic import BaseModel, Field

from .loader import remote_cache_dir


# Module-level zip cache so a single Discovery session doesn't re-download
# the same repo for Browse → Install → Pull.
_ZIP_CACHE: dict[tuple[str, str, str], tuple[float, bytes, str]] = {}
_ZIP_TTL_SECS = 600.0  # 10 minutes


# ---------------------------------------------------------------------------
# Index format (opencode-compatible)
# ---------------------------------------------------------------------------

class IndexSkill(BaseModel):
    name: str
    files: list[str] = Field(default_factory=list)


class Index(BaseModel):
    skills: list[IndexSkill] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# GitHub repo descriptor
# ---------------------------------------------------------------------------

@dataclass
class GhRepo:
    owner: str
    repo: str
    ref: str = "main"
    subdir: str = ""  # may be empty; path inside the repo to scope discovery

    @property
    def zip_url(self) -> str:
        # codeload.github.com serves raw zip archives without API rate limits.
        return f"https://codeload.github.com/{self.owner}/{self.repo}/zip/refs/heads/{self.ref}"

    @property
    def cache_key(self) -> tuple[str, str, str]:
        return (self.owner, self.repo, self.ref)


_GH_HTTPS_RE = re.compile(
    r"^https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?"
    r"(?:/tree/(?P<ref>[^/]+)(?:/(?P<subdir>.*?))?)?/?$"
)
_GH_SCHEME_RE = re.compile(
    r"^github://(?P<owner>[^/]+)/(?P<repo>[^/@]+)"
    r"(?:/(?P<subdir>[^@]+?))?(?:@(?P<ref>.+))?$"
)


def _parse_github(url: str) -> GhRepo | None:
    m = _GH_HTTPS_RE.match(url) or _GH_SCHEME_RE.match(url)
    if not m:
        return None
    d = m.groupdict()
    return GhRepo(
        owner=d["owner"],
        repo=d["repo"],
        ref=d.get("ref") or "main",
        subdir=(d.get("subdir") or "").strip("/"),
    )


# ---------------------------------------------------------------------------
# ClawHub registry — openclaw's public skill / plugin marketplace.
# Docs: https://docs.openclaw.ai/clawhub/api
# ---------------------------------------------------------------------------

_CLAWHUB_BASE = "https://clawhub.ai"


@dataclass
class ClawHubRef:
    slug: str = ""  # empty means "the whole hub" — browse trending list

    @property
    def is_hub_root(self) -> bool:
        return not self.slug


_CLAWHUB_SCHEME_RE = re.compile(r"^clawhub://(?P<slug>[^/?#]*)?$")
_CLAWHUB_HTTPS_RE = re.compile(
    r"^https?://(?:www\.)?clawhub\.ai/(?:skills/)?(?P<slug>[^/?#]+)?/?$"
)


def _parse_clawhub(url: str) -> ClawHubRef | None:
    m = _CLAWHUB_SCHEME_RE.match(url)
    if m:
        return ClawHubRef(slug=(m.group("slug") or "").strip())
    m = _CLAWHUB_HTTPS_RE.match(url)
    if m:
        return ClawHubRef(slug=(m.group("slug") or "").strip())
    return None


# ---------------------------------------------------------------------------
# Common HTTP helpers
# ---------------------------------------------------------------------------

_MAX_CONCURRENCY = 4
_USER_AGENT = "OpenProgram-Discovery/1.0"


async def _get_text(client: httpx.AsyncClient, url: str) -> str:
    r = await client.get(url, timeout=30.0, headers={"User-Agent": _USER_AGENT})
    r.raise_for_status()
    return r.text


async def _get_bytes(client: httpx.AsyncClient, url: str) -> bytes:
    r = await client.get(url, timeout=60.0, headers={"User-Agent": _USER_AGENT})
    r.raise_for_status()
    return r.content


# ---------------------------------------------------------------------------
# Mode 1 — explicit JSON index
# ---------------------------------------------------------------------------

async def _pull_one_indexed(
    client: httpx.AsyncClient,
    base_url: str,
    skill: IndexSkill,
    sem: asyncio.Semaphore,
    namespace: str | None = None,
) -> str | None:
    full_name = _apply_namespace(skill.name, namespace)
    target_dir = remote_cache_dir() / full_name
    target_dir.mkdir(parents=True, exist_ok=True)
    async with sem:
        try:
            for rel in skill.files:
                file_url = urljoin(base_url.rstrip("/") + "/", rel)
                data = await _get_bytes(client, file_url)
                out = target_dir / rel
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(data)
        except Exception:
            return None
    return full_name


async def _pull_from_index(
    client: httpx.AsyncClient, url: str, namespace: str | None = None,
) -> list[str]:
    raw = await _get_text(client, url)
    index = Index.model_validate_json(raw)
    base = url.rsplit("/", 1)[0] + "/"
    sem = asyncio.Semaphore(_MAX_CONCURRENCY)
    results = await asyncio.gather(
        *(_pull_one_indexed(client, base, s, sem, namespace) for s in index.skills),
        return_exceptions=True,
    )
    return [r for r in results if isinstance(r, str)]


# ---------------------------------------------------------------------------
# Mode 2 — GitHub repo auto-discovery via zip download.
#
# We deliberately avoid the GitHub REST/tree API because unauthenticated
# requests are capped at 60/hr per IP and that ceiling is trivially hit by
# normal Browse → Install → Pull workflow. ``codeload.github.com`` returns
# the full repo zip without any API rate limit; we hold the bytes in a
# small in-memory TTL cache and unpack them with ``zipfile`` on demand.
# ---------------------------------------------------------------------------

# Files in a skill directory we pull alongside SKILL.md. Anything else
# is skipped to keep the cache lean.
_COMPANION_DIRS = ("references", "templates", "scripts", "examples", "assets")


def _is_companion(rel: str) -> bool:
    parts = rel.split("/")
    return len(parts) > 1 and parts[0] in _COMPANION_DIRS


async def _fetch_repo_zip(client: httpx.AsyncClient, repo: GhRepo) -> tuple[bytes, str]:
    """Return ``(zip_bytes, top_level_dir)``. Caches per repo+ref.

    The top-level directory inside the GitHub zip is named
    ``<repo>-<ref>``; we strip it from every member path before matching.
    """
    now = time.time()
    cached = _ZIP_CACHE.get(repo.cache_key)
    if cached is not None and now - cached[0] < _ZIP_TTL_SECS:
        return cached[1], cached[2]

    r = await client.get(
        repo.zip_url, timeout=60.0,
        headers={"User-Agent": _USER_AGENT, "Accept": "application/zip"},
    )
    r.raise_for_status()
    data = r.content
    # Determine the top-level dir name by inspecting the first archive member.
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        first = next((n for n in zf.namelist() if n), "")
        top = first.split("/", 1)[0] if "/" in first else first
    _ZIP_CACHE[repo.cache_key] = (now, data, top)
    return data, top


def _list_skill_dirs(zf: zipfile.ZipFile, top: str, scope: str) -> dict[str, list[str]]:
    """Map ``skill_dir`` (path inside repo, no top prefix) → list of
    companion file paths relative to that dir."""
    out: dict[str, list[str]] = {}
    members = [n for n in zf.namelist() if not n.endswith("/")]

    def _strip(member: str) -> str | None:
        if not member.startswith(top + "/"):
            return None
        rel = member[len(top) + 1:]
        if scope and not rel.startswith(scope + "/"):
            return None
        return rel

    for m in members:
        rel = _strip(m)
        if rel is None or not rel.endswith("/SKILL.md"):
            continue
        out[rel[: -len("/SKILL.md")]] = []

    for m in members:
        rel = _strip(m)
        if rel is None:
            continue
        for skill_dir in out:
            if rel.startswith(skill_dir + "/") and rel != skill_dir + "/SKILL.md":
                out[skill_dir].append(rel[len(skill_dir) + 1:])
                break
    return out


def _apply_namespace(name: str, namespace: str | None) -> str:
    """Prefix ``name`` with ``namespace`` so all skills from one source
    live in their own tree. Returns ``namespace/name`` unless namespace
    is empty/None."""
    ns = (namespace or "").strip("/").strip()
    return f"{ns}/{name}" if ns else name


def _write_skill_from_zip(
    zf: zipfile.ZipFile, top: str, skill_dir: str, name: str, files: list[str]
) -> str:
    target_dir = remote_cache_dir() / name
    target_dir.mkdir(parents=True, exist_ok=True)

    # SKILL.md is mandatory.
    md_member = f"{top}/{skill_dir}/SKILL.md"
    md_bytes = zf.read(md_member)
    (target_dir / "SKILL.md").write_bytes(md_bytes)

    for rel in files:
        if rel == "SKILL.md" or not _is_companion(rel):
            continue
        member = f"{top}/{skill_dir}/{rel}"
        try:
            data = zf.read(member)
        except KeyError:
            continue
        out = target_dir / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
    return name


async def _pull_from_github(
    client: httpx.AsyncClient, repo: GhRepo, namespace: str | None = None,
) -> list[str]:
    data, top = await _fetch_repo_zip(client, repo)
    scope = repo.subdir.strip("/")
    pulled: list[str] = []
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        skill_dirs = _list_skill_dirs(zf, top, scope)
        for skill_dir, files in skill_dirs.items():
            rel_name = skill_dir
            if scope and rel_name.startswith(scope + "/"):
                rel_name = rel_name[len(scope) + 1:]
            full_name = _apply_namespace(rel_name, namespace)
            try:
                pulled.append(_write_skill_from_zip(zf, top, skill_dir, full_name, files))
            except Exception:
                continue
    return pulled


# ---------------------------------------------------------------------------
# ClawHub — fetch skill catalog, download single skill zip, etc.
# ---------------------------------------------------------------------------

async def _clawhub_list(
    client: httpx.AsyncClient, query: str = "", limit: int = 50,
) -> list[dict]:
    """Hit ``/api/v1/search`` when a query is provided, else
    ``/api/v1/skills?sort=trending``. Returns the raw items list."""
    if query.strip():
        url = f"{_CLAWHUB_BASE}/api/v1/search?q={query.strip()}"
    else:
        url = f"{_CLAWHUB_BASE}/api/v1/skills?sort=trending&limit={limit}"
    raw = await _get_text(client, url)
    import json as _json
    payload = _json.loads(raw)
    items = payload.get("items") or payload.get("results") or payload
    if not isinstance(items, list):
        return []
    return items


async def _browse_clawhub(
    client: httpx.AsyncClient, query: str = "",
) -> list[CatalogEntry]:
    items = await _clawhub_list(client, query=query)
    out: list[CatalogEntry] = []
    for it in items:
        slug = it.get("slug") or it.get("name") or ""
        if not slug:
            continue
        summary = it.get("summary") or it.get("description") or ""
        out.append(CatalogEntry(
            name=slug,
            description=summary,
            path=slug,
            files=[],
            content_hash="",  # ClawHub has its own version field; skip per-entry hash
        ))
    return out


async def _pull_one_clawhub(
    client: httpx.AsyncClient, slug: str, namespace: str | None,
) -> str | None:
    """Download the slug's zip from /api/v1/download and extract SKILL.md
    + companion dirs into the remote-cache."""
    url = f"{_CLAWHUB_BASE}/api/v1/download?slug={slug}"
    try:
        data = await _get_bytes(client, url)
    except Exception:
        return None
    full_name = _apply_namespace(slug, namespace)
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            members = [n for n in zf.namelist() if not n.endswith("/")]
            # The zip's top-level dir is typically "<slug>-<version>" — strip
            # the common prefix so files land flat under the cache dir.
            top = members[0].split("/", 1)[0] if members and "/" in members[0] else ""
            # Find the SKILL.md location
            md_member = next((m for m in members if m.endswith("SKILL.md")), None)
            if not md_member:
                return None
            target_dir = remote_cache_dir() / full_name
            target_dir.mkdir(parents=True, exist_ok=True)
            target_dir.joinpath("SKILL.md").write_bytes(zf.read(md_member))
            # The directory that contains SKILL.md inside the zip — pull its
            # companions ("references", "scripts", etc.) alongside.
            md_dir = md_member.rsplit("/", 1)[0] if "/" in md_member else ""
            for m in members:
                if m == md_member or m.endswith("/"):
                    continue
                if md_dir and not m.startswith(md_dir + "/"):
                    continue
                rel = m[len(md_dir) + 1:] if md_dir else m
                if rel == "SKILL.md" or not _is_companion(rel):
                    continue
                out = target_dir / rel
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(zf.read(m))
    except Exception:
        return None
    return full_name


async def _pull_from_clawhub(
    client: httpx.AsyncClient, ref: ClawHubRef, namespace: str | None,
) -> list[str]:
    if ref.is_hub_root:
        # "Install all" the hub root: pull the top trending list.
        items = await _clawhub_list(client, limit=20)
        slugs = [it.get("slug") for it in items if it.get("slug")]
    else:
        slugs = [ref.slug]
    pulled: list[str] = []
    for slug in slugs:
        res = await _pull_one_clawhub(client, slug, namespace)
        if res:
            pulled.append(res)
    return pulled


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def _default_namespace(url: str) -> str:
    """Derive a slug from the URL so each source gets its own folder.

    GitHub URLs map to the repo name (lowercased, kept stable across
    refs/subdirs). ClawHub URLs map to ``clawhub``. Non-GitHub URLs map
    to the hostname with dots replaced by hyphens.
    """
    if _parse_clawhub(url) is not None:
        return "clawhub"
    gh = _parse_github(url)
    if gh is not None:
        return gh.repo.lower()
    try:
        host = urlparse(url).hostname or ""
    except Exception:
        host = ""
    return host.replace(".", "-").lower() or "remote"


async def _pull_async(url: str, namespace: str | None = None) -> list[str]:
    ns = namespace if namespace is not None else _default_namespace(url)
    async with httpx.AsyncClient(follow_redirects=True) as client:
        # 0. ClawHub registry (clawhub:// or clawhub.ai URL)
        ch = _parse_clawhub(url)
        if ch is not None:
            return await _pull_from_clawhub(client, ch, namespace=ns)
        # 1. Explicit github:// scheme or github.com URL → repo mode
        gh = _parse_github(url)
        if gh is not None:
            return await _pull_from_github(client, gh, namespace=ns)
        # 2. JSON index URL
        if url.endswith(".json"):
            try:
                return await _pull_from_index(client, url, namespace=ns)
            except httpx.HTTPStatusError as e:
                # Auto-fallback: ``.json`` 404 on raw.githubusercontent.com →
                # try treating the host repo as a tree.
                parsed = urlparse(url)
                if (
                    e.response is not None
                    and e.response.status_code == 404
                    and parsed.hostname == "raw.githubusercontent.com"
                ):
                    parts = parsed.path.strip("/").split("/")
                    if len(parts) >= 3:
                        owner, repo, ref = parts[0], parts[1], parts[2]
                        return await _pull_from_github(
                            client, GhRepo(owner=owner, repo=repo, ref=ref),
                            namespace=ns,
                        )
                raise
        # 3. Fall through: try as JSON index by default
        return await _pull_from_index(client, url, namespace=ns)


def pull(url: str, namespace: str | None = None) -> list[str]:
    """Synchronous wrapper. Returns successfully pulled skill names.

    Raises a regular Exception on top-level failure (route catches and
    serialises). Per-skill failures are swallowed so a partial pull is
    a successful response.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_pull_async(url, namespace))
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(lambda: asyncio.run(_pull_async(url, namespace))).result()


# ---------------------------------------------------------------------------
# Catalog browsing — list skills inside a source without downloading.
# Used by the Discovery UI so users can pick individual skills.
# ---------------------------------------------------------------------------

@dataclass
class CatalogEntry:
    name: str
    description: str
    path: str  # path inside the source (skill_dir for github mode)
    files: list[str]  # extra files we'd download alongside SKILL.md
    content_hash: str = ""  # sha256 of upstream SKILL.md body; used to detect drift


_FRONTMATTER_RE = re.compile(
    r"^---\s*\n(?P<fm>.*?)\n---\s*\n", re.DOTALL
)


def _parse_description(skill_md: str) -> str:
    m = _FRONTMATTER_RE.match(skill_md)
    if not m:
        return ""
    for line in m.group("fm").splitlines():
        line = line.strip()
        if line.lower().startswith("description:"):
            value = line.split(":", 1)[1].strip()
            return value.strip("\"'")
    return ""


def _sha256(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def _browse_index(client: httpx.AsyncClient, url: str) -> list[CatalogEntry]:
    raw = await _get_text(client, url)
    index = Index.model_validate_json(raw)
    base = url.rsplit("/", 1)[0] + "/"
    out: list[CatalogEntry] = []
    for s in index.skills:
        skill_md_url = urljoin(base, f"{s.name}/SKILL.md") if "SKILL.md" not in s.files \
            else urljoin(base, next((f for f in s.files if f.endswith("SKILL.md")), s.files[0]))
        try:
            md = await _get_text(client, skill_md_url)
            desc = _parse_description(md)
            digest = _sha256(md)
        except Exception:
            desc = ""
            digest = ""
        out.append(CatalogEntry(
            name=s.name, description=desc, path=s.name,
            files=list(s.files), content_hash=digest,
        ))
    return out


async def _browse_github(client: httpx.AsyncClient, repo: GhRepo) -> list[CatalogEntry]:
    data, top = await _fetch_repo_zip(client, repo)
    scope = repo.subdir.strip("/")
    out: list[CatalogEntry] = []
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        skill_dirs = _list_skill_dirs(zf, top, scope)
        for skill_dir, files in skill_dirs.items():
            try:
                md = zf.read(f"{top}/{skill_dir}/SKILL.md").decode("utf-8", errors="replace")
                desc = _parse_description(md)
                digest = _sha256(md)
            except Exception:
                desc = ""
                digest = ""
            rel_name = skill_dir
            if scope and rel_name.startswith(scope + "/"):
                rel_name = rel_name[len(scope) + 1:]
            out.append(
                CatalogEntry(
                    name=rel_name, description=desc, path=skill_dir,
                    files=files, content_hash=digest,
                )
            )
    return sorted(out, key=lambda e: e.name)


async def _browse_async(url: str) -> list[dict]:
    async with httpx.AsyncClient(follow_redirects=True) as client:
        ch = _parse_clawhub(url)
        if ch is not None:
            # ``clawhub://``  → trending list.
            # ``clawhub://<slug>`` → single skill detail (one-item catalog).
            if ch.is_hub_root:
                entries = await _browse_clawhub(client)
            else:
                items = await _clawhub_list(client, query=ch.slug)
                pick = next((it for it in items if it.get("slug") == ch.slug), None)
                entries = [
                    CatalogEntry(
                        name=ch.slug,
                        description=(pick or {}).get("summary", "") if pick else "",
                        path=ch.slug, files=[], content_hash="",
                    )
                ]
            return [
                {
                    "name": e.name, "description": e.description, "path": e.path,
                    "files": e.files, "content_hash": e.content_hash,
                }
                for e in entries
            ]
        gh = _parse_github(url)
        if gh is not None:
            entries = await _browse_github(client, gh)
        elif url.endswith(".json"):
            try:
                entries = await _browse_index(client, url)
            except httpx.HTTPStatusError as e:
                parsed = urlparse(url)
                if (
                    e.response is not None
                    and e.response.status_code == 404
                    and parsed.hostname == "raw.githubusercontent.com"
                ):
                    parts = parsed.path.strip("/").split("/")
                    if len(parts) >= 3:
                        entries = await _browse_github(
                            client, GhRepo(owner=parts[0], repo=parts[1], ref=parts[2])
                        )
                    else:
                        raise
                else:
                    raise
        else:
            entries = await _browse_index(client, url)
    return [
        {
            "name": e.name, "description": e.description, "path": e.path,
            "files": e.files, "content_hash": e.content_hash,
        }
        for e in entries
    ]


def diff(url: str, namespace: str | None = None) -> dict:
    """Compare each catalog entry's upstream SKILL.md hash against the
    locally-cached copy. Returns ``{installed, outdated, missing,
    up_to_date}`` lists of names (each name is the *namespaced* full
    name as the loader sees it)."""
    entries = browse(url)
    ns = namespace if namespace is not None else _default_namespace(url)
    cache = remote_cache_dir()

    installed: list[str] = []
    outdated: list[str] = []
    missing: list[str] = []
    up_to_date: list[str] = []

    for e in entries:
        full = _apply_namespace(e["name"], ns)
        local = cache / full / "SKILL.md"
        if not local.exists():
            missing.append(full)
            continue
        installed.append(full)
        try:
            local_hash = _sha256(local.read_text(encoding="utf-8"))
        except OSError:
            outdated.append(full)
            continue
        if e["content_hash"] and local_hash != e["content_hash"]:
            outdated.append(full)
        else:
            up_to_date.append(full)
    return {
        "installed": installed,
        "outdated": outdated,
        "missing": missing,
        "up_to_date": up_to_date,
    }


def browse(url: str) -> list[dict]:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_browse_async(url))
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(lambda: asyncio.run(_browse_async(url))).result()


# ---------------------------------------------------------------------------
# Install a single named skill (rather than pulling the whole source).
# ---------------------------------------------------------------------------

async def _install_one_async(
    url: str, name: str, namespace: str | None = None,
) -> str | None:
    ns = namespace if namespace is not None else _default_namespace(url)
    async with httpx.AsyncClient(follow_redirects=True) as client:
        ch = _parse_clawhub(url)
        if ch is not None:
            # ``name`` here is the slug we want to install.
            return await _pull_one_clawhub(client, name or ch.slug, ns)
        gh = _parse_github(url)
        if gh is not None:
            entries = await _browse_github(client, gh)
            match = next((e for e in entries if e.name == name), None)
            if match is None:
                return None
            full_name = _apply_namespace(match.name, ns)
            data, top = await _fetch_repo_zip(client, gh)
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                return _write_skill_from_zip(
                    zf, top, match.path, full_name, match.files,
                )
        # JSON index
        raw = await _get_text(client, url)
        index = Index.model_validate_json(raw)
        match = next((s for s in index.skills if s.name == name), None)
        if match is None:
            return None
        base = url.rsplit("/", 1)[0] + "/"
        sem = asyncio.Semaphore(_MAX_CONCURRENCY)
        return await _pull_one_indexed(client, base, match, sem, namespace=ns)


def install_one(url: str, name: str, namespace: str | None = None) -> str | None:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_install_one_async(url, name, namespace))
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(
            lambda: asyncio.run(_install_one_async(url, name, namespace))
        ).result()
