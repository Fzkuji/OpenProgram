"""Tests for the LSP client and the three lsp_* tools.

The protocol layer is exercised against a real subprocess: a small
Python script that speaks Content-Length-framed JSON-RPC and answers
with canned results. That covers the framing, the reader thread and the
request/response pairing without needing pyright installed.

The tool layer is tested against a stub server object, so the 1-based to
0-based conversion, the unavailable degradation and the truncation are
checked independently of any child process.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import textwrap
import threading
import time

import pytest

from openprogram.programs.tools.code.lsp import shared
# The impl functions are the tool bodies as plain Python; the
# @function-wrapped names are AgentTool objects for LLM dispatch.
from openprogram.programs.tools.code.lsp.lsp_definition.lsp_definition import (
    _definition_impl as lsp_definition,
)
from openprogram.programs.tools.code.lsp.lsp_diagnostics.lsp_diagnostics import (
    _diagnostics_impl as lsp_diagnostics,
)
from openprogram.programs.tools.code.lsp.lsp_references.lsp_references import (
    _references_impl as lsp_references,
)
from openprogram.lsp import client as lsp_client
from openprogram.lsp.client import LanguageServer, ServerSpec, ServerUnavailable


# ---------------------------------------------------------------------------
# A fake language server: canned JSON-RPC over stdio
# ---------------------------------------------------------------------------

FAKE_SERVER = textwrap.dedent('''
    import json, sys

    def send(payload):
        body = json.dumps(payload).encode("utf-8")
        sys.stdout.buffer.write(
            f"Content-Length: {len(body)}\\r\\n\\r\\n".encode("ascii") + body
        )
        sys.stdout.buffer.flush()

    def read():
        headers = {}
        while True:
            line = sys.stdin.buffer.readline()
            if not line:
                return None
            line = line.strip()
            if not line:
                break
            name, _, value = line.partition(b":")
            headers[name.strip().lower()] = value.strip()
        length = int(headers.get(b"content-length", 0))
        return json.loads(sys.stdin.buffer.read(length).decode("utf-8")) if length else None

    while True:
        message = read()
        if message is None:
            break
        method = message.get("method")
        if message.get("id") in (0, 1) and method is not None:
            sys.exit(1)   # client reused an id the server already owns
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": message["id"],
                  "result": {"capabilities": {}}})
        elif method == "textDocument/didOpen":
            # Pyright-style: the server takes integer ids 0 and 1 for its
            # own workspace/configuration requests. A client that reuses
            # one of them is a protocol violation the real server dies on.
            for own_id in (0, 1):
                send({"jsonrpc": "2.0", "id": own_id,
                      "method": "workspace/configuration",
                      "params": {"items": [{"section": "python"}]}})
            send({"jsonrpc": "2.0",
                  "method": "textDocument/publishDiagnostics",
                  "params": {"uri": message["params"]["textDocument"]["uri"],
                             "diagnostics": [{
                                 "range": {"start": {"line": 4, "character": 7},
                                           "end": {"line": 4, "character": 12}},
                                 "severity": 1, "source": "fake",
                                 "message": "undefined name  \\"widget\\""}]}})
        elif method == "textDocument/definition":
            send({"jsonrpc": "2.0", "id": message["id"], "result": [{
                "uri": "file:///tmp/target.py",
                "range": {"start": {"line": 9, "character": 4},
                          "end": {"line": 9, "character": 10}}}]})
        elif method == "textDocument/references":
            send({"jsonrpc": "2.0", "id": message["id"],
                  "result": [{"uri": "file:///tmp/caller.py",
                              "range": {"start": {"line": i, "character": 0},
                                        "end": {"line": i, "character": 3}}}
                             for i in range(3)]})
        elif method == "boom":
            send({"jsonrpc": "2.0", "id": message["id"],
                  "error": {"code": -32601, "message": "no such method"}})
        elif method == "shutdown":
            send({"jsonrpc": "2.0", "id": message["id"], "result": None})
        elif method == "exit":
            break
''')


@pytest.fixture
def fake_server(tmp_path):
    script = tmp_path / "fake_language_server.py"
    script.write_text(FAKE_SERVER)
    process = subprocess.Popen(
        [sys.executable, str(script)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    spec = ServerSpec(language_id="python", binary="fake",
                      arguments=(), install_hint="none", extensions=(".py",))
    server = LanguageServer(spec, str(tmp_path), process)
    server.initialize()
    yield server
    server.stop()


# ---------------------------------------------------------------------------
# Protocol layer
# ---------------------------------------------------------------------------

def test_request_response_round_trip(fake_server):
    result = fake_server.request("textDocument/definition", {
        "textDocument": {"uri": "file:///tmp/a.py"},
        "position": {"line": 0, "character": 0},
    })
    assert result[0]["uri"] == "file:///tmp/target.py"
    assert result[0]["range"]["start"]["line"] == 9


def test_server_error_response_raises(fake_server):
    with pytest.raises(RuntimeError, match="no such method"):
        fake_server.request("boom", {})


def test_did_open_publishes_diagnostics(fake_server, tmp_path):
    source = tmp_path / "sample.py"
    source.write_text("a = 1\n" * 8)
    uri = fake_server.open_file(str(source))
    diagnostics = fake_server.wait_for_diagnostics(uri, timeout=5.0)
    assert len(diagnostics) == 1
    assert diagnostics[0]["severity"] == 1


def test_wait_for_diagnostics_timeout_raises(fake_server):
    with pytest.raises(TimeoutError, match="diagnostics timed out"):
        fake_server.wait_for_diagnostics("file:///never-opened.py", timeout=0.01)


def test_reopening_a_file_sends_did_change(fake_server, tmp_path):
    source = tmp_path / "sample.py"
    source.write_text("a = 1\n")
    uri = fake_server.open_file(str(source))
    assert uri in fake_server._opened
    # Second open must not re-announce the document; it refreshes it.
    fake_server.open_file(str(source))
    assert len(fake_server._opened) == 1


def test_document_versions_increase_on_every_change(fake_server, tmp_path, monkeypatch):
    source = tmp_path / "sample.py"
    source.write_text("value = 1\n")
    sent = []
    monkeypatch.setattr(fake_server, "notify", lambda method, params: sent.append((method, params)))

    fake_server.open_file(str(source))
    source.write_text("value = 2\n")
    fake_server.open_file(str(source))
    source.write_text("value = 3\n")
    fake_server.open_file(str(source))

    assert [method for method, _ in sent] == [
        "textDocument/didOpen",
        "textDocument/didChange",
        "textDocument/didChange",
    ]
    assert [params["textDocument"]["version"] for _, params in sent] == [1, 2, 3]


def test_concurrent_open_serializes_document_versions(fake_server, tmp_path, monkeypatch):
    source = tmp_path / "sample.py"
    source.write_text("value = 1\n")
    sent = []
    start = threading.Barrier(3)

    def notify(method, params):
        sent.append((method, params["textDocument"]["version"]))
        time.sleep(0.05)

    monkeypatch.setattr(fake_server, "notify", notify)

    def open_file():
        start.wait()
        fake_server.open_file(str(source))

    threads = [threading.Thread(target=open_file) for _ in range(2)]
    for thread in threads:
        thread.start()
    start.wait()
    for thread in threads:
        thread.join()

    assert sent == [
        ("textDocument/didOpen", 1),
        ("textDocument/didChange", 2),
    ]


def test_dead_server_reports_unavailable(fake_server):
    fake_server.process.kill()
    fake_server.process.wait()
    with pytest.raises((ServerUnavailable, TimeoutError)):
        fake_server.request("textDocument/definition", {}, timeout=2.0)


def test_uri_round_trip_handles_spaces(tmp_path):
    path = str(tmp_path / "a dir" / "file.py")
    assert lsp_client.uri_to_path(lsp_client.path_to_uri(path)) == path


def test_find_workspace_stops_at_project_marker(tmp_path):
    root = tmp_path / "project"
    (root / "pkg" / "sub").mkdir(parents=True)
    (root / "pyproject.toml").write_text("")
    found = lsp_client.find_workspace(str(root / "pkg" / "sub" / "mod.py"))
    assert os.path.realpath(found) == os.path.realpath(str(root))


def test_spec_lookup_by_extension():
    assert lsp_client.spec_for_file("/a/b.py").binary == "pyright-langserver"
    assert lsp_client.spec_for_file("/a/b.tsx").binary == "typescript-language-server"
    assert lsp_client.spec_for_file("/a/b.rs") is None


# ---------------------------------------------------------------------------
# Tool layer — unavailable degradation
# ---------------------------------------------------------------------------

def test_unsupported_language_degrades(tmp_path):
    source = tmp_path / "main.rs"
    source.write_text("fn main() {}\n")
    result = lsp_diagnostics(str(source))
    assert "no language server configured" in result


def test_missing_binary_names_the_install(tmp_path, monkeypatch):
    monkeypatch.setattr(lsp_client.shutil, "which", lambda _: None)
    source = tmp_path / "mod.py"
    source.write_text("x = 1\n")
    result = lsp_diagnostics(str(source))
    assert "unavailable: install pyright-langserver" in result
    assert "npm install -g pyright" in result


def test_relative_path_rejected():
    assert "absolute path" in lsp_diagnostics("mod.py")


def test_missing_file_rejected(tmp_path):
    assert "file not found" in lsp_diagnostics(str(tmp_path / "gone.py"))


# ---------------------------------------------------------------------------
# Tool layer — formatting against a stub server
# ---------------------------------------------------------------------------

class _StubServer:
    """Records the params the tool sends, replies with canned locations."""

    def __init__(self, workspace, result):
        self.workspace = workspace
        self.result = result
        self.sent: list[tuple[str, dict]] = []
        self.diagnostics: list[dict] = []

    def open_file(self, path):
        return lsp_client.path_to_uri(path)

    def request(self, method, params, timeout=None):
        self.sent.append((method, params))
        return self.result

    def wait_for_diagnostics(self, uri, timeout=None):
        return self.diagnostics


@pytest.fixture
def stub(tmp_path, monkeypatch):
    source = tmp_path / "mod.py"
    source.write_text("import os\n\n\ndef widget():\n    return os\n")
    holder = {}

    def install(result):
        server = _StubServer(str(tmp_path), result)
        holder["server"] = server
        monkeypatch.setattr(shared, "get_server", lambda _p: server)
        return server, str(source)

    holder["install"] = install
    return holder


def test_positions_convert_to_zero_based(stub):
    server, path = stub["install"]([])
    lsp_references(path, line=4, column=5)
    _method, params = server.sent[0]
    assert params["position"] == {"line": 3, "character": 4}


def test_positions_convert_unicode_columns_to_utf16(stub):
    server, path = stub["install"]([])
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("😀widget()\n")
    lsp_references(path, line=1, column=2)
    _method, params = server.sent[0]
    assert params["position"] == {"line": 0, "character": 2}


def test_position_floor_is_one(stub):
    server, path = stub["install"]([])
    lsp_definition(path, line=0, column=0)
    _method, params = server.sent[0]
    assert params["position"] == {"line": 0, "character": 0}


def test_references_render_relative_one_based(stub, tmp_path):
    target = tmp_path / "caller.py"
    target.write_text("widget()\n")
    server, path = stub["install"]([{
        "uri": lsp_client.path_to_uri(str(target)),
        "range": {"start": {"line": 0, "character": 0},
                  "end": {"line": 0, "character": 6}},
    }])
    result = lsp_references(path, line=4, column=5)
    assert "1 reference" in result
    assert "caller.py:1:1" in result
    assert "widget()" in result


def test_references_empty_result_is_explicit(stub):
    _server, path = stub["install"]([])
    assert "No references found" in lsp_references(path, line=4, column=5)


def test_references_truncate_beyond_the_cap(stub, tmp_path):
    target = tmp_path / "caller.py"
    target.write_text("x\n" * 200)
    locations = [{
        "uri": lsp_client.path_to_uri(str(target)),
        "range": {"start": {"line": i, "character": 0},
                  "end": {"line": i, "character": 1}},
    } for i in range(shared.MAX_LOCATIONS + 20)]
    _server, path = stub["install"](locations)
    result = lsp_references(path, line=4, column=5)
    assert "20 more references not shown" in result
    assert result.count("caller.py:") == shared.MAX_LOCATIONS


def test_definition_accepts_a_bare_location(stub, tmp_path):
    target = tmp_path / "target.py"
    target.write_text("def widget():\n    pass\n")
    _server, path = stub["install"]({
        "uri": lsp_client.path_to_uri(str(target)),
        "range": {"start": {"line": 0, "character": 4},
                  "end": {"line": 0, "character": 10}},
    })
    result = lsp_definition(path, line=5, column=12)
    assert "target.py:1:5" in result
    assert "def widget():" in result


def test_locations_convert_utf16_columns_for_display(stub, tmp_path):
    target = tmp_path / "target.py"
    target.write_text("😀widget()\n", encoding="utf-8")
    _server, path = stub["install"]({
        "uri": lsp_client.path_to_uri(str(target)),
        "range": {"start": {"line": 0, "character": 2},
                  "end": {"line": 0, "character": 8}},
    })
    assert "target.py:1:2" in lsp_definition(path, line=1, column=1)


def test_definition_accepts_a_location_link(stub, tmp_path):
    target = tmp_path / "target.py"
    target.write_text("def widget():\n    pass\n")
    _server, path = stub["install"]([{
        "targetUri": lsp_client.path_to_uri(str(target)),
        "targetSelectionRange": {"start": {"line": 0, "character": 4},
                                 "end": {"line": 0, "character": 10}},
    }])
    assert "target.py:1:5" in lsp_definition(path, line=5, column=12)


def test_definition_empty_result_is_explicit(stub):
    _server, path = stub["install"](None)
    assert "No definition found" in lsp_definition(path, line=4, column=5)


def test_diagnostics_render_one_based_with_severity(stub):
    server, path = stub["install"](None)
    server.diagnostics = [
        {"range": {"start": {"line": 4, "character": 7}},
         "severity": 1, "source": "pyright", "message": "undefined  name"},
        {"range": {"start": {"line": 0, "character": 0}},
         "severity": 2, "message": "unused import"},
    ]
    result = lsp_diagnostics(path)
    lines = result.splitlines()
    assert "2 diagnostics" in lines[0]
    # Sorted by position, so the line-1 warning comes first.
    assert lines[1] == "1:1 warning: unused import"
    assert lines[2] == "5:8 error: undefined name [pyright]"


def test_diagnostics_convert_utf16_columns_for_display(stub):
    server, path = stub["install"](None)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("😀widget()\n")
    server.diagnostics = [{
        "range": {"start": {"line": 0, "character": 2}},
        "severity": 1,
        "message": "problem",
    }]
    assert "1:2 error: problem" in lsp_diagnostics(path)


def test_diagnostics_clean_file_says_so(stub):
    _server, path = stub["install"](None)
    assert lsp_diagnostics(path) == "No diagnostics for mod.py"


def test_diagnostics_timeout_is_not_reported_as_clean(stub):
    server, path = stub["install"](None)
    server.wait_for_diagnostics = lambda _uri: (_ for _ in ()).throw(
        TimeoutError("diagnostics timed out after 20s")
    )
    result = lsp_diagnostics(path)
    assert result == "Error: TimeoutError: diagnostics timed out after 20s"


def test_diagnostics_truncate_beyond_the_cap(stub):
    server, path = stub["install"](None)
    server.diagnostics = [
        {"range": {"start": {"line": i, "character": 0}},
         "severity": 1, "message": f"problem {i}"}
        for i in range(shared.MAX_DIAGNOSTICS + 5)
    ]
    result = lsp_diagnostics(path)
    assert "5 more diagnostics not shown" in result


# ---------------------------------------------------------------------------
# Registration and caching
# ---------------------------------------------------------------------------

def test_tools_are_registered():
    from openprogram.programs._runtime import get
    for name in ("lsp_diagnostics", "lsp_references", "lsp_definition"):
        tool = get(name)
        assert tool is not None, f"{name} not registered"
        assert "file" in tool.parameters["properties"]


def test_server_cache_reuses_one_process_per_workspace(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text("")
    first = tmp_path / "a.py"
    second = tmp_path / "b.py"
    first.write_text("x = 1\n")
    second.write_text("y = 2\n")

    started: list[str] = []

    def fake_start(spec, workspace):
        started.append(workspace)
        stub = _StubServer(workspace, [])
        stub.process = type("P", (), {"poll": staticmethod(lambda: None)})()
        return stub

    monkeypatch.setattr(lsp_client, "_start", fake_start)
    monkeypatch.setattr(lsp_client.shutil, "which", lambda _: "/usr/bin/fake")
    monkeypatch.setattr(lsp_client, "_servers", {})

    lsp_client.get_server(str(first))
    lsp_client.get_server(str(second))
    assert started == [str(tmp_path)]


# ---------------------------------------------------------------------------
# Real pyright, when it happens to be installed
# ---------------------------------------------------------------------------

@pytest.mark.skipif(shutil.which("pyright-langserver") is None,
                    reason="pyright-langserver not installed")
def test_real_pyright_round_trip(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='t'\nversion='0'\n")
    source = tmp_path / "mod.py"
    source.write_text("def widget() -> int:\n    return undefined_name\n")
    try:
        result = lsp_diagnostics(str(source))
        assert "2:12" in result
        assert "undefined" in result.lower()
    finally:
        lsp_client.shutdown_all()


def test_client_request_ids_never_collide_with_the_servers(fake_server, tmp_path):
    """The fake server claims integer ids 0 and 1 on didOpen and exits if a
    client request reuses one — the way pyright does. Opening a file and then
    making a request must survive that."""
    source = tmp_path / "sample.py"
    source.write_text("a = 1\n")
    uri = fake_server.open_file(str(source))
    assert fake_server.wait_for_diagnostics(uri, timeout=5.0)
    result = fake_server.request("textDocument/references", {
        "textDocument": {"uri": uri},
        "position": {"line": 0, "character": 0},
    })
    assert len(result) == 3
