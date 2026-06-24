# 上下文组成 —— 注册式三层（目标态设计）

Status: **大部分已实现** · Created: 2026-06-23 · Updated: 2026-06-24

> 本文定义**每次 LLM 调用喂什么**的目标态。核心不是"列出有哪些成分"(那会定死、
> 不可扩展),而是定义**一套规则 + 注册机制**：成分如何归层、如何排序、如何按条件
> 出现；具体成分由各功能**注册**进来,加功能不改框架。
>
> 设计来源:借鉴 Hermes 的三层(stable/context/volatile = 我们的 L0/L1/L2),但
> **改进它的硬编码**——Hermes 的三层组装是写死的 if 链(加新指导要改中心函数),我们
> 做成真正的注册式(开闭原则:开放扩展、关闭修改)。
>
> **命题**:论文 "LLM-as-Code,模型是程序里的一个零件"——这个零件每次被调用要知道
> 自己的处境(我是谁/谁调我/在哪步),同时只看到该看的历史(结果,不是每个子函数的
> 内部过程)。

---

## 一、三层判据：信息流向哪

判据 = **"这次调用结束后,这个信息流向哪"**。把当前调用想成一个子节点:

| 层 | 级别 | 判据 | 内容 | wire 位置 |
|---|---|---|---|---|
| **L0** | 系统级 | **永远带** —— 整会话不变 | 身份/指令/工具/技能/全局记忆/环境 | 最前 · tools + system 头 |
| **L1** | 会话级 | **留给后续** —— 追加增长,前缀稳 | 项目层 + **统一调用树**(历史) | 中间 · 追加,吃缓存 |
| **L2** | 任务级 | **纯本次** —— 每次全变 | situation(我在树的哪) + 本次输入 + 输出格式 | 最后 · 不缓存 |

### L1 的核心:一棵统一调用树

L1 历史**就是一棵调用树**——它本质是整个上下文 DAG 的**一条活跃链路**(从根到当前节点
那条路;DAG 上的分支/重试等其他链路不在这条上)。所以它是**一棵干净的树/链,没有分支
合并、没有环**。

这棵树:
- **记录所有函数调用**(不管调不调大模型),节点带调用逻辑。
- **大模型相关的 io**(模型输入/输出、产出内容)加在对应节点上 → 树本身就是完整上下文。
- **追加增长**:每次调用最多往末尾加一两行(把新节点的结构展出来),**老前缀不动**。
- **完成即释放**:一个子节点(子函数)跑完后,**释放它身上的 io,只留调用逻辑 + 关键产出**;
  释放发生在树的靠后段(刚完成的那块),**不动前面老节点** → 缓存只损末尾一小段。

### 为什么调用树和历史合成一个(而非分两层)

它们内容本是同一批 DAG 节点,分开放(结构一块、io 一块)会让"每次变的结构块"单独成层,
**那块永远命不中缓存**。合成一棵追加树后:增长只在末尾、释放只在末尾、老前缀稳定 →
**大段命中缓存**,只末尾一小段重算。一块一块追加管理,缓存反而比"分两块各自变"更优。
而且结构 + 内容一体,模型顺着一棵树就看清"我在哪 + 沿途发生了什么",不用脑补关联。

### L2 只剩"纯本次"

调用树进 L1 后,L2 不再有"历史/结果"——结果已经在 L1 那棵树的节点里了。L2 只留**指挥
这一次怎么干**的指令:situation(当前在树的哪个节点、调用路径)、本次输入、输出格式 /
契约。这些每次全变,放最后,不缓存。

---

## 二、注册模型（核心,解决扩展性）

不枚举成分,而是定义**统一的成分接口 + 三个注册列表**。框架只管规则,成分是注册项。

### 成分接口

```python
@dataclass
class ContextComponent:
    name: str                          # 标识
    layer: Literal["L0", "L1", "L2"]   # 归哪层(判据见 §一)
    order: int                         # 层内排序:越稳越小(见 §三)
    condition: Callable[[Ctx], bool]   # 出现条件(返回 True 才进上下文;无条件=恒 True)
    build: Callable[[Ctx], str | None] # 条件满足时生成这块内容(None=本次为空)
    cacheable: bool = True             # 是否参与缓存前缀
```

### 三个注册列表 + 组装规则

L0 / L1 / L2 各维护一个注册列表。框架组装时:

```
对每一层:
  收集该层所有注册成分
  → 按 order 排序(越稳越前)
  → 过滤掉 condition(ctx) 为 False 的(功能不在/不适用,自动不出现)
  → 对留下的逐个 build(ctx),拼成该层内容
最终:tools(L0)→ system(L0+L1项目层)→ messages(L1历史 + L2)
```

### 为什么这样就不定死(回答扩展性)

- **加新功能**(多 agent、新 channel、新 provider、新工具指导):功能侧**注册一个
  ContextComponent**(声明 layer/order/condition/build),**框架代码一行不改**。
- **不需要的功能**:不注册,零负担;将来要了再注册。
- 框架管的是**规则**(三层判据 + 排序 + 注册接口),成分是**开放集合**。这正是
  Hermes 没做到的——它把成分硬编码进 build 函数,加一个要改中心函数。

> 改进点(vs Hermes):Hermes 只在"记忆 provider / 平台 hint"两个接触点有注册,核心
> 指导(工具感知/模型特定/平台格式)是 if 链硬编码。我们把注册推广到**所有成分**,
> 包括工具指导、模型指导、平台格式——它们都是注册项,各带 condition。

---

## 三、层内排序规则

层内也按稳定度排:**越稳越靠前、越常变越靠后**(缓存前缀匹配,层内顺序同样影响命中)。
`order` 字段就是这个序。历史这类每轮追加的,排所在层最后。

```
tools    = L0[toolset, MCP]                               ← 整会话不变 · 断点①
system   = L0[整体身份 → 指导块 → 技能/工具 → 全局记忆 → 环境信息]  ← 不变 · 断点②
         + L1[项目身份 → 项目记忆 → USER档案 → cwd → 绑定]   ← 换项目才变 · 断点③
messages = L1[统一调用树 …追加增长,完成节点释放 io…]      ← 前缀稳,吃缓存 · 断点④
         + L2[situation → git/todo → prefetch → 本次输入 → 输出规格] ← 每次全变,不缓存
```

要点:
- L0 内:身份/指导/工具最稳放最前;环境信息(OS/后端/日期)虽整会话稳但更接近会变,放 L0 尾。
- L1 内:项目固定信息(身份/记忆/USER/cwd/绑定)放前;**统一调用树追加增长,放 L1 最后**——
  它末尾增长 / 末尾释放,前缀稳 → 大段命中缓存。
- L2 内:全是纯本次的,每次全变;situation(我在树的哪)放前,本次输入/输出规格在后。
- **L2(每次变)必须排在 L1 调用树之后**——若把每次变的 situation/调用结构放调用树前面,
  会把后面那棵又大又稳的树的缓存连带作废。这是归层的硬约束(见 §一"为什么合成一个")。

> 上图是**设计/缓存视角**(标出每层位置 + 断点)。模型实际收到的是一段连续文本,
> **没有"L0/L1/L2/断点"这些字样**——分层只是我们组织内容和打缓存断点的依据,不写进
> prompt。例子见 §六。

---

## 四、当前注册成分快照

下面是**截至现在已注册/该注册的成分**(随功能增长,不是限制)。每个标 `order` /
`condition` / 现状。✅=已有,➕=该加,标 condition 说明何时出现。

### L0 系统级

| order | 成分 | condition | 现状 |
|---|---|---|---|
| 1 | 整体身份("你是 X agent") | 恒 | ✅ |
| 2 | inline agent prompt | 有则出 | ✅ |
| 3 | 工具强制(act-don't-ask) | 恒(可按模型) | ✅ tool_enforcement |
| 4 | 模型特定操作指导 | 按当前 provider/model | ✅ model_guidance(_MODEL_GUIDANCE 每 provider 一条) |
| 5 | 平台渲染格式 | 按当前 channel | ✅ platform_format(contextvar + _PLATFORM_RULES per channel) |
| 6 | computer-use 指导 | computer-use 工具启用 | ➕(低优先) |
| 7 | 技能索引 | 有启用技能 | ✅ |
| 8 | 工具 + MCP schema | 恒 | ✅ |
| 9 | 全局/用户级记忆 | 有 | ✅ |
| 10 | 环境信息(OS/shell/远程后端) | 恒(成体系) | ✅ environment(OS/shell;cwd 另由 tool-runtime) |
| 11 | 当前日期(日粒度) | 恒 | ✅ current_date |

### L1 会话/项目级

| order | 成分 | condition | 现状 |
|---|---|---|---|
| 1 | 项目身份(AGENTS.md) | 有项目文件 | ✅(现状错塞 L0,应 L1) |
| 2 | Prompt 注入检测(扫 1 再注入) | 加载项目文件时 | ✅ pi_shield + detect_injection_patterns |
| 3 | 上下文文件截断 | 项目文件超大 | ➕ |
| 4 | 项目级记忆 | 有 | ✅(现状错塞 L0) |
| 5 | USER.md 用户档案 | 有 | ✅ 已由 workspace_files 加载 read_user_md |
| 6 | 工作目录 cwd | 恒 | ✅ |
| 7 | 是否在 git 仓库 | 在 git 仓库 | ✅ git_repo_flag |
| 8 | session/model/thinking/tier 绑定 | 恒 | ✅ |
| 9 | deferred tools catalog | 有延迟工具 | ✅ |
| 10 | **统一调用树(历史)** | 有历史 | ✅ DAG 现成;改造点见下。追加增长 + 完成节点释放 io,排 L1 最后 |

> 第 10 项是 L1 核心:整个 DAG 当前活跃链路渲染成一棵带 io 的调用树(见 §一)。现状
> DAG / ContextCommit / tool-aging / summarize 已提供"节点 + 压缩"的底座;改造点是把
> "完成子节点释放 io、只留逻辑 + 关键产出"做成默认渲染(对应默认 `expose=io`),让树
> 追加增长、前缀稳定。

### L2 任务级（纯本次,无历史 —— 历史已在 L1 调用树里）

| order | 成分 | condition | 现状 |
|---|---|---|---|
| 1 | 本次处境 situation | @agentic_function 内调用 | ✅(step 6a/6b: _situational_prefix + _compute_call_path) |
| 2 | git 分支 / status | 在 git 仓库 | ➕(中) |
| 3 | todo / 任务计划 / 进度 | 有 todo | ✅ todo_progress(读 _TODOS 列表) |
| 4 | token 预算提示 | 接近预算 | ➕(低) |
| 5 | per-turn memory prefetch | 检索到相关记忆 | ✅(现状错塞 system,应 L2) |
| 6 | 本次用户输入 + 附件 | 恒 | ✅ |
| 7 | 输出格式 / schema | 本步要求 | ✅ |
| 8 | 输出契约 output_contract | 本步有下游 | ✅ 在 _situational_prefix 中作为 `Your output:` 行 |
| 9 | timestamp | 恒 | ✅(每次变,最末) |

### 不注册(我们没这功能,留机制位)

Kanban 多 agent 协调、Nous 订阅指导、Hermes profile 机制 —— 我们无对应功能,**不注册**。
将来真做了对应功能,它自己注册一个 ContextComponent 即可,框架不改。

---

## 四'、各成分的 prompt 模板

下面给关键成分的**可照抄 prompt 模板**:中文说明 + 英文 prompt 正文(面向模型,跟现有
skills / situational 块一致用英文)+ 占位符 + 注册参数(layer/order/condition)。写代码
时 `build()` 直接产出这些文本。格式学 `_situational_prefix`(`[…]` 标签)与 Hermes
GUIDANCE(`# 标题` + `<tag>` 分块)。

### 1. situation（L2 · order 1 · condition: 在 @agentic_function 内调用）★ 核心

扩展现状 `_situational_prefix`:不只防递归,补「职责 / 调用路径 / 程序位置 / 输出去向」。

分块用**成对 XML 标签**(`<situation>…</situation>`),不用 `#` 标题——边界明确,内容里
出现 `#`/代码/markdown 都不会和分块混淆(同 Claude Code 的 `<system-reminder>` 等惯例)。

```text
<situation>
You are running INSIDE the agentic function `{fn_name}`.
Job: {fn_doc}
Call path: {call_path}
Position: {program_position}
Your output: {output_contract}

The tool list may include `{fn_name}` itself — do NOT call it (re-entering
causes infinite recursion). Use lower-level tools to do the work directly.
</situation>
```

占位符:
- `{fn_name}` 当前函数名 · `{fn_doc}` 其 docstring 首句(职责)
- `{call_path}` 调用链,如 `research_agent → _pick_stage → literature → seed_surveys`
- `{program_position}` 在程序的位置,如 `literature 阶段第 1 步,后续 → extract_framework`
- `{output_contract}` 见下条(本块内联渲染,不单独成块)

> 防递归段(最后两句)沿用现状 `_situational_prefix`;前半是新增的处境。

### 2. output_contract（L2 · 内联进 situation）

产出被怎么用,一句话。模板按消费方式三选一:

```text
# 解析成决策
Your output will be parsed by the caller into a decision — emit exactly one
JSON object matching the menu below.
# 写文件 / 交付物
Your output becomes `{artifact}`, the deliverable consumed by `{consumer}`.
# 传给下一函数
Your output is passed to `{next_fn}` as its `{param}`.
```

### 3. 环境块（L0 · order 10 · condition: 恒）

OS / shell / cwd / 远程后端 合成一块(学 Hermes build_environment_hints)。

```text
<environment>
- OS: {os}  ·  Shell: {shell}
- Working directory: {cwd}
- Runtime: {backend}            # local / Docker / Modal / SSH:host
</environment>
```

> cwd 也是 L1 工作目录成分;此处环境块只放"机器/平台"类(OS/shell/backend),cwd 由
> L1 那条负责,避免重复 —— 实现时二选一渲染,默认 cwd 归 L1。

### 4. 当前日期（L0 · order 11 · condition: 恒）

日粒度(非分钟),缓存友好。

```text
Today is {weekday}, {month} {day}, {year}.
```

### 5. 模型特定指导（L0 · order 4 · condition: 按 provider/model）

通用骨架,每 provider 注册一条填进去(精简自 Hermes OPENAI_MODEL_EXECUTION_GUIDANCE)。

```text
# Execution guidance ({provider})
<tool_use>
- Use tools when they improve correctness or grounding; don't stop early when
  another call would materially help.
</tool_use>
<verify>
- Check prerequisites before acting; verify results before declaring done.
</verify>
{provider_extra}     # 各 provider 的额外项,如 Gemini「用绝对路径」
```

### 6. 平台渲染格式（L0 · order 5 · condition: 按 channel）

每 channel 注册一条骨架(我们有 wechat / slack / discord / telegram)。

```text
# Output channel: {channel}
{format_rule}
```

`{format_rule}` 示例:
- telegram/discord: `Use Markdown. Wrap code in fences. Keep replies focused.`
- wechat: `Plain text only — no Markdown. Short paragraphs.`
- sms: `Plain text, ≤ {limit} chars, no formatting.`

### 7. 调用树格式：YAML（L1 · order 10 · 默认 expose=io）

调用树**喂给模型用 YAML**(不是 ASCII 树画)。理由:模型对 YAML 很熟、省 token、层级靠
缩进、**多行 io 用 `|` 块不破坏结构**、io 就在节点对象里(不解耦)。文档里别处若出现
`├─ │` 树画,只是给人看的示意——实际喂模型的是下面这种 YAML。

字段(全名,不缩写):

| 字段 | 含义 |
|---|---|
| `function` | 函数名 |
| `input` | 输入(运行中/未释放时完整保留;长文本用 `\|` 块) |
| `output` | 输出(同上;长文本用 `\|` 块) |
| `status` | `running`(当前在跑) / `done`(已完成且 io 已释放) |
| `children` | 子调用(嵌套,按 §六 判据递归) |

格式:

```yaml
# 运行中 / io 未释放:带完整 input/output,多行用 | 块
- function: seed_surveys
  input: "query: LLM agent frameworks surveys …"
  output: |
    surveys:
      1. arXiv:2603.22386  "From Static Templates to Dynamic Runtime"
      2. arXiv:2601.xxxxx  "Agentic Runtime Graphs"
      … (N 篇,完整保留,不省)

# 已完成且 io 释放(程度二):留结构,只标 status,关键产出可留一句
- function: _lit_decide
  status: done
```

**程度二**:子树完成后,它的子节点**结构(`function` 那几行)全保留**(模型仍看得到经哪几步
做出来),只是各子节点的实际 `input`/`output` **释放**(标 `status: done`);子树根自己的关键
产出(如 framework)保留为摘要。

> 结构便宜→全留(历史安全网);io 贵→完成即释放。这是默认 `expose=io`;`expose=llm/full`
> 时连内部 LLM 交互都展开,`render_range` 收窄时连结构也可少留(见 §五)。

---

## 五、默认与可配置（expose / render_range）

§四是**默认**情况。"父子之间传多少历史"由两个旋钮决定(现状机制,见 `context.md`):

| 旋钮 | 管什么 | 默认 | 默认效果 |
|---|---|---|---|
| `expose` | 函数对外暴露自己多少 | `io` | 父子只传接口(身份+输入+输出),不传内部步骤 |
| `render_range` | 当前调用向上/向内取多少历史 | 不限 | L1 历史按完整链取,超预算才压缩 |

"子只见父接口、看不到父内部"是 `expose=io` 默认的结果,不是铁律。一般一个函数也只
调一个子函数,默认够用。要让某函数看更多(`expose=llm/full`)或更少(`render_range`
收窄),改它的声明即可。

> 论文 "context length 由调用深度决定、不随步数累加" 的落点:子树返回时**释放内部 io、
> 保留结构**(默认 expose=io)——父看到子树的结构骨架 + 关键产出,但不背它内部每一步的
> io。结构便宜不爆,io 完成即释放,所以大小由当前路径深度定。

---

## 六、Case：多层级调用逐步推演

### 调用树怎么生成(默认规则)

L1 历史里的"调用树",**框架自动从调用栈 / DAG 生成,零大模型参与**——它就是程序执行
事实(论文 "context built from the execution **call tree**")。

**默认展开判据 = "这个被调用的东西会不会再调大模型":**

| 被调用的 | 会不会调大模型 | 默认 |
|---|---|---|
| agentic 函数(`@agentic_function`) | 会 | **进树**,显示 `函数(input) → output`,并对它内部**递归**同样判据 |
| 普通函数 / 工具(read/bash/arxiv_search…) | 不会(只是去做事) | **折叠**,不进树 |
| 函数内部的大模型推理本身 | — | **不进树**(过去了就过去了;有用的产出已成为该函数的 output) |

判据本质:**会再调大模型 = 有上下文价值 → 记;不调大模型 = 纯执行操作 → 不记。**
大模型在嵌套里是一环:它调的东西中,**只有"又是 agentic 函数(还要调大模型)"的才继续
往树里加并递归**;它调的普通工具一律忽略(否则一次模型调用调几十个工具,树会爆炸)。

**节点带 io + 完成释放(方案 B 的关键)**:进树的节点**带它的实际 io**(输入 + 输出 /
模型产出)——树本身就是完整上下文。一个子节点**跑完后释放它的 io,只留调用逻辑 + 关键
产出**(那行 `func(...) → ✓结果`)。释放发生在树的靠后段(刚完成的那块),老前缀不动 →
追加增长 + 末尾释放,前缀稳 → 大段命中缓存。整棵树大小由**当前活跃路径深度**决定,不由
总调用数累加(论文 "by call depth not accumulation")。

> 这正是默认 `expose=io`:agentic 函数露 io、内部 llm/普通工具折叠、完成子节点 io 释放。
> 可被 expose/render_range 覆盖(见 §五),但**默认就是这套**。

### 例子结构

整棵树(YAML;节点 = agentic 函数,普通工具/模型推理折叠):

```yaml
- function: research_agent
  children:
    - function: _pick_stage          # 内部 1 次模型决策(折叠)
      output: "进 literature"
    - function: literature           # 内部:模型 + seed_surveys/arxiv 等(普通工具折叠)
      output: "framework{4 branches}"
      children:                       # 内部 agentic 函数(会调大模型)→ 递归进树
        - function: _lit_decide
        - function: seed_surveys
        - function: extract_framework
    - function: _pick_stage
      output: "进 idea"
    - function: idea
      children:
        - function: generate_ideas
          children:
            - function: check_novelty  # idea 里又一个 agentic 函数 → 递归
```

`literature` 内部的 `_lit_decide`/`seed_surveys`/`extract_framework`:会调大模型的
agentic 函数才进树(本例如此);普通工具则折叠。下面步骤推演展开它们,展示递归 + io 释放。

下面挑几个调用点推演。**说明用 L0/L1/L2 标注每段属于哪层,但 prompt 例子里写的是模型
实际收到的连续文本——不含任何"L1/L2"字样**(分层是我们的缓存/组织视角,不进 prompt)。

> 下面每步用**成对 XML 标签**分块(`<environment>` / `<project>` / `<call_tree>` /
> `<situation>`)——边界明确,内容里的 `#`/YAML/代码都不会和分块混淆。`<call_tree>` 里是
> L1 那棵 YAML 调用树,随执行推进生长。`status: running` 标当前正在跑的节点。

### 步骤 ① 正在跑 `_lit_decide`（树长到第 3 层）

模型实际收到的(连续文本,层标注仅讲解):

```text
You are research-agent (agent_id=main).                          ← L0 身份
<environment>
- OS: macOS · Shell: zsh · Runtime: local
- Working directory: /…/OpenProgram
- Today is Tuesday, June 24, 2026.
</environment>
[tools · skills · global memory …]                               ← L0

<project>                                                        ← L1 项目层
AGENTS.md(项目介绍) · 项目记忆 · model: openai-codex:gpt-5.5
</project>

<call_tree>                                                      ← L1 调用树(YAML,生长中)
- function: research_agent
  input: "把 LLM-as-Code 扩成 AAAI long paper"
  children:
    - function: _pick_stage
      output: "进 literature"
    - function: literature
      input: "LLM-as-Code → AAAI"
      status: running
      children:
        - function: _lit_decide
          status: running          # ← 你在这
</call_tree>

<situation>                                                      ← L2
You are running INSIDE the agentic function `_lit_decide`.
Job: pick the next literature-stage action.
Call path: research_agent → _pick_stage → literature → _lit_decide
Position: literature 决策环节,候选 [seed_surveys / extract_framework / done]
Your output will be parsed into a decision — emit one JSON object.
(do NOT call `_lit_decide` itself)
</situation>

Research direction: LLM-as-Code … 选下一动作。                    ← L2 current_task
```

体现:调用树这时长到第 3 层,`_lit_decide` 是当前 `[运行中]` 节点。树是框架从调用栈
自动拼的;situation 的 call path 就是从根到 `[你在这]` 那条路径。

### 步骤 ② 往下钻,正在跑 `seed_surveys`（树第 4 层)

L0 不变省略,只看生长的 L1 调用树 + L2:

```text
<call_tree>                                                      ← L1
- function: research_agent
  children:
    - function: _pick_stage
      output: "进 literature"
    - function: literature
      status: running
      children:
        - function: _lit_decide
          output: "下一步 seed_surveys"        # 出了 output,父还在跑
        - function: seed_surveys
          status: running                      # ← 你在这
          input: "query: LLM agent frameworks surveys …"
</call_tree>

<situation>                                                      ← L2
You are running INSIDE `seed_surveys`.
Call path: research_agent → _pick_stage → literature → _lit_decide → seed_surveys
Position: literature 检索工序,产出综述列表
Your output is stored by literature, fed to extract_framework next.
</situation>

Search query: LLM agent frameworks surveys …                     ← L2 current_task
```

体现:`_lit_decide` 出了 output(`→ "下一步 seed_surveys"`)就**合上不再展开**;树往下长一层
到 `seed_surveys`。**注意 `seed_surveys` 内部会调 `arxiv_search` 这类普通工具——它们不调
大模型,按默认判据折叠,不进树**(否则一次检索调几十个工具,树会爆)。L0 一字不变。

### 步骤 ③ 弹回主循环,正在跑第 2 轮 `_pick_stage`（literature 子树已释放 io)

```text
<call_tree>                                                      ← L1
- function: research_agent
  children:
    - function: _pick_stage
      output: "进 literature"
    - function: literature
      output: "framework{name, 4 branches}"    # 关键产出保留(摘要)
      children:                                 # 结构全留,各子节点 io 已释放
        - {function: _lit_decide, status: done}
        - {function: seed_surveys, status: done}
        - {function: extract_framework, status: done}
    - function: _pick_stage
      status: running                           # ← 你在这,主循环第 2 轮
</call_tree>

<situation>                                                      ← L2
You are running INSIDE `_pick_stage`.
Call path: research_agent → _pick_stage
Position: 主循环第 2 轮,已完成 [literature],下一候选 idea
Your output will be parsed into a stage name.
</situation>

Progress: literature done (framework ready)。选下一阶段。         ← L2 current_task
```

体现(程度二:**结构保留、io 释放**):literature 跑完后,它内部 `_lit_decide` /
`seed_surveys` / `extract_framework` 的**调用结构那几行还在**(模型仍看得到 literature 是
经哪几步做出来的),但每个子节点的**实际 io 内容被释放**(换成 `→ ✓`)。结构便宜所以留作
安全网,io 贵所以删。literature 自己的 output(framework)作为关键产出保留。第 2 轮
`_pick_stage` 的 situation 结构与第 1 轮相同 → 缓存友好。

### 步骤 ④ 第 2 轮深处,正在跑 `check_novelty`（递归:idea 里又一个 agentic 函数)

```text
<call_tree>                                                      ← L1
- function: research_agent
  children:
    - function: _pick_stage
      output: "进 literature"
    - function: literature                       # 已完成:结构在、io 释放
      output: "framework{…}"
      children:
        - {function: _lit_decide, status: done}
        - {function: seed_surveys, status: done}
        - {function: extract_framework, status: done}
    - function: _pick_stage
      output: "进 idea"
    - function: idea
      status: running
      children:
        - function: generate_ideas
          status: running
          children:
            - function: check_novelty            # ← 你在这(idea 内嵌套 agentic 函数,递归)
              status: running
</call_tree>

<situation>                                                      ← L2
You are running INSIDE `check_novelty`.
Call path: research_agent → _pick_stage → idea → generate_ideas → check_novelty
Position: idea 阶段查新工序
Your output is passed to generate_ideas as the per-idea novelty verdict.
</situation>

对以下 idea 查新:&lt;generate_ideas 的产出&gt;                          ← L2 current_task
```

体现:`generate_ideas` / `check_novelty` 是 idea 内部**会调大模型的 agentic 函数**,所以按
判据**递归展开进树**(若是普通工具就折叠了)。literature 那棵完成的子树**结构仍在(几行)、
io 已释放** —— 它是历史的安全网,不是删光。整会话上下文 = L0(恒定)+ L1(调用树:结构
追加增长、完成节点释放 io)+ L2(本次)。结构便宜所以全留,io 贵所以完成即释放 → 大小由
**当前活跃路径深度**定,不随总调用数爆炸。

---

## 七、现状证据（处境完全缺失）

真实 session,research_agent 跑 36 轮,每轮 prompt 开头一律裸任务,**无任何处境**:

| frame | prompt 开头(实际) | 缺的处境 |
|---|---|---|
| `_pick_stage` | `User project description: …` | 不知道自己是阶段选择器、有主循环 |
| `seed_surveys` | `Search query: …` | 不知道在 literature 里、产出给谁 |
| `extract_framework` | `Research direction: …` | 不知道前一步是 seed_surveys |

模型看到的是去掉处境的孤立填空题。历史那部分现状靠 DAG/ContextCommit 部分有;真正
零实现的是 **L2 的 situation 处境**(雏形 `_situational_prefix` 只做了防递归)。

---

## 八、实现要点（开始写代码时）

1. 定义 `ContextComponent` + 三个注册表 + 组装器(收集→排序→过滤→build)。
2. 把现状已有成分(✅)改成注册项;把该加的(➕)按 condition 注册。
3. **纪律**:skills/tools/MCP 默认整会话冻结。@agentic_function 可自定义,但"进函数
   一次性设定、函数内不变",绝不每次调用都改(否则 L0 抖动、缓存崩)。
4. 修现状三处塞错:项目身份/项目记忆/prefetch 现在塞在 system 前部当不变前缀,实际
   会变——按本设计各归其位(项目层→断点③后,prefetch→L2)。
5. 缓存断点按 §三 四个边界打(对接 `../providers/request-build.md` 的 cache_policy)。

---

## 相关文档
- `context.md` —— 现状机制(L1 历史由 DAG + ContextCommit 产出;expose/render_range 在那)
- `context-comparison.md` —— 与参考项目的成分对比(查漏来源)
- `../providers/request-build.md` —— 下游:Context 翻译成各家 wire + 缓存落地
- `agentic-self-recursion.md` —— `_situational_prefix`,L2 处境的雏形
