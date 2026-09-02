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

import asyncio
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping


class UserDeclined(Exception):
    """用户主动拒绝回答（runtime.ask 抛出）。"""


class AskTimeout(Exception):
    """等待超时且没有 default（runtime.ask 抛出）。"""


@dataclass
class PendingQuestion:
    id: str
    session_id: str           # webui session（前端路由用），可空
    kind: str                 # "ask" | "confirm" | "approval" | "form" | "ask_many"
    prompt: str
    execution_id: str = ""    # owning execution; cancel closes these as cancelled
    options: list[str] = field(default_factory=list)
    multi: bool = False
    allow_custom: bool = True
    detail: str = ""
    # kind="form" 专用：MCP-elicitation 风格的 flat-object 字段 schema
    # （字段名 → {type, title, description, enum, default, …}）。答案是一个
    # dict（字段名 → 值），而非 ask 的 str / list[str]。其它 kind 留空。
    schema: dict = field(default_factory=dict)
    # kind="ask_many" 专用：一次打包问的一组问题，前端一屏内可切换着答、
    # 全答完一起提交。每项 = {prompt, options, multi, allow_custom}。答案是
    # 一个 list（与 questions 等长，每项一个 str / list[str]）。其它 kind 留空。
    questions: list = field(default_factory=list)
    created_at: float = 0.0
    expires_at: float = 0.0
    wait_generation: int = 0
    execution_version: int = 0


# resolve 结果：(outcome, value)
#   outcome ∈ {"answered", "declined"}; value 是答案（answered）或 None（declined）
_Resolution = tuple[str, object]


class QuestionRegistry:
    """Local wake notifier over the durable execution wait authority.

    The name remains an import seam for runtime callers.  It has no pending
    question or answer state: all lifecycle reads and writes go to
    ``execution_waits``.  Its only local state is an Event used to wake a
    currently blocked thread sooner than its bounded database poll interval.
    """

    def __init__(self) -> None:
        self._events: dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    def register(self, q: PendingQuestion) -> threading.Event:
        """Ensure the supplied presentation record has one durable wait row."""
        from openprogram.agent.run_control import get_current_execution_id
        from openprogram.execution import default_store
        from openprogram.execution.waits import DurableWaitStore

        executions = default_store()
        waits = DurableWaitStore(executions)
        existing = waits.get_wait(q.id)
        if existing is None:
            execution_id = q.execution_id or get_current_execution_id()
            execution = executions.get_execution(execution_id or "")
            if execution is None or execution.current_attempt_id is None:
                raise RuntimeError("question registration requires a live canonical attempt")
            generation = execution.owner_lease.get("generation")
            if not isinstance(generation, int):
                raise RuntimeError("question registration requires a fenced canonical attempt")
            now = time.time()
            waits.open_wait(
                wait_id=q.id, execution_id=execution.execution_id,
                attempt_id=execution.current_attempt_id, generation=generation,
                kind=q.kind, request={
                    "prompt": q.prompt, "options": q.options, "multi": q.multi,
                    "allow_custom": q.allow_custom, "detail": q.detail,
                    "schema": q.schema, "questions": q.questions,
                }, policy_snapshot={"version": 1, "kind": q.kind},
                expires_at=q.expires_at if q.expires_at > now else now + 300,
                checkpoint_id=execution.checkpoint_head_id,
            )
        ev = threading.Event()
        with self._lock:
            self._events[q.id] = ev
        return ev

    def resolve(self, qid: str, outcome: str, value: object = None) -> bool:
        """Compatibility import seam; lifecycle still uses a command record."""
        return resolve_question_and_broadcast(qid, outcome, value)

    def wake(self, qid: str) -> None:
        """Wake a local waiter after durable state was changed elsewhere.

        Subprocess bridges use this after receiving a terminal durable wait
        projection.  It deliberately does not write an answer: only the
        canonical execution command surface may do that.
        """
        with self._lock:
            event = self._events.get(qid)
        if event is not None:
            event.set()

    def consume(self, qid: str) -> _Resolution | None:
        from openprogram.execution import default_store
        from openprogram.execution.waits import DurableWaitStore

        wait = DurableWaitStore(default_store()).get_wait(qid)
        if wait is None or wait.status.value in {"open", "claimed"}:
            return None
        outcome = {
            "resolved": "answered", "declined": "declined",
            "expired": "timeout", "cancelled": "cancelled",
        }[wait.status.value]
        return outcome, wait.answer

    def list_pending(self, session_id: str | None = None) -> list[PendingQuestion]:
        from openprogram.execution import default_store
        from openprogram.execution.waits import DurableWaitStore

        records = DurableWaitStore(default_store()).list_open(session_id=session_id)
        return [_pending_from_wait(record) for record in records]

    def cancel_session(self, session_id: str) -> None:
        from openprogram.execution import default_store
        from openprogram.execution.waits import DurableWaitStore

        waits = DurableWaitStore(default_store())
        execution_ids = {wait.execution_id for wait in waits.list_open(session_id=session_id)}
        for execution_id in execution_ids:
            waits.cancel_execution(execution_id)

    def cancel_execution(
        self, session_id: str, execution_id: str | None = None,
    ) -> None:
        """Close exact execution waits; session-wide mode remains internal only."""
        from openprogram.execution import default_store
        from openprogram.execution.waits import DurableWaitStore

        waits = DurableWaitStore(default_store())
        if execution_id is not None:
            ids = {wait.wait_id for wait in waits.list_open(execution_id=execution_id)}
            waits.cancel_execution(execution_id)
            self._wake(ids)
            return
        for wait in waits.list_open(session_id=session_id):
            ids = {item.wait_id for item in waits.list_open(execution_id=wait.execution_id)}
            waits.cancel_execution(wait.execution_id)
            self._wake(ids)

    def _wake(self, ids: set[str]) -> None:
        with self._lock:
            events = [event for qid, event in self._events.items() if qid in ids]
        for event in events:
            event.set()


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


def _pending_from_wait(wait) -> PendingQuestion:
    from openprogram.execution import default_store

    request = dict(wait.request)
    execution = default_store().get_execution(wait.execution_id)
    return PendingQuestion(
        id=wait.wait_id, session_id=(execution.session_id if execution is not None else ""), kind=wait.kind,
        prompt=str(request.get("prompt") or ""), execution_id=wait.execution_id,
        options=list(request.get("options") or []), multi=bool(request.get("multi")),
        allow_custom=bool(request.get("allow_custom", True)),
        detail=str(request.get("detail") or ""),
        schema=dict(request.get("schema") or {}), questions=list(request.get("questions") or []),
        created_at=wait.created_at, expires_at=wait.expires_at,
        wait_generation=wait.claim_generation,
        execution_version=(execution.status_version if execution is not None else 0),
    )


# 提问传输（QuestionTransport）
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
    """把一次提问送到能应答的一侧。子类实现 publish / retract。"""

    def publish(self, data: dict) -> None:  # pragma: no cover - 抽象
        raise NotImplementedError

    def retract(self, qid: str) -> None:  # pragma: no cover - 抽象
        """收回一个未答的问题（超时）——让前端卡片消失。子类实现。"""
        raise NotImplementedError


class EventLayerTransport(QuestionTransport):
    """默认通道：经事件层把提问发成前端卡片，并进总线（可观测/可订阅）。
    worker 主进程用——WS 就连在这个进程上。"""

    def publish(self, data: dict) -> None:
        try:
            from openprogram.events import emit_ws_frame, emit_safe
            # 1) 给前端：ws.frame 透传成可见卡片（webui 订阅转发）。
            emit_ws_frame({"type": "question.asked", "data": data})
            # 2) 进事件层：发一份纯 question.asked 事件，让"发生了一次提问"像
            #    其他活动一样出现在统一事件流里（可观测/可订阅）。
            emit_safe("question.asked", "agent", data,
                      {"session": data.get("session_id", "")})
        except Exception:
            pass

    def retract(self, qid: str) -> None:
        # 超时：广播 question.rejected，前端按"收回"处理（dequeue 卡片）。
        try:
            from openprogram.events import emit_ws_frame
            emit_ws_frame({"type": "question.rejected", "data": {"id": qid}})
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

    def retract(self, qid: str) -> None:
        # 子进程超时无需自己收回前端卡片：父进程在子进程退出时（finally 的
        # leftover-decline）会把残留待答按 declined 收尾、广播 question.rejected
        # 撤回卡片。这里 no-op。
        pass


_default_transport = EventLayerTransport()


def default_question_transport() -> QuestionTransport:
    """runtime 没被显式装别的 transport 时用的默认通道（事件层）。"""
    return _default_transport


def emit_question_asked(data: dict, transport: "QuestionTransport | None" = None) -> None:
    """发出一次提问，经给定 transport（不给则用默认事件层通道）送出去。"""
    (transport or _default_transport).publish(data)


def retract_question(qid: str, transport: "QuestionTransport | None" = None) -> None:
    """收回一个未答的问题（超时）——经 transport 让前端卡片消失。"""
    (transport or _default_transport).retract(qid)


def open_question(
    *,
    session_id: str,
    kind: str,
    prompt: str,
    options: list[str] | None = None,
    multi: bool = False,
    allow_custom: bool = True,
    detail: str = "",
    schema: dict | None = None,
    questions: list | None = None,
    request_metadata: Mapping[str, Any] | None = None,
    policy_snapshot: Mapping[str, Any] | None = None,
    timeout: float = 300.0,
    on_asked,
) -> tuple[PendingQuestion, threading.Event]:
    """注册一个问题 + emit（不等）。返回 (PendingQuestion, 唤醒 Event)。

    把"注册 + 发问"从"怎么等答案"里拆出来：同步调用方（runtime.ask）拿 Event
    阻塞，async 调用方（工具批准，跑在 asyncio loop 上）用 asyncio.to_thread
    等同一个 Event，互不阻塞各自的执行模型。on_asked(PendingQuestion) 负责把
    问题送出去（经 transport / 事件层）。
    """
    from openprogram.execution import default_store
    from openprogram.execution.waits import DurableWaitStore

    reg = get_question_registry()
    now = time.time()
    execution_id = ""
    try:
        from openprogram.agent.run_control import get_current_execution_id
        execution_id = get_current_execution_id() or ""
    except Exception:
        execution_id = ""
    if not execution_id:
        raise RuntimeError("runtime.ask requires a canonical execution context")
    executions = default_store()
    execution = executions.get_execution(execution_id)
    if execution is None or execution.current_attempt_id is None:
        raise RuntimeError("runtime.ask requires a live canonical attempt")
    generation = execution.owner_lease.get("generation")
    if not isinstance(generation, int):
        raise RuntimeError("runtime.ask requires a fenced canonical attempt")
    request = {
        "prompt": prompt, "options": list(options or []), "multi": multi,
        "allow_custom": allow_custom, "detail": detail,
        "schema": dict(schema or {}), "questions": list(questions or []),
    }
    metadata = dict(request_metadata or {})
    conflict = set(metadata).intersection(request)
    if conflict:
        raise ValueError(f"question metadata cannot replace request fields: {sorted(conflict)!r}")
    request.update(metadata)
    default_policy = {
        "version": 1, "kind": kind,
        "on_decline": "deny" if kind == "approval" else "return_declined",
        "on_timeout": "deny" if kind == "approval" else "return_timeout",
    }
    wait = DurableWaitStore(executions).open_wait(
        wait_id=f"wait_{new_question_id()}", execution_id=execution_id,
        attempt_id=execution.current_attempt_id, generation=generation, kind=kind,
        request=request,
        policy_snapshot=dict(policy_snapshot or default_policy), expires_at=now + timeout,
        checkpoint_id=execution.checkpoint_head_id,
    )
    q = _pending_from_wait(wait)
    # The durable row is already committed above.  The registry only keeps a
    # process-local wake event; it must not perform a second lifecycle write.
    ev = reg.register(q)
    try:
        on_asked(q)
    except Exception:
        pass
    return q, ev


def consume_or_timeout(qid: str) -> _Resolution:
    """等待结束后取结果：被答了返回 (outcome, value)，否则 ("timeout", None)。"""
    res = get_question_registry().consume(qid)
    return res if res is not None else ("pending", None)


def resolve_question_and_broadcast(qid: str, outcome: str, value=None) -> bool:
    """Translate a non-Web surface answer into one canonical wait command.

    The caller supplies content only.  Exact execution identity, expected
    revision and wait generation come from the durable record, never from a
    process-local question map.
    """
    from openprogram.execution import default_control_service, default_store
    from openprogram.execution.attempts import AttemptStore
    from openprogram.execution.control import RuntimeControlService
    from openprogram.execution.driver import DriverRegistry
    from openprogram.execution.waits import DurableWaitStore

    store = default_store()
    wait = DurableWaitStore(store).get_wait(qid)
    if wait is None or wait.status.value not in {"open", "claimed"}:
        return False
    execution = store.get_execution(wait.execution_id)
    if execution is None:
        return False
    command_id = f"question-bridge-{outcome}-{uuid.uuid4().hex}"
    service = default_control_service()
    if service.executions.path != store.path:
        # Embedded callers may bind a temporary execution store in tests.
        # The command still goes through the same canonical service class.
        service = RuntimeControlService(store, AttemptStore(store), DriverRegistry())
    try:
        if outcome == "answered":
            asyncio.run(service.request_wait_answer(
                command_id=command_id, execution_id=wait.execution_id,
                expected_version=execution.status_version,
                actor={"surface": "question-bridge"}, wait_id=wait.wait_id,
                generation=wait.claim_generation, answer=value,
            ))
        else:
            asyncio.run(service.request_wait_decline(
                command_id=command_id, execution_id=wait.execution_id,
                expected_version=execution.status_version,
                actor={"surface": "question-bridge"}, wait_id=wait.wait_id,
                generation=wait.claim_generation, reason=value,
            ))
    except Exception:
        return False
    get_question_registry().wake(qid)
    try:
        from openprogram.events import emit_ws_frame
        emit_ws_frame({
            "type": "question.replied" if outcome == "answered" else "question.rejected",
            "data": {"id": qid},
        })
    except Exception:
        pass
    return True


def ask_blocking(
    *,
    session_id: str,
    kind: str,
    prompt: str,
    options: list[str] | None = None,
    multi: bool = False,
    allow_custom: bool = True,
    detail: str = "",
    schema: dict | None = None,
    questions: list | None = None,
    timeout: float = 300.0,
    on_asked,
    transport: "QuestionTransport | None" = None,
) -> _Resolution:
    """注册问题、emit、**同步**阻塞等答案。返回 (outcome, value)。

    outcome:
      * "answered" — value 是答案（str 或 list[str]）
      * "declined" — value 是 None
      * "cancelled" — execution cancel closed the wait; value 是 None
      * "timeout"  — value 是 None
    on_asked(PendingQuestion) 由调用方提供，负责把问题广播到前端（emit 事件）。
    超时不抛——把 outcome="timeout" 交给上层（runtime.ask/confirm）按各自语义处理；
    同时经 transport 收回前端卡片（否则超时后卡片会一直挂着）。
    """
    q, ev = open_question(
        session_id=session_id, kind=kind, prompt=prompt, options=options,
        multi=multi, allow_custom=allow_custom, detail=detail, schema=schema,
        questions=questions, timeout=timeout, on_asked=on_asked,
    )
    deadline = time.monotonic() + timeout
    outcome, value = "pending", None
    while outcome == "pending":
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            from openprogram.execution import default_store
            from openprogram.execution.waits import DurableWaitStore

            DurableWaitStore(default_store()).expire_due()
            outcome, value = consume_or_timeout(q.id)
            break
        ev.wait(timeout=min(0.25, remaining))
        ev.clear()
        outcome, value = consume_or_timeout(q.id)
    if outcome in {"pending", "timeout"}:
        retract_question(q.id, transport)
        return "timeout", None
    return outcome, value
