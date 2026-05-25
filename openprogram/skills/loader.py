"""Five-source skill loader.

Merges skills from:

    1. Bundled    — ``openprogram/skills_bundled/<name>/``
    2. User       — ``~/.openprogram/skills/<name>/``
    3. Project    — ``<cwd>/skills/<name>/``
    4. Plugin     — registered via :func:`register_plugin_skills`
    5. RemoteCache— ``~/.openprogram/cache/skills/<name>/``

Conflict policy: later source wins (so a project skill overrides
the bundled one with the same name).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Iterable

from openprogram.memory.wiki import helpers as _h


SOURCES = ("bundled", "user", "project", "plugin", "remote-cache")


@dataclass
class Skill:
    name: str  # hierarchical path, e.g. "research/literature/survey"
    description: str
    category: str
    optional: bool
    allowed_tools: list[str]
    triggers: dict
    version: str
    source: str
    path: str  # absolute path to SKILL.md
    body: str = ""
    aliases: list[str] = field(default_factory=list)

    @property
    def path_segments(self) -> list[str]:
        return self.name.split("/") if self.name else []

    @property
    def leaf(self) -> str:
        return self.path_segments[-1] if self.path_segments else ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["path_segments"] = self.path_segments
        d["leaf"] = self.leaf
        return d


# Plugin-provided skill dirs, registered by plugin loader.
_PLUGIN_SKILL_DIRS: dict[str, Path] = {}


def register_plugin_skills(name: str, directory: str | os.PathLike) -> None:
    """Register a plugin's skill root. Plugin loader calls this when a
    plugin with ``entrypoints.skills`` is enabled."""
    _PLUGIN_SKILL_DIRS[name] = Path(directory)


def unregister_plugin_skills(name: str) -> None:
    _PLUGIN_SKILL_DIRS.pop(name, None)


# --- source directories ---------------------------------------------------

def bundled_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "skills_bundled"


def user_dir() -> Path:
    return Path.home() / ".openprogram" / "skills"


def project_dir(cwd: str | os.PathLike | None = None) -> Path:
    return Path(cwd or os.getcwd()) / "skills"


def remote_cache_dir() -> Path:
    return Path.home() / ".openprogram" / "cache" / "skills"


def _source_dirs(cwd: str | os.PathLike | None = None) -> list[tuple[str, Path]]:
    out: list[tuple[str, Path]] = [
        ("bundled", bundled_dir()),
        ("user", user_dir()),
        ("project", project_dir(cwd)),
    ]
    for plugin_name, d in _PLUGIN_SKILL_DIRS.items():
        out.append((f"plugin:{plugin_name}", d))
    out.append(("remote-cache", remote_cache_dir()))
    return out


# --- parsing --------------------------------------------------------------

def _bool(v) -> bool:
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    return s in ("true", "yes", "1", "on")


def _parse_skill_md(path: Path, source: str, root: Path) -> Skill | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    fm, body = _h.parse_frontmatter(text)
    # Hierarchical name from directory layout relative to the source root.
    rel_dir = path.parent.relative_to(root)
    name = rel_dir.as_posix() if str(rel_dir) != "." else (fm.get("name") or path.parent.name)
    description = fm.get("description", "") or ""
    if isinstance(description, list):
        description = ", ".join(description)
    category = fm.get("category", "") or ""
    if isinstance(category, list):
        category = category[0] if category else ""
    optional = _bool(fm.get("optional", False))
    allowed_tools = fm.get("allowed-tools", []) or fm.get("allowed_tools", []) or []
    if isinstance(allowed_tools, str):
        allowed_tools = [allowed_tools]
    # triggers may be a nested map — our flat parser stores it as a string list.
    # Accept either shape; if not a dict, fall back to {}.
    triggers_raw = fm.get("triggers", {})
    if isinstance(triggers_raw, dict):
        triggers = triggers_raw
    else:
        triggers = {}
    version = fm.get("version", "") or ""
    if isinstance(version, list):
        version = version[0] if version else ""
    aliases_raw = fm.get("aliases", []) or []
    if isinstance(aliases_raw, str):
        aliases_raw = [aliases_raw]
    aliases = [str(a).strip() for a in aliases_raw if str(a).strip()]
    return Skill(
        name=str(name),
        description=str(description),
        category=str(category),
        optional=optional,
        allowed_tools=list(allowed_tools),
        triggers=dict(triggers),
        version=str(version),
        source=source,
        path=str(path),
        body=body,
        aliases=aliases,
    )


def _iter_source_skills(source: str, root: Path) -> Iterable[Skill]:
    """Yield every ``SKILL.md`` found anywhere under ``root``.

    The skill's hierarchical name is its containing directory's path
    relative to ``root``, joined by ``/``. Example: ``root/research/lit/survey/SKILL.md``
    becomes name ``research/lit/survey``.
    """
    if not root.exists() or not root.is_dir():
        return
    for md in sorted(root.rglob("SKILL.md")):
        if not md.is_file():
            continue
        skill = _parse_skill_md(md, source, root)
        if skill is not None:
            yield skill


def list_skills(cwd: str | os.PathLike | None = None) -> list[Skill]:
    """Return merged list of skills from all sources.

    Later sources override earlier ones on name conflict.
    """
    by_name: dict[str, Skill] = {}
    for source, root in _source_dirs(cwd):
        for skill in _iter_source_skills(source, root):
            by_name[skill.name] = skill
    # Stable order: category then name.
    return sorted(by_name.values(), key=lambda s: (s.category or "~", s.name))


def get_skill(name: str, cwd: str | os.PathLike | None = None) -> Skill | None:
    """Exact name lookup (full hierarchical path)."""
    for skill in list_skills(cwd):
        if skill.name == name:
            return skill
    return None


class AmbiguousSkillError(LookupError):
    """Raised when a short name matches multiple skills."""

    def __init__(self, query: str, candidates: list[str]):
        super().__init__(
            f"ambiguous skill name {query!r}; candidates: {', '.join(candidates)}"
        )
        self.query = query
        self.candidates = candidates


def resolve(
    query: str,
    cwd: str | os.PathLike | None = None,
    skills: list[Skill] | None = None,
) -> Skill | None:
    """Resolve a short / fuzzy ``query`` to a single :class:`Skill`.

    Match priority (highest first):

    1. Exact full path (``research/literature/survey``)
    2. Exact alias
    3. Exact leaf-name (last segment) — unique only
    4. Exact suffix of the path (``literature/survey``) — unique only
    5. Substring of the full path — unique only

    Returns ``None`` if nothing matches. Raises
    :class:`AmbiguousSkillError` when a non-exact rule yields multiple
    candidates so the caller can surface options.
    """
    q = (query or "").strip().strip("/")
    if not q:
        return None
    pool = skills if skills is not None else list_skills(cwd)

    # 1. exact full path
    for s in pool:
        if s.name == q:
            return s
    # 2. exact alias
    alias_matches = [s for s in pool if q in s.aliases]
    if len(alias_matches) == 1:
        return alias_matches[0]
    if len(alias_matches) > 1:
        raise AmbiguousSkillError(q, [s.name for s in alias_matches])
    # 3. exact leaf
    leaf_matches = [s for s in pool if s.leaf == q]
    if len(leaf_matches) == 1:
        return leaf_matches[0]
    if len(leaf_matches) > 1:
        raise AmbiguousSkillError(q, [s.name for s in leaf_matches])
    # 4. exact suffix of full path
    suffix_matches = [
        s for s in pool
        if s.name.endswith("/" + q) or s.name == q
    ]
    if len(suffix_matches) == 1:
        return suffix_matches[0]
    if len(suffix_matches) > 1:
        raise AmbiguousSkillError(q, [s.name for s in suffix_matches])
    # 5. substring fuzzy on full path
    sub_matches = [s for s in pool if q.lower() in s.name.lower()]
    if len(sub_matches) == 1:
        return sub_matches[0]
    if len(sub_matches) > 1:
        raise AmbiguousSkillError(q, [s.name for s in sub_matches])
    return None


def complete(
    query: str,
    cwd: str | os.PathLike | None = None,
    limit: int = 20,
) -> list[dict]:
    """Return autocomplete candidates for slash-command / picker UI.

    Each entry: ``{name, leaf, parent, description, source, match}`` where
    ``match`` is one of ``prefix | leaf | suffix | substring | alias``.
    Ordered by match quality.
    """
    q = (query or "").strip().lstrip("/")
    skills = list_skills(cwd)
    if not q:
        return [
            {
                "name": s.name, "leaf": s.leaf,
                "parent": "/".join(s.path_segments[:-1]),
                "description": s.description, "source": s.source,
                "match": "all",
            }
            for s in skills[:limit]
        ]
    ql = q.lower()

    def entry(s: Skill, match: str) -> dict:
        return {
            "name": s.name,
            "leaf": s.leaf,
            "parent": "/".join(s.path_segments[:-1]),
            "description": s.description,
            "source": s.source,
            "match": match,
        }

    seen: set[str] = set()
    out: list[dict] = []

    def add(s: Skill, match: str) -> None:
        if s.name in seen:
            return
        seen.add(s.name)
        out.append(entry(s, match))

    # prefix on full path (so /research/ filters to that branch)
    for s in skills:
        if s.name.lower().startswith(ql):
            add(s, "prefix")
    # alias prefix
    for s in skills:
        if any(a.lower().startswith(ql) for a in s.aliases):
            add(s, "alias")
    # leaf prefix
    for s in skills:
        if s.leaf.lower().startswith(ql):
            add(s, "leaf")
    # suffix
    for s in skills:
        if s.name.lower().endswith("/" + ql):
            add(s, "suffix")
    # substring anywhere
    for s in skills:
        if ql in s.name.lower():
            add(s, "substring")
    return out[:limit]


def skill_resource_tree(skill: Skill) -> list[str]:
    """Return relative file paths under the skill dir, excluding SKILL.md itself."""
    root = Path(skill.path).parent
    if not root.exists():
        return []
    out: list[str] = []
    for p in sorted(root.rglob("*")):
        if p.is_dir() or p.name == "SKILL.md":
            continue
        out.append(str(p.relative_to(root)))
    return out
