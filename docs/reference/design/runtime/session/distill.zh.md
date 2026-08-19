# Distill——把会话变成可复用流程

一个会话记录了一次已完成的工作。`read_conversation` 将这份记录提供给 Programs 和用户自定义 skill。OpenProgram 不再随包提供默认 distill skill。

保留的产品能力是转写器。产品级复用工作流应实现为 Program；如果用户需要的是模型指令，也可以自行编写 skill。

## 组成

| 部件 | 位置 | 做什么 |
|---|---|---|
| 转写器 | `openprogram/store/session/transcript.py` | `render_read_conversation()`——把会话的一条分支渲染成 LLM 可读的纯文本 |
| 模型可调工具 | `openprogram/programs/functions/vanilla/read_conversation/` | `read_conversation`——转写器的 `@function` 封装，缺省读当前会话 |
| 产品文档 | `docs/capabilities/distill.md` | 面向用户的说明 |

## 转写器

`get_branch` 返回的是对话链——由 `predecessor` 串起的 user/assistant 节点。工具调用和函数调用节点不在这条链上，它们通过 `caller` 挂在发起它们的那个 assistant 轮次上（[DAG overview](../dag/overview.md)）。转写器把两者合起来：走一遍分支，在每一轮下面打印 `caller` 指向它的那些调用。这和 `graph_builder` 给网页 DAG 用的是同一条边，只是读成散文而不是坐标。

签名：

```python
render_read_conversation(
    session_id,
    head_id=None,               # 缺省：会话的活跃 head
    start_turn=0,               # 1 起、含端点；0=第一轮，负数从尾部数
    end_turn=0,                 # 1 起、含端点；0=最后一轮，-1=最后一轮
    include_function_calls=True,
    max_chars=60_000,
    store=None,                 # 缺省：agent.session_db.default_db()
) -> str
```

`start_turn`/`end_turn`按转写里打印的1起`[N]`序号选一段闭区间，语义对齐Python切片，负数从尾部数（`start_turn=-10`即最后10轮）。切片保留全局轮号，翻页时序号连续可对照；选了子范围时头部写成`turns 37-52 of 120`，空范围返回一行提示而不是空转写。

store 走 `default_db()` 而不是写死 `~/.openprogram` 路径，这样绑定到项目的会话能通过同一套定位逻辑解析。`store` 参数是给测试用的。

每条调用打印名字、成功或 `FAILED` 标记、截断后的参数、截断后的结果。失败的调用保留而不过滤：一次失败加上它的修正，正是"坑"在记录里的样子，也是蒸馏中最有价值的材料。

两类节点会被显式标注而不是当普通轮次处理——因为这两种情况下，读者会从表面内容得出错误结论：

- **压缩摘要**（`context/summary`）代表一段被折叠的范围。标注出来，读者才知道那里是细节被丢了，而不是会话本来就单薄。
- **spawn 分支根**开启一条子分支。标注出来，嵌套 agent 的工作才不会被当成主线读。

截断分两层。单字段上限防止一次失控的工具结果（读了个大文件）把周围的推理挤掉；总预算切在最后一个放得下的完整轮次上，并写明第一个被丢的轮号（`re-read with start_turn=N to continue`），被截断的转写不会看起来像完整的，读者也能从断点接着翻。

`scripts/dag_dump.py` 仍是调试视图，看节点 id、lane、tier。两者不重叠。

## 会话发现复用已有工具

`list_agents`（`programs/functions/send_message/`）已经能列出会话 id 和 `SID:HEAD` 分支端点。这正是 `read_conversation` 要的参数，所以不新增列表工具。工具接受整串 `SID:HEAD` 传进 `session_id` 并自行拆分，因为那就是 `list_agents` 打印的形式。

## 判断放在 skill 里

蒸馏本身是判断任务：决定转写里哪些能泛化、哪些只属于那一天。这里没有任何确定性可言，所以不存在 `distill()` 函数——skill 正文告诉 agent 要提取什么（目标、前置条件、步骤、决策点、坑），它用自己的 `Write` 工具写出文件。

这沿用 agentic programming 里删掉 `create()` / `edit()` / `improve()` 封装时定下的先例：函数体全部内容就是一次 LLM 调用加一次文件写入的，这层 agent 不需要。

旧版默认 distill skill 曾负责选择产出形态；该默认 skill 现已退役。产品级复用流程应实现为 Program，用户仍可自行编写外部 skill。

## 产物落进既有 skill 管道

用户自定义 skill 写到 `~/.openprogram/skills/<name>/` 或 `<cwd>/skills/<name>/`——这是加载器合并的四个来源中的两个。因此它不用重启就生效（watcher 热重载），并自动可以用 `/<name>` 调用（`commands/_skill_adapter.py` 把每个被发现的 skill 投射进 slash command 注册表）。

这就是这个功能不需要自己的子系统的原因：存储、发现、重载、调用路径全都已经在了。蒸馏要做的只是把文件写到对的位置。

## 修订补上闭环

蒸馏覆盖精炼，不止首次生成。skill 正文要求 agent 动笔前先找同一主题的既有 skill——目标和前置条件重合才算，名字碰巧像不算——找到就原地修订：保留仍然成立的，替换被这次会话证伪的，把新的决策点和坑合并进流程里。蒸馏出的 `@agentic_function` 遵循同一规则：改本体而不是复制，名字保持稳定，调用方不受影响。

这就是 record → replay → refine 循环里的 refine 一环。用起来失败的 skill 走同一条路修——失败的那次运行本身就是可蒸馏的会话——抱怨式说法（"这个 skill 不好用，按这次的经验改一下"）在 skill description 的触发语里，不需要单独机制就能路由到这里。

修订不新增机制。找既有 skill 用加载器的查找位置和 `openprogram skills list`；改文件用 agent 自己的工具；历史在 git 里，skill 正文禁止在散文里留变更记录。和蒸馏本身一样，修订是 skill 承载的判断，不是代码。

## 命名

产品词是 **distill**，不是 "workflow"。在本仓库里 "workflow" 已经指 agentic program——一个预置的完整 agent harness，文档在 `docs/capabilities/workflows/`。蒸馏出的流程是更小的东西：从一次会话里提取的知识，不是一个分发的程序。复用这个词会把同一个文档 Tab 里并排出现的两个概念混成一个。

## 实现状态

已按本文实现。
