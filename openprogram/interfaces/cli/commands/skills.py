"""``openprogram skills`` handlers."""
from __future__ import annotations

import os
import sys


# ---------------------------------------------------------------------------
# Registry-driven verbs: search / install / update / remove
# ---------------------------------------------------------------------------
# These talk to ``openprogram.extensions.skills`` (the new hierarchical loader and
# ClawHub-aware discovery) directly, no web server needed.


def _cmd_skills_search(query: str, source: str | None = None, limit: int = 20) -> int:
    """Search for skills. Default source = ClawHub registry; ``--source`` can
    point at any URL the discovery module recognises (github://, https://github.com/...,
    JSON index URL, clawhub://, https://clawhub.ai/skills/<slug>)."""
    from openprogram.extensions.skills.discovery import browse

    url = source or "clawhub://"
    if url == "clawhub://" and query.strip():
        # ClawHub has a real /api/v1/search endpoint — use the
        # ``clawhub://?q=...`` form via _browse_clawhub by passing a
        # synthetic URL. Simpler: call browse with clawhub:// then
        # filter locally on description, because browse() returns
        # the trending list. To use real registry search we'd need to
        # extend the URL grammar — keep it simple, use substring filter.
        entries = browse(url)
        q = query.lower()
        entries = [
            e for e in entries
            if q in e["name"].lower() or q in (e.get("description") or "").lower()
        ][:limit]
    else:
        entries = browse(url)[:limit]

    if not entries:
        print("(no matches)")
        return 0
    for e in entries:
        name = e["name"]
        desc = (e.get("description") or "").strip().replace("\n", " ")
        if len(desc) > 80:
            desc = desc[:77] + "…"
        print(f"  {name:30s} {desc}")
    return 0


def _cmd_skills_install(spec: str, source: str | None = None) -> int:
    """Install a single skill. ``spec`` is the skill slug — interpreted
    relative to ``source`` (default ClawHub).

    Examples::

        openprogram skills install weather
        openprogram skills install pdf --source https://github.com/anthropics/skills/tree/main/skills
        openprogram skills install clawhub:weather   # explicit prefix form
    """
    from openprogram.extensions.skills.discovery import install_one

    src = source
    name = spec
    if ":" in spec and src is None:
        prefix, _, slug = spec.partition(":")
        if prefix == "clawhub":
            src, name = "clawhub://", slug
        elif prefix == "github" and slug:
            src, name = "github://" + slug, slug
    if src is None:
        src = "clawhub://"
    result = install_one(src, name)
    if result is None:
        print(f"Error: {name} not found in {src}", file=sys.stderr)
        return 1
    print(f"Installed: {result}")
    return 0


def _cmd_skills_update(all_flag: bool, name: str | None) -> int:
    """Re-pull outdated remote-cache skills.

    Without ``--all`` a name argument is required. With ``--all`` we
    walk every previously-registered discovery source and re-pull any
    skills whose local SKILL.md hash drifted from upstream.
    """
    from openprogram.extensions.skills.discovery import diff, install_one
    from openprogram.extensions.skills.loader import list_skills
    from openprogram.interfaces.server.routes.catalog.skills import _load_discovery_sources, DEFAULT_DISCOVERY_SUGGESTIONS

    if not all_flag and not name:
        print("Error: pass --all or a skill name", file=sys.stderr)
        return 2

    if name and not all_flag:
        # Single-skill update: find which source it came from by slug
        # prefix (the namespace) then install_one re-pulls.
        installed = next((s for s in list_skills() if s.name == name), None)
        if installed is None:
            print(f"Error: skill not installed: {name}", file=sys.stderr)
            return 1
        namespace = installed.path_segments[0] if "/" in installed.name else None
        # Find the source whose slug matches this namespace.
        sources = [s["url"] for s in DEFAULT_DISCOVERY_SUGGESTIONS
                   if s.get("slug") == namespace]
        sources += _load_discovery_sources()
        for src in sources:
            short = name.split("/", 1)[-1] if "/" in name else name
            try:
                r = install_one(src, short)
                if r:
                    print(f"Updated: {r}")
                    return 0
            except Exception:
                continue
        print(f"Error: could not resolve upstream for {name}", file=sys.stderr)
        return 1

    # --all: walk every source, find outdated, re-install each.
    sources: list[tuple[str, str | None]] = [
        (s["url"], s.get("slug")) for s in DEFAULT_DISCOVERY_SUGGESTIONS
    ]
    for url in _load_discovery_sources():
        if not any(u == url for u, _ in sources):
            sources.append((url, None))

    total = 0
    for url, slug in sources:
        try:
            d = diff(url, namespace=slug)
        except Exception as e:
            print(f"  [{url}] diff failed: {e}", file=sys.stderr)
            continue
        outdated = d.get("outdated", [])
        if not outdated:
            continue
        print(f"  [{url}] {len(outdated)} outdated")
        for full_name in outdated:
            short = full_name.split("/", 1)[-1] if "/" in full_name else full_name
            try:
                r = install_one(url, short, namespace=slug)
                if r:
                    print(f"    updated: {r}")
                    total += 1
            except Exception as e:
                print(f"    fail {full_name}: {e}", file=sys.stderr)
    print(f"\nUpdated {total} skill(s)")
    return 0


def _cmd_skills_remove(name: str) -> int:
    """Delete an installed skill — only allowed for project/user/remote-cache
    sources (plugin-provided skills are read-only)."""
    import shutil
    from openprogram.extensions.skills.loader import get_skill

    s = get_skill(name)
    if s is None:
        print(f"Error: skill not found: {name}", file=sys.stderr)
        return 1
    if s.source not in ("project", "user", "remote-cache"):
        print(f"Error: cannot remove skill from source {s.source!r}", file=sys.stderr)
        return 1
    skill_dir = os.path.dirname(s.path)
    shutil.rmtree(skill_dir, ignore_errors=True)
    print(f"Removed: {name}  ({skill_dir})")
    return 0


def _cmd_skills_list(override_dirs, as_json: bool) -> int:
    """Print the skills the runtime discovers.

    Without ``--dir`` that is the four external sources (remote-cache /
    plugin / user / project). With ``--dir`` it is exactly
    the directories named, read by the same loader so the rows mean the
    same thing either way."""
    from openprogram.extensions.skills import list_skills, load_skills
    skills = load_skills(override_dirs) if override_dirs else list_skills()

    if as_json:
        import json as _json
        print(_json.dumps([{
            "name": s.name,
            "description": s.description,
            "category": s.category,
            "source": s.source,
            "path": s.path,
            "version": s.version,
        } for s in skills], indent=2))
        return 0

    if not skills:
        print("(no skills discovered)")
        return 0
    # Group by source.
    by_source: dict[str, list] = {}
    for s in skills:
        by_source.setdefault(s.source, []).append(s)
    print(f"Discovered {len(skills)} skill(s) across {len(by_source)} source(s):\n")
    for source in sorted(by_source):
        rows = by_source[source]
        print(f"  [{source}] {len(rows)} skill(s)")
        for s in rows:
            desc = (s.description or "").strip().replace("\n", " ")
            if len(desc) > 70:
                desc = desc[:67] + "…"
            print(f"    {s.name:40s} {desc}")
        print()
    return 0


def _cmd_skills_doctor(override_dirs) -> int:
    """Scan skill dirs for broken SKILL.md files and shadowed names.

    Reads the same roots the loader reads — the four external sources, or
    exactly the ``--dir`` list when given — and walks them the same way,
    recursively, so a namespace directory holding nested skills
    (``anthropic-skills/docx/SKILL.md``) is not mistaken for a broken one.

    A name that appears in two sources is the override rule working, so it
    is reported as shadowing rather than counted as a problem. Within one
    source a name cannot repeat: it is the directory path."""
    from pathlib import Path as _Path

    from openprogram.state.frontmatter import parse_frontmatter
    from openprogram.extensions.skills.loader import _source_dirs

    if override_dirs:
        roots = [("explicit", _Path(d)) for d in override_dirs]
    else:
        roots = list(_source_dirs())
    issues: list[str] = []
    shadowed: list[str] = []
    # name -> (source, path) of the last source that defined it; the loader
    # walks sources low to high, so the last one is the one that wins.
    winner: dict[str, tuple[str, str]] = {}
    count = 0

    for source, root in roots:
        if not root.is_dir():
            print(f"[warn] skill dir does not exist: {root}")
            continue
        found = sorted(root.rglob("SKILL.md"))
        for entry in sorted(root.iterdir()):
            if entry.is_dir() and not any(
                p.is_relative_to(entry) for p in found
            ):
                issues.append(f"{entry}: missing SKILL.md")
        for skill_md in found:
            if not skill_md.is_file():
                continue
            count += 1
            try:
                text = skill_md.read_text(encoding="utf-8")
            except OSError as e:
                issues.append(f"{skill_md}: cannot read ({e})")
                continue
            fm, _body = parse_frontmatter(text)
            if not fm:
                issues.append(f"{skill_md}: no YAML front matter (--- ... --- block)")
                continue
            name = str(fm.get("name") or "").strip()
            description = str(fm.get("description") or "").strip()
            if not name:
                issues.append(f"{skill_md}: front matter missing `name`")
            if not description:
                issues.append(f"{skill_md}: front matter missing `description`")
            if not name:
                continue
            key = skill_md.parent.relative_to(root).as_posix()
            if key == ".":
                key = name
            if key in winner:
                prev_source, prev_path = winner[key]
                shadowed.append(
                    f"{key!r}: [{source}] {skill_md} overrides "
                    f"[{prev_source}] {prev_path}"
                )
            winner[key] = (source, str(skill_md))

    if shadowed:
        print(f"{len(shadowed)} skill(s) shadowed by a higher-priority source:")
        for line in shadowed:
            print(f"  - {line}")
        print()
    if not issues:
        print(f"All skill dirs OK ({count} SKILL.md discovered, "
              f"{len(winner)} distinct skill(s)).")
        return 0
    print(f"Found {len(issues)} issue(s):")
    for issue in issues:
        print(f"  - {issue}")
    return 1


def _cmd_install_skills(target=None):
    """Copy a source checkout's ``skills/`` folder into Claude Code or Gemini CLI.

    OpenProgram ships no bundled skills, so this only does something in a
    checkout that has its own ``skills/`` folder; skills by name come from
    ``openprogram skills install <slug>``.
    """
    import shutil

    home = os.path.expanduser("~")
    targets = {}
    if shutil.which("claude"):
        targets["claude"] = os.path.join(home, ".claude", "skills")
    if shutil.which("gemini"):
        targets["gemini"] = os.path.join(home, ".gemini", "skills")

    if target:
        if target not in targets:
            print(f"Error: {target} CLI not found. Install it first.")
            sys.exit(1)
        targets = {target: targets[target]}

    if not targets:
        print("No CLI tools found. Install Claude Code or Gemini CLI first:")
        print("  npm i -g @anthropic-ai/claude-code")
        print("  npm i -g @google/gemini-cli")
        sys.exit(1)

    import openprogram
    pkg_dir = os.path.dirname(os.path.dirname(openprogram.__file__))
    skills_dir = os.path.join(pkg_dir, "skills")

    if not os.path.isdir(skills_dir):
        print("Error: this installation has no skills/ folder to copy; OpenProgram ships no bundled skills.")
        print("Install a skill by name instead:")
        print("  openprogram skills install <slug>")
        sys.exit(1)

    for name, dest in targets.items():
        os.makedirs(dest, exist_ok=True)
        count = 0
        for item in os.listdir(skills_dir):
            src = os.path.join(skills_dir, item)
            dst = os.path.join(dest, item)
            if os.path.isdir(src):
                if os.path.exists(dst):
                    shutil.rmtree(dst)
                shutil.copytree(src, dst)
                count += 1
            elif os.path.isfile(src):
                shutil.copy2(src, dst)
                count += 1
        print(f"  Installed {count} skills to {dest} ({name})")

    print("\nDone! Your agent can now use agentic functions via natural language.")
