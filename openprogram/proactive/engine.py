"""Proactive 引擎：把注册的规则接到事件层。

两条 lane（execution-model.md §2-§4）：
* 挡路（gate）：同步。包成 tool_gate 的 gate 函数注册到 tool.before 问询点。
  多规则取最严（deny > ask > allow），cooldown 防刷屏，规则出错吞掉。
* 旁观（observer）：异步。订阅总线，先把事件 fold 进 state，再叫醒订阅该
  事件类型的 observer 规则，落地 Notify / Inject / Prepare。

防自触发（invariants.md 不变式 1）：proactive 自己产生的事件
（root_origin=proactive）不再喂给规则，斩断自激。

install_proactive() 幂等，worker 启动调一次。
"""
from __future__ import annotations

import sys
import threading

from openprogram.agent.event_bus import Event, emit_ws_frame, get_event_bus
from openprogram.agent.tool_gate import register_tool_gate

from .actions import Gate, Inject, Notify, Prepare
from .policy import registered_policies
from .state import get_state_store

# cooldown 记账：dedup_key -> 上次出手的事件 ts（用事件时钟，不用 wall-clock）
_last_fired: dict[str, float] = {}
_cooldown_lock = threading.Lock()
_installed = False
_install_lock = threading.Lock()


def _cooldown_ok(key: str, cooldown_s: float, now_ts: float) -> bool:
    if cooldown_s <= 0:
        return True
    with _cooldown_lock:
        last = _last_fired.get(key)
        if last is not None and (now_ts - last) < cooldown_s:
            return False
        _last_fired[key] = now_ts
        return True


# ─── 挡路 lane（同步，接 tool.before 问询点）─────────────────────────────────

def _gate_fn(event: Event) -> str | None:
    """tool_gate 回调：跑所有 gate 规则，取最严裁决，返回 deny 理由或 None。

    这版把 ask 也按 deny 处理（同样回 error tool result 给模型，附"需确认"
    措辞）——通用确认弹窗（接 ApprovalRegistry）留到后续，先保证危险动作拦得住。
    """
    state = get_state_store().get(event.metadata.get("session", ""))
    deny_reasons: list[str] = []
    ask_reasons: list[str] = []
    for policy in registered_policies(lane="gate"):
        try:
            action = policy.evaluate(event, state)
        except Exception as exc:  # 规则 bug 不能砖掉工具调用（§5 fail-open）
            print(f"[proactive] gate policy {policy.name} error: {exc}", file=sys.stderr)
            continue
        if not isinstance(action, Gate) or action.verdict == "allow":
            continue
        if not _cooldown_ok(policy.dedup_key(event, state), policy.cooldown_s, event.ts):
            continue
        (deny_reasons if action.verdict == "deny" else ask_reasons).append(action.reason)
    # 最严：deny > ask > allow
    if deny_reasons:
        return "; ".join(r for r in deny_reasons if r) or "blocked by policy"
    if ask_reasons:
        joined = "; ".join(r for r in ask_reasons if r) or "needs confirmation"
        return f"需要确认（暂以拦截处理，请手动放行重试）：{joined}"
    return None


# ─── 旁观 lane（异步，订阅总线）──────────────────────────────────────────────

def _land_observer_action(policy, action, event: Event) -> None:
    """把 observer 动作落地。"""
    sid = event.metadata.get("session", "")
    if isinstance(action, Notify):
        # 前端 use-ws.ts 的 "event" case 接收；带 proactive 标记便于前端区分来源。
        emit_ws_frame({
            "type": "event",
            "data": {
                "kind": "proactive.notify",
                "policy": policy.name,
                "message": action.message,
                "severity": action.severity,
                "ref": action.ref,
                "session_id": sid,
            },
        })
    elif isinstance(action, Inject):
        # 记一条 derived 事件；steering 注入接入留到有真规则需要时（避免空接）。
        get_event_bus().emit(Event(
            id=event.id + ":inject", ts=event.ts, type="proactive.inject",
            origin="proactive", payload={"text": action.text, "policy": policy.name},
            metadata={"session": sid},
        ))
    elif isinstance(action, Prepare):
        get_event_bus().emit(Event(
            id=event.id + ":prepare", ts=event.ts, type="proactive.prepare_requested",
            origin="proactive", payload={"spec": action.spec, "policy": policy.name},
            metadata={"session": sid},
        ))


def _observer_consumer(event: Event) -> None:
    """总线订阅回调：fold state，然后跑订阅该事件类型的 observer 规则。"""
    # 防自触发（不变式 1）：proactive 起头的因果链不再喂规则。
    if event.metadata.get("root_origin") == "proactive" or event.origin == "proactive":
        return
    store = get_state_store()
    try:
        store.apply(event)
    except Exception as exc:
        print(f"[proactive] state fold error: {exc}", file=sys.stderr)
    state = store.get(event.metadata.get("session", ""))
    for policy in registered_policies(lane="observer"):
        if event.type not in policy.on:
            continue
        try:
            action = policy.evaluate(event, state)
        except Exception as exc:  # 规则 bug 不影响别的规则、不影响 agent
            print(f"[proactive] observer policy {policy.name} error: {exc}", file=sys.stderr)
            continue
        if action is None:
            continue
        if not _cooldown_ok(policy.dedup_key(event, state), policy.cooldown_s, event.ts):
            continue
        try:
            _land_observer_action(policy, action, event)
        except Exception as exc:
            print(f"[proactive] land action error ({policy.name}): {exc}", file=sys.stderr)


def install_proactive() -> bool:
    """把引擎接到事件层（幂等）。返回是否本次真正安装。"""
    global _installed
    with _install_lock:
        if _installed:
            return False
        _installed = True
    register_tool_gate(_gate_fn)                 # 挡路：同步问询点
    get_event_bus().subscribe(_observer_consumer)  # 旁观：订阅全部事件，内部按 on 过滤
    return True
