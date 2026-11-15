"""Conversation titles name what the branch is about now.

Both titling paths (background retitles in ``_maybe_auto_title`` and the
menu's "Generate a name" in ``_llm_rename``) build the model's input with
``build_title_context``: a greeting opener is skipped, recent turns are
weighted, tool records are left out. A conversation that opened with small
talk is retitled at turns 2 and 3. A typed title locks; a generated one
does not. The title model is stubbed throughout.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from openprogram.interfaces.server import server
from openprogram.interfaces.server.ws_actions import session as actions
from openprogram.runtime.context.session_db import SessionDB
from openprogram.runtime.conversation.turn_runtime import titles

SID = "title-ctx"

GITHUB_TURNS = [
    ("hl", "在，有什么需要我帮忙的？"),
    ("你能操控 Chrome 吗？我们打开了一个 GitHub 账号，你把账号的个人信息改成我的信息。",
     "能，我看到了 GitHub 资料编辑页面。确认一下这是你有权修改的账号吗？"),
    ("可以的可以的信息你自己去填就行了好吧",
     "已保存，GitHub 个人主页显示为：姓名 Zichuan FU，单位 City University of Hong Kong。"),
]


@pytest.fixture
def db(tmp_path, monkeypatch):
    store = SessionDB(tmp_path / "sessions-git")
    monkeypatch.setattr("openprogram.runtime.context.session_db.default_db", lambda: store)
    store.create_session(SID, "main", title="")
    return store


@pytest.fixture
def model(monkeypatch):
    """Stub title model: records each context it is given."""
    contexts: list[str] = []

    def generate(context: str) -> str:
        contexts.append(context)
        return "Update GitHub profile" if "GitHub" in context else "Simple greeting"

    monkeypatch.setattr(titles, "_generate_title_from_context", generate)
    return contexts


@pytest.fixture(autouse=True)
def synchronous_titles(monkeypatch):
    """Run the background title thread inline and record broadcasts."""
    class Inline:
        def __init__(self, target, daemon=None):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(titles, "threading", SimpleNamespace(Thread=Inline))
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(titles, "_broadcast_title_update",
                        lambda sid, title: sent.append((sid, title)))
    return sent


def _append(db, turns, start=0, pred=None):
    for i, (user, reply) in enumerate(turns, start):
        db.append_message(SID, {"id": f"u{i}", "role": "user", "content": user,
                                "timestamp": float(2 * i), "predecessor": pred})
        db.append_message(SID, {"id": f"a{i}", "role": "assistant", "content": reply,
                                "timestamp": float(2 * i + 1), "predecessor": f"u{i}"})
        pred = f"a{i}"
    db.set_head(SID, pred)
    return pred


def _turn(db, n):
    """Persist turn ``n`` (0-based) of GITHUB_TURNS and run the titler."""
    pred = f"a{n - 1}" if n else None
    _append(db, [GITHUB_TURNS[n]], start=n, pred=pred)
    user, reply = GITHUB_TURNS[n]
    titles._maybe_auto_title(db, SID, db.get_session(SID), user, reply)
    return db.get_session(SID)


def test_context_skips_greeting_opener_and_centres_on_the_task(db):
    _append(db, GITHUB_TURNS)
    context = titles.build_title_context(db.get_branch(SID))
    assert "GitHub" in context
    assert "hl" not in context.split()
    assert "有什么需要我帮忙" not in context
    # The later acknowledgement's reply carries the work; it stays.
    assert "Zichuan FU" in context
    assert len(context) <= titles._MAX_CONTEXT_CHARS


def test_context_leaves_out_tool_and_runtime_records():
    messages = [
        {"role": "tool", "display": "runtime", "content": "You are main, an agent..."},
        {"role": "user", "content": "Rename the files in the photos folder by date"},
        {"role": "assistant", "content": "Renamed 42 files."},
        {"role": "tool", "content": '{"stdout": "mv a b"}'},
        {"role": "user", "content": "[attachment: /tmp/a.png] thanks"},
    ]
    context = titles.build_title_context(messages)
    assert "photos folder" in context
    assert "You are main" not in context and "stdout" not in context
    assert "attachment" not in context


def test_context_weights_recent_turns_within_the_limit():
    messages = []
    for i in range(30):
        messages.append({"role": "user", "content": f"step {i} " + "x" * 300})
        messages.append({"role": "assistant", "content": f"done {i} " + "y" * 300})
    context = titles.build_title_context(messages)
    assert len(context) <= titles._MAX_CONTEXT_CHARS
    assert "step 0 " in context and "step 29 " in context
    assert "step 5 " not in context and "…" in context


def test_all_small_talk_keeps_everything():
    context = titles.build_title_context([
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "Hello! How can I help?"},
    ])
    assert "User: hi" in context


def test_generate_a_name_uses_the_shared_context(db, model):
    from openprogram.interfaces.server.ws_actions.session import lifecycle

    _append(db, GITHUB_TURNS)
    assert lifecycle._llm_rename(SID) == "Update GitHub profile"
    assert model == [titles.build_title_context(db.get_branch(SID))]


def test_background_titles_use_the_shared_context_and_retitle_after_small_talk(db, model, synchronous_titles):
    after_one = _turn(db, 0)
    assert after_one["title"] == "Simple greeting"
    after_two = _turn(db, 1)
    assert after_two["title"] == "Update GitHub profile"
    assert model[-1] == titles.build_title_context(db.get_branch(SID))
    _turn(db, 2)
    assert len(model) == 3  # turn 3 retitles too
    assert synchronous_titles[-1] == (SID, "Update GitHub profile")


def test_no_early_retitle_after_a_substantive_opener(db, model):
    db.append_message(SID, {"id": "u0", "role": "user", "timestamp": 0.0, "predecessor": None,
                            "content": "Help me update my GitHub profile"})
    db.append_message(SID, {"id": "a0", "role": "assistant", "timestamp": 1.0,
                            "predecessor": "u0", "content": "Sure."})
    db.set_head(SID, "a0")
    titles._maybe_auto_title(db, SID, db.get_session(SID), "Help me update my GitHub profile")
    _append(db, [("Now set the location", "Location set to Hong Kong.")], start=1, pred="a0")
    titles._maybe_auto_title(db, SID, db.get_session(SID), "Now set the location")
    assert len(model) == 1


def test_context_follows_the_current_branch(db):
    _append(db, GITHUB_TURNS[:2])
    db.append_message(SID, {"id": "u9", "role": "user", "timestamp": 9.0, "predecessor": "a0",
                            "content": "Plan a trip to Kyoto in spring"})
    db.append_message(SID, {"id": "a9", "role": "assistant", "timestamp": 10.0,
                            "predecessor": "u9", "content": "Here is a 5-day Kyoto plan."})
    db.set_head(SID, "a9")
    context = titles.build_title_context(db.get_branch(SID))
    assert "Kyoto" in context and "GitHub" not in context


def _rename(db, monkeypatch, **cmd):
    class Socket:
        frames: list = []

        async def send_text(self, text):
            self.frames.append(json.loads(text))

    monkeypatch.setattr(server, "_sessions", {})
    monkeypatch.setattr(server, "_broadcast", lambda text: None)

    async def go():
        task = await actions.handle_rename_session(Socket(), {"session_id": SID, **cmd})
        if task is not None:
            await task
    asyncio.run(go())
    return db.get_session(SID)


def test_typed_title_is_never_overwritten(db, model, monkeypatch):
    _turn(db, 0)
    row = _rename(db, monkeypatch, title="My GitHub chores")
    assert row["extra_meta"]["_user_titled"] is True
    calls = len(model)
    _turn(db, 1)
    _turn(db, 2)
    assert db.get_session(SID)["title"] == "My GitHub chores"
    assert len(model) == calls


def test_generated_name_does_not_lock(db, model, monkeypatch):
    _turn(db, 0)
    _turn(db, 1)
    # An older typed lock is replaced by the generated name the user asked for.
    db.update_session(SID, title="Typed earlier", _user_titled=True)
    row = _rename(db, monkeypatch)
    assert row["title"] == "Update GitHub profile"
    assert not row["extra_meta"].get("_user_titled")
    assert row["extra_meta"]["_auto_titled"] is True
    # Later background retitles may still improve it.
    _turn(db, 2)
    assert len(model) == 4  # turn 1, turn 2, generate, turn 3
    # A typed title still wins afterwards.
    assert _rename(db, monkeypatch, title="Mine")["extra_meta"]["_user_titled"] is True


def test_retitle_keeps_a_title_changed_during_the_call(db, monkeypatch):
    monkeypatch.setattr(titles, "_generate_title_from_context", lambda c: "First")
    _turn(db, 0)

    def generate(context):
        db.update_session(SID, title="Generated meanwhile", _user_titled=False)
        return "Stale retitle"

    monkeypatch.setattr(titles, "_generate_title_from_context", generate)
    _turn(db, 1)
    assert db.get_session(SID)["title"] == "Generated meanwhile"
