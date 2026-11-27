"""apply_edits, after magpie's ``internal/fasttools/edits.go`` (MIT
License, Copyright (c) yetone)."""
from __future__ import annotations

import os
import stat
import tempfile
from dataclasses import dataclass, field
from typing import Any

from .checks import Check, run_one
from .search import cut_line
from .server import ToolError, at, fields, go_quote, os_error_text, root, shown, to_bytes

MAX_EDITS = 100


@dataclass
class EditReq:
    path: str = ""
    old: str = ""
    new: str = ""
    replace_all: bool = False
    content: str | None = None


@dataclass
class FileEdit:
    """One file's edits, made in memory before any file is written."""
    path: str
    name: str
    was: str = ""  # "" for a file that isn't there
    exists: bool = False
    mode: int = 0o644
    now: str = ""
    changes: list[str] = field(default_factory=list)  # what each edit did, for the result
    failed: list[str] = field(default_factory=list)

    def apply(self, n: int, e: EditReq) -> str:
        """Make edit n on the text in memory, saying why it can't."""
        if e.content is not None:
            c = e.content
            if self.exists and "\r\n" in self.was and "\r\n" not in c:
                c = c.replace("\n", "\r\n")
            self.now = c
            self.changes.append(lines(line_count(c)) + " written")
            return ""
        if not self.exists and self.now == "":
            return f"edit {n}: the file isn't there (give content to create it)"
        if e.old == "":
            return f"edit {n}: old is empty (give content to write the whole file)"
        old, nw = e.old, e.new
        # the file's own line ends: an edit written with \n still matches a
        # CRLF file, and keeps it CRLF
        if "\r\n" in self.now and "\r\n" not in old:
            old = old.replace("\n", "\r\n")
            nw = nw.replace("\n", "\r\n")
        c = self.now.count(old)
        if c == 0:
            return f"edit {n}: old isn't in the file{nearest(self.now, e.old)}"
        if c > 1 and not e.replace_all:
            return f"edit {n}: old is in the file {c} times, at lines {line_list(self.now, old, 8)}: give more lines around it, or replaceAll"
        line = self.now[:self.now.index(old)].count("\n") + 1
        if e.replace_all:
            self.now = self.now.replace(old, nw)
            self.changes.append(f"{c} replaced from line {line}")
        else:
            self.now = self.now.replace(old, nw, 1)
            d = nw.count("\n") - old.count("\n")
            self.changes.append(f"line {line}{delta(d)}")
        return ""


def _edit(raw: Any, i: int) -> EditReq:
    f = fields(raw, {"path": "string", "old": "string", "new": "string", "replaceAll": "bool", "content": "string?"}, f" (edit {i + 1})")
    return EditReq(f["path"], f["old"], f["new"], f["replaceAll"], f["content"])


def _decode(b: bytes) -> str:
    # a file's bytes kept as they are through the edit
    return b.decode("utf-8", "surrogateescape")


def apply_edits(raw: Any) -> str:
    a = fields(raw, {"cwd": "string", "edits": "array", "bestEffort": "bool", "dryRun": "bool",
                     "verify": "string", "verifyTimeout": "int"})
    edits = [_edit(e, i) for i, e in enumerate(a["edits"] or [])]
    if not edits:
        raise ToolError("no edits")
    if len(edits) > MAX_EDITS:
        raise ToolError(f"{len(edits)} edits: at most {MAX_EDITS} in one call")
    dir = root(a["cwd"])
    order: list[FileEdit] = []
    by_path: dict[str, FileEdit] = {}
    failures = 0
    for i, e in enumerate(edits):
        if e.path == "":
            raise ToolError(f"edit {i + 1} has no path")
        p = at(dir, e.path)
        fe = by_path.get(p)
        if fe is None:
            fe = FileEdit(path=p, name=shown(dir, p))
            try:
                st = os.stat(p)
            except FileNotFoundError:
                st = None
            except OSError as err:
                raise ToolError(f"{fe.name}: {os_error_text(err, 'stat', p)}") from None
            if st is not None:
                if stat.S_ISDIR(st.st_mode):
                    raise ToolError(f"{fe.name} is a folder")
                try:
                    with open(p, "rb") as fh:
                        b = fh.read()
                except OSError as err:
                    raise ToolError(f"{fe.name}: {os_error_text(err, 'open', p)}") from None
                fe.was, fe.exists, fe.mode = _decode(b), True, stat.S_IMODE(st.st_mode) & 0o777
            fe.now = fe.was
            by_path[p] = fe
            order.append(fe)
        why = fe.apply(i + 1, e)
        if why:
            fe.failed.append(why)
            failures += 1
    out: list[str] = []
    if failures > 0 and not a["bestEffort"]:
        out.append(f"nothing written: {failures} of {len(edits)} edits can't be made\n")
        for fe in order:
            for f in fe.failed:
                out.append(f"{fe.name}: {f}\n")
        return "".join(out).rstrip("\n")
    if a["dryRun"]:
        out.append("dry run, nothing written\n")
    written = 0
    for fe in order:
        for f in fe.failed:
            out.append(f"{fe.name}: not made: {f}\n")
        if fe.now == fe.was and fe.exists:
            if fe.changes:
                out.append(f"{fe.name}: unchanged (the edits give the same text)\n")
            continue
        if not a["dryRun"]:
            try:
                write_file(fe.path, fe.now, fe.mode)
            except OSError as err:
                out.append(f"{fe.name}: not written: {err}\n")
                continue
            written += 1
        verb = "edited" if fe.exists else "created"
        out.append(f"{fe.name}: {verb} ({'; '.join(fe.changes)})\n")
    if a["dryRun"] or a["verify"] == "":
        return "".join(out).rstrip("\n")
    if written == 0:
        out.append("verify not run: nothing was written\n")
        return "".join(out).rstrip("\n")
    t = a["verifyTimeout"]
    if t <= 0:
        t = 120
    r = run_one(dir, Check(cmd=a["verify"], name="verify"), float(min(t, 900)))
    out.append(r.text(True))
    return "".join(out).rstrip("\n")


def line_count(s: str) -> int:
    """How many lines s has: a last line's \\n ends it, it doesn't start
    another."""
    if s == "":
        return 0
    return (s[:-1] if s.endswith("\n") else s).count("\n") + 1


def delta(d: int) -> str:
    if d > 0:
        return " +" + lines(d)
    if d < 0:
        return " -" + lines(-d)
    return ""


def lines(n: int) -> str:
    if n == 1:
        return "1 line"
    return f"{n} lines"


def line_list(text: str, s: str, most: int) -> str:
    """The lines s starts at in text, up to most of them."""
    found: list[str] = []
    off = 0
    while len(found) < most:
        i = text.find(s, off)
        if i < 0:
            break
        found.append(str(text[:i].count("\n") + 1))
        off = i + max(len(s), 1)
    return ", ".join(found)


def nearest(text: str, old: str) -> str:
    """Where old nearly is in text: the same lines with other spacing, or
    the line its first line is on."""
    ls = text.replace("\r\n", "\n").split("\n")
    want = old.replace("\r\n", "\n").split("\n")
    while want and want[0].strip() == "":
        want = want[1:]
    while want and want[-1].strip() == "":
        want = want[:-1]
    if not want:
        return ""

    def norm(s: str) -> str:
        return " ".join(s.split())

    # the same lines, other indenting or spacing
    for i in range(len(ls) - len(want) + 1):
        if all(norm(ls[i + j]) == norm(w) for j, w in enumerate(want)):
            return f"; it is at line {i + 1} with other spacing (or indenting): copy those lines as they are"
    # its first line, and how far the rest matches
    first = norm(want[0])
    if first == "":
        return ""
    best, best_n = -1, 0
    for i, l in enumerate(ls):
        if norm(l) != first:
            continue
        n = 1
        while n < len(want) and i + n < len(ls) and norm(ls[i + n]) == norm(want[n]):
            n += 1
        if n > best_n:
            best, best_n = i, n
    if best < 0:
        return "; nor is its first line: read the file again, it may have changed"
    where = best + best_n
    got = ""
    if where < len(ls):
        got = f", where the file has {go_quote(cut_line(ls[where].strip()))}"
    return f"; its first {best_n} of {len(want)} lines are at line {best + 1}, then it differs at line {where + 1}{got}"


def write_file(path: str, s: str, mode: int) -> None:
    """Write s to path through a file beside it renamed over it, so a
    reader never sees it half-written, keeping its mode."""
    # a link's file is written, and the link stays one
    try:
        os.stat(path)
        path = os.path.realpath(path)
    except OSError:
        pass
    d = os.path.dirname(path)
    os.makedirs(d, mode=0o755, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + os.path.basename(path) + ".", dir=d)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(to_bytes(s))
        os.chmod(tmp, mode)
        from openprogram.host._platform.filesystem import replace_file
        replace_file(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
