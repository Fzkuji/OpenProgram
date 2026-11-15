"""Per-session run settings: additional working directories and the Sandbox override."""
from __future__ import annotations

import json

from openprogram.state.store.session.conversation_work import run_for_conversation


# ── 会话额外工作目录（additional-working-directories.md §3.2）──
# 整表替换语义：前端算好增删后发完整列表，幂等、无"重复添加/删不存在"分支。
# 校验失败整帧拒绝（error 帧带原因），不做部分写入。

async def handle_set_working_dirs(ws, cmd: dict):
    """整表替换会话的额外工作目录。dirs 逐条 expanduser + 必须是存在的目录，
    非法条目整帧拒绝，全部合法才落库并广播 working_dirs 帧。"""
    from pathlib import Path
    from openprogram.interfaces.server import server as _s
    session_id = (cmd.get("session_id") or "").strip()
    raw_dirs = cmd.get("dirs")
    if not session_id or not isinstance(raw_dirs, list):
        await ws.send_text(json.dumps({
            "type": "error",
            "data": {"message": "set_working_dirs requires session_id and dirs (list)"},
        }))
        return
    dirs: list[str] = []
    for d in raw_dirs:
        expanded = Path(str(d)).expanduser()
        if not expanded.is_dir():
            await ws.send_text(json.dumps({
                "type": "error",
                "data": {"message": f"Not a directory: {d}"},
            }))
            return
        # 存 expanduser 后的绝对路径字符串（不 realpath——用户看到自己选的
        # 路径，归一化交给 check_path_safety 消费端）。
        dirs.append(str(expanded))
    from openprogram.runtime.conversation.session_config import save_session_run_config
    # Written under the conversation's lock: off the event loop.
    await run_for_conversation(session_id, 
        lambda: save_session_run_config(session_id, additional_working_dirs=dirs))
    _s._broadcast(json.dumps({"type": "working_dirs", "data": {
        "session_id": session_id, "dirs": dirs,
    }}))


def _inherited_sandbox(session_id: str) -> bool:
    """The Sandbox a branch has when it does not set one: its project's,
    else the configured default (``FALLBACK_ORDER``)."""
    from openprogram.host.sandbox import ui_state
    from openprogram.runtime.conversation.session_config import SessionRunConfig, sandbox_override_from_config
    return bool(ui_state(sandbox_override_from_config(SessionRunConfig(), session_id)).get("sandbox"))


def _sandbox_row(session_id: str, cmd: dict, requested, writes: bool):
    """``(row, override, branch_id)`` of the branch a Sandbox read or write
    names, after writing it; raises ``BranchConfigConflict``."""
    from openprogram.runtime.conversation.session_config import _config_from_row
    from openprogram.interfaces.server.branch_settings import write_branch_settings
    from openprogram.state.store.session.branch_config import config_for_branch
    from openprogram.runtime.context.session_db import default_db
    db = default_db()
    # A draft whose model was already picked has a row, though it is
    # not created yet; its Sandbox is stored there too. A draft with
    # no row gets no ghost row: its choice is echoed below.
    if writes and db.get_session(session_id) is not None:
        if isinstance(requested, bool) and requested == _inherited_sandbox(session_id):
            # Switched to what the project (or the product default)
            # gives anyway: nothing is stored, the branch keeps
            # following it.
            requested = None
        row, _selected = write_branch_settings(session_id, cmd, {"sandbox_enabled": requested})
    else:
        row = db.get_session(session_id) or {}
        named = cmd.get("branch_id") if isinstance(cmd.get("branch_id"), str) and cmd.get("branch_id") else None
        row = {**row, **config_for_branch(row, named), "branch_id": named} if named and row else row
    return row, _config_from_row(row).sandbox_enabled, row.get("branch_id") or row.get("active_branch_id")


async def handle_set_sandbox(ws, cmd: dict):
    """Read or set the session Sandbox override. Missing sandbox_enabled
    is a read. Response includes backend availability so the Plus menu
    can disable itself when sandbox-exec / bwrap is missing."""
    from openprogram.host.sandbox import ui_state
    from openprogram.interfaces.server import server as _s

    session_id = (cmd.get("session_id") or "").strip()
    from openprogram.interfaces.server.branch_settings import current_branch
    from openprogram.state.store.session.branch_config import BranchConfigConflict
    row = {}
    override = None
    requested = cmd.get("sandbox_enabled") if "sandbox_enabled" in cmd else None
    # ``null`` puts the branch back to the project's or global Sandbox mode.
    writes = "sandbox_enabled" in cmd and (requested is None or isinstance(requested, bool))
    branch_id = None
    if session_id:
        # Read and written under the conversation's lock: off the event loop.
        try:
            row, override, branch_id = await run_for_conversation(session_id, 
                _sandbox_row, session_id, cmd, requested, writes)
        except BranchConfigConflict as exc:
            active_branch_id = await run_for_conversation(session_id, current_branch, session_id)
            await ws.send_text(json.dumps({"type": "sandbox_changed", "data": {
                "session_id": session_id, "request_id": cmd.get("request_id"),
                "action": "set_sandbox", "error": "branch_config_conflict", "code": exc.code,
                "active_branch_id": active_branch_id,
            }}))
            return
    # Draft chats have no session row yet, so save is a no-op. Echo the
    # requested override anyway — otherwise ui_state(None) reports the
    # configured default and the Plus-menu switch snaps back.
    if not row and override is None and isinstance(requested, bool):
        override = requested
    data = {"session_id": session_id or None, **ui_state(override),
            # ``branch``: this branch set it; ``inherited``: it follows the
            # project or the configured default (the switch's hint says so).
            "sandbox_source": "branch" if override is not None else "inherited",
            "request_id": cmd.get("request_id"), "action": "set_sandbox",
            "branch_id": branch_id, "active_branch_id": row.get("active_branch_id")}
    frame = json.dumps({"type": "sandbox_changed", "data": data})
    await ws.send_text(frame)
    if "sandbox_enabled" in cmd:
        # Reads are request-local. Broadcast only mutations, without the caller's ID.
        broadcast = {k: v for k, v in data.items() if k != "request_id"}
        _s._broadcast(json.dumps({"type": "sandbox_changed", "data": broadcast}))
