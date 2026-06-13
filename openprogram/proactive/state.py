"""SessionState — 事件流 fold 出的"当前状况"（events-and-state.md §4）。

规则（尤其 observer）做判断时常要看积累下来的状况，而不只是眼前这条事件。
状况不单独存，由事件增量 fold 出来——没有谁手动维护计数器，它是事件的副产品。

这版只做 L0 机械层（确定、便宜）：改了哪些文件、某工具失败几次、本轮有无
验证动作、turn 边界。L1 启发 / L2 语义（需 LLM）按需再加，不预先造。
按 session 分片，多 session 互不污染（events-and-state §5）。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field

from openprogram.agent.event_bus import Event


@dataclass
class SessionState:
    """单个 session 的累积状况。"""
    changed_files: set[str] = field(default_factory=set)
    tool_fail_count: dict[str, int] = field(default_factory=dict)
    # 本轮（自上次 user.prompt_submitted 起）的信号
    turn_has_file_change: bool = False
    turn_has_verification: bool = False   # 跑了测试 / 用浏览器验证等
    last_tool: str = ""


# 哪些工具算"验证动作"——含 MCP 浏览器操作（policies-mvp 强调，否则前端改动误报）
_VERIFY_TOOLS = ("bash", "test")
_VERIFY_TOOL_SUBSTR = ("pytest", "test", "chrome", "browser", "navigate")


def _is_verification(tool: str, args: dict) -> bool:
    t = (tool or "").lower()
    if any(s in t for s in _VERIFY_TOOL_SUBSTR):
        return True
    if t in _VERIFY_TOOLS:
        cmd = str((args or {}).get("command", "")).lower()
        return any(s in cmd for s in ("test", "pytest", "npm run", "make "))
    return False


class StateStore:
    """进程级、按 session 分片的 state fold。"""

    def __init__(self) -> None:
        self._states: dict[str, SessionState] = {}
        self._lock = threading.Lock()

    def get(self, session_id: str) -> SessionState:
        with self._lock:
            return self._states.setdefault(session_id or "", SessionState())

    def apply(self, event: Event) -> None:
        """把一条事件 fold 进对应 session 的状况（增量，不重算历史）。"""
        sid = event.metadata.get("session", "")
        st = self.get(sid)
        t, p = event.type, event.payload
        with self._lock:
            if t == "user.prompt_submitted":
                # 新一轮：重置 turn 级信号
                st.turn_has_file_change = False
                st.turn_has_verification = False
            elif t == "file.changed":
                path = p.get("path")
                if path:
                    st.changed_files.add(path)
                st.turn_has_file_change = True
            elif t == "tool.before":
                tool = p.get("tool", "")
                st.last_tool = tool
                if _is_verification(tool, p.get("args") or {}):
                    st.turn_has_verification = True
            elif t == "tool.after":
                tool = p.get("tool", "")
                if p.get("is_error"):
                    st.tool_fail_count[tool] = st.tool_fail_count.get(tool, 0) + 1
                else:
                    st.tool_fail_count[tool] = 0


_store: StateStore | None = None
_store_lock = threading.Lock()


def get_state_store() -> StateStore:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = StateStore()
    return _store
