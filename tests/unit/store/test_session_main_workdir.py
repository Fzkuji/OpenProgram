"""会话主工作目录的语义：第一轮定格、缺失不回退、relocate 留痕。

设计：docs/reference/design/runtime/session/operations.md "Main project
binding" + docs/reference/design/runtime/additional-working-directories.md。
锁四件事：
  1. 有轮次的会话改绑别的项目 → 被拒（FROZEN_ERROR）；同项目重绑仍放行
     （草稿转正后 chat_ack 的幂等 bind 走这条路）；
  2. 绑定项目目录消失 → project_workdir_for 返回 None（不悄悄回落到默认
     项目的家目录），project_path_missing 报出缺失路径；
  3. relocate_project 改项目 path、保留 id，并在会话图上留一个
     role=code / name=project/relocate / caller=ROOT 的记录节点；
  4. 追加目录在有轮次的会话上照样能增删。
"""
from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import pytest

from openprogram.agent.session_db import SessionDB
from openprogram.webui.ws_actions import project as ws_project


class FakeWS:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_text(self, text: str) -> None:
        self.sent.append(json.loads(text))


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """隔离 SessionDB 与项目注册表（state dir 指向 tmp）。"""
    db = SessionDB(tmp_path / "sessions.sqlite")
    monkeypatch.setattr("openprogram.agent.session_db.default_db", lambda: db)
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: str(state))
    return db


def _reply(ws: FakeWS, kind: str) -> dict:
    frames = [f for f in ws.sent if f.get("type") == kind]
    assert frames, f"no {kind} frame in {ws.sent}"
    return frames[0]["data"]


def _add_turn(db, session_id: str) -> None:
    db.append_message(session_id, {
        "id": "u1", "role": "user", "content": "hi",
        "timestamp": 0, "predecessor": None,
    })


# 1. 第一轮定格


def test_bind_allowed_while_session_has_no_turns(env, tmp_path: Path):
    db = env
    from openprogram.store.project import project_store as P
    proj = P.resolve_project(tmp_path / "proj_a", name="a")
    (tmp_path / "proj_a").mkdir(exist_ok=True)
    db.create_session("s1", "main")

    ws = FakeWS()
    asyncio.run(ws_project.handle_set_session_project(
        ws, {"session_id": "s1", "project_id": proj.id}))

    data = _reply(ws, "session_project_set")
    assert data["ok"] is True and data["error"] is None
    assert P.project_for_session("s1").id == proj.id


def test_rebind_rejected_once_session_has_turns(env, tmp_path: Path):
    db = env
    from openprogram.store.project import project_store as P
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    a = P.resolve_project(tmp_path / "a", name="a")
    b = P.resolve_project(tmp_path / "b", name="b")
    db.create_session("s1", "main")
    P.bind_session("s1", a.id)
    _add_turn(db, "s1")

    ws = FakeWS()
    asyncio.run(ws_project.handle_set_session_project(
        ws, {"session_id": "s1", "project_id": b.id}))

    data = _reply(ws, "session_project_set")
    assert data["ok"] is False
    assert data["error"] == ws_project.FROZEN_ERROR
    # 绑定没被改动。
    assert P.project_for_session("s1").id == a.id


def test_rebind_to_same_project_still_allowed_after_turns(env, tmp_path: Path):
    """草稿转正：第一帧带 project_id 建库，chat_ack 再补一次幂等 bind，
    此时会话已有轮次——同项目重绑必须放行，否则反向索引写不进去。"""
    db = env
    from openprogram.store.project import project_store as P
    (tmp_path / "a").mkdir()
    a = P.resolve_project(tmp_path / "a", name="a")
    # create_session(project_id=...) 就是 handle_chat 走的那条路，它已经
    # 顺手 bind_session；ack 后的那次 set_session_project 是补幂等。
    db.create_session("s1", "main", project_id=a.id)
    _add_turn(db, "s1")

    ws = FakeWS()
    asyncio.run(ws_project.handle_set_session_project(
        ws, {"session_id": "s1", "project_id": a.id}))

    assert _reply(ws, "session_project_set")["ok"] is True
    assert P.project_for_session("s1").id == a.id


# 2. 缺失不回退


def test_project_workdir_none_when_directory_missing(env, tmp_path: Path,
                                                     monkeypatch):
    """目录消失 → 不回落到默认项目（家目录），返回 None 让调用方用
    会话 workdir/ 兜底。"""
    from openprogram.agent.internals import _workdir
    from openprogram.store.project import project_store as P
    gone = tmp_path / "gone"
    gone.mkdir()
    proj = P.resolve_project(gone, name="gone")
    env.create_session("s1", "main")
    P.bind_session("s1", proj.id)
    shutil.rmtree(gone)

    assert _workdir.project_workdir_for("s1") is None
    assert _workdir.project_path_missing("s1") == proj.path


def test_project_path_missing_none_when_directory_present(env, tmp_path: Path):
    from openprogram.agent.internals import _workdir
    from openprogram.store.project import project_store as P
    live = tmp_path / "live"
    live.mkdir()
    proj = P.resolve_project(live, name="live")
    env.create_session("s1", "main")
    P.bind_session("s1", proj.id)

    assert _workdir.project_workdir_for("s1") == live
    assert _workdir.project_path_missing("s1") is None


def test_list_projects_reports_path_missing(env, tmp_path: Path):
    from openprogram.store.project import project_store as P
    gone = tmp_path / "gone"
    gone.mkdir()
    P.resolve_project(gone, name="gone")
    shutil.rmtree(gone)

    ws = FakeWS()
    asyncio.run(ws_project.handle_list_projects(ws, {"session_id": ""}))
    by_name = {p["name"]: p for p in _reply(ws, "projects_list")["projects"]}
    assert by_name["gone"]["path_missing"] is True


# 3. relocate 留痕


def test_relocate_project_moves_path_and_keeps_id(env, tmp_path: Path):
    from openprogram.store.project import project_store as P
    old = tmp_path / "old"
    old.mkdir()
    new = tmp_path / "new"
    new.mkdir()
    proj = P.resolve_project(old, name="old")

    moved = P.relocate_project(proj.id, new)
    assert moved.id == proj.id
    assert Path(moved.path) == new.resolve()
    assert Path(P.get_project(proj.id).path) == new.resolve()


def test_relocate_default_project_refused(env):
    from openprogram.store.project import project_store as P
    default = P.get_default_project()
    with pytest.raises(P.ProjectStoreError):
        P.relocate_project(default.id, Path.home())


def test_relocate_records_a_graph_node(env, tmp_path: Path):
    db = env
    from openprogram.store.project import project_store as P
    from openprogram.store.project.relocate_node import NODE_NAME
    old = tmp_path / "old"
    old.mkdir()
    new = tmp_path / "new"
    new.mkdir()
    proj = P.resolve_project(old, name="old")
    db.create_session("s1", "main")
    P.bind_session("s1", proj.id)
    _add_turn(db, "s1")
    head_before = db.get_session("s1").get("head_id") if hasattr(db, "get_session") else None

    ws = FakeWS()
    asyncio.run(ws_project.handle_relocate_project(ws, {
        "session_id": "s1", "project_id": proj.id, "path": str(new),
    }))

    data = _reply(ws, "project_relocated")
    assert data["ok"] is True
    assert Path(data["old_path"]) == old.resolve()
    assert data["node_id"]

    nodes = [n for n in db.get_nodes("s1") if n.name == NODE_NAME]
    assert len(nodes) == 1
    node = nodes[0]
    assert node.role == "code"
    assert node.caller == "ROOT"
    assert node.predecessor is None
    assert node.metadata["old_path"] == proj.path
    assert Path(node.metadata["new_path"]) == new
    assert node.metadata["project_id"] == proj.id
    assert str(new) in node.output
    # caller 已设 → 不动 head。
    if head_before is not None:
        assert db.get_session("s1").get("head_id") == head_before
    # 记录节点不进对话流。
    assert all(m.get("role") != "code" or m.get("function") != NODE_NAME
               for m in db.get_branch("s1"))


def test_relocate_rejects_non_directory(env, tmp_path: Path):
    from openprogram.store.project import project_store as P
    old = tmp_path / "old"
    old.mkdir()
    proj = P.resolve_project(old, name="old")

    ws = FakeWS()
    asyncio.run(ws_project.handle_relocate_project(ws, {
        "session_id": "", "project_id": proj.id,
        "path": str(tmp_path / "nowhere"),
    }))
    data = _reply(ws, "project_relocated")
    assert data["ok"] is False and "not a directory" in data["error"]
    assert Path(P.get_project(proj.id).path) == old.resolve()


# 4. 追加目录：定格之后照样增删


# 5. 文件夹移动后的自动适应
#    relocate 随迁位置索引；stale 位置查找自愈；resolve 自动认领被移走的
#    项目；启动清理不把"仓库不可达"的会话当空壳删除。


def _git_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """独立 SessionStore（git 仓库版），并让 project_store 的
    relocate → default_store() 拿到它。"""
    from openprogram.store.session.session_store import SessionStore
    store = SessionStore(tmp_path / "state" / "sessions")
    monkeypatch.setattr(
        "openprogram.store.session.session_store.default_store", lambda: store)
    return store


def test_relocate_rewrites_session_locations(env, tmp_path: Path, monkeypatch):
    from openprogram.store.project import project_store as P
    store = _git_store(tmp_path, monkeypatch)
    old = tmp_path / "old"
    old.mkdir()
    store.create_session("s1", "main", project_path=str(old))
    proj = P.project_for_session("s1")
    assert Path(store._locations["s1"]) == old / ".openprogram" / "sessions" / "s1"

    new = tmp_path / "new"
    shutil.move(str(old), str(new))
    P.relocate_project(proj.id, new)

    assert Path(store._locations["s1"]) == new / ".openprogram" / "sessions" / "s1"
    assert (store._session_dir("s1") / "history").is_dir()


def test_stale_location_heals_from_project_registry(env, tmp_path: Path,
                                                    monkeypatch):
    """locations.json 指向死路径、注册表已是新路径（先搬家再定位、然后
    换进程重启的顺序）→ 查找回退到项目当前路径并回写索引。"""
    from openprogram.store.project import project_store as P
    store = _git_store(tmp_path, monkeypatch)
    old = tmp_path / "old"
    old.mkdir()
    store.create_session("s1", "main", project_path=str(old))
    proj = P.project_for_session("s1")
    new = tmp_path / "new"
    shutil.move(str(old), str(new))
    P.relocate_project(proj.id, new)
    # 人为把索引改回死路径，模拟另一个进程里未随迁的快照。
    store._record_location("s1", old / ".openprogram" / "sessions" / "s1")

    healed = store._session_dir("s1")
    assert healed == new / ".openprogram" / "sessions" / "s1"
    assert Path(store._locations["s1"]) == healed


def test_resolve_project_claims_moved_folder(env, tmp_path: Path, monkeypatch):
    """打开搬走后的新位置 → 认领旧项目（保 id、更新 path），不造新项目。"""
    from openprogram.store.project import project_store as P
    store = _git_store(tmp_path, monkeypatch)
    old = tmp_path / "old"
    old.mkdir()
    store.create_session("s1", "main", project_path=str(old))
    proj = P.project_for_session("s1")
    new = tmp_path / "new"
    shutil.move(str(old), str(new))

    claimed = P.resolve_project(new)

    assert claimed.id == proj.id
    assert Path(claimed.path) == new.resolve()
    # 位置索引也随认领更新（claim 内部走 relocate_project）。
    assert Path(store._locations["s1"]) == new / ".openprogram" / "sessions" / "s1"


def test_resolve_project_does_not_claim_a_copy(env, tmp_path: Path, monkeypatch):
    """旧路径还在（是拷贝不是移动）→ 不认领，正常发新 id。"""
    from openprogram.store.project import project_store as P
    store = _git_store(tmp_path, monkeypatch)
    old = tmp_path / "old"
    old.mkdir()
    store.create_session("s1", "main", project_path=str(old))
    proj = P.project_for_session("s1")
    copy = tmp_path / "copy"
    shutil.copytree(old, copy)

    other = P.resolve_project(copy)

    assert other.id != proj.id
    assert Path(P.get_project(proj.id).path) == old.resolve()


def test_startup_cleanup_keeps_unreachable_project_sessions(env, tmp_path: Path,
                                                            monkeypatch):
    """项目移走且未定位时重启 worker → 会话不能被当空壳清除。"""
    from openprogram.store.session.session_store import SessionStore
    store = _git_store(tmp_path, monkeypatch)
    old = tmp_path / "old"
    old.mkdir()
    store.create_session("s1", "main", project_path=str(old),
                         created_at=0.0, updated_at=0.0)
    store._flush_index()
    shutil.move(str(old), str(tmp_path / "elsewhere"))

    reopened = SessionStore(tmp_path / "state" / "sessions")

    assert "s1" in reopened._index


def test_additional_dirs_still_editable_after_freeze(env, tmp_path: Path):
    """主目录定格不牵连追加目录——有轮次的会话仍能增删。"""
    db = env
    from openprogram.store.project import project_store as P
    (tmp_path / "a").mkdir()
    a = P.resolve_project(tmp_path / "a", name="a")
    db.create_session("s1", "main")
    P.bind_session("s1", a.id)
    _add_turn(db, "s1")
    extra = tmp_path / "extra"
    extra.mkdir()

    ws = FakeWS()
    asyncio.run(ws_project.handle_add_session_workdir(
        ws, {"session_id": "s1", "path": str(extra)}))
    assert _reply(ws, "session_workdir_added")["ok"] is True
    assert str(extra.resolve()) in _reply(ws, "session_workdirs")["workdirs"]

    ws2 = FakeWS()
    asyncio.run(ws_project.handle_remove_session_workdir(
        ws2, {"session_id": "s1", "path": str(extra)}))
    assert _reply(ws2, "session_workdir_removed")["ok"] is True
    assert str(extra.resolve()) not in _reply(ws2, "session_workdirs")["workdirs"]
