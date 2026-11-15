"""Rules of the transcript model, checked against the tracked source.

Each scan has no allowlist: code that breaks a rule fails the test. The web
side's rules live in apps/web/tests/chat/transcript/transcript-contract.test.mjs.
"""
from __future__ import annotations

import ast
import os
import re
from pathlib import Path

from scripts.generate_transcript_types import TARGET, render

ROOT = Path(__file__).resolve().parents[3]
TRANSCRIPT = "openprogram/interfaces/server/transcript/"
# The web side's own scan names the string it forbids.
WEB_CONTRACTS = "apps/web/tests/chat/transcript/transcript-contract.test.mjs"


# Build output and installed packages, never source.
_NOT_SOURCE = {"node_modules", ".next", "_frontend", "__pycache__", "out", "dist", "build", "test-results", "playwright-report"}


def _tracked(*paths: str, suffixes: tuple[str, ...] = (".py",)) -> list[Path]:
    """Source files under ``paths``; build output (``_frontend/``, ``.next/``) is never read."""
    found = []
    for path in paths:
        for directory, children, files in os.walk(ROOT / path):
            children[:] = [child for child in children if child not in _NOT_SOURCE and not child.startswith(".")]
            found += [Path(directory) / name for name in files if name.endswith(suffixes)]
    return sorted(found)


def _relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def test_the_generated_web_types_match_the_schema() -> None:
    assert TARGET.read_text(encoding="utf-8") == render(), "run python -m scripts.generate_transcript_types"


def test_nothing_asks_a_client_to_reread_a_whole_conversation() -> None:
    found = [
        _relative(path)
        for path in _tracked("openprogram", "apps", suffixes=(".py", ".ts", ".tsx", ".mjs", ".js"))
        if _relative(path) != WEB_CONTRACTS and "session_reload" in path.read_text(encoding="utf-8", errors="ignore")
    ]
    assert found == []


_OLD_EXTRA = re.compile(r"json\.loads\(\s*\w*extra\b")
_RECEIPT_HEADER = re.compile(r"\(\?:?task\|message\) from")


def test_only_the_legacy_module_reads_old_record_shapes() -> None:
    """Old ``extra`` strings and receipt headers are read in transcript/legacy.py alone."""
    found = []
    for path in _tracked(TRANSCRIPT, "openprogram/interfaces/server/ws_actions/session/"):
        if _relative(path) == f"{TRANSCRIPT}legacy.py":
            continue
        source = path.read_text(encoding="utf-8")
        found += [f"{_relative(path)}: {match.group(0)}" for pattern in (_OLD_EXTRA, _RECEIPT_HEADER) for match in pattern.finditer(source)]
    assert found == []


def _imported_modules(tree: ast.AST) -> list[str]:
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names.append(node.module)
        elif isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
            func = node.func
            called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if called in {"import_module", "__import__"}:
                names.append(node.args[0].value)
    return names


def test_runtime_and_state_never_import_the_server_interface() -> None:
    found = []
    for path in _tracked("openprogram/runtime", "openprogram/state"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        found += [
            f"{_relative(path)}: {name}"
            for name in _imported_modules(tree)
            if name == "openprogram.interfaces.server" or name.startswith("openprogram.interfaces.server.")
        ]
    assert found == []


def test_the_write_observer_only_queues() -> None:
    """What session_log calls after a write projects nothing and does no I/O: it queues."""
    tree = ast.parse((ROOT / TRANSCRIPT / "publisher.py").read_text(encoding="utf-8"))
    publisher = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "TranscriptPublisher")
    observers = [node for node in publisher.body if isinstance(node, ast.FunctionDef) and node.name.startswith("notice")]
    assert {node.name for node in observers} >= {"notice", "notice_foreign"}
    for node in observers:
        body = [statement for statement in node.body if not (isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant))]
        assert len(body) == 1, node.name
        call = body[0].value if isinstance(body[0], ast.Expr) else None
        assert isinstance(call, ast.Call) and ast.unparse(call.func) == "self._queue.put", node.name
