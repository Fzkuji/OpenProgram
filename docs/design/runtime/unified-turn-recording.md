# 统一"记一次模型调用"——主干 + 可选钩子

Status: design（待定，先设计不动代码）· Created: 2026-06-19

> 目标:不管哪个入口（聊天 / 函数体内 / 以后新增的），"一次模型被调用并记进 DAG"都走**同一条主干流程**。各入口的特殊处理做成主干上**有就执行、没有就跳过**的钩子（hook），最终汇到同一个底层写入。不是两条平行路线硬拆，是一条线穿下来挂可选钩子。

## 1. 为什么要做（问题回顾）

现在"记一次模型回复"有两套配对写法（开占位 running → 回填结果），按入口分裂:

| 入口 | 开占位 | 回填 | 在哪 |
|---|---|---|---|
| dispatcher（聊天） | `insert_placeholder`（_turn_lifecycle.py） | `persist_assistant_message`（persistence.py） | agent/dispatcher/ |
| runtime.exec（函数体内） | `_open_model_call_node`（runtime.py） | `_close_model_call_node`（runtime.py） | agentic_programming/ |

来历:聊天那套最早就有;exec 那套是 2026-06-18 修 wiki_agent 递归 bug 时新补的（`1ffc8a80`），当时没复用聊天那套（依赖方向会反 + 字段绑死聊天概念），于是并存。

**底层其实已经统一**:两套最后都落到同一个 `GraphStoreShim` + `_msg_to_node`/`_node_to_msg`（`store/session/_msg_adapter.py`），都写成 `role=llm` 的 `Call` 节点。分裂的是**上层那段"开占位→组装字段→回填"的配对逻辑**，各写各的。

## 2. 设计:一条主干 + 可选钩子

把"记一次模型调用"抽成一条主干，各入口的特殊处理是主干上的钩子:

```
        一次模型被调用（任何入口）
                  │
   ┌──────────────┼───────────────────────────┐
   │  ① open 节点（status=running）            │  主干·统一
   │     写一个 Call(role, parent_id, meta)    │
   └──────────────┬───────────────────────────┘
                  │
   ┌──────────────┼───────────────────────────┐
   │  [钩子 before] 入口特有的"调用前"处理      │  可选·有就跑
   │   · 聊天:推 placeholder 给前端(实时气泡) │
   │   · exec:_call_id 重指(工具归属)         │
   └──────────────┬───────────────────────────┘
                  │
   ┌──────────────┼───────────────────────────┐
   │  ② 跑 agent_loop（调模型 + tool loop）     │  主干·共享引擎
   └──────────────┬───────────────────────────┘
                  │
   ┌──────────────┼───────────────────────────┐
   │  [钩子 after] 入口特有的"调用后"处理       │  可选·有就跑
   │   · 聊天:组装 token 列 / ordered blocks   │
   │          / cancelled-vs-completed 终态     │
   │   · exec:choices 解析 / 嵌套结果归属      │
   └──────────────┬───────────────────────────┘
                  │
   ┌──────────────┼───────────────────────────┐
   │  ③ close 节点（回填 output + 钩子产出的    │  主干·统一·唯一落点
   │     字段 + 终态），写同一个 DAG            │
   └──────────────────────────────────────────┘
```

要点:
- **① open / ③ close 是主干**，所有入口都走，写同一个 `Call` 节点、同一个存储。
- **钩子 before/after 是可选的**——入口有特殊处理就挂，没有就跳过（默认空）。
- **钩子产出的字段通过透传进 close**，主干原语不认识 `token`/`blocks`/`source` 这些具体字段，只负责"把这包 metadata 写进节点"。这样底层原语不被聊天概念污染。
- **以后新增入口**（定时任务、外部 API、子 agent）只需走主干 + 按需挂自己的钩子，不用再造一套。

## 3. 主干原语签名

放在能被两个包共用的位置（建议 `store/session/` 或 `context/`，因为本质是 DAG 写入；当前在 `runtime.py`，迁移时下沉）。

```python
def open_call_node(
    store, *,
    role: str = "llm",            # 聊天传 "assistant"(经 _node_to_msg 还原), exec 默认 "llm"
    parent_id: str | None = None, # 聊天传 user_msg_id(分支用); exec 用 called_by
    called_by: str | None = None, # exec 的工具归属
    node_id: str | None = None,   # 调用方可指定 id(聊天的 assistant_msg_id)
    name: str = "",               # model id
    metadata: dict | None = None, # 入口透传的额外字段(source/worker_id/started_at...)
) -> str | None:                  # 返回 node_id, 无 store 时 None
    """主干第①步:写一个 status=running 的占位节点。"""

def close_call_node(
    store, node_id: str | None, *,
    output: str,
    status: str = "success",      # 聊天传 completed/cancelled/error; exec 传 success/error
    metadata: dict | None = None, # 钩子产出的字段(token 列/blocks/...)合并进节点 metadata
) -> None:
    """主干第③步:回填 output + 终态 + 钩子字段。无 node_id 时 no-op。"""
```

关键:`metadata` 是钩子和主干之间的契约。聊天把 `{token_*, extra:{blocks,tool_calls}, parent_id}` 塞进去，exec 不塞。原语只 `node.metadata.update(metadata)`，不解释内容。

## 4. 各入口怎么挂钩子

### dispatcher（聊天）

```
open_call_node(store, role="assistant", node_id=assistant_msg_id,
               parent_id=user_msg_id,
               metadata={source, worker_id, started_at})   # ← 钩子 before 的字段
→ 跑 agent_loop（前端实时气泡靠这个 running 节点）
→ [钩子 after] 组装 blocks / token 列 / cancelled-or-completed
→ close_call_node(store, id, output=final_text, status=completed|cancelled,
                  metadata={token_*, token_model, extra:{blocks,tool_calls}})
```

`persist_assistant_message` 退化成"组装钩子字段"——不再自己 `append_message`/`shim.update`，把字段交给 `close_call_node`。`insert_placeholder` 退化成 `open_call_node(role="assistant", ...)`。

### runtime.exec（函数体内）

```
open_call_node(store, role="llm", called_by=_call_id, metadata={prompt_text})
→ _call_id 重指到此节点（钩子 before:工具归属）
→ 跑 agent_loop
→ close_call_node(store, id, output=reply, status=success)   # 钩子 after 基本为空
```

exec 几乎就是主干裸跑——它本来就接近主干形态（现在的 `_open/_close_model_call_node` 就是雏形）。

### 错误路径

两个入口的"出错回填"统一成 `close_call_node(status="error", output=err_text, metadata={error, trace})`。删掉 `fold_error_into_placeholder` / `write_standalone_error_node` 里重复的 update 逻辑。

## 5. 不被破坏的约束（实证过）

统一后必须保持这些，否则前端/分支/计量断（详见 `llm-call-unification.md` 的依赖分析）:

| 字段 | 谁要 | 怎么保 |
|---|---|---|
| `extra.blocks` | 前端气泡 thinking/text/tool 卡片 | 聊天钩子 after 仍组装，经 metadata 透传 |
| token 列 + token_model | 计量 UI | 同上 |
| `metadata.parent_id=user_msg_id` | 分支/fork/rewind | open 时传 parent_id |
| cancelled/completed 终态 | 用户停止的部分输出 | close 的 status 参数 |
| 序列化输出 `role="assistant"` | 前端识别气泡 | `_node_to_msg` 不变（llm 节点默认还原 assistant） |

零前端改动——序列化收口点 `_node_to_msg` 不动，前端读到的字段照旧。

## 6. 迁移步骤（每步独立可验证）

| 步 | 做什么 | 验证 |
|---|---|---|
| 1 | 抽 `open_call_node`/`close_call_node` 主干原语（先放 runtime.py，签名按上面;支持 metadata 透传 + role/parent_id 参数） | 单测:open→close 写出带额外 metadata 的节点 |
| 2 | exec 的 `_open/_close_model_call_node` 改成调主干原语（行为不变） | pytest tests/agentic_programming/ |
| 3 | dispatcher `insert_placeholder` → `open_call_node(role=assistant,...)` | pytest tests/unit/test_dispatcher* |
| 4 | dispatcher `persist_assistant_message` → 组装字段 + `close_call_node(...)`，删自己的 append/update | 同上 + webui 端到端(气泡/blocks/token/停止/fork) |
| 5 | 错误路径统一到 `close_call_node(status=error)`，删 fold/standalone 重复逻辑 | 触发一次 provider error，前端红框正常 |
| 6 | 把主干原语下沉到 store/session（可选，去掉跨包依赖方向问题） | 全量 pytest |

步 1-2 零风险（只重构 exec 自己）。步 3-5 动 dispatcher 持久化，需 webui 端到端验证。步 6 是收尾。

## 7. 权衡（诚实记录）

- **收益**:代码里只有一处"记一次模型调用"的配对逻辑;新入口零成本接入;概念清晰（主干 + 钩子）。
- **代价**:动 dispatcher 持久化是高风险区（前端字段、分支、计量都挂在上面）。实测底层存储已统一，真正去重的是上层配对逻辑 + 那几个 update 调用。
- **是否值得**:取决于"以后会不会有更多入口"。若会（定时任务、外部 API、多 agent），主干+钩子的收益随入口数增长;若聊天+exec 就是全部，收益主要是消除"两套并存"的认知负担。

## 相关文件

- `openprogram/agentic_programming/runtime.py` — _open/_close_model_call_node（主干原语雏形）
- `openprogram/agent/dispatcher/persistence.py` — persist_assistant_message（钩子 after 字段组装）
- `openprogram/agent/internals/_turn_lifecycle.py` — insert_placeholder / fold_error（钩子 before / 错误路径）
- `openprogram/store/session/_msg_adapter.py` — _msg_to_node / _node_to_msg（统一序列化收口点）
- `openprogram/store/session/graphstore_shim.py` — append / update（统一存储底层）
- `docs/design/runtime/llm-call-unification.md` — 调用拓扑 + 依赖分析（本文档的上游）
