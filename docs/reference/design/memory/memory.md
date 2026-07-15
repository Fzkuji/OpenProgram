# 记忆子系统

OpenProgram 如何让 agent 跨会话"记住"事情。

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

## 三个层级

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

在会话开始时，运行时会把 `core.md` 作为前缀块加入系统提示词。这个块足够
小（<2 KB，约 512 tokens），不会扰乱缓存。格式仿照 Hermes 的
`MEMORY.md / USER.md` 横幅：

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

## 失效模式与当前健康状况

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
