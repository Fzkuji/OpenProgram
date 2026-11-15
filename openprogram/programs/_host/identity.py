"""What an application or package is called and says about itself.

Every surface that lists an application (the Launchpad, the Applications
page, the Abilities detail) shows the same three things: a ``title``, a
one-sentence ``description`` and an ``icon`` glyph name. They come from,
in order:

* ``application.json``: its ``title``, ``description`` and ``icon``;
* a built-in Python application's ``__init__.py``: the module-level
  string constants ``TITLE``, ``DESCRIPTION`` and ``ICON``, read from
  the source without importing it;
* a package's ``README.md``: the first ``# Heading`` is the title and the
  first sentence of the paragraph after it is the description.

The title falls back to the directory name; the description and icon are
empty when nothing declares them.
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

#: Longest description kept; the surfaces clamp it to one line anyway.
MAX_DESCRIPTION = 300

_SENTENCE = re.compile(r"(.+?[.!?])(?=\s|$)", re.S)
_INLINE = re.compile(r"`([^`]*)`|\*\*([^*]+)\*\*|\[([^\]]+)\]\([^)]*\)")


def _clean(text: str) -> str:
    text = _INLINE.sub(lambda match: next(group for group in match.groups() if group is not None), text)
    return " ".join(text.split())


def first_sentence(paragraph: str) -> str:
    """The first sentence of ``paragraph``, as plain text on one line."""
    text = _clean(paragraph)
    match = _SENTENCE.match(text)
    sentence = match.group(1) if match else text
    if len(sentence) > MAX_DESCRIPTION:
        sentence = sentence[:MAX_DESCRIPTION - 1].rstrip() + "…"
    return sentence


def readme_identity(directory: Path) -> tuple[str, str]:
    """``(title, description)`` from ``README.md``; empty strings when absent."""
    try:
        lines = (directory / "README.md").read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return "", ""
    title = ""
    paragraph: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not title:
            if stripped.startswith("# "):
                title = stripped[2:].strip()
            continue
        if not stripped:
            if paragraph:
                break
            continue
        if stripped.startswith(("#", "```", "|", "<", "- ", "* ", ">")):
            if paragraph:
                break
            continue
        paragraph.append(stripped)
    return title, first_sentence(" ".join(paragraph)) if paragraph else ""


def _module_constants(path: Path) -> dict[str, str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, SyntaxError):
        return {}
    found: dict[str, str] = {}
    for node in tree.body:
        if (
            isinstance(node, ast.Assign) and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id in {"TITLE", "DESCRIPTION", "ICON"}
            and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)
        ):
            found[node.targets[0].id] = node.value.value
    return found


def _manifest(directory: Path) -> dict:
    try:
        raw = json.loads((directory / "application.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def identity(directory: Path) -> dict[str, str]:
    """``{"title", "description", "icon"}`` of the application at ``directory``."""
    directory = Path(directory)
    title = description = icon = ""
    manifest = _manifest(directory)
    if manifest:
        title = str(manifest.get("title") or "")
        description = str(manifest.get("description") or "")
        icon = str(manifest.get("icon") or "")
    else:
        constants = _module_constants(directory / "__init__.py")
        title = constants.get("TITLE", "")
        description = constants.get("DESCRIPTION", "")
        icon = constants.get("ICON", "")
    if not title or not description:
        readme_title, readme_description = readme_identity(directory)
        title = title or readme_title
        description = description or readme_description
    return {
        "title": title or directory.name,
        "description": " ".join(description.split()),
        "icon": icon,
    }
