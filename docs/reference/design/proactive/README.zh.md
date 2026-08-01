# Proactive Layer — 设计

给 OpenProgram 加一层"主动性":在 agent 干活的过程中，框架自己盯着，在该出手的时候
出手——拦下危险命令、提醒该补的测试、发现模型卡住了。用户没开口，框架也会动。

这一层的核心是**事件驱动**：agent 干活时发生的每件事（用户发消息、模型回复、调用工具、
工具失败、文件被改）都记成一条"事件"；你的主动规则不是焊死在某个固定位置，而是"订阅"
这些事件，事件发生时被唤起、做判断、决定要不要出手。

## 这层分两块：事件底座 + 主动性应用

- **事件底座**（地基，给整个框架用）：一条统一的事件流，谁都能订阅。proactive 只是它第一个
  消费者，webui、将来别的功能也能用。
- **主动性应用**（建在底座上）：规则（Policy）订阅事件流，在该出手时出手。

两块解耦。可以只做底座、先不做规则。

## 怎么读

**先读事件底座**（你最近在确认的就是这块——系统到底支不支持"在某时机做某事"）：

0. **[`event-reference.html`](event-reference.html)** —— **官方 API Reference**：所有事件类型
   （26 个，A/B/ws.frame 三类）逐一列清，每个带 payload 字段表、触发时机、源码 file:line；
   全部 API、三种用法（观察/拦截/发问）。可搜索、可展开。双击用浏览器打开。**查事件先看它。**
1. [`event-layer.md`](event-layer.md) —— **统一 Event 模型 + 这层在框架里的定位 + 框架图**。
   事件长什么样、两大类事件源（agent 干活 / 系统状态）、总线放哪、跟谁交互。
   **可视化版本：[`event-layer.html`](event-layer.html)**（真 SVG 架构图 + 事件流动画，
   双击用浏览器打开），md 是同内容的文字源。
2. [`framework-evolution.md`](framework-evolution.md) —— **框架里的信号怎么路由**。
   为什么让 webui 当路由点会留下缺口、总线当中枢后各角色是什么、各子系统往总线里放什么、
   哪些部分刻意不动。
   **可视化版本：[`framework-evolution.html`](framework-evolution.html)**。

**再读主动性应用**（建在底座之上的 proactive）：

2. [`overview.md`](overview.md) —— 跟着一个场景走一遍（模型想跑 `rm -rf`，框架怎么拦），
   规则、出手方式、状态等概念在故事里就地解释。
3. [`events-and-state.md`](events-and-state.md) —— 状态怎么从事件"累加"（fold）出来；
   这是规则能"记住过去"的原理。

**想动手改、讨论细节**：

| Doc | 讲什么 |
|---|---|
| [`execution-model.md`](execution-model.md) | 规则（Policy）怎么写；"挡路的"和"旁观的"两类有何不同 |
| [`policies-mvp.md`](policies-mvp.md) | 三条具体规则，当样板照着写新规则 |
| [`invariants.md`](invariants.md) | 框架要守的底线（主要是"别让框架自己触发自己、绕成死循环"） |

## 一句话回答几个你可能会问的问题

| 问题 | 答 |
|---|---|
| 这层是新框架还是补丁？ | 一个独立的层，但复用现有机制（事件总线、工具批准、后台任务），不另起炉灶 |
| 规则用什么写？ | 普通 Python 类，不是配置文件 / DSL。会写 Python 就会写规则 |
| 会不会很重？ | 范围**只有地基**——事件、状态、规则、出手。研究级的议题（防篡改、离线回放验证、对抗安全）在这个范围之外，放在 `_research_archive/` |

## 实现状态

事件底座已就位：总线已启用，两类源都在往里发，`tool.before` 带同步拦截点，webui 作为
普通订阅者消费总线。尚未构建的是 `execution-model.md` 和 `policies-mvp.md` 描述的
proactive 规则层。想看事件流：`OPENPROGRAM_EVENT_LOG=1 openprogram worker restart`，
发条消息，读 `/tmp/openprogram-events.jsonl`。
