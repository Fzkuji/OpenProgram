"""What a trial's data may contain, and that nothing in it can repeat."""
from __future__ import annotations

import itertools
import json
import os
from pathlib import Path
import shutil
import sqlite3

import pytest

from openprogram.operations.self_update.trial import data_copy
from openprogram.operations.self_update.trial.fake_model import MODEL_ID, PROVIDER_ID
from openprogram.runtime.execution._schema import TABLES
from openprogram.state.store.session.session_log import SessionLog, encode_record, read_log_file

SECRET = "sk-live-secret"
MODEL_URL = "http://127.0.0.1:9/v1"


CHAT = "chat-4"
_STAMPS = itertools.count(1)


def _row(table: str, **values) -> dict:
    """A whole row, the way the worker logs one."""
    row = {name: "{}" if name.endswith("_json") else "x" for name in TABLES[table].required}
    row.update(created_at=1.0, updated_at=1.0, **values)
    return {name: value for name, value in row.items() if name in TABLES[table].columns}


def _execution(execution_id: str, status: str) -> dict:
    return _row("executions", execution_id=execution_id, session_id=CHAT, status=status, status_version=1,
                current_attempt_id=None if status == "completed" else "attempt-1")


def _append(chat: Path, record_type: str, data: dict) -> None:
    with open(chat / "main.jsonl", "ab") as stream:
        stream.write(encode_record(next(_STAMPS), record_type, data))


def _log_rows(chat: Path, *changes: dict) -> None:
    _append(chat, "turn.rows", {"rows": list(changes)})


def _change(table: str, **values) -> dict:
    return {"t": table, "r": _row(table, **values)}


def _folded(chat: Path) -> dict:
    """What the session's real log reader returns, last line for a row winning."""
    latest: dict = {}
    for batch in SessionLog(chat).read_run_rows():
        for line in batch:
            key = line["gone"][0] if "gone" in line else line["r"][TABLES[line["t"]].key[0]]
            latest[(line["t"], key)] = line.get("r")
    return latest


@pytest.fixture
def live(tmp_path: Path) -> Path:
    state = tmp_path / "live"
    state.mkdir()
    (state / "config.json").write_text(json.dumps({
        "hooks": {"session.start": []},
        "providers": {"openai": {"enabled": True, "api_key": SECRET, "max_tokens": 5}},
        "ui": {"web_port": 18100},
        "channels": {"wechat": {"token": SECRET}},
    }))
    for name, content in {
        "auth/profile.json": SECRET, "mcp_tokens/server.json": SECRET, "mcp_servers.json": SECRET,
        "channels/wechat/state.json": SECRET, "chrome-profile/Cookies": SECRET, "runtime/current": SECRET,
        "self-updates/active.json": SECRET, "worktrees/x/file": SECRET, "logs/worker.log": SECRET,
        "recordings/a.wav": SECRET, "meetings/a.json": SECRET, "worker.pid": "1", "worker.port": "18100",
        "owner.json": json.dumps({"version": 1, "principal_id": "owner/install/real"}),
        "programs/mine/program.py": "x = 1\n", "skills/s/SKILL.md": "skill\n", "program-sources.json": "{}\n",
        "agents.json": "{}\n", "projects/projects.json": "{}\n", "projects/default/settings.json": "{}\n",
        "projects/default/secret-project-file.txt": SECRET,
    }.items():
        path = state / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    agent = state / "agents" / "main" / "agent.json"
    agent.parent.mkdir(parents=True)
    agent.write_text(json.dumps({"id": "main", "model": {"provider": "openai", "id": "gpt"}, "api_key": SECRET}))
    for index in range(5):
        chat = state / "projects" / "default" / "chats" / f"chat-{index}"
        chat.mkdir(parents=True)
        _append(chat, "meta.patch", {"set": {"title": f"chat {index}"}})
        moment = 1000 + index
        os.utime(chat / "main.jsonl", (moment, moment))
        os.utime(chat, (moment, moment))
    chat = state / "projects" / "default" / "chats" / CHAT
    _append(chat, "file.put", {"name": "jobs.json", "content": json.dumps(
        {"version": 1, "jobs": {"a": {"status": "running"}, "b": {"status": "completed"}}})})
    _log_rows(chat, {"t": "executions", "r": _execution("running-1", "running")},
              {"t": "executions", "r": _execution("finished-1", "completed")})
    connection = sqlite3.connect(state / "processes.db")
    connection.execute("CREATE TABLE processes (id TEXT, pid INTEGER)")
    connection.execute("INSERT INTO processes VALUES ('p', 123)")
    connection.commit()
    connection.close()
    return state


def test_credentials_and_live_state_are_never_copied(live, tmp_path):
    target = tmp_path / "copy"
    data_copy.copy_state(live, target, model_base_url=MODEL_URL)
    for name in ("auth", "mcp_tokens", "mcp_servers.json", "channels", "chrome-profile", "runtime", "self-updates",
                 "worktrees", "logs", "recordings", "meetings", "worker.pid", "worker.port"):
        assert not (target / name).exists(), name
    everything = "".join(path.read_text(errors="replace") for path in target.rglob("*")
                         if path.is_file() and not path.name.endswith(".db"))
    assert SECRET not in everything
    assert not (target / "projects" / "default" / "secret-project-file.txt").exists()


def test_the_config_has_no_hooks_and_one_loopback_model_as_default(live, tmp_path):
    target = tmp_path / "copy"
    data_copy.copy_state(live, target, model_base_url=MODEL_URL)
    config = json.loads((target / "config.json").read_text())
    assert "hooks" not in config and "ui" not in config
    assert config["providers"]["openai"] == {"enabled": True, "max_tokens": 5}
    assert config["providers"][PROVIDER_ID]["base_url"] == MODEL_URL
    assert config["last_used_model"] == {"provider": PROVIDER_ID, "model": MODEL_ID}
    assert config["execution"]["auto_resume_window_seconds"] == 0
    agent = json.loads((target / "agents" / "main" / "agent.json").read_text())
    assert agent["model"] == {"provider": PROVIDER_ID, "id": MODEL_ID} and "api_key" not in agent


def test_only_the_last_three_conversations_are_copied(live, tmp_path):
    target = tmp_path / "copy"
    report = data_copy.copy_state(live, target, model_base_url=MODEL_URL)
    assert set(report.conversations) == {"chat-2", "chat-3", "chat-4"}
    assert {path.name for path in (target / "projects/default/chats").iterdir()} == {"chat-2", "chat-3", "chat-4"}


def test_a_fresh_owner_is_generated(live, tmp_path):
    target = tmp_path / "copy"
    data_copy.copy_state(live, target, model_base_url=MODEL_URL)
    owner = json.loads((target / "owner.json").read_text())
    assert owner["principal_id"].startswith("owner/install/") and owner["principal_id"] != "owner/install/real"


def test_a_running_row_in_the_source_is_never_resumed(live, tmp_path):
    target = tmp_path / "copy"
    source_log = live / "projects/default/chats" / CHAT / "main.jsonl"
    before = source_log.read_bytes()
    report = data_copy.copy_state(live, target, model_base_url=MODEL_URL)
    assert report.sanitized["executions"] == 1
    chat = target / "projects/default/chats" / CHAT
    rows = _folded(chat)
    assert rows[("executions", "running-1")]["status"] == "interrupted"
    assert rows[("executions", "running-1")]["current_attempt_id"] is None
    assert rows[("executions", "finished-1")]["status"] == "completed"
    # The ending is appended, so what the worker wrote is still there and still passes its checksum.
    assert (chat / "main.jsonl").read_bytes().startswith(before)
    assert len(read_log_file(chat / "main.jsonl")) == len((chat / "main.jsonl").read_bytes().splitlines())
    # The source keeps running; only the copy changed.
    assert source_log.read_bytes() == before


def test_every_kind_of_unfinished_run_row_ends(live, tmp_path):
    chat = live / "projects/default/chats" / CHAT
    _log_rows(chat,
              _change("attempts", attempt_id="a1", execution_id="running-1", generation=1, status="active"),
              _change("execution_waits", wait_id="w1", execution_id="running-1", status="open", generation=1),
              _change("commands", command_id="c1", execution_id="running-1", status="accepted"),
              _change("effects", effect_id="f1", execution_id="running-1", status="dispatched"),
              _change("execution_resource_intents", intent_id="i1", execution_id="running-1", state="pending"),
              _change("job_admissions", admission_id="d1", job_id="running-1", session_id=CHAT, state="queued"),
              _change("job_finalizations", job_id="running-1", session_id=CHAT, state="pending"),
              _change("usage_reservations", reservation_id="r1", job_id="running-1", session_id=CHAT, state="reserved"))
    target = tmp_path / "copy"
    report = data_copy.copy_state(live, target, model_base_url=MODEL_URL)
    for name in ("attempts", "waits", "commands", "effects", "resource_intents", "admissions", "finalizations",
                 "reservations"):
        assert report.sanitized[name] == 1, name
    rows = _folded(target / "projects/default/chats" / CHAT)
    assert rows[("attempts", "a1")]["status"] == "ended"
    assert rows[("execution_waits", "w1")]["status"] == "cancelled"
    assert rows[("commands", "c1")]["status"] == "rejected"
    assert rows[("effects", "f1")]["status"] == "not_committed"
    assert rows[("execution_resource_intents", "i1")]["state"] == "applied"
    assert rows[("job_admissions", "d1")]["state"] == "released"
    assert rows[("job_finalizations", "running-1")]["state"] == "completed"
    assert rows[("usage_reservations", "r1")]["state"] == "released"


def _event(sequence: int, kind: str, payload: dict) -> dict:
    return {"t": "execution_events", "r": {
        "sequence": sequence, "execution_id": "running-1", "execution_sequence": sequence, "execution_version": 1,
        "kind": kind, "payload_json": json.dumps(payload), "created_at": 1.0, "schema_version": 1}}


def test_only_an_unsettled_restart_intent_is_removed(live, tmp_path):
    chat = live / "projects/default/chats" / CHAT
    _log_rows(chat, _event(1, "execution.restart.requested", {}),
              _event(2, "execution.restart.settled", {"request_sequence": 1}),
              _event(3, "execution.restart.requested", {}))
    target = tmp_path / "copy"
    report = data_copy.copy_state(live, target, model_base_url=MODEL_URL)
    assert report.sanitized["restart_intents"] == 1
    tables = data_copy._copied_run_tables(target, [CHAT])
    assert [row["sequence"] for row in tables.handle.rows("execution_events", order="sequence")] == [1, 2]


def test_a_restart_intent_the_store_wrote_is_cleared_in_the_copy(live, tmp_path):
    from openprogram.runtime.execution.store import ExecutionStore

    tables = data_copy._copied_run_tables(live, [CHAT])
    with tables.transaction() as rows:
        ExecutionStore._append_event(rows, execution_id="running-1", kind="execution.restart.requested",
                                     payload={"mode": "checkpoint"}, created_at=1.0)
    target = tmp_path / "copy"
    report = data_copy.copy_state(live, target, model_base_url=MODEL_URL)
    assert report.sanitized["restart_intents"] == 1
    assert not data_copy._unsettled_restarts(data_copy._copied_run_tables(target, [CHAT]).handle)


def test_run_state_that_stays_unfinished_stops_the_trial(live, tmp_path, monkeypatch):
    from openprogram.state.store.session.session_store.service import SessionStore

    monkeypatch.setattr(SessionStore, "append_run_rows", lambda *_args: None)
    with pytest.raises(data_copy.DataCopyError, match="executions"):
        data_copy.copy_state(live, tmp_path / "copy", model_base_url=MODEL_URL)


def test_the_run_logs_beside_the_conversations_are_copied(live, tmp_path):
    (live / "run_index.jsonl").write_text(
        json.dumps({"e": "running-1", "s": CHAT}) + "\n" + json.dumps({"e": "other", "s": "chat-0"}) + "\n")
    (live / "revisions.jsonl").write_text(json.dumps({"rows": []}) + "\n")
    target = tmp_path / "copy"
    data_copy.copy_state(live, target, model_base_url=MODEL_URL)
    assert [json.loads(line) for line in (target / "run_index.jsonl").read_text().splitlines()] == [
        {"e": "running-1", "s": CHAT}]
    assert (target / "revisions.jsonl").read_text() == (live / "revisions.jsonl").read_text()


def test_conversations_with_no_session_log_stop_the_trial(live, tmp_path):
    for log in live.glob("projects/default/chats/*/main.jsonl"):
        log.unlink()
    with pytest.raises(data_copy.DataCopyError, match="session log"):
        data_copy.copy_state(live, tmp_path / "copy", model_base_url=MODEL_URL)


def test_the_file_recovery_folder_does_not_count_as_a_conversation(live, tmp_path):
    chats = live / "projects/default/chats"
    shutil.rmtree(chats)
    (chats / ".file-recovery").mkdir(parents=True)
    report = data_copy.copy_state(live, tmp_path / "copy", model_base_url=MODEL_URL)
    assert report.conversations == []


def test_a_folder_without_a_log_is_not_picked_over_a_conversation(live, tmp_path):
    leftover = live / "projects/default/chats/leftover"
    leftover.mkdir()
    (leftover / "old.json").write_text("{}")
    report = data_copy.copy_state(live, tmp_path / "copy", model_base_url=MODEL_URL)
    assert "leftover" not in report.conversations and len(report.conversations) == 3


def test_a_copied_conversation_that_lost_its_log_stops_the_sanitize(live, tmp_path):
    target = tmp_path / "copy"
    data_copy.copy_state(live, target, model_base_url=MODEL_URL)
    (target / "projects/default/chats" / CHAT / "main.jsonl").unlink()
    with pytest.raises(data_copy.DataCopyError, match=CHAT):
        data_copy.sanitize(target)


def test_queued_jobs_and_live_processes_end_in_the_copy(live, tmp_path):
    target = tmp_path / "copy"
    report = data_copy.copy_state(live, target, model_base_url=MODEL_URL)
    assert report.sanitized["jobs"] == 1 and report.sanitized["processes"] == 1
    from openprogram.state.store.session.session_state_file import read_files

    jobs = json.loads(read_files(target / "projects/default/chats" / CHAT)["jobs.json"])["jobs"]
    assert jobs["a"]["status"] == "cancelled" and jobs["b"]["status"] == "completed"
    connection = sqlite3.connect(target / "processes.db")
    assert connection.execute("SELECT COUNT(*) FROM processes").fetchone() == (0,)
    connection.close()


def test_a_database_past_the_limit_is_sampled_and_reported(live, tmp_path):
    big = sqlite3.connect(live / "usage.db")
    big.execute("CREATE TABLE usage (id INTEGER PRIMARY KEY, payload TEXT)")
    big.executemany("INSERT INTO usage (payload) VALUES (?)", [("x" * 100,) for _ in range(5000)])
    big.commit()
    big.close()
    target = tmp_path / "copy"
    limit = sum(path.stat().st_size for path in live.glob("*.db") if path.name != "usage.db")
    report = data_copy.copy_state(live, target, model_base_url=MODEL_URL, database_limit=limit)
    assert report.sampled == ["usage.db"]
    connection = sqlite3.connect(target / "usage.db")
    assert connection.execute("SELECT COUNT(*) FROM usage").fetchone() == (data_copy.SAMPLE_ROWS,)
    connection.close()
    usage = next(item for item in report.databases if item.name == "usage.db")
    assert usage.scale == 5000 / data_copy.SAMPLE_ROWS


def test_a_database_that_cannot_be_sampled_stops_with_a_reason(live, tmp_path):
    (live / "broken.db").write_bytes(b"this is not a database")
    target = tmp_path / "copy"
    with pytest.raises(data_copy.DataCopyError):
        data_copy.copy_state(live, target, model_base_url=MODEL_URL, database_limit=0)
