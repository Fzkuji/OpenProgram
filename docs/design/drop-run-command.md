# 取消 `/run` 命令: 统一所有 @agentic_function 执行路径

Status: planned · Owner: 用户主导

## 现状

`@agentic_function` (gui_agent / research_agent / wiki_agent 等) 当前有
**两条** 执行路径, 入口和后端都不一样:

1. **用户主动跑** ("/run gui_agent task=..." 或 Functions 面板 fn-form 提交)
   - 前端: chat 输入框解析 `/run ...` → ws 发 `{action: "run", function, kwargs}`;
     或 fn-form 提交 → `POST /api/run/{name}`
   - 后端: `webui/_execute/__init__.py::execute_in_context` 分支 `action=="run"`
     → `webui/_execute/run.py::run_function` → 走自己的 placeholder write
     + `live_progress` + `GraphStoreShim.update` finalize
2. **LLM 主动调** (gpt-5.5 看到 tools 列表, 自己决定 call)
   - 后端: `dispatcher.agent_loop` 跑工具 → `_wrap_agentic_runtime_block`
     (commit bb863ba6 新加的) → 同样 placeholder + finalize, 视觉上跟 (1) 一致

两条路径执行结果现在视觉上统一 (#128 把 (2) 包装成 runtime-block), 但代码上
仍是两套 placeholder / finalize / DAG anchor 实现, `_execute/run.py` 跟
`dispatcher._wrap_agentic_runtime_block` 各写一份。维护负担、bug 风险、
state machine 多套, 都没必要。

## 终态

**单一执行路径**: 所有 @agentic_function 调用 (无论触发方是用户 UI 还是 LLM
决策) 都走 `dispatcher.agent_loop` 的 tool_call dispatch, 由
`_wrap_agentic_runtime_block` 写 runtime placeholder, 由 agent_loop 跑工具,
由 agentic_function decorator 把内部 DAG anchor 到 runtime row 下,
由 `live_progress` + `GraphStoreShim.update` 收尾。

`webui/_execute/run.py`、`/api/run/{name}` endpoint、ws `action="run"`
分支、chat composer 里 `/run` typed-command 解析, 全部删除。

## 设计

### 新 endpoint: `POST /api/function/{name}`

入参:

```json
{
  "session_id": "...",       // 可选, 不传则 _get_or_create_session
  "kwargs": { ... },         // 函数参数
  "work_dir": "/abs/path"    // 必填 (跟旧 /api/run 一样要求)
}
```

实现:

```python
@app.post("/api/function/{name}")
async def post_function(name: str, body: dict):
    conv = _get_or_create_session(body.get("session_id"))
    msg_id = uuid.uuid4().hex[:8]
    # 1. 写一条用户行为命令消息 (不是 user-typed 自然语言, 是 UI 触发的 marker)
    _append_msg(conv, {
        "role": "user",
        "id": msg_id,
        "content": f"[function call] {name}({_kwargs_repr(kwargs)})",
        "source": "fn-form",
        "display": "runtime",
        ...
    })
    # 2. 向 dispatcher 注入一个 forced tool_call:
    #    LLM 不参与决策, 直接合成 tool_call=[{name, arguments=kwargs}]
    #    塞进 agent_loop 的输入, 让它跑这条工具。
    threading.Thread(
        target=_dispatch_forced_tool_call,
        args=(conv["id"], msg_id, name, kwargs, body.get("work_dir")),
        daemon=True,
    ).start()
    return {"session_id": conv["id"], "msg_id": msg_id}
```

### Dispatcher 入口: `dispatch_forced_tool_call`

新增在 `openprogram/agent/dispatcher.py`:

```python
def dispatch_forced_tool_call(
    session_id: str, anchor_msg_id: str,
    tool_name: str, tool_input: dict,
    work_dir: Optional[str] = None,
) -> None:
    """跑一次 @agentic_function 但跳过 LLM 决策。

    跟 agent_loop 跑工具时的代码路径**完全一样**, 只是 tool_calls 数组不是
    模型生成的, 是 caller 注入的。复用 _wrap_agentic_runtime_block + 内部
    decorator anchor + live_progress + GraphStoreShim.update 全套设施。
    """
    # 找 AgentTool
    tool = _resolve_agentic_tool(tool_name)
    if tool is None or not getattr(tool, "_is_agentic", False):
        # 非 agentic tool 不允许这条路径 (避免 bash 这种被 forced)
        raise ValueError(...)
    # 包装成 runtime block
    wrapped = _wrap_agentic_runtime_block(tool, session_id, anchor_msg_id, ...)
    # 执行
    result = wrapped.execute(tool_input)  # 内部走 dispatcher 现有 wrapper
    # 写结果 + 翻 status=done 由 wrapper 自己处理
```

### 删除清单

| 文件 | 处理 |
|---|---|
| `webui/_execute/run.py` | 删 |
| `webui/_execute/__init__.py::execute_in_context` 里 `action=="run"` 分支 | 删 |
| `webui/routes/chat.py::/api/run/{name}` | 删 |
| `webui/ws_actions/chat.py` 三处 `if parsed["action"] == "run"` | 删 |
| `webui/_chat_helpers.py` 里那些 `action: "run"` 解析分支 | 改成 `action: "function_call"` 或类似, 让前端别再发 `/run` typed 命令 |
| `web/components/chat/composer/` 里识别 `/run` 前缀的解析 | 删 (`/run` 不再是命令) |
| fn-form 提交逻辑 | 改成调用新 `/api/function/{name}` |

### 兼容性

旧 session 历史里已经存了 `role=user, content="run gui_agent task=..."`
的消息, conv-mapper 现在能渲染。这些行不动, 保留显示。新触发改走新路径。

老的 `/api/run/{name}` endpoint 可以保留一段时间 (返回 deprecation header
+ 内部 forward 到 `/api/function/{name}`), 但 chat 里的 `/run` typed
command 必须立刻删 — 它依赖 `_chat_helpers.py` 解析, 一删那条解析路径
就直接断, 用户敲 `/run xxx` 就被当成纯文本消息发给 LLM。

## 实施分轨

- **A (后端)**: 新 `/api/function/{name}` + `dispatch_forced_tool_call` +
  删 `_execute/run.py` + 删 ws/REST `action=run` 分支 + 删
  `_chat_helpers.py` 里的 `/run` 解析
- **B (前端)**: fn-form 提交改 endpoint; composer 删 `/run` typed-command
  解析; welcome-screen / favorites-list 触发函数的入口也改走新 endpoint
- **C (协调 + 审计)**: 主程负责 — 写本设计文档、追所有 `/run` / `api/run` /
  `action=run` 引用确保删干净、最后跑端到端验证 (浏览器点 Functions
  面板触发 gui_agent + LLM 自己 call gui_agent, 两条路径都看到 RuntimeBlock)

## 不在范围

- `dispatcher.agent_loop` 本身的重构 (它已经能跑 @agentic_function tool, 不动)
- `_wrap_agentic_runtime_block` 自身改 (它已经在工作, 复用)
- 老 session 的迁移 (已存的消息不改)
