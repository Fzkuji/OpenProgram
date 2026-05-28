"""``openprogram skills`` handlers."""
from __future__ import annotations

import os
import sys


# ---------------------------------------------------------------------------
# Registry-driven verbs: search / install / update / remove
# ---------------------------------------------------------------------------
# These talk to ``openprogram.skills`` (the new hierarchical loader and
# ClawHub-aware discovery) directly, no web server needed.


def _cmd_skills_search(query: str, source: str | None = None, limit: int = 20) -> int:
    """Search for skills. Default source = ClawHub registry; ``--source`` can
    point at any URL the discovery module recognises (github://, https://github.com/...,
    JSON index URL, clawhub://, https://clawhub.ai/skills/<slug>)."""
    from openprogram.skills.discovery import browse

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
    from openprogram.skills.discovery import install_one

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
    from openprogram.skills.discovery import diff, install_one
    from openprogram.skills.loader import list_skills
    from openprogram.webui.routes.skills import (
        _load_discovery_sources, DEFAULT_DISCOVERY_SUGGESTIONS,
    )

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
    sources (bundled and plugin-provided skills are read-only)."""
    import shutil
    from openprogram.skills.loader import get_skill

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
    """Print skills the runtime would discover from all five sources
    (bundled / user / project / plugin / remote-cache). When
    ``override_dirs`` is set we fall back to the legacy flat loader
    so callers passing ``--dir`` still work."""
    if override_dirs:
        # legacy path — flat dirs only
        from openprogram.agentic_programming.skills import load_skills
        skills = load_skills(override_dirs)
        if as_json:
            import json as _json
            print(_json.dumps([{
                "name": s.name,
                "description": s.description,
                "slug": s.slug,
                "file_path": s.file_path,
            } for s in skills], indent=2))
            return 0
        if not skills:
            # Match the non-legacy branch (line 211 below) so callers
            # passing ``--dir`` to an empty directory get the same
            # "(no skills discovered)" line as the default discovery
            # path. Without this the legacy branch silently printed
            # "Discovered 0 skill(s):" + no rows, which read as a
            # render bug.
            print("(no skills discovered)")
            return 0
        print(f"Discovered {len(skills)} skill(s):\n")
        for s in skills:
            print(f"  {s.name}  ({s.slug})")
            print(f"    {s.description[:100]}")
        return 0

    from openprogram.skills.loader import list_skills
    skills = list_skills()

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
    """Scan skill dirs for broken SKILL.md files and duplicate names."""
    from pathlib import Path as _Path

    from openprogram.agentic_programming.skills import (
        _parse_front_matter, default_skill_dirs,
    )

    dirs = override_dirs or default_skill_dirs()
    issues: list[str] = []
    seen_names: dict[str, str] = {}

    for d in dirs:
        root = _Path(d)
        if not root.is_dir():
            print(f"[warn] skill dir does not exist: {d}")
            continue
        for entry in sorted(root.iterdir()):
            if not entry.is_dir():
                continue
            skill_md = entry / "SKILL.md"
            if not skill_md.is_file():
                issues.append(f"{entry}: missing SKILL.md")
                continue
            try:
                text = skill_md.read_text(encoding="utf-8")
            except OSError as e:
                issues.append(f"{skill_md}: cannot read ({e})")
                continue
            fm = _parse_front_matter(text)
            if not fm:
                issues.append(f"{skill_md}: no YAML front matter (--- ... --- block)")
                continue
            name = (fm.get("name") or "").strip()
            description = (fm.get("description") or "").strip()
            if not name:
                issues.append(f"{skill_md}: front matter missing `name`")
            if not description:
                issues.append(f"{skill_md}: front matter missing `description`")
            if name and name in seen_names and seen_names[name] != str(skill_md):
                issues.append(
                    f"{skill_md}: duplicate name {name!r} "
                    f"(first seen at {seen_names[name]})"
                )
            if name:
                seen_names.setdefault(name, str(skill_md))

    if not issues:
        print(f"All skill dirs OK ({len(seen_names)} skill(s) discovered).")
        return 0
    print(f"Found {len(issues)} issue(s):")
    for issue in issues:
        print(f"  - {issue}")
    return 1


def _cmd_install_skills(target=None):
    """Install skills to Claude Code or Gemini CLI."""
    import shutil
    import tempfile
    import subprocess

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
        print("No CLI tools found. Install Codex CLI or Gemini CLI first:")
        print("  npm i -g @openai/codex && codex auth")
        print("  npm i -g @google/gemini-cli")
        sys.exit(1)

    import openprogram
    pkg_dir = os.path.dirname(os.path.dirname(openprogram.__file__))
    local_skills = os.path.join(pkg_dir, "skills")

    if os.path.isdir(local_skills):
        skills_dir = local_skills
    else:
        print("Downloading skills from GitHub...")
        tmp = tempfile.mkdtemp()
        try:
            subprocess.run(
                ["git", "clone", "--depth=1", "--filter=blob:none", "--sparse",
                 "https://github.com/Fzkuji/Agentic-Programming.git", tmp],
                check=True, capture_output=True,
            )
            subprocess.run(
                ["git", "sparse-checkout", "set", "skills"],
                cwd=tmp, check=True, capture_output=True,
            )
            skills_dir = os.path.join(tmp, "skills")
        except (subprocess.CalledProcessError, FileNotFoundError):
            print("Error: Failed to download skills. Install git or clone the repo manually:")
            print("  git clone https://github.com/Fzkuji/Agentic-Programming.git")
            print("  cp -r Agentic-Programming/skills/* ~/.claude/skills/")
            sys.exit(1)

    if not os.path.isdir(skills_dir):
        print("Error: skills/ directory not found.")
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
