"""Built-in SkillTool — injects a SKILL.md into the model context.

``invoke(name)`` returns the SKILL.md text (including frontmatter)
and writes an invocation trace line to
``~/.openprogram/skills/invoke_log.jsonl``::

    {"ts": 1716624321.123, "skill": "deploy", "source": "user",
     "md_hash": "9a3c..."}
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Iterable

from .loader import get_skill, resolve, AmbiguousSkillError


def _log_path() -> Path:
    return Path.home() / ".openprogram" / "skills" / "invoke_log.jsonl"


def _append_log(entry: dict) -> None:
    p = _log_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def invoke(name: str, cwd: str | None = None) -> str:
    """Return the full SKILL.md text for ``name`` and record the call.

    ``name`` may be a full hierarchical path (``research/literature/survey``)
    or any short form the resolver accepts (alias / leaf / suffix /
    unambiguous substring). Ambiguous matches raise
    :class:`AmbiguousSkillError` with the candidate list.
    """
    skill = get_skill(name, cwd=cwd) or resolve(name, cwd=cwd)
    if skill is None:
        raise KeyError(f"skill not found: {name}")
    text = Path(skill.path).read_text(encoding="utf-8")
    md_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    _append_log({
        "ts": time.time(),
        "skill": skill.name,
        "query": name,
        "source": skill.source,
        "md_hash": md_hash,
    })
    return text


def read_trace(name: str | None = None, limit: int = 50) -> list[dict]:
    p = _log_path()
    if not p.exists():
        return []
    lines = p.read_text(encoding="utf-8").splitlines()
    rows: list[dict] = []
    for line in reversed(lines):
        try:
            row = json.loads(line)
        except Exception:
            continue
        if name is not None and row.get("skill") != name:
            continue
        rows.append(row)
        if len(rows) >= limit:
            break
    return rows


class SkillTool:
    """Wrapper exposed as a tool for the model.

    The framework should call ``SkillTool.invoke(name)`` and feed the
    returned string back as an assistant-visible message.
    """

    name = "Skill"
    description = "Load a skill's SKILL.md into context"

    @staticmethod
    def invoke(name: str, cwd: str | None = None) -> str:
        return invoke(name, cwd=cwd)

    @staticmethod
    def names(cwd: str | None = None) -> Iterable[str]:
        from .loader import list_skills
        return [s.name for s in list_skills(cwd=cwd)]
