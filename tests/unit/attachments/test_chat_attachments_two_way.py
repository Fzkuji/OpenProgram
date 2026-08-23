"""Attachments in both directions: one marker lexicon, one path policy.

Covers the four seams the two-way flow rests on:

* ``openprogram.attachments`` — the marker every producer writes and the
  root check every reader/sender goes through (symlinks resolved first).
* ``ws_actions.chat`` — a browser IMAGE now lands on disk and gets a
  marker, on top of still reaching the model as an image block.
* ``functions.tools.send_file`` — what the agent may hand back, and the
  spelled-out refusal when it names something out of bounds.
* ``channels._conversation`` — the marker becomes a real platform upload
  and stops being text on the wire.
"""
from __future__ import annotations

import base64
import json
import re
import stat
from pathlib import Path

import pytest

from openprogram import attachments as att


@pytest.fixture
def state(tmp_path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An isolated state dir, with the sessions root materialised.

    The SessionStore singleton is reset around the test: persisting an
    attachment resolves the session workdir, which builds that singleton
    on first use. Built while this fixture's state dir is patched in, it
    would outlive the test and hand a tmp_path root to whatever runs
    next (``test_state_isolation`` catches exactly that).
    """
    root = tmp_path / "state"
    (root / "sessions").mkdir(parents=True)
    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: root)
    monkeypatch.setattr(
        "openprogram.store.session.session_store._default_store", None)
    return root


# ---------------------------------------------------------------------------
# marker lexicon
# ---------------------------------------------------------------------------

def test_marker_round_trips():
    marker = att.format_marker("shot.png", "/tmp/a/shot.png", 20481)
    assert marker == '[attachment: shot.png (png, 20 KB) @json "/tmp/a/shot.png"]'
    assert att.find_markers(f"hi\n{marker}") == [
        (marker, "shot.png", "/tmp/a/shot.png")]


def test_marker_uses_mime_when_the_name_has_no_extension():
    assert "(jpeg, 1 KB)" in att.format_marker("photo", "/p", 10, mime="image/jpeg")


def test_marker_keeps_the_count_badge_inside_the_parens():
    marker = att.format_marker("spec.pdf", "/p/spec.pdf", 2048, count="500 pages")
    assert marker == ('[attachment: spec.pdf (pdf, 2 KB, 500 pages) '
                      '@json "/p/spec.pdf"]')
    assert att.find_markers(marker)[0][2] == "/p/spec.pdf"


def test_marker_keeps_source_path_and_immutable_preview_path():
    marker = att.format_marker(
        "spec.pdf",
        "/Users/test/spec.pdf",
        2048,
        preview_path="/state/sessions/s1/workdir/attachments/spec.pdf",
    )
    match = att.JSON_MENTION_RE.fullmatch(marker)
    assert match is not None
    assert json.loads(match.group(5)) == "/Users/test/spec.pdf"
    assert json.loads(match.group(6)) == (
        "/state/sessions/s1/workdir/attachments/spec.pdf"
    )
    assert att.find_markers(marker)[0][2] == "/Users/test/spec.pdf"


def test_marker_neutralises_brackets_in_a_filename():
    """A ']' in the name would truncate the marker and leak the tail as
    prose — the exact class of bug this lexicon exists to prevent."""
    marker = att.format_marker("we[ird](1).png", "/p/x.png", 100)
    assert att.find_markers(marker)[0][2] == "/p/x.png"


def test_marker_round_trips_path_delimiters_quotes_and_trailing_space():
    path = '/Users/test/dir]name/report "final".pdf '
    marker = att.format_marker("report (final).pdf", path, 1024)
    assert att.find_markers(marker) == [
        (marker, "report _final_.pdf", path),
    ]
    assert att.strip_markers(f"{marker}\n\nexplain").strip() == "explain"
    from openprogram.webui.ws_actions import chat
    assert chat._title_from_text(f"{marker}\n\nexplain") == "explain"


def test_marker_matches_the_regex_the_web_chip_actually_ships():
    """The chip parser is a separate implementation in TypeScript. Read
    its regex out of the shipped source and run the produced marker
    through it, so the two can't drift apart unnoticed."""
    src = (Path(__file__).resolve().parents[3]
           / "apps/web/lib/attachment-marker.ts").read_text()
    body = re.search(r"const JSON_ATTACHED_MENTION =\s*\n\s*/(.+)/g;", src).group(1)
    ts_re = re.compile(body.replace("(?:", "(?:"))
    marker = att.format_marker("a.png", "/tmp/a.png", 20481)
    m = ts_re.search(marker)
    assert m is not None
    assert m.group(1) == "a.png"


# ---------------------------------------------------------------------------
# path policy
# ---------------------------------------------------------------------------

def test_readable_roots_cover_sessions_and_channel_attachments(state):
    adir = state / "channels" / "telegram" / "accounts" / "default" / "attachments"
    adir.mkdir(parents=True)
    roots = att.readable_roots()
    assert (state / "sessions").resolve() in roots
    assert adir.resolve() in roots


def test_channel_credentials_are_not_a_readable_root(state):
    """``credentials.json`` is the attachments dir's SIBLING — the account
    directory itself must never become a served root."""
    acct = state / "channels" / "telegram" / "accounts" / "default"
    (acct / "attachments").mkdir(parents=True)
    (acct / "credentials.json").write_text('{"bot_token": "secret"}')
    assert att.resolve_within(acct / "credentials.json",
                              att.readable_roots()) is None


def test_sendable_roots_exclude_channel_dirs_and_the_checkout(state):
    (state / "channels" / "telegram" / "accounts" / "d" / "attachments").mkdir(
        parents=True)
    roots = att.sendable_roots()
    assert roots == [(state / "sessions").resolve()]


def test_resolve_within_accepts_a_path_inside_a_root(state):
    f = state / "sessions" / "s1" / "workdir" / "out.txt"
    f.parent.mkdir(parents=True)
    f.write_text("x")
    assert att.resolve_within(f, att.sendable_roots()) == f.resolve()


def test_resolve_within_rejects_a_symlink_escaping_the_root(state, tmp_path):
    """The check that matters: a link PLANTED INSIDE an allowed root
    pointing outside it. Testing containment on the name would pass;
    resolving first is what makes it fail."""
    secret = tmp_path / "outside" / "id_rsa"
    secret.parent.mkdir(parents=True)
    secret.write_text("PRIVATE KEY")
    wd = state / "sessions" / "s1" / "workdir"
    wd.mkdir(parents=True)
    link = wd / "innocent.txt"
    link.symlink_to(secret)
    assert link.is_file()                       # the link itself resolves
    assert att.resolve_within(link, att.sendable_roots()) is None


def test_resolve_within_rejects_dotdot(state):
    assert att.resolve_within(
        state / "sessions" / ".." / "config.json",
        att.sendable_roots()) is None


# ---------------------------------------------------------------------------
# inbound: a browser image lands on disk
# ---------------------------------------------------------------------------

# 1x1 PNG.
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmM"
    "IQAAAABJRU5ErkJggg==")


def test_image_attachment_is_written_and_gets_a_marker(state):
    from openprogram.webui.ws_actions import chat
    out = chat._persist_attachments("sess1", [{
        "type": "image",
        "data": base64.b64encode(_PNG).decode(),
        "media_type": "image/png",
    }], "what is this")
    marker, name, path = att.find_markers(out)[0]
    assert name.endswith(".png")
    saved = Path(path)
    assert saved.read_bytes() == _PNG
    assert saved.parent == state / "sessions" / "sess1" / "workdir" / "attachments"
    assert out.startswith("what is this")


def test_unnamed_image_keeps_an_extension(state):
    """A pasted screenshot has bytes and a media type but no filename.
    Calling it "file" would cost it the extension the chip and the raw
    endpoint both dispatch on."""
    from openprogram.webui.ws_actions import chat
    assert chat._attachment_name({"media_type": "image/jpeg"}, 1) == "image-1.jpg"
    assert chat._attachment_name({"media_type": "image/png"}, 2) == "image-2.png"
    assert chat._attachment_name({"filename": "given.png"}, 3) == "given.png"


def test_document_mention_is_still_rewritten_in_place(state):
    """Docs arrive with a path-less mention the composer wrote; it gets
    the path spliced in, not a second marker appended."""
    from openprogram.webui.ws_actions import chat
    text = "[attachment: notes.txt (txt, 1 KB)]\n\nread this"
    out = chat._persist_attachments("sess2", [{
        "type": "document",
        "data": base64.b64encode(b"hello\nworld\n").decode(),
        "media_type": "text/plain",
        "filename": "notes.txt",
    }], text)
    assert len(att.find_markers(out)) == 1
    assert "2 lines" in out


def test_original_local_path_marker_is_not_duplicated(state):
    """Desktop uploads keep their source path and an immutable preview copy."""
    from openprogram.webui.ws_actions import chat
    original = "/Users/test/Research Files/notes.txt"
    text = att.format_marker("notes.txt", original, 1024, mime="text/plain")
    text = f"{text}\n\nread this"
    out = chat._persist_attachments("sess-local-path", [{
        "type": "document",
        "data": base64.b64encode(b"hello\nworld\n").decode(),
        "media_type": "text/plain",
        "filename": "notes.txt",
        "source_path": original,
    }], text)
    markers = att.find_markers(out)
    assert len(markers) == 1
    assert markers[0][2] == original
    match = att.JSON_MENTION_RE.search(out)
    assert match is not None and match.group(6)
    preview = Path(json.loads(match.group(6)))
    assert preview.read_bytes() == b"hello\nworld\n"
    assert preview.parent == (
        state / "sessions" / "sess-local-path" / "workdir" / "attachments"
    )
    assert Path.home().resolve() not in att.readable_roots("sess-local-path")


def test_same_source_path_versions_keep_distinct_preview_copies(state):
    from openprogram.webui.ws_actions import chat

    original = "/Users/test/notes.txt"
    previews = []
    for body in (b"first version\n", b"second version\n"):
        out = chat._persist_attachments("sess-versions", [{
            "type": "document",
            "data": base64.b64encode(body).decode(),
            "media_type": "text/plain",
            "filename": "notes.txt",
            "source_path": original,
        }], att.format_marker("notes.txt", original, len(body)))
        match = att.JSON_MENTION_RE.search(out)
        assert match is not None and match.group(6)
        previews.append(Path(json.loads(match.group(6))))
    assert previews[0] != previews[1]
    assert previews[0].read_bytes() == b"first version\n"
    assert previews[1].read_bytes() == b"second version\n"


def test_duplicate_source_path_in_one_turn_updates_each_marker_once(state):
    from openprogram.webui.ws_actions import chat

    original = "/Users/test/notes.txt"
    marker = att.format_marker("notes.txt", original, 1024)
    out = chat._persist_attachments("sess-duplicate-source", [{
        "type": "document",
        "data": base64.b64encode(body).decode(),
        "media_type": "text/plain",
        "filename": "notes.txt",
        "source_path": original,
    } for body in (b"first copy\n", b"second copy\n")], f"{marker}\n{marker}")
    matches = list(att.JSON_MENTION_RE.finditer(out))
    assert len(matches) == 2
    previews = [Path(json.loads(match.group(6))) for match in matches]
    assert previews[0] != previews[1]
    assert previews[0].read_bytes() == b"first copy\n"
    assert previews[1].read_bytes() == b"second copy\n"


def test_preview_token_text_inside_source_path_is_not_a_preview_field(state):
    from openprogram.webui.ws_actions import chat

    original = "/Users/test/@previewjson/notes.txt"
    body = b"content\n"
    out = chat._persist_attachments("sess-preview-text", [{
        "type": "document",
        "data": base64.b64encode(body).decode(),
        "media_type": "text/plain",
        "filename": "notes.txt",
        "source_path": original,
    }], att.format_marker("notes.txt", original, len(body)))
    matches = list(att.JSON_MENTION_RE.finditer(out))
    assert len(matches) == 1
    assert json.loads(matches[0].group(5)) == original
    assert Path(json.loads(matches[0].group(6))).read_bytes() == body


@pytest.mark.parametrize("reserved", [".opdedup.json", ".opsourcepaths.json"])
def test_reserved_attachment_names_do_not_overwrite_internal_indexes(
    state, reserved,
):
    from openprogram.webui.ws_actions import chat

    body = b"user payload\n"
    out = chat._persist_attachments("sess-reserved", [{
        "type": "document",
        "data": base64.b64encode(body).decode(),
        "media_type": "application/json",
        "filename": reserved,
    }], f"[attachment: {reserved} (json, 1 KB)]")
    saved = Path(att.find_markers(out)[0][2])
    assert saved.name.startswith("attachment-")
    assert saved.read_bytes() == body
    index = saved.parent / ".opdedup.json"
    assert isinstance(json.loads(index.read_text()), dict)
    assert stat.S_IMODE(index.stat().st_mode) == 0o600


def test_same_name_browser_documents_each_get_a_saved_path(state):
    from openprogram.webui.ws_actions import chat
    text = ("[attachment: notes.txt (txt, 1 KB)]\n"
            "[attachment: notes.txt (txt, 1 KB)]")
    incoming = [{
        "type": "document",
        "data": base64.b64encode(body).decode(),
        "media_type": "text/plain",
        "filename": "notes.txt",
    } for body in (b"first\n", b"second\n")]
    out = chat._persist_attachments("sess-browser-docs", incoming, text)
    markers = att.find_markers(out)
    assert len(markers) == 2
    assert all(Path(marker[2]).is_file() for marker in markers)


def test_same_name_browser_images_each_get_a_saved_path(state):
    from openprogram.webui.ws_actions import chat
    incoming = [{
        "type": "image",
        "data": base64.b64encode(_PNG + suffix).decode(),
        "media_type": "image/png",
        "filename": "photo.png",
    } for suffix in (b"a", b"b")]
    out = chat._persist_attachments("sess-browser-images", incoming, "compare")
    markers = att.find_markers(out)
    assert len(markers) == 2
    assert len({marker[2] for marker in markers}) == 2


def test_local_and_browser_same_name_keep_both_paths(state):
    from openprogram.webui.ws_actions import chat
    original = "/Users/test/notes.txt"
    text = (f"[attachment: notes.txt (txt, 1 KB) @ {original}]\n"
            "[attachment: notes.txt (txt, 1 KB)]")
    incoming = [{
        "type": "document",
        "data": base64.b64encode(b"local\n").decode(),
        "media_type": "text/plain",
        "filename": "notes.txt",
        "source_path": original,
    }, {
        "type": "document",
        "data": base64.b64encode(b"browser\n").decode(),
        "media_type": "text/plain",
        "filename": "notes.txt",
    }]
    out = chat._persist_attachments("sess-mixed-docs", incoming, text)
    markers = att.find_markers(out)
    assert len(markers) == 2
    assert markers[0][2] == original
    assert Path(markers[1][2]).is_file()


def test_source_path_metadata_is_removed_before_provider_dispatch():
    """The message marker still sends the path; only duplicate attachment
    object metadata is removed before the provider call."""
    from openprogram.webui.ws_actions import chat
    image = {
        "type": "image",
        "data": "png-data",
        "media_type": "image/png",
        "filename": "photo.png",
        "source_path": "/Users/test/photo.png",
    }
    assert chat._attachments_for_dispatch([image]) == [{
        "type": "image",
        "data": "png-data",
        "media_type": "image/png",
        "filename": "photo.png",
    }]
    assert chat._attachments_for_dispatch([{
        "type": "document", "data": "doc-data", "source_path": "/tmp/doc",
    }]) is None


# ---------------------------------------------------------------------------
# outbound: the send_file tool
# ---------------------------------------------------------------------------

@pytest.fixture
def in_session(state, monkeypatch: pytest.MonkeyPatch):
    """A turn context whose session workdir exists."""
    from openprogram.programs.tools.interaction import send_file as sf
    monkeypatch.setattr(
        "openprogram.agent.run_control.get_current_session_id", lambda: "s1")
    wd = state / "sessions" / "s1" / "workdir"
    wd.mkdir(parents=True)
    sf.begin_turn()
    return wd


def _send(path) -> str:
    from openprogram.programs.tools.interaction import send_file as sf
    return sf._send_file_impl(str(path))


def test_send_file_registers_a_file_in_the_workdir(in_session):
    from openprogram.programs.tools.interaction import send_file as sf
    chart = in_session / "chart.png"
    chart.write_bytes(_PNG)
    assert "Attached chart.png" in _send(chart)
    entries = sf.drain()
    assert entries == [{"path": str(chart), "name": "chart.png",
                        "size": len(_PNG)}]
    marker = sf.markers_for(entries)
    assert att.find_markers(marker)[0][2] == str(chart)
    assert sf.drain() == []                      # drained, not duplicated


def test_send_file_refuses_a_path_outside_the_roots(in_session, tmp_path):
    from openprogram.programs.tools.interaction import send_file as sf
    outside = tmp_path / "elsewhere.txt"
    outside.write_text("nope")
    msg = _send(outside)
    assert msg.startswith("Error:")
    assert "outside the directories" in msg
    assert str(in_session.parent) in msg or "sessions" in msg   # names the roots
    assert sf.drain() == []


def test_send_file_refuses_a_symlink_out_of_the_roots(in_session, tmp_path):
    from openprogram.programs.tools.interaction import send_file as sf
    secret = tmp_path / "id_rsa"
    secret.write_text("PRIVATE KEY")
    link = in_session / "chart.png"
    link.symlink_to(secret)
    assert _send(link).startswith("Error:")
    assert sf.drain() == []


def test_send_file_reports_a_missing_file(in_session):
    assert "no such file" in _send(in_session / "ghost.png")


def test_send_file_is_idempotent_within_a_turn(in_session):
    from openprogram.programs.tools.interaction import send_file as sf
    f = in_session / "a.txt"
    f.write_text("x")
    _send(f)
    assert "Already attached" in _send(f)
    assert len(sf.drain()) == 1


def test_send_file_refuses_an_oversize_file(in_session, monkeypatch):
    from openprogram.programs.tools.interaction import send_file as sf
    monkeypatch.setattr(sf, "MAX_SEND_BYTES", 4)
    f = in_session / "big.bin"
    f.write_bytes(b"12345")
    assert "over the" in _send(f)
    assert sf.drain() == []


def test_send_file_is_hidden_where_there_is_no_attachment_channel():
    from openprogram.programs import _runtime
    for source in ("cli", "tui", "wechat"):
        names = [t.name for t in _runtime.filter_for(names=["send_file"],
                                                     source=source)]
        assert names == [], source
    for source in ("web", "telegram", "discord", "slack"):
        names = [t.name for t in _runtime.filter_for(names=["send_file"],
                                                     source=source)]
        assert names == ["send_file"], source


# ---------------------------------------------------------------------------
# outbound: the channel delivery step
# ---------------------------------------------------------------------------

def test_channel_uploads_the_file_and_drops_the_marker(monkeypatch):
    from openprogram.channels import _conversation, _transport
    calls = []
    monkeypatch.setattr(_transport, "post_file",
                        lambda ch, acct, target, path, caption="":
                        calls.append((ch, target, path))
                        or _transport.SendResult.success("m1"))
    reply = ("Here is the chart.\n\n"
             + att.format_marker("chart.png", "/w/chart.png", 2048))
    out = _conversation._deliver_outbound_files(
        "telegram", "default", "42", reply)
    assert calls == [("telegram", "42", "/w/chart.png")]
    assert out == "Here is the chart."


def test_channel_without_file_upload_says_so_in_words(monkeypatch):
    """WeChat's iLink has no upload API. Leaking the raw marker to the
    user is the failure this whole lexicon exists to avoid, and silently
    dropping it is worse — rewrite it into a sentence."""
    from openprogram.channels import _conversation, _transport
    monkeypatch.setattr(
        _transport, "post_file",
        lambda *a, **k: _transport.SendResult.fail("not_supported", "no api"))
    reply = "Done.\n\n" + att.format_marker("chart.png", "/w/chart.png", 2048)
    out = _conversation._deliver_outbound_files("wechat", "default", "u", reply)
    assert "[attachment:" not in out
    assert "chart.png" in out and "/w/chart.png" in out


def test_channel_text_without_markers_is_untouched():
    from openprogram.channels import _conversation
    reply = "just words, and a [bracket] for good measure"
    assert _conversation._deliver_outbound_files("t", "d", "1", reply) == reply


# ---------------------------------------------------------------------------
# outbound: the dispatcher folds registered files into the reply text
# ---------------------------------------------------------------------------

def test_dispatcher_appends_the_marker_to_the_reply(state, tmp_path,
                                                    monkeypatch):
    """``send_file`` registers; the dispatcher is what puts the marker on
    the one string every consumer reads (stored message, streamed result,
    TurnResult) — so the web chip and the channel upload both see it."""
    from unittest.mock import patch
    from openprogram.agent import dispatcher as D
    from openprogram.agent.session_db import SessionDB
    from openprogram.programs.tools.interaction import send_file as sf

    db = SessionDB(tmp_path / "sessions-git")
    monkeypatch.setattr("openprogram.agent.session_db.default_db", lambda: db)
    monkeypatch.setattr("openprogram.store.session.session_store.default_store",
                        lambda: db)
    monkeypatch.setattr("openprogram.store.default_store", lambda: db)

    chart = state / "sessions" / "s1" / "workdir" / "chart.png"
    chart.parent.mkdir(parents=True)
    chart.write_bytes(_PNG)

    def _stub(*, req, history, on_event, cancel_event, **_extra):
        # Called from inside the dispatcher's try block, exactly where a
        # real tool call runs.
        assert "Attached chart.png" in sf._send_file_impl(str(chart))
        return "Here is the chart.", {}, []

    with patch.object(D, "_run_loop_blocking", side_effect=_stub):
        result = D.process_user_turn(
            D.TurnRequest(session_id="s1", agent_id="main",
                          user_text="draw me a chart", source="web"),
            on_event=lambda _e: None,
        )

    assert result.final_text.startswith("Here is the chart.")
    assert att.find_markers(result.final_text)[0][2] == str(chart)
    stored = [m for m in db.get_messages("s1") if m.get("role") == "assistant"]
    assert att.find_markers(stored[-1]["content"])


def test_the_default_project_is_not_a_root(state, monkeypatch):
    """Every session is auto-bound to the DEFAULT project, whose path is
    the user's home directory. Honouring that binding would make $HOME
    sendable from every ad-hoc chat — found by running the real thing,
    not by reading the code."""
    class _Proj:
        id, path, is_default = "default", str(Path.home()), True

    monkeypatch.setattr(
        "openprogram.store.project.project_store.project_for_session",
        lambda sid: _Proj())
    assert att.sendable_roots("s1") == [(state / "sessions").resolve()]
    assert Path.home().resolve() not in att.readable_roots("s1")


def test_a_real_bound_project_is_a_root(state, tmp_path, monkeypatch):
    proj_dir = tmp_path / "work" / "myrepo"
    proj_dir.mkdir(parents=True)

    class _Proj:
        id, path, is_default = "p1", str(proj_dir), False

    monkeypatch.setattr(
        "openprogram.store.project.project_store.project_for_session",
        lambda sid: _Proj())
    assert proj_dir.resolve() in att.sendable_roots("s1")
