"""Helpers shared by the three LSP tools.

The tools speak 1-based line and column numbers because that is what
every editor, traceback and `file:line:col` string the model has ever
read uses. LSP is 0-based on both axes, so the conversion happens here
and nowhere else.
"""
from __future__ import annotations

import os

from openprogram.lsp import ServerUnavailable, get_server
from openprogram.lsp.client import LanguageServer, uri_to_path

# Enough locations to see the shape of a symbol's use without burying
# the turn. Beyond this the model should narrow the question.
MAX_LOCATIONS = 50
MAX_DIAGNOSTICS = 50


def prepare(file_path: str) -> tuple[LanguageServer | None, str, str]:
    """Resolve the file, start/reuse its server, and open the current text.

    Returns ``(server, uri, error)``. When ``error`` is non-empty the
    server is None and the string is the tool result to hand back.
    """
    if not os.path.isabs(file_path):
        return None, "", f"Error: file must be an absolute path, got {file_path!r}"
    if not os.path.isfile(file_path):
        return None, "", f"Error: file not found: {file_path}"
    try:
        server = get_server(file_path)
        uri = server.open_file(file_path)
    except ServerUnavailable as exc:
        return None, "", str(exc)
    except Exception as exc:
        return None, "", f"Error: {type(exc).__name__}: {exc}"
    return server, uri, ""


def position(line: int, column: int) -> dict:
    """1-based tool coordinates to 0-based LSP coordinates."""
    return {"line": max(1, line) - 1, "character": max(1, column) - 1}


def format_location(location: dict, root: str) -> str:
    """One ``path:line:col`` line from an LSP Location or LocationLink."""
    uri = location.get("uri") or location.get("targetUri") or ""
    span = (location.get("range") or location.get("targetSelectionRange")
            or location.get("targetRange") or {})
    start = span.get("start") or {}
    path = uri_to_path(uri)
    try:
        path = os.path.relpath(path, root)
    except ValueError:
        pass
    return f"{path}:{start.get('line', 0) + 1}:{start.get('character', 0) + 1}"


def read_line(location: dict) -> str:
    """The source text at a location, stripped — context for a bare path."""
    uri = location.get("uri") or location.get("targetUri") or ""
    span = (location.get("range") or location.get("targetSelectionRange")
            or location.get("targetRange") or {})
    line_number = (span.get("start") or {}).get("line", 0)
    try:
        with open(uri_to_path(uri), "r", encoding="utf-8", errors="replace") as fh:
            for index, text in enumerate(fh):
                if index == line_number:
                    return text.strip()[:200]
    except OSError:
        pass
    return ""


def truncate(lines: list[str], limit: int, noun: str) -> str:
    if len(lines) <= limit:
        return "\n".join(lines)
    remainder = len(lines) - limit
    return "\n".join(lines[:limit] + [f"... {remainder} more {noun} not shown"])
