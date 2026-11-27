"""The stdio MCP server and what every tool shares, after magpie's
``internal/fasttools/fasttools.go`` (MIT License, Copyright (c) yetone).

It does in one call what an agent otherwise takes many turns for: several
searches at once (batch_search), several files' outlines and line ranges
(read_slices), every edit of a change, checked before any is written and
verified after (apply_edits), and independent checks run side by side
(run_checks).
"""
from __future__ import annotations

import json
import os
import stat
import sys
import threading
from typing import Any, BinaryIO, TextIO

from .plugin import PLUGIN_NAME
from .tools import INSTRUCTIONS, SERVER_NAME, tool_list

# OUT_CAP is the most text a tool gives back: Antigravity keeps a longer
# MCP result in a file of its own and gives the model a path to read
# instead, another turn.
OUT_CAP = 8000

# the longest request line read, as magpie's scanner buffer
_MAX_LINE = 32 << 20


class ToolError(Exception):
    """Why a tool call can't be made: its text goes back with isError."""


USAGE = """usage: openprogram fast-tools mcp | session-start

What Antigravity's fast-tools plugin starts, once OpenProgram has turned it
on:
  mcp            the batch_search, read_slices, apply_edits and run_checks MCP server (stdio)
  session-start  the SessionStart hook: a snapshot of the workspace for a new conversation"""


def run(args: list[str]) -> int:
    """``openprogram fast-tools …``: mcp serves the tools on stdin and
    stdout; session-start is the SessionStart hook."""
    sub = args[0] if args else ""
    if sub == "mcp":
        try:
            serve(sys.stdin.buffer, sys.stdout)
        except Exception as e:  # the scanner's error, as magpie returns it
            print(f"openprogram: {e}", file=sys.stderr)
            return 1
        return 0
    if sub == "session-start":
        from .session import session_start
        session_start(sys.stdin.buffer, sys.stdout)
        return 0
    if sub in ("-h", "--help", "help"):
        print(USAGE)
        return 0
    print(USAGE, file=sys.stderr)
    return 2


class _Server:
    def __init__(self, w: TextIO):
        self._mu = threading.Lock()
        self._w = w

    def send(self, v: Any) -> None:
        line = json.dumps(v, separators=(",", ":"), sort_keys=True, ensure_ascii=False)
        with self._mu:
            try:
                self._w.write(line + "\n")
                self._w.flush()
            except Exception:
                pass

    def handle(self, req: dict) -> None:
        method = _get(req, "method")
        result: Any = None
        rpc_err: Any = None
        if method == "initialize":
            result = {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": "1"},
                "instructions": INSTRUCTIONS,
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": tool_list()}
        elif method == "tools/call":
            p = _get(req, "params")
            name, arguments = "", None
            if isinstance(p, dict):
                n = _get(p, "name")
                name = n if isinstance(n, str) else ""
                arguments = _get(p, "arguments")
            try:
                text = call(name, arguments)
            except Exception as e:
                result = {"content": [{"type": "text", "text": str(e)}], "isError": True}
            else:
                result = {"content": [{"type": "text", "text": cap_text(text)}]}
        else:
            rpc_err = {"code": -32601, "message": "no method " + method}
        out: dict[str, Any] = {"jsonrpc": "2.0", "id": req.get("id", _get(req, "id"))}
        if rpc_err is not None:
            out["error"] = rpc_err
        else:
            out["result"] = result
        self.send(out)


def serve(r: BinaryIO | TextIO, w: TextIO) -> None:
    """Answer requests from r on w until r ends. Calls run side by side:
    a long run_checks doesn't hold a search behind it."""
    s = _Server(w)
    threads: list[threading.Thread] = []
    too_long = False
    while True:
        line = r.readline(_MAX_LINE + 1)
        if not line:
            break
        if isinstance(line, bytes):
            line = line.decode("utf-8", "replace")
        body = line.rstrip("\n").rstrip("\r")
        if len(line) > _MAX_LINE and not line.endswith("\n"):
            too_long = True
            break
        try:
            m = json.loads(body)
        except ValueError:
            continue
        if not isinstance(m, dict):
            continue
        method = _get(m, "method")
        if not isinstance(method, str) or method == "" or _key(m, "id") is None:
            continue  # a notification, or a reply to nothing asked
        t = threading.Thread(target=s.handle, args=(m,), daemon=True)
        t.start()
        threads.append(t)
    for t in threads:
        t.join()
    if too_long:
        raise ValueError("bufio.Scanner: token too long")


def call(name: str, args: Any) -> str:
    """Run one tool."""
    if args is None:
        args = {}
    if name == "batch_search":
        from .search import batch_search
        return batch_search(args)
    if name == "read_slices":
        from .slices import read_slices
        return read_slices(args)
    if name == "apply_edits":
        from .edits import apply_edits
        return apply_edits(args)
    if name == "run_checks":
        from .checks import run_checks
        return run_checks(args)
    raise ToolError(f"no tool {go_quote(name)}")


def cap_text(s: str) -> str:
    """Cut text to OUT_CAP, saying how much was left out."""
    b = to_bytes(s)
    if len(b) <= OUT_CAP:
        return s
    cut = OUT_CAP - 120
    while cut > 0 and not utf8_start(b[cut]):
        cut -= 1
    i = b.rfind(b"\n", 0, cut)
    if i > cut // 2:
        cut = i + 1
    return from_bytes(b[:cut]) + f"\n… {len(b) - cut} more bytes left out: narrow the query (a path, a smaller range, fewer queries)\n"


def utf8_start(b: int) -> bool:
    return b & 0xC0 != 0x80


def to_bytes(s: str) -> bytes:
    """s as UTF-8, a file's undecodable bytes as they were."""
    try:
        return s.encode("utf-8", "surrogateescape")
    except UnicodeEncodeError:
        return s.encode("utf-8", "replace")


def from_bytes(b: bytes) -> str:
    """b as text to show: a byte that isn't UTF-8 is U+FFFD, as Go's JSON
    encoder gives it."""
    return b.decode("utf-8", "replace")


def byte_len(s: str) -> int:
    return len(to_bytes(s))


def root(cwd: str) -> str:
    """The folder a call works in: its cwd when given, else the server's
    own, unless that is the plugin's folder, where Antigravity may start a
    plugin's server."""
    if not os.path.isabs(cwd):
        wd = os.getcwd()
        if os.path.basename(wd) == PLUGIN_NAME and os.path.basename(os.path.dirname(wd)) == "plugins":
            raise ToolError("give cwd, the workspace's absolute path")
        cwd = os.path.join(wd, cwd)
    try:
        st = os.stat(cwd)
    except OSError as e:
        raise ToolError(f"cwd {cwd}: {os_error_text(e, 'stat', cwd)}") from None
    if not stat.S_ISDIR(st.st_mode):
        raise ToolError(f"cwd {cwd} isn't a folder")
    return os.path.normpath(cwd)


def at(dir: str, p: str) -> str:
    """p in dir: p itself when absolute."""
    if p == "":
        return dir
    if os.path.isabs(p):
        return os.path.normpath(p)
    return os.path.normpath(os.path.join(dir, p.replace("/", os.sep)))


def shown(dir: str, p: str) -> str:
    """p as a result names it: relative to dir when inside it."""
    try:
        r = os.path.relpath(p, dir)
    except ValueError:
        return p
    if not r.startswith(".."):
        return r.replace(os.sep, "/")
    return p


# --- what Go's encoding/json and fmt do, as the tools' messages need them ---

def go_quote(s: str) -> str:
    """s as Go's %q writes it (near enough: a JSON string)."""
    return json.dumps(s, ensure_ascii=False)


def os_error_text(e: OSError, op: str, path: str) -> str:
    """An OS error as Go words it: ``open /x: no such file or directory``."""
    why = e.strerror or str(e)
    if why:
        why = why[0].lower() + why[1:]
    return f"{op} {path}: {why}"


def _key(d: dict, name: str) -> str | None:
    """The key of d that a Go struct field called name takes: the same
    name, else the same ignoring case."""
    if name in d:
        return name
    low = name.lower()
    for k in d:
        if isinstance(k, str) and k.lower() == low:
            return k
    return None


def _get(d: dict, name: str, default: Any = None) -> Any:
    k = _key(d, name)
    return default if k is None else d[k]


_KINDS = {
    "string": (str, "a string"),
    "int": (int, "an integer"),
    "bool": (bool, "a boolean"),
    "array": (list, "an array"),
    "object": (dict, "an object"),
}
_ZERO = {"string": "", "int": 0, "bool": False, "array": None, "object": None}


def _json_kind(v: Any) -> str:
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, (int, float)):
        return "number"
    if isinstance(v, str):
        return "string"
    if isinstance(v, list):
        return "array"
    if isinstance(v, dict):
        return "object"
    return "null"


def fields(raw: Any, spec: dict[str, str], where: str = "") -> dict[str, Any]:
    """raw read as a Go struct of spec's fields (name → string, int, bool,
    array, object, or one of those with ``?`` for a pointer that stays None
    when absent): a value of another type is an error, as json.Unmarshal's;
    a null or missing one is its zero value."""
    if not isinstance(raw, dict):
        raise ToolError(f"arguments: cannot read {_json_kind(raw)} as an object{where}")
    out: dict[str, Any] = {}
    for name, kind in spec.items():
        nullable = kind.endswith("?")
        kind = kind.rstrip("?")
        v = _get(raw, name)
        if v is None:
            out[name] = None if nullable else _ZERO[kind]
            continue
        typ, what = _KINDS[kind]
        ok = isinstance(v, typ)
        if kind == "int" and (isinstance(v, bool) or not isinstance(v, int)):
            ok = False
        if not ok:
            raise ToolError(f"arguments: {name}{where} is {_json_kind(v)}, not {what}")
        out[name] = v
    return out
