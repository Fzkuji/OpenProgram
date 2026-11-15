"""Exemptions only tighten against a reference no session can move.

The reference is the last published source, else where this branch left
stable. Later commits on dev cannot launder a looser value into it.
"""
from __future__ import annotations

import ast
import subprocess
from collections.abc import Iterable, Iterator
from pathlib import Path

from . import corpus as loader
from . import policy
from .model import ERROR, Corpus, Finding

POLICY = "scripts/docs_site/governance/policy.py"
_LOOSER_IF_SMALLER = {"BASELINE_REASON_MIN"}
_REASONS = "LOOSENED_BECAUSE"


def _revisions(scope) -> list[str]:
    found = ["refs/publish/last"]
    done = subprocess.run(["git", "merge-base", "HEAD", "stable"], cwd=scope.root, capture_output=True, text=True)
    if done.returncode == 0 and done.stdout.strip():
        found.append(done.stdout.strip())
    return [*found, scope.base or "HEAD", "HEAD"]


def fixed_text(corpus: Corpus, path: str) -> str | None:
    """The file at the oldest fixed reference that holds it; None before it first existed."""
    for revision in _revisions(corpus.scope):
        text = loader.read_many(Path(corpus.scope.root), revision, [path])[path]
        if text is not None:
            return text
    return None


def constants(source: str) -> dict[str, object]:
    """The module's literal UPPERCASE assignments, without running it."""
    found: dict[str, object] = {}
    for node in ast.parse(source).body:
        target = node.targets[0] if isinstance(node, ast.Assign) else getattr(node, "target", None)
        if not isinstance(target, ast.Name) or not target.id.isupper() or node.value is None:
            continue
        value = node.value
        if isinstance(value, ast.Call) and getattr(value.func, "id", "") == "frozenset" and value.args:
            value = value.args[0]
        try:
            found[target.id] = ast.literal_eval(value)
        except ValueError:
            continue
    return found


def _looser(name: str, old: object, new: object) -> Iterator[tuple[str, str]]:
    """(reason key, what loosened) for each way ``new`` exempts more than ``old``."""
    if isinstance(new, (set, frozenset)) or (isinstance(new, tuple) and new and isinstance(new[0], str)):
        for item in sorted(set(new) - set(old)):
            yield item, f"{name} gains {item}"
        return
    pairs = zip(old, new) if isinstance(new, tuple) else [(old, new)]
    if any(b < a if name in _LOOSER_IF_SMALLER else b > a for a, b in pairs):
        yield name, f"{name} goes from {old} to {new}"


def check(corpus: Corpus) -> Iterable[Finding]:
    try:
        current = constants(corpus.read(POLICY))
    except (OSError, subprocess.CalledProcessError):
        return
    earlier_text = fixed_text(corpus, POLICY)
    if earlier_text is None:
        return
    earlier = constants(earlier_text)
    reasons = current.get(_REASONS, {})
    for name, value in current.items():
        if name == _REASONS or name not in earlier or type(earlier[name]) is not type(value):
            continue
        for key, what in _looser(name, earlier[name], value):
            if len(str(reasons.get(key, "")).strip()) < policy.BASELINE_REASON_MIN:
                yield Finding("policy.loosened", ERROR, POLICY, 0, f"{what}, with no recorded reason.",
                              f"revert it, or record why under {key!r} in {_REASONS}.", key)
