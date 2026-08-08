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

**后果**：往 DEFAULT_TOOLS（`functions/__init__.py:69`）加新工具（如 list_sessions / send_message）后，**所有曾经开过 web_search 或选过非 full profile 的会话**永远拿当初那张名字列表，看不到新工具。

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
dict-override 分支（`_model_tools.py:397-421`）除 `enabled/disabled/allowed/toolset` 外还需识别 `web_search` 键。因为 web_search 不在 DEFAULT_TOOLS 里，若只存意图而 dict 分支不认这个键，展开结果就不含 web_search，开关失效。所以 C 是 B 的必需前置：展开后缺 web_search 则由 `agent_tools(names=[...]+["web_search"])` 补上。provider 内建 web_search（`openai_codex.py:376` 那种）是另一条可选路径，取决于各 provider 支持度（见 §8）。

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
- **新工具自动可见**：`{enabled: True}` 意图展开后含最新加进 DEFAULT_TOOLS 的工具（如 send_message / list_sessions）。
- **缓存稳定**：意图不变的会话连发两 turn，provider usage 显示第二 turn 命中缓存、工具集未抖动。
- **各写入端一致**：全仓不存在第二处 `list(_DEFAULT_TOOLS)` / `[t.name for t in` 式物化；webui/channels/TUI（`session.py`、`cli/src/ws/client.ts`）凡能开 web_search / 选 profile 的写入端都透传意图。

---

## 7. 延迟加载：可用 ≠ 常驻

§4.1 已确认工具数组每轮计费。开关决定哪些工具**可用**，延迟加载（defer）决定这些
可用工具里哪些每轮为自己的 JSON Schema 付费。这是两条独立的轴，把它们混为一谈正是
常驻成本随工具数量线性增长的原因。

### 7.1 可用工具的两种形态

一个可用工具以两种形态之一进入请求：

- **常驻（resident）**——完整 JSON Schema 进 provider 的 tools 数组，每轮都带。
- **延迟（deferred）**——在系统提示的目录里只占一行**裸工具名**。模型需要时调
  `tool_search` 加载 schema，此后从下一轮起到会话结束它都是常驻的。

目录只发名字、不发描述。名字足够让模型认出候选并点名索取，同时无论 defer 多少个
工具，整个目录都压在两百来 token。

延迟工具**不是被禁用**：模型照样能调，只是得先加载 schema。禁用的工具则同时不在
数组和目录里，根本无法调用。

### 7.2 切分逻辑

`split_tools_for_dispatch(tools)`（`functions/_runtime.py`）读每个工具的 `_defer`
边车属性，减去本会话已加载集合，把解析后的工具集切成 `(provider_tools, catalog)`：

- 非 defer → 进 provider 数组
- defer 且在已加载集合里 → 进 provider 数组
- defer 且未加载 → 进目录

已加载集合存在 ContextVar 里（`install_loaded_deferred` 由 dispatcher 每会话初始化；
`mark_deferred_loaded` 由 `tool_search` 内部写入）。这个集合里哪些条目真正进数组，
每轮只决定一次——见 §7.4。

### 7.3 哪些工具被 defer

`apply_default_deferral()` 在 import 末尾跑一次，按 `_defer = name not in RESIDENT_TOOLS`
打标。两类工具落到 defer：

1. 在 `full` 暴露白名单里但不在 `DEFAULT_TOOLS` 中的——memory、worktree、browser、
   image 等冷门工具。它们本来就不在默认集，defer 不损失什么。
2. `DEFERRED_DEFAULT_TOOLS`——仍留在 `DEFAULT_TOOLS`（默认可用）但体积大、调用少，
   因此不常驻：

   | 工具 | schema | 为什么不常驻 |
   |---|---|---|
   | `playwright_browser` | ~1170 tok | 单个 schema 最大。浏览器自动化是明确且小众的意图，编码会话完全不碰。 |
   | `enter_plan_mode` | ~1050 tok | plan mode 通常由用户经档位 chip / TUI 进入（`plan_mode.sync_tier`），不走这个工具。模型自行判断进 plan 是罕见路径。 |
   | `exit_plan_mode` | ~640 tok | 只在 plan mode 激活时有意义，且 plan-mode 提示块已明确点名它，模型据此知道要加载。 |
   | `send_message` | ~380 tok | 跨 session / branch 通信，只在多分支协作时用到。 |

`tool_search` 自身永不 defer——它是加载其它一切的唯一入口，defer 它会死锁。这一点有
双重保险：`RESIDENT_TOOLS` 的并集，以及 `apply_default_deferral` 里的显式 guard。

效果：默认常驻数组从每轮 ~7.9k 降到 ~4.7k token，约减 41%，且没有任何工具变得不可用。

### 7.4 工具数组以"轮"为粒度

工具数组是 Anthropic 和 OpenAI **两家缓存前缀的根**——缓存断点打在最后一个工具条目
上，因此数组之后的一切（系统提示、记忆、整条历史）都缓存在它后面。数组一变，这些
token 全部按原价重读。

所以数组**在轮边界定格**。`freeze_turn_tools` 在 agent loop 外层循环每轮跑一次，钉住
本轮有资格进数组的 defer 工具集合；`split_tools_for_dispatch` 读这个冻结集合，而不是
实时的已加载集合。轮内途中的 `tool_search` 会写入已加载集合，但撑不大数组——数组以及
扎根其上的前缀，在本轮每一次 provider 调用中都逐字节相同。下一次 `freeze_turn_tools`
再把这期间攒下的工具一并放行。

不冻结时，一次 `tool_search` 就让本轮剩余全部调用的前缀作废——而工具加载恰恰就发生在
轮内，这个作废落在最糟的时点。按两天真实流量实测，53% 的输入 token 未命中缓存源于此。

**可用性不等数组**。两条路径保证轮内加载的工具当轮即可调用：

1. `tool_search` 在结果文本里直接返回**完整 schema**，形状与数组条目一致
   （`{"name", "description", "parameters"}`），并明确写明可以立即调用。模型据此文本
   构造调用。
2. 派发按名字在**完整工具列表**里解析，而不是在 provider 数组里
   （`agent_loop._execute_tool_calls`）。已加载但尚未进数组的工具，调用照常执行。

所以冻结改变的只是工具**何时在数组里被公示**，从不影响它**能否被使用**。晋升的代价
——一次前缀重写——每工具每会话只付一次，且落在轮边界上，那里本来就有新的用户消息改
写了尾部。

这也是 defer 名单挑"少用"而非单纯挑"大"的原因：一个多数会话都会用到的工具，defer 等
于拿固定节省换反复的缓存未命中。

在任何一轮之外构造 provider 数组的调用方（预算计量、`breakdown`、测试）用
`release_turn_tools()` 退回到实时已加载集合。

### 7.5 目录是装配器组件

目录不由 dispatcher 手工拼接。它是注册的上下文组件（`deferred_catalog`，L1 order 25，
在 `context/components.py`），由 `_build_deferred_catalog` 构建，所以引擎计入预算的
字符串就是实际发出的字符串。该组件从 ContextVar 读工具列表，无 defer 工具时返回空串。

预算口径同样跟随这个切分。`_estimate_one_tool`（`context/budget.py`）按每个工具**实
际发上线的东西**计价：

- **常驻**——`description` **和** `parameters` schema 都算，外加少量包装。两半都要算：
  描述约占常驻成本的 45%，只算 schema 会把数组少估好几千 token。
- **延迟**——只算裸名字加一个换行，与 `deferred_catalog_text` 对齐。按描述给 defer 工
  具计价会把目录高估约一个数量级，因为描述恰恰是目录不发的那部分。

两半中任何一半算错都很难被发现，因为两个误差方向相反，一个看着合理的总数可以同时
掩盖两者。因此 `tests/context/test_budget.py` 拿真实 tokenize 的上线载荷，对两半分别
校验，各自不超 15%。

### 7.6 应当成立的性质

回归保护在 `tests/context/test_tool_defer.py`：

- 新会话的常驻数组里不出现任何 `DEFERRED_DEFAULT_TOOLS` 成员，`RESIDENT_TOOLS` 里也没有
- 它们全都出现在目录里，以及装配后的系统提示里
- `tool_search` 能把延迟工具移入 provider 数组、移出目录
- `tool_search` 永不被 defer；`apply_default_deferral` 幂等
- 轮内 `tool_search` 不改变 provider 数组和目录；加载的工具在下一个轮边界进数组
- `tool_search` 返回完整参数 schema；已加载但未进数组的工具仍能被派发解析

---

## 8. 已知边界

- **provider 内建 web_search vs 工具数组 web_search**（改 C 二选一）：codex/OpenAI Responses 确认走内建 `opts["web_search"]`（`openai_codex.py:376`）；Anthropic / 其它 provider 需逐个核对。确认前保留"web_search 作为工具名叠加"的稳妥路径。
- **dict-override 的 `allowed` 语义**：当前 `allowed`（`_model_tools.py:406`）是对 DEFAULT_TOOLS 过滤、不是 full 全集；本次不扩这块。
- **工具数组的精确 token 数**属服务端口径，本仓库静态测不出，仅确认"计费且在缓存前缀"。

---

## 9. 实现状态

### 9.1 承载文件

| 文件 | 承担什么 |
|---|---|
| `openprogram/agent/session_config.py` | `SessionRunConfig` 的 `web_search` / `toolset` 意图字段；`save/load` 读写它们（存 git session meta，不需改 DB schema——`update_session(**fields)` 任意 key 透传）；`tools_override_from_config` 输出 **dict 意图**（`{enabled, toolset, web_search}`）供实时展开，不物化整张工具表；`list[str]` 仅用于用户显式精选，原样透传 |
| `openprogram/webui/ws_actions/chat.py` | 把 `tools_profile` / `web_search_flag` 作为**意图**透传给 `save_session_run_config(toolset=, web_search=)`；"tools=False + web_search=True → `["web_search"]`" 这一单元素 list 属用户精选，不是全量快照 |
| `openprogram/agent/_model_tools.py` | dict-override 分支（`resolve_tools`，~397-421）的 `web_search` 叠加：`_overlay_web_search` 在 toolset / names 两条路径展开后，若意图含 web_search 且结果缺它则补上 |
| `openprogram/functions/_runtime.py` | defer 机制本体：`_defer` 边车、已加载集合、`freeze_turn_tools` / `release_turn_tools`（§7.4）、`split_tools_for_dispatch`、`tool_search` 及它返回的 schema、`deferred_catalog_text` |
| `openprogram/agent/agent_loop.py` | 每轮在外层循环顶部调一次 `freeze_turn_tools`；工具调用按名字在完整工具列表里解析，因此轮内加载的工具仍可派发 |

### 9.2 关键设计点（别破坏）

- **展开必须确定性**：工具数组在 prompt 缓存前缀根部，顺序一抖整段缓存 miss。当前
  `agent_tools` 按 names/registry 顺序返回，天然稳定——`tests/unit/test_tool_expansion_deterministic.py`
  锁住它。**以后改 `agent_tools` / `_filter_agent_tools` 切勿引入 `set()` 迭代 /
  dict churn 破坏顺序**，否则缓存会无声失效（不报错，只是悄悄变贵）。
- **绝不把"全部工具"物化成 list 存进会话**：全部工具永远由 `{enabled: True}` 意图
  实时展开。`list[str]` 只表示用户显式精选的少数工具。这是整个设计的红线。
- **不改历史**：工具开关只控制"接下来能调什么"，不过滤/重写历史 tool_use（会破坏
  tool_use↔tool_result 配对 → provider 400）。
- **工具数组只在轮边界变**（§7.4）。任何在轮内撑大数组的做法，都会作废本轮剩余部分
  的缓存前缀。若某个工具需要更早可用，就在工具结果里把 schema 交给模型，不要往数组
  里追加。

### 9.3 测试（回归保护）

- `tests/unit/test_tool_expansion_deterministic.py` — 展开确定性（缓存前缀稳定）
- `tests/unit/test_session_config_tools_intent.py` — 意图往返、用户精选 list 原样透传、
  端到端：意图展开含新工具（send_message/list_sessions）+ web_search 叠加生效
- `tests/unit/test_session_config.py::test_tools_enabled_yields_live_intent_not_snapshot` —
  `tools=True` 产出 `{enabled:True}` 意图而非 list 快照
- `tests/context/test_tool_defer.py` — §7.6 的各项 defer 性质，含轮边界冻结
- `tests/context/test_budget.py` — 工具计价两半各自对真实 tokenize 载荷校验（§7.5）

### 9.4 扩展点

- **provider 内建 web_search**（§8）：web_search 当前走"工具名叠加"路径；若确认各 provider
  支持内建 web_search，可在 `_overlay_web_search` 处切换。
- **新意图维度**（如按 channel 限工具）：往 dict 意图加键 + 在 `resolve_tools` dict
  分支处理，不要回到"存展开列表"的形态。
