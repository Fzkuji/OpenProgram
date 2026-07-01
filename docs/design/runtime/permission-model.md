# 权限系统设计（Permission System Design）

本文是 OpenProgram 权限系统的**实现级设计文档**：一个不了解代码的人读完，就知道权限系统是什么、有哪些权限、后端怎么写、前端怎么写、代码写在哪些文件。每个数据结构给字段定义，每个关键函数给签名，每个 WS 帧给字段，每个前端改动给组件结构。所有引用带 `file:line`，指向当前 `main` 工作区。

阅读顺序：**① 概览**（是什么、怎么运作）→ **② 有哪些权限**（模式与规则的定义）→ **③ 后端怎么实现**（判定、匹配、存储）→ **④ 前端怎么实现**（审批卡片、模式开关、规则管理）→ **⑤ 值守**（一个正交机制）→ **⑥ 落地分步** → **⑦ 不做的边界**。

---

## 1. 概览

### 1.1 权限系统解决什么

模型想调用一个工具（bash / write / …）时，只有三种可能的处置：**直接执行**、**先问用户**、**直接拒绝**。权限系统就是决定每一次工具调用走哪条路的机制。它不是安全沙箱（不做进程/文件隔离），是**决策与知情层**——让用户能控制"什么自动、什么要点头、什么绝不允许"，并在需要点头时看清批的是什么。

### 1.2 四部分怎么协作

权限判定由四个部分串成一条决策链，从硬到软：

| 部分 | 管什么 | 能否被 bypass 关掉 | 位置 |
|---|---|---|---|
| **gate（硬拦截）** | 策略层的绝对禁止（proactive policy 的 deny/ask） | 否，永远生效 | `openprogram/agent/tool_gate.py` |
| **规则层** | 用户配的 allow / deny / ask 规则（per-tool + per-pattern，多来源分层） | deny/ask 否；allow 是 | `openprogram/agent/internals/_approval.py`（`_match_rule`）+ `openprogram/functions/permission_rule.py`（新） |
| **权限模式** | 全局档位：ask / acceptEdits / plan / dontAsk / bypass（对齐 Claude Code 5 档） | 档位本身就是这个开关 | `_gated_execute`（`internals/_approval.py:74-102`） |
| **审批流** | 需要点头时的前后端交互（弹卡片、阻塞等答、写回记忆） | 否（弹出即阻塞） | `await_user_approval`（`internals/_approval.py:135-200`）+ 前端 QuestionMode |

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
│ _gated_execute (internals/_approval.py:74-102)                 │
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
│   → 前端 QuestionMode 渲染审批卡片                               │
│   → 用户点 允许一次 / 总是允许 / 拒绝                             │
│   → question_reply/question_reject → _resolve_question          │
│   → threading.Event 唤醒 → consume_or_timeout → (approved,      │
│      reason, scope)                                             │
│   scope=="always" → 写回 allow 规则落盘                         │
└──────────────────────────────────────────────────────────────┘
        │
        ▼
   approved → orig_execute   |   denied/timeout → [denied] error result
```

---

## 2. 有哪些权限（定义）

这一节只讲"是什么"——权限模式和规则的定义。怎么实现在第 3、4 节。

### 2.1 权限模式（5 档）

权限模式定义在 `openprogram/agent/dispatcher/types.py:19`，合法值集在 `openprogram/agent/session_config.py:23`（对齐 Claude Code 5 档；本设计新增 `acceptEdits`/`plan`/`dontAsk`，无 `auto`）：

```python
# openprogram/agent/dispatcher/types.py:19
PermissionMode = Literal["ask", "acceptEdits", "plan", "dontAsk", "bypass"]

# openprogram/agent/session_config.py:23
VALID_PERMISSION = {"ask", "acceptEdits", "plan", "dontAsk", "bypass"}
```

| 模式 | 行为 |
|---|---|
| `ask` | 每个工具调用都弹审批卡片阻塞等答（除非规则 allow 或 per-tool 声明不需审批）。逐次问。 |
| `acceptEdits` | 对**写类且路径安全**的工具（read/write/edit/glob/grep/list，且目标在工作目录内、非危险文件）自动放行；bash/exec/shell 等命令类**仍走完整审批**。 |
| `plan` | 计划态。写类工具在此模式对模型不可见（`apply_tool_policy(source="plan")`），且记录进入前的档位到 `pre_plan_mode`，退出（`exit_plan_mode`）时恢复。 |
| `dontAsk` | 本该弹卡片的调用直接返回 `[denied]`，绝不打断用户。等价"全拒需要人工确认的操作"。 |
| `bypass` | 全部直接放行，不弹审批。**例外**：`exit_plan_mode` 强制审批（`_force_approval_tools`，`internals/_approval.py:72`）；规则层 deny/ask 仍生效。 |

> 大小写坑：`acceptEdits` / `dontAsk` 是驼峰，`_normalize_permission`（`session_config.py:235-239`）现在做 `.lower()` 会把它们规成 `acceptedits` / `dontask`。新增这两档时须把 `VALID_PERMISSION` 也存驼峰并改比较为大小写不敏感（`mode in {m.lower() for m in VALID_PERMISSION}` 后映射回规范值），否则驼峰档永远被判非法。这是新增档位的第一个必改点。

### 2.2 规则（allow / deny / ask 三平行）

规则是用户在运行时叠加的覆盖，与档位正交。三个平行 list，规则的 behavior 由它住在哪个 list 决定，不由字符串自带字段：

```python
@dataclass
class PermissionRules:
    allow: list[str] = field(default_factory=list)
    deny:  list[str] = field(default_factory=list)
    ask:   list[str] = field(default_factory=list)
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
@dataclass(frozen=True)
class PermissionRuleValue:
    tool_name: str
    pattern: str | None = None   # None = per-tool；非 None = per-pattern
```

### 2.3 规则来源（5 层）

规则可来自多个来源，低优先级在前、高优先级在后，后进覆盖前进。只映射真实存在的载体（无企业策略后台/特性开关，见第 7 节边界）：

| 层 | 优先级 | 载体 | 可写 |
|---|---|---|---|
| user（全局） | 最低 | 全局 settings.json 的 `tools.permission_rules`（`config_schema.py` 加 `SettingSpec`） | 是 |
| project（团队共享，进版本库） | ↑ | `.openprogram/settings.json` 的 `tools.permission_rules` | 是 |
| local（项目本地，不进版本库） | ↑ | `.openprogram/settings.local.json` 的 `tools.permission_rules` | 是 |
| cliArg（本次进程） | ↑ | CLI `--allow-tool` / `--deny-tool` / `--ask-tool`，只内存 | 是（内存） |
| session（本会话） | 最高 | `SessionRunConfig.permission_rules`，随会话落 SessionDB | 是 |

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

关键性质：**gate 在权限审批包装之外，`bypass` 关不掉它**（`tool_gate.py:14-15`），对 subagent 同样生效。gate 是策略层（proactive policy 的 Gate allow/deny/ask）的硬拦截点，必须快（同步热路径，不许 LLM / 慢 IO）。

### 3.2 审批包装（关卡 B）与决策伪代码

工具进入 dispatcher 时被逐个包一层审批（`dispatcher/__init__.py:784`）：`tools = [_wrap_with_approval(t, req, on_event) for t in tools]`。包在工具协程**内部**，因为 agent_loop 急切调度 `tool.execute`，从外面拦有竞态（`internals/_approval.py:56-60`）。`_gated_execute` 是被替换进去的 execute。改造后完整判定顺序：

```python
# openprogram/agent/internals/_approval.py:74-102（改造后）
_force_approval_tools = {"exit_plan_mode"}  # :72

async def _gated_execute(call_id, args, cancel, on_update):
    name = agent_tool.name
    force_ask = name in _force_approval_tools

    # ── ① 规则层 deny/ask —— bypass 之前，最高安全优先级 ──
    verdict = _match_rule(req.permission_rules, name, args)   # 3.4
    if verdict == "deny":
        return _denied_result(f"[denied] blocked by deny rule: {name}")
    if verdict == "ask":
        return await _do_approval(call_id, args, cancel, on_update)  # 即使 bypass 也弹

    # ── ② force_ask（exit_plan_mode），bypass 也不能跳 ──
    if force_ask:
        return await _do_approval(call_id, args, cancel, on_update)

    # ── ③ bypass 短路（deny/ask/force 之后）──
    if req.permission_mode == "bypass":
        return await orig_execute(call_id, args, cancel, on_update)

    # ── ④ dontAsk：本该问的直接拒 ──
    if req.permission_mode == "dontAsk":
        if _would_need_approval(agent_tool, args, req.permission_mode):
            return _denied_result("[denied] dontAsk mode: approval required")
        return await orig_execute(call_id, args, cancel, on_update)

    # ── ⑤ 规则层 allow —— bypass 之后 ──
    if verdict == "allow":
        return await orig_execute(call_id, args, cancel, on_update)

    # ── ⑥ acceptEdits：写安全工具自动放行 ──
    if req.permission_mode == "acceptEdits":
        if getattr(agent_tool, "_accept_edits_safe", False) \
           and _path_is_safe(name, args, req):        # 3.3 / 3.5
            return await orig_execute(call_id, args, cancel, on_update)
        # 命令类工具（bash 等）在 acceptEdits 下不自动放行 → 落 ⑦


    # ── ⑦ 弹卡片阻塞等答 ──
    return await _do_approval(call_id, args, cancel, on_update)

async def _do_approval(call_id, args, cancel, on_update):
    approved, reason, scope = await await_user_approval(
        req=req, tool_name=agent_tool.name, args=args, on_event=on_event)
    if not approved:
        return _denied_result(reason or f"user did not approve {agent_tool.name}")
    if scope == "always":
        _persist_always_allow_rule(req.session_id, agent_tool.name)  # 4.4
    return await orig_execute(call_id, args, cancel, on_update)
```

**deny 早于 bypass 的安全约束（全设计最关键）**：deny/ask 规则匹配（① ②）必须在 bypass 短路（③）之前。反例：若把规则整块插在 bypass 之后，则 web 默认 bypass（`_execute/__init__.py:557`）下，用户配的 `deny: ["bash(rm -rf:*)"]` 永远不被查到——rm -rf 被静默执行。所以 deny/ask 查在 bypass 之前、allow 查在 bypass 之后。现状 `_gated_execute` 是 bypass 短路在最前，改造把 deny/ask 提前、allow 放后，并加 dontAsk/acceptEdits 分支。

### 3.3 各权限模式的分支实现

对应 3.2 伪代码编号：

- **acceptEdits（⑥）**：三件事——① `@function` 加 `accept_edits_safe: bool = False`（`functions/_runtime.py`），给 read/write/edit/glob/grep/list 标 `True`，bash/exec/shell 保持 `False`；② `_path_is_safe` 复用 3.5 的 `check_path_safety`（路径不在危险清单、无 Windows 绕过、在工作目录集内）；③ 命令类（bash/exec/shell）即使有宽 allow 也 fall-through 到 ⑦ 强制审批。
- **plan（可见性 + 联动）**：两层含义。可见性——`apply_tool_policy(tools, source="plan")`（`dispatcher/__init__.py:779-781`）滤掉 `unsafe_in` 含 `"plan"` 的写类工具，根本不进模型工具列表；档位联动——进 plan 记 `pre_plan_permission_mode`、退出恢复（3.6）。`_gated_execute` 无 plan 专属分支（写类已被滤掉，只读工具按 ask 常规走）。
- **dontAsk（④）**：ask → deny。`_would_need_approval(agent_tool, args, mode)` 复用 ⑦ 判定（per-tool required / 高风险 / ask 全量）判断这次在非 dontAsk 下会不会需要审批；会 → `[denied]`；不会 → 直接执行。规则层 deny/ask（①）仍在其前生效，allow（⑤）不受影响。
- **ask**：不命中 allow、per-tool 不免审的工具全部落 ⑦。
- **bypass（③）**：deny/ask/force 之后全部直接执行。

### 3.4 规则匹配 `_match_rule`

```python
# openprogram/agent/internals/_approval.py（新增）
def _match_rule(rules: "PermissionRules | None", tool_name: str, args: dict) -> "str | None":
    """返回 "deny" | "ask" | "allow" | None（未命中）。
    优先级固定 deny > ask > allow：先扫 deny 命中即返回，再 ask，再 allow。
    每档内部：先试 per-tool（rule.pattern is None 且 tool_name 相等），
    再试 per-pattern（rule.pattern 对 parse_command(tool_name, args) 前缀/glob 匹配）。"""
    if rules is None:
        return None
    for behavior, ruleset in (("deny", rules.deny), ("ask", rules.ask), ("allow", rules.allow)):
        for raw in ruleset:
            rv = parse_rule(raw)
            if rv.tool_name != tool_name:
                continue
            if rv.pattern is None:                      # per-tool
                return behavior
            cmd = parse_command(tool_name, args)        # 见下
            if cmd is not None and _pattern_matches(rv.pattern, cmd):
                return behavior
    return None
```

- **per-tool**（`rv.pattern is None`）：`rv.tool_name == tool_name` 命中整工具。例 `deny: ["bash"]` 拦所有 bash。
- **per-pattern**（`rv.pattern` 非空）：先取可比命令串 `cmd = parse_command(...)`，再 `_pattern_matches`：`:*` 结尾→前缀匹配（`git:*` 匹配 `git status`、不匹配 `github`）；含 glob→`fnmatch`（`/etc/**` 匹配 `/etc/passwd`）；否则精确相等。

命令解析器 + 规则解析（新文件 `openprogram/functions/permission_rule.py`）：

```python
def parse_rule(s: str) -> PermissionRuleValue: ...
def rule_to_string(v: PermissionRuleValue) -> str: ...   # 与 parse_rule 对偶
def parse_command(tool_name: str, args: dict) -> str | None:
    """把工具参数归约成可比字符串（per-pattern 匹配用）。
    bash/exec/shell → args["command"]；read/write/edit → args["path"]；
    其余无可比字段 → None（per-pattern 对其不生效，只 per-tool 可拦）。"""
```

各层规则合并（`dispatcher/__init__.py`，构造 TurnRequest 前调）：

```python
def _merge_permission_rules(user, project, local, cli, session) -> PermissionRules:
    """按优先级 user < project < local < cli < session 顺序拼接三 list。
    来源顺序只影响同一 behavior 内先后；因 _match_rule 命中即返回，
    deny 永远整体先于 ask 先于 allow，不受来源顺序干扰。这 5 层都可写。"""
    merged = PermissionRules()
    for layer in (user, project, local, cli, session):
        if layer:
            merged.allow += layer.allow; merged.deny += layer.deny; merged.ask += layer.ask
    return merged
```

合并结果填进 `TurnRequest.permission_rules`（`dispatcher/types.py` 新增字段），各构造点（web `_execute/__init__.py`、channel `_conversation.py`、CLI）各加一行填充。

**与 per-tool `requires_approval` 的关系**：两层并存互补。`@function(requires_approval=...)`（`functions/_runtime.py:722, :772`）是工具作者写死的声明（`True`/`False`/`None`/`callable(**args)->bool|str`），求值在 `_evaluate_approval`（`:543-568`，callable 抛异常保守要求审批），dispatcher 经 `tool_requires_approval`（`:1043-1047`）读。规则层是用户运行时覆盖，跑在 per-tool 之前（① ⑤ 在 ⑦ 之前）。

### 3.5 危险检测与路径安全

**RiskLevel + 卡片高亮**：

```python
# openprogram/agent/internals/_approval.py（新）
def _risk_level(tool_name: str, args: dict) -> str:
    """返回 "low"|"medium"|"high"。
    high：bash 含 rm -rf / sudo / 管道到 shell / Bash(*) 泛匹配；写危险文件。
    medium：写工作目录外；git push。 low：只读工具。"""
```

`_approval_detail`（`internals/_approval.py:122-132`，现状生成"工具名 + 参数全文，超长首尾截断"）扩成携带 `risk_level`，`_on_asked`（`:173-181`）的 `question.asked` 帧带上它，前端据此上色（4.2）。

**路径安全**（新文件 `openprogram/functions/tools/file_safety.py`）：

```python
DANGEROUS_FILES = [".gitconfig", ".gitmodules", ".bashrc", ".bash_profile",
                   ".zshrc", ".zprofile", ".profile", ".mcp.json", ".claude.json"]
DANGEROUS_DIRECTORIES = [".git", ".vscode", ".idea", ".openprogram"]
DANGEROUS_BASH_PATTERNS = ["python","python3","node","deno","ruby","perl",
                           "php","sh","bash","eval","exec","sudo","ssh","npx"]

def check_path_safety(path: str, working_dirs: list[str]) -> dict:
    """返回 {safe, message, classifier_approvable}。
    不安全：命中 DANGEROUS_FILES / 段命中 DANGEROUS_DIRECTORIES / 目标在
    working_dirs 之外 / Windows 绕过（NTFS 流 ::$DATA、8.3 短名 ~1、UNC \\、
    尾部点空格、DOS 设备名 CON/PRN、三连点 .../）。工作目录集 = cwd +
    req.additional_working_dirs（3.5 额外目录）。"""
```

**safetyCheck（bypass 免疫）**：`tool_requires_approval`（`functions/_runtime.py:1043-1047`）返回从 `(bool, reason)` 扩成 `(bool, reason, classifier_approvable)`。write/edit/bash 集成 `check_path_safety`，不安全路径即使 bypass 也强制审批——在 3.2 中通过"path 不安全时把 verdict 视为 ask"实现（插在 ① 处），保证 bypass 免疫。

**危险模式剥离**：进 `acceptEdits` 档时（`session_config.py:155` 档切换处）扫 allow 规则，把危险的（如 `bash(python:*)`）临时剥离、退出恢复，防止过宽 allow 规则在 acceptEdits 下绕过审批。`is_dangerous_allow_rule(tool_name, pattern)` 用 `DANGEROUS_BASH_PATTERNS` 判定。

**额外工作目录**：`SessionRunConfig.additional_working_dirs`（3.6）是路径安全的工作目录集扩展，`check_path_safety` 的 `working_dirs = [cwd] + req.additional_working_dirs`。用户可加"这个目录也算安全区"，acceptEdits 与路径安全都读它；缺它则只认死一个 cwd。

### 3.6 存储：session meta schemaless + SessionRunConfig

**session 层是 schemaless 的**——这是全部权限持久化不需要 DB migration 的原因。`SessionDB.update_session(session_id, **fields)`（`store/session/session_store.py:602-639`）把 `head_id` 特殊路由到 `idx.set_head()`，其余任意字段（`permission_mode`/新增的 `permission_rules`/`pre_plan_permission_mode`/`additional_working_dirs`）全部经 `idx.set_meta(**clean)` 落进 session meta。所以加权限字段只改 `session_config.py` 的 load/save，旧会话读回不报错。

```python
# openprogram/store/session/session_store.py:602-639
def update_session(self, session_id, **fields):
    """head_id → set_head()；其余字段 → set_meta(**clean)。schemaless。"""
def get_session(self, session_id):
    """行形 dict：固定键 id/title/agent_id/source/model/created_at/updated_at/
    last_node_id(=idx.head_id，无独立 head_id 键)；其余 meta 序列化进 extra_json，
    经 _row_to_session 二次成形（_msg_adapter.py:232-233 setdefault 直通）。"""
```

```python
# openprogram/agent/session_config.py:29-39（新增字段）
@dataclass
class SessionRunConfig:
    tools_enabled: Optional[bool] = None
    tools_override: ToolsOverride = None
    web_search: Optional[bool] = None
    toolset: Optional[str] = None
    thinking_effort: Optional[str] = None
    permission_mode: Optional[str] = None
    # ── 新增（本设计）──
    permission_rules: Optional[PermissionRules] = None          # 2.2
    pre_plan_permission_mode: Optional[str] = None              # 3.7 plan 联动
    additional_working_dirs: list[str] = field(default_factory=list)  # 3.5 路径安全

# :154-156
def permission_from_config(cfg, *, default: str) -> str:
    return _normalize_permission(cfg.permission_mode) or default
```

**默认值三处**（`permission_from_config` 的 default 决定 session 未设置时落哪）：

| 入口 | 默认 | 位置 |
|---|---|---|
| TurnRequest 数据类字段 | `ask` | `dispatcher/types.py:53` |
| Web 执行路径 | `bypass` | `webui/_execute/__init__.py:557` |
| 渠道（channels） | `ask` | `channels/_conversation.py:237` |

子 agent 固定 `bypass`（`sub_agent_run.py:89`）：子 agent 的 lane 上没有 UI 订阅审批事件，ask 会让每工具超时 `[denied]`；且"派生子 agent"本身已是用户显式动作。

### 3.7 plan 联动（prePlanMode 记录恢复）

进 plan 记录当前档位、退出恢复。用 in-memory per-session 上下文（plan 中间态不该持久化）或 `SessionRunConfig.pre_plan_permission_mode`。

- **进 plan**：`pre_plan = 当前 permission_mode`；`permission_mode = "plan"`。写类工具经 `apply_tool_policy(source="plan")` 滤掉。
- **退 plan**（`exit_plan_mode` 批准回调——已是 `_force_approval_tools`，bypass 也强制审批）：`permission_mode = pre_plan or "ask"`；清 `pre_plan`。含降级保护：恢复目标档不可用则降到 `ask`。
- **bypass 可用性**：记进 plan 前是否为 bypass，退出时据此决定是否允许恢复到 bypass。

可选新文件 `openprogram/agent/permission_context.py` 持有 plan 期 `pre_plan_mode`（per-session in-memory）。

---

## 4. 前端怎么实现

前端三件事：审批卡片（收 question.asked、渲染三选一）、权限模式开关（composer pill）、规则管理面板（settings 页删规则）。

### 4.1 审批卡片入口

唯一入口组件是 `QuestionMode`（`web/components/chat/composer/modes/question/question-mode.tsx:129-232`），按 `kind` 分支。收到 `question.asked` 帧（字段见下）后进入 approval 分支渲染卡片。

`question.asked` 帧字段（后端 `emit_question_asked` 发，`internals/_approval.py:173-181`）：

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

`tool`/`args` 是 approval 专属（`:179-180`），给前端画危险摘要；`risk_level` 本设计新增，驱动高亮。

### 4.2 审批卡片三选一 + 危险高亮

approval 分支现状渲染两颗按钮（允许/拒绝，`question-mode.tsx:308-327`；点击提交发 WS 在 `submit()` `:162-169`）。本设计改成三选一（允许一次 / 总是允许 / 拒绝）+ 危险高亮：

```tsx
// question-mode.tsx:308-327（目标）
if (step.kind === "approval") {
  const pick = (answer as { pick: "once" | "always" | "deny" | null }).pick;
  const risk = step.risk_level ?? "low";   // "low" | "medium" | "high"
  return (
    <>
      <div className={styles.prompt}>{withColon(step.prompt)}</div>
      {step.detail ? (
        <pre className={approvalStyles.summary + " " + approvalStyles["risk_" + risk]}>
          {step.detail}
        </pre>
      ) : null}
      <div className={styles.options}>
        {(["once", "always", "deny"] as const).map((p) => (
          <button className={styles.opt + (pick === p ? " " + styles.optPicked : "")}
            onClick={() => onChange({ pick: pick === p ? null : p })}>
            {pick === p ? "✓ " : ""}
            {p === "once" ? "允许一次" : p === "always" ? "总是允许" : "拒绝"}
          </button>
        ))}
      </div>
    </>
  );
}
```

`Answer` 类型（`question-mode.tsx:72-75`）approval 分支从 `{pick:"allow"|"deny"|null}` 改为 `{pick:"once"|"always"|"deny"|null}`。危险高亮：CSS module 加 `.risk_high`（红边/红底）、`.risk_medium`（橙）、`.risk_low`（绿），套在现有 `.summary` 上。按钮沿用 `.opt`/`.optPicked`（30px pill，选中 `color-mix(accent-blue @ 18%)` + 蓝字 + `✓`）。

### 4.3 回传 WS payload

前端 `wsSend`（`question-mode.tsx:30-35`），`submit()` 按 pick 发（`:162-169`）：

```js
wsSend({ action: "question_reply", id: q.id, answer: "允许", scope: "once" })   // 允许一次
wsSend({ action: "question_reply", id: q.id, answer: "允许", scope: "always" }) // 总是允许
wsSend({ action: "question_reject", id: q.id, reason: "<可选理由>" })            // 拒绝
```

后端处理（`webui/ws_actions/session.py:693-708`）：

```python
async def handle_question_reply(ws, cmd):          # :693-698
    qid = cmd.get("id") or ""; answer = cmd.get("answer")
    scope = cmd.get("scope", "once")               # 新增
    if qid: _resolve_question(qid, "answered", answer, scope=scope)

async def handle_question_reject(ws, cmd):         # :701-708
    qid = cmd.get("id") or ""; reason = cmd.get("reason")
    if qid: _resolve_question(qid, "declined", reason)
```

`_resolve_question`（`session.py:686-708` + REST 收口 `webui/routes/questions.py:19-55`）是 WS/REST/channel `/answer` 的共享 claim-once 路径：resolve registry + 广播 `question.replied`/`question.rejected` 收回别处 UI。`scope` 作为 value 附带信息传回 `await_user_approval`（value 携带 `{"answer":..., "scope":...}`，消费时拆出 scope）。REST：`GET /api/questions` 列 pending；`POST /api/questions/{qid}/reply` body `{answer, scope?}`；`POST /api/questions/{qid}/reject`。

后端 `await_user_approval` 返回从 `(approved, reason)` 扩成 `(approved, reason, scope)`（`internals/_approval.py:135-200`），`scope ∈ {"once","always"}`。流程不变：`open_question(kind="approval",...)` → `await asyncio.to_thread(ev.wait, timeout)`（不阻塞 asyncio loop，默认 300s）→ `consume_or_timeout`：answered 且 value ∈ `{"允许","approve","yes","y","true","ok","是"}` → `(True, None, scope)`；declined → `(False, reason, "once")`；timeout → `retract_question` 收回卡片 → `(False, None, "once")`。

### 4.4 allow-always 写回 / 删规则

`_gated_execute` 拿到 `approved=True and scope=="always"` → 写回 allow 规则：

```python
def _persist_always_allow_rule(session_id, tool_name, destination="session"):
    """把 rule_to_string(PermissionRuleValue(tool_name)) 追加进目标层 allow。
    destination="session"（默认）→ load/save_session_run_config → 落 session meta。
    destination="local" → 写 .openprogram/settings.local.json 的 tools.permission_rules.allow。"""
def _remove_always_rule(session_id, rule, destination):
    """从目标层 allow/deny/ask 移除一条规则字符串（撤回误点的"总是允许"）。"""
```

默认写 session 层；卡片可选"记住到本项目"→ 写 local 层。下次同工具 `_match_rule` 命中 allow → 不再弹。删规则面板在 settings 权限页（4.6）列各层已记住规则、逐条删 → `wsSend({action:"remove_permission_rule", rule, destination})` → `ws_actions` 新 handler 调 `_remove_always_rule`。

### 4.5 权限模式开关（composer pill）

前端加权限模式 pill，仿 thinking-effort pill（`composer/controls/thinking-effort-pill.tsx` + `use-thinking-effort.ts`）。现状 composer 无权限控件——协议层已支持带 `permission_mode`（`ws_actions/chat.py:312`），但前端总走后端默认（web=bypass）。

```
web/components/chat/composer/controls/
  permission-mode-pill.tsx     ← 仿 thinking-effort-pill.tsx
  use-permission-mode.ts       ← 仿 use-thinking-effort.ts
```

`PermissionModePill` props `{expanded, onToggle, value, onChange}`，展开列 6 档（中文标签"逐次问/自动/自动批写/计划/不打断/全通过"）。`usePermissionMode` 返回 `{mode, options, menuOpen, setMenuOpen, set}`，`set()` 调 `setComposerSettings({permission_mode: mode})`（session-store 按会话隔离）。

发送时（`composer/index.tsx:663-681`）把 mode 加进帧：`send({action:"chat", ..., permission_mode: mode})`。后端 `ws_actions/chat.py:312` 已读 `cmd.get("permission_mode")`，`:475-482` 存进 run config 并回填 `conv["permission_mode"]`，回给前端的 `chat_ack`/消息帧带 `permission_mode`，前端据此同步 pill。下游 dispatcher `effective_permission = permission_from_config(run_cfg, default="bypass")`（`_execute/__init__.py:557`）塞进 TurnRequest。CLI/TUI 同理加 `--permission` 标志 + `/permission` 命令。

### 4.6 规则管理面板（settings 页）

settings 权限页挂点（`web/components/settings/settings-tabs-layout.tsx:10-118`）：新建 `app/(shell)/settings/permissions/page.tsx`，`SettingsTab` union 加 `"permissions"`，导航加 `<Link href="/settings/permissions">`，active 判定加 `pathname.startsWith("/settings/permissions")`。页面列各层（session/local/project/user）已记住的 allow/deny/ask 规则，支持逐条删除（4.4）和手动新增。

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

## 6. 落地分步

每步可独立验证，标依赖。主线 S1→S5 先整体交付（三平行规则 + allow-always + 前端三选一）；其余并行。

| 步 | 内容 | 依赖 | 验证 |
|---|---|---|---|
| S1 | `SessionRunConfig` 加 `permission_rules` + `pre_plan_permission_mode` + `additional_working_dirs` + load/save（`session_config.py:29-39,:60-76,:78-109`）。schemaless 无 migration | 无 | 写规则→重载 session 字段还在；旧会话读回不报错 |
| S2 | `TurnRequest` 加 `permission_rules` + 各构造点填充（`dispatcher/types.py` + web/channel/CLI） | S1 | 规则从 session 流到 dispatcher |
| S3 | `permission_rule.py`（`parse_rule`/`rule_to_string`/`parse_command`）+ `_match_rule` + `_gated_execute` 两段插：deny/ask 在 bypass 前、allow 在后（`internals/_approval.py:74-102`） | S2 | 手塞 allow→不弹；deny→即使 bypass 也 `[denied]`；ask→即使 bypass 也弹 |
| S4 | `await_user_approval` 返回加 `scope` + `_persist_always_allow_rule` 写回 session allow（`internals/_approval.py:135-200`） | S3 | 点"总是允许"→下次同工具不弹 |
| S5 | 前端三选一按钮 + `scope`/destination 回传 + `handle_question_reply` 读 scope（`question-mode.tsx:308-327,:162-168` + `ws_actions/session.py:693-698`） | S4 | 浏览器点三按钮各自行为正确（自查）；"记住到本项目"写 local |
| S6 | 多层来源：全局 `config_schema.py`、project/local `.openprogram/settings*.json`、cliArg 标志 + `_merge_permission_rules`（`dispatcher/__init__.py`） | S1、S3 | 各层写 deny→按 session>cli>local>project>user 生效 |
| S7 | 加 `acceptEdits`/`dontAsk`/`plan` 档 + `_normalize_permission` 大小写修正（`session_config.py:23,:235-239` + `dispatcher/types.py:19`） | 默认值三处（3.6）确认 | 三档能存取、驼峰不被误判非法 |
| S8 | `@function` 加 `accept_edits_safe` + 文件类工具标记 + `_gated_execute` acceptEdits 分支（含 bash 强制审批）（`functions/_runtime.py` + `functions/tools/*` + `internals/_approval.py`） | S7、S13 | acceptEdits 下写文件不弹、bash 仍弹 |
| S9 | plan 联动：进 plan 记 `pre_plan_mode`、`exit_plan_mode` 回调恢复（`dispatcher/__init__.py:779-781` + exit_plan_mode 回调 + `permission_context.py`） | S7 | ask→plan→退出回 ask；bypass 进的 plan 退出可回 bypass |
| S10 | per-pattern：`parse_command` + `_match_rule` 支持 `tool(pattern)`（`permission_rule.py` + `internals/_approval.py`） | S3 | `bash(git:*)` allow 放行 `git status`、不放行 `rm -rf` |
| S11 | 权限模式 pill：`permission-mode-pill.tsx` + `use-permission-mode.ts` + composer 塞 payload（4.5） | S7 | 前端能选 6 档、随会话隔离、后端收到（自查） |
| S12 | 危险分级 `_risk_level` + detail 结构化 + `risk_level` 入帧 + 前端高亮（`internals/_approval.py:122-132,:173-181` + `question-mode.tsx` + CSS） | 无 | `rm -rf` 卡片标红、只读标绿（自查） |
| S13 | 路径安全 `file_safety.py`（`check_path_safety` + Windows 绕过 + `DANGEROUS_*`）+ `tool_requires_approval` 返回加 `classifier_approvable` + 集成 write/edit/bash + 危险规则剥离（`functions/tools/file_safety.py` + `functions/_runtime.py:1043-1047` + `session_config.py:155`） | S3 | 写 `.git/`/`.bashrc` 即使 bypass 也强制审批；acceptEdits 下 `bash(python:*)` allow 被剥离 |
| S14 | removeRules 面板：settings 权限页列规则 + 逐条删 + `_remove_always_rule` + WS handler（4.4、4.6） | S5、S6 | 误点"总是允许"后能在面板删掉、下次重新弹 |

---

## 7. 不做的边界

只列真没有物理载体、或加了必然冲突无法消解的。不是"为省事不做"。

- **企业策略层（policy/flag 来源）**：无 GrowthBook 特性开关、无企业 MDM/策略下发后台。第 2.3 的来源分层结构全部落地，唯独这两层无载体承接。未来接企业部署可补。
- **实时 command 来源层**：无"斜杠命令实时改权限上下文"的机制；等价能力由 session 层 + cliArg 层覆盖。
- **LLM 分类器审批档**：一套独立分类器判定引擎（模型 API 调用 + transcript 准备 + denial 累计限流）。我们的 `auto` 是硬编码高风险集启发式，语义不同。分类器是判定引擎替换，不是权限规则，属独立工作线。
- **外部审批委托（permissionPromptTool）**：把审批决策委托给外部 MCP 工具。我们统一走 QuestionRegistry + 前端卡片，无此机制与需求。未来接自定义审批后端可补。
- **沙箱隔离（安全边界）**：权限系统是决策与知情层，不是安全边界，不做进程/文件隔离。真正隔离是独立 sandbox 工作线。注意区分：3.5 的 safetyCheck 强制提示（bypass 免疫）是审批层的强制问，属本设计；sandbox 另说。

---

相关代码：`openprogram/agent/tool_gate.py`、`openprogram/agent/internals/_approval.py`、`openprogram/agent/questions.py`、`openprogram/agent/attended.py`、`openprogram/agent/session_config.py`、`openprogram/agent/dispatcher/types.py`、`openprogram/agent/dispatcher/__init__.py`、`openprogram/agent/agent_loop.py`、`openprogram/agent/sub_agent_run.py`、`openprogram/functions/_runtime.py`、`openprogram/functions/__init__.py`、`openprogram/store/session/session_store.py`、`openprogram/webui/_execute/__init__.py`、`openprogram/webui/ws_actions/chat.py`、`openprogram/webui/ws_actions/session.py`、`openprogram/webui/routes/questions.py`、`openprogram/channels/_conversation.py`、`web/components/chat/composer/modes/question/question-mode.tsx`、`web/components/chat/composer/controls/`、`web/lib/net/use-ws.ts`、`web/components/settings/settings-tabs-layout.tsx`。新增文件：`openprogram/functions/permission_rule.py`、`openprogram/functions/tools/file_safety.py`、`openprogram/agent/permission_context.py`、`web/components/chat/composer/controls/permission-mode-pill.tsx`、`web/components/chat/composer/controls/use-permission-mode.ts`。
