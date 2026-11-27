"""The SessionStart hook, after magpie's ``internal/fasttools/session.go``
(MIT License, Copyright (c) yetone)."""
from __future__ import annotations

import json
import os
import stat
import subprocess
from typing import Any, BinaryIO, TextIO

from .search import SKIP_DIRS, git_env
from .server import from_bytes, go_quote, to_bytes, utf8_start

# SNAPSHOT_CAP is the most a snapshot says: it is in every turn's prompt
# from then on.
SNAPSHOT_CAP = 1500


def session_start(inp: BinaryIO | TextIO, out: TextIO) -> None:
    """The SessionStart hook: read the hook's input from inp and write what
    a new conversation is told of its workspace to out — its folder, its
    git state, what kind of project it is and what is at its top — so the
    first turns aren't spent finding out. Anything it can't read is left
    out; with nothing to say it writes {}, which changes nothing, so a hook
    that fails never stops a conversation."""
    try:
        b = inp.read(1 << 20)
    except Exception:
        b = b""
    if isinstance(b, str):
        b = b.encode("utf-8", "replace")
    d = workspace_of(b or b"")
    text = snapshot(d) if d else ""
    if text == "":
        out.write("{}\n")
        out.flush()
        return
    out.write(json.dumps({
        "injectSteps": [{"systemMessage": {"systemMessage": text}}],
    }, separators=(",", ":"), ensure_ascii=False) + "\n")
    out.flush()


def workspace_of(b: bytes) -> str:
    """The folder a SessionStart input names: its first workspace, else its
    cwd. The hook's own working folder is the plugin's, so it is never the
    workspace."""
    try:
        m = json.loads(b)
    except ValueError:
        m = None
    if not isinstance(m, dict):
        m = {}
    for k in ("workspacePaths", "workspace_paths", "workspaceRoots", "workspace_roots", "workspaces"):
        l = m.get(k)
        if isinstance(l, list):
            for v in l:
                d = as_dir(v)
                if d:
                    return d
    for k in ("cwd", "workspacePath", "workspace_path", "workspaceRoot", "workspace_root"):
        d = as_dir(m.get(k))
        if d:
            return d
    return ""


def as_dir(v: Any) -> str:
    """v as a folder that is there: a path, a file:// URI, or an object with
    one under path or uri."""
    if isinstance(v, dict):
        for k in ("path", "uri", "fsPath"):
            d = as_dir(v.get(k))
            if d:
                return d
        return ""
    if not isinstance(v, str):
        return ""
    s = v
    if s.startswith("file://"):
        s = s[len("file://"):]
    if len(s) > 2 and s[0] == "/" and s[2] == ":":
        s = s[1:]  # file:///C:/…
    if not os.path.isabs(s):
        return ""
    try:
        if stat.S_ISDIR(os.stat(s).st_mode):
            return os.path.normpath(s)
    except (OSError, ValueError):
        pass
    return ""


def snapshot(dir: str) -> str:
    """What a new conversation in dir is told of it."""
    lines = ["Workspace: " + dir + git_line(dir)]
    p = project_line(dir)
    if p:
        lines.append("Project: " + p)
    t = top_line(dir)
    if t:
        lines.append("Top level: " + t)
    lines.append(f"Batch independent work: batch_search for several searches, read_slices for several files, apply_edits for every edit of a change, run_checks for builds and tests side by side; pass cwd {go_quote(dir)}.")
    s = "\n".join(lines)
    b = to_bytes(s)
    if len(b) > SNAPSHOT_CAP:
        b = b[:SNAPSHOT_CAP - 1]
        while b and not utf8_start(b[-1]):
            b = b[:-1]
        # magpie leaves here the first byte of the character it cut, which
        # its JSON gives as U+FFFD; that byte goes too
        if b and b[-1] >= 0x80:
            b = b[:-1]
        s = from_bytes(b) + "…"
    return s


def git_line(dir: str) -> str:
    """dir's branch and what is changed there, briefly."""
    try:
        r = subprocess.run(
            ["git", "-C", dir, "status", "--porcelain=v1", "--branch", "--untracked-files=normal"],
            stdin=subprocess.DEVNULL, capture_output=True, env=git_env(), timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if r.returncode != 0:
        return ""
    rows = from_bytes(r.stdout).rstrip("\n").split("\n")
    branch = ""
    if rows and rows[0].startswith("## "):
        branch = rows[0][3:]
        i = branch.find("...")
        if i >= 0:
            # main...origin/main [ahead 1]: the branch, and how far off
            rest = branch[i + 3:]
            branch = branch[:i]
            j = rest.find("[")
            if j >= 0:
                branch += " " + rest[j:]
        rows = rows[1:]
    changed = [r.strip() for r in rows if r.strip() != ""]
    s = " (git"
    if branch:
        s += ": " + branch
    if not changed:
        return s + ", clean)"
    s += f", {len(changed)} changed: "
    n = min(len(changed), 12)
    s += ", ".join(changed[:n])
    if len(changed) > n:
        s += ", …"
    return s + ")"


def _exists(dir: str, name: str) -> bool:
    try:
        os.stat(os.path.join(dir, name))
        return True
    except OSError:
        return False


def project_line(dir: str) -> str:
    """The kinds of project dir is, by the files that say so."""
    kinds: list[str] = []

    def read(name: str) -> str:
        try:
            with open(os.path.join(dir, name), "rb") as fh:
                b = fh.read((1 << 20) + 1)
        except OSError:
            return ""
        if len(b) > 1 << 20:
            return ""
        return from_bytes(b)

    s = read("go.mod")
    if s:
        k = "Go"
        for l in s.split("\n"):
            t = l.strip()
            if t.startswith("module "):
                k += " module " + t[len("module "):].strip()
                break
        kinds.append(k)
    s = read("package.json")
    if s:
        p = _package_json(s)
        if p is not None:
            k = "Node"
            if p["name"]:
                k += " package " + p["name"]
            pm = ""
            if p["packageManager"]:
                pm = p["packageManager"].split("@", 1)[0]
            elif _exists(dir, "bun.lockb") or _exists(dir, "bun.lock"):
                pm = "bun"
            elif _exists(dir, "pnpm-lock.yaml"):
                pm = "pnpm"
            elif _exists(dir, "yarn.lock"):
                pm = "yarn"
            if pm:
                k += " (" + pm + ")"
            if p["workspaces"] is not None:
                k += ", workspaces"
            if p["scripts"]:
                names = sorted(p["scripts"], key=lambda n: n.encode("utf-8", "replace"))
                if len(names) > 12:
                    names = names[:12] + ["…"]
                k += ", scripts: " + " ".join(names)
            kinds.append(k)
    for file, kind in (
        ("Cargo.toml", "Rust (Cargo)"), ("pyproject.toml", "Python (pyproject)"), ("requirements.txt", "Python"),
        ("pom.xml", "Java (Maven)"), ("build.gradle", "JVM (Gradle)"), ("build.gradle.kts", "JVM (Gradle)"),
        ("Package.swift", "Swift package"), ("Gemfile", "Ruby"), ("composer.json", "PHP (Composer)"),
        ("mix.exs", "Elixir"), ("deno.json", "Deno"), ("CMakeLists.txt", "CMake"), ("Makefile", "Makefile"),
        ("pubspec.yaml", "Dart/Flutter"), ("build.zig", "Zig"),
    ):
        if _exists(dir, file) and kind not in kinds:
            kinds.append(kind)
    for f in ("AGENTS.md", "GEMINI.md", "CLAUDE.md", "README.md"):
        if _exists(dir, f):
            kinds.append("has " + f)
    return "; ".join(kinds)


def _package_json(s: str) -> dict[str, Any] | None:
    """package.json's name, scripts, packageManager and workspaces; None
    when it isn't one Go's json.Unmarshal would read into them."""
    try:
        m = json.loads(s)
    except ValueError:
        return None
    if m is None:
        m = {}
    if not isinstance(m, dict):
        return None

    def get(k: str) -> Any:
        if k in m:
            return m[k]
        for key in m:
            if key.lower() == k.lower():
                return m[key]
        return None

    name, scripts, pm, ws = get("name"), get("scripts"), get("packageManager"), get("workspaces")
    if name is not None and not isinstance(name, str):
        return None
    if pm is not None and not isinstance(pm, str):
        return None
    if scripts is not None and (not isinstance(scripts, dict) or not all(isinstance(v, str) or v is None for v in scripts.values())):
        return None
    return {"name": name or "", "packageManager": pm or "", "scripts": scripts or {}, "workspaces": ws}


def top_line(dir: str) -> str:
    """What is at dir's top, folders first."""
    try:
        with os.scandir(dir) as it:
            ents = sorted(it, key=lambda e: os.fsencode(e.name))
    except OSError:
        return ""
    dirs: list[str] = []
    files: list[str] = []
    for e in ents:
        n = e.name
        if n.startswith(".") or n in SKIP_DIRS:
            continue
        try:
            is_dir = e.is_dir(follow_symlinks=False)
        except OSError:
            is_dir = False
        if is_dir:
            dirs.append(n + "/")
        else:
            files.append(n)
    every = dirs + files
    if not every:
        return ""
    if len(every) > 40:
        return " ".join(every[:40]) + f" … ({len(every)} in all)"
    return " ".join(every)
