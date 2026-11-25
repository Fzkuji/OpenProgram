"""The Claude app's and DeepSeek Harness's conversations come into OpenProgram and follow the apps."""
from __future__ import annotations

import json

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
    assert row.get("harness") is None  # a turn sent here runs in OpenProgram
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
