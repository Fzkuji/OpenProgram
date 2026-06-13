"""User-input requests — 函数中途停下来问用户（runtime.ask / confirm）。

设计：docs/design/runtime/user-input-requests.md（Phase 1）。

机制：函数体里调 runtime.ask(...) → 在本进程级 registry 注册一个
PendingQuestion + 一个 threading.Event → 经事件层 emit `question.asked`
（webui 订阅转发成前端可见卡片）→ 函数阻塞在 Event 上 → 用户在前端答 →
resolve_question 写答案、set Event → 函数返回继续。

三态显式（替代旧的"300s 静默返回 None"）：
* answered  — 拿到答案
* declined  — 用户点拒绝 → ask 抛 UserDeclined
* timeout   — 超时 → confirm 返回 default；ask 无 default 时抛 AskTimeout

registry 是 per-request（按 question id），修掉旧全局 handler 的并发覆盖 bug。
resolve 是 claim-once（第一个答复者赢，跨多前端去重）。stop 时用哨兵解除。
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field


class UserDeclined(Exception):
    """用户主动拒绝回答（runtime.ask 抛出）。"""


class AskTimeout(Exception):
    """等待超时且没有 default（runtime.ask 抛出）。"""


@dataclass
class PendingQuestion:
    id: str
    session_id: str           # webui session（前端路由用），可空
    kind: str                 # "ask" | "confirm"
    prompt: str
    options: list[str] = field(default_factory=list)
    multi: bool = False
    allow_custom: bool = True
    detail: str = ""
    created_at: float = 0.0
    expires_at: float = 0.0


# resolve 结果：(outcome, value)
#   outcome ∈ {"answered", "declined"}; value 是答案（answered）或 None（declined）
_Resolution = tuple[str, object]


class QuestionRegistry:
    """进程级待答问题表。线程安全，claim-once。"""

    def __init__(self) -> None:
        self._pending: dict[str, PendingQuestion] = {}
        self._events: dict[str, threading.Event] = {}
        self._results: dict[str, _Resolution] = {}
        self._lock = threading.Lock()

    def register(self, q: PendingQuestion) -> threading.Event:
        ev = threading.Event()
        with self._lock:
            self._pending[q.id] = q
            self._events[q.id] = ev
        return ev

    def resolve(self, qid: str, outcome: str, value: object = None) -> bool:
        """写入结果并唤醒等待者。返回该 id 是否存在且未被领取过（claim-once）。"""
        with self._lock:
            if qid not in self._events or qid in self._results:
                return False
            self._results[qid] = (outcome, value)
            ev = self._events[qid]
            self._pending.pop(qid, None)
        ev.set()
        return True

    def consume(self, qid: str) -> _Resolution | None:
        with self._lock:
            return self._results.pop(qid, None)

    def list_pending(self, session_id: str | None = None) -> list[PendingQuestion]:
        with self._lock:
            ps = list(self._pending.values())
        if session_id is None:
            return ps
        return [p for p in ps if p.session_id == session_id]

    def cancel_session(self, session_id: str) -> None:
        """stop 时解除该 session 所有待答问题（按 declined 处理）。"""
        with self._lock:
            ids = [qid for qid, p in self._pending.items()
                   if p.session_id == session_id]
        for qid in ids:
            self.resolve(qid, "declined", None)


_registry: QuestionRegistry | None = None
_registry_lock = threading.Lock()


def get_question_registry() -> QuestionRegistry:
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                _registry = QuestionRegistry()
    return _registry


def new_question_id() -> str:
    return uuid.uuid4().hex[:12]


# ─── 提问传输（QuestionTransport）─────────────────────────────────────────────
#
# 一次提问要"送到能应答的一侧"。这件事跟 Python logging 的 Handler 同构：
# logging.Handler.emit(record) 把一条记录送到它的目的地，子类换目的地（文件 /
# socket / 终端）。这里 QuestionTransport.publish(data) 把一次提问送到它的目的地，
# 子类换通道：
#   * EventLayerTransport —— 经事件层把问题发成前端卡片 + 进总线（worker 进程用）。
#   * QueueTransport      —— 经 mp.Queue 把问题送回父进程（@agentic_function 跑的
#                             子进程用：子进程的 EventBus 没有订阅者，WS 在父进程，
#                             直接走事件层等于对空气喊；必须走父子之间唯一的队列）。
#
# transport 不是藏在模块里的全局开关——它由 runtime 显式持有（runtime._question_transport），
# 默认 EventLayerTransport；process_runner 在子进程里给那个 runtime 换成 QueueTransport。
# 看 runtime.ask 就能看出问题往哪条 transport 走（对齐 logging：handler 显式挂在
# logger 上，而不是用全局 flag 让 emit 变身）。


class QuestionTransport:
    """把一次提问送到能应答的一侧。子类实现 publish。"""

    def publish(self, data: dict) -> None:  # pragma: no cover - 抽象
        raise NotImplementedError


class EventLayerTransport(QuestionTransport):
    """默认通道：经事件层把提问发成前端卡片，并进总线（可观测/可订阅）。
    worker 主进程用——WS 就连在这个进程上。"""

    def publish(self, data: dict) -> None:
        try:
            from openprogram.agent.event_bus import emit_ws_frame, emit_safe
            # 1) 给前端：ws.frame 透传成可见卡片（webui 订阅转发）。
            emit_ws_frame({"type": "question.asked", "data": data})
            # 2) 进事件层：发一份纯 question.asked 事件，让"发生了一次提问"像
            #    其他活动一样出现在统一事件流里（可观测/可订阅）。
            emit_safe("question.asked", "agent", data,
                      {"session": data.get("session_id", "")})
        except Exception:
            pass


class QueueTransport(QuestionTransport):
    """子进程通道：把提问 envelope 推进父子之间的 mp.Queue，由父进程的 drain
    线程接走、注册到父进程 registry 并发前端。``__op_question__`` 标记让父进程
    把它当"提问"拦截，而不是当普通事件透传给 WS。"""

    def __init__(self, queue) -> None:
        self._queue = queue

    def publish(self, data: dict) -> None:
        try:
            self._queue.put({"__op_question__": True, "data": data},
                            block=False)
        except Exception:
            pass


_default_transport = EventLayerTransport()


def default_question_transport() -> QuestionTransport:
    """runtime 没被显式装别的 transport 时用的默认通道（事件层）。"""
    return _default_transport


def emit_question_asked(data: dict, transport: "QuestionTransport | None" = None) -> None:
    """发出一次提问，经给定 transport（不给则用默认事件层通道）送出去。"""
    (transport or _default_transport).publish(data)


def ask_blocking(
    *,
    session_id: str,
    kind: str,
    prompt: str,
    options: list[str] | None = None,
    multi: bool = False,
    allow_custom: bool = True,
    detail: str = "",
    timeout: float = 300.0,
    on_asked,
) -> _Resolution:
    """注册问题、emit、阻塞等答案。返回 (outcome, value)。

    outcome:
      * "answered" — value 是答案（str 或 list[str]）
      * "declined" — value 是 None
      * "timeout"  — value 是 None
    on_asked(PendingQuestion) 由调用方提供，负责把问题广播到前端（emit 事件）。
    超时不抛——把 outcome="timeout" 交给上层（runtime.ask/confirm）按各自语义处理。
    """
    reg = get_question_registry()
    now = time.time()
    q = PendingQuestion(
        id=new_question_id(), session_id=session_id or "", kind=kind,
        prompt=prompt, options=list(options or []), multi=multi,
        allow_custom=allow_custom, detail=detail,
        created_at=now, expires_at=now + timeout,
    )
    ev = reg.register(q)
    try:
        on_asked(q)
    except Exception:
        pass
    fired = ev.wait(timeout=timeout)
    if not fired:
        # 超时：尽量清理 registry（若期间被答则以答案为准）
        res = reg.consume(q.id)
        return res if res is not None else ("timeout", None)
    res = reg.consume(q.id)
    return res if res is not None else ("timeout", None)
