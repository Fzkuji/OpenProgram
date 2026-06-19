# 历史记录的数据结构 —— 一整张图(最终定稿)

Status: **decided（最终模型，开始实现）** · Created: 2026-06-19 · Finalized: 2026-06-20

> 本文定**历史记录存成什么数据结构 + 上下文怎么从中检索 + 怎么画**。
> 模型选型理由见 `docs/research/execution-trace-model-selection.md`(span 概念 + 创新点)。
> 实现合并计划见本文第八节。

## 一、最终结论(一整张图)

整个 session = **一整张 DAG**(不切成多个独立 trace)。

- **节点(span)**:user / llm / code 三种 role。一种数据结构,大模型调用永远是同一种 llm 节点,不因"被用户触发"还是"被函数触发"分裂。
- **边**:`called_by`(谁调出谁),有向、无环。唯一的结构边。
- **共享 seq**:整张图一套单调递增 seq(全局时间序)。
- **顶层多轮**:平级节点(都 `frame_entry_seq=-1`),**不串成链**(不用 parent_id 首尾相接)。
- **轮内嵌套**:函数调用是 called_by 子树。
- **上下文**:在这**一整张图**上,按 `seq + frame + expose` 检索(`compute_reads`)。

### 为什么必须是一整张图(不是独立 trace + session)

业界(LangSmith/Datadog)把每次请求切成独立 trace、用 session 标签归类——因为它们是**事后观测**,上下文不读回。我们不行:**我们的 `compute_reads` 靠"同一张图、同一套 seq"检索历史**。一旦切成独立图,第 N 轮的 llm 就看不到前面几轮(跨图检索不到),顶层对话连贯性断裂。所以:**一整张图,共享 seq,是硬约束。**

### 关键区分:同一张图 ≠ 串成链

这两件事之前被混淆,其实正交:

| | 含义 | 要不要 |
|---|---|---|
| **同一张图** | 所有节点共享一张图、一套 seq | **要**(否则 compute_reads 跨不过去) |
| **串成链** | 第2轮 parent_id 指第1轮回复(首尾相接) | **不要**(逻辑反:像"大模型调用了下一个用户";脏) |

正确:顶层多轮是**同一张图里的平级节点**(都 frame=-1),靠 seq 排序 + compute_reads 全可见,**不需要串链**。

```
一张图(共享 seq):
seq0 user1   顶层(frame=-1)
seq1 llm1    顶层      called_by=user1(轮内:llm 被这轮触发)
seq2 user2   顶层      平级,不指向 llm1 ← 不串链
seq3 llm2    顶层      called_by=user2
seq4 user3   顶层
seq5 llm3    顶层      called_by=user3
   compute_reads(frame=-1) → 取所有 seq<5 顶层节点 → 前两轮全可见 ✓
```

## 二、节点结构

```
Node(span):
  id           唯一编号
  seq          单调递增整数,全局时间序(排序唯一依据)
  created_at   wall-clock(给人看,不排序)

  role         "user" | "llm" | "code"   ← 只决定渲染,不分裂本质
  name         模型 id / 函数名 / 用户名

  input        prompt / 函数参数 / None
  output       回复 / 返回值 / 用户文本
  status       running | success | error | cancelled

  called_by    谁调出我(父 id)。顶层 user 为空。
  attributes   元信息(token/model/source/expose…);LLM 叶子字段对齐 gen_ai.*
  reads        这次 LLM 调用读了哪些节点(引用,渲染上下文用;不是结构边)
```

### 三种 role

| role | called_by | input | output |
|---|---|---|---|
| user | 空(顶层根) | None | 用户文本 |
| llm | 触发它的节点(顶层=本轮 user;函数内=那个 code 节点) | system(可选) | 模型回复 |
| code | 调它的节点(模型 tool_use → 那个 llm 节点) | 函数参数 | 返回值(返回=填这字段,不画返回边) |

## 三、循环不是节点

for/while 循环**不占节点**(执行轨迹不记代码结构)。循环跑 N 次 = 同一个父下 N 个兄弟(按 seq 排)。可视化时重复多了折叠成 ×N(纯显示,数据仍是 N 个节点)。

- 顶层多轮(聊天 while)= 顶层 N 个平级 user/llm
- 函数内 for = 那个 code 节点下 N 个子

## 四、上下文检索(已实现,沿用)

`compute_reads(graph, head_seq, frame_entry_seq, render_range)` —— 在**一整张图**上选 reads:

- **顶层聊天**(frame=-1):所有节点 in-frame,**全可见**(累加)→ 前面所有轮的对话都喂进去。
- **轮内函数**(frame=该 code 节点的 seq):pre-frame(到根的历史)+ in-frame(自己内部进展)可见;别的函数内部按 `expose` 弹出(io 默认只露输入输出)。

**顶层 = 全加(无层级选择,本来就该平);轮内 = frame+expose 层级选择。** 同一个 compute_reads,两层各取所需。这套机制**现状已支持**,不用改。

## 五、两种视图(同一份数据)

| 视图 | 怎么走 |
|---|---|
| 聊天流 | 顶层 user + 其 llm,按 seq 排,函数嵌套折叠 |
| 调用树 | 沿 called_by 全展开;循环兄弟折叠 ×N |

## 六、fork / 分支

版本派生,跟 called_by 正交。用独立的 `attributes.forked_from`(指被派生节点)。active 分支 = 沿同一版本线 + seq 取,排除岔掉的。**不复用 called_by,也不靠对话链。**

## 七、创新点(护城河,别 claim 零件)

**可 claim 的是融合**:记录的调用树**本身就是运行时上下文**,每次调用按"帧作用域 + per-function expose"查询它,节点全部保留(供 fork/replay)。无框架做全(LangGraph 有保留图+fork 但无读回作上下文/无 pop;StackMemory 有栈作用域但靠搜索+丢摘要)。**别单独 claim "ContextVar 调用栈追踪""图 fork"——那是常见的。** 详见调研文档。

## 八、实现:两套合并(分步,带决策定论)

现状两套并存:
- **聊天**:`process_user_turn` → `engine.prepare`(`_assemble_messages`,真 ToolCall/ToolResult 链 + aging + 附件 + 压缩)→ `agent_loop`;记录 `insert_placeholder`/`persist_assistant_message`(写 token 列 + blocks + parent_id)。
- **exec**:`_open_model_call_node` → `compute_reads` + `render_dag_messages` → `_close_model_call_node`。

目标:统一成一套——都走"一张图 + compute_reads + 统一记录原语"。

### 关键决策(动手前必须定的,已查实)

**决策 0:parent_id 链不删 —— 存储有链,检索不看链。**
之前想"顶层改平级、删 parent_id 链"会崩:`get_branch`(session_store.py:742)沿 parent_id 取分支,**fork/rewind/主干遍历(session_store.py:800)/压缩(engine.py:314)/删分支(branch.py:443)全依赖它**;branch.py 没有 forked_from,fork 就是新节点 parent_id 指向岔点。
**定论**:存储层**保留 parent_id 链**(分支骨架,不动);"顶层平级可见"在**检索层**实现——`compute_reads` 本来就不看 parent_id、只按 seq(nodes.py:586)。同一张图 + seq 检索(平级)与 parent_id 分支骨架共存,正交。**这正是"同一张图 ≠ 串成链":存储可有链(给分支),检索不看链(按 seq 平级)。**

**决策 1:两种 code 节点必须区分渲染(合并第一坑)。**
- 模型 tool_use 的 code 节点:有 tool_call_id(现藏在合成 id `{assistant_msg_id}_t_{tid}` 里,dispatcher:462)→ **必须** ToolCall/ToolResult(否则 provider 拒孤儿 tool_use)。
- 代码直调 @agentic_function 的 code 节点(function.py:132):**无** tool_call_id → user/assistant 文本对(现状 render.py:100 对的)。
**定论**:给节点加显式 `metadata.tool_call_id`(模型 tool_use 才有);`render_dag_messages` 按它分两路。ToolCall 必须**挂在所属 llm 节点的 AssistantMessage.content 里**(现状 render 每节点独立 emit,对 ToolCall 是错的——要按 called_by 把 tool 节点归到其 llm 节点内)。旧 session 兼容:`{id}_t_{tid}` 仍可读出 tid。

**决策 2:统一 status 词汇。** 聊天用 completed/cancelled/error,exec 用 success/error。统一成一套(completed/error/cancelled),否则 `_node_to_msg`(_msg_adapter.py:117)默认 + 流式恢复 UI 会误判 exec 节点。

**决策 3:统一记录原语签名。**
`open_call_node(role, name, system, content, called_by, reads, parent_id=None, tool_call_id=None, source=None, status="running") -> id`
`close_call_node(id, output, status, usage=None, blocks=None)`
load-bearing 字段(不能丢):blocks+tool_calls(前端气泡,branch.py:216)、token_* 列(计量,persistence.py:122)、parent_id+called_by(fork)、status(取消)。`_close_model_call_node` 现在丢了 usage/blocks → 聊天切过去前必须补。

**决策 4:聊天换 compute_reads 前,render 必须补 5 项**(vs `_assemble_messages`):
(a) ToolCall/ToolResult 链(决策1);(b) ToolResultMessage 类型(现只 User/Assistant);(c) 图片/附件注入(现只 TextContent from output);(d) 工具结果 aging(engine.py:241 的 [aged] stub);(e) 压缩/摘要节点(engine.py:600 的 sm_/summary)。每项都是硬前提。

**决策 5:自动重试抽成包住 `run_once` 的策略函数。** `_run_with_retry`(session.py:178)依赖 Agent 对象;dispatcher 调的是裸 agent_loop(dispatcher:917)+ asyncio.Event 取消。抽出"重试策略(可重试判定+退避+重跑+丢上次 assistant)"包住一个 `run_once()→final AssistantMessage`,dispatcher 的 `_drain`(dispatcher:870)当 run_once。坑:重跑会重发 prompt → 第二次须 continue-from-context(仿 session.py:230);placeholder/persist 须在重试循环**之后**跑一次,不是每次。

### 落地顺序(依赖排序,每步独立验证)

| 步 | 做什么 | 独立性 | 验证 |
|---|---|---|---|
| 1 | 节点加 `tool_call_id` 判别 + render 分两路(ToolCall 归到 llm 节点内) | **独立**·加性(无 id 则走文本对,不破现状) | test_render_dag_messages + 新增 ToolCall 归组用例 |
| 2 | 统一 status 词汇 + close_call_node 补 usage/blocks 字段 | 独立 | _node_to_msg status 测试 |
| 3 | render 补 5 缺口(工具链/ToolResult/图片/aging/摘要) | 依赖 1+2 | golden 对比 render vs _assemble(带工具+图片+摘要的 fixture) |
| 4 | 聊天上下文换 compute_reads(**保留 parent_id 骨架**) | 依赖 3 | test_dag_session_db_branches + rewind + 带工具 turn 往返一致 |
| 5 | 统一记录:聊天 persist 改调 open/close_call_node | 依赖 2 | test_dispatcher_integration + 计量列在 + 前端 blocks 在 |
| 6 | 自动重试抽出来包住统一 loop | 独立·最后 | 注入 errors-once 的 stream_fn 重试测试 |
| 7 | 可视化按新模型画(顶层平级 + 轮内嵌套 + 循环折叠 ×N) | 独立·纯前端 | 浏览器自检 |

步 1/2/6/7 可独立发;3→4 和 2→5 是耦合主线。高风险区(4/5 动 dispatcher 持久化)单独专注做。
fork 现状靠 parent_id(决策0 保留),**不需要为本次合并改 fork**;forked_from 是更远期的概念清理,本次不做。

## 相关文件
- `openprogram/context/nodes.py` — Call + compute_reads(检索,已支持一张图)
- `openprogram/context/render.py` — render_dag_messages(S1 改这里)
- `openprogram/agent/dispatcher/__init__.py` — get_branch / agent_loop 入口(S2/S3)
- `openprogram/agent/dispatcher/persistence.py`、`agent/internals/_turn_lifecycle.py` — 聊天记录(S4)
- `openprogram/agentic_programming/runtime.py` — exec / _open/_close_model_call_node(S1/S4)
- `openprogram/store/session/session_store.py` — get_branch / 存储(S2)
