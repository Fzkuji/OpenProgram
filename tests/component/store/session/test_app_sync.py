"""The Claude app's and DeepSeek Harness's conversations come into OpenProgram and follow the apps."""
from __future__ import annotations

import json
import os
import time

import pytest
import zstandard

from openprogram.state.store import SessionStore
from openprogram.state.store.session.external_import import app_sync, deepseek_harness


def _cc(uuid, parent, kind, content, ts):
    record = {"type": kind, "uuid": uuid, "parentUuid": parent, "sessionId": "cli-1", "cwd": "/work/repo",
              "timestamp": f"2026-10-01T10:00:{ts:02d}Z", "entrypoint": "claude-desktop",
              "message": {"role": kind, "content": content}}
    if kind == "assistant":
        record["message"]["model"] = "claude-test"
    return record


def _claude_turns(count):
    records, parent = [], None
    for index in range(count):
        user, reply = f"u{index}", f"a{index}"
        records += [_cc(user, parent, "user", f"question {index}", index * 2),
                    _cc(reply, user, "assistant", [{"type": "text", "text": f"answer {index}"}], index * 2 + 1)]
        parent = reply
    return records


class Apps:
    def __init__(self, root):
        self.root = root
        self.claude_app = root / "Claude"
        self.claude_home = root / "claude"
        self.dsh = root / "dsh"

    def claude(self, cli_id, turns, *, title="Fix the parser", archived=False, activity=1000):
        transcript = self.claude_home / "projects" / "-work-repo" / f"{cli_id}.jsonl"
        transcript.parent.mkdir(parents=True, exist_ok=True)
        transcript.write_text("".join(json.dumps(r) + "\n" for r in _claude_turns(turns)), encoding="utf-8")
        meta = self.claude_app / "claude-code-sessions" / "acct" / "org" / f"local_{cli_id}.json"
        meta.parent.mkdir(parents=True, exist_ok=True)
        meta.write_text(json.dumps({"cliSessionId": cli_id, "cwd": "/work/repo", "title": title,
                                    "model": "claude-test", "isArchived": archived,
                                    "lastActivityAt": activity * 1000}), encoding="utf-8")

    def deepseek(self, session_id, events, *, archived=False):
        log = self.dsh / "sessions" / "--work-ds--" / session_id / deepseek_harness.LOG_NAME
        log.parent.mkdir(parents=True, exist_ok=True)
        lines = [{"type": "session", "version": 4, "id": session_id, "createdAt": 1, "cwd": "/work/ds"}]
        lines += [{"seq": i, "time": 1_793_954_000_000 + i * 1000, **e} for i, e in enumerate(events)]
        log.write_bytes(zstandard.ZstdCompressor().compress("".join(json.dumps(l) + "\n" for l in lines).encode()))
        workspace = {"global": {"archivedSessionIds": [session_id] if archived else []},
                     "tables": {"workspaces": {"w": {"path": "/work/ds", "sessionIds": [session_id]}}}}
        (self.dsh / "storages").mkdir(parents=True, exist_ok=True)
        (self.dsh / "storages" / "workspace.json").write_text(json.dumps(workspace), encoding="utf-8")

    def items(self):
        return app_sync.items(claude_app=self.claude_app, claude_home=self.claude_home, dsh_home=self.dsh)


@pytest.fixture
def apps(tmp_path):
    return Apps(tmp_path)


@pytest.fixture
def store(tmp_path):
    s = SessionStore(tmp_path / "sessions")
    yield s
    s.close()


def _sync(apps, store, tmp_path):
    return app_sync.sync(store=store, ledger_root=tmp_path / "ledger", agent_id="main", found=apps.items())


def _texts(store, session_id):
    head = store.get_session(session_id)["head_id"]
    return [m["content"] for m in store.get_branch(session_id, head) if m["role"] in ("user", "assistant")]


DEEPSEEK_TURN = [
    {"type": "model/selection", "data": {"provider": "deepseek-official", "model": "deepseek-flash"}},
    {"type": "turn/start", "data": {"turn": 0}},
    {"type": "user/message", "data": {"role": "user", "source": {"kind": "plugin", "plugin": "agents-md"},
                                      "content": [{"type": "text", "text": "AGENTS.md says hi"}]}},
    {"type": "user/message", "data": {"role": "user", "source": {"kind": "user"},
                                      "content": [{"type": "text", "text": "List the files"}]}},
    {"type": "assistant/message", "data": {"turn": 0, "step": 0, "message": {
        "role": "assistant", "source": {"kind": "model", "provider": "deepseek-official", "model": "deepseek-flash"},
        "content": [{"type": "reasoning", "text": "use ls"},
                    {"type": "tool-call", "id": "c1", "name": "bash", "arguments": "{\"command\": \"ls\"}"}]}}},
    {"type": "tool/result", "data": {"turn": 0, "step": 0, "message": {
        "role": "user", "source": {"kind": "tool", "callId": "c1"},
        "content": [{"type": "tool-result", "toolCallId": "c1", "content": [{"type": "text", "text": "a.py"}]}]}}},
    {"type": "assistant/message", "data": {"turn": 0, "step": 1, "message": {
        "role": "assistant", "source": {"kind": "model", "provider": "deepseek-official", "model": "deepseek-flash"},
        "content": [{"type": "text", "text": "There is a.py."}]}}},
    {"type": "session/title", "data": {"title": "Files", "messageSeqs": [3], "source": "llm"}},
]


def test_a_deepseek_harness_log_reads_as_turns(apps) -> None:
    apps.deepseek("session-1", DEEPSEEK_TURN)
    [conversation] = deepseek_harness.conversations(apps.dsh)
    session = deepseek_harness.parse(conversation)
    assert (session.title, session.cwd, session.model) == ("Files", "/work/ds", "deepseek-flash")
    [turn] = session.turns
    assert turn.user_text == "List the files"
    tool = turn.tools()[0]
    assert (tool.name, tool.arguments, tool.result) == ("bash", {"command": "ls"}, "a.py")
    assert turn.reply_text() == "There is a.py." and turn.blocks[0] == {"type": "thinking", "text": "use ls"}


def test_conversations_started_in_a_terminal_come_in(apps) -> None:
    """``claude`` in a terminal writes ``entrypoint: cli``; the Agent SDK's and ``claude -p``'s stay out."""
    apps.claude("app-1", 1)
    for cli_id, entrypoint in (("term-1", "cli"), ("sdk-1", "sdk-cli")):
        records = _claude_turns(1)
        for record in records:
            record["entrypoint"] = entrypoint
            record["sessionId"] = cli_id
        path = apps.claude_home / "projects" / "-work-repo" / f"{cli_id}.jsonl"
        path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    assert sorted(item.id for item in apps.items()) == ["app-1", "term-1"]


def test_archived_conversations_stay_out(apps) -> None:
    apps.claude("old", 1, archived=True)
    apps.deepseek("session-1", DEEPSEEK_TURN, archived=True)
    assert apps.items() == []


def test_app_conversations_arrive_and_follow_the_app(apps, store, tmp_path) -> None:
    apps.claude("cli-1", 1)
    apps.deepseek("session-1", DEEPSEEK_TURN)
    results = {r["agent"]: r for r in _sync(apps, store, tmp_path)}
    assert {r["status"] for r in results.values()} == {"created"}
    claude = results["claude-code"]["session_id"]
    row = store.get_session(claude)
    assert (row["source"], row["title"], row["model_override"]) == ("claude-code", "Fix the parser", "claude-test")
    # A turn sent here runs in the person's Claude Code, in the same session.
    [record] = row["harness_threads"]["claude-code-harness"].values()
    assert record["thread_id"] == "cli-1" and record["seen"] == row["head_id"]
    assert _texts(store, claude) == ["question 0", "answer 0"]
    assert store.get_session(results["deepseek-harness"]["session_id"])["source"] == "deepseek-harness"
    assert _sync(apps, store, tmp_path) == []  # nothing new: not read again

    # The person went on here, then in the app: the app's turn follows.
    head = row["head_id"]
    store.append_message(claude, {"id": "mine", "role": "user", "content": "said here",
                                  "parent_id": head, "caller": "ROOT",
                                  "predecessor": head, "created_at": 2000})
    store.set_head(claude, "mine")
    apps.claude("cli-1", 2, activity=3000)
    [result] = _sync(apps, store, tmp_path)
    assert (result["status"], result["turns_added"]) == ("appended", 1)
    assert _texts(store, claude) == ["question 0", "answer 0", "said here", "question 1", "answer 1"]


def _reply(message_id, text, turn):
    return {"type": "assistant/message", "data": {"turn": turn, "step": 0, "message": {
        "id": message_id, "role": "assistant", "source": {"kind": "model", "model": "deepseek-flash"},
        "content": [{"type": "text", "text": text}]}}}


def _ask(text):
    return {"type": "user/message", "data": {"role": "user", "source": {"kind": "user"},
                                             "content": [{"type": "text", "text": text}]}}


def test_a_deepseek_conversation_goes_on_in_its_session(apps, store, tmp_path) -> None:
    """It runs on the DeepSeek Harness engine; the turns that engine ran are not written twice."""
    first = [DEEPSEEK_TURN[0], _ask("one"), _reply("r1", "first", 0)]
    apps.deepseek("session-1", first)
    [result] = _sync(apps, store, tmp_path)
    session_id = result["session_id"]
    row = store.get_session(session_id)
    config = row.get("branch_config") or {}
    settings = next(iter(config.values()), config) if config and "harness" not in config else config
    assert (row.get("harness") or settings.get("harness")) == "deepseek-harness"
    assert (row.get("model_override") or settings.get("model_override")) == "deepseek-official:deepseek-flash"
    [record] = row["harness_threads"]["deepseek-harness"].values()
    assert record["thread_id"] == "session-1" and record["seen"] == row["head_id"]

    # A turn ran here in the session (its reply r2), then one in the app (r3).
    head = row["head_id"]
    store.append_message(session_id, {"id": "mine", "role": "user", "content": "two", "parent_id": head,
                                      "caller": "ROOT", "predecessor": head, "created_at": 2000})
    store.append_message(session_id, {"id": "mine_reply", "role": "assistant", "content": "second",
                                      "parent_id": "mine", "caller": "ROOT", "predecessor": "mine",
                                      "created_at": 2001})
    store.set_head(session_id, "mine_reply")
    from openprogram.runtime.harnesses import handoff
    handoff.write_record(store, session_id, "deepseek-harness", "b", {
        "thread_id": "session-1", "seen": "mine_reply", "at": time.time() + 1, "own_turns": ["r2"]})
    apps.deepseek("session-1", first + [_ask("two"), _reply("r2", "second", 1), _ask("three"), _reply("r3", "third", 2)])
    for log in apps.dsh.glob("sessions/*/session-1/*"):
        os.utime(log, (time.time() + 60, time.time() + 60))
    [result] = _sync(apps, store, tmp_path)
    assert result["turns_added"] == 1
    assert _texts(store, session_id) == ["one", "first", "two", "second", "three", "third"]
    records = store.get_session(session_id)["harness_threads"]["deepseek-harness"]
    assert records["b"]["seen"] == store.get_session(session_id)["head_id"] and records["b"]["synced_at"]


def _own_conversation(store, *, seen="mine_reply"):
    """A conversation here whose DeepSeek Harness engine started the app's session-1 (reply r1)."""
    store.create_session("mine", "main", title="Mine")
    store.append_message("mine", {"id": "q", "role": "user", "content": "one", "parent_id": None,
                                  "caller": "ROOT", "predecessor": "ROOT", "created_at": 1000})
    store.append_message("mine", {"id": "mine_reply", "role": "assistant", "content": "first",
                                  "parent_id": "q", "caller": "ROOT", "predecessor": "q", "created_at": 1001})
    store.set_head("mine", "mine_reply")
    from openprogram.runtime.harnesses import handoff
    handoff.write_record(store, "mine", "deepseek-harness", "b", {
        "thread_id": "session-1", "seen": seen, "at": time.time(), "own_turns": ["r1"]})


def test_a_session_an_engine_started_gets_the_apps_later_turns(apps, store, tmp_path) -> None:
    """Not a new conversation: the turn made in the app follows the engine's own."""
    _own_conversation(store)
    apps.deepseek("session-1", [DEEPSEEK_TURN[0], _ask("one"), _reply("r1", "first", 0),
                                _ask("two"), _reply("r2", "second", 1)])
    [result] = _sync(apps, store, tmp_path)
    assert result["session_id"] == "mine" and result["turns_added"] == 1
    assert _texts(store, "mine") == ["one", "first", "two", "second"]
    assert [s["id"] for s in store.list_sessions(limit=10)] == ["mine"]


def test_app_turns_follow_their_thread_when_another_branch_is_shown(apps, store, tmp_path) -> None:
    _own_conversation(store)
    store.append_message("mine", {"id": "other", "role": "user", "content": "elsewhere", "parent_id": "q",
                                  "caller": "ROOT", "predecessor": "q", "created_at": 1002})
    store.set_head("mine", "other")
    apps.deepseek("session-1", [DEEPSEEK_TURN[0], _ask("one"), _reply("r1", "first", 0),
                                _ask("two"), _reply("r2", "second", 1)])
    _sync(apps, store, tmp_path)
    assert store.get_session("mine")["head_id"] == "other"
    [record] = store.get_session("mine")["harness_threads"]["deepseek-harness"].values()
    assert [m["content"] for m in store.get_branch("mine", record["seen"])][-2:] == ["two", "second"]


def test_a_conversation_synced_before_its_engine_is_put_on_it(apps, store, tmp_path) -> None:
    apps.claude("cli-1", 1)
    [result] = _sync(apps, store, tmp_path)
    session_id = result["session_id"]
    store.update_session(session_id, harness_threads={})
    store.update_branch_config(session_id, None, {"harness": None, "provider_override": None})
    assert _sync(apps, store, tmp_path) == []  # nothing new in the app
    row = store.get_session(session_id)
    [record] = row["harness_threads"]["claude-code-harness"].values()
    assert record["thread_id"] == "cli-1" and record["seen"] == row["head_id"]


def test_turns_an_earlier_reading_counted_do_not_stop_the_sync(apps, store, tmp_path) -> None:
    """An earlier reading counted a turn this one does not (Claude Code's interruption note):
    the app's new turns still come, numbered past what was written."""
    from openprogram.state.store.session.external_import.ledger import locked
    from openprogram.state.store.session.external_import.writer import node_prefix, user_id
    apps.claude("cli-1", 2)
    [created] = _sync(apps, store, tmp_path)
    session_id = created["session_id"]
    # As an earlier reading wrote it: a Stop's note as a third turn, last in the ledger.
    note = user_id(node_prefix("claude-code", "cli-1"), 2)
    head = store.get_session(session_id)["head_id"]
    store.append_message(session_id, {"id": note, "role": "user", "content": "[Request interrupted by user]",
                                      "parent_id": head, "caller": "ROOT", "predecessor": head,
                                      "created_at": 1500, "import_fingerprint": "note"})
    store.set_head(session_id, note)
    with locked(tmp_path / "ledger") as ledger:
        entry = ledger.get("claude-code", "cli-1")
        ledger.put("claude-code", "cli-1", {**entry, "turns": 3, "last_fingerprint": "note",
                                            "last_node_id": note + "_reply", "last_digest": "note"})
    apps.claude("cli-1", 3, activity=3000)
    [result] = _sync(apps, store, tmp_path)
    assert (result["status"], result["turns_added"]) == ("appended", 1)
    texts = _texts(store, session_id)
    assert texts[-2:] == ["question 2", "answer 2"] and texts.count("question 1") == 1
    assert store.message_exists(session_id, user_id(node_prefix("claude-code", "cli-1"), 3))
    assert _sync(apps, store, tmp_path) == []


def test_a_rewind_in_the_app_leaves_the_conversation_as_it_is(apps, store, tmp_path) -> None:
    apps.claude("cli-1", 3)
    [created] = _sync(apps, store, tmp_path)
    before = _texts(store, created["session_id"])
    apps.claude("cli-1", 2, activity=3000)  # the app went back a turn
    _sync(apps, store, tmp_path)
    assert _texts(store, created["session_id"]) == before


def test_a_turn_still_running_after_a_note_is_completed(apps, store, tmp_path) -> None:
    """An earlier reading wrote A, a Stop's note, then B while B was still running: B's answer still comes."""
    from openprogram.state.store.session.external_import import claude_code
    from openprogram.state.store.session.external_import.codex_sync import _write_records
    from openprogram.state.store.session.external_import.ledger import locked
    from openprogram.state.store.session.external_import.writer import node_prefix, reply_id, user_id, write_turns
    apps.claude("cli-1", 1)
    [created] = _sync(apps, store, tmp_path)
    session_id = created["session_id"]
    prefix = node_prefix("claude-code", "cli-1")
    # As the earlier reading wrote it: the note as turn 1, then B unanswered as turn 2.
    head = store.get_session(session_id)["head_id"]
    note = user_id(prefix, 1)
    store.append_message(session_id, {"id": note, "role": "user", "content": "[Request interrupted by user]",
                                      "parent_id": head, "caller": "ROOT", "predecessor": head, "created_at": 1500,
                                      "import_fingerprint": "note"})
    apps.claude("cli-1", 2)
    session = claude_code.parse(apps.claude_home / "projects" / "-work-repo" / "cli-1.jsonl")
    session.turns[1].blocks = []
    last = write_turns(store, session_id, session, indices=[1], predecessor=note, offset=1)
    assert last == reply_id(prefix, 2)
    store.set_head(session_id, last)
    _write_records(store, session_id, "cli-1", head, last, provider="claude-code-harness")
    with locked(tmp_path / "ledger") as ledger:
        entry = ledger.get("claude-code", "cli-1")
        ledger.put("claude-code", "cli-1", {**entry, "turns": 3, "offset": 0, "last_node_id": last,
                                            "last_fingerprint": session.turns[1].fingerprint(),
                                            "last_digest": session.turns[1].digest()})
    apps.claude("cli-1", 3, activity=3000)
    [result] = _sync(apps, store, tmp_path)
    assert result["status"] == "appended"
    texts = _texts(store, session_id)
    assert texts.count("answer 1") == 1 and texts.count("question 1") == 1
    assert texts[-2:] == ["question 2", "answer 2"]


def test_a_message_written_as_stopped_stays_while_the_next_one_waits(apps, store, tmp_path) -> None:
    def touch(at):
        for log in apps.dsh.glob("sessions/*/session-1/*"):
            os.utime(log, (at, at))

    first = [DEEPSEEK_TURN[0], _ask("one"), _reply("r1", "first", 0), _ask("stopped")]
    apps.deepseek("session-1", first)
    touch(time.time() - 600)
    [result] = _sync(apps, store, tmp_path)
    assert _texts(store, result["session_id"])[:3] == ["one", "first", "stopped"]
    apps.deepseek("session-1", first + [_ask("waiting")])
    touch(time.time())
    [again] = _sync(apps, store, tmp_path)
    assert again["status"] == "unchanged" and again["waiting"]
    assert _texts(store, result["session_id"])[:3] == ["one", "first", "stopped"]
