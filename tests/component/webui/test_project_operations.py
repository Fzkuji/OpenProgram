"""Project menu actions preserve files, ownership, and the source Git checkout."""
import asyncio
import json
import subprocess
import pytest
from openprogram.store.project import project_store as store
from openprogram.webui.ws_actions import project as actions
from test_project_metadata import WS


def invoke(action, project_id, **payload):
    ws = WS()
    asyncio.run(actions.ACTIONS[action](ws, {"action": action, "project_id": project_id, **payload}))
    return ws.sent[-1]["data"]


def test_hide_restore_preserves_files_and_bindings(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "_registry_path", lambda: tmp_path / "registry.json")
    source = tmp_path / "project"
    source.mkdir()
    (source / "file").write_text("keep")
    p = store.resolve_project(source)
    store.bind_session("chat", p.id)
    assert invoke("remove_project", p.id)["ok"]
    assert store.get_project(p.id).hidden
    assert store.project_for_session("chat").id == p.id
    assert (source / "file").read_text() == "keep"
    assert invoke("restore_project", p.id)["ok"]
    assert not store.get_project(p.id).hidden
    invoke("remove_project", p.id)
    assert not store.resolve_project(source).hidden
    assert not invoke("remove_project", store.get_default_project().id)["ok"]
    assert not invoke("remove_project", "missing")["ok"]


def test_archive_persists_only_project_sessions_and_reports_partial_failure(tmp_path, monkeypatch):
    from openprogram.agent import session_db
    monkeypatch.setattr(store, "_registry_path", lambda: tmp_path / "registry.json")
    p = store.resolve_project(tmp_path)
    for sid in ("a", "b", "gone"):
        store.bind_session(sid, p.id)
    rows = {sid:{"id":sid} for sid in ("a", "b", "outside")}
    class DB:
        fail = False
        def get_session(self, sid): return rows.get(sid)
        def update_session(self, sid, **fields):
            if self.fail and sid == "b": raise OSError("disk failure")
            rows[sid].update(fields)
    db=DB()
    monkeypatch.setattr(session_db, "default_db", lambda:db)
    db.fail=True
    result=invoke("archive_project_chats", p.id)
    assert not result["ok"] and result["session_ids"]==["a"]
    assert "archived" not in rows["b"]
    db.fail=False
    assert invoke("archive_project_chats", p.id)["ok"]
    assert rows["a"]["archived"] and rows["b"]["archived"]
    assert "archived" not in rows["outside"]


def test_worktree_real_git_and_retry(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "_registry_path", lambda:tmp_path / "registry.json")
    source=tmp_path / "source"; source.mkdir()
    def git(*args):
        return subprocess.run(["git", "-C", str(source), *args],check=True,capture_output=True,text=True).stdout.strip()
    git("init");git("-c","user.name=Test","-c","user.email=test@example.test","commit","--allow-empty","-m","initial")
    (source / "dirty").write_text("untouched")
    before=git("status","--porcelain")
    p=store.resolve_project(source)
    target=tmp_path / "new worktree"
    result=invoke("create_project_worktree",p.id,path=str(target),branch="codex/sidebar-test")
    assert result["ok"],result
    assert (target / ".git").is_file()
    assert not (target / "dirty").exists()
    assert git("status","--porcelain")==before
    again=invoke("create_project_worktree",p.id,path=str(target),branch="codex/sidebar-test")
    assert again["ok"] and again["project"]["id"]==result["project"]["id"]
    assert not invoke("create_project_worktree",p.id,path=str(target),branch="other")["ok"]
    for path,branch in ((str(source / "nested"),"nested"),("relative","relative"),(str(tmp_path / "invalid"),"--detach")):
        assert not invoke("create_project_worktree",p.id,path=path,branch=branch)["ok"]
    assert store.get_project(p.id).path==str(source)


def test_default_archive_includes_legacy_unbound_chats(tmp_path, monkeypatch):
    from openprogram.agent import session_db
    monkeypatch.setattr(store, "_registry_path", lambda:tmp_path / "registry.json")
    home=store.get_default_project()
    project=store.resolve_project(tmp_path)
    store.bind_session("other", project.id)
    rows={sid:{"id":sid} for sid in ("unbound", "other")}
    class DB:
        def list_sessions(self, **kw): return list(rows.values())
        def get_session(self, sid): return rows.get(sid)
        def update_session(self, sid, **fields): rows[sid].update(fields)
    monkeypatch.setattr(session_db, "default_db", lambda:DB())
    result=invoke("archive_project_chats", home.id)
    assert result["ok"] and result["session_ids"]==["unbound"]
    assert rows["unbound"]["archived"] and "archived" not in rows["other"]


def test_subfolder_project_worktree_cannot_modify_containing_checkout(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "_registry_path", lambda:tmp_path / "registry.json")
    repo=tmp_path / "repo";repo.mkdir()
    def git(*args):
        return subprocess.run(["git","-C",str(repo),*args],check=True,capture_output=True,text=True).stdout.strip()
    git("init");git("-c","user.name=Test","-c","user.email=test@example.test","commit","--allow-empty","-m","initial")
    sub=repo / "sub";sub.mkdir()
    p=store.resolve_project(sub)
    before=git("status","--porcelain")
    result=invoke("create_project_worktree",p.id,path=str(repo / "new-worktree"),branch="codex/nested")
    assert not result["ok"] and "outside" in result["error"]
    assert not (repo / "new-worktree").exists()
    assert not git("branch", "--list", "codex/nested")
    assert git("status","--porcelain")==before
