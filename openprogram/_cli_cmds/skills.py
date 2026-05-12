"""``openprogram skills`` handlers."""
from __future__ import annotations

import os
import sys


def _cmd_skills_list(override_dirs, as_json: bool) -> int:
    """Print skills the runtime would discover, in override-precedence order."""
    from openprogram.agentic_programming.skills import default_skill_dirs, load_skills

    dirs = override_dirs or default_skill_dirs()
    skills = load_skills(dirs)

    if as_json:
        import json as _json
        print(_json.dumps([{
            "name": s.name,
            "description": s.description,
            "slug": s.slug,
            "file_path": s.file_path,
            "base_dir": s.base_dir,
        } for s in skills], indent=2))
        return 0

    print(f"Search dirs (override order):")
    for d in dirs:
        exists = "✓" if os.path.isdir(d) else "✗"
        print(f"  {exists}  {d}")
    if not skills:
        print("\n(no skills discovered)")
        return 0
    print(f"\nDiscovered {len(skills)} skill(s):\n")
    for s in skills:
        print(f"  {s.name}  ({s.slug})")
        print(f"    {s.description[:100]}")
        print(f"    {s.file_path}")
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
