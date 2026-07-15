# Agentic 函数防自递归机制

> 现状：从「deny 屏蔽工具」改成「处境引导 + 递归深度上限兜底」(commit `1f6f5fce`)。
> 本文档基于真实代码逐条对应 file:line，可照着核对。
> 相关代码：
> - `openprogram/agentic_programming/function.py`
> - `openprogram/agentic_programming/runtime.py`
> - 测试：`tests/agentic_programming/test_self_recursion_guard.py`(8 用例)

---

## 1. 问题：agentic 函数为什么会自递归

一个 agentic 函数(如 `wiki_agent`)的函数体里跑一个内层 agent loop——通过 `runtime.exec(content=[task])` 驱动内层 LLM。

两个诱因叠加：

1. **默认 toolset = full，含函数自己。** 裸 `runtime.exec(content=...)` 不传 `tools=` / `toolset=`，`_call_via_providers` 把它解析成 `DEFAULT_TOOLSET = "full"`：
   - `openprogram/agentic_programming/runtime.py:1467` `DEFAULT_TOOLSET = "full"`
   - `runtime.py:1468-1483` `raw_tools is None` 分支 → `_resolve_agent_tools(toolset="full", ...)`
   - 而 `full` 工具集列出了所有 harness 入口本身(`wiki_agent` / `research_agent` / `gui_agent` …，见 `openprogram.functions.TOOLSETS["full"]`)。所以内层模型的工具列表里**有它正在执行的那个函数**。

2. **模型看到 docstring 匹配任务，误以为该调。** 模型看到 `wiki_agent` 的工具描述("Maintain a wiki vault — route to ingest…")正好匹配当前任务，认为应该路由给 `wiki_agent` → 调自己 → 进去又是裸 exec、又看到自己 → 无限递归。

实战根因记录(7 层嵌套实例)：`docs/design/TODO-doc-code-gaps.md` §1。会话记录里 `context_tree` 展示了 7 层嵌套(`4d76→0c07→0964→c6f9→f1c9→4379→8746→100c`)。

---

## 2. 设计理念：为什么用「引导」而非「deny」

**让模型理解自己的处境、自主判断不调，而不是强行从工具列表里屏蔽掉它自己。**

旧的 deny 方案(wrapper 把函数自己的名字推进 `_current_tool_policy["deny"]`，使内层模型看不到自己)的问题：

- 模型学不会处境判断——它不知道「我正在 X 内部」这件事，只是「X 不在工具列表里」。换个上下文(deny 没生效、或跨函数环)它照样会犯。
- 违背理念——框架替模型做了决定，而不是给模型足够信息让它自己做对的决定。这是用户明确要求的方向：模型该知道自己在哪、自己判断不调。

新方案把「不调自己」变成模型能理解的一条处境信息(你正在 X 体内，调 X = 无限递归)，模型据此自主不调；同时保留一个与模型判断无关的**深度上限**作为止损兜底。

---

## 3. 三个机制怎么协同

### (主) 处境提示 —— 防「发生」

`_situational_prefix(fn_name, fn_doc)`(`runtime.py:321-341`)生成一段英文处境提示：

```
[Execution context] You are currently running INSIDE the agentic function `{fn_name}`.
The tool list may include `{fn_name}` itself — do NOT call it. Calling `{fn_name}`
re-enters where you are now and causes infinite recursion. Use lower-level tools
(search / read-write files / run code) to do the work directly.
```

`fn_doc` 非空时，docstring 被**降级置后**(`text += f"\n\nThis function's job: {fn_doc.strip()}"`，`runtime.py:339-340`)——诱因(docstring 描述)不再盖过警告。

**注入到哪：user turn 开头的 text block，不进 system 前缀。**

- DAG 路径：`runtime.py:578-587` 构造 `frame_prefix_blocks`(从当前 frame 节点读 `name` + `metadata.doc`)，然后 `runtime.py:597` `_build_pi_context(frame_prefix_blocks + (content or []))`——拼在当前轮 `content` 之前，作为当前轮 user 消息的开头 block。
- standalone 回退路径(无 store)：`runtime.py:1518-1532`，从 `_recursion_depth` 取最深的函数名(`max(_depths, key=_depths.get)`，`runtime.py:1525`)，`_situational_prefix(_cur_fn, "")`(无 doc)，同样拼在 `content` 之前(`runtime.py:1532`)。
- system 前缀单独组装(`runtime.py:1535-1539`：`self.system` + `_skills_block()`)，**处境提示不进 system**。

**为什么放 user turn、不放 system：** 决策6(`session-dag.md`)要求整个项目共用一个**统一且恒定**的 system prompt(身份 + 项目记忆 + 统一工具列表 + skills)以最大化 KV 缓存命中——前缀一变，长上下文后全不命中、成本爆。处境提示是**逐函数、逐调用点**变化的(每个函数名/docstring 不同)，放进 system 会破坏前缀恒定。放在 user turn 开头既能让模型看到，又不碰 system 前缀。

### 删 deny —— 工具列表含函数自己，靠引导不靠屏蔽

wrapper 不再把函数自己的名字推进 `_current_tool_policy["deny"]`。内层模型的工具列表里**仍然能看到它自己**，靠处境提示让模型自主不调。

`_current_tool_policy` 的**其它用途保留未动**：`source` / `allow` / `toolset` / unattended deny。具体见 `runtime.py:1451-1458`——`policy.get("deny")` 仍在用，与 unattended 的 `denied_ask_tools` 合并(`runtime.py:1457`)；`source`/`allow`/`toolset` 在 `runtime.py:1469`/`1479-1482` 仍生效。删的只是「把函数自己名字注入 deny」这一处。

### (兜底) 深度上限 —— 止损安全网

- `_MAX_AGENTIC_RECURSION_DEPTH = 5`(`function.py:48`)。
- `_recursion_depth` 是一个 `ContextVar[Optional[dict]]`(`function.py:49-51`)，存 **per-function-name** 的当前嵌套深度 `{name: depth}`。
- 进入 wrapper 时：取本函数名(`getattr(self, "tool_name", None) or fn.__name__`，sync `function.py:964`，async `function.py:852`)，读当前深度，**超限则抛 `RecursionError`**，否则 +1 写回(token 保存)：
  - sync：`function.py:964-976`
  - async：`function.py:852-864`
- 抛错确切条件：`_cur_depth >= _MAX_AGENTIC_RECURSION_DEPTH`(即已经在第 5 层、要进第 6 层时抛)。消息：`f"agentic function {name} exceeded max nesting depth {5} — possible runaway recursion"`(sync `function.py:967-972`，async `function.py:855-860`)。
- `finally` 复位：`_recursion_depth.reset(token)`(sync `function.py:989`，async `function.py:877`)——return / 异常都复位。

**正常调用永不触及上限**：处境提示先拦住「发生」，深度计数只在模型无视引导、连续 re-enter 同名函数 5 层后才触发。

**三者定位：** 处境提示 = 防发生(让模型自己不调)；删 deny = 配套(工具可见，引导才有对象)；深度上限 = 止损安全网(模型失控时不烧无限 token)。

---

## 4. 关键代码位置表

| 机制 | 代码 | file:line |
|---|---|---|
| 深度上限常量 | `_MAX_AGENTIC_RECURSION_DEPTH = 5` | `function.py:48` |
| 深度计数 contextvar | `_recursion_depth` | `function.py:49-51` |
| sync wrapper：本函数名 | `getattr(self,"tool_name",None) or fn.__name__` | `function.py:964` |
| sync wrapper：超限抛错 | `if _cur_depth >= MAX: raise RecursionError` | `function.py:967-972` |
| sync wrapper：+1 写回 | `_recursion_depth.set({**prev, name: cur+1})` | `function.py:973-976` |
| sync wrapper：finally 复位 | `_recursion_depth.reset(token)` | `function.py:989` |
| async wrapper：本函数名 | 同上 | `function.py:852` |
| async wrapper：超限抛错 | 同上 | `function.py:855-860` |
| async wrapper：+1 写回 | 同上 | `function.py:861-864` |
| async wrapper：finally 复位 | 同上 | `function.py:877` |
| 处境提示文案 | `_situational_prefix(fn_name, fn_doc)` | `runtime.py:321-341` |
| 处境提示注入(DAG 路径) | `frame_prefix_blocks` → `_build_pi_context(prefix + content)` | `runtime.py:578-587`, `597` |
| 处境提示注入(standalone 回退) | 从 `_recursion_depth` 取最深名 → 拼 content 前 | `runtime.py:1518-1532` |
| system 前缀单独组装(不含提示) | `self.system` + `_skills_block()` | `runtime.py:1535-1539` |
| `_current_tool_policy` 其它用途 | deny/source/allow/toolset 解析 | `runtime.py:1451-1483` |

---

## 5. 行为契约(从测试提炼)

来自 `tests/agentic_programming/test_self_recursion_guard.py`：

| # | 契约 | 测试 |
|---|---|---|
| 1 | 处境提示含函数名、含 "do NOT call it"、含 "recursion"，docstring 降级到末尾(`recursion` 出现位置在 docstring 之前) | `test_situational_prefix_warns_against_self_call` |
| 2 | 空 docstring 时不追加 "This function's job"，提示仍含函数名 | `test_situational_prefix_handles_empty_doc` |
| 3 | 函数自己的名字**不再**进 `_current_tool_policy["deny"]`(self-deny 删干净) | `test_self_name_NOT_denied_during_call` |
| 4 | 正常调用一层时，本函数名深度 = 1(进入即 +1) | `test_depth_increments_during_call` |
| 5 | 无脑自调超限抛 `RecursionError`，消息含函数名 + 上限数字；进入函数体次数恰为 `_MAX_AGENTIC_RECURSION_DEPTH`(到上限止住、不再深入) | `test_depth_backstop_raises_past_limit` |
| 6 | A→B 不同名独立计数：B 深 nesting 不计入 A 的限额，反之亦然(per-name 不误伤) | `test_distinct_subcalls_not_collateral_damage` |
| 7 | return 后深度复位回调用前的值 | `test_depth_restored_after_return` |
| 8 | 抛异常后深度也复位 | `test_depth_restored_after_exception` |

补充：测试用 `_deny()` helper(`test:51-53`)读 `_current_tool_policy.get(None).get("deny")`，`_depth(name)` helper(`test:55-56`)读 `_recursion_depth.get(None).get(name, 0)`——核对时可对照这两个读法确认计数/deny 形态。

---

## 6. 与旧 deny 方案的对比

| 维度 | 旧:deny 屏蔽工具 | 新:处境引导 + 深度上限 |
|---|---|---|
| 怎么做 | wrapper 把函数自己名字推进 `_current_tool_policy["deny"]`，内层模型看不到自己 | 工具列表含函数自己；user turn 开头注入处境提示让模型自主不调；超 5 层抛 `RecursionError` 兜底 |
| 模型认知 | 不知道「我在 X 内部」，只是 X 不在列表 | 明确知道处境(你在 X 体内、调 X = 递归) |
| 是否破坏 system 前缀缓存 | deny 在 policy 层、不动 system；但屏蔽是「替模型决定」 | 提示放 user turn、不进 system，前缀仍恒定(符合决策6) |
| 失控止损 | 靠屏蔽间接挡(屏蔽失效就无底) | 显式深度上限 5 层硬止损 |
| 优 | 直接、无需模型配合 | 模型学会处境判断；符合理念;兜底确定性强 |
| 劣 | 模型学不会处境判断;违背理念;屏蔽一旦不生效就裸奔 | 纯引导对弱模型不 100% 可靠(故有深度上限兜底) |

---

## 7. 已知局限

1. **纯引导对弱模型 / 长上下文不 100% 可靠。** 处境提示是让模型自主判断，弱模型或上下文过长稀释提示时可能仍会调自己——所以保留深度上限作为确定性兜底。
2. **跨函数环(A→B→A 交替)第一版未覆盖。** 深度上限按**同名**计数(`_recursion_depth[name]`)，只挡直接自递归(A→A→A…)。A→B→A→B 这种交替环里 A 的深度每次只 +1 到一定层、B 同理，不会触发任一名的上限。整条调用链识别(把 A 在调用链上出现过就算环)是增强项，未做。
3. **旧 deny 实现其实也只挡直接自递归、不挡跨函数环。** 旧 deny 把「当前函数自己」推进 deny，A 跑时 deny 的是 A，B 仍可被调、B 里再调 A 也不在 B 的 deny 里。所以新方案在「跨函数环」这点上**不是回退**——两版都只防直接自递归，跨链识别是两版共同的待办增强。

---

## 关联文档

- `docs/design/runtime/session-dag.md` 决策6 —— 统一 system 前缀约束，本机制把处境提示放 user turn 正是为遵守该约束。
- `docs/design/TODO-doc-code-gaps.md` §1 —— 7 层嵌套根因记录。
