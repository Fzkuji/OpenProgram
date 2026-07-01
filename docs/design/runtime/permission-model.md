# 权限系统设计（Permission System Design）

本文是 OpenProgram 权限系统的**实现级设计文档**，描述当前 `main` 的真实实现：一个不了解代码的人读完，就知道权限系统是什么、有哪些权限、后端怎么写、前端怎么写、代码写在哪些文件。每个数据结构给字段定义，每个关键函数给签名，每个 WS 帧给字段，每个前端组件给结构。所有引用带 `file:line`，指向当前 `main` 工作区。

阅读顺序：**① 概览**（是什么、怎么运作）→ **② 有哪些权限**（模式与规则的定义）→ **③ 后端怎么实现**（判定、匹配、存储）→ **④ 前端怎么实现**（审批卡片、模式选择、规则管理）→ **⑤ 值守**（一个正交机制）→ **⑥ 关键约束与代码地图** → **⑦ 不做的边界**。

---

## 1. 概览

### 1.1 权限系统解决什么

模型想调用一个工具（bash / write / …）时，只有三种可能的处置：**直接执行**、**先问用户**、**直接拒绝**。权限系统就是决定每一次工具调用走哪条路的机制。它不是安全沙箱（不做进程/文件隔离），是**决策与知情层**——让用户能控制"什么自动、什么要点头、什么绝不允许"，并在需要点头时看清批的是什么。

### 1.2 四部分怎么协作

权限判定由四个部分串成一条决策链，从硬到软：

| 部分 | 管什么 | 能否被 bypass 关掉 | 位置 |
|---|---|---|---|
| **gate（硬拦截）** | 策略层的绝对禁止（proactive policy 的 deny/ask） | 否，永远生效 | `openprogram/agent/tool_gate.py` |
| **规则层** | 用户配的 allow / deny / ask 规则（per-tool + per-pattern，**项目级**为主，多来源分层） | deny/ask 否；allow 是 | `openprogram/agent/internals/_approval.py:50-69`（`_match_rule`）+ `openprogram/functions/permission_rule.py` |
| **权限模式（会话级）** | 会话档位：ask / acceptEdits / dontAsk / bypass / plan（对齐 Claude Code 官方名，5 档） | 档位本身就是这个开关 | `_gated_execute`（`internals/_approval.py:151-188`） |
| **审批流** | 需要点头时的前后端交互（弹卡片、阻塞等答、写回项目规则） | 否（弹出即阻塞） | `await_user_approval`（`internals/_approval.py:235-305`）+ 前端 approval mode |

关键安全约束贯穿全文：**决策优先级是 deny > ask > allow，且 deny/ask 判定早于 bypass 短路。** 因为 web 入口默认就是 bypass（`webui/_execute/__init__.py:557`），如果把 deny 规则匹配放在 bypass 之后，用户设的"禁止 rm -rf"会在默认下被静默忽略——那是安全缺陷。第 3 节的判定伪代码严格保证这一点。

### 1.3 数据流

```
LLM 发起工具调用
        │
        ▼
┌──────────────────────────────────────────────────────────────┐
│ agent_loop.py:581  构造 tool.before 事件                        │
│ agent_loop.py:587  decide_tool_gate(before_ev)  ← gate 硬拦截    │
│   deny → 抛 ToolGateDenied → error tool result 回给模型（终止）  │
└──────────────────────────────────────────────────────────────┘
        │ 放行
        ▼
┌──────────────────────────────────────────────────────────────┐
│ _gated_execute (internals/_approval.py:151-188)                │
│                                                                │
│  ① 规则层 deny/ask（bypass 之前）                                │
│     _match_rule → "deny" → 返回 [denied]（任何模式含 bypass）    │
│     _match_rule → "ask"  → 强制 await_user_approval（含 bypass） │
│  ② force_ask 工具（exit_plan_mode）→ 强制审批                    │
│  ③ permission_mode == "bypass" → 直接执行                       │
│  ④ permission_mode == "dontAsk" → 本该问的直接 [denied]         │
│  ⑤ 规则层 allow（bypass 之后）                                   │
│     _match_rule → "allow" → 直接执行                            │
│  ⑥ permission_mode == "acceptEdits" 且工具写安全 → 直接执行      │
│  ⑦ 其余 → await_user_approval 弹卡片阻塞                        │
└──────────────────────────────────────────────────────────────┘
        │
        ▼   需审批时
┌──────────────────────────────────────────────────────────────┐
│ await_user_approval → open_question(kind="approval")           │
│   → emit_question_asked → 事件层 → WS question.asked 帧          │
│   → 前端 approval mode 渲染审批卡片                              │
│   → 用户点 允许一次 / 总是允许 / 拒绝                             │
│   → question_reply/question_reject → _resolve_question          │
│   → threading.Event 唤醒 → consume_or_timeout → (approved,      │
│      reason, scope)                                             │
│   scope=="always" → _persist_always_allow_rule → 写回项目规则   │
└──────────────────────────────────────────────────────────────┘
        │
        ▼
   approved → orig_execute   |   denied/timeout → [denied] error result
```

---

## 2. 有哪些权限（定义）

这一节只讲"是什么"——权限模式和规则的定义。怎么实现在第 3、4 节。

### 2.1 权限模式（5 档，Claude Code 官方名）

权限模式是**会话级**的：存 `SessionRunConfig.permission_mode`，前端在 composer 的 plus-menu 里选、session-store 按会话隔离。定义在 `openprogram/agent/dispatcher/types.py:19`，合法值集在 `openprogram/agent/session_config.py:25`（对齐 Claude Code 官方 5 档；**无 `auto`** —— auto 是 Claude Code 内部 LLM 分类器档，不作对外档，已删）：

```python
# openprogram/agent/dispatcher/types.py:19
PermissionMode = Literal["ask", "acceptEdits", "plan", "dontAsk", "bypass"]

# openprogram/agent/session_config.py:25
VALID_PERMISSION = {"ask", "acceptEdits", "plan", "dontAsk", "bypass"}
_PERMISSION_BY_LOWER = {m.lower(): m for m in VALID_PERMISSION}  # 大小写不敏感规范化
```

档位内部值用官方英文原名，前端标签对齐 Claude Code 官方中英文（`use-permission-mode.ts` 的 `MODE_LABELS`）：

| 模式（内部值） | 前端标签（EN / 中文） | 行为 |
|---|---|---|
| `ask` | Default / 默认 | 每个工具调用都弹审批卡片阻塞等答（除非规则 allow 或 per-tool 声明不需审批）。逐次问。 |
| `acceptEdits` | Accept Edits / 接受编辑 | 对**写类且路径安全**的工具（read/write/edit/glob/grep/list，且目标在工作目录内、非危险文件）自动放行；bash/exec/shell 等命令类**仍走完整审批**。 |
| `dontAsk` | Don't Ask / 不再询问 | 本该弹卡片的调用直接返回 `[denied]`，绝不打断用户。等价"全拒需要人工确认的操作"。 |
| `bypass` | Bypass / 绕过权限 | 全部直接放行，不弹审批。**例外**：`exit_plan_mode` 强制审批（`_FORCE_APPROVAL_TOOLS`，`internals/_approval.py:34`）；规则层 deny/ask 仍生效。 |
| `plan` | Plan Mode / 计划模式 | 计划态。写类工具在此模式对模型不可见（`apply_tool_policy(source="plan")`）——纯**可见性**控制，与批准强度正交（见 §3.7）。 |

> 大小写规范化：`acceptEdits` / `dontAsk` 是驼峰。`VALID_PERMISSION` 存的是驼峰规范值，`_PERMISSION_BY_LOWER` 建一张 `小写 → 规范值` 表；`_normalize_permission`（`session_config.py:271-275`）用 `_PERMISSION_BY_LOWER.get(value.lower())` 做大小写不敏感匹配，所以前端传 `"acceptedits"` 也能规回 `"acceptEdits"`，非法值返回 `None`。

### 2.2 规则（allow / deny / ask 三平行）

规则是用户配的覆盖，与档位正交，**主要载体是项目**（见 §2.3）。三个平行 list，规则的 behavior 由它住在哪个 list 决定，不由字符串自带字段：

```python
# openprogram/agent/session_config.py:32-42
@dataclass
class PermissionRules:
    allow: list[str] = field(default_factory=list)
    deny:  list[str] = field(default_factory=list)
    ask:   list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.allow or self.deny or self.ask)
```

**规则字符串语法**：

```
ToolName                 整工具，per-tool。例：Bash / write_file / read_file
ToolName(content)        命令级，per-pattern。例：bash(git:*) / read_file(/etc/**)
```

- `bash` → `{tool_name="bash"}`（整个 bash 工具）
- `bash(git status)` → `{tool_name="bash", pattern="git status"}`（精确命令）
- `bash(git:*)` → `{tool_name="bash", pattern="git:*"}`（前缀通配：`git ` 开头的命令）
- `read_file(/etc/**)` → `{tool_name="read_file", pattern="/etc/**"}`（路径 glob）
- 转义：pattern 内的 `(` `)` `\` 需转义（`\( \) \\`），它们是语法定界符。序列化/反序列化对偶。

```python
# openprogram/functions/permission_rule.py:19-22
@dataclass(frozen=True)
class PermissionRuleValue:
    tool_name: str
    pattern: str | None = None   # None = per-tool；非 None = per-pattern
```

### 2.3 规则来源（3 层，项目是主要载体）

规则可来自多个来源，低优先级在前、高优先级在后，后进覆盖前进。合并在 `load_merged_rules(session_id)`（`openprogram/functions/permission_rule.py:100-146`）。**项目层是规则的主要载体**——规则跟项目走，切会话仍在、"总是允许"能长期记住。只映射真实存在的载体（无 local/cliArg/企业策略后台，见第 7 节边界）：

| 层 | 优先级 | 载体 | 可写 |
|---|---|---|---|
| global（全局配置） | 最低 | 全局 config 的 `tools.permission_rules`（`webui._setup._read_config()`） | 是 |
| **project（主要载体）** | ↑ | `<project>/.openprogram/settings.json` 的 `permission_rules`；默认项目落 `<state>/projects/default-settings.json`。经 `project_for_session(session_id)` 反查项目 | 是 |
| session（本会话，一次性覆盖） | 最高 | `SessionRunConfig.permission_rules`，随会话落 session meta（schemaless） | 是 |

- 读写项目层：`openprogram/store/project/project_store.py` 的 `load_project_settings` / `save_project_settings`（`:565-592`）；载体路径由 `_settings_path_for`（`:559-562`）决定——非默认项目落 `<project>/.openprogram/settings.json`，默认项目落 `<state>/projects/default-settings.json`（绝不往家目录塞配置）。
- 合并只是拼接三 list：deny/ask/allow 的总序由 `_match_rule` 保证（命中即返回，deny > ask > allow），来源顺序只影响同一 behavior 内的先后。
- "总是允许"（scope=`always`）写回**项目** settings（`_persist_always_allow_rule`，`internals/_approval.py:90-107`），不再是 session meta。

### 2.4 审批帧的数据载体 PendingQuestion

审批合流进统一的 `QuestionRegistry`——审批就是 `kind="approval"` 的问题，和 `runtime.ask` 走同一条链路、同一个前端承接点。

```python
# openprogram/agent/questions.py:35-53
@dataclass
class PendingQuestion:
    id: str                    # UUID hex[:12]
    session_id: str            # webui 会话 id，可空
    kind: str                  # "ask"|"confirm"|"approval"|"form"|"ask_many"
    prompt: str
    options: list[str] = field(default_factory=list)
    multi: bool = False
    allow_custom: bool = True
    detail: str = ""           # approval 用：工具名+参数摘要
    schema: dict = field(default_factory=dict)
    questions: list = field(default_factory=list)
    created_at: float = 0.0
    expires_at: float = 0.0
```

`_Resolution = tuple[str, object]`，registry 的 `outcome` 只有两态 `{"answered", "declined"}`（`questions.py:56-58`）。`"timeout"` 不是 registry 状态，而是 `consume_or_timeout`（`questions.py:257-259`）等不到结果时**合成**的返回值：`return res if res is not None else ("timeout", None)`。

---

## 3. 后端怎么实现

一次工具调用完整走两道关卡：gate（agent 主循环里）、审批包装（工具协程内）。规则匹配、各模式分支、存储、危险检测都在这一层。

### 3.1 gate（关卡 A，同步硬拦截）

在 `agent_loop.py` 里每次执行工具之前：`agent_loop.py:581` 构造 `tool.before` 事件 → `agent_loop.py:587` 调 `decide_tool_gate(before_ev)` 问一圈已注册的 gate → 有 deny 则 `agent_loop.py:593-594` 抛 `ToolGateDenied`，deny 理由作为 error tool result 回给模型。

```python
# openprogram/agent/tool_gate.py:26-27, 53-69
ToolGate = Callable[[Event], "str | None"]   # 返回 None 放行 / 字符串 deny 理由

def decide_tool_gate(event: Event) -> str | None:
    """询问所有 gate，取最严：任一 deny 即拦（理由 "; " 合并）。
    gate 抛异常 → fail-open（stderr 打印），继续问下一个。"""
```

关键性质：**gate 在权限审批包装之外，`bypass` 关不掉它**（`tool_gate.py:15`），对 subagent 同样生效。gate 是策略层（proactive policy 的 Gate allow/deny/ask）的硬拦截点，必须快（同步热路径，不许 LLM / 慢 IO）。

### 3.2 审批包装（关卡 B）与决策伪代码

工具进入 dispatcher 时被逐个包一层审批（`dispatcher/__init__.py:784`：`tools = [_wrap_with_approval(t, req, on_event) for t in tools]`）。包在工具协程**内部**，因为 agent_loop 急切调度 `tool.execute`，从外面拦有竞态（`internals/_approval.py:120-125`）。`_gated_execute` 是被替换进去的 execute（`internals/_approval.py:151-188`），完整判定顺序（7 分支）：

```python
# openprogram/agent/internals/_approval.py:34, 151-188
_FORCE_APPROVAL_TOOLS = {"exit_plan_mode"}  # :34

async def _gated_execute(call_id, args, cancel, on_update):
    mode = req.permission_mode
    force_ask = name in _FORCE_APPROVAL_TOOLS

    # ① 规则层 deny/ask —— bypass 之前，最高安全优先级
    verdict = _match_rule(getattr(req, "permission_rules", None), name, args)  # 3.4
    if verdict == "deny":
        return _denied(f"[denied] blocked by deny rule: {name}")
    if verdict == "ask":
        return await _approve_then_run(call_id, args, cancel, on_update)  # 即使 bypass 也弹

    # ② force_ask（exit_plan_mode），bypass 也不能跳
    if force_ask:
        return await _approve_then_run(call_id, args, cancel, on_update)

    # ③ bypass 短路（deny/ask/force 之后）
    if mode == "bypass":
        return await orig_execute(call_id, args, cancel, on_update)

    per_tool_required, _reason = tool_requires_approval(agent_tool, args)

    # ④ dontAsk：本该问的直接拒
    if mode == "dontAsk":
        if _would_need_approval(name, per_tool_required):
            return _denied(f"[denied] dontAsk mode: approval required for {name}")
        return await orig_execute(call_id, args, cancel, on_update)

    # ⑤ 规则层 allow —— bypass 之后
    if verdict == "allow":
        return await orig_execute(call_id, args, cancel, on_update)

    # ⑥ acceptEdits：写安全工具自动放行；命令类落审批
    if mode == "acceptEdits" and getattr(agent_tool, "_accept_edits_safe", False) \
            and _path_is_safe(name, args, req):        # 3.3 / 3.5
        return await orig_execute(call_id, args, cancel, on_update)

    # ⑦ 弹卡片阻塞等答（default / acceptEdits 的命令类都落这里）
    return await _approve_then_run(call_id, args, cancel, on_update)

# internals/_approval.py:140-149
async def _approve_then_run(call_id, args, cancel, on_update):
    approved, reason, scope = await await_user_approval(
        req=req, tool_name=name, args=args, on_event=on_event)
    if not approved:
        return _denied(reason_or_default(reason, name))
    if scope == "always":
        _persist_always_allow_rule(req.session_id, name)  # 写回项目规则，见 §4.4
    return await orig_execute(call_id, args, cancel, on_update)
```

**deny 早于 bypass 的安全约束（全设计最关键，务必保留）**：deny/ask 规则匹配（① ②）必须在 bypass 短路（③）之前。反例：若把规则整块插在 bypass 之后，则 web 默认 bypass（`_execute/__init__.py:557`）下，用户配的 `deny: ["bash(rm -rf:*)"]` 永远不被查到——rm -rf 被静默执行。所以 deny/ask 查在 bypass 之前、allow 查在 bypass 之后。这个先后是安全性质，改动 `_gated_execute` 时不可打乱。

### 3.3 各权限模式的分支实现

对应 3.2 伪代码编号：

- **acceptEdits（⑥）**：三部分——① `@function` 有 `accept_edits_safe: bool = False` 参数（`functions/_runtime.py:723`），落到工具对象的 `_accept_edits_safe`（`:1030`）；read/write/edit/glob/grep/list 各自的 `@function` 标 `True`（如 `functions/tools/write/write.py:24`、`edit/edit.py:25`、`read/read.py:28`、`grep/grep.py:101`、`list/list.py:30`、`glob/glob.py:43`），bash/exec/execute_code 不标（默认 `False`）；② `_path_is_safe`（`internals/_approval.py:77-87`）复用 3.5 的 `check_path_safety`（路径在工作目录集内、非危险文件/目录、无 Windows 绕过）；③ 命令类工具即使有宽 allow 也 fall-through 到 ⑦ 强制审批。
- **plan（可见性控制）**：`apply_tool_policy(tools, source="plan")`（`dispatcher/__init__.py`）滤掉写类工具，根本不进模型工具列表。plan 状态存布尔集（`agent/plan_mode.py` 的 `_active`），**不切批准强度**——与批准档正交（详见 §3.7）。`_gated_execute` 无 plan 专属分支（写类已被滤掉，只读工具按当前档常规走）。
- **dontAsk（④）**：ask → deny。`_would_need_approval(tool_name, per_tool_required)`（`internals/_approval.py:72-74`）判断这次在非 dontAsk 下会不会需要审批（`per_tool_required` 或工具属高风险集 `_RISKY_TOOLS`）；会 → `[denied]`；不会 → 直接执行。规则层 deny/ask（①）仍在其前生效，allow（⑤）不受影响。
- **ask**：不命中 allow、per-tool 不免审的工具全部落 ⑦。
- **bypass（③）**：deny/ask/force 之后全部直接执行。

### 3.4 规则匹配 `_match_rule`

```python
# openprogram/agent/internals/_approval.py:50-69
def _match_rule(rules, tool_name: str, args: dict) -> "str | None":
    """返回 "deny" | "ask" | "allow" | None（未命中）。
    优先级固定 deny > ask > allow：先扫 deny 命中即返回，再 ask，再 allow。
    每档内部：先试 per-tool（rule.pattern is None 且 tool_name 相等），
    再试 per-pattern（rule.pattern 对 parse_command(tool_name, args) 前缀/glob 匹配）。"""
    if rules is None:
        return None
    from openprogram.functions.permission_rule import parse_rule, parse_command, pattern_matches
    cmd = None  # 惰性求值：只在遇到 per-pattern 规则时才解析命令
    for behavior, ruleset in (("deny", rules.deny), ("ask", rules.ask), ("allow", rules.allow)):
        for raw in ruleset:
            rv = parse_rule(raw)
            if rv.tool_name != tool_name:
                continue
            if rv.pattern is None:                      # per-tool
                return behavior
            if cmd is None:
                cmd = parse_command(tool_name, args)    # 见下
            if cmd is not None and pattern_matches(rv.pattern, cmd):
                return behavior
    return None
```

- **per-tool**（`rv.pattern is None`）：`rv.tool_name == tool_name` 命中整工具。例 `deny: ["bash"]` 拦所有 bash。
- **per-pattern**（`rv.pattern` 非空）：先取可比命令串 `cmd = parse_command(...)`，再 `pattern_matches`（`permission_rule.py:149-159`）：`:*` 结尾→前缀匹配（`git:*` 匹配 `git status`、不匹配 `github`）；含 glob（`*?[`）→`fnmatch`（`/etc/**` 匹配 `/etc/passwd`）；否则精确相等。

命令解析器 + 规则解析（`openprogram/functions/permission_rule.py`）：

```python
# permission_rule.py:43, 77, 83-97
def parse_rule(s: str) -> PermissionRuleValue: ...        # "bash(git:*)" → (bash, "git:*")
def rule_to_string(v: PermissionRuleValue) -> str: ...    # 与 parse_rule 对偶
def parse_command(tool_name: str, args: dict) -> str | None:
    """把工具参数归约成可比字符串（per-pattern 匹配用）。
    bash/exec/shell/execute_code/process → args["command"]；
    read*/write*/edit*/apply_patch/list → args["path"] 或 args["file_path"]；
    其余无可比字段 → None（per-pattern 对其不生效，只 per-tool 可拦）。"""
```

各层规则合并（`load_merged_rules(session_id)`，`permission_rule.py:100-146`）——按优先级 global < project < session 拼接三 list，供 `_gated_execute` 用（真正跑判定时 `req.permission_rules` 由构造 TurnRequest 时填入）：

```python
# openprogram/functions/permission_rule.py:100-146
def load_merged_rules(session_id: str) -> PermissionRules:
    """合并三层真实载体：全局配置 < 项目（主要载体）< 会话（一次性覆盖）。
    项目层经 project_for_session(session_id) 反查 → load_project_settings。
    合并只是拼接三 list；deny/ask/allow 的总序由 _match_rule 保证（命中即返回），
    来源顺序只影响同一 behavior 内的先后。"""
```

**与 per-tool `requires_approval` 的关系**：两层并存互补。`@function(requires_approval=...)`（`functions/_runtime.py`）是工具作者写死的声明（`True`/`False`/`None`/`callable(**args)->bool|str`），dispatcher 经 `tool_requires_approval`（`_gated_execute` 在 `:170` 取其 `per_tool_required`）读。规则层是用户运行时覆盖，跑在 per-tool 之前（① ⑤ 在 ⑦ 之前）。

### 3.5 危险检测与路径安全

**RiskLevel + 卡片高亮**（`internals/_approval.py:208-232`）：

```python
# openprogram/agent/internals/_approval.py:208-219
def _risk_level(tool_name: str, args: dict) -> str:
    """审批卡片的危险分级 "low"|"medium"|"high"，驱动前端高亮。
    high：命令类工具（_RISKY_TOOLS）且命令含 rm -rf / sudo / mkfs /
          fork bomb / 管道到 shell / curl / wget。
    medium：其余命令类工具；写/编辑/删除类工具。 low：只读工具。"""
```

`_approval_detail`（`internals/_approval.py:222-232`，生成"工具名 + 参数全文，超长首尾截断"）给审批卡片一段可读摘要（第一版不做危险 token 高亮）。`_on_asked`（`:274-282`）的 `question.asked` 帧带上 `tool`/`args`/`risk_level`，前端据此上色（§4.2）。

**路径安全**（`openprogram/functions/tools/file_safety.py`）：

```python
# file_safety.py:20-50
DANGEROUS_FILES = {".bashrc", ".bash_profile", ".bash_login", ".profile",
                   ".zshrc", ".zprofile", ".zshenv", ".gitconfig", ".gitmodules",
                   ".git-credentials", ".npmrc", ".pypirc", ".netrc",
                   ".mcp.json", ".claude.json", ".env"}
DANGEROUS_DIRECTORIES = {".git", ".hg", ".svn", ".vscode", ".idea",
                         ".openprogram", ".claude", ".ssh", ".gnupg"}
DANGEROUS_BASH_PATTERNS = {"python","python3","node","deno","bun","ruby","perl",
                           "php","sh","bash","zsh","eval","exec","source",
                           "sudo","ssh","npx"}

def check_path_safety(path: str, working_dirs=None) -> dict:
    """返回 {"safe": bool, "message": str}。不安全：命中 DANGEROUS_FILES（按
    basename）/ 段命中 DANGEROUS_DIRECTORIES / 目标在 working_dirs 之外 /
    Windows 绕过（NTFS 流 ::$DATA、8.3 短名 ~1、UNC \\、尾部点空格、
    DOS 设备名 CON/PRN、三连点 .../）。working_dirs 缺省 = [cwd]。"""
```

`check_path_safety` 目前只被 acceptEdits 分支的 `_path_is_safe`（`internals/_approval.py:77-87`）消费：路径不安全 → acceptEdits 不自动放行、fall-through 到 ⑦ 审批。

**额外工作目录**：`SessionRunConfig.additional_working_dirs`（§3.6）扩展路径安全的工作目录集。`_path_is_safe` 组装 `work_dirs = [os.getcwd(), *req.additional_working_dirs]`（`:86`）传给 `check_path_safety`。该字段从 session meta 经 `TurnRequest.additional_working_dirs`（`dispatcher/types.py:112`）流下，填充点在 `webui/_execute/chat.py:259`、`channels/_conversation.py:240`。用户可加"这个目录也算安全区"；缺它则只认 cwd。

**尚未接线**：`is_dangerous_allow_rule(tool_name, pattern)`（`file_safety.py:94-100`，用 `DANGEROUS_BASH_PATTERNS` 判一条 allow 规则在 acceptEdits 下会不会放过危险命令）已实现但**无调用方**——"进 acceptEdits 时临时剥离危险 allow 规则"目前不启用。当前也**没有** bypass 免疫的 safetyCheck 强制审批（`tool_requires_approval` 仍是 `(bool, reason)` 二元组，不带 `classifier_approvable`）：路径安全只在 acceptEdits 分支起作用，bypass 下写危险文件不会被强制拦。若要补，见 §7 末尾。

### 3.6 存储：session meta schemaless + SessionRunConfig

存储分两处，各管一半：**权限模式在会话（session meta），权限规则在项目（settings.json）**。

**会话层（模式）是 schemaless 的**——这是权限模式持久化不需要 DB migration 的原因。`SessionDB.update_session(session_id, **fields)`（`store/session/session_store.py:602-639`）把 `head_id` 特殊路由到 `idx.set_head()`，其余任意字段（`permission_mode` / `additional_working_dirs`，以及一次性覆盖用的 `permission_rules`）全部经 `idx.set_meta(**clean)` 落进 session meta。所以加会话级权限字段只改 `session_config.py` 的 load/save，旧会话读回不报错。

```python
# openprogram/store/session/session_store.py:602-639
def update_session(self, session_id, **fields):
    """head_id → set_head()；其余字段 → set_meta(**clean)。schemaless。"""
```

```python
# openprogram/agent/session_config.py:45-59
@dataclass
class SessionRunConfig:
    tools_enabled: Optional[bool] = None
    tools_override: ToolsOverride = None
    web_search: Optional[bool] = None
    toolset: Optional[str] = None
    thinking_effort: Optional[str] = None
    permission_mode: Optional[str] = None
    # ── 权限规则（会话层作最高优先的一次性覆盖；主要载体是项目，见 §2.3）──
    permission_rules: Optional[PermissionRules] = None          # §2.2
    additional_working_dirs: list[str] = field(default_factory=list)  # §3.5 路径安全

# session_config.py:190-191
def permission_from_config(cfg, *, default: str) -> str:
    return _normalize_permission(cfg.permission_mode) or default
```

**项目层（规则）**落在 `<project>/.openprogram/settings.json`（默认项目落 `<state>/projects/default-settings.json`）的 `permission_rules` 键，读写经 `project_store.load_project_settings` / `save_project_settings`（`store/project/project_store.py:565-592`）。这是规则的主要载体，跟项目走。会话层 `permission_rules` 仅作最高优先的一次性覆盖。合并见 `load_merged_rules`（§3.4）。

**默认值三处**（`permission_from_config` 的 default 决定 session 未设置时落哪）：

| 入口 | 默认 | 位置 |
|---|---|---|
| TurnRequest 数据类字段 | `ask` | `dispatcher/types.py:53` |
| Web 执行路径 | `bypass` | `webui/_execute/__init__.py:557` |
| 渠道（channels） | `ask` | `channels/_conversation.py:238` |

子 agent 固定 `bypass`（`sub_agent_run.py:89`）：子 agent 的 lane 上没有 UI 订阅审批事件，ask 会让每工具超时 `[denied]`；且"派生子 agent"本身已是用户显式动作。

### 3.7 plan 与 permission_mode 的关系（不做 prePlanMode）

plan 是**可见性控制**（藏写工具，`agent/plan_mode.py` 布尔集），不切 `permission_mode`——两者正交。所以不像 Claude Code 那样需要"进 plan 记住旧档、退出恢复"（CC 的 plan 是权限档，占用档位槽才需要 prePlanMode）。进/退 plan 只翻 `plan_mode._active` 的开关，当前的 `permission_mode`（ask/acceptEdits/dontAsk/bypass）始终不变、退出即原样生效——不记录、不恢复。代码里**没有** `pre_plan_permission_mode` 字段，**没有** `permission_context.py`。

---

## 4. 前端怎么实现

前端三件事：审批卡片（收 question.asked、渲染三选一）、权限模式选择（composer plus-menu，会话级）、规则管理面板（**Projects 页**，项目级）。

### 4.1 审批卡片入口

审批合流进统一的问题渲染（approval 是 `kind="approval"` 的问题，和 `runtime.ask` 走同一条链路）。入口组件 `QuestionMode`（`web/components/chat/composer/modes/question/question-mode.tsx`）按 `kind` 分支：approval 分支（`:82-83, :309-334`）把帧的 `prompt`/`detail`/`risk_level` 归一成一个 approval step 后渲染卡片。

`question.asked` 帧字段（后端 `emit_question_asked` 发，`internals/_approval.py:274-282`）：

```json
{
  "type": "question.asked",
  "data": {
    "id": "<uuid hex[:12]>", "session_id": "<可空>", "kind": "approval",
    "prompt": "允许执行 <tool_name>？",
    "options": ["允许", "拒绝"], "multi": false, "allow_custom": false,
    "detail": "<tool_name>\n<args_json 超长截断>", "expires_at": 1735689600.0,
    "tool": "<tool_name>", "args": { "...": "工具参数 dict" },
    "risk_level": "high"
  }
}
```

`tool`/`args`/`risk_level` 是 approval 专属（`:281`），给前端画危险摘要、驱动高亮。

### 4.2 审批卡片三选一 + 危险高亮

approval 分支渲染三颗按钮（允许一次 / 总是允许 / 拒绝）+ 危险高亮（`question-mode.tsx:309-334`）：

```tsx
// question-mode.tsx:309-334
if (step.kind === "approval") {
  const pick = (answer as { pick: "once" | "always" | "deny" | null }).pick;
  const risk = step.risk ?? "low";   // "low" | "medium" | "high"
  const label = { once: "允许一次", always: "总是允许", deny: "拒绝" } as const;
  return (
    <>
      <div className={styles.prompt}>{withColon(step.prompt)}</div>
      {step.detail ? (
        <pre className={approvalStyles.summary + " " + (approvalStyles["risk_" + risk] ?? "")}>
          {step.detail}
        </pre>
      ) : null}
      <div className={styles.options}>
        {(["once", "always", "deny"] as const).map((p) => (
          <button className={styles.opt + (pick === p ? " " + styles.optPicked : "")}
            onClick={() => onChange({ pick: pick === p ? null : p })}>
            {pick === p ? "✓ " : ""}{label[p]}
          </button>
        ))}
      </div>
    </>
  );
}
```

`Answer` 类型 approval 分支是 `{pick:"once"|"always"|"deny"|null}`（`question-mode.tsx:74`）。危险高亮由 `approval-mode.module.css` 的 `.risk_high`/`.risk_medium`/`.risk_low` 套在 `.summary` 上；按钮用 `.opt`/`.optPicked`（选中态加 `✓`）。

### 4.3 回传 WS payload

前端 `submit()` 按 pick 发（`question-mode.tsx:162-166`）：

```js
wsSend({ action: "question_reply", id: q.id, answer: "允许", scope: "once" })   // 允许一次
wsSend({ action: "question_reply", id: q.id, answer: "允许", scope: "always" }) // 总是允许
wsSend({ action: "question_reject", id: q.id })                                 // 拒绝
```

后端处理（`webui/ws_actions/session.py:693-712`）——`scope` 存在时把 `{answer, scope}` 打包成 value，`await_user_approval` 消费时拆出 scope：

```python
# webui/ws_actions/session.py:693-712
async def handle_question_reply(ws, cmd):
    qid = cmd.get("id") or ""; answer = cmd.get("answer"); scope = cmd.get("scope")
    if qid:
        value = {"answer": answer, "scope": scope} if scope else answer
        _resolve_question(qid, "answered", value)

async def handle_question_reject(ws, cmd):
    qid = cmd.get("id") or ""; reason = cmd.get("reason")
    if qid:
        _resolve_question(qid, "declined", reason)
```

`_resolve_question`（`session.py:686-690`）薄封装 `resolve_question_and_broadcast`（`questions.py`）——WS/REST/channel `/answer` 的共享 claim-once 路径：resolve registry + 广播收回别处 UI。

后端 `await_user_approval` 返回 `(approved, reason, scope)`（`internals/_approval.py:235-305`），`scope ∈ {"once","always"}`。流程：`open_question(kind="approval",...)` → `await asyncio.to_thread(ev.wait, timeout)`（不阻塞 asyncio loop，默认 300s）→ `consume_or_timeout`：answered 时拆出 `answer`/`scope`，`answer ∈ {"允许","approve","yes","y","true","ok","是"}` → `(True, None, scope)`；declined → `(False, reason, "once")`；timeout → `retract_question` 收回卡片 → `(False, None, "once")`。

### 4.4 allow-always 写回项目规则

`_approve_then_run` 拿到 `approved=True and scope=="always"` → 写回一条 per-tool allow 规则到**项目**层：

```python
# openprogram/agent/internals/_approval.py:90-107
def _persist_always_allow_rule(session_id: str, tool_name: str) -> None:
    """把 tool_name 作为一条 per-tool allow 规则，落到项目层
    (<project>/.openprogram/settings.json 的 permission_rules.allow)。
    经 project_for_session(session_id) 反查项目，缺则用 get_default_project()。
    规则跟项目走——切会话仍生效、长期记住。"""
```

写完下次同工具 `_match_rule` 命中 allow → 不再弹。撤回误点的"总是允许"：在 Projects 页规则面板（§4.6）逐条删除。

### 4.5 权限模式选择（composer plus-menu，会话级）

权限模式选择由 `usePermissionMode` hook 驱动（`web/components/chat/composer/controls/use-permission-mode.ts`），状态存 session-store 的 `composerSettings.permission_mode`（**按会话隔离**）。5 档标签用 Claude Code 官方名（`MODE_LABELS`）：Default/默认、Accept Edits/接受编辑、Don't Ask/不再询问、Bypass/绕过权限、Plan Mode/计划模式。hook 返回 `{mode, options, menuOpen, setMenuOpen, set}`，`set()` 调 `setComposerSettings({permission_mode})`。选择项挂在 composer 的 plus-menu 里（`composer/index.tsx:344` 用该 hook）。

发送时（`composer/index.tsx:697`）把 mode 加进帧：`{action:"chat", ..., permission_mode: mode}`（仅当非空）。后端 `ws_actions/chat.py` 读 `cmd.get("permission_mode")`、存进 run config 并回填 `conv["permission_mode"]`，回给前端（session-store `index.ts:320,351` 同步）。下游 dispatcher `effective_permission = permission_from_config(run_cfg, default="bypass")`（`_execute/__init__.py:557`）塞进 TurnRequest。

### 4.6 规则管理面板（Projects 页，项目级）

规则管理 UI 在 **Projects 页**（`web/components/projects/projects-page.tsx:146-148`）：点开一个项目展开其规则面板。面板组件 `PermissionsSection`（`web/components/projects/permissions-section.tsx`）按 `projectId` 工作：

- 列出该项目的 deny / ask / allow 三组规则，每组可手动新增、逐条删除。
- 拉取/刷新走 WS：`list_permission_rules` / `add_permission_rule` / `remove_permission_rule`，请求都带 `project_id`；后端广播 `permission_rules` 帧（`session.py:742-748`）刷新面板。
- 规则字符串语法 `ToolName` 或 `ToolName(pattern)`（如 `bash(git:*)`），见 §2.2。

后端 WS handler（`webui/ws_actions/session.py:751-783`）都是**项目级**：`_resolve_project_id`（`:718-730`）支持请求直接带 `project_id`（Projects 页知道项目），或只带 `session_id` 时经 `project_for_session` 反查项目（composer 路径）；`_mutate_project_rule` 增删后 `save_project_settings` + 广播。

> 已删除：settings 的 Permissions tab、chat composer 里的 "Manage rules…" modal。规则不再在会话/全局 settings 里管，统一落项目 + Projects 页。

---

## 5. 值守（attended）——一个正交机制

值守与权限是两套独立机制，管的不是一回事：

| 机制 | 管什么 | 谁触发 |
|---|---|---|
| **权限模式** | 模型调工具时是否要用户批准 | 模型发起工具调用 |
| **值守（attended）** | 模型是否有权主动向用户提问 | 模型想调 `ask_user_question` |

值守在 `openprogram/agent/attended.py`。核心（`attended.py:1-23`）：长跑要么"有人看着能回答"（attended），要么"人走开了别问"（unattended）。控制手段——unattended 时不把提问工具给模型。状态：进程级默认 `_default = False`（`:33`，默认 unattended）+ 按 session 覆盖 `_by_session`（`:34`）。落地：`denied_ask_tools`（`:64-68`）在 unattended 时把 `ask_user_question` 折进工具解析 deny 集，运行时侧 `runtime.py:1516` 引用。设置入口 `set_attended(value, session_id)`（`:38-46`），web 经 `ws_actions/runtime.py:503-504` 的 `handle_set_attended`（per-session）。

**配合**：权限模式管"工具执行要不要批准"，值守管"模型能不能开口问"。unattended + bypass = 既不停下问、工具全直接执行（无人值守自动跑）；attended + ask = 可提问、每工具要点头（盯着干）。二者正交，任意组合。

---

## 6. 关键约束与代码地图

### 6.1 改动权限代码时必须守住的性质

- **deny/ask 早于 bypass**：`_gated_execute`（`internals/_approval.py:151-188`）里规则层 deny/ask（① ②）必须在 bypass 短路（③）之前。web 默认就是 bypass，把 deny/ask 挪到 bypass 之后会让"禁止 rm -rf"被静默忽略——安全缺陷。
- **exit_plan_mode 强制审批**：`_FORCE_APPROVAL_TOOLS`（`:34`）在 bypass 下也弹卡片；提交计划要用户签字。
- **模式 vs 规则的作用域**：权限模式是**会话级**（session meta），权限规则是**项目级**为主（`<project>/.openprogram/settings.json`）。别把两者的存储混起来。
- **驼峰规范化**：`acceptEdits`/`dontAsk` 是驼峰规范值，一切比较走 `_normalize_permission` 的大小写不敏感表（`session_config.py:271-275`），不要直接 `.lower()` 后当规范值用。
- **acceptEdits 只放路径安全的写工具**：命令类（bash/exec/execute_code）无论如何 fall-through 到审批（⑥ 只对 `_accept_edits_safe=True` 且 `_path_is_safe` 的工具放行）。

### 6.2 代码地图

| 关注点 | 代码位置 |
|---|---|
| 判定链 `_gated_execute` / `_match_rule` / `await_user_approval` / `_persist_always_allow_rule` / `_risk_level` | `openprogram/agent/internals/_approval.py` |
| 规则字符串解析、匹配、多层合并 | `openprogram/functions/permission_rule.py`（`parse_rule` / `parse_command` / `pattern_matches` / `load_merged_rules`） |
| 路径安全 / 危险文件目录 / Windows 绕过 | `openprogram/functions/tools/file_safety.py` |
| gate 硬拦截 | `openprogram/agent/tool_gate.py` |
| 权限模式合法值 + 规范化 + SessionRunConfig 字段 | `openprogram/agent/session_config.py` |
| `PermissionMode` 类型 + TurnRequest 字段/默认 | `openprogram/agent/dispatcher/types.py` |
| 会话 meta schemaless 存储 | `openprogram/store/session/session_store.py` |
| 项目级 settings 读写 + `project_for_session` | `openprogram/store/project/project_store.py` |
| `accept_edits_safe` 声明 + per-tool `requires_approval` | `openprogram/functions/_runtime.py`；工具标记在 `openprogram/functions/tools/{read,write,edit,glob,grep,list}/` |
| web 默认 bypass + effective_permission | `openprogram/webui/_execute/__init__.py`；`additional_working_dirs` 填充在 `_execute/chat.py`、`channels/_conversation.py` |
| WS：审批应答 + 项目规则 list/add/remove | `openprogram/webui/ws_actions/session.py`、`chat.py` |
| 值守（正交机制） | `openprogram/agent/attended.py`、`openprogram/webui/ws_actions/runtime.py` |
| 前端审批卡片（approval mode） | `web/components/chat/composer/modes/question/question-mode.tsx` + `../approval/approval-mode.module.css` |
| 前端权限模式选择（会话级 hook） | `web/components/chat/composer/controls/use-permission-mode.ts` + `composer/index.tsx` |
| 前端规则面板（项目级） | `web/components/projects/projects-page.tsx` + `web/components/projects/permissions-section.tsx` |

---

## 7. 不做的边界

只列真没有物理载体、或加了必然冲突无法消解的。不是"为省事不做"。

- **企业策略层（policy/flag 来源）**：无特性开关、无企业 MDM/策略下发后台。§2.3 的三层来源全部落地，唯独企业层无载体承接。未来接企业部署可补。
- **local / cliArg 规则层**：无 `.openprogram/settings.local.json`、无 `--allow-tool` 之类的 CLI 标志。等价能力由项目层 + 会话层覆盖。
- **LLM 分类器审批档（旧 `auto`）**：一套独立分类器判定引擎（模型 API 调用 + transcript 准备 + denial 累计限流）。`auto` 档已从对外档删除——它是判定引擎替换，不是权限规则，属独立工作线。
- **外部审批委托（permissionPromptTool）**：把审批决策委托给外部 MCP 工具。统一走 QuestionRegistry + 前端卡片，无此机制。未来接自定义审批后端可补。
- **沙箱隔离（安全边界）**：权限系统是决策与知情层，不是安全边界，不做进程/文件隔离。真正隔离是独立 sandbox 工作线。
- **bypass 免疫的 safetyCheck 强制审批**：`tool_requires_approval` 仍是 `(bool, reason)`，无 `classifier_approvable`；路径安全只在 acceptEdits 分支起作用，bypass 下写危险文件不会被强制拦。`is_dangerous_allow_rule`（`file_safety.py:94-100`）已实现但未接线。若要补 bypass 免疫，需把 `tool_requires_approval` 扩成三元组、在 `_gated_execute` ① 处把"路径不安全"视为 ask。
