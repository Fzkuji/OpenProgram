# 工具开关 / 工具集管理设计

> 所有 `file:line` 经代码核对。核心原则一句话：**会话只存"开关意图"，绝不存"展开后的工具名列表"——工具表每次运行时从 registry 实时展开，这样新增工具对所有历史会话自动生效。**

---

## 1. 工具集如何确定

### 1.1 优先级链

一次 turn 给模型的工具，由 `_resolve_tools(agent_profile, req.tools_override, source)` 算出（`dispatcher/__init__.py:764` → `_model_tools.py:385`）。优先级：

| 顺序 | 来源 | 行为 |
|---|---|---|
| 1 | `override`（per-turn / per-session） | `_model_tools.py:385` `wanted = override if override is not None else profile.get("tools")` |
| 2 | agent profile 的 `tools` | override 为 None 时回落 |
| 3 | 都没有 → `agent_tools(only_available=True)`（= DEFAULT_TOOLS） | `_model_tools.py:386-391` |

`override` 的取值分别处理：
- `[]` → 关闭所有工具（`:392-393`）
- **`dict`**（`enabled`/`disabled`/`allowed`/`toolset`）→ **意图式**，运行时实时展开（`:397-421`）。这是会话该存的形态。
- **`list[str]`** → `agent_tools(names=[...])`，按名字固定（`:423-428`）。仅用于表示用户显式精选的少数工具。

session 配置经 `tools_override_from_config(cfg)` 转成 override（`session_config.py:82-93`），消费点：webui `_execute/chat.py:105`、channels `_conversation.py:238`。

### 1.2 反面形态：把"意图"提前物化成"列表快照"

会话若在写入时就把开关意图展开成 `list[str]` 存下，工具集便被冻结在写入那一刻。
webui 的 `handle_chat` 在拼 `tools_flag` 时有**两处**会这样做，是本设计要消除的形态：

**路径 A — 选了非 full 的工具 profile**（`ws_actions/chat.py:321-328`）：
```python
if tools_profile and tools_flag is True:
    resolved = _at(toolset=tools_profile, only_available=True)
    if resolved is not None:
        tools_flag = [t.name for t in resolved]   # ← toolset 被提前展开成 list
```
toolset 本应"存 preset 名、运行时展开"，这里却当场展开。

**路径 B — 开了 Web Search**（`chat.py:336-356`）：
```python
if web_search_flag:
    ...
    elif tools_flag is True:
        base = list(_DEFAULT_TOOLS)        # ← 整张 DEFAULT_TOOLS 物化成字面量
    ...
    tools_flag = base                       # ← tools_flag 从 True/None 变成 list[str]
```
注意 `:348-353`：即便 `tools_flag is None`（"跟随 profile"），开了 web_search 也会 `base = list(DEFAULT_TOOLS)`，连"跟随 profile"的意图也被抹平。

> 两条路径互相独立，A 在 B 之前执行。两条都必须走意图透传，只改其中一条不足以消除物化。

### 1.3 快照对老会话的影响

物化出的 list 一路存进 DB：
1. `chat.py:482-488` → `save_session_run_config(tools=<list>)`
2. `session_config.py:111-113` `_normalize_tools_value`：list → `(enabled=True, override=[名字])`，写入 `tools_enabled`/`tools_override` 两列
3. 之后每个 turn：`load_session_run_config` → `tools_override_from_config` 命中 `:85-86` `if cfg.tools_override: return list(cfg.tools_override)`，**原样吐回老快照**
4. 该 list 进 `_model_tools.py:423-428` 走 `agent_tools(names=[...])`，**只认快照里那些名字**

**后果**：往 DEFAULT_TOOLS（`functions/__init__.py:69`）加新工具（如 list_sessions / message_branch）后，**所有曾经开过 web_search 或选过非 full profile 的会话**永远拿当初那张名字列表，看不到新工具。

对比：从没动过这两个开关的会话存的是 `tools_enabled=True`（bool），`:87-90` 每次实时返回 `list(DEFAULT_TOOLS)`，新工具自动可见。**差异 = "存 bool/意图" vs "存物化 list"。**

---

## 2. 其他项目怎么做（共识）

| 项目 | 存什么 | 怎么避免过期 |
|---|---|---|
| **opencode** | per-session `tools: Record<name, boolean>` 意图映射 | 工具表每 turn 由 live registry 现建，再用意图过滤（`config.ts:552`、`session/tools.ts:86`） |
| **claude-code** | `--tools`（选择，哨兵 `""`=无/`"default"`=全）+ `--allowedTools`/`--disallowedTools`（allow/deny 模式串） | 内置集是常量，只存"选择/允许/拒绝意图"，运行时求交（`main.tsx:988`） |
| **pi-ai** | builtin 开关 + extension 工具分开管 | 关 builtin 是布尔意图，不影响 extension 实时装载 |
| **hermes** | named preset（toolset 名） | 存 preset **名**，运行时展开（我们的 `TOOLSETS` 移植自此） |

**共识：三家无一例外存"开关意图"（布尔 / allow-deny / preset 名），从不冻结展开后的工具清单。** 真正的工具表永远请求时从 live registry 现场展开。

---

## 3. 核心原则：存【意图】不存【列表】

session 该存的最小意图：

| 字段 | 含义 | 取值 |
|---|---|---|
| tools 全开/全关 | 主开关 | `True` / `False` / `None`(跟随 profile) |
| web_search | 在主开关结果上**叠加** web_search | `bool` |
| preset 名 | 选了哪套 toolset | `"full"` / `"research"` / … / `None` |
| 用户显式禁用 | 手动关掉的少数工具名 | `list[str]`（短） |

运行时喂给已有 dict-override 通道（`_model_tools.py:397-421`）实时展开。这样：加新工具 → 老会话下一 turn 自动获得；删工具/改 preset → 自动跟随；存档体积 O(用户动过的几项)。我们现有的 dict-override 分支和 `tools_enabled=True` bool 分支**已经是这个形态**——只有上面两条物化路径退化成了 list。

---

## 4. 工具开关与上下文 / 缓存 / 历史的影响（已逐条核对）

1. **工具数组算不算 token**：ContextCommit 的 `total_tokens` **不含**工具数组（commit 数据类无 tools 字段，`commit/types.py:104-140`）；但 provider 请求侧工具数组**计费**（`anthropic.py:601` 随请求发出）。→ 工具集大小对 compaction 预算不可见，但对每 turn 真实输入成本可见。
2. **缓存**：工具数组在**缓存前缀根部**（`cache_policy.py:80-89`，第一个 breakpoint 打在最后一个工具，仅 Anthropic/Bedrock explicit 模式）。→ **任何对工具数组的增删改都改写缓存前缀 → 整段 prompt 缓存 miss**。**硬约束：展开必须确定性**（稳定排序+去重），同一意图每次展开逐字节一致，缓存才命中。
3. **历史里有、当前工具表没有的调用**：历史 tool_use/tool_result 从 message 渲染（`anthropic.py:328-451`），与当前 `context.tools` 无关，能正常回放；但模型这 turn 无法再发起该调用。→ **改造不要根据当前工具表过滤/重写历史 tool_use**（破坏 tool_use↔tool_result 配对会 400）。
4. **ContextCommit 重放不受影响**：commit 不存工具数组，工具集是请求期产物。→ 把工具快照从存档拿掉**不动任何 commit 重放语义**，降低改造风险。

---

## 5. 三个承载点

**A — chat.py 透传意图**（`ws_actions/chat.py:321-328` 与 `:336-356`）：
- profile 路径：把 **preset 名**透传（走 dict-override 的 `toolset` 字段），不展开成 `[t.name for t in resolved]`。
- web_search 路径：把 web_search 作为**叠加意图**透传，`tools_flag` 保持 True/None，不改写成 list。

**B — session_config 存意图**：
- `SessionRunConfig`（`session_config.py:12-18`）带 `web_search: Optional[bool]` 与 preset 名字段。
- load/save（`:20-79`）读写这两项。
- `tools_override_from_config`（`:82-93`）输出 **dict 意图** 而非 list：`tools_enabled is False` → `[]`；否则 → `{"enabled": True if enabled else None, "toolset": <preset>, "disabled": [...], "web_search": <bool>}`。不写 list[str] 快照。

**C — dict-override 支持 web_search 叠加**：
dict-override 分支（`_model_tools.py:397-421`）除 `enabled/disabled/allowed/toolset` 外还需识别 `web_search` 键。因为 web_search 不在 DEFAULT_TOOLS 里，若只存意图而 dict 分支不认这个键，展开结果就不含 web_search，开关失效。所以 C 是 B 的必需前置：展开后缺 web_search 则由 `agent_tools(names=[...]+["web_search"])` 补上。provider 内建 web_search（`openai_codex.py:376` 那种）是另一条可选路径，取决于各 provider 支持度（见 §7）。

**`list[str]` 仅表示用户显式精选**（如 web-search-only 的 `["web_search"]`）：
`tools_override_from_config` 原样透传，`_model_tools.py` 的 list 分支照名字展开。
它不再承载"全部工具的物化快照"——全部工具永远由 `{enabled: True}` 意图实时展开。

---

## 6. 应当成立的性质

设计正确时，下列性质同时成立，它们也是回归验证的着眼点：

- **展开确定性**：同一意图连续展开两次逐元素相等（排序稳定 + 去重），避免 §4.2 的缓存抖动。
- **意图往返**：存 `web_search=True` 读回 True；旧会话无该字段读回 None 不报错。
- **dict 输出**：`enabled=True` → dict 含 enabled；带 web_search → 展开含 web_search；`enabled=False` → `[]`。
- **写入不物化**：新建会话开 web_search 或选 research 后，DB 里 `tools_override` 为 NULL 或 dict，不是全量 list。
- **新工具自动可见**：`{enabled: True}` 意图展开后含最新加进 DEFAULT_TOOLS 的工具（如 message_branch / list_sessions）。
- **缓存稳定**：意图不变的会话连发两 turn，provider usage 显示第二 turn 命中缓存、工具集未抖动。
- **各写入端一致**：全仓不存在第二处 `list(_DEFAULT_TOOLS)` / `[t.name for t in` 式物化；webui/channels/TUI（`session.py`、`cli/src/ws/client.ts`）凡能开 web_search / 选 profile 的写入端都透传意图。

---

## 7. 已知边界

- **provider 内建 web_search vs 工具数组 web_search**（改 C 二选一）：codex/OpenAI Responses 确认走内建 `opts["web_search"]`（`openai_codex.py:376`）；Anthropic / 其它 provider 需逐个核对。确认前保留"web_search 作为工具名叠加"的稳妥路径。
- **dict-override 的 `allowed` 语义**：当前 `allowed`（`_model_tools.py:406`）是对 DEFAULT_TOOLS 过滤、不是 full 全集；本次不扩这块。
- **工具数组的精确 token 数**属服务端口径，本仓库静态测不出，仅确认"计费且在缓存前缀"。

---

## 8. 实现状态

### 8.1 承载文件

| 文件 | 承担什么 |
|---|---|
| `openprogram/agent/session_config.py` | `SessionRunConfig` 的 `web_search` / `toolset` 意图字段；`save/load` 读写它们（存 git session meta，不需改 DB schema——`update_session(**fields)` 任意 key 透传）；`tools_override_from_config` 输出 **dict 意图**（`{enabled, toolset, web_search}`）供实时展开，不物化整张工具表；`list[str]` 仅用于用户显式精选，原样透传 |
| `openprogram/webui/ws_actions/chat.py` | 把 `tools_profile` / `web_search_flag` 作为**意图**透传给 `save_session_run_config(toolset=, web_search=)`；"tools=False + web_search=True → `["web_search"]`" 这一单元素 list 属用户精选，不是全量快照 |
| `openprogram/agent/_model_tools.py` | dict-override 分支（`resolve_tools`，~397-421）的 `web_search` 叠加：`_overlay_web_search` 在 toolset / names 两条路径展开后，若意图含 web_search 且结果缺它则补上 |

### 8.2 关键设计点（别破坏）

- **展开必须确定性**：工具数组在 prompt 缓存前缀根部，顺序一抖整段缓存 miss。当前
  `agent_tools` 按 names/registry 顺序返回，天然稳定——`tests/unit/test_tool_expansion_deterministic.py`
  锁住它。**以后改 `agent_tools` / `_filter_agent_tools` 切勿引入 `set()` 迭代 /
  dict churn 破坏顺序**，否则缓存会无声失效（不报错，只是悄悄变贵）。
- **绝不把"全部工具"物化成 list 存进会话**：全部工具永远由 `{enabled: True}` 意图
  实时展开。`list[str]` 只表示用户显式精选的少数工具。这是整个设计的红线。
- **不改历史**：工具开关只控制"接下来能调什么"，不过滤/重写历史 tool_use（会破坏
  tool_use↔tool_result 配对 → provider 400）。

### 8.3 测试（回归保护）

- `tests/unit/test_tool_expansion_deterministic.py` — 展开确定性（缓存前缀稳定）
- `tests/unit/test_session_config_tools_intent.py` — 意图往返、用户精选 list 原样透传、
  端到端：意图展开含新工具（message_branch/list_sessions）+ web_search 叠加生效
- `tests/unit/test_session_config.py::test_tools_enabled_yields_live_intent_not_snapshot` —
  `tools=True` 产出 `{enabled:True}` 意图而非 list 快照

### 8.4 扩展点

- **provider 内建 web_search**（§7）：web_search 当前走"工具名叠加"路径；若确认各 provider
  支持内建 web_search，可在 `_overlay_web_search` 处切换。
- **新意图维度**（如按 channel 限工具）：往 dict 意图加键 + 在 `resolve_tools` dict
  分支处理，不要回到"存展开列表"的形态。
