# 记忆子系统

OpenProgram 如何让 agent 跨会话"记住"事情。

> 本文覆盖记忆子系统的全貌：目标的两级架构、当前在跑的线性总结链，以及
> 两者之间的路径。实体层的 git 底座细节见
> [`git-as-entity-memory.md`](git-as-entity-memory.md) 和
> [`entity-memory.md`](entity-memory.md)；抽象层的 Timeline / Graph / Core
> 存储见 [`virtual-memory.md`](virtual-memory.md)。
>
> 路径口径：所有状态都在 `~/.openprogram/`（= `get_state_dir()`）下；命名
> profile 用 `~/.openprogram-<profile>/`。

## 为什么需要它

原始的 LLM 在一段会话结束后会忘记一切。每次新对话都从零开始，于是用户
不得不一遍又一遍地复述相同的事实（"我是产品经理，请避免使用术语"、
"项目位于 `~/Projects/foo`"）。记忆子系统通过读取每一段已结束的对话、
提炼出持久的事实、并把其中最重要的事实回填到下一段对话的提示词中，来
解决这个问题。

我们关心两个产品层面的特性：

1. **模型无需提示就能拿到正确的事实。** 当你打开一段新对话时，你稳定
   的偏好以及项目稳定的事实已经在模型的工作记忆中——无需手动执行
   `/remember`。
2. **存储保持小巧且可审阅。** 记忆是磁盘上的纯 Markdown 文件，人类可读，
   便于手动编辑或清除。没有不透明的向量存储，没有微调后的权重。

## 架构：实体层 + 虚拟层

记忆分**实体层**（git 存的、不可变、完整的真实历史）和**虚拟层**（从实体提炼的、
紧凑的、带指针的索引）。LLM 调用时**只注入虚拟层**；需要原始细节时，LLM 顺着
虚拟层里的 **provenance 指针**，用工具自己导航回实体层去取。

```
        ┌──────────────────────── 实体层 (raw, git, 完整) ────────────────────────┐
        │   Session-Git                          Project-Git                        │
        │   每会话一个 repo                        绑用户工作目录 (真实代码/文档仓)      │
        │   每 turn 一 commit                      agent 改文件 → 自动 commit          │
        │   · 绑了项目 → <项目>/.openprogram/sessions/<id>/                          │
        │   · 随手聊   → <state>/sessions/<id>/   (默认项目仅逻辑标签, 无独立 repo)     │
        └────────────────────────────────┬─────────────────────────────────────────┘
                                          │  持续提炼 (distillation), 带 provenance
                          ┌───────────────┴───────────────┐
                          ▼                               ▼
              ┌────────────────────┐          ┌────────────────────┐
              │  时间轴 (Journal)    │          │  知识图谱 (Wiki)     │   ← 虚拟层 (derived)
              │  "何时发生了什么"     │          │  "实体之间什么关系"   │      每条都带指针
              │  bi-temporal         │          │  bi-temporal edges   │      回指实体层
              └──────────┬──────────┘          └──────────┬──────────┘
                         └──────────────┬─────────────────┘
                                        ▼
                              ┌──────────────────┐
                              │   召回 (recall)    │  只把虚拟层注入 LLM context
                              │   只注入虚拟        │  LLM 看到指针 → 用工具导航回实体层
                              └──────────────────┘
```

### 为什么抽象层要直接读实体层

线性总结链（`raw chat → 抽 0-10 facts → journal → wiki → core`）每一层都丢信息，
而且抽象层是从上一层的有损总结来的，不是从源头来的。这样实体层和抽象层就成了
两套不相通的东西。本设计让实体层成为抽象层唯一读取的数据源，并让每条派生记录
都保留一个指回源头的指针。

### 跟主流框架对比

| 框架 | 实体层 | 抽象层 | 召回方式 | 时间维度 | 知识图谱 |
|---|---|---|---|---|---|
| Claude Code | CLAUDE.md + 会话 | auto-memory MEMORY.md (索引+topic) | 注入索引，topic 按需读 | 无 | 无 |
| OpenClaw | MEMORY.md + 日记 | 同上 + wiki 插件 | 注入 + 语义搜索 | 无 | 弱 |
| mem0 | — | 向量 DB | RAG 切块灌入 | 仅写入时间 | 无 |
| Letta/MemGPT | 对话历史 | tiered (core/recall/archival) | LLM 工具搬运 | 部分 | 无 |
| Zep/Graphiti | — | temporal knowledge graph | 图查询 | bi-temporal | 有 |
| **本设计** | **git (session+project)** | **时间轴 + 知识图谱** | **注入虚拟，LLM 导航回实体** | **bi-temporal** | **有** |

### 本设计的特点

1. **Git 作为 episodic memory 的底座**。实体记忆不是自研存储，直接用 git：commit
   不可变，记录无法被篡改；log = 时间线；checkout = 时光机；branch = 探索过的
   分支；而且 agent 能用标准工具（`git log` / `grep` / `diff`）自己读。可审计、
   可复现、可回溯。

2. **Provenance-pointer 索引，而非替代**。虚拟层不取代实体层，而是给它建一个
   **带坐标的导航地图**。每条虚拟记忆都挂一个指针 `(project, session, commit,
   timestamp)`，指回它在实体层的出处。这解决了"有损总结丢上下文"的根本
   问题——**任何时候都能顺着指针钻回源头记录**。

3. **LLM 自导航召回（map → territory），而非 RAG 灌块**。传统 RAG 把相关 chunk
   切出来塞进 context，污染上下文且丢结构。本设计只注入紧凑的虚拟地图，LLM 读到
   "2026-05 在项目 X 修了 Windows bug，完整历史在 session local_13d5"，**需要
   细节时自己用工具走过去取**。context 小、保真度满、检索由 agent 主导。

4. **时间轴 + 知识图谱双投影，都 bi-temporal**。同一个 git 底座投影出两个正交
   视图：时间轴回答"何时"，知识图谱回答"什么关系"。两者都记两个时间——
   `event_time`（事情发生的时间）和 `ingestion_time`（我们记下来的时间）——
   支持时间旅行查询和矛盾检测。

实体层的两个 git 存储详见 [`entity-memory.md`](entity-memory.md) 和
[`git-as-entity-memory.md`](git-as-entity-memory.md)；虚拟层的 Timeline / Graph /
Core 存储、五阶段提炼管道和导航工具详见
[`virtual-memory.md`](virtual-memory.md)。

## 当前在跑的三个层级

代码里当前的抽象层是下面这条线性总结链。等上面的管道落地后，Timeline 取代
`short-term/`，Graph 取代 `wiki/`，`core.md` 由两者重新投影。

```
┌────────────────────────────────────────────────────────────────┐
│  short-term/YYYY-MM-DD.md                                      │
│  Raw daily notes. Append-only. Each line records one observation.│
│  Lifetime: kept indefinitely, but only the recent ones feed     │
│  the next phase. Source of truth for "what was actually said".  │
└────────────────────────────────────┬───────────────────────────┘
                                     │  sleep · light + deep
                                     ▼
┌────────────────────────────────────────────────────────────────┐
│  wiki/<kind>/<slug>.md                                         │
│  Curated knowledge pages with structured frontmatter (claims,  │
│  evidence, confidence, sources). Four kinds:                    │
│      user/         — facts about the human                      │
│      entities/     — people, products, places, organizations    │
│      concepts/     — things they keep talking about             │
│      procedures/   — things they keep doing                     │
│  + index.md / log.md / reflections.md at the root.              │
│  Lifetime: indefinite. Rewritten by deep / REM phases.          │
└────────────────────────────────────┬───────────────────────────┘
                                     │  sleep · deep + REM
                                     ▼
┌────────────────────────────────────────────────────────────────┐
│  core.md                                                       │
│  <2 KB. The bits the model literally sees at the top of every  │
│  system prompt. Frozen for the duration of any one session so  │
│  the provider's prompt cache hits.                             │
└────────────────────────────────────────────────────────────────┘
```

一切都位于 `<state>/memory/` 之下，其默认值为
`~/.openprogram/memory/`，并遵循 `--profile` / `OPENPROGRAM_STATE_DIR`。

## 端到端流程

一条记忆观察进入系统有两条途径，外加一个把它们做合并整理的后台进程。

### 流程 A —— 会话结束时的摘要（主路径）

由 `session_watcher`（`memory/session_watcher.py`）自动触发。

```
conversation ends ─────► poll every 5 min ─────► session idle ≥30 min?
                                                       │ yes
                                                       ▼
                                            load all messages from SessionDB
                                                       │
                                                       ▼
                                       send to LLM with summarizer prompt
                                       (build_default_llm + BuiltinMemoryProvider)
                                                       │
                                                       ▼
                          parse JSON array of {type, text, tags, confidence}
                                                       │
                                                       ▼
                              append each entry to short-term/<today>.md
```

提示词模板位于 `memory/builtin/summarizer.py:SYSTEM_PROMPT`。它要求模型
给出 0–10 条简短事实，分类为：

- `user-pref` —— "用户偏好简洁的回复"
- `env` —— "项目位于 ~/Projects/foo, Python 3.12"
- `project` —— "产品名为 OpenProgram"
- `procedure` —— "用户通过 `pytest -q` 运行测试"
- `fact` —— 任何其他持久的内容

每条记录都带有一个置信度分数（0.0–1.0）——这在后面 deep-sleep 把高置信度
的记录提升到 wiki 时很重要。

哪些会话已经被处理过的状态保存在
`<state>/memory/.state/session-end.json`，因此 worker 重启不会
重新处理每一段对话。

### 流程 B —— 压缩前的摘要

当一段对话增长到超出上下文窗口时，运行时会压缩较旧的消息。在它们被丢弃
之前，同一个摘要器会对即将被丢弃的那一段消息运行
（`memory/builtin/provider.py` 中的 `on_pre_compress`）。提取出的事实会
并入压缩摘要中，因此即便原始的对话轮次没有保留下来，洞见也能存活。

这条路径是自动且静默的。它不是一个单独的文件，也不是一个单独的调度。

### Sleep —— 合并整理 worker

worker 中的一个守护线程（`memory/scheduler.py`）每天本地时间 03:00 唤醒，
并按顺序运行三个协作的阶段：

```
light  ─► dedupe + score short-term entries                  (no LLM)
   │      Output: write phase signals to .state/sleep-stage.json
   ▼
deep   ─► promote candidates to wiki, rewrite affected pages, refresh core.md (LLM)
   │      Light scored each entry; deep picks the top N by score and
   │      writes / updates a wiki page per fact, then regenerates core.md
   │      with the highest-signal short text snippets that fit in 2 KB.
   ▼
rem    ─► scan wiki for themes / contradictions, append reflections.md (LLM)
          Looks at the whole wiki and writes free-form observations:
          "user mentioned X in three sessions, suggests a recurring
          interest", "concepts/A says X but procedures/B implies Y".
```

这些阶段是解耦的：light 无条件运行；deep 和 REM 需要接入一个可调用的 LLM
（worker 在启动时通过 `build_default_llm` 传入一个）。如果没有可用的 LLM，
light 仍会收集并打分；deep 则是空操作，直到下一次有 LLM 的扫描。

每个阶段涉及的文件：

| 阶段 | 文件                              | 输出                                |
|-------|-----------------------------------|---------------------------------------|
| light | `memory/sleep/light.py`           | `.state/sleep-stage.json`（分数）    |
| deep  | `memory/sleep/deep.py`            | `wiki/<kind>/<slug>.md` + `core.md`   |
| rem   | `memory/sleep/rem.py`             | `wiki/reflections.md`                 |

每次扫描后，`.state/last-sleep.json` 会记录 `{ts, phase,
promoted, skipped}`，因此你可以 `cat` 它来查看记忆上一次运行的时间。

## 模型实际看到的内容

在会话开始时，运行时会把 `core.md` 作为前缀块加入系统提示词。格式仿照
Hermes 的 `MEMORY.md / USER.md` 横幅：

```
═════════════════════════════════════════════════════
OpenProgram memory (machine-wide) — 6% (116/2048 chars), last consolidated 2026-05-08
═════════════════════════════════════════════════════
USER: User prefers terse answers in Chinese.
§
ENTITY: Backend daemon called worker, not daemon
§
ENTITY: Uses Ink for TUI

[for full context use memory_recall <query>]
```

### 2 KB 预算是硬约束，不是建议

`CORE_BUDGET_CHARS = 2048` 限定这个块的贡献量，且真正执行：
`system_prompt_block` 先用 `strip_chrome` 过一遍文件（去掉横线抬头和末尾指引，
它们是装饰不是内容），再用 `truncate_to_budget` 截断，然后文本才进提示词。
整理（consolidation）可能写超——抬头会把超出量按百分比印出来——所以预算必须在
读取这一侧兜住。

截断切在**章节边界**上：markdown 标题连同正文整段留或整段去，模型不会读到半个
意思。单个章节本身就超过整个预算时，退到下一级边界——先段落、再句子——保证块不会
断在词中间。被丢掉的部分就地写明，并指向 `memory_browse` 取其余内容。

这件事的分量超过它的体积，因为这个块在**每个会话的每一轮**系统提示词里。不执行
预算时，它是整个提示词里最大的单项，达到自己所声明预算的 174%。

页脚指向 `memory_recall`——一个模型可以在对话中途调用的工具，当它需要比
`core.md` 所容纳的更多细节时，用来获取某个特定的 wiki 页面。实现位于
`memory/tools/`（工具表层），背后由 `memory/builtin/recall.py`（FTS 搜索）
支撑。

## 检索：用于召回的 FTS 索引

`<state>/memory/index.sqlite` 持有一个覆盖 wiki 页面和 short-term 记录的
SQLite FTS5 索引。两张表：

- `wiki_fts` —— 每个 wiki 页面，按 title + body + claims + aliases 建立索引
- `short_fts` —— 每条 short-term 记录，按 text + tags 建立索引

`memory_recall` 工具查询这个索引，按 BM25 + 时近度排序，返回排名最前的
3-5 条匹配记录。索引在每次写入时增量重建（没有单独的同步步骤）。

## 文件布局参考

```
<state>/memory/
    core.md                           injected into system prompt
    short-term/
        2026-05-08.md                 daily notes
        2026-05-09.md
        ...
    wiki/
        index.md                      hand-edited TOC
        log.md                        free-form notes
        reflections.md                REM-phase output
        user/
            profile.md                facts about the human
        entities/
            <slug>.md
        concepts/
            <slug>.md
        procedures/
            <slug>.md
    index.sqlite                      FTS index over wiki + short-term
    .state/
        recall-counts.json            "this page was recalled N times"
        last-sleep.json               last sweep timestamp + outcome
        sleep-stage.json              light phase's scored candidates
        session-end.json              per-session "already processed" markers
        sleep.lock                    advisory lock for concurrent sweeps
```

## 代码地图

```
openprogram/memory/
    __init__.py            public API + module-level docstring
    provider.py            MemoryProvider abstract interface
    builtin/
        provider.py        BuiltinMemoryProvider — default implementation
        summarizer.py      LLM prompt + JSON parser for session-end
        recall.py          FTS query + ranking
    short_term.py          append-only daily file writer
    wiki.py                wiki page read / write helpers
    core.py                core.md render / write
    index.py               FTS index management
    store.py               filesystem layout (paths + ensure dirs)
    schema.py              dataclasses (ShortTermEntry, WikiPage, …)
    session_watcher.py     polls SessionDB, fires on idle
    scheduler.py           daemon thread that runs sleep at 03:00 daily
    llm_bridge.py          provider-agnostic LLM callable factory
    recall_counts.py       per-page recall counter (used by ranking)
    sleep/
        __init__.py        re-exports run_sweep + run_phase
        runner.py          orchestrates light → deep → REM
        light.py           dedupe + score
        deep.py            promote to wiki + rewrite core
        rem.py             cross-page reflections
        scoring.py         signal heuristics (frequency, recency, etc.)
```

## 插件接入点

`MemoryProvider`（`memory/provider.py`）是抽象基类。默认实现是
`BuiltinMemoryProvider`。要替换成另一种记忆后端（mem0、Honcho、Hindsight、
某个向量存储……），注册一个子类，并通过 agent 配置把它接入运行时。运行时
只会调用以下这些生命周期钩子：

```python
initialize(session_id, **kwargs)
system_prompt_block() -> str            # injected at session start
prefetch(query, *, session_id="") -> list[str]   # before each LLM call
on_session_end(messages) -> None        # after a turn ends idle
on_pre_compress(messages) -> str        # before context compression drops messages
```

其余的一切（文件布局、sleep 各阶段、FTS 索引）都是 builtin provider 的
实现细节。插件不必照搬这套三层模型。

## 失效模式

| 症状                          | 可能原因                                   | 修复                                     |
|----------------------------------|------------------------------------------------|-----------------------------------------|
| 没有 `short-term/<today>.md`       | 会话结束摘要器没找到任何持久内容，或 LLM 调用返回为空 / 无法解析 | 检查 `.state/session-end.json`——如果今天的 session_ids 带着时间戳在里面，说明摘要器被调用了；只是这段对话缺少持久的事实 |
| `core.md` 里只有框架级别的事实 | deep 阶段还没有任何高置信度的个人观察 | 围绕你的项目 / 偏好做几段真实的对话 |
| `last-sleep.json` 显示 `promoted=0` | 同上——short-term 记录低于分数阈值 | 增加长对话的数量，或手动编辑一个 wiki 页面 |
| 摘要器返回 []            | LLM 忽略了系统提示词（例如 `claude-code` provider——meridian 和较旧的 claude-max-api-proxy 都会丢弃 system 角色） | 当 provider 需要时，`build_default_llm` 会把 system 折叠进 user；用 `grep '_inline_system' openprogram/memory/llm_bridge.py` 验证 |
| session-end 状态陈旧          | 之前的 worker 在处理过程中崩溃          | 删除 `.state/session-end.json`——会话会在下次轮询时被重新扫描 |
| 昨晚 sleep 没有运行      | 03:00 时 worker 没在运行，或 LLM 不可用 | worker 启动时会调用 `scheduler.start_in_worker`；检查 `worker.log` 中是否有 `[worker] memory: sleep + session-end watcher running` |

## 设计渊源

- **三层拆分**（short / wiki / core）：借鉴自 Karpathy 的
  "LLM Wiki" 模式，其中原始观察被提炼进一个 wiki，而 wiki 的 TL;DR
  回填到提示词中。
- **`MEMORY.md` 注入格式**：从 Hermes 复制而来，让在不同 agent 之间
  迁移的用户看到熟悉的横幅。
- **MemoryProvider 接口**：同样来自 Hermes（`memory_provider.py`），
  以保留日后接入 mem0 / Honcho / 等等的选项。
- **把 sleep 设计成带 light/deep/REM 阶段的每日 cron**：对真实睡眠周期的
  一种致敬，主要是为了让 deep-LLM-pass 变得廉价（每天一批），而不是在
  每一轮对话上都运行它。

## 研究角度

1. **Git-native episodic memory for LLM agents** —— 用版本控制系统做 agent
   长期记忆的不可变底座，支持回溯 / 分支 / 标准工具检索。
2. **Provenance-linked virtual memory** —— 总结层不替代源头，而是用坐标给它
   建索引；解决有损总结的根本张力（压缩 vs 保真）。
3. **LLM-navigated recall** —— agent 读一张紧凑地图，按需导航回源头，区别于
   RAG 的盲目切块灌入；context 更小、保真度更高、检索由 agent 主导。
4. **Dual bi-temporal projection** —— 同一底座投影出时间轴 + 知识图谱，两者都
   bi-temporal，支持时间旅行和矛盾追踪。

评估方向：context 占用 vs 召回准确率的权衡；跨多会话的长程一致性；矛盾检测
召回率；与 RAG / Zep / mem0 基线的对比。

## 附录：实现状态

实体层已就位：Project schema、会话绑定、project-git，以及会话落在项目内都已
实现（`store/project_store.py`、`store/session_store.py`）。提炼所需的读取层在
`store/session/provenance.py`。

虚拟层尚未建成。当前在跑的是上面记录的线性 journal/wiki/core 链，它读的仍是
`get_branch()` 渲染出的对话文本，而不是 session-git 的 `Call` DAG；project-git
的 commit 历史完全没有被读取。接通这一跳是提炼管道的第一步。

召回仍注入 v1 core；导航工具尚未注册。UI 上有顶栏项目选择器；Projects 面板、
回溯时间轴和 `/memory` 命令尚未建成。
