"""A chat with no project works in the default folder, often the home
directory. A deck the agent builds there must reach the person in their
own App: ``send_file`` accepts it, the reply's path is a link, and turn end
attaches it. Dot-folders and secrets in that folder stay refused on every
surface, and a conversation that delivers to a chat platform keeps the
narrow roots, so the home folder is never readable out to WeChat.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from openprogram.state import attachments as att
from openprogram.state import attachment_markers as lexicon
from openprogram.state.store.session.files.session_log import SessionLog


SID = "local_s1"


@pytest.fixture
def home(tmp_path, monkeypatch: pytest.MonkeyPatch) -> Path:
    state = tmp_path / "home" / ".openprogram"
    home = tmp_path / "home"
    repo = state / "projects" / "default" / "chats" / SID
    repo.mkdir(parents=True)
    SessionLog(repo).write_meta({"id": SID, "project_id": "default"})
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: state)
    monkeypatch.setattr(
        "openprogram.state.store.session.session_store.shared._default_store", None)
    from openprogram.state.store.project import project_store
    default = SimpleNamespace(id="default", path=str(home), is_default=True)
    monkeypatch.setattr(project_store, "project_for_session", lambda _sid: default)
    monkeypatch.setattr(project_store, "get_default_project", lambda: default)
    monkeypatch.setattr(
        "openprogram.runtime.execution.run_control.get_current_session_id", lambda: SID)
    monkeypatch.setattr(
        "openprogram.programs.tools.interaction.send_file._bases", lambda _sid: [home])
    return home


def _write(path: Path, data: bytes = b"PK\x03\x04deck") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _secrets(home: Path) -> list[Path]:
    return [
        _write(home / ".ssh" / "id_x", b"KEY"),
        _write(home / ".ssh" / "deck.pptx"),
        _write(home / "credentials.json", b"{}"),
        _write(home / "id_ed25519", b"KEY"),
        _write(home / "server.key", b"KEY"),
        _write(home / ".openprogram-dev" / "deck.pptx"),
        _write(home / ".openprogram" / "auth.pdf"),
    ]


def _req(**kw):
    return SimpleNamespace(**{"session_id": SID, "source": "web",
                              "authority_tier": "owner", **kw})


def test_a_deck_built_in_the_home_folder_can_be_sent_to_its_own_chat(home):
    from openprogram.programs.tools.interaction import send_file as sf
    deck = _write(home / "op-verify-deck.pptx")
    out = sf._send_file_impl(str(deck))
    assert out.startswith("Attached op-verify-deck.pptx"), out


def test_turn_end_attaches_a_deck_built_in_the_home_folder(home):
    from openprogram.programs.tools.interaction import send_file as sf
    deck = _write(home / "op-verify-deck.pptx")
    script = _write(home / "build.py", b"print(1)")
    text, paths = sf.turn_attachments(
        f"Created [build.py]({script}) and [deck]({deck}).", [], _req(), time.time() - 30)
    assert [p for _m, _n, p in lexicon.find_markers(text)] == [str(deck.resolve())]
    assert paths == [str(deck.resolve())]


def test_file_paths_resolves_a_home_file_for_its_own_chat_only(home):
    from openprogram.interfaces.server.routes.files.file_search import _existing_files
    deck = _write(home / "op-verify-deck.pptx")
    assert _existing_files([str(deck)], SID) == {str(deck): str(deck.resolve())}
    # A chat that does not exist gets no working folder.
    assert _existing_files([str(deck)], "local_other") == {str(deck): None}


def test_dot_folders_secrets_and_the_state_dir_stay_refused_everywhere(home):
    from openprogram.interfaces.server.routes.files.file_search import _existing_files
    from openprogram.programs.tools.interaction import send_file as sf
    for path in _secrets(home):
        assert att.resolve_within(path, att.readable_roots(SID)) is None, path
        assert _existing_files([str(path)], SID) == {str(path): None}, path
        assert sf._send_file_impl(str(path)).startswith("Error:"), path
    text, paths = sf.turn_attachments(
        " ".join(str(p) for p in _secrets(home)), [], _req(), time.time() - 30)
    assert lexicon.find_markers(text) == [] and paths == []


def test_a_symlink_from_the_home_folder_into_ssh_is_refused(home):
    from openprogram.programs.tools.interaction import send_file as sf
    key = _write(home / ".ssh" / "id_x", b"KEY")
    link = home / "notes.pdf"
    link.symlink_to(key)
    assert sf._send_file_impl(str(link)).startswith("Error:")


def test_a_conversation_that_delivers_to_a_chat_platform_keeps_the_narrow_roots(home):
    from openprogram.programs.tools.interaction import send_file as sf
    repo = home / ".openprogram" / "projects" / "default" / "chats" / SID
    SessionLog(repo).write_meta({"id": SID, "project_id": "default", "channel": "wechat"})
    deck = _write(home / "op-verify-deck.pptx")
    assert "outside the directories" in sf._send_file_impl(str(deck))
    call = {"tool": "send_file", "input": json.dumps({"path": str(deck)}),
            "result": "Attached op-verify-deck.pptx (12 bytes).", "is_error": False}
    text, paths = sf.turn_attachments(f"Deck: {deck}", [call], _req(), time.time() - 30)
    assert lexicon.find_markers(text) == [] and paths == []
    # Shown in the owner's own App, never sent out.
    assert att.resolve_within(deck, att.readable_roots(SID)) == deck.resolve()


def test_a_platform_contacts_conversation_stays_narrow_after_its_binding_is_cleared(home):
    # Unbinding (or evicting) clears ``channel`` but the contact's next
    # message is routed back here and its reply goes to them.
    from openprogram.programs.tools.interaction import send_file as sf
    repo = home / ".openprogram" / "projects" / "default" / "chats" / SID
    SessionLog(repo).write_meta({"id": SID, "project_id": "default", "channel": "",
                                 "source": "wechat", "peer_id": "wxid_stranger"})
    deck = _write(home / "op-verify-deck.pptx")
    assert "outside the directories" in sf._send_file_impl(str(deck))


def test_app_data_folders_stay_refused(home):
    from openprogram.programs.tools.interaction import send_file as sf
    for path in (_write(home / "Library" / "Keychains" / "login.keychain-db", b"K"),
                 _write(home / "Library" / "Application Support" / "Google" / "Chrome"
                        / "Default" / "Cookies", b"C"),
                 _write(home / "AppData" / "Local" / "deck.pptx")):
        assert att.resolve_within(path, att.readable_roots(SID)) is None, path
        assert sf._send_file_impl(str(path)).startswith("Error:"), path


def test_a_default_folder_at_the_filesystem_root_is_no_root(home, monkeypatch):
    from openprogram.state.store.project import project_store
    root = SimpleNamespace(id="default", path=Path(home).anchor, is_default=True)
    monkeypatch.setattr(project_store, "project_for_session", lambda _sid: root)
    assert not any(isinstance(r, att.WorkdirRoot) for r in att.readable_roots(SID))
    assert not any(isinstance(r, att.WorkdirRoot) for r in att.sendable_roots(SID))
