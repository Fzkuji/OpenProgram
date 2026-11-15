"""A direct edit of a running version: saved, not applied, reported once."""
import importlib.util
import json
import os
import subprocess

import pytest

from openprogram.operations.self_update.versions import layout as paths
from openprogram.operations.self_update.versions import store, unapplied
from tests.support.sealed_versions import BASE, REPOSITORY, make_layout, make_version, point_at


def _load(name):
    spec = importlib.util.spec_from_file_location(
        f"bootstrap_capture_{name}", REPOSITORY / "scripts/release/bootstrap" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


restore_tool = _load("restore_version")
versions_tool = _load("versions")


@pytest.fixture
def layout(tmp_path):
    return make_layout(tmp_path / "state" / "runtime", tools=True)


def edit_and_capture(layout, version="v1", *, files=1):
    directory = layout.version(version) if layout.version(version).is_dir() else make_version(layout, version)
    site = paths.site_packages(directory, BASE) / "openprogram"
    (site / "module.py").write_text("value = 'edited'\n")
    if files > 1:
        (site / "added.py").write_text("new = True\n")
    if files > 2:
        (directory / "desktop" / "main.js").unlink()
    return restore_tool.capture(layout.root, version)


class Teller:
    def __init__(self, answer=True):
        self.answer = answer
        self.said = []

    def __call__(self, key, text):
        self.said.append((key, text))
        return self.answer


def test_the_owner_is_told_once(layout):
    edit_and_capture(layout, files=3)
    teller = Teller()
    assert unapplied.report_pending(layout, teller) == 3
    key, text = teller.said[0]
    assert text.startswith("3 files were changed directly in the running version. They are saved and not applied. The version started clean.")
    assert "changes.patch" in text and "git apply" in text
    assert key in {path.name for path in (layout.root / "unapplied").iterdir()}
    assert unapplied.report_pending(layout, teller) == 0
    assert len(teller.said) == 1
    assert json.loads(next((layout.root / "unapplied").glob("*/manifest.json")).read_text())["reported"] is True


def test_one_file_reads_as_one_file(layout):
    edit_and_capture(layout)
    teller = Teller()
    unapplied.report_pending(layout, teller)
    assert teller.said[0][1].startswith("1 file was changed directly in the running version. It is saved and not applied.")


def test_with_no_conversation_it_stays_to_be_told(layout):
    edit_and_capture(layout)
    teller = Teller(answer=False)
    assert unapplied.report_pending(layout, teller) == 0
    assert len(unapplied.pending(layout)) == 1
    later = Teller()
    assert unapplied.report_pending(layout, later) == 1
    assert unapplied.pending(layout) == []


def test_several_captures_are_one_message(layout):
    edit_and_capture(layout, files=1)
    edit_and_capture(layout, files=2)
    teller = Teller()
    assert unapplied.report_pending(layout, teller) == 3
    assert len(teller.said) == 1 and teller.said[0][1].count("changes.patch") == 2


def test_a_capture_that_recorded_no_files_says_nothing(layout):
    entry = layout.root / "unapplied" / "20261101T000000000000Z"
    entry.mkdir(parents=True)
    (entry / "manifest.json").write_text(json.dumps(
        {"version": "v1", "changed": [], "added": [], "removed": [], "reported": False}))
    teller = Teller()
    assert unapplied.report_pending(layout, teller) == 0
    assert teller.said == [] and unapplied.pending(layout) == []


def test_a_damaged_capture_is_skipped(layout):
    (layout.root / "unapplied" / "broken").mkdir(parents=True)
    (layout.root / "unapplied" / "broken" / "manifest.json").write_text("{")
    assert unapplied.all_captures(layout) == []


def test_the_saved_patch_applies_to_the_versions_commit(layout, tmp_path):
    environment = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "PATH": os.environ["PATH"]}
    work = tmp_path / "work"
    (work / "openprogram").mkdir(parents=True)
    (work / "apps" / "desktop").mkdir(parents=True)
    (work / "openprogram" / "module.py").write_text("value = 1\n")
    (work / "apps" / "desktop" / "main.js").write_text("exports.start = () => {};\n")
    subprocess.run(["git", "init", "-q", str(work)], check=True, env=environment)
    edit_and_capture(layout, files=3)
    capture = unapplied.all_captures(layout)[0]
    done = subprocess.run(
        ["git", "-C", str(work), "apply", str(capture.patch)],
        capture_output=True, text=True, env=environment,
    )
    assert done.returncode == 0, done.stderr
    assert (work / "openprogram" / "module.py").read_text() == "value = 'edited'\n"
    assert (work / "openprogram" / "added.py").read_text() == "new = True\n"
    assert not (work / "apps" / "desktop" / "main.js").exists()


def test_the_version_is_clean_after_the_capture(layout):
    edit_and_capture(layout, files=3)
    assert store.changed_files(layout.version("v1"), BASE) == []


def test_versions_list_shows_saved_edits(layout, capsys):
    make_version(layout, "v1")
    point_at(layout, "v1")
    versions_tool.list_versions(layout.root)
    assert "Direct edits" not in capsys.readouterr().out
    edit_and_capture(layout, files=2)
    versions_tool.list_versions(layout.root)
    output = capsys.readouterr().out
    assert "Direct edits saved, not applied:" in output
    assert "2 files" in output and "changes.patch" in output


def _settle(monkeypatch, layout, *, healthy=True, environment=None):
    from openprogram.operations.worker import runner

    monkeypatch.setattr(paths, "current_runtime", lambda *_: paths.CurrentRuntime(layout, "v1"))
    monkeypatch.setattr(runner, "_answers_healthz", lambda _port: healthy)
    monkeypatch.setattr(runner, "_HEALTHY_START_POLLS", 1)
    monkeypatch.setattr(runner.time, "sleep", lambda _seconds: None)
    if environment:
        monkeypatch.setenv(*environment)
    else:
        monkeypatch.delenv("OPENPROGRAM_TRIAL", raising=False)
    told = Teller()
    from openprogram.operations.self_update.delivery import owner_notice

    monkeypatch.setattr(owner_notice, "tell", told)
    runner._settle_managed_version(18100)
    return told


def test_a_healthy_start_reports_the_edits(layout, monkeypatch):
    edit_and_capture(layout)
    point_at(layout, "v1")
    told = _settle(monkeypatch, layout)
    assert len(told.said) == 1 and unapplied.pending(layout) == []


def test_a_start_that_never_answered_reports_nothing(layout, monkeypatch):
    edit_and_capture(layout)
    point_at(layout, "v1")
    assert _settle(monkeypatch, layout, healthy=False).said == []
    assert len(unapplied.pending(layout)) == 1


def test_a_trial_reports_nothing(layout, monkeypatch):
    edit_and_capture(layout)
    point_at(layout, "v1")
    assert _settle(monkeypatch, layout, environment=("OPENPROGRAM_TRIAL", "1")).said == []


def _session_with_reply(db, session_id):
    from openprogram.state.store import SessionNodeWriter
    from openprogram.state.store.session.nodes import Call

    db.create_session(session_id, "main", source="web")
    writer = SessionNodeWriter(db, session_id)
    writer.append(Call(id=f"{session_id}_user", role="user", name="user", created_at=1.0, output="hi"))
    writer.append(Call(
        id=f"{session_id}_reply", role="llm", name="llm", created_at=2.0, output="hello",
        predecessor=f"{session_id}_user",
    ))


def test_the_notice_lands_once_in_the_newest_conversation(tmp_path, monkeypatch):
    from openprogram.operations.self_update.delivery import owner_notice
    from openprogram.runtime import context as _context  # noqa: F401
    from openprogram.runtime.context import session_db
    from openprogram.state.store import SessionStore

    db = SessionStore(tmp_path / "sessions")
    monkeypatch.setattr(session_db, "default_db", lambda: db)
    assert owner_notice.tell("n1", "nothing to attach to") is False
    db.create_session("channel", "main", source="wechat")
    assert owner_notice.tell("n1", "nothing to attach to") is False
    _session_with_reply(db, "chat")
    assert owner_notice.tell("n1", "Saved and not applied.") is True
    assert owner_notice.tell("n1", "Saved and not applied.") is True
    _, index = db._open("chat")
    notices = [node for node in index.nodes_by_id.values() if node.name == owner_notice.SOURCE]
    assert len(notices) == 1
    assert notices[0].predecessor == "chat_reply" and notices[0].caller == "chat_reply"
    assert json.loads(notices[0].output) == {"notice": "Saved and not applied."}
    assert index.head_id == "chat_reply"


def test_a_built_in_application_is_not_hot_loaded_inside_a_version(tmp_path, monkeypatch):
    from openprogram.programs._host.ui import catalog, loader

    source = tmp_path / "application"
    source.mkdir()
    row = {"kind": "application", "application": {"id": "builtin_app", "enabled": True}, "source": str(source)}
    monkeypatch.setattr(catalog.catalog_store, "_read", lambda: [row])
    monkeypatch.setattr(catalog, "source_root", lambda _row: source)
    monkeypatch.setattr(catalog, "tree_stamp", lambda _root: ("stamped",))
    monkeypatch.setattr(
        "openprogram.programs._host.registry._builtin_applications_root", lambda: str(source.parent),
    )
    monkeypatch.setattr(paths, "managed", lambda: False)
    assert any(item[0] == "builtin_app" for item in loader.watch_stamp() if isinstance(item, tuple) and item)
    monkeypatch.setattr(paths, "managed", lambda: True)
    assert not any(item and item[0] == "builtin_app" for item in loader.watch_stamp()[1:])
