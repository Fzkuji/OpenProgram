"""Which top-level names under the state directory the code writes, and how backup classifies them.

The scan reads source, never imports it, so a page check can run it on any
version of the tree. It follows the state directory through local names,
small helpers that return it, and module-level string constants.
"""
from __future__ import annotations

import ast
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
import re

BACKUP_FILE = "openprogram/operations/backup.py"
SCANNED_ROOT = "openprogram"
_STATE_CALLS = re.compile(r"^(?:get_state_dir|_state_dir|dev_state_dir)$")  # memory's own state_dir() is not this directory
_STATE_NAMES = re.compile(r"^(?:state_dir|state_root)$")
_NAME = re.compile(r"^[\w.+-]+$")


@dataclass(frozen=True)
class Classified:
    included: frozenset[str]
    credentials: frozenset[str]
    regenerated: frozenset[str]

    @property
    def known(self) -> frozenset[str]:
        return self.included | self.credentials | self.regenerated


def classified(backup_source: str) -> Classified:
    """The three tuples in backup.py, without importing it."""
    tuples: dict[str, tuple[str, ...]] = {}
    for node in ast.parse(backup_source).body:
        target = node.target if isinstance(node, ast.AnnAssign) else node.targets[0] if isinstance(node, ast.Assign) else None
        if isinstance(target, ast.Name) and target.id in {"INCLUDED", "CREDENTIAL_ENTRIES", "REGENERATED"} and node.value is not None:
            tuples[target.id] = tuple(ast.literal_eval(node.value))
    return Classified(*(frozenset(tuples.get(name, ())) for name in ("INCLUDED", "CREDENTIAL_ENTRIES", "REGENERATED")))


def _called_name(call: ast.Call) -> str:
    return call.func.attr if isinstance(call.func, ast.Attribute) else getattr(call.func, "id", "")


class _Scan:
    def __init__(self, tree: ast.Module) -> None:
        self.tree = tree
        self.strings = {
            node.targets[0].id: node.value.value for node in tree.body
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)}
        self.locals: set[str] = set()
        self.helpers: set[str] = set()
        self._settle()

    def is_state(self, node: ast.AST) -> bool:
        if isinstance(node, ast.Name):
            return node.id in self.locals or _STATE_NAMES.match(node.id) is not None
        if isinstance(node, ast.Attribute):
            return _STATE_NAMES.match(node.attr) is not None
        if isinstance(node, ast.Call):
            name = _called_name(node)
            if _STATE_CALLS.match(name) or name in self.helpers:
                return True
            if name in {"Path", "str", "resolve", "expanduser"}:
                inner = node.args[0] if node.args else node.func.value if isinstance(node.func, ast.Attribute) else None
                return inner is not None and self.is_state(inner)
        return False

    def _settle(self) -> None:
        while True:
            before = len(self.locals) + len(self.helpers)
            for node in ast.walk(self.tree):
                if isinstance(node, ast.Assign) and self.is_state(node.value):
                    self.locals.update(t.id for t in node.targets if isinstance(t, ast.Name))
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    returned = [part.value for part in ast.walk(node) if isinstance(part, ast.Return) and part.value is not None]
                    if len(returned) == 1 and self.is_state(returned[0]):
                        self.helpers.add(node.name)
            if len(self.locals) + len(self.helpers) == before:
                return

    def literal(self, node: ast.AST) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        return self.strings.get(node.id) if isinstance(node, ast.Name) else None

    def names(self) -> Iterable[str]:
        for node in ast.walk(self.tree):
            pieces: list[ast.AST] = []
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div) and self.is_state(node.left):
                pieces = [node.right]
            elif isinstance(node, ast.Call):
                if _called_name(node) == "joinpath" and isinstance(node.func, ast.Attribute) and self.is_state(node.func.value):
                    pieces = node.args[:1]
                elif _called_name(node) == "join" and len(node.args) >= 2 and self.is_state(node.args[0]):
                    pieces = node.args[1:2]
            for piece in pieces:
                text = self.literal(piece)
                if text:
                    first = text.split("/", 1)[0]
                    if _NAME.match(first):
                        yield first


def names_in_source(source: str) -> set[str]:
    return set(_Scan(ast.parse(source)).names())


def scan(root: Path) -> dict[str, set[str]]:
    """Top-level state name -> the source files that build it."""
    found: dict[str, set[str]] = {}
    for path in sorted((root / SCANNED_ROOT).rglob("*.py")):
        if "node_modules" in path.parts or "__pycache__" in path.parts:
            continue
        try:
            names = names_in_source(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue
        for name in names:
            found.setdefault(name, set()).add(path.relative_to(root).as_posix())
    return found
