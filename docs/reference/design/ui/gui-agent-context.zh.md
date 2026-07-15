# GUI agent — 调用结构与上下文流

本文档记录 `gui_agent` 在当前 `expose` / `render_range` 默认语义下的上下文是怎么流动的，以及每个 `@agentic_function` 上的装饰器参数为什么这么设。

参考：
- 装饰器语义：[`agentic-programming/function-metadata.md`](../../../capabilities/agentic-programming/writing-functions/function-metadata.md)
- render_context 实现：`openprogram/context/nodes.py`
- 代码：`openprogram/functions/agentics/GUI-Agent-Harness/gui_harness/`

## 1. 调用结构

```
gui_agent(task)                   ← 顶层 @agentic_function (no render_range)
  └─ loop N 次：
     gui_step(task, feedback)     ← @agentic_function 编排 (no runtime.exec)
       ├─ observe()               Python: 截图 + 检测组件 + 识别状态
       ├─ verify_step(...)        @agentic_function LLM leaf
       ├─ plan_next_action(...)   @agentic_function LLM leaf
       └─ dispatch_action(...)    Python: 执行 plan 选的动作
     ↓ return dict(goal, action, target, success, error, ...)
     ↓ 作为下一个 gui_step 的 feedback 传入
  └─ conclusion(task, ...)        @agentic_function LLM leaf
```

`gui_step` 不直接调 `runtime.exec`，它的作用是把四个阶段串起来。所有 LLM 调用发生在 `verify_step` / `plan_next_action` / `conclusion` 这三个 leaf 函数里。

## 2. 各函数的 render_range 配置

| 函数 | render_range | 理由 |
|---|---|---|
| `gui_agent` | 不设（用默认） | 顶层，应该看到完整对话历史 + 自己 frame 内所有 gui_step 的 io。`callers=None`（全保留 chat 历史）+ `subcalls=-1`（gui_step 链自然累积）正好是想要的 |
| `gui_step` | 不设 | 自己不调 `runtime.exec`，render_range 对它没有任何实际意义 |
| `verify_step` | `{"callers": 0}` | 一次快照判断"上一步做没做成"。所需信息（前一步 goal/action/target/outcome、当前 screenshot、本步检测到的组件）全部通过 `content=[...]` 显式塞进去。`callers=0` 把上层 chat 历史和之前的 gui_step 链全墙掉，避免淹没快照判断 |
| `plan_next_action` | 不设（用默认） | **planner 必须看见历史**才能做出非重复决策。默认 `callers=None` 让它看到 task 描述 + 之前所有 gui_step 的 io（goal/action/target/success）+ 本轮 verify 的 io。`subcalls=-1` 在 leaf 里没作用（leaf 没有 in-frame 节点） |
| `conclusion` | `{"callers": 0}` | 总结要 ground 在最终屏幕状态，不让 step-by-step 叙述污染。所需信息（task、completed、steps_taken、final screenshot）显式塞进 `content=[...]` |

辅助 leaf（component_memory 里的几个、`learn`、`observe`、`general_action`）都用 `{"callers": 0}` —— 它们是独立判断器，输入都通过 `content=[...]` 给齐，不需要对话上下文。

## 3. 关键设计点

### plan_next_action 看见什么

从 plan 第 5 步的视角，`render_context` 的输入：

- `frame_entry_seq` = plan_next_action #5 这个 code 节点的 seq
- `head_seq` = DAG 当前最大 seq
- `render_range` = `None`（用默认 `callers=None, subcalls=-1`）

走 render_context：
- pre-frame = 所有 seq ≤ frame_entry_seq 的节点 = 顶层 chat 那条 user 消息、gui_agent 的 code 节点、gui_step #1..#4 的 code 节点（含各自 io）、本轮 gui_step #5 的 verify_step code 节点（含 io）、observe Python 结果对应的节点（如果走 DAG 的话）
- in-frame = plan_next_action #5 自己 frame 内的节点 = 空（leaf 还没发 exec）
- pre-frame 不截断；in-frame 不截断（也没东西可截）
- expose 过滤：每个 gui_step 是 `expose="io"`，所以 gui_step 的 io 节点保留、gui_step 内部的 verify_step / plan_next_action 的 LLM 调用被藏掉

最后 plan_next_action #5 的 prompt 里有：

```
user: <原始 task>
... 之前 chat 历史 ...
[gui_step #1] input={...} output={"goal": "open Firefox", "action": "click", "target": "Firefox icon", "success": true}
[gui_step #2] input={...} output={"goal": "open url bar", "action": "click", ...}
[gui_step #3] input={...} output={...}
[gui_step #4] input={...} output={...}
[verify_step #5] input={...} output={"step_succeeded": true, "observation": ...}
[plan_next_action #5] input={...}   ← 自己
```

planner 因此能看到"前四步都做了什么、最近一步 verify 怎么判的"，做出不重复的下一步决策。

### verify_step 为什么相反

verify_step 是被动判断："上一步做的事情，从当前截图看成功了吗？"判断材料完全是局部的：

- 上一步 feedback dict（goal/action/target/success/error）— 已经通过 `content=[feedback_text]` 塞进
- 当前 screenshot — 已经通过 `content=[{"type": "image", ...}]` 塞进
- 当前检测到的组件 — 已经通过 `content=[component_info]` 塞进

如果让它看完整 chat + gui_step 链，反而会引入噪音让"上一步是否成功"这个简单判断被宏观叙述带偏。所以显式 `callers=0` 墙掉。

### conclusion 同理

总结要写出"最终屏幕上 visibly 是什么"，不要写"step 3 干了什么、step 7 又干了什么"那种过程叙述。`callers=0` + 在 prompt 里硬约束"用屏幕上具体可见的文字"。

## 4. 跟旧版本的差异

之前的代码用了 `{"callers": 0, "subcalls": 0}` 全墙策略，再通过 Python 显式构造 feedback dict 一层层往下传。问题是 planner 真的只看得到上一步的 feedback，看不到完整 trace，会出现重复执行同样动作的 bug。

现在的设计：

- planner 走 DAG 默认行为天然看见历史（不再需要显式累加）
- 隔离需要的 leaf（verify / conclusion / 工具型判断器）显式 `callers=0`
- 不再有"top-level 是特例"的分支 —— gui_agent 顶层和它内部的 gui_step 都走完全相同的 render_context 代码路径

旧的 "collapsed 模式提议"、"hidden 副作用"、"子函数 io 冗余"等分析都已经不适用：
- 旧 "io 漏点：露 code 子调用"分析过时——`expose="io"` 现在的语义就是 frame-自己的 input/output 露、frame 内部的 LLM 藏；嵌套子函数自己再用自己的 expose 决定它的 io 露不露
- 不再需要 "collapsed" 模式——`expose="io"` 默认就把内部 LLM 藏好，子函数的 io 在它自己的 expose 决定下要么露要么藏，没有"既要露 io 又要藏嵌套孙子"的中间需要

## 5. 改完后需要观察什么

- 长 task 跑下来 plan_next_action 的 prompt 会不会因为 gui_step 链太长而过大。如果有 token 压力，给 `plan_next_action` 或 `gui_agent` 加 `render_range={"callers": N}` 显式截断最近 N 个 gui_step
- screenshot 是 image 节点，N 张大图会撑爆 context。可能要走"压缩老 screenshot 为文字描述"路径，但那是 prompt 层的优化，不是 render_range 层的事
