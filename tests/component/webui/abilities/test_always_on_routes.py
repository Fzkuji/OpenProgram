"""HTTP surface of always-on: Agent configuration and conversation work."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openprogram.runtime.conversation.always_on import runtime
from openprogram.runtime.agent_registry import manager
from openprogram.interfaces.server.routes.catalog import agents


def _client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setattr(manager, "_state_root", lambda: tmp_path)
    manager.create("main", name="Default Agent", make_default=True)
    app = FastAPI()
    agents.register(app)
    return TestClient(app)


def test_always_on_section_saves_and_status_reads(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    agent = client.get("/api/agents/main").json()["agent"]
    assert agent["always_on"]["enabled"] is False
    saved = client.patch("/api/agents/main", json={
        "expected_revision": agent["revision"],
        "always_on": {"enabled": True, "goal": "Watch CI", "wake_minutes": 15, "channels": [
            {"channel": "telegram", "account_id": "default", "peer_id": "42", "label": "Bob"},
        ], "quiet_hours": {"start": "22:00", "end": "07:30"}, "wake_days": [0, 1, 2, 3, 4]},
    })
    assert saved.status_code == 200
    assert saved.json()["agent"]["always_on"] == {
        "enabled": True, "goal": "Watch CI", "wake_minutes": 15,
        "research": True, "autonomy": "report",
        "channels": [{"channel": "telegram", "account_id": "default", "peer_id": "42", "label": "Bob"}],
        "quiet_hours": {"start": "22:00", "end": "07:30"}, "wake_days": [0, 1, 2, 3, 4],
        "check_in_model": "", "notify": manager.default_notify(), "daily_budget_usd": None,
    }
    tuned = client.patch("/api/agents/main", json={
        "expected_revision": saved.json()["agent"]["revision"],
        "always_on": {"wake_minutes": None, "check_in_model": "openai/gpt-5-mini", "daily_budget_usd": 0.5,
                      "notify": {"finding": {"desktop": False, "channels": False}}},
    })
    assert tuned.status_code == 200
    tuned_config = tuned.json()["agent"]["always_on"]
    assert tuned_config["wake_minutes"] is None and tuned_config["daily_budget_usd"] == 0.5
    assert tuned_config["notify"]["finding"] == {"desktop": False, "channels": False}
    assert tuned_config["goal"] == "Watch CI"
    saved = tuned
    bad_quiet = client.patch("/api/agents/main", json={
        "expected_revision": saved.json()["agent"]["revision"],
        "always_on": {"quiet_hours": {"start": "22:00", "end": "22:00"}},
    })
    assert bad_quiet.status_code == 400
    bad = client.patch("/api/agents/main", json={
        "expected_revision": saved.json()["agent"]["revision"],
        "always_on": {"wake_minutes": 2},
    })
    assert bad.status_code == 400
    bad_channel = client.patch("/api/agents/main", json={
        "expected_revision": saved.json()["agent"]["revision"],
        "always_on": {"channels": [{"channel": "email", "account_id": "a", "peer_id": "b"}]},
    })
    assert bad_channel.status_code == 400
    status = client.get("/api/agents/main/always-on").json()
    assert status["config"]["goal"] == "Watch CI"
    assert isinstance(status["channel_targets"], list)
    assert client.get("/api/agents/missing/always-on").status_code == 404
    # Wake state belongs to conversations, not to the Agent.
    assert client.post("/api/agents/main/always-on/wake").status_code in (404, 405)


def _work_client(tmp_path, monkeypatch):
    from openprogram.interfaces.server.routes.execution import conversation_work

    monkeypatch.setattr(manager, "_state_root", lambda: tmp_path)
    manager.create("main", name="Default Agent", make_default=True)

    class Db:
        sessions = {"c1": {"id": "c1", "agent_id": "main", "source": "", "archived": False}}

        def read_index(self, _sid, _reader):
            return None

        def get_session(self, sid):
            return self.sessions.get(sid)

        def _session_dir(self, sid):
            folder = tmp_path / "sessions" / sid
            if sid in self.sessions:
                folder.mkdir(parents=True, exist_ok=True)
            return folder

        def state_file(self, sid, name):
            return self._session_dir(sid) / name

    db = Db()
    monkeypatch.setattr(runtime, "_db", lambda: db)
    monkeypatch.setattr("openprogram.runtime.context.session_db.default_db", lambda: db)
    monkeypatch.setattr(runtime, "_turn_running", lambda sid: False)
    monkeypatch.setattr(runtime, "_goal", lambda sid: None)
    monkeypatch.setattr(runtime, "_broadcast", lambda frame: None)
    monkeypatch.setattr("openprogram.runtime.conversation.open_work._publish", lambda *a: None)
    tasks = [
        {"id": "t1", "type": "webhook", "title": "Issues", "created_at": 1, "hook_key": "k",
         "execution": {"session_id": "c1", "signature": "secret"}},
        {"id": "t2", "type": "recurring", "title": "Other", "created_at": 2,
         "execution": {"session_id": "c9"}},
        {"id": "t3", "type": "recurring", "title": "Build", "created_at": 3, "session_id": "c1",
         "execution": {"session_id": "cron-t3"}},
    ]
    monkeypatch.setattr("openprogram.runtime.scheduler.service.list_tasks", lambda: tasks)
    app = FastAPI()
    conversation_work.register(app)
    return TestClient(app)


def test_a_conversation_reads_its_own_work(tmp_path, monkeypatch) -> None:
    from openprogram.runtime.conversation import open_work

    client = _work_client(tmp_path, monkeypatch)
    open_work.add("c1", "Wait for Dana")
    body = client.get("/api/sessions/c1/work").json()
    assert body["always_on"]["enabled"] is False
    assert [item["text"] for item in body["open_work"]] == ["Wait for Dana"]
    assert [row["id"] for row in body["routines"]] == ["t3", "t1"]
    assert "execution" not in body["routines"][1]
    assert body["routines"][1]["hook_url"].endswith("/hooks/t1")
    assert client.get("/api/sessions/missing/work").status_code == 404


def test_open_work_is_finished_and_dropped_from_the_panel(tmp_path, monkeypatch) -> None:
    from openprogram.runtime.conversation import open_work

    client = _work_client(tmp_path, monkeypatch)
    item = open_work.add("c1", "Send the draft")
    done = client.patch(f"/api/sessions/c1/open-work/{item['id']}", json={"status": "done"})
    assert done.status_code == 200 and done.json()["status"] == "done"
    assert client.patch("/api/sessions/c1/open-work/9", json={"status": "done"}).status_code == 404
    assert client.patch(f"/api/sessions/c1/open-work/{item['id']}", json={"status": "x"}).status_code == 400
    assert client.delete(f"/api/sessions/c1/open-work/{item['id']}").status_code == 200
    assert open_work.load("c1") == []


def test_wake_now_is_refused_while_off(tmp_path, monkeypatch) -> None:
    client = _work_client(tmp_path, monkeypatch)
    response = client.post("/api/sessions/c1/always-on/wake")
    assert response.status_code == 409
    assert "off" in response.json()["error"]
    assert client.post("/api/sessions/missing/always-on/wake").status_code == 409


def test_report_preferences_are_added_and_removed(tmp_path, monkeypatch) -> None:
    client = _work_client(tmp_path, monkeypatch)
    assert client.post("/api/sessions/c1/always-on/preferences", json={"text": "x"}).status_code == 409
    manager.update("main", {"always_on": {"enabled": True}})
    assert client.post("/api/sessions/c1/always-on/preferences", json={"text": " "}).status_code == 400
    added = client.post("/api/sessions/c1/always-on/preferences",
                        json={"text": "Don't report: Nightly build passed"})
    assert added.status_code == 200 and added.json()["text"] == "Don't report: Nightly build passed"
    body = client.get("/api/sessions/c1/work").json()["always_on"]
    assert [row["id"] for row in body["preferences"]] == [added.json()["id"]]
    assert body["today_cost_usd"] == 0.0 and body["next_wake_reason"] is None
    assert set(body["config"]) >= {"check_in_model", "notify", "daily_budget_usd", "wake_minutes"}
    pref = added.json()["id"]
    assert client.delete(f"/api/sessions/c1/always-on/preferences/{pref}").status_code == 200
    assert client.delete(f"/api/sessions/c1/always-on/preferences/{pref}").status_code == 404


def test_the_pending_inbox_lists_unanswered_decisions_and_unseen_news(tmp_path, monkeypatch) -> None:
    from openprogram.runtime.conversation.always_on import pending, store

    client = _work_client(tmp_path, monkeypatch)
    db = runtime._db()
    db.sessions["c1"]["title"] = "VPS watch"
    db.list_sessions = lambda agent_id=None, limit=None: [s for s in db.sessions.values() if s["agent_id"] == agent_id]
    chain: list[dict] = []
    db.get_branch = lambda sid, head=None: chain
    monkeypatch.setattr(pending, "_db", lambda: db)
    assert client.get("/api/always-on/pending").json() == {"items": []}

    manager.update("main", {"always_on": {"enabled": True}})
    ask = store.add_report("c1", title="Buy the 2G plan?", body="$18.9/year\nmore", kind="approval")
    news = store.add_report("c1", title="One plan is back in stock", body="", kind="finding")
    old = store.add_report("c1", title="Old news", body="", kind="done")
    store.update_report("c1", old["id"], at=old["at"] - 8 * 86400)
    items = client.get("/api/always-on/pending").json()["items"]
    assert [(item["title"], item["kind"]) for item in items] == [
        ("One plan is back in stock", "finding"), ("Buy the 2G plan?", "approval")]
    assert items[0]["session_title"] == "VPS watch" and items[0]["agent_name"] == "Default Agent"

    # Opening the conversation reads its news; the decision waits for a reply.
    assert pending.mark_seen("c1") is True and pending.mark_seen("c1") is False
    assert [item["report_id"] for item in pending.items()] == [ask["id"]]
    chain.append({"role": "user", "timestamp": ask["at"] - 1})
    assert [item["report_id"] for item in pending.items()] == [ask["id"]]
    chain.append({"role": "user", "timestamp": ask["at"] + 1})
    assert pending.items() == []
    assert news["id"] != ask["id"]


def _pending_with_chain(tmp_path, monkeypatch) -> tuple:
    from openprogram.runtime.conversation.always_on import pending

    _work_client(tmp_path, monkeypatch)
    db = runtime._db()
    db.scanned = []
    def list_sessions(agent_id=None, limit=None):
        db.scanned.append(agent_id)
        return [s for s in db.sessions.values() if s["agent_id"] == agent_id]
    db.list_sessions = list_sessions
    chain: list[dict] = []
    db.get_branch = lambda sid, head=None: chain
    monkeypatch.setattr(pending, "_db", lambda: db)
    return pending, db, chain


@pytest.mark.parametrize("automatic", [
    {"automation": "handoff"}, {"automation": "reminder"}, {"display": "runtime"}, {"speaker_kind": "runtime"},
])
def test_a_turn_nobody_typed_does_not_answer_a_decision(tmp_path, monkeypatch, automatic) -> None:
    from openprogram.runtime.conversation.always_on import store

    pending, _db, chain = _pending_with_chain(tmp_path, monkeypatch)
    manager.update("main", {"always_on": {"enabled": True}})
    ask = store.add_report("c1", title="Buy the 2G plan?", body="", kind="approval")
    chain.append({"role": "user", "timestamp": ask["at"] + 25 * 3600, **automatic})
    assert [item["report_id"] for item in pending.items(now=ask["at"] + 26 * 3600)] == [ask["id"]]
    # Behind that later automatic row, a real message answers it.
    chain.insert(0, {"role": "user", "timestamp": ask["at"] + 1})
    assert pending.items(now=ask["at"] + 26 * 3600) == []


def test_a_message_typed_into_an_automatic_turn_answers_a_decision(tmp_path, monkeypatch) -> None:
    from openprogram.runtime.conversation.always_on import store

    pending, _db, chain = _pending_with_chain(tmp_path, monkeypatch)
    manager.update("main", {"always_on": {"enabled": True}})
    ask = store.add_report("c1", title="Buy the 2G plan?", body="", kind="approval")
    chain.append({"role": "user", "timestamp": ask["at"] + 1, "steering": True, "speaker_kind": "runtime"})
    assert pending.items() == []


def test_turning_always_on_off_keeps_pending_and_a_never_enabled_agent_is_not_scanned(tmp_path, monkeypatch) -> None:
    from openprogram.runtime.conversation.always_on import store

    pending, db, _chain = _pending_with_chain(tmp_path, monkeypatch)
    assert pending.items() == [] and "main" not in db.scanned

    manager.update("main", {"always_on": {"enabled": True}})
    ask = store.add_report("c1", title="Buy the 2G plan?", body="", kind="approval")
    store.update_agent_state("main", enabled_at=None)  # what a tick leaves after turning off
    manager.update("main", {"always_on": {"enabled": False}})
    assert [item["report_id"] for item in pending.items()] == [ask["id"]]
