# Agent 调用流程（权威设计）

Status: design · Created: 2026-06-18

> 这是 agent 调用的**核心框架**——所有 turn / LLM 调用都按这个流程走。后续加功能在此基础上往对应节点插入,不改骨架。本文档曾名"LLM 调用路径统一",现升级为整个调用流程的权威设计。

## 总览图

完整拓扑见 [`agent-call-flow.svg`](agent-call-flow.svg)(收敛分叉 + 每步顺序 + 已有/未来插入点)。

骨架一句话:**两个平级入口(dispatcher · runtime.exec)各管各的外围、各写各的 DAG 记录,只在共享的 agent_loop 汇合;exec 在 loop 之下 → 工具体内可再调 LLM 形成嵌套**。

## 真实拓扑:收敛分叉,不是三层嵌套

代码实证(`agent_loop` 全仓只有两个调用点:`dispatcher/__init__.py:902` 和 `agent/agent.py:418`):dispatcher **不经过** exec,exec **不经过** dispatcher。它们是平级的两个入口,只共享 agent_loop。

```
入口 A: 用户消息 → dispatcher ──直接─────────────┐
                                                  ├─→ agent_loop(共享引擎)
入口 B: @agentic_function 体内 → runtime.exec ──经 AgentSession─┘
```

| 入口 | 职责 | 到 agent_loop 的路径 | 写什么 DAG 记录 | 实现 |
|---|---|---|---|---|
| **A · dispatcher** | turn 生命周期:session、user 节点、attach Runtime、resolve model、解析工具、持久化、前端广播 | **直接** `agent_loop(...)`(`__init__.py:902`) | 顶层回复 → **assistant 会话消息**(SessionDB, `persistence.py:207`) | `agent/dispatcher/__init__.py` |
| **B · runtime.exec** | 一次 LLM 调用:开/关 llm 节点、构建上下文 | 经 **AgentSession** → `session.run`(`runtime.py:1576`)→ `agent.py:418` | 这次调用 → **role=llm DAG 节点** | `agentic_programming/runtime.py` |
| **共享 · agent_loop** | tool loop 引擎:调模型 → 执行工具 → 喂回 → 循环到纯文本 | — | 工具的 code 节点 called_by → 当前 llm 节点 | `agent/agent_loop.py:114` |

### 为什么是两个入口,不是一个

dispatcher 和 exec 服务两种不同场景,外围插件不重叠:dispatcher 要 session 管理 + 前端 WebSocket 广播 + 标题/压缩;exec 要 DAG llm 节点 + AgentSession retry-rollback。硬合并会让一方背上另一方不需要的逻辑(实测试着折叠时撞到模型分叉、节点重复——见下文步 4)。共享 agent_loop,因为"调模型→执行工具→循环"这个引擎对两者一样。

### 关键:exec 在 agent_loop **之下**,所以能嵌套

当 dispatcher 跑 turn,模型调了 `@agentic_function` 工具(如 wiki_agent)→ 工具体内 `runtime.exec` → 又起一个 agent_loop。所以 exec 既是"入口 B",又被 agent_loop 里的工具反向调用:

```
dispatcher → agent_loop → 模型调工具 → @agentic_function 体 → runtime.exec → agent_loop(嵌套)→ ...
```

这是 agentic programming 的根本能力(函数体内可嵌套调 LLM),也是 wiki_agent 递归的来源。如果 exec 在 loop 之上,工具就无法自己再调 LLM。

### 每个节点内部的有序步骤

**A · dispatcher**:1 建/载 session(沿 active branch 取历史)→ 2 写 user 节点 → 3 attach Runtime(_store + _current_runtime)→ 4 resolve model(agent profile + override)→ 5 解析工具(channel/plan/审批 包装)→ 6 **直接调 agent_loop** → 7 持久化 + finalize(assistant 节点、标题、auto-compact)。

**B · runtime.exec**:1 开 llm 节点(running,_call_id 指向它,外含 timeout/retry 循环)→ 2 构建上下文【a 选历史节点 compute_reads → b render 成消息 + 当前 turn → c 解析工具集 toolset/policy/unattended-deny → d 拼 system + skills → e 建 AgentSession(选 stream_fn)】→ 3 **经 AgentSession 调 agent_loop** → 4 关 llm 节点(回填 output,success)。

**共享 · agent_loop**:每轮调模型前【a convert_to_llm → b memory prefetch → c deferred-tool re-split】→ 调模型/流式 → 判断 tool_use? → 是:执行工具 → 结果喂回 → 再调模型;否(纯文本):退出循环。

## 横向对比

| | OpenCode | OpenClaw | Hermes | OpenProgram (统一后) |
|---|---|---|---|---|
| 一次 LLM 调用的抽象 | `Step`(一个节点,tool loop 在内部) | `runId` 配对(llm_input/llm_output,tool 嵌套在内) | 无层级,平铺列表 | `exec`(一个 llm 节点,tool loop 在内部) |
| 记录方式 | 配对:Step.Started → Step.Ended | 配对:llm_input hook → llm_output hook | 只追加,不标记 | 配对:写 running → 回填 output |
| 工具调用归属 | ToolPart 在 Step 内部(子部分) | 嵌套在同一个 runId 下(子 hook) | 平级 tool 消息 | code 节点的 called_by 指向 llm 节点(子节点) |
| 聊天 vs 编程调用 | 统一(一条路径) | 统一(同一个 hook 系统) | 统一(一个 run_conversation) | 统一(都走 runtime.exec) |

OpenProgram 采用 OpenCode/OpenClaw 的模型:一次调用 = 一个节点,配对写入,工具是子节点。

## 问题(现状)

现在"调一次 LLM"有三条路径:

| 路径 | 入口 | tool loop | DAG 写 llm 节点 |
|---|---|---|---|
| 常规聊天 | dispatcher → agent_loop | agent_loop 管 | dispatcher 写 SessionDB 消息(不是 DAG llm 节点) |
| exec legacy | exec → `self._call()` | 没有 | exec 写 llm 节点 |
| exec providers | exec → `session.run` → agent_loop | agent_loop 管 | **没写**(bug) |

### 具体表现

1. **exec providers 路径不写 llm 节点**。`_call_via_providers` 在 `session.run` 返回后直接 return,没调 `_append_model_call_node`。wiki_agent 走这条路径,DAG 里 wiki_agent 直接连 wiki_agent,中间没有 LLM 节点。

2. **dispatcher 路径写的不是 DAG llm 节点**。dispatcher 调 `persist_assistant_message` 写的是 SessionDB 的 assistant 消息(带 token 统计),不是 DAG 的 `Call(role=llm)` 节点。

3. **legacy 路径没有 tool loop**。模型只能返回纯文本,不能调工具。

## 设计

### 核心原则

一次 `runtime.exec` = 一个 llm 节点。

llm 节点和 code 节点是同一个抽象:一次调用,进入时 running,结束时回填 output。内部发生了什么(tool loop 跑了几轮、调了哪些工具)都是内部过程,不拆成多个节点。

```
进入 exec  → 写 llm 节点 (status=running, output=None)
exec 内部  → agent_loop 跑 LLM + tool loop
             (工具的 code 节点 called_by 指向此 llm 节点)
exec 返回  → 回填 llm 节点 (output=最终回复, status=success)
```

### 调用树示例

wiki_agent 递归场景,统一后的 DAG:

```
llm (dispatcher 调 exec,模型决定调 wiki_agent)
  code wiki_agent d1 (工具执行)
    llm (wiki_agent 内部调 exec,模型又调 wiki_agent)
      code wiki_agent d2 (递归)
        llm (又一次 exec)
          code wiki_agent d3
            ...
```

每两个 code 节点之间都有一个 llm 节点,调用链完整。

### 统一路径

```
runtime.exec(content=[...])
  → 写 llm 节点 (status=running, output=None)
  → 构建上下文 (compute_reads + render_dag_messages + content)
  → 调 LLM,跑 tool loop 直到模型返回纯文本
      (tool loop 内部模型调的工具,其 code 节点 called_by 指向此 llm 节点)
  → 回填 llm 节点 (output=最终回复, status=success)
  → 返回文本
```

### 各层改动

**dispatcher**:不再直接调 agent_loop,改成调 runtime.exec。保留的职责:
- 写 user 节点
- 设置 session 上下文(ContextVar)
- 调 runtime.exec
- turn 级别善后(标题生成、compaction 触发)

```
process_user_turn (改后)
  → 写 user 节点 (不变)
  → 设 _store / _current_runtime (不变)
  → runtime.exec(content=用户消息)  ← 改这里
  → finalize
```

**runtime.exec**:统一为一条路径。
- legacy `call=my_func` 包装成轻量 provider adapter,统一走 `_call_via_providers`
- `_call_via_providers` 补上 llm 节点写入(配对:进入写 running,返回回填)
- 删掉 legacy 分支

**agent_loop**:不变。纯粹的"LLM 调用 + tool 执行"引擎,不关心 DAG。

## 现状详细分析

### 路径 1: 常规聊天 (dispatcher)

```
用户发消息
→ process_user_turn (dispatcher/__init__.py:96)
  → 写 user 节点到 DAG (db.append_message)
  → 设 _store ContextVar
  → _run_loop_blocking (dispatcher/__init__.py:640)
    → 构建 AgentContext (tools, system_prompt, history)
    → agent_loop([prompt], context, config) (agent_loop.py:232)
      → _stream_assistant_response → LLM 回复
      → 如果是 tool_use → _execute_tool_calls → tool.execute
        → 如果工具是 @agentic_function → wrapper 写 code 节点
        → 工具返回 → agent_loop 继续
      → 最终拿到纯文本回复
    ← 返回 final_text
  → persist_assistant_message (persistence.py:31)
    → 写 assistant 消息到 SessionDB (不是 DAG llm 节点)
  → finalize
```

### 路径 2: runtime.exec legacy

```
@agentic_function 函数体里调 runtime.exec(content=[...])
→ exec (runtime.py:789)
  → self._call(content) → 用户自定义函数,返回文本
  → _append_model_call_node(reply=...) → 写 llm 节点到 DAG
← 返回文本
```

### 路径 3: runtime.exec providers

```
@agentic_function 函数体里调 runtime.exec(content=[...])
→ exec (runtime.py:789)
  → _call_via_providers (runtime.py:1306)
    → 构建 AgentSession
    → session.run(current) → 内部跑 agent_loop (同路径 1 的代码)
    ← 返回 final assistant message
  → return _assistant_text(final)  ← 没有写 llm 节点!
← 返回文本
```

## 落地顺序

### 步 1-2(✅ 已完成）

| 步 | 做什么 | 验证 |
|---|---|---|
| 1 | exec 配对写 llm 节点(`_open_model_call_node` / `_close_model_call_node`),sync + async 两条路径 | ✅ wiki_agent session DAG 里出现 llm 节点 |
| 2 | `_call_via_providers` 里 session.run 之前把 `_call_id` 切到 llm 节点,tool loop 的工具 code 节点 called_by 正确指向 llm 节点 | ✅ `test_tool_loop_subcall_attributes_to_llm_node` |

### 步 3:删 legacy `call=` 分支

把 `Runtime(call=fn)` 包装成一个 provider model,统一走 `_call_via_providers`,删掉 `_call_fn` / `_uses_legacy_call` 和两条 legacy 分支。

| 步 | 做什么 | 文件 | 验证 |
|---|---|---|---|
| 3a | 新增 `CallableModel` adapter(sync+async,把 pi-ai messages 转回 content 调用户 fn,返回单条 AssistantMessage,无 tool loop;补回 `response_format`→prompt suffix) | 新建 `openprogram/providers/callable_model.py` | `pytest tests/providers/test_functions.py` |
| 3b | `Runtime.__init__` 里 `call=` → `self.api_model = CallableModel(call)`,callable runtime 强制 `toolset="none"` | `runtime.py` __init__ | `pytest tests/providers/test_functions.py` |
| 3c | 删 `_call`/`_async_call` 的 `_call_fn` 分支 | `runtime.py` | `pytest tests/agentic_programming/` |
| 3d | 删 `_uses_legacy_call` + exec/async_exec 两条 legacy 分支 | `runtime.py` | full `pytest tests/agentic_programming tests/providers` |

**关键发现(已验证)**:`_call_via_providers` 不忽略 content——`_render_history_messages(content)` 把 content 作为当前 turn(`runtime.py:1451`),无 store 时 `_build_pi_context(content)`(`:1456`)。所以 adapter 不需要特殊处理 content,AgentSession 会把 history+current 给 model,adapter 转回 content 调用户 fn。

### 步 4:stream_fn 注入(✅ 已完成);dispatcher 不折叠(已论证)

| 步 | 做什么 | 状态 |
|---|---|---|
| 4a | 把 `stream_fn` 参数穿过 exec → _call_via_providers → AgentSession.__init__ → AgentOptions,让 exec 可注入流 | ✅ `test_exec_stream_fn_injection` |

**dispatcher 不折叠进 runtime.exec(实测论证)**

最初设想让 `_run_loop_blocking` 改调 runtime.exec。实测推翻了这个方向:

1. **模型会分叉**:dispatcher 用 `_resolve_model(agent_profile, req.model_override)`(agent profile + 用户选的模型),而 attached runtime 是 `create_runtime()` 无参自动探测的,`api_model` 不一致。走 exec 会用错模型。
2. **上下文会冲突**:dispatcher 用 context-engine 准备好了消息(`prep.agent_messages`),exec 自己从 DAG render 历史,两套会打架。
3. **流式事件不兼容**:exec 的 `on_stream` 发 flat dict,dispatcher 的 `on_event` 要 webui envelope。
4. **最关键——会写重复节点**:试着在 dispatcher 里用 `_open/_close_model_call_node` 加一个 llm 节点,结果 DAG 里出现 `[user, assistant, assistant]`——因为 **dispatcher 的 assistant 会话消息(`persist_assistant_message`)本身就是顶层 LLM 调用的 DAG 记录**。再加 llm 节点就重复了。`test_dispatcher_integration.py::test_real_loop_text_only` 直接抓到这个重复。

**结论**:dispatcher 顶层 LLM 调用**已经**有 DAG 表示(role=assistant 的会话消息节点),工具调用挂在它下面。不需要也不应该再加 llm 节点。原始的"code→code 缺 llm 节点"问题只发生在 **`@agentic_function` 内部 exec 的 tool loop**,已被步 1-2 修复。dispatcher 保持现状。

dispatcher 和 exec 的关系不是"dispatcher 走 exec",而是**两个并列的 LLM 调用入口**,各自把顶层调用记进 DAG(dispatcher → assistant 会话节点;exec → llm 节点),共享下层的 agent_loop 引擎和 DAG 节点写入 API。这跟其他框架一致(OpenClaw 也有独立 dispatcher 层)。

### Agent 类:不动

`Agent` 在 exec 下层(exec → AgentSession → Agent → agent_loop),是循环驱动器不是平行 LLM 路径,只在 `session.py:94` 构造一处。折叠它等于重写循环,无收益。

### 事件层:无改动

`tool.before` 只在 `_execute_tool_calls`(`agent_loop.py:518`)一处 fire,所有路径都经过它。CallableModel 无内部 tool loop(用户 fn 只返回 str),不会重复/丢失事件。

### 风险

1. **dispatcher 测试 seam(最高)**:4a 把 stream_fn 穿过 4 层后,`_run_loop_blocking` 仍是 patch 入口,测试的 stream_fn 经 exec 流到 model。`test_dispatcher_dag_attach.py` 整体替换 `_run_loop_blocking`,不受影响。
2. **prompt-cache 前缀稳定**(4b):override 不能破坏 DAG-prefix 缓存。
3. **response_format 回归**(3a):`claude_call`/`gemini_call` 的 JSON-mode 靠 adapter 补回 suffix。

### 待更新测试

**Trivial(删 assert/override)**:`test_openai.py:61`、`test_anthropic.py:63`、`test_gemini.py:68`(删 `_uses_legacy_call() is False`);`test_decision.py:24`、`test_loop_options.py:153`、`test_dispatcher_dag_attach.py:89`(删 `_uses_legacy_call→True` override,留 `_call` override)。

**Behavioral(需重验)**:`test_runtime_exec_dag.py:34`、`test_functions.py` 的 `_mock_call`(按 content shape 分支,adapter 必须原样传 content)、`conftest.py` 的 `echo_call`/`noop_call`。

## 统一"记一次模型回复"的写入（保留新原语，删旧机制）

### 现状:同一件事两套写法

"记录模型回复"现在有两套**配对写节点**(开占位 running → 回填结果)的实现,按入口分:

| 入口 | 开占位 | 回填 | 实现 |
|---|---|---|---|
| dispatcher(聊天) | `insert_placeholder`(_turn_lifecycle.py:65) | `persist_assistant_message`(persistence.py) | 旧机制 |
| exec(代码) | `_open_model_call_node`(runtime.py:631) | `_close_model_call_node`(runtime.py:656) | 新原语 |

两套结构一模一样(open→close 配对),但旧的那套散在 dispatcher,新的在 runtime。这是"头尾都写两份"的根源。

### 关键事实(让统一变简单)

存储层**没有 `ROLE_ASSISTANT`**——只有 user/llm/code(`context/nodes.py:36-40`)。聊天回复**早就存成 `ROLE_LLM`**:`_msg_to_node` 把 `role="assistant"` 映射成 `ROLE_LLM`,`_node_to_msg` 读回来默认还原成 `"assistant"`(`_msg_adapter.py`)。exec 的 `_open_model_call_node` 写的也是 `ROLE_LLM`,读回来同样默认 "assistant"。

**两个原语在节点层早就用同一个 role。** 唯一差异是 dispatcher 多写了 4 样元数据,exec 的裸原语没写。序列化只有一个收口点 `_node_to_msg` → 前端读到的还是 "assistant"。

### 方案:升级新原语 → dispatcher 改调它 → 删旧机制

把 exec 的配对原语升级成**通用配对原语**(能装下 dispatcher 需要的字段),两个入口都用它,删掉旧的 placeholder/persist 重复机制。

统一原语必须保留这 4 样(否则前端/分支/计量会断):

| 字段 | 谁要 | 风险 |
|---|---|---|
| `extra.blocks`(thinking/text/tool 卡片顺序) | 前端气泡主体(conv-mapper.ts) | 最高 |
| token 列 + token_model | 计量 UI | 高 |
| `metadata.parent_id=user_msg_id` | 分支/fork/rewind 的 active-branch 重建 | 高 |
| `cancelled/completed` 终态(exec 当前写 success) | 用户停止时的部分输出 | 中 |

原语签名升级(可选参数,exec 不传、dispatcher 传):

```python
open_model_call_node(*, role="llm", parent_id=None, content_text="", model=None) -> node_id
close_model_call_node(node_id, *, reply, status="success", blocks=None, usage=None)
```

落地顺序:
1. 升级 `_open/_close_model_call_node`,加 `parent_id` / `blocks` / `usage` / `status` 可选参数(exec 调用不变,默认行为不变)
2. dispatcher 的 `insert_placeholder` 改成调 `open_model_call_node(role="assistant", parent_id=user_msg_id)`
3. dispatcher 的 `persist_assistant_message` 改成调 `close_model_call_node(blocks=..., usage=..., status="completed"/"cancelled")`——只保留字段组装,删掉自己的 append/update
4. 删 `_turn_lifecycle.py` 的 placeholder/error-fold 里重复的写节点逻辑
5. 验证:webui 端到端聊天(气泡、thinking/tool 卡片、token、停止、fork)全部正常 + 全量 pytest

零前端改动——因为 `_node_to_msg` 仍输出 "assistant",前端读的字段(blocks/token/parent_id)都还在,只是改由统一原语写。

## 相关文件

- `openprogram/agentic_programming/runtime.py` — exec / _call_via_providers / _open|_close_model_call_node(统一原语所在)
- `openprogram/providers/callable_model.py` — CallableModel adapter
- `openprogram/agent/agent_loop.py` — agent_loop / _execute_tool_calls
- `openprogram/agent/session.py` — AgentSession
- `openprogram/agent/dispatcher/__init__.py` — process_user_turn / _run_loop_blocking
- `openprogram/agent/dispatcher/persistence.py` — persist_assistant_message(改调统一原语)
- `openprogram/agent/internals/_turn_lifecycle.py` — insert_placeholder / fold_error(删重复写入)
- `openprogram/store/session/_msg_adapter.py` — _node_to_msg(序列化收口点,role 还原)
- `openprogram/agentic_programming/function.py` — @agentic_function wrapper
