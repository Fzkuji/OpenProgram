# 统一 session 上下文创建

## 问题(为什么写这篇)

OpenProgram 的几个核心能力——**函数 docstring 自动进 prompt**、**DAG 持久化**、
**ask_user 追踪**、**嵌套调用的 called_by 归属**——都依赖一个 per-turn 的
**session 上下文**:一组 ContextVar(`_store` / `_current_turn_id` /
`_current_runtime` / `_call_id`),由 `SessionDB` + `session_id` 构成。

这套上下文**只有 dispatcher(网页 / chat 入口)在建**,而且建立逻辑**全部内联在
`process_user_turn()` 里,没有抽成可复用函数**。后果:

- **命令行直跑的入口(research harness `main.py`)根本没建 session** → `_store=None`
  → 上述能力全部静默失效。具体表现:`design_experiments` / `write_section` 的
  docstring 写了详细指令,但命令行跑时模型收不到 → 退化成"What would you like to
  do"对话。**同一个函数,网页端正常、命令行不正常。**
- `process_runner`(子进程)和 tests 各自**手抄了一遍** dispatcher 的 set/reset
  逻辑(`process_runner.py:149-174`、`tests/.../test_runtime_exec_dag.py`)——
  证明这块缺一个共享单元。

核心矛盾:**装了 OpenProgram,不管命令行还是网页,行为就该一致**。现在不一致,
根因是"建 session"这件事没有统一入口,谁记得做谁做、CLI 忘了做。

## standalone(`_store=None`)下静默失效的能力清单

| 能力 | 失效点 | 表现 |
|---|---|---|
| docstring 进 prompt | `render.py:101`(走不到) | 函数指令到不了模型,退化成对话 |
| DAG 持久化 | `runtime` append 节点 no-op | 跑的过程不进 session 历史 |
| 从 DAG 渲染历史 | `_render_history_messages` return None | 每次 exec 看不到前面步骤 |
| ask_user 追踪 | placeholder/finish 节点不写 | 提问不入历史 |
| 嵌套 called_by | `_call_id=None` | 节点归属丢失 |

实现模式统一是"`store=_store.get(); if store is None: return`"——不崩,但什么都不做。

## session 上下文由什么组成(调查结论)

```
SessionDB (default_db / SessionStore)           持久化后端(per-session git 仓库)
   └─ ~/.openprogram/sessions/<session_id>/      history/ + context/ + meta.json
GraphStoreShim(db, session_id)                   瘦包装:append/update/load DAG 节点
ContextVars(per turn,必须 set+reset 配对):
   _store           = GraphStoreShim(...)         深层代码读它写 DAG / 渲染 doc
   _current_turn_id = assistant_msg_id            文件备份归属到哪条消息
   _current_runtime = create_runtime()            @agentic_function 自动注入用
   _call_id         = (由 @agentic_function 包装设)  节点 called_by 归属
```

work-dir 与 session 是**两套独立持久化**:work-dir 存 research 产物文件
(literature review/ ideas/ paper/),session 存对话 DAG。互补,不冲突——接 session
不动 work-dir。

## 设计:抽出统一的 session 上下文管理器

把 dispatcher 内联的那套抽成**一个可复用单元**,所有入口(dispatcher / CLI /
research / process_runner / tests)都用它。形态用 context manager(保证 set/reset
配对,不泄漏 token):

```python
# openprogram/store/session/context.py  (新文件)

@contextmanager
def session_context(
    session_id: str | None = None,
    *,
    agent_id: str = "main",
    turn_id: str | None = None,
    runtime=None,            # 已有 runtime 就复用,否则按需建
    create_runtime_if_none: bool = True,
):
    """Install the per-turn session ContextVars and tear them down on exit.

    The single place that wires _store / _current_turn_id / _current_runtime
    so docstring-into-prompt, DAG persistence, ask_user tracking all work the
    SAME whether the caller is the web dispatcher, the CLI, research harness,
    a subprocess, or a test. Standalone callers that pass nothing still get a
    real (ad-hoc) session instead of silently degrading.
    """
    db = default_db()
    sid = session_id or ("adhoc_" + _short_uuid())
    if db.get_session(sid) is None:
        db.create_session(sid, agent_id, source="cli")
    rt = runtime
    if rt is None and create_runtime_if_none:
        rt = create_runtime()           # 降级:无 provider 则 rt=None,store 仍装
    tid = turn_id or ("turn_" + _short_uuid())

    tokens = []
    tokens.append(("_store",  _store.set(GraphStoreShim(db, sid))))
    tokens.append(("_turn",   _current_turn_id.set(tid)))
    if rt is not None:
        tokens.append(("_rt", _current_runtime.set(rt)))
    try:
        yield SessionHandle(db=db, session_id=sid, runtime=rt, turn_id=tid)
    finally:
        for _name, tok in reversed(tokens):
            tok.var.reset(tok)   # 实现里持有 var 引用以便 reset
```

### session 的边界:由 session_id 的传递决定,不是按调用次数

聊天里 session 边界天然清楚(开聊→结束)。命令行 / client 调用是"无状态的单次
函数调用",边界模糊——**不能"一次调用 = 一个 session"**,否则"跑一次任务、再接着
优化"会裂成两个不相干的 session,历史断掉;反过来也不该把无关的两件事塞进一个。

**谁决定"续上次"还是"另起",由调用方显式传 `session_id` 表达** —— 这正是
OpenProgram 已有的机制(`openprogram --resume <id>`、webui 前端把 id 带回),
统一 session 只是让所有入口沿用同一套规则,不发明新东西:

| 调用方意图 | 传什么 | `session_context` 行为 |
|---|---|---|
| 跑一件新任务 | 不传 session_id | 新建一个,**把 id 返回/打印**给调用方 |
| 接着在同一任务上聊 / 优化(第 2、3 次调用) | 传上次返回的那个 id | **复用**:接着往同一 session 写,历史接上 |
| 另起完全无关的事 | 不传(或传别的 id) | 独立新 session |

关键决定(回应"不能每调用一次就存一个 session"):**`session_context` 不默认每次
新建 adhoc。** 规则是:

- 传了 `session_id` → 复用(存在就接着写;不存在就以这个 id 建)。
- 没传 → 才新建,且**必须把 id 暴露出去**(CLI 打印 "session: research_xxx
  (--resume to continue)";代码 client 在返回值里带 `session_id`)。

这样连续性由 **id 的传递** 表达,与调用几次无关:

```python
# 代码 client 的理想用法
r1 = run_research("survey agent reliability")          # session_id=None → 新建
print(r1.session_id)                                   # -> research_ab12cd
r2 = run_research("now turn it into a paper",
                  session_id=r1.session_id)            # 续同一 session,历史接上
r3 = run_research("unrelated: GUI agent benchmark")    # 不传 → 独立新 session
```

CLI 对应:首跑不带 `--session` → 打印新 id;`--session <id>`(或复用 `--resume`)续。

### session 的结束

session **不需要显式"结束"** —— 它是 append-only 的 git 历史,写完就停,下次带同
id 来就接着写。没有"关闭"动作要做(`session_context` 退出只 reset ContextVar,不删
session)。"结束"只是调用方不再带这个 id 而已。需要清理时由 session 管理(已有的
session 列表 / 删除)处理,不在每次调用的职责里。

### 各入口怎么用

- **dispatcher**:把现有内联的 set/reset 替换成 `with session_context(req.session_id, ...)`。
  行为不变(它本来就建 session),只是去重。
- **research harness `main.py`**:在调 `research_agent` 外面包一层——
  ```python
  with session_context(session_id="research_" + uuid, runtime=rt) as h:
      result = research_agent(task=task, runtime=h.runtime, ...)
  ```
  这一层让 docstring 机制在命令行也生效,**不用改任何 stage 函数、不用把指令搬进
  content**。这是修复 research 后半段退化的正道。
- **process_runner / tests**:用同一个 manager 取代各自手抄的 set/reset。

### 落地顺序(每步独立可验证)

| 步 | 做什么 | 验证 |
|---|---|---|
| S1 | 新增 `session_context` manager(抽 dispatcher 逻辑,行为等价) | 单测:进入后 `_store/_current_turn_id/_current_runtime` 非 None,退出后复位 |
| S2 | research `main.py` 用它包住 research_agent | 命令行跑一个带详细 docstring 的函数,抓 prompt 确认 docstring 进了(实证过装 _store 后 doc=True) |
| S3 | dispatcher 改用它(去重,行为不变) | 现有 dispatcher 测试全绿 |
| S4 | process_runner / tests 收口到它 | 子进程 DAG 追踪、测试 fixture 仍工作 |

S1+S2 就能修好 research 命令行(你当前的痛点);S3+S4 是去重收尾。

## 撤销之前的"搬指令进 content"绕过

为让 research 后半段能跑,之前把 `design_experiments` / `write_section` 的指令从
docstring 搬进了 `content`(commit 09dc750 的一部分)。那是绕过 session 缺失的临时手段,
违背"docstring 写指令、函数体干净"的设计意图。S2 落地后应**撤回**这部分,让 docstring
重新承担指令。(`write_paper` 编排器是真的新增能力,保留。)

## 一句话

不是少装了什么组件——是 **"建 session" 没有统一入口,只有 dispatcher 做了,CLI 没做**。
统一成一个 `session_context` manager,所有入口都走它,命令行就和网页一致了。
