# 实现地图

门控模型在代码中的所在位置。改动实现时请参考本文。

## 文件地图

```
openprogram/agent/management/
  ├─ gating.py              ← 共享辅助模块 (NEW)
  └─ manager.py             ← AgentSpec schema (规范结构体)

openprogram/agent/
  └─ _model_tools.py        ← 门控点：tools、MCP

openprogram/webui/ws_actions/
  └─ chat.py                ← 门控点：skills (/skill X 命令)

openprogram/functions/
  └─ __init__.py            ← agent_tools() 遵循解析后的名称列表
```

## 共享辅助模块

**`openprogram/agent/management/gating.py`** —— 三个导出项，无其他依赖。

```python
def match_any(name: str, patterns: Iterable[str]) -> bool
    # fnmatch.fnmatchcase 通配符匹配
    # 空/假值 patterns → False（调用方意为“无约束”）

def gate(*, name, category="", disabled=(), allowed=(), categories=()) -> str | None
    # 项目通过则返回 None，否则返回拒绝原因字符串
    # 解析顺序：disabled → allowed → categories

def check_required(installed, required) -> list[str]
    # 返回 installed 中没有任何项匹配的 required 模式
    # 用于 MCP “此 agent 需要服务器 X” 的硬性要求
```

这些都是纯函数 —— 无副作用、无全局状态 —— 可从任意层（web、dispatcher、CLI）导入。

## 规范 schema

**`openprogram/agent/management/manager.py:63-86`** —— `AgentSpec` dataclass：

```python
@dataclass
class AgentSpec:
    id: str
    name: str = ""
    ...
    skills: dict[str, Any] = field(default_factory=lambda: {
        "disabled": [], "allowed": [], "categories": [],
    })
    tools: dict[str, Any] = field(default_factory=lambda: {
        "disabled": [], "allowed": [],
    })
    mcp: dict[str, Any] = field(default_factory=lambda: {
        "disabled": [], "allowed": [], "required": [],
    })
```

每个块都是普通的 `dict`，因此 JSON 可轻松往返序列化。默认值全部为空（无约束）。

## 门控点 1 —— skills (/skill 命令)

**`openprogram/webui/ws_actions/chat.py:90-116`**

当用户输入 `/skill X` 时，处理器会：

1. 将 `X` 解析为一个 `Skill` 对象（`_skill_resolve`）。
2. 加载 agent 配置，取出 `skills` 块。
3. 调用 `gate(name=resolved.name, category=resolved.category, disabled=..., allowed=..., categories=...)`。
4. 若 `gate()` 返回了拒绝字符串，该聊天消息变为一条 `[error] skill X: <reason>` 系统消息，且不会展开 skill 正文。
5. 否则像以往一样将 SKILL.md 展开进用户回合。

```python
from openprogram.agents.gating import gate as _gate
gate_error = _gate(
    name=resolved.name,
    category=resolved.category or "",
    disabled=prof.get("disabled") or [],
    allowed=prof.get("allowed") or [],
    categories=prof.get("categories") or [],
)
if gate_error:
    raise PermissionError(gate_error)
```

## 门控点 2 —— tools

**`openprogram/agent/_model_tools.py:174-272`** —— `resolve_tools()`。

该函数接受以下任一形式：
- `wanted: list[str]` —— 每回合的显式覆盖（不施加门控，调用方已自行选择）。
- `wanted: dict` —— 来自 agent 配置的 `{enabled?, disabled, allowed, toolset?}` 结构。
- `wanted: None` —— 回退到 `agent_tools(source=..., only_available=True)`。

通配符门控发生在 238-262 行：

```python
if isinstance(wanted, dict):
    disabled_patterns = list(wanted.get("disabled") or [])
    allowed_patterns = list(wanted.get("allowed") or [])
    ...
    names = [
        n for n in DEFAULT_TOOLS
        if not match_any(n, disabled_patterns)
        and (not allowed_patterns or match_any(n, allowed_patterns))
    ]
```

更早的 `enabled: list[str]` 形式仍然优先（它是显式覆盖）。新的 `disabled`/`allowed` 模式仅在 `enabled` 缺失时才生效。

## 门控点 3 —— MCP

**`openprogram/agent/_model_tools.py:192-224`** —— `_apply_mcp_gate()`，一个在 `resolve_tools` 的每个返回路径上都会调用的内部辅助函数。

MCP 工具从 `agent_tools()` 中以 `slack__send_message` 或 `github-mcp__create_issue`（服务器名 + `__` + 工具名）这样的名称浮现。门控按 `<server>` 前缀过滤：

```python
def _apply_mcp_gate(tool_list):
    ...
    def _server_of(name: str) -> str:
        return name.split("__", 1)[0] if "__" in name else ""
    seen_servers = {_server_of(t.name) for t in tool_list if _server_of(t.name)}
    missing = check_required(seen_servers, required)
    if missing:
        return None   # 硬性失败 —— agent 回合在无工具状态下运行
    out = []
    for t in tool_list:
        srv = _server_of(t.name)
        if not srv:
            out.append(t)             # 原生工具，无 MCP 命名空间
            continue
        if disabled and match_any(srv, disabled): continue
        if allowed and not match_any(srv, allowed): continue
        out.append(t)
    return out
```

`required` 是**硬性**检查 —— 若有任何 required 模式在 `seen_servers` 中无任何匹配，整个工具列表都会被替换为 `None`。dispatcher 会记录缺失列表，agent 在该回合以工具禁用状态运行。

## 为何是三个门控点，而非一个

每个门控都运行在 LLM 即将看到该扩展的那一刻：

| 扩展 | LLM 何时看到它？ | 门控点 |
|---|---|---|
| Skill | 当 `/skill X` 运行且 SKILL.md 被注入回合时 | `chat.py` handler |
| Tool | 当 `resolve_tools()` 为 `agent_loop` 构建 `tools=[...]` 参数时 | `_model_tools.py` |
| MCP | 同 tool（MCP 工具通过同一条 `agent_tools()` 流水线浮现） | `_model_tools.py` (`_apply_mcp_gate`) |

我们曾考虑在调用栈更早处放置单一的 `apply_all_gates(profile, ...)` 收口点。我们否决了它，因为这三个点的“输入列表长什么样”各不相同 —— skills 是带有 category 字段的 `Skill` 对象，tools 是裸字符串，MCP 工具是带命名空间的字符串。共享辅助函数（`match_any`、`gate`、`check_required`）覆盖了约 90% 的逻辑；各调用点之间仅输入形态不同。

## 向后兼容

带有 `skills: ["pdf", "drawio"]`（裸列表而非 dict）的旧 agent 配置会在加载时被规范化。我们已在 `AgentSpec.from_dict` 中将 `skills: list` → `skills: {disabled: list}` 迁移，因此现有配置无需任何修改即可继续工作。

同样，`tools: ["bash", "read"]` 仍然有效 —— 列表形式被视为白名单（旧的 `enabled` 语义）。

## 测试

目前尚无针对 `gating.py` 的专门单元测试 —— `match_any` 是 `fnmatch.fnmatchcase` + 迭代，逻辑简单到只有一行。集成测试通过以下方式进行：

- `openprogram/_cli_cmds/doctor.py` —— 健康检查会枚举已安装的 skills/tools/MCP，并在启动时暴露门控错误。
- WS 冒烟测试 —— 在带有 disabled 模式的配置下运行 `/skill X`，会在聊天记录中返回拒绝消息。

若 `match_any` 的语义某天偏离 `fnmatch.fnmatchcase`（例如我们某天加入 `**` 递归 glob 支持），再补充正式的单元测试。
