# 工具开关 / 工具集管理设计

> 所有 `file:line` 经代码核对。核心原则一句话：**会话只存"开关意图"，绝不存"展开后的工具名列表"——工具表每次运行时从 registry 实时展开，这样新增工具对所有历史会话自动生效。**

---

## 1. 现状与问题

### 1.1 工具集怎么定（优先级链）

一次 turn 给模型的工具，由 `_resolve_tools(agent_profile, req.tools_override, source)` 算出（`dispatcher/__init__.py:764` → `_model_tools.py:385`）。优先级：

| 顺序 | 来源 | 行为 |
|---|---|---|
| 1 | `override`（per-turn / per-session） | `_model_tools.py:385` `wanted = override if override is not None else profile.get("tools")` |
| 2 | agent profile 的 `tools` | override 为 None 时回落 |
| 3 | 都没有 → `agent_tools(only_available=True)`（= DEFAULT_TOOLS） | `_model_tools.py:386-391` |

`override` 的取值分别处理：
- `[]` → 关闭所有工具（`:392-393`）
- **`dict`**（`enabled`/`disabled`/`allowed`/`toolset`）→ **意图式**，运行时实时展开（`:397-421`）。**这是我们想要的形态。**
- **`list[str]`** → `agent_tools(names=[...])`，**按名字钉死**（`:423-428`）。**这是问题根源。**

session 配置经 `tools_override_from_config(cfg)` 转成 override（`session_config.py:82-93`），消费点：webui `_execute/chat.py:105`、channels `_conversation.py:238`。

### 1.2 病根：两条路径把"意图"提前物化成"列表快照"

webui 的 `handle_chat` 在拼 `tools_flag` 时，有**两处**把开关意图展开成 `list[str]`：

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

> 两条路径独立，A 甚至在 B 之前。**只修 B 治不干净——必须两条都改。**

### 1.3 快照如何钉死老会话

物化出的 list 一路存进 DB：
1. `chat.py:482-488` → `save_session_run_config(tools=<list>)`
2. `session_config.py:111-113` `_normalize_tools_value`：list → `(enabled=True, override=[名字])`，写入 `tools_enabled`/`tools_override` 两列
3. 之后每个 turn：`load_session_run_config` → `tools_override_from_config` 命中 `:85-86` `if cfg.tools_override: return list(cfg.tools_override)`，**原样吐回老快照**
4. 该 list 进 `_model_tools.py:423-428` 走 `agent_tools(names=[...])`，**只认快照里那些名字**

**后果**：往 DEFAULT_TOOLS（`functions/__init__.py:69`）加新工具（如 list_sessions / message_branch）后，**所有曾经开过 web_search 或选过非 full profile 的老会话**永远拿当初那张名字列表，看不到新工具。

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

## 5. 改法（治本 + 治标）

### 5.1 治本

**改 A — chat.py 两条物化路径都去掉**（`ws_actions/chat.py:321-328` **和** `:336-356`）：
- profile 路径：不再 `[t.name for t in resolved]`，改成把 **preset 名**透传（走 dict-override 的 `toolset` 字段）。
- web_search 路径：不再 `list(DEFAULT_TOOLS)`，改成把 web_search 作为**叠加意图**透传，不把 `tools_flag` 从 True/None 改写成 list。

**改 B — session_config 存意图**：
- `SessionRunConfig`（`session_config.py:12-18`）新增 `web_search: Optional[bool]`（preset 名若还没有也加）。
- load/save（`:20-79`）读写新列（DB schema 加列，`session_db.py`）。
- `tools_override_from_config`（`:82-93`）改输出 **dict 意图** 而非 list：`tools_enabled is False` → `[]`；否则 → `{"enabled": True if enabled else None, "toolset": <preset>, "disabled": [...], "web_search": <bool>}`。彻底不再写 list[str] 快照。

**改 C — dict-override 支持 web_search 叠加（必需前置，不是可选）**：
dict-override 分支（`_model_tools.py:397-421`）当前只认 `enabled/disabled/allowed/toolset`，**没有 web_search 键**。若 web_search 改成只存意图、而 web_search 不在 DEFAULT_TOOLS 里 → 展开结果**不含 web_search → web_search 失效**。所以 C 必须和 B 同批：让 dict 分支识别 `web_search`，展开后缺则 `agent_tools(names=[...]+["web_search"])` 补上（稳妥路径）。provider 内建 web_search（`openai_codex.py:376` 那种）作为后续二选一，先确认各 provider 支持度。

### 5.2 治标：迁移老会话（判据要可靠）

**关键认知：仅凭 `tools_override` 列的内容，事后无法区分"快照物化" vs "用户真实子集"**——写入路径丢了意图，两者落库都是 list[str]。所以只能基于**集合等价匹配到已知物化产物**：

对每条 `tools_override` 为非空 list 的行：
- `set(override)` 等于 `set(agent_tools(toolset=ts))`（遍历 TOOLSETS 每个 ts）或 `set(DEFAULT_TOOLS)`，**允许 ±web_search 一个元素**：
  - 命中某 toolset → 改写为 `{"toolset": ts}` + web_search 位
  - 命中 DEFAULT_TOOLS → 改写为 `tools_enabled=True, tools_override=NULL` + web_search 位
  - **都不命中** → 大概率是用户真实精选/历史版本 preset → **保守保留原样**
- 因 DEFAULT_TOOLS/TOOLSETS 随版本变，纯 set 等价仍可能假阴；更稳是给脚本喂"物化发生时的常量快照"（从 git 历史取当时定义）按时间分段比对——成本高，**先 dry-run 出分类统计再决定**。

**读时归一兜底**（`session_config.py:85-86`）：读到等价于 DEFAULT_TOOLS 快照的 override 当场降级为 `enabled=True` 意图（即便迁移漏跑也自愈）。toolset 等价行靠一次性脚本（每 turn 对所有 toolset 做 set 比对开销大，兜底只覆盖最常见的 DEFAULT_TOOLS）。

**list[str] 分支必须保留**（`_model_tools.py:423-428`）：存量未迁移行 + 第三方/历史数据仍喂 list[str]，分支不能删。

---

## 6. 落地分步（每步可验证）

1. **稳定化展开（前置硬约束）**：确认 `agent_tools` / dict 分支展开是确定性排序+去重。验证：同意图连续展开两次逐元素相等（防 §4.2 缓存抖动）。
2. **session_config 加 web_search（+preset）意图列**：改 `SessionRunConfig` + load/save + DB schema。验证：存 `web_search=True` 读回 True；旧行无该列读回 None 不报错。
3. **`tools_override_from_config` 改输出 dict 意图**。单测：`enabled=True` → dict 含 enabled；`+web_search` → 展开含 web_search；`enabled=False` → `[]`。
4. **改 C 同批：dict 分支支持 web_search 叠加**（`_model_tools.py:397-421`）。验证：dict 意图带 web_search → 展开结果含 web_search。
5. **chat.py 去物化（两条路径）**（`:321-328` + `:336-356`）：profile 透传 preset 名、web_search 透传意图。验证：新建会话开 web_search / 选 research，查 DB `tools_override` 为 NULL 或 dict，不是全量 list。
6. **端到端验证新工具可见**：DEFAULT_TOOLS 临时加标记工具 → "开过 web_search / 选过 profile"的会话下一 turn 工具表出现它（当前 bug 的反例）。
7. **读时归一兜底**（`session_config.py:85-86`）。验证：手造旧快照行、不跑迁移、直接发 turn，拿到 live 工具集。
8. **一次性迁移脚本**（§5.2 判据，先 dry-run）。验证：跑前后对比受影响行；抽样老会话发 turn 工具集随 registry 更新。
9. **缓存回归**：开 web_search 会话连发两 turn（意图不变），provider usage 确认第二 turn 命中缓存、工具集未抖动。
10. **三端写入一致**：grep 全仓 `list(_DEFAULT_TOOLS)` / `[t.name for t in` 确认**没有第二处物化**；webui/channels/TUL（`session.py`、`cli/src/ws/client.ts`）若也能开 web_search/选 profile，各自核对写入端不物化。

---

## 7. 已知边界

- **provider 内建 web_search vs 工具数组 web_search**（改 C 二选一）：codex/OpenAI Responses 确认走内建 `opts["web_search"]`（`openai_codex.py:376`）；Anthropic / 其它 provider 需逐个核对。确认前保留"web_search 作为工具名叠加"的稳妥路径。
- **存量坏行判别阈值**：需看真实 DB 分布，脚本先 dry-run 分类统计再执行。
- **dict-override 的 `allowed` 语义**：当前 `allowed`（`_model_tools.py:406`）是对 DEFAULT_TOOLS 过滤、不是 full 全集；本次不扩这块。
- **工具数组的精确 token 数**属服务端口径，本仓库静态测不出，仅确认"计费且在缓存前缀"。
