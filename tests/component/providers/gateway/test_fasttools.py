"""The fast tools' MCP server, its Antigravity plugin and SessionStart hook,
after yetone/magpie's ``internal/fasttools/fasttools_test.go`` (MIT License,
Copyright (c) yetone)."""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from openprogram.models.gateway.agents.fasttools import plugin as plug
from openprogram.models.gateway.agents.fasttools import server, session
from openprogram.models.gateway.agents.fasttools.checks import tail_of
from openprogram.models.gateway.agents.fasttools.search import git_env
from openprogram.models.gateway.agents.fasttools.slices import outline_of
from openprogram.models.gateway.agents.fasttools.tools import SERVER_NAME, TOOL_NAMES


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    # the tests run in a home of their own, never the user's real files
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))


def tree(base: Path, files: dict[str, str]) -> str:
    """Write files (name → text) under a new folder."""
    d = base / "tree"
    d.mkdir()
    for name, s in files.items():
        p = d / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(s.encode("utf-8"))
    return str(d)


def must_call(name: str, v) -> str:
    return server.call(name, json.loads(json.dumps(v)))


def read(p) -> str:
    return Path(p).read_bytes().decode("utf-8")


def git(dir: str, *a: str) -> None:
    env = {**git_env(), "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}
    r = subprocess.run(["git", "-C", dir, "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                        "-c", "commit.gpgsign=false", *a], env=env, capture_output=True)
    assert r.returncode == 0, f"git {a}: {r.stdout!r} {r.stderr!r}"


def test_batch_search(tmp_path):
    """One batch_search answers every query in it, each under its own
    heading: text with context, regex without case, a glob over paths and
    one over names, and files in node_modules aren't searched."""
    dir = tree(tmp_path, {
        "a/one.go": "package a\n\nfunc Alpha() {}\n\n// beta lives here\nfunc Beta() {}\n",
        "b/two.ts": "export function gamma() {}\nconst BETA = 1\n",
        "docs/README.md": "# beta\n",
        "node_modules/x/beta.js": "beta beta beta\n",
        "bin.dat": "beta\x00\x01",
        "c/crlf.go": "package c\r\nfunc Bc() {}\r\n",
    })
    out = must_call("batch_search", {"cwd": dir, "queries": [
        {"kind": "text", "pattern": "beta lives", "context": 1},
        {"kind": "regex", "pattern": r"^func B\w+\(\) \{\}$", "ignoreCase": True},
        {"kind": "glob", "pattern": "**/*.ts"},
        {"kind": "glob", "pattern": "*.md"},
        {"kind": "text", "pattern": "beta", "ignoreCase": True, "glob": "*.ts"},
    ]})
    for want in [
        "## 1. text beta lives", "a/one.go:5: // beta lives here", "  a/one.go:4- ", "  a/one.go:6- func Beta() {}",
        "## 2. regex", "a/one.go:6: func Beta() {}", "c/crlf.go:2: func Bc() {}",
        "## 3. glob **/*.ts", "b/two.ts",
        "## 4. glob *.md", "docs/README.md",
        "## 5. text beta (*.ts)", "b/two.ts:2: const BETA = 1",
    ]:
        assert want in out, out
    assert "node_modules" not in out and "bin.dat" not in out, out
    assert "a/one.go:3" not in out, out


def test_batch_search_git_read_only(tmp_path):
    """git queries run read-only commands only: one that writes, runs a
    program or writes a file through a flag is turned away before git runs."""
    dir = tree(tmp_path, {"f.txt": "one\n"})
    git(dir, "init", "-q", "-b", "main")
    git(dir, "add", "f.txt")
    git(dir, "commit", "-qm", "first")
    out = must_call("batch_search", {"cwd": dir, "queries": [
        {"kind": "git", "pattern": "log --oneline"},
        {"kind": "git", "pattern": "push origin main"},
        {"kind": "git", "pattern": "branch -D main"},
        {"kind": "git", "pattern": "diff --output=" + os.path.join(dir, "x")},
        {"kind": "git", "pattern": "log; touch y"},
        {"kind": "git", "pattern": "-c core.pager=touch log"},
    ]})
    assert "first" in out, out
    for i, p in enumerate(out.split("## ")[2:]):
        assert p.count("\n") <= 3 and "error" in p.lower(), f"query {i + 2} wasn't turned away:\n{p}"
    for f in ("x", "y"):
        assert not os.path.exists(os.path.join(dir, f))
    git(dir, "rev-parse", "--verify", "main")  # the branch is still there


def test_read_slices(tmp_path):
    """A short file comes whole, a long one as its outline, and ranges as
    asked, numbered."""
    long = ['package p\n\nimport "fmt"\n\nconst (\n\tA = 1\n\tB = 2\n)\n\n']
    for i in range(300):
        long.append("// filler\n")
        if i == 150:
            long.append('func Middle(x int) error {\n\treturn fmt.Errorf("x")\n}\n')
    long.append("type T struct{}\n")
    dir = tree(tmp_path, {"short.txt": "a\nb\nc\n", "long.go": "".join(long)})
    out = must_call("read_slices", {"cwd": dir, "files": [
        {"path": "short.txt"},
        {"path": "long.go"},
        {"path": "long.go", "ranges": [[6, 7]]},
        {"path": "gone.txt"},
    ]})
    for want in [
        "## short.txt (3 lines)\n1\ta\n2\tb\n3\tc",
        "outline:", "6:   A = 1", "7:   B = 2", ": func Middle(x int) error", ": type T struct",
        "lines 6-7:\n6\t\tA = 1\n7\t\tB = 2",
        "## gone.txt\nerror:",
    ]:
        assert want in out, out
    assert not ("filler" in out and "lines " not in out), out


def test_outline_js():
    """The JS outliner lists declarations, not an if or a for that looks
    like a method."""
    lines = "export function a() {\n  if (x) {\n  }\n  for (const y of z) {\n  }\n}\nclass B {\n  run(x) {\n  }\n}\nconst c = (x) => x\n".split("\n")
    got = "\n".join(outline_of("f.ts", lines))
    for want in ["1: export function a()", "7: class B", "8:   run(x)", "11: const c = (x) => x"]:
        assert want in got, got
    assert "if (x)" not in got and "for (" not in got, got


def test_apply_edits_all_or_nothing(tmp_path):
    """apply_edits writes nothing when any edit can't be made, and says why,
    with where the text nearly is."""
    dir = tree(tmp_path, {"a.go": "package a\n\nfunc A() {\n\treturn\n}\n", "b.go": "package b\nvar x = 1\nvar x = 1\n"})
    out = must_call("apply_edits", {"cwd": dir, "edits": [
        {"path": "a.go", "old": "func A()", "new": "func AA()"},
        {"path": "a.go", "old": "    return\n}", "new": "x"},
        {"path": "b.go", "old": "var x = 1", "new": "var y = 2"},
        {"path": "c.go", "old": "x", "new": "y"},
    ]})
    for want in ["nothing written: 3 of 4 edits can't be made", "at line 4 with other spacing", "2 times, at lines 2, 3", "c.go: edit 4: the file isn't there"]:
        assert want in out, out
    assert "AA" not in read(os.path.join(dir, "a.go"))


def test_apply_edits(tmp_path):
    """Every edit is made: several in one file, a new file, a CRLF file kept
    CRLF, replaceAll, and a link's file written with the link left one."""
    dir = tree(tmp_path, {"a.go": "one\ntwo\nthree\n", "w.txt": "x\r\ny\r\n", "r.txt": "k k k\n", "real/t.txt": "old\n"})
    try:
        os.symlink(os.path.join(dir, "real", "t.txt"), os.path.join(dir, "link.txt"))
    except OSError as e:
        pytest.skip(f"no symlinks here: {e}")
    os.chmod(os.path.join(dir, "a.go"), 0o755)
    out = must_call("apply_edits", {"cwd": dir, "edits": [
        {"path": "a.go", "old": "one\n", "new": "uno\n"},
        {"path": "a.go", "old": "three", "new": "tres\nquattro"},
        {"path": "new/n.txt", "content": "fresh\n"},
        {"path": "w.txt", "old": "x\ny", "new": "x\nz"},
        {"path": "r.txt", "old": "k", "new": "q", "replaceAll": True},
        {"path": "link.txt", "old": "old", "new": "new"},
    ]})
    checks = {"a.go": "uno\ntwo\ntres\nquattro\n", "new/n.txt": "fresh\n", "w.txt": "x\r\nz\r\n", "r.txt": "q q q\n", "real/t.txt": "new\n"}
    for f, want in checks.items():
        assert read(os.path.join(dir, f)) == want, out
    assert os.path.islink(os.path.join(dir, "link.txt"))
    if os.name != "nt":
        assert os.stat(os.path.join(dir, "a.go")).st_mode & 0o777 == 0o755
    assert "new/n.txt: created (1 line written)" in out and "a.go: edited (line 1; line 3 +1 line)" in out, out
    for e in os.listdir(dir):
        assert not e.startswith("."), f"a temp file left: {e}"


def test_apply_edits_dry_run_verify(tmp_path):
    """A dry run writes nothing; verify runs after a write and says how it
    went."""
    dir = tree(tmp_path, {"a.txt": "x\n"})
    out = must_call("apply_edits", {"cwd": dir, "dryRun": True, "verify": "exit 1", "edits": [{"path": "a.txt", "old": "x", "new": "y"}]})
    assert read(os.path.join(dir, "a.txt")) == "x\n" and "dry run" in out and "verify" not in out, out
    out = must_call("apply_edits", {"cwd": dir, "verify": "echo broken-build; exit 3", "edits": [{"path": "a.txt", "old": "x", "new": "y"}]})
    assert read(os.path.join(dir, "a.txt")) == "y\n" and "FAIL verify: exit 3" in out and "broken-build" in out, out


def test_run_checks(tmp_path):
    """run_checks runs its commands side by side; a failure shows the end of
    its output, a pass shows none."""
    began = time.monotonic()
    out = must_call("run_checks", {"cwd": str(tmp_path), "commands": [
        {"cmd": "echo quiet-pass", "name": "pass"},
        {"cmd": "echo loud-failure; exit 2", "name": "fail"},
        "sleep 2",
        "sleep 2",
    ]})
    took = time.monotonic() - began
    for want in ["3 of 4 passed", "ok   pass", "FAIL fail: exit 2", "  loud-failure"]:
        assert want in out, out
    assert "quiet-pass" not in out, out
    # two 2s sleeps one after the other take 4s; side by side, 2
    assert took < 3.5, f"the checks ran one after another: {took}"


@pytest.mark.skipif(os.name == "nt", reason="the tree is ended with taskkill on Windows")
def test_run_checks_timeout_ends_the_group(tmp_path):
    """A check past its timeout is ended with everything it started, not
    only its shell: a child it left running would go on after the call."""
    out = must_call("run_checks", {"cwd": str(tmp_path), "commands": [{"cmd": "sleep 60 & echo $! > child.pid; wait", "timeout": 1}]})
    assert "timed out" in out, out
    pid = int(read(tmp_path / "child.pid").strip())
    try:
        # the child is ended with its group; it is gone once the system
        # has reaped it
        deadline = time.monotonic() + 10
        while True:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            assert time.monotonic() < deadline, f"the check's child {pid} is still running"
            time.sleep(0.02)
    finally:
        try:
            os.kill(pid, 9)
        except OSError:
            pass


def test_tail_of():
    """A failure's tail is its last lines, colours and rewritten progress
    taken out."""
    b = []
    for i in range(100):
        b.append("line " + "x" * (i % 3) + "\n")
    b.append("\x1b[31mred error\x1b[0m\n10%\r50%\r100%\n")
    got = tail_of("".join(b))
    assert got.startswith("… 62 lines before\n") and "red error\n100%" in got and "\x1b" not in got and "50%" not in got, repr(got)


def test_session_start(tmp_path, monkeypatch):
    """The SessionStart hook names the workspace agy says, never its own
    folder (the plugin's), and says nothing at all when it can't tell."""
    dir = tree(tmp_path, {"go.mod": "module example.com/w\n\ngo 1.26\n", "package.json": '{"name":"w","scripts":{"test":"x","build":"y"}}', "pnpm-lock.yaml": "", "AGENTS.md": "", "cmd/main.go": ""})
    git(dir, "init", "-q", "-b", "trunk")

    def hook(inp: str) -> dict:
        out = io.StringIO()
        session.session_start(io.BytesIO(inp.encode()), out)
        return json.loads(out.getvalue())

    def text(m: dict) -> str:
        steps = m.get("injectSteps") or []
        if len(steps) != 1:
            return ""
        return steps[0]["systemMessage"]["systemMessage"]

    uri = "file://" + dir.replace(os.sep, "/")
    if os.name == "nt":
        uri = "file:///" + dir.replace(os.sep, "/")
    for inp in [
        json.dumps({"workspacePaths": ["/not/there", dir], "conversationId": "c"}),
        json.dumps({"cwd": dir}),
        json.dumps({"workspacePaths": [{"uri": uri}]}),
    ]:
        s = text(hook(inp))
        for want in ["Workspace: " + dir + " (git: ", "trunk", "Go module example.com/w", "Node package w (pnpm), scripts: build test", "has AGENTS.md", "Top level: cmd/ AGENTS.md", "batch_search"]:
            assert want in s, f"{inp}: {s}"
    # the hook runs in the plugin's folder: that is never the workspace
    monkeypatch.chdir(dir)
    for inp in ["", "not json", '{"workspacePaths":["relative"]}', '{"cwd":""}']:
        assert hook(inp) == {}, inp


def test_snapshot_cap(tmp_path):
    """A snapshot stays under its cap however much is at the top."""
    files = {}
    for i in range(60):
        files["名" * 30 + chr(ord("a") + i % 26) + "x" * i] = ""
    s = session.snapshot(tree(tmp_path, files))
    n = len(s.encode("utf-8"))
    assert n <= session.SNAPSHOT_CAP + len("…".encode()) and (s.endswith("…") or "in all" in s), f"{n} bytes:\n{s}"
    assert "\ufffd" not in s and "Workspace: " in s, repr(s)


def test_serve(tmp_path):
    """The MCP server answers initialize, lists its tools and calls them; a
    notification gets no answer, an unknown method an error."""
    dir = tree(tmp_path, {"x.txt": "needle\n"})
    inp = "\n".join([
        '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18"}}',
        '{"jsonrpc":"2.0","method":"notifications/initialized"}',
        '{"jsonrpc":"2.0","id":2,"method":"tools/list"}',
        json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "batch_search", "arguments": {"cwd": dir, "queries": [{"kind": "text", "pattern": "needle"}]}}}),
        '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"nope"}}',
        '{"jsonrpc":"2.0","id":5,"method":"resources/list"}',
    ]) + "\n"
    out = io.StringIO()
    server.serve(io.BytesIO(inp.encode()), out)
    got = {}
    for line in out.getvalue().splitlines():
        m = json.loads(line)
        got[json.dumps(m["id"])] = m
    assert len(got) == 5, out.getvalue()
    r = got["1"]["result"]
    assert r["serverInfo"]["name"] == SERVER_NAME and r["instructions"], r
    names = []
    for tl in got["2"]["result"]["tools"]:
        assert tl.get("inputSchema") and tl.get("description"), tl
        names.append(tl["name"])
    assert names == TOOL_NAMES
    res3 = got["3"]["result"]
    assert res3.get("isError") is not True and "x.txt:1: needle" in res3["content"][0]["text"], res3
    assert got["4"]["result"]["isError"] is True, got["4"]
    assert got["5"].get("error") is not None, got["5"]


def test_cap_text():
    """What a tool gives back is cut to what agy shows inline, saying so."""
    s = "一行\n" * 5000
    got = server.cap_text(s)
    assert len(got.encode()) <= server.OUT_CAP and "more bytes left out" in got and got.startswith("一行\n"), got[-200:]


def test_root_not_the_plugin_folder(tmp_path, monkeypatch):
    """In the plugin's folder, where agy may start its server, a call with
    no cwd is turned away rather than run there."""
    d = tmp_path / "plugins" / plug.PLUGIN_NAME
    d.mkdir(parents=True)
    monkeypatch.chdir(d)
    for cwd in ["", "sub"]:
        with pytest.raises(server.ToolError, match="give cwd"):
            server.root(cwd)
    with pytest.raises(Exception):
        server.call("read_slices", {"files": [{"path": "plugin.json"}]})


def _exe(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)
    return str(path)


def _mcp(home) -> dict:
    return json.loads(read(plug.plugin_dir(home) / "mcp_config.json"))


def test_plugin(tmp_path, monkeypatch):
    """The plugin is written, found, kept and taken out; one of the same
    name that OpenProgram didn't write is never touched."""
    home = tmp_path / "h"
    home.mkdir()
    exe = _exe(home / "app" / "openprogram")
    assert not plug.is_on(home)
    plug.keep(home, [exe])
    assert not plug.is_on(home), "keep turned it on"
    plug.write(home, [exe])
    assert plug.is_on(home)
    s = _mcp(home)["mcpServers"][SERVER_NAME]
    assert s["command"] == exe and s["args"] == ["fast-tools", "mcp"], s
    assert len(s["tools"]) == len(TOOL_NAMES) and s["tools"]["batch_search"]["eager"] is True, s
    h = read(plug.plugin_dir(home) / "hooks.json")
    assert '"SessionStart"' in h and "fast-tools session-start" in h, h
    assert "batch_search" in read(plug.plugin_dir(home) / "rules" / "AGENTS.md")

    # another OpenProgram that is there is left; a gone one is replaced
    other = _exe(home / "other" / "openprogram")

    def starts() -> str:
        return _mcp(home)["mcpServers"][SERVER_NAME]["command"]

    plug.keep(home, [other])
    assert starts() == exe, "keep took it from a program still there"
    os.remove(exe)
    plug.keep(home, [other])
    assert starts() == other, "keep left it starting a gone program"
    # one an older OpenProgram wrote is written again
    mk = plug.plugin_dir(home) / plug.MARK_FILE
    mk.write_text('{"fastTools":0}')
    assert not plug.is_on(home), "a mark of 0 counted as on"
    mk.write_text('{"fastTools":-1}')
    assert not plug.is_on(home), "a negative mark counted as on"
    mk.write_text('{"fastTools":%d}' % plug.RULES_VERSION)
    rules = plug.plugin_dir(home) / "rules" / "AGENTS.md"
    rules.write_text("old rules")
    plug.keep(home, [other])
    assert read(rules) == "old rules", "keep wrote a current plugin again"
    # a newer OpenProgram, with newer rules, finds this one's
    monkeypatch.setattr(plug, "RULES_VERSION", plug.RULES_VERSION + 1)
    plug.keep(home, [other])
    assert read(rules) == plug.RULES and plug.written(home) == plug.RULES_VERSION, "keep left an older plugin"
    # a plugin.json of only the fields Antigravity knows; the mark is beside it
    pj = json.loads(read(plug.plugin_dir(home) / "plugin.json"))
    for k in pj:
        assert k in ("name", "displayName", "version", "description", "logo"), k
    assert (plug.plugin_dir(home) / pj["logo"]).exists()
    # a folder left half-written, the mark and no plugin.json: not on, and
    # written over or taken out
    os.remove(plug.plugin_dir(home) / "plugin.json")
    assert not plug.is_on(home), "on with no plugin.json"
    plug.write(home, [other])
    assert plug.is_on(home)
    plug.remove(home)
    assert not plug.is_on(home)
    assert not plug.plugin_dir(home).exists()
    plug.remove(home)  # nothing there

    # the user's own plugin of that name
    plug.plugin_dir(home).mkdir(parents=True)
    pj_path = plug.plugin_dir(home) / "plugin.json"
    own = '{"name":"fast-tools","version":"3.0.0"}'
    pj_path.write_text(own)
    with pytest.raises(plug.NotOursError):
        plug.write(home, [exe])
    with pytest.raises(plug.NotOursError):
        plug.remove(home)
    plug.keep(home, [other])
    assert read(pj_path) == own and not plug.is_on(home)
    assert not (plug.plugin_dir(home) / "mcp_config.json").exists()
    # a folder of the user's own files, with no plugin.json yet
    os.remove(pj_path)
    (plug.plugin_dir(home) / "notes.md").write_text("mine")
    with pytest.raises(plug.NotOursError):
        plug.remove(home)


def test_plugin_starts_openprogram_argv(tmp_path):
    """OpenProgram's program is an argv: it starts the server with
    fast-tools mcp after it, also when given ending in them already."""
    home = tmp_path / "h"
    for cmd in ([sys.executable, "-m", "openprogram"], [sys.executable, "-m", "openprogram", "fast-tools", "mcp"]):
        plug.write(home, cmd)
        s = _mcp(home)["mcpServers"][SERVER_NAME]
        assert s["command"] == sys.executable and s["args"] == ["-m", "openprogram", "fast-tools", "mcp"], s
        hook = json.loads(read(plug.plugin_dir(home) / "hooks.json"))[plug.PLUGIN_NAME]["SessionStart"][0]["command"]
        assert hook.endswith(" -m openprogram fast-tools session-start"), hook
    with pytest.raises(ValueError):
        plug.write(tmp_path / "h2", [])


def test_hook_command(tmp_path):
    """The hook's command line is quoted for the shell agy runs it in."""
    for exe, goos, want in [
        (["/usr/local/bin/openprogram"], "darwin", "/usr/local/bin/openprogram fast-tools session-start"),
        (["/Applications/OpenProgram.app/Contents/MacOS/openprogram"], "darwin", "/Applications/OpenProgram.app/Contents/MacOS/openprogram fast-tools session-start"),
        (["/Users/a b/openprogram"], "linux", "'/Users/a b/openprogram' fast-tools session-start"),
        (["/home/o'neil/openprogram"], "linux", "'/home/o'\\''neil/openprogram' fast-tools session-start"),
        ([r"C:\Program Files\openprogram\openprogram.exe"], "windows", r'"C:\Program Files\openprogram\openprogram.exe" fast-tools session-start'),
    ]:
        assert plug.hook_command_on(exe, goos) == want
    if os.name == "nt":
        return
    # sh runs the quoted line as written
    d = tmp_path / "o'ne il"
    d.mkdir()
    exe = d / "fake"
    exe.write_text('#!/bin/sh\necho "ran $*"\n')
    exe.chmod(0o755)
    out = subprocess.run(["/bin/sh", "-c", plug.hook_command([str(exe), "-m", "x y"])], capture_output=True, text=True)
    assert out.returncode == 0 and out.stdout.strip() == "ran -m x y fast-tools session-start", out


def test_run_usage(capsys):
    """`openprogram fast-tools` with no or an unknown subcommand says how it
    is used."""
    for a in ([], ["what"]):
        assert server.run(a) != 0
        assert "usage: openprogram fast-tools" in capsys.readouterr().err
    assert server.run(["--help"]) == 0
    assert "session-start" in capsys.readouterr().out
