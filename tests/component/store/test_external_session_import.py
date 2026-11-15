"""Claude Code / Codex transcripts import as native sessions, idempotently."""
import json

import pytest

from openprogram.state.store.session.nodes import Graph
from openprogram.runtime.context.render import render_dag_messages
from openprogram.state.store import SessionStore
from openprogram.state.store.session.external_import import claude_code, codex, service
from openprogram.state.store.session.external_import.sanitize import clean_user_text


def _jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")


def _cc(uuid, parent, kind, content, ts, **extra):
    record = {"type": kind, "uuid": uuid, "parentUuid": parent, "sessionId": "cc-1",
              "cwd": "/work/repo", "timestamp": f"2026-10-01T10:00:{ts:02d}Z",
              "message": {"role": kind, "content": content}}
    if kind == "assistant":
        record["message"]["model"] = "claude-test"
    record.update(extra)
    return record


def _claude_records():
    return [
        {"type": "custom-title", "customTitle": "Fix the parser", "sessionId": "cc-1"},
        _cc("u1", None, "user", "<system-reminder>ignore me</system-reminder>Fix the parser", 1),
        _cc("a1", "u1", "assistant", [{"type": "thinking", "thinking": "look first"}], 2),
        _cc("a2", "a1", "assistant", [{"type": "tool_use", "id": "toolu_1", "name": "Bash",
                                        "input": {"command": "ls"}}], 3),
        _cc("r1", "a2", "user", [{"type": "tool_result", "tool_use_id": "toolu_1",
                                   "content": "parser.py", "is_error": False}], 4),
        _cc("a3", "r1", "assistant", [{"type": "text", "text": "Fixed."}], 5),
        # Rewound branch: the user went back to u1 and asked again.
        _cc("u2-old", "a3", "user", "abandoned question", 6),
        _cc("a4-old", "u2-old", "assistant", [{"type": "text", "text": "abandoned"}], 7),
        _cc("meta", "a3", "user", "Caveat: local command", 8, isMeta=True),
        _cc("cmd", "meta", "user", "<command-name>/clear</command-name><command-args></command-args>", 9),
        _cc("u2", "cmd", "user", "Now add tests", 10),
        _cc("side", "u2", "assistant", [{"type": "text", "text": "subagent"}], 11, isSidechain=True),
        _cc("a5", "u2", "assistant", [{"type": "text", "text": "Tests added."}], 12),
    ]


def _codex_records(extra_turn=False):
    records = [
        {"type": "session_meta", "timestamp": "2026-10-02T09:00:00Z",
         "payload": {"id": "cx-1", "cwd": "/work/other", "source": "vscode"}},
        {"type": "turn_context", "timestamp": "2026-10-02T09:00:00Z", "payload": {"model": "gpt-test"}},
        {"type": "response_item", "timestamp": "2026-10-02T09:00:01Z",
         "payload": {"type": "message", "role": "user", "content": [
             {"type": "input_text", "text": "# AGENTS.md instructions\n\nbe nice"},
             {"type": "input_text", "text": "<environment_context>\n<cwd>/x</cwd>\n</environment_context>"}]}},
        {"type": "response_item", "timestamp": "2026-10-02T09:00:02Z",
         "payload": {"type": "message", "role": "user", "content": [
             {"type": "input_text", "text": "<in-app-browser-context source=\"x\">page</in-app-browser-context>"},
             {"type": "input_text", "text": "Summarise the repo"}]}},
        {"type": "response_item", "timestamp": "2026-10-02T09:00:03Z",
         "payload": {"type": "reasoning", "summary": [{"type": "summary_text", "text": "scan files"}]}},
        {"type": "response_item", "timestamp": "2026-10-02T09:00:04Z",
         "payload": {"type": "function_call", "name": "exec_command", "call_id": "call_1",
                     "arguments": "{\"cmd\": \"ls\"}"}},
        {"type": "response_item", "timestamp": "2026-10-02T09:00:05Z",
         "payload": {"type": "function_call_output", "call_id": "call_1",
                     "output": [{"type": "input_text", "text": "README.md"}]}},
        {"type": "response_item", "timestamp": "2026-10-02T09:00:06Z",
         "payload": {"type": "message", "role": "assistant",
                     "content": [{"type": "output_text", "text": "It has a README."}]}},
    ]
    if extra_turn:
        records += [
            {"type": "response_item", "timestamp": "2026-10-02T09:01:00Z",
             "payload": {"type": "message", "role": "user",
                         "content": [{"type": "input_text", "text": "And the tests?"}]}},
            {"type": "response_item", "timestamp": "2026-10-02T09:01:01Z",
             "payload": {"type": "message", "role": "assistant",
                         "content": [{"type": "output_text", "text": "None yet."}]}},
        ]
    return records


@pytest.fixture
def homes(tmp_path):
    cc_home, cx_home = tmp_path / "claude", tmp_path / "codex"
    _jsonl(cc_home / "projects" / "-work-repo" / "cc-1.jsonl", _claude_records())
    _jsonl(cc_home / "projects" / "-work-repo" / "cc-1" / "subagents" / "agent-a.jsonl",
           [_cc("s1", None, "user", "sub task", 1, isSidechain=True)])
    _jsonl(cx_home / "sessions" / "2026" / "10" / "02" / "rollout-cx-1.jsonl", _codex_records())
    _jsonl(cx_home / "sessions" / "2026" / "10" / "02" / "rollout-sub.jsonl", [
        {"type": "session_meta", "payload": {"id": "sub", "cwd": "/w", "source": {"subagent": "x"}}},
    ])
    (cx_home / "session_index.jsonl").write_text(
        json.dumps({"id": "cx-1", "thread_name": "Old name"}) + "\n"
        + json.dumps({"id": "cx-1", "thread_name": "Repo summary"}) + "\n", encoding="utf-8")
    return {"claude-code": cc_home, "codex": cx_home}


@pytest.fixture
def store(tmp_path):
    s = SessionStore(tmp_path / "sessions")
    yield s
    s.close()


def _import(homes, store, tmp_path, **kw):
    return service.import_transcripts(homes=homes, store=store, ledger_root=tmp_path / "ledger",
                                      agent_id="main", **kw)


def test_claude_code_follows_the_active_chain(homes):
    session = claude_code.parse(homes["claude-code"] / "projects" / "-work-repo" / "cc-1.jsonl")
    assert session.title == "Fix the parser"
    assert session.cwd == "/work/repo"
    assert [t.user_text for t in session.turns] == ["Fix the parser", "Now add tests"]
    first = session.turns[0]
    assert [b["type"] if isinstance(b, dict) else "tool" for b in first.blocks] == ["thinking", "tool", "text"]
    tool = first.tools()[0]
    assert (tool.name, tool.arguments, tool.result) == ("Bash", {"command": "ls"}, "parser.py")
    assert session.turns[1].reply_text() == "Tests added."


def test_codex_drops_injected_context_and_pairs_tools(homes):
    titles = codex.thread_titles(homes["codex"])
    path = homes["codex"] / "sessions" / "2026" / "10" / "02" / "rollout-cx-1.jsonl"
    session = codex.parse(path, titles)
    assert session.title == "Repo summary"
    assert [t.user_text for t in session.turns] == ["Summarise the repo"]
    turn = session.turns[0]
    assert turn.model == "gpt-test"
    assert turn.tools()[0].arguments == {"cmd": "ls"}
    assert turn.tools()[0].result == "README.md"
    assert turn.reply_text() == "It has a README."


def test_discovery_skips_subagent_transcripts(homes, store, tmp_path):
    rows = service.list_candidates(homes=homes, store=store, ledger_root=tmp_path / "ledger")
    assert sorted(r["source_id"] for r in rows) == ["cc-1", "cx-1"]
    assert {r["state"] for r in rows} == {"new"}


def test_import_writes_native_dag_and_is_idempotent(homes, store, tmp_path):
    results = _import(homes, store, tmp_path)
    assert sorted(r["status"] for r in results) == ["created", "created"]
    sid = next(r["session_id"] for r in results if r["agent"] == "claude-code")
    meta = store.get_session(sid)
    assert meta["title"] == "Fix the parser"
    assert meta["source"] == "import"
    assert meta["imported_from"]["agent"] == "claude-code"
    assert meta["head_id"].endswith("_0001_reply")
    msgs = store.get_messages(sid)
    roles = [m["role"] for m in msgs if m["id"] != "ROOT"]
    assert roles == ["user", "assistant", "tool", "user", "assistant"]
    assert [m["content"] for m in store.get_branch(sid, meta["head_id"])][-1] == "Tests added."

    again = _import(homes, store, tmp_path)
    assert {r["status"] for r in again} == {"unchanged"}
    assert len(store.list_sessions(limit=50)) == 2
    states = service.list_candidates(homes=homes, store=store, ledger_root=tmp_path / "ledger")
    assert {r["state"] for r in states} == {"imported"}


def test_grown_source_appends_to_the_same_session(homes, store, tmp_path):
    path = homes["codex"] / "sessions" / "2026" / "10" / "02" / "rollout-cx-1.jsonl"
    first = _import(homes, store, tmp_path, agents=["codex"])[0]
    _jsonl(path, _codex_records(extra_turn=True))
    second = _import(homes, store, tmp_path, agents=["codex"])[0]
    assert second["status"] == "appended"
    assert second["session_id"] == first["session_id"]
    assert second["turns_added"] == 1
    head = store.get_session(first["session_id"])["head_id"]
    assert [m["content"] for m in store.get_branch(first["session_id"], head)][-1] == "None yet."


def test_session_continued_here_is_not_appended_to(homes, store, tmp_path):
    path = homes["codex"] / "sessions" / "2026" / "10" / "02" / "rollout-cx-1.jsonl"
    first = _import(homes, store, tmp_path, agents=["codex"])[0]
    sid = first["session_id"]
    head = store.get_session(sid)["head_id"]
    store.append_message(sid, {"id": "local-turn", "role": "user", "content": "follow-up here",
                               "predecessor": head})
    store.set_head(sid, "local-turn")
    _jsonl(path, _codex_records(extra_turn=True))
    second = _import(homes, store, tmp_path, agents=["codex"])[0]
    assert second["status"] == "created"
    assert second["session_id"] != sid
    assert store.get_session(sid)["head_id"] == "local-turn"


def test_imported_tool_calls_render_as_text_for_the_model(homes, store, tmp_path):
    sid = next(r["session_id"] for r in _import(homes, store, tmp_path) if r["agent"] == "claude-code")
    graph = Graph()
    pair = store._open(sid)
    for node in pair[1].nodes_by_seq:
        graph.nodes[node.id] = node
    ids = [n.id for n in pair[1].nodes_by_seq if n.id != "ROOT"]
    messages = render_dag_messages(graph, ids)
    kinds = [type(block).__name__ for m in messages for block in m.content]
    assert "ToolCall" not in kinds
    texts = [getattr(b, "text", "") for m in messages for b in m.content]
    assert any(t.startswith("[claude-code tool call] Bash(") and "parser.py" in t for t in texts)


@pytest.mark.parametrize("raw,expected", [
    ("hello<system-reminder>x</system-reminder>", "hello"),
    ("<command-name>/review</command-name><command-args>auth</command-args>", "/review auth"),
    ("<local-command-stdout>done</local-command-stdout>", ""),
    ("# AGENTS.md instructions\n\nrules", ""),
])
def test_clean_user_text(raw, expected):
    assert clean_user_text(raw) == expected


def test_unanswered_transcript_is_skipped_until_it_changes(homes, store, tmp_path):
    path = homes["claude-code"] / "projects" / "-work-repo" / "probe.jsonl"
    _jsonl(path, [_cc("p1", None, "user", "Reply with OK", 1, sessionId="probe")])
    first = {r["source_id"]: r["status"] for r in _import(homes, store, tmp_path, agents=["claude-code"])}
    assert first["probe"] == "empty"
    rows = service.list_candidates(["claude-code"], homes=homes, store=store,
                                   ledger_root=tmp_path / "ledger")
    assert {r["source_id"]: r["state"] for r in rows}["probe"] == "empty"
    _jsonl(path, [_cc("p1", None, "user", "Reply with OK", 1, sessionId="probe"),
                  _cc("p2", "p1", "assistant", [{"type": "text", "text": "OK"}], 2, sessionId="probe")])
    again = {r["source_id"]: r["status"] for r in _import(homes, store, tmp_path, agents=["claude-code"])}
    assert again["probe"] == "created"


def test_forked_claude_session_is_its_own_source(homes, store, tmp_path):
    # A fork's file starts with its parent's records, parent sessionId included.
    fork = homes["claude-code"] / "projects" / "-work-repo" / "cc-fork.jsonl"
    _jsonl(fork, _claude_records() + [
        _cc("u3", "a5", "user", "Fork-only question", 13),
        _cc("a6", "u3", "assistant", [{"type": "text", "text": "Fork answer."}], 14),
    ])
    first = _import(homes, store, tmp_path, agents=["claude-code"])
    assert sorted((r["source_id"], r["status"]) for r in first) == [
        ("cc-1", "created"), ("cc-fork", "created")]
    assert len({r["session_id"] for r in first}) == 2
    again = _import(homes, store, tmp_path, agents=["claude-code"])
    assert {r["status"] for r in again} == {"unchanged"}


def test_bookkeeping_records_do_not_mark_a_source_updated(homes, store, tmp_path):
    path = homes["claude-code"] / "projects" / "-work-repo" / "cc-1.jsonl"
    _import(homes, store, tmp_path, agents=["claude-code"])
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "last-prompt", "sessionId": "cc-1"}) + "\n")
    rows = service.list_candidates(["claude-code"], homes=homes, store=store,
                                   ledger_root=tmp_path / "ledger")
    assert [r["state"] for r in rows] == ["imported"]


def test_turn_running_at_import_is_completed_later(homes, store, tmp_path):
    path = homes["claude-code"] / "projects" / "-work-repo" / "cc-1.jsonl"
    records = _claude_records()
    _jsonl(path, records[:-1])          # the second turn has no reply yet
    first = _import(homes, store, tmp_path, agents=["claude-code"])[0]
    _jsonl(path, records)
    second = _import(homes, store, tmp_path, agents=["claude-code"])[0]
    assert second["status"] == "appended"
    assert second["session_id"] == first["session_id"]
    sid = first["session_id"]
    head = store.get_session(sid)["head_id"]
    branch = store.get_branch(sid, head)
    assert [m["content"] for m in branch][-1] == "Tests added."
    assert len([m for m in store.get_messages(sid) if m["role"] == "user" and m["id"] != "ROOT"]) == 2


def test_summary_scan_resumes_where_it_stopped(tmp_path):
    path = tmp_path / "s.jsonl"
    records = [r for r in _claude_records() if r["type"] != "custom-title"]
    _jsonl(path, records)
    scan = {}
    assert claude_code.summarize(path, scan).title == "Fix the parser"
    stopped = scan["offset"]
    assert stopped == path.stat().st_size

    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "ai-title", "aiTitle": "Parser work"}) + "\n")
        fh.write('{"type": "custom-title", "customTitle": "half')  # still being written
    assert claude_code.summarize(path, scan).title == "Parser work"
    assert scan["offset"] < path.stat().st_size

    _jsonl(path, records[:2])  # replaced by a shorter file
    assert claude_code.summarize(path, scan).title == "Fix the parser"
    assert scan["offset"] == path.stat().st_size


def _claude_in(homes, folder, source_id="cc-2"):
    records = [_cc(f"{source_id}-u", None, "user", "Draft the slides", 1, cwd=str(folder), sessionId=source_id),
               _cc(f"{source_id}-a", f"{source_id}-u", "assistant", [{"type": "text", "text": "Drafted."}], 2,
                   cwd=str(folder), sessionId=source_id)]
    path = homes["claude-code"] / "projects" / "-drs" / f"{source_id}.jsonl"
    _jsonl(path, records)
    return path


def test_a_conversation_joins_its_folder_project_which_is_added_when_missing(homes, store, tmp_path):
    from pathlib import Path
    from openprogram.state.store.project import project_store

    folder = tmp_path / "Joint Modeling in DRS"
    folder.mkdir()
    created = _import(homes, store, tmp_path, paths=[_claude_in(homes, folder)])[0]
    project = project_store.project_for_session(created["session_id"])
    assert project is not None and not project.is_default and Path(project.path) == folder.resolve()
    # The same folder is not added twice.
    other = _import(homes, store, tmp_path, paths=[_claude_in(homes, folder, "cc-3")])[0]
    assert project_store.project_for_session(other["session_id"]).id == project.id
    # The home folder, and a folder that is gone, stay in the default project.
    for cwd, source_id in ((Path.home(), "cc-4"), (tmp_path / "gone", "cc-5")):
        row = _import(homes, store, tmp_path, paths=[_claude_in(homes, cwd, source_id)])[0]
        placed = project_store.project_for_session(row["session_id"])
        assert placed is None or placed.is_default


def test_an_earlier_import_in_the_default_project_moves_when_imported_again(homes, store, tmp_path, monkeypatch):
    from openprogram.state.store.project import project_store

    folder = tmp_path / "drs"
    folder.mkdir()
    path = _claude_in(homes, folder)
    monkeypatch.setattr(service, "_project_for", lambda cwd: None)
    first = _import(homes, store, tmp_path, paths=[path])[0]
    monkeypatch.undo()
    again = _import(homes, store, tmp_path, paths=[path])[0]
    assert again["status"] == "unchanged" and again["moved"] is True
    project = project_store.project_for_session(first["session_id"])
    assert project is not None and project.path == str(folder.resolve())
    assert store.get_session(first["session_id"])["project_id"] == project.id
    # A conversation already in a project stays there.
    assert _import(homes, store, tmp_path, paths=[path])[0]["moved"] is False


def test_malformed_lines_are_skipped_not_fatal(homes, store, tmp_path):
    cc = homes["claude-code"] / "projects" / "-work-repo" / "cc-1.jsonl"
    cx = homes["codex"] / "sessions" / "2026" / "10" / "02" / "rollout-cx-1.jsonl"
    for path in (cc, cx):
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        lines.insert(2, "{not json\n")
        lines.insert(4, "[1, 2]\n")
        lines.append('{"type": "user", "message": ')  # a record still being written
        path.write_bytes("".join(lines).encode("utf-8") + b"\xff\n")
    assert [t.user_text for t in claude_code.parse(cc).turns] == ["Fix the parser", "Now add tests"]
    assert [t.user_text for t in codex.parse(cx).turns] == ["Summarise the repo"]
    assert sorted(r["status"] for r in _import(homes, store, tmp_path)) == ["created", "created"]


def test_credentials_in_a_transcript_are_not_copied(homes, store, tmp_path):
    key = "sk-ant-" + "a1B2" * 10
    gh = "ghp_" + "Z9" * 20
    (homes["claude-code"] / ".credentials.json").write_text(json.dumps({"token": key}))
    (homes["codex"] / "auth.json").write_text(json.dumps({"OPENAI_API_KEY": key}))
    path = homes["claude-code"] / "projects" / "-work-repo" / "cc-9.jsonl"
    _jsonl(path, [
        _cc("u1", None, "user", f"use {key} for the key point of the token count", 1),
        _cc("a1", "u1", "assistant", [{"type": "tool_use", "id": "t1", "name": "Bash",
                                        "input": {"command": "cat .env", "api_key": "plain-value"}}], 2),
        _cc("r1", "a1", "user", [{"type": "tool_result", "tool_use_id": "t1",
                                   "content": f"GITHUB_TOKEN={gh}\nPASSWORD=hunter2hunter2"}], 3),
        _cc("a2", "r1", "assistant", [{"type": "text", "text": f"Found {gh}."}], 4),
    ])
    row = _import(homes, store, tmp_path, paths=[path])[0]
    dump = json.dumps(store.get_messages(row["session_id"])) + json.dumps(store.get_session(row["session_id"]))
    # The listing's scan cache keeps a first message too.
    dump += "".join(f.read_text() for f in (tmp_path / "ledger").glob("*.json"))
    for secret in (key, gh, "plain-value", "hunter2hunter2"):
        assert secret not in dump
    assert "the key point of the token count" in dump  # prose about keys is untouched
    assert _import(homes, store, tmp_path, paths=[path])[0]["status"] == "unchanged"


def test_cli_dry_run_lists_pending_and_writes_nothing(homes, store, tmp_path, monkeypatch, capsys):
    from openprogram.interfaces.cli.commands.sessions import _cmd_sessions_import
    from openprogram.state.store.session import external_import as ext

    _import(homes, store, tmp_path, agents=["codex"])
    monkeypatch.setattr(ext, "list_candidates", lambda agents, since=None: service.list_candidates(
        agents, since=since, homes=homes, store=store, ledger_root=tmp_path / "ledger"))
    monkeypatch.setattr(ext, "plan_imports", lambda agents, paths=None: service.plan_imports(
        agents, paths=paths, homes=homes, store=store, ledger_root=tmp_path / "ledger"))

    def refuse(*a, **kw):
        raise AssertionError("a dry run must not import")
    monkeypatch.setattr(ext, "import_transcripts", refuse)
    _cmd_sessions_import([], "all", None, False, True)
    out = capsys.readouterr().out
    assert "Would import 1 conversation(s)" in out
    assert "create  Claude Code" in out and "Fix the parser" in out
    assert "Repo summary" not in out  # already imported
    assert len(store.list_sessions(limit=50)) == 1


def test_a_turn_written_with_a_secret_is_rewritten_when_its_source_grows(homes, store, tmp_path, monkeypatch):
    key = "sk-proj-" + "q7" * 15
    path = homes["claude-code"] / "projects" / "-work-repo" / "cc-8.jsonl"
    _jsonl(path, [
        _cc("u1", None, "user", f"try {key}", 1),
        _cc("a1", "u1", "assistant", [{"type": "text", "text": f"Using {key}."}], 2),
    ])
    monkeypatch.setattr(service, "remove_secrets", lambda session: session)
    first = _import(homes, store, tmp_path, paths=[path])[0]
    monkeypatch.undo()
    # Unchanged, it is left alone; once the source grows, the turn it
    # last imported is rewritten, in the same session.
    assert _import(homes, store, tmp_path, paths=[path])[0]["status"] == "unchanged"
    _jsonl(path, [
        _cc("u1", None, "user", f"try {key}", 1),
        _cc("a1", "u1", "assistant", [{"type": "text", "text": f"Using {key}."}], 2),
        _cc("u2", "a1", "user", "thanks", 3),
        _cc("a2", "u2", "assistant", [{"type": "text", "text": "Done."}], 4),
    ])
    again = _import(homes, store, tmp_path, paths=[path])[0]
    assert again["session_id"] == first["session_id"] and again["status"] == "appended"
    assert key not in json.dumps(store.get_messages(first["session_id"]))
    assert len(store.list_sessions(limit=50)) == 1


def test_dry_run_reports_a_rewound_source_as_a_new_conversation(homes, store, tmp_path):
    path = homes["codex"] / "sessions" / "2026" / "10" / "02" / "rollout-cx-1.jsonl"
    first = _import(homes, store, tmp_path, agents=["codex"])[0]
    records = _codex_records()
    records[3]["payload"]["content"][1]["text"] = "Summarise it differently"  # rewound
    records.append({"type": "response_item", "timestamp": "2026-10-02T09:05:00Z",
                    "payload": {"type": "message", "role": "assistant",
                                "content": [{"type": "output_text", "text": "Again."}]}})
    _jsonl(path, records)
    ledger = (tmp_path / "ledger" / "external_sessions.json").read_text()
    plan = service.plan_imports(["codex"], homes=homes, store=store, ledger_root=tmp_path / "ledger")
    assert [p["status"] for p in plan] == ["created"]
    assert (tmp_path / "ledger" / "external_sessions.json").read_text() == ledger
    assert len(store.list_sessions(limit=50)) == 1
    real = _import(homes, store, tmp_path, agents=["codex"])[0]
    assert real["status"] == "created" and real["session_id"] != first["session_id"]


def test_a_key_cut_by_the_title_limit_does_not_survive_in_the_title(homes, store, tmp_path):
    key = "sk-ant-" + "k3" * 30
    path = homes["claude-code"] / "projects" / "-work-repo" / "cc-7.jsonl"
    _jsonl(path, [
        _cc("u1", None, "user", "x" * 60 + " " + key, 1),
        _cc("a1", "u1", "assistant", [{"type": "text", "text": "ok"}], 2),
    ])
    row = _import(homes, store, tmp_path, paths=[path])[0]
    title = store.get_session(row["session_id"])["title"]
    assert "sk-ant-k3" not in title and "k3k3" not in title
