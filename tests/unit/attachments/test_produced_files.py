"""Files a turn produced are attached at turn end without a send_file call.

The case behind it: a model builds a deck with a python script and answers
"done, it is at exports/plan.pptx". Nothing recorded a send_file call, so the
reply had no card. ``produced_files`` attaches what the turn itself made,
and only that: a file the reply merely names, but which the turn did not
modify, never goes out.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from openprogram.state import attachments as att


@pytest.fixture
def wd(tmp_path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "state"
    workdir = root / "projects" / "default" / "chats" / "s1" / "workdir"
    workdir.mkdir(parents=True)
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: root)
    monkeypatch.setattr(
        "openprogram.state.store.session.session_store.shared._default_store", None)
    monkeypatch.setattr(
        "openprogram.programs.tools.interaction.send_file._bases", lambda _sid: [workdir])
    return workdir


def _make(path: Path, data: bytes = b"PK\x03\x04deck", *, age: float = 0.0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    if age:
        then = time.time() - age
        os.utime(path, (then, then))
    return path


def _attached(text: str) -> list[str]:
    return [p for _whole, _name, p in att.find_markers(text)]


def _produced(text, calls=None, since=None):
    from openprogram.programs.tools.interaction import send_file as sf
    started = time.time() - 30 if since is None else since
    return [e["path"] for e in sf.produced_files(text, calls, "s1", started)]


def test_a_deck_the_reply_names_is_attached_however_it_is_written(wd):
    deck = _make(wd / "exports" / "plan.pptx")
    spaced = _make(wd / "My Report.docx")
    sheet = _make(wd / "q3.xlsx")
    pdf = _make(wd / "out" / "summary.pdf")
    reply = (f"Done. The deck is at `{deck}:12`, the report is "
             f"[My Report](<{spaced}>), the sheet is [q3](q3.xlsx#L4) "
             f"and the PDF is out/summary.pdf.")
    assert _produced(reply) == [str(spaced), str(sheet), str(deck), str(pdf)]


def test_a_file_the_turn_did_not_modify_is_never_attached(wd):
    old = _make(wd / "input.pdf", age=3600)
    assert _produced(f"I read `{old}` and summarised it.") == []


def test_source_files_and_text_the_edit_card_shows_stay_out(wd):
    script = _make(wd / "build_deck.py", b"print()")
    notes = _make(wd / "notes.md", b"# notes")
    report = _make(wd / "report.html", b"<p>hi</p>")
    calls = [{"tool": "edit", "input": json.dumps({"file_path": str(notes)}),
              "result": "ok"}]
    reply = f"Ran `{script}`, updated `{notes}`, wrote `{report}`."
    assert _produced(reply, calls) == [str(report)]


def test_a_path_outside_the_sendable_roots_is_not_attached(wd, tmp_path):
    outside = _make(tmp_path / "elsewhere" / "deck.pptx")
    assert _produced(f"Saved to {outside}") == []


def test_uploads_keys_folders_and_escaping_links_never_go_out(wd, tmp_path):
    upload = _make(wd / "attachments" / "brief.pdf")      # the user's own upload
    steer = _make(wd.parent / "steer-images" / "ab12.png", b"\x89PNG")
    key = _make(wd / "server.key", b"-----BEGIN PRIVATE KEY-----")
    folder = wd / "bundle.pdf"
    folder.mkdir()
    secret = _make(tmp_path / "outside" / "secret.pdf")
    link = wd / "linked.pdf"
    link.symlink_to(secret)
    reply = " ".join(f"`{p}`" for p in (upload, steer, key, folder, link))
    assert _produced(reply) == []


def test_files_the_tools_saved_count_without_being_named(wd):
    image = _make(wd / "images" / "cat_1.png", b"\x89PNG")
    calls = [{"tool": "image_generate", "input": {"prompt": "cat"},
              "result": f"# image_generate (via x, 1 image)\noutput_dir: {image.parent}\n\n- {image}"}]
    assert _produced("Here is the cat.", calls) == [str(image)]


def test_a_script_the_write_tool_saved_to_build_the_deck_stays_out(wd):
    script = _make(wd / "build_deck.py", b"print()")
    log = _make(wd / "build.log", b"ok")
    page = _make(wd / "preview.html", b"<p>hi</p>")
    deck = _make(wd / "deck.pptx")
    calls = [{"tool": "write", "input": {"file_path": str(p), "content": "x"}, "result": "ok"}
             for p in (script, log, page)]
    calls.append({"tool": "image_generate", "input": {"prompt": "x"},
                  "result": f"- {wd / 'build.sh'}"})
    _make(wd / "build.sh", b"#!/bin/sh")
    # The script, the log and the generated non-image are never results; the
    # page is already shown by the write card.
    assert _produced(f"The deck is at `{deck}`.", calls) == [str(deck)]


def test_the_count_is_capped(wd):
    from openprogram.programs.tools.interaction import send_file as sf
    paths = [_make(wd / f"slide_{i}.png", b"\x89PNG") for i in range(sf.MAX_PRODUCED_FILES + 4)]
    reply = "\n".join(f"- {p}" for p in paths)
    assert len(_produced(reply)) == sf.MAX_PRODUCED_FILES


def test_a_failure_attaches_nothing_and_never_reaches_the_turn(wd, monkeypatch):
    from openprogram.programs.tools.interaction import send_file as sf
    _make(wd / "plan.pptx")

    def boom(*_a, **_k):
        raise RuntimeError("disk gone")
    monkeypatch.setattr(sf, "_produced_files", boom)
    assert sf.produced_files("plan.pptx", None, "s1", time.time() - 30) == []


def _req(**kw):
    base = {"session_id": "s1", "source": "web", "authority_tier": "owner"}
    return SimpleNamespace(**{**base, **kw})


def test_turn_end_adds_one_card_per_file_and_lets_the_channel_upload_it(wd):
    from openprogram.programs.tools.interaction import send_file as sf
    deck = _make(wd / "plan.pptx")
    chart = _make(wd / "chart.png", b"\x89PNG")
    sent = [{"tool": "send_file", "input": json.dumps({"path": str(chart)}),
             "result": "Attached chart.png (4 bytes).",
             "is_error": False}]
    reply = f"Deck: [plan.pptx]({deck}). Chart: `{chart}`."
    text, paths = sf.turn_attachments(reply, sent, _req(), time.time() - 30)
    assert _attached(text) == [str(chart), str(deck)]
    assert paths == [str(chart), str(deck)]
    again, _ = sf.turn_attachments(text, sent, _req(), time.time() - 30)
    assert again == text


@pytest.mark.parametrize("req", [
    _req(authority_tier="paired"),   # a chat-app speaker who may not read the owner's files
    _req(source="cli"),              # a terminal has no attachment channel
])
def test_produced_files_go_only_where_send_file_could_send_them(wd, req):
    from openprogram.programs.tools.interaction import send_file as sf
    deck = _make(wd / "plan.pptx")
    text, paths = sf.turn_attachments(f"Deck: {deck}", [], req, time.time() - 30)
    assert _attached(text) == [] and paths == []


def test_a_turn_with_no_start_time_attaches_only_what_send_file_sent(wd):
    from openprogram.programs.tools.interaction import send_file as sf
    deck = _make(wd / "plan.pptx")
    text, paths = sf.turn_attachments(f"Deck: {deck}", [], _req(), None)
    assert _attached(text) == [] and paths == []
