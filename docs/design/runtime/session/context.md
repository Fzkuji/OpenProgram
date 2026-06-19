# Session 上下文

`session_context` 是统一的 per-turn 上下文管理器，负责装载 ContextVar（`_store` / `_current_turn_id` / `_current_runtime` / `_call_id`），让 docstring 进 prompt、DAG 持久化、ask_user 追踪等能力在所有入口生效。

## 接口

```python
@contextmanager
def session_context(
    session_id: str | None = None,
    *,
    agent_id: str = "main",
    turn_id: str | None = None,
    runtime=None,
    create_runtime_if_none: bool = True,
):
    db = default_db()
    sid = session_id or ("adhoc_" + _short_uuid())
    if db.get_session(sid) is None:
        db.create_session(sid, agent_id, source="cli")
    rt = runtime
    if rt is None and create_runtime_if_none:
        rt = create_runtime()
    tid = turn_id or ("turn_" + _short_uuid())

    tokens = []
    tokens.append(("_store",  _store.set(GraphStoreShim(db, sid))))
    tokens.append(("_turn",   _current_turn_id.set(tid)))
    if rt is not None:
        tokens.append(("_rt", _current_runtime.set(rt)))
    try:
        yield SessionHandle(db=db, session_id=sid, runtime=rt, turn_id=tid)
    finally:
        for _name, tok in reversed(tokens):
            tok.var.reset(tok)
```

`session_context` 在 session 不存在时会调 `create_session`——这是 [lifecycle.md](lifecycle.md) 中创建入口之一。

## session 边界

由 `session_id` 的传递决定，不按调用次数。

| 调用方意图 | 传什么 | 行为 |
|---|---|---|
| 跑一件新任务 | 不传 session_id | 新建，把 id 返回/打印给调用方 |
| 接着同一任务（第 2、3 次调用） | 传上次返回的 id | 复用，历史接上 |
| 另起无关的事 | 不传（或传别的 id） | 独立新 session |

CLI 对应：首跑不带 `--session` → 打印新 id；`--session <id>` 续。

## session 的结束

session 不需要显式"结束"——它是 append-only 的 git 历史，写完就停，下次带同 id 来就接着写。`session_context` 退出只 reset ContextVar，不删 session。

## 各入口的用法

| 入口 | 用法 |
|------|------|
| dispatcher | `with session_context(req.session_id, ...)` 替换现有内联 set/reset |
| research harness | `with session_context(session_id="research_" + uuid, runtime=rt)` 包住 research_agent |
| process_runner | 同一个 manager 取代手抄的 set/reset |
| tests | 同上 |
