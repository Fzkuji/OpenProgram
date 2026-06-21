"""Channel session 存储 — 路径、创建、加载、保存.

Channel session 跟 webui session 用同一份 SessionDB 后端 + 同一份
``<state>/agents/<agent_id>/sessions/<session_key>/`` 目录布局, 所以
bound 进来的 session 在 webui 侧栏跟用户本地起的会话并列显示, 不需要
专门的 channel session 列表.

从 ``_conversation.py`` 拆分出来 — 该文件原本 588 行混了 5 个职责
(路由 / session_key 计算 / session 存储 / progress streaming / webui
broadcast). 这里只承担 session 存储这一块.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from openprogram.agent.management import manager as _agents


# ---------------------------------------------------------------------------
# 路径 helpers
# ---------------------------------------------------------------------------

def session_path(agent_id: str, session_key: str) -> Path:
    return _agents.sessions_dir(agent_id) / session_key


def meta_path(agent_id: str, session_key: str) -> Path:
    return session_path(agent_id, session_key) / "meta.json"


def messages_path(agent_id: str, session_key: str) -> Path:
    return session_path(agent_id, session_key) / "messages.json"


# ---------------------------------------------------------------------------
# 创建 / 加载
# ---------------------------------------------------------------------------

def load_or_init_session(
    *,
    agent_id: str,
    session_key: str,
    channel: str,
    account_id: str,
    peer: dict[str, Any],
    user_display: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Load (or create) the SQLite-backed session row + replay its
    message log.

    Channels used to write meta.json + messages.json files; SessionDB now
    owns both. We still mkdir the legacy folder so any sub-paths (e.g.
    ``trees/`` for webui context-tree dumps) keep working without churn.
    """
    from openprogram.agent.session_db import default_db
    db = default_db()

    session_path(agent_id, session_key).mkdir(parents=True, exist_ok=True)

    sess = db.get_session(session_key)
    if sess is None:
        # Title starts empty — channel sessions go through the exact same
        # two-stage auto-titling (truncated first line → background LLM)
        # as local/webui sessions, driven by dispatcher finalize →
        # _maybe_auto_title. The channel/account_id columns survive for the
        # frontend to render a brand prefix at display time; they are never
        # baked into the title here.
        meta: dict[str, Any] = {
            "id": session_key,
            "agent_id": agent_id,
            "title": "",
            "created_at": time.time(),
            "channel": channel,
            "source": channel,
            "account_id": account_id,
            "peer": dict(peer),
            "peer_kind": peer.get("kind"),
            "peer_id": peer.get("id"),
            "peer_display": user_display,
        }
        db.create_session(
            session_key, agent_id,
            title=meta["title"],
            created_at=meta["created_at"],
            channel=channel,
            source=channel,
            account_id=account_id,
            peer_kind=peer.get("kind"),
            peer_id=peer.get("id"),
            peer_display=user_display,
            peer=dict(peer),  # full peer dict goes to extra_meta
        )
        return meta, []

    meta = dict(sess)
    # Refresh peer display if the upstream handle changed. Touch only the
    # peer_display column — never the title (titling is owned by the
    # two-stage auto-titler).
    if user_display and meta.get("peer_display") != user_display:
        meta["peer_display"] = user_display
        db.update_session(
            session_key,
            peer_display=user_display,
        )
    # Backfill peer dict from columns when missing from extra_meta
    # (older rows).
    if "peer" not in meta and meta.get("peer_id"):
        meta["peer"] = {"kind": meta.get("peer_kind") or "direct",
                        "id": meta["peer_id"]}
    messages = db.get_messages(session_key)
    return meta, messages


# ---------------------------------------------------------------------------
# 保存
# ---------------------------------------------------------------------------

def save_session(
    agent_id: str,
    session_key: str,
    meta: dict[str, Any],
    messages: list[dict[str, Any]],
    *,
    new_messages: list[dict[str, Any]] | None = None,
) -> None:
    """Persist meta updates and append any new messages.

    ``new_messages`` lets the caller skip re-writing the entire history
    on every turn (the old JSON-file path had no choice). Pass the
    just-ingested rows; if omitted we fall back to inferring "what's
    new" by id-diff against the DB, which is slower but still correct.
    """
    from openprogram.agent.session_db import default_db
    db = default_db()

    # Always touch the legacy dir so other code that drops sub-paths
    # there (webui's ``trees/``) stays happy.
    session_path(agent_id, session_key).mkdir(parents=True, exist_ok=True)

    db.update_session(
        session_key,
        agent_id=agent_id,
        title=meta.get("title"),
        head_id=meta.get("head_id"),
        peer_display=meta.get("peer_display"),
        provider_name=meta.get("provider_name"),
        model=meta.get("model"),
    )
    if new_messages is None:
        existing_ids = {m["id"] for m in db.get_messages(session_key)}
        new_messages = [m for m in messages if m.get("id") not in existing_ids]
    if new_messages:
        db.append_messages(session_key, new_messages)
