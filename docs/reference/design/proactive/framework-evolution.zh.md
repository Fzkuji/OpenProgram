# 框架信号路由：一条总线当中枢

事件层（`event-layer.md`）定义了事件是什么。这篇讲它对整个框架的影响：谁来路由信号、
各子系统往总线里放什么、以及这套设计刻意不碰哪些部分。

## 1. 总线要解决的问题

![六套机制各自为政](diagrams/framework-asis.svg)

没有总线时，信号存在的目的只有一个：让前端看到。这个目的把每个信号都硬连到 webui server 的
`_broadcast`——task_status、channel_turn、skills:changed 各用各的 JSON 直连，agent 事件经
dispatcher 回调链到达。webui 是个 UI 组件，让它当路由点就等于把 UI 关注点放进了框架的信号
链路。比耦合更麻烦的是缺口：auth 把自己的事件建模对了却几乎没人订阅，memory 和文件改动
根本不发信号，hooks 的返回值被丢弃（能观察不能拦截），EventBus 闲置。

代价落在任何一个新消费者身上。proactive 是第一个：没有总线，它得分别对接五六套机制，而有
些时机根本没有信号可以对接。

## 2. 总线当中枢：三个角色

![一条总线当中枢](diagrams/framework-tobe.svg)

| 角色 | 谁 | 这个角色是什么 |
|---|---|---|
| **中枢** | EventBus | 唯一路由点：进程级单例、统一 Event 格式、按类型订阅 |
| **订阅者** | webui server | 一个普通订阅者：订阅总线，转发前端 WS |
| **订阅者** | proactive 及未来任何功能 | 同样只是订阅者，一行 `subscribe(types=…)` 接入 |

总线旁边有一处同步问询点 `tool.before`——全框架唯一的拦截位。它复用 `_approval`，对 subagent
同样生效。其余一切与总线的交互都是异步观察。

## 3. 各子系统往总线里放什么

| 子系统 | 它在总线设计里的角色 |
|---|---|
| agent loop | 保留内部 AgentEvent 流，同时在关键节点 emit 到总线；`tool.before` 带同步问询 |
| dispatcher | 保留 on_event 回调链，同时 emit `user.prompt_submitted` 等事件 |
| task runner | emit `subagent.*`；前端广播由 webui 订阅转发 |
| auth | 内部不动；一段桥接把 AuthEvent 翻成 Event emit 进总线 |
| context | 在已有回调里顺手 emit `context.*` |
| channels | emit `channel.*`；前端直连并存 |
| memory | 处理起止 emit，把定时 poll 包装成事件 |
| 文件改动 | 在 `checkpoint_before_edit` 处 emit `file.changed` |
| plugin hooks | 内部统一走总线；hooks 保留为插件 API，包一层订阅 |
| webui server | 订阅总线，而不是接收各路直连 |
| EventBus | 中枢本身：按类型订阅 + 单例访问 |

auth 的桥接值得单说，它是"子系统自己已经把事件建模对了"这一类的通用做法：子系统保留自己的
机制，一段桥接把它的事件翻译成统一格式。auth 内部不需要知道总线的存在。

## 4. 新旧路径并行

总线**并行**于现有路径运行，而不是一次性替换掉它们。每个源独立迁移，每次迁移都能单独验证、
单独回退。

两个性质让这件事成立。把一个源接进总线是纯加法：总线收到一份拷贝，旧路径原样跑，行为不变。
把 webui 切成订阅者确实动了旧路径，所以它用透传信封——webui 广播出去的帧和原来逐字节一致，
前端不用改、也感知不到切换。这也是设计选信封而不选影子比对的理由：帧完全相同就不需要比对。

## 5. 刻意不动的东西

不动什么和动什么一样重要。总线设计不伸手进这些地方：

- dispatcher 的七阶段 turn 编排，以及 `process_user_turn` 的对外签名
- session git DAG 存储与 contextgit
- TaskRunner 的线程池模型
- `ApprovalRegistry` 批准机制——问询点复用它，而不是改写它
- AuthStore 自身——用桥接，不修改
- 前端 WS 协议——webui 广播的帧不变，所以对前端透明

## 实现状态

总线已启用并接入 A 类源，`file.changed` 与 `tool.before` 同步问询点已就位，B 类源已桥接
（auth 经 `events/bridges.py`，context / channels / memory / webui watcher 在源头 tap），
webui 已是总线订阅者——五个外部源（task runner、sub_agent、worktree、functions watcher、
channels）改为 emit `ws.frame` 事件，不再 import webui。剩余工作是 proactive 规则层，
它消费总线，不新增源。

> 可视化版本：[`framework-evolution.html`](framework-evolution.html)。
