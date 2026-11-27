"""batch_search, after magpie's ``internal/fasttools/search.go`` (MIT
License, Copyright (c) yetone).

magpie's regexes are Go's RE2; these are Python's ``re``, which reads RE2's
everyday syntax alike but backtracks, so a pathological pattern can take
long where RE2 wouldn't.
"""
from __future__ import annotations

import os
import re
import stat
import subprocess
import threading
from dataclasses import dataclass, field
from typing import Any, Callable

from .server import (
    OUT_CAP,
    ToolError,
    fields,
    from_bytes,
    go_quote,
    root,
    to_bytes,
    utf8_start,
)

MAX_QUERIES = 20
# a file bigger than this isn't searched: a bundle, a lock file, data
MAX_SEARCH_FILE = 2 << 20
# files listed at most: a home folder given as cwd isn't walked whole
MAX_LISTED = 200000
LINE_CUT = 240


@dataclass
class Query:
    kind: str = ""
    pattern: str = ""
    path: str = ""
    glob: str = ""
    ignore_case: bool = False
    context: int = 0
    max: int = 0


_QUERY_SPEC = {"kind": "string", "pattern": "string", "path": "string", "glob": "string",
               "ignoreCase": "bool", "context": "int", "max": "int"}


def _query(raw: Any, i: int) -> Query:
    f = fields(raw, _QUERY_SPEC, f" (query {i + 1})")
    return Query(f["kind"], f["pattern"], f["path"], f["glob"], f["ignoreCase"], f["context"], f["max"])


def batch_search(raw: Any) -> str:
    a = fields(raw, {"cwd": "string", "queries": "array"})
    queries = [_query(q, i) for i, q in enumerate(a["queries"] or [])]
    if not queries:
        raise ToolError("no queries")
    if len(queries) > MAX_QUERIES:
        raise ToolError(f"{len(queries)} queries: at most {MAX_QUERIES} in one call")
    dir = root(a["cwd"])
    # the files are listed once, for every query that needs them
    once = threading.Lock()
    listed: dict[str, Any] = {}

    def list_() -> list[str]:
        with once:
            if "files" not in listed and "err" not in listed:
                try:
                    listed["files"] = list_files(dir)
                except Exception as e:
                    listed["err"] = e
        if "err" in listed:
            raise listed["err"]
        return listed["files"]

    out = [""] * len(queries)

    def one(i: int, q: Query) -> None:
        head = f"## {i + 1}. {q.kind} {q.pattern}"
        if q.path:
            head += " in " + q.path
        if q.glob:
            head += " (" + q.glob + ")"
        try:
            res = run_query(dir, q, list_)
        except Exception as e:
            out[i] = head + "\nerror: " + str(e)
            return
        out[i] = head + "\n" + res

    threads = [threading.Thread(target=one, args=(i, q), daemon=True) for i, q in enumerate(queries)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # each query gets a fair part of the room, so the first one's many
    # matches don't push out the rest
    share = OUT_CAP // len(out)
    for i, s in enumerate(out):
        if len(to_bytes(s)) > share and share > 200:
            out[i] = cap_to(s, share)
    return "\n\n".join(out)


def cap_to(s: str, n: int) -> str:
    """Cut s to about n bytes at a line's end, saying how many lines were left."""
    b = to_bytes(s)
    cut = n
    while cut > 0 and not utf8_start(b[cut]):
        cut -= 1
    i = b.rfind(b"\n", 0, cut)
    if i > 0:
        cut = i
    rest = b[cut:].count(b"\n")
    return from_bytes(b[:cut]) + f"\n… {rest} more lines (narrow it with path, glob or max)"


def _base(f: str) -> str:
    return f[f.rfind("/") + 1:]


def run_query(dir: str, q: Query, list_: Callable[[], list[str]]) -> str:
    most = q.max
    if most <= 0:
        most = 40
    most = min(most, 200)
    if q.kind == "git":
        return git_query(dir, q.pattern)
    if q.kind == "glob":
        files = list_()
        rx = glob_re(q.pattern)
        hit: list[str] = []
        n = 0
        for f in scoped(files, q):
            if rx.search(f) or "/" not in q.pattern and rx.search(_base(f)):
                n += 1
                if len(hit) < most:
                    hit.append(f)
        if n == 0:
            return "no files"
        s = "\n".join(hit)
        if n > len(hit):
            s += f"\n… {n} files in all"
        return s
    if q.kind in ("text", "regex"):
        pat = q.pattern
        if q.kind == "text":
            pat = re.escape(pat)
        flags = re.MULTILINE
        if q.ignore_case:
            flags |= re.IGNORECASE
        # ^ and $ are a line's: a file is first matched whole, so it is
        # skipped only when no line of it can match
        try:
            rx = re.compile(pat, flags)
        except re.error as e:
            raise ToolError(f"regex: {e}") from None
        files = list_()
        return grep(dir, scoped(files, q), rx, most, min(max(q.context, 0), 5))
    raise ToolError(f"kind {go_quote(q.kind)}: text, regex, glob or git")


def scoped(files: list[str], q: Query) -> list[str]:
    """The files under q's path and matching q's glob."""
    p = q.path.replace(os.sep, "/").strip("/")
    if p.startswith("./"):
        p = p[2:]
    g = None
    if q.glob and q.kind != "glob":
        try:
            g = glob_re(q.glob)
        except ToolError:
            g = None
    if p in ("", ".") and g is None:
        return files
    out = []
    for f in files:
        if p not in ("", ".") and f != p and not f.startswith(p + "/"):
            continue
        if g is not None and not g.search(f) and ("/" in q.glob or not g.search(_base(f))):
            continue
        out.append(f)
    return out


@dataclass
class _Hit:
    file: str
    lines: list[str] = field(default_factory=list)  # already formatted
    n: int = 0  # matches in the file


def grep(dir: str, files: list[str], rx: re.Pattern, most: int, ctx: int) -> str:
    """Search files side by side, keeping them in their listed order."""
    hits: list[_Hit | None] = [None] * len(files)
    mu = threading.Lock()
    state = {"next": 0, "found": 0}

    def worker() -> None:
        while True:
            with mu:
                if state["next"] >= len(files) or state["found"] >= most * 4:
                    return
                i = state["next"]
                state["next"] += 1
            h = grep_file(dir, files[i], rx, ctx)
            if h is not None:
                with mu:
                    hits[i] = h
                    state["found"] += h.n

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(os.cpu_count() or 1)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    b: list[str] = []
    shown_n = total = in_files = 0
    for h in hits:
        if h is None:
            continue
        total += h.n
        in_files += 1
        for l in h.lines:
            if shown_n >= most:
                break
            b.append(l + "\n")
            if not l.startswith("  ") and l != "--":
                shown_n += 1
    if total == 0:
        return "no matches"
    if total > shown_n:
        b.append(f"… {total} matches in {in_files} files")
        if state["next"] < len(files):
            b.append(" (search stopped early)")
        b.append("\n")
    return "".join(b).rstrip("\n")


def grep_file(dir: str, rel: str, rx: re.Pattern, ctx: int) -> _Hit | None:
    p = os.path.join(dir, rel.replace("/", os.sep))
    try:
        st = os.stat(p)
    except OSError:
        return None
    if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_SEARCH_FILE:
        return None
    try:
        with open(p, "rb") as fh:
            data = fh.read()
    except OSError:
        return None
    if b"\x00" in data[:8000]:
        return None
    # a CRLF line's $ is before its \r
    data = data.replace(b"\r\n", b"\n")
    text = from_bytes(data)
    if not rx.search(text):
        return None
    lines = text.split("\n")
    h = _Hit(file=rel)
    last = -1
    for i, l in enumerate(lines):
        if not rx.search(l):
            continue
        h.n += 1
        if ctx > 0:
            frm = max(i - ctx, last + 1)
            if last >= 0 and frm > last + 1:
                h.lines.append("--")
            for j in range(frm, i):
                h.lines.append(f"  {rel}:{j + 1}- {cut_line(lines[j])}")
        h.lines.append(f"{rel}:{i + 1}: {cut_line(l)}")
        last = i
        if ctx > 0:
            for j in range(i + 1, min(i + ctx, len(lines) - 1) + 1):
                if rx.search(lines[j]):
                    break
                h.lines.append(f"  {rel}:{j + 1}- {cut_line(lines[j])}")
                last = j
    return h


def cut_line(l: str) -> str:
    l = l.rstrip("\r")
    b = to_bytes(l)
    if len(b) <= LINE_CUT:
        return from_bytes(b)
    cut = LINE_CUT
    while cut > 0 and not utf8_start(b[cut]):
        cut -= 1
    return from_bytes(b[:cut]) + "…"


# SKIP_DIRS aren't walked when the folder isn't a git checkout.
SKIP_DIRS = [".git", "node_modules", ".hg", ".svn", ".venv", "venv", "__pycache__", ".next", ".turbo", ".cache", "target", "dist", "build", ".gradle", ".idea", "Pods", "DerivedData"]


def list_files(dir: str) -> list[str]:
    """The files under dir, slash-separated and relative: what git tracks
    and doesn't ignore in a checkout, the folder walked otherwise."""
    try:
        r = subprocess.run(
            ["git", "-C", dir, "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            stdin=subprocess.DEVNULL, capture_output=True, env=git_env(), timeout=15,
        )
        ok = r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        ok = False
    if ok:
        files: list[str] = []
        for f in r.stdout.split(b"\x00"):
            if f:
                files.append(os.fsdecode(f))
            if len(files) >= MAX_LISTED:
                break
        # a file git still lists but that's deleted isn't searched
        return [f for i, f in enumerate(files) if i == 0 or f != files[i - 1]]
    return _walk(dir)


def _walk(dir: str) -> list[str]:
    """The folder walked as Go's filepath.WalkDir does: in name order, links
    not followed, a folder it can't read left out."""
    files: list[str] = []
    st = os.lstat(dir)  # the root's error is the walk's
    if not stat.S_ISDIR(st.st_mode):
        if stat.S_ISREG(st.st_mode):
            files.append(".")
        return files

    def walk(d: str) -> bool:
        try:
            with os.scandir(d) as it:
                ents = sorted(it, key=lambda e: os.fsencode(e.name))
        except OSError:
            if d == dir:
                raise
            return True
        for e in ents:
            try:
                is_dir = e.is_dir(follow_symlinks=False)
                is_reg = e.is_file(follow_symlinks=False)
            except OSError:
                continue
            if is_dir:
                if e.name in SKIP_DIRS:
                    continue
                if not walk(e.path):
                    return False
                continue
            if not is_reg:
                continue
            files.append(os.path.relpath(e.path, dir).replace(os.sep, "/"))
            if len(files) >= MAX_LISTED:
                return False
        return True

    walk(dir)
    return files


def glob_re(glob: str) -> re.Pattern:
    """glob as a regexp over a slash-separated path: * and ? within a name,
    ** across folders, {a,b} for either."""
    glob = glob.replace(os.sep, "/")
    if glob.startswith("./"):
        glob = glob[2:]
    b = ["^"]
    in_brace = False
    i = 0
    while i < len(glob):
        c = glob[i]
        if c == "*" and i + 1 < len(glob) and glob[i + 1] == "*":
            i += 1
            if i + 1 < len(glob) and glob[i + 1] == "/":
                i += 1
                b.append("(?:.*/)?")
            else:
                b.append(".*")
        elif c == "*":
            b.append("[^/]*")
        elif c == "?":
            b.append("[^/]")
        elif c == "{":
            in_brace = True
            b.append("(?:")
        elif c == "}" and in_brace:
            in_brace = False
            b.append(")")
        elif c == "," and in_brace:
            b.append("|")
        elif c == "[":
            j = glob.find("]", i)
            if j < 0:
                b.append(r"\[")
                i += 1
                continue
            j -= i
            cls = glob[i + 1:i + j]
            if cls.startswith("!"):
                cls = "^" + cls[1:]
            b.append("[" + cls + "]")
            i += j
        else:
            b.append(re.escape(c))
        i += 1
    b.append(r"\Z")  # Go's $: the end of the text, not before a last \n
    try:
        return re.compile("".join(b))
    except re.error as e:
        raise ToolError(f"glob {go_quote(glob)}: {e}") from None


# GIT_READ are the git commands a git query runs: ones that only read.
GIT_READ = ["status", "diff", "log", "show", "blame", "ls-files", "grep", "branch", "rev-parse", "shortlog", "describe", "ls-tree", "cat-file", "merge-base", "reflog", "tag"]

# GIT_NEVER are options that would have a read-only command write a file or
# run a program: a pager, an external diff, a text conversion.
GIT_NEVER = ["--output", "-O", "--open-files-in-pager", "--ext-diff", "--textconv", "--filters", "--exec", "--upload-pack"]

# GIT_LISTING are the options branch and tag take only to list; a word that
# isn't an option names a branch or tag to make, unless one of LIST_WITH
# says it is a pattern or a commit to list by.
GIT_LISTING = {
    "branch": ["-a", "--all", "-r", "--remotes", "-v", "-vv", "--verbose", "-l", "--list", "--contains", "--no-contains", "--merged", "--no-merged", "--points-at", "--show-current", "--sort", "--format", "--no-color", "--column", "--no-column", "-i", "--ignore-case"],
    "tag": ["-l", "--list", "-n", "--contains", "--no-contains", "--merged", "--no-merged", "--points-at", "--sort", "--format", "--no-color", "--column", "--no-column", "-i", "--ignore-case"],
}
LIST_WITH = ["-l", "--list", "--contains", "--no-contains", "--merged", "--no-merged", "--points-at", "--sort", "--format"]


def only_listing(sub: str, args: list[str]) -> None:
    """Raise why git sub args would change something, for branch, tag and
    reflog, which list but can also make, move and delete."""
    if sub == "reflog":
        if args and args[0] in ("expire", "delete"):
            raise ToolError(f"git reflog {args[0]}: only listing")
        return
    allowed = GIT_LISTING.get(sub)
    if allowed is None:
        return
    pattern = any(w.split("=", 1)[0] in LIST_WITH for w in args)
    for w in args:
        if not w.startswith("-"):
            if not pattern:
                raise ToolError(f"git {sub} {w}: only listing (add --list to list by a pattern)")
            continue
        k = w.split("=", 1)[0]
        if k not in allowed and not (sub == "tag" and w.startswith("-n")):
            raise ToolError(f"git {sub} {w}: only listing")


def git_query(dir: str, args: str) -> str:
    a = args.strip()
    if a.startswith("git "):
        a = a[4:]
    words = split_words(a.strip())
    if not words:
        raise ToolError("no git command")
    sub = words[0]
    if sub not in GIT_READ:
        raise ToolError(f"git {sub}: only read-only commands ({', '.join(GIT_READ)})")
    for w in words[1:]:
        if w == "--":
            break
        for n in GIT_NEVER:
            if w == n or w.startswith(n + "=") or len(n) == 2 and w.startswith(n) and not w.startswith("--"):
                raise ToolError(f"git {sub} {w}: not in a read-only search")
    only_listing(sub, words[1:])
    full = ["--no-pager", "-C", dir, sub]
    if sub in ("diff", "log", "show"):
        full += ["--no-ext-diff", "--no-textconv", "--no-color"]
    if sub == "grep":
        full += ["-n", "--no-color"]
    full += words[1:]
    try:
        r = subprocess.run(["git", *full], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, env=git_env(), timeout=20)
    except FileNotFoundError:
        raise ToolError('exec: "git": executable file not found in $PATH') from None
    except subprocess.TimeoutExpired as e:
        s = from_bytes(e.output or b"").rstrip("\n")
        if s == "":
            raise ToolError("signal: killed") from None
        raise ToolError(f"signal: killed: {s}") from None
    except OSError as e:
        raise ToolError(str(e)) from None
    s = from_bytes(r.stdout).rstrip("\n")
    if r.returncode != 0:
        why = f"exit status {r.returncode}" if r.returncode > 0 else "signal: killed"
        if s == "":
            raise ToolError(why)
        if sub == "grep" and s.strip() == "":
            return "no matches"
        raise ToolError(f"{why}: {s}")
    if s == "":
        return "(nothing)"
    return s


def git_env() -> dict[str, str]:
    """git's environment with nothing that would run a program."""
    env = {"GIT_PAGER": "cat", "PAGER": "cat", "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"}
    for k, v in os.environ.items():
        if k in ("GIT_PAGER", "PAGER", "GIT_EXTERNAL_DIFF", "GIT_TERMINAL_PROMPT", "GIT_OPTIONAL_LOCKS", "GIT_EDITOR", "GIT_SSH_COMMAND"):
            continue
        env[k] = v
    return env


def split_words(s: str) -> list[str]:
    """s read as a shell would its words: '…' and "…" quote, a backslash
    escapes the next character. Nothing is expanded."""
    words: list[str] = []
    cur: list[str] = []
    in_word = False
    q = ""
    i = 0
    while i < len(s):
        c = s[i]
        if q:
            if c == q:
                q = ""
            elif c == "\\" and q == '"' and i + 1 < len(s):
                i += 1
                cur.append(s[i])
            else:
                cur.append(c)
        elif c in ("'", '"'):
            q = c
            in_word = True
        elif c == "\\" and i + 1 < len(s):
            i += 1
            cur.append(s[i])
            in_word = True
        elif c in (" ", "\t", "\n"):
            if in_word:
                words.append("".join(cur))
                cur = []
                in_word = False
        elif c in "|;&<>`$":
            raise ToolError(f"git arguments are words only, not shell ({go_quote(c)})")
        else:
            cur.append(c)
            in_word = True
        i += 1
    if q:
        raise ToolError("an unclosed quote")
    if in_word:
        words.append("".join(cur))
    return words
