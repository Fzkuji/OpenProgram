"""read_slices, after magpie's ``internal/fasttools/slices.go`` (MIT
License, Copyright (c) yetone)."""
from __future__ import annotations

import os
import re
from typing import Any

from .search import cap_to
from .server import OUT_CAP, ToolError, at, fields, from_bytes, os_error_text, root, shown, to_bytes, utf8_start

MAX_FILES = 20
# a file of at most this many lines comes whole when no range is asked
WHOLE_LINES = 250
# the most outline lines given for one file
OUTLINE_MAX = 120


def _ranges(v: Any, i: int) -> list[list[int]]:
    if v is None:
        return []
    if not isinstance(v, list):
        raise ToolError(f"arguments: ranges (file {i + 1}) is not an array")
    out = []
    for r in v:
        if r is None:
            out.append([])
            continue
        if not isinstance(r, list) or any(isinstance(x, bool) or not isinstance(x, int) for x in r if x is not None):
            raise ToolError(f"arguments: ranges (file {i + 1}) are arrays of integers")
        out.append([0 if x is None else x for x in r])
    return out


def read_slices(raw: Any) -> str:
    a = fields(raw, {"cwd": "string", "files": "array"})
    files = []
    for i, f in enumerate(a["files"] or []):
        g = fields(f, {"path": "string", "ranges": "array", "outline": "bool"}, f" (file {i + 1})")
        files.append((g["path"], _ranges(g["ranges"], i), g["outline"]))
    if not files:
        raise ToolError("no files")
    if len(files) > MAX_FILES:
        raise ToolError(f"{len(files)} files: at most {MAX_FILES} in one call")
    dir = root(a["cwd"])
    parts = [slice_(dir, *f) for f in files]
    share = OUT_CAP // len(parts)
    for i, s in enumerate(parts):
        if len(to_bytes(s)) > share and share > 300:
            parts[i] = cap_to(s, share)
    return "\n\n".join(parts)


def _go_ints(r: list[int]) -> str:
    return "[" + " ".join(str(x) for x in r) + "]"


def slice_(dir: str, path: str, ranges_asked: list[list[int]], outline_asked: bool) -> str:
    p = at(dir, path)
    name = shown(dir, p)
    try:
        with open(p, "rb") as fh:
            b = fh.read()
    except OSError as e:
        return "## " + name + "\nerror: " + os_error_text(e, "read" if isinstance(e, IsADirectoryError) else "open", p)
    if b"\x00" in b[:8000]:
        return f"## {name}\na binary file, {len(b)} bytes"
    text = from_bytes(b)
    if text.endswith("\n"):
        text = text[:-1]
    lines = text.split("\n")
    out: list[str] = [f"## {name} ({len(lines)} lines)\n"]
    ranges = ranges_asked
    outline = outline_asked
    if not ranges:
        if len(lines) <= WHOLE_LINES:
            ranges = [[1, len(lines)]]
        else:
            outline = True
    if outline:
        o = outline_of(p, lines)
        if not o:
            if not ranges_asked:
                # nothing to outline: the start of it, then
                ranges = [[1, min(len(lines), 120)]]
        else:
            out.append("outline:\n")
            for i, l in enumerate(o):
                if i == OUTLINE_MAX:
                    out.append(f"… {len(o) - i} more\n")
                    break
                out.append(l + "\n")
    for r in ranges:
        if len(r) != 2:
            out.append(f"a range is [start, end], not {_go_ints(r)}\n")
            continue
        frm, to = max(r[0], 1), min(r[1], len(lines))
        if r[1] <= 0:
            to = len(lines)
        if frm > to:
            out.append(f"lines {r[0]}-{r[1]}: the file has {len(lines)}\n")
            continue
        if len(ranges) > 1 or frm != 1 or to != len(lines):
            out.append(f"lines {frm}-{to}:\n")
        for i in range(frm, to + 1):
            out.append(f"{i}\t{lines[i - 1].rstrip(chr(13))}\n")
    return "".join(out).rstrip("\n")


# OUTLINERS find a file's declarations by its extension: a line that
# matches is one, given with its number. They are lines, not a parse: a
# declaration over several lines shows its first.
# They are compiled the first time one is used, as magpie's lazyre.
_OUTLINER_SRC: dict[str, str] = {}
_compiled: dict[str, re.Pattern] = {}


def _add(rx: str, *exts: str) -> None:
    for e in exts:
        _OUTLINER_SRC[e] = rx


_add(r"^(func|type|var|const) |^\)$|^(var|const|type) \($", ".go")
_add(r"^\s*(export\s+)?(default\s+)?(async\s+)?(function\*?|class|interface|type|enum|namespace|abstract\s+class)\s|^\s*(export\s+)?(const|let|var)\s+\w+\s*=\s*(async\s*)?(\(|function|\w+\s*=>)|^\s*(public|private|protected|static|async|get|set|readonly|\s)*\s*[A-Za-z_$][\w$]*\s*\([^)]*\)\s*(:\s*[^{]+)?\{\s*$|^export\s+(default|\{)", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts", ".vue", ".svelte")
_add(r"^\s*(async\s+)?def\s|^\s*class\s|^[A-Z_][A-Z0-9_]*\s*=", ".py", ".pyi")
_add(r"^\s*(pub(\([^)]*\))?\s+)?(async\s+)?(unsafe\s+)?(fn|struct|enum|trait|impl|mod|type|const|static|macro_rules!)\b", ".rs")
_add(r"^\s*(public|private|protected|internal|static|final|abstract|sealed|open|override|data|suspend|inline|\s)*\s*(class|interface|enum|record|object|fun|struct|protocol|extension|func|init|deinit|var|let)\s+\w|^\s*(public|private|protected|internal|static|final|abstract|override|async|virtual|\s)+[\w<>\[\],.? ]+\s+\w+\s*\([^;]*$", ".java", ".kt", ".kts", ".cs", ".swift", ".scala")
_add(r"^\s*(class|struct|enum|namespace|union|typedef|template)\b|^[A-Za-z_][\w:<>,\s*&]*\s+\**[A-Za-z_][\w:~]*\s*\([^;]*$|^#define\s", ".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh", ".m", ".mm")
_add(r"^\s*(def|class|module)\s", ".rb")
_add(r"^\s*((public|private|protected|static|abstract|final)\s+)*(function|class|interface|trait|enum)\s", ".php")
_add(r"^\s*(local\s+)?function\s|^\s*[\w.]+\s*=\s*function\b", ".lua")
_add(r"^\s*(function\s+\w+|\w+\s*\(\)\s*\{)", ".sh", ".bash", ".zsh")
_add(r"^#{1,6}\s", ".md", ".mdx", ".markdown")
_add(r"^\s*\[[^\]]+\]\s*$", ".toml", ".ini", ".cfg")
_add(r"^[A-Za-z_][\w-]*:", ".yaml", ".yml")
_add(r"^\s*(@media|@layer|@keyframes|@supports)|^[^\s{][^{]*\{\s*$", ".css", ".scss", ".less")
_add(r"^\s*(CREATE|ALTER)\s+(TABLE|VIEW|INDEX|FUNCTION|PROCEDURE|TRIGGER)\b", ".sql")
_add(r"^\s*(func|class|enum|extends|signal|const)\s", ".gd")
_add(r"^\s*(defmodule|def|defp|defmacro)\s", ".ex", ".exs")
_add(r"^\s*(fn|pub fn|const|pub const|struct|enum|test)\s", ".zig")


def _rx(src: str) -> re.Pattern:
    # ASCII: RE2's \w, \s and \b are ASCII's
    r = _compiled.get(src)
    if r is None:
        r = _compiled[src] = re.compile(src, re.ASCII)
    return r


def _outliner(ext: str) -> re.Pattern | None:
    src = _OUTLINER_SRC.get(ext)
    return None if src is None else _rx(src)


# _JS_KEYWORD: `if (…) {` looks like a method to the JS outliner
_JS_KEYWORD = r"^(if|for|while|switch|catch|with|return|else|do|try|function\s*\()\b"


def outline_of(path: str, lines: list[str]) -> list[str] | None:
    """The declarations of the file at path, one line each with its line
    number; None for a kind of file with no outliner (magpie's Outline)."""
    ext = os.path.splitext(path)[1].lower()
    rx = _outliner(ext)
    if rx is None:
        return None
    out: list[str] = []
    in_block = False  # a Go var ( … ) or const ( … ) block
    for i, l in enumerate(lines):
        l = l.rstrip("\r")
        if ext == ".go":
            if l == ")":
                in_block = False
                continue
            elif l in ("var (", "const (", "type ("):
                in_block = True
            elif in_block and l.startswith("\t") and not l.startswith("\t\t") and not l.startswith("\t//") and l.strip() != "":
                out.append(f"{i + 1}: {cut_outline(l)}")
                continue
        if not rx.search(l):
            continue
        t = l.strip()
        if t == "" or t.startswith("//") or t.startswith("*"):
            continue
        if is_js(ext) and _rx(_JS_KEYWORD).search(t):
            continue
        out.append(f"{i + 1}: {cut_outline(l)}")
    return out


def is_js(ext: str) -> bool:
    return ext in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts", ".vue", ".svelte")


def cut_outline(l: str) -> str:
    l = l.rstrip(" \t{")
    l = l.replace("\t", "  ")
    b = to_bytes(l)
    if len(b) > 160:
        cut = 160
        while cut > 0 and not utf8_start(b[cut]):
            cut -= 1
        l = from_bytes(b[:cut]) + "…"
    return l
