"""user-input Phase 2 —— @agentic_function 子进程桥。

子进程里 runtime.ask 的链路（user-input-requests.md Phase 2）：
  child:  runtime._ask_raw → emit_question_asked(data, QueueTransport) → 把 envelope
          推进 event_queue（不走子进程的死 EventBus）；ask_blocking 阻塞在子进程
          **本地** registry 的 Event 上。
  parent: _drain 收到 {"__op_question__": True} envelope → _bridge_question_to_parent
          在**父进程** registry 注册同一个 qid + 发前端卡片 + 起 waiter 线程；
          WS reply 经 _resolve_question resolve 父 registry → waiter 把答案推进
          answer_queue。
  child:  _answer_pump 从 answer_queue 取答案 → resolve 子进程本地 registry →
          ask_blocking 唤醒返回。

提问"往哪条通道送"对齐 Python logging 的 Handler 模型：QuestionTransport.publish
是 Handler.emit；EventLayerTransport / QueueTransport 是两种目的地。通道由 runtime
显式持有，不是模块级全局开关。

这里用两个独立 QuestionRegistry 实例 + 两条真 mp.Queue 在同进程里把父/子两半接起来
（不真 spawn 子进程，省去 ~1s 导入开销，但走的是一模一样的队列语义）。
"""
from __future__ import annotations

import multiprocessing as mp
import threading
import time

import pytest

import openprogram.agent.questions as Q
from openprogram.agent.questions import (
    QuestionRegistry, PendingQuestion, ask_blocking,
    QueueTransport, EventLayerTransport, emit_question_asked,
)


@pytest.fixture(autouse=True)
def _fresh_registry(monkeypatch):
    """每个用例一张干净的 registry。"""
    monkeypatch.setattr(Q, "_registry", QuestionRegistry())
    yield


# ─── child 半：QueueTransport 把提问推进队列 ──────────────────────────────────

def test_queue_transport_pushes_tagged_envelope():
    """QueueTransport.publish 把带 __op_question__ 标记的 envelope 推进队列，
    供父进程 drain 拦截。"""
    q: mp.Queue = mp.get_context("spawn").Queue()
    QueueTransport(q).publish({"id": "q1", "prompt": "lib?"})
    env = q.get(timeout=2)
    assert env == {"__op_question__": True, "data": {"id": "q1", "prompt": "lib?"}}


def test_default_transport_is_event_layer(monkeypatch):
    """不给 transport（父进程现状）→ 默认 EventLayerTransport → emit_ws_frame
    + emit_safe，不抛。"""
    frames, events = [], []
    import openprogram.agent.event_bus as EB
    monkeypatch.setattr(EB, "emit_ws_frame", lambda f: frames.append(f))
    monkeypatch.setattr(EB, "emit_safe", lambda *a, **k: events.append((a, k)))
    emit_question_asked({"id": "q9", "prompt": "x", "session_id": "s"})
    assert frames and frames[0]["type"] == "question.asked"
    assert events  # 总线也发了一份


# ─── child 半：answer-pump 唤醒本地 registry ──────────────────────────────────

def test_answer_pump_resolves_local_registry():
    """模拟子进程 _answer_pump：从 answer_queue 取答案 resolve 本地 registry，
    把阻塞在 ask_blocking 的调用唤醒。"""
    answer_q: mp.Queue = mp.get_context("spawn").Queue()

    def _pump():
        reg = Q.get_question_registry()
        while True:
            msg = answer_q.get()
            if msg is None:
                return
            reg.resolve(msg["id"], msg["outcome"], msg.get("value"))

    threading.Thread(target=_pump, daemon=True).start()

    captured = {}

    def on_asked(q):
        captured["id"] = q.id
        # 模拟父进程稍后把答案送回来
        threading.Timer(
            0.05, lambda: answer_q.put(
                {"id": q.id, "outcome": "answered", "value": "luxon"})
        ).start()

    outcome, value = ask_blocking(
        session_id="s", kind="ask", prompt="lib?", timeout=5,
        on_asked=on_asked)
    assert (outcome, value) == ("answered", "luxon")
    answer_q.put(None)


# ─── parent 半：_bridge_question_to_parent 三态 ───────────────────────────────

def _drain_one(answer_q):
    """非阻塞取一条 answer_queue 消息（最多等 2s）。"""
    return answer_q.get(timeout=2)


def test_bridge_answered_routes_back():
    """子进程问 → 父进程注册 → WS reply → 答案经 answer_queue 回子进程。"""
    from openprogram.agent.process_runner import _bridge_question_to_parent
    from openprogram.webui.ws_actions.session import _resolve_question

    answer_q: mp.Queue = mp.get_context("spawn").Queue()
    pending, lock = set(), threading.Lock()
    data = {"id": "qX", "session_id": "s", "kind": "ask", "prompt": "lib?",
            "options": ["dayjs", "luxon"], "allow_custom": True}

    _bridge_question_to_parent(data, answer_q, pending, lock)
    # 父 registry 现在有这条待答
    assert {p.id for p in Q.get_question_registry().list_pending("s")} == {"qX"}
    assert "qX" in pending

    # WS 侧用户回答（走真实 _resolve_question）
    _resolve_question("qX", "answered", "luxon")

    msg = _drain_one(answer_q)
    assert msg == {"id": "qX", "outcome": "answered", "value": "luxon"}
    # waiter 收尾后从 pending 摘除
    time.sleep(0.05)
    assert "qX" not in pending


def test_bridge_declined_routes_back():
    from openprogram.agent.process_runner import _bridge_question_to_parent
    from openprogram.webui.ws_actions.session import _resolve_question

    answer_q: mp.Queue = mp.get_context("spawn").Queue()
    pending, lock = set(), threading.Lock()
    _bridge_question_to_parent(
        {"id": "qD", "session_id": "s", "kind": "ask", "prompt": "?"},
        answer_q, pending, lock)
    _resolve_question("qD", "declined", None)
    msg = _drain_one(answer_q)
    assert msg == {"id": "qD", "outcome": "declined", "value": None}


def test_bridge_decline_on_child_gone():
    """子进程死了 → _decline_bridged_question 把待答按 declined 收尾，
    答案（declined）仍经 answer_queue 推回（推给已死子进程是无害 no-op）。"""
    from openprogram.agent.process_runner import (
        _bridge_question_to_parent, _decline_bridged_question,
    )

    answer_q: mp.Queue = mp.get_context("spawn").Queue()
    pending, lock = set(), threading.Lock()
    _bridge_question_to_parent(
        {"id": "qK", "session_id": "s", "kind": "ask", "prompt": "?"},
        answer_q, pending, lock)
    _decline_bridged_question("qK")
    msg = _drain_one(answer_q)
    assert msg["id"] == "qK" and msg["outcome"] == "declined"


def test_bridge_cancel_session_declines():
    """stop 路径：cancel_session 把父 registry 该 session 全部待答 declined，
    waiter 把 declined 推回子进程。"""
    from openprogram.agent.process_runner import _bridge_question_to_parent

    answer_q: mp.Queue = mp.get_context("spawn").Queue()
    pending, lock = set(), threading.Lock()
    _bridge_question_to_parent(
        {"id": "qC", "session_id": "sess1", "kind": "confirm", "prompt": "go?"},
        answer_q, pending, lock)
    Q.get_question_registry().cancel_session("sess1")
    msg = _drain_one(answer_q)
    assert msg == {"id": "qC", "outcome": "declined", "value": None}


# ─── 两半接起来：父子全链路（同进程模拟，不真 spawn）──────────────────────────

def test_full_loop_child_asks_parent_answers():
    """两半接成完整闭环：child（本地 registry + QueueTransport 发问 + answer-pump
    收答案）+ parent（bridge + WS resolve），用两条真 mp.Queue 串起来，验证
    ask_blocking 真的拿到答案。qid 由 ask_blocking 生成，随 envelope 流到父侧，
    父侧用同一个 qid 回答。"""
    from openprogram.agent.process_runner import _bridge_question_to_parent
    from openprogram.webui.ws_actions.session import _resolve_question

    ctx = mp.get_context("spawn")
    event_q: mp.Queue = ctx.Queue()       # child→parent（问题）
    answer_q: mp.Queue = ctx.Queue()      # parent→child（答案）
    child_reg = QuestionRegistry()        # 模拟子进程独立的 registry 单例
    pending, lock = set(), threading.Lock()
    transport = QueueTransport(event_q)   # 子进程发问走这条通道

    # child answer-pump：从 answer_q 取答案 resolve child_reg。
    def _child_pump():
        while True:
            m = answer_q.get()
            if m is None:
                return
            child_reg.resolve(m["id"], m["outcome"], m.get("value"))
    threading.Thread(target=_child_pump, daemon=True).start()

    # parent drain + WS reply：拦截 envelope → bridge → 用真实 qid 回答。
    def _parent_drain():
        env = event_q.get(timeout=3)
        assert env.get("__op_question__")
        data = env["data"]
        _bridge_question_to_parent(data, answer_q, pending, lock)
        time.sleep(0.05)  # 让 waiter 线程起来
        _resolve_question(data["id"], "answered", "luxon")
    threading.Thread(target=_parent_drain, daemon=True).start()

    # child：在 child_reg 上阻塞问问题。on_asked 复刻 runtime._ask_raw —— 经
    # QueueTransport 把问题 envelope 推上去。临时把全局 registry 指向 child_reg
    # 模拟"子进程的单例就是它"。
    import openprogram.agent.questions as QM
    prev = QM._registry
    QM._registry = child_reg

    def _on_asked(q):
        emit_question_asked({
            "id": q.id, "session_id": q.session_id, "kind": q.kind,
            "prompt": q.prompt, "options": q.options, "multi": q.multi,
            "allow_custom": q.allow_custom, "detail": q.detail,
            "expires_at": q.expires_at,
        }, transport)

    try:
        outcome, value = ask_blocking(
            session_id="s", kind="ask", prompt="lib?", timeout=5,
            on_asked=_on_asked)
    finally:
        QM._registry = prev
        answer_q.put(None)

    assert (outcome, value) == ("answered", "luxon")
