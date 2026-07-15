# DAG 边字段重命名设计文档

Status: **draft（待讨论确认）** · Created: 2026-06-29

> 一个 DAG 节点有两种父关系，现在两个字段都叫 `called_by`（一个在节点顶层、
> 一个在 metadata 里），导致代码里反复分不清在读哪个 —— 这是过去一连串分支/
> 渲染 bug 的共同根源。本文设计把两者改成不同名字（`caller` + `predecessor`），
> 彻底消除歧义。

## 一、问题：两个同名 `called_by`

一个节点要表达**两种不同的父子关系**：

| 关系 | 含义 | 现在叫什么 | 举例 |
|---|---|---|---|
| **caller** | 谁"调用"了我（子调用边） | 节点顶层 `Call.called_by` | 工具被哪个 LLM 调起；ROOT 调起顶层节点 |
| **conv 前驱** | 聊天顺序上我接在谁后面（对话链边） | `metadata.called_by` | 第二轮 user 接在第一轮 reply 后 |

两个都叫 `called_by`，只是一个在顶层、一个在 metadata。**名字撞了，语义完全不同。**

### 为什么需要两个

它们经常不一样。典型例子——顶层 user 节点：
- caller = `ROOT`（它不是被谁调用的，是会话发起）
- conv 前驱 = 上一轮的 reply（聊天顺序）

只有一个字段没法同时表达"挂在根上"和"接在上一句后面"。分支区分**靠的是 conv
前驱**（同一个 conv 前驱有多个孩子 = fork），不是 caller。

## 二、现状全貌（codegraph 实测）

### 后端

| 文件 | 符号 | 现状 |
|---|---|---|
| `context/nodes.py` | `Call.called_by: str = ""` | dataclass 唯一的边字段，语义=caller |
| `store/session/_msg_adapter.py` | `_msg_to_node` | msg dict 的 `called_by` → 有时进 `Call.called_by`（tool/attach），有时进 `metadata.called_by`（user/llm） |
| `store/session/_msg_adapter.py` | `_node_to_msg` | 反向：`Call.called_by` 和 `metadata.called_by` 拼回 msg dict 的 `called_by`+`caller` 两个 key |
| `store/session/session_store.py` | `_node_conv_predecessor` | 读 `metadata.called_by` |
| `store/session/session_store.py` | `_node_caller` | 读 `Call.called_by` |
| `store/session/memory_index.py` | `append(node, predecessor, caller)` | 两个索引：`children_by_predecessor`（conv）/ `children_by_caller`（caller） |
| `webui/graph_builder.py` | `build_session_graph` | 构建 graph dict，每节点带 `called_by`（conv 前驱）+ `caller` |
| `webui/graph_layout/_common.py` | `called_by_of` / `predecessor_of` | **第 23 行 `called_by_of = predecessor_of` 覆盖** → tier/lane/depth/topology 全读 conv 前驱 |
| `webui/graph_layout/{tier,lane,depth,topology}.py` | — | 经 `called_by_of` 读 conv 前驱 |

### 前端

| 文件 | 符号 | 现状 |
|---|---|---|
| `dag/types.ts` | `GNode` | 有 **3 个字段**：`parent_id`（死字段，没人写）、`called_by`（conv 前驱）、`caller`（子调用） |
| `dag/types.ts` | `layoutParent(n)` | 返回 `n.called_by`（conv 前驱），构建树用 |
| `dag/pipeline.ts` | `render` | 第 186 `n.caller` 判 internal；第 202 `m.called_by \|\| m.called_by`（**重复 or，笔误残留**） |
| `dag/render/{edges,nodes,badges}.ts` | — | 读 `called_by`（conv 前驱）画连线/判分支 |
| `conversations.ts` | `LegacyMessage` / `BranchRow` | msg/branch dict 流转 |

### 混乱症状（都是同名导致的真实 bug）

1. `_common.py:23` 用 `predecessor_of` 覆盖 `called_by_of` —— 名字写着 caller，实际读 conv 前驱。tier 阶梯 bug 就源于此。
2. `pipeline.ts:202` `m.called_by || m.called_by` —— 两个一样，明显是想写两个不同字段但都打成同名了。
3. `_msg_to_node` 里 `called_by = tool_use.get("called_by") or predecessor` —— predecessor（conv）和 caller 混进同一个变量。
4. 我自己之前调试时反复说错"是 ROOT 还是空"，正因为两个同名字段。

## 三、重命名方案

### 新命名

| 关系 | 旧名 | 新名 |
|---|---|---|
| 子调用边（谁调用我） | `called_by`（顶层） | **`caller`** |
| 对话链边（聊天前驱） | `called_by`（metadata） | **`predecessor`** |

理由：
- `caller` 已经是前端在用的名字（msg dict 的 `caller` key、`_node_caller`），统一到它
- `predecessor` 比 `called_by` 更准确表达"对话链上的父"，且不和 caller 撞

### 后端改动

| 文件 | 改什么 |
|---|---|
| `context/nodes.py` | `Call.called_by` → `Call.caller`（dataclass 字段重命名）。加一次性兼容：`to_dict`/`from_dict` 读旧 `called_by` 键回填 `caller`，让旧磁盘数据能读 |
| `_msg_adapter.py` | `_msg_to_node`：msg 的 `caller` → `Call.caller`；msg 的 `predecessor`（新）/`called_by`（旧兼容）→ `metadata.predecessor`。`_node_to_msg`：反向输出 `caller` + `predecessor` 两个明确的 key |
| `session_store.py` | `_node_conv_predecessor` 读 `metadata.predecessor`；`_node_caller` 读 `Call.caller`。注释更新 |
| `memory_index.py` | 参数名/索引名不变（`predecessor`/`caller` 已经清晰），只确认读对字段 |
| `graph_builder.py` | 输出 graph dict 用 `predecessor` + `caller` 两个明确 key（不再用 `called_by`） |
| `graph_layout/_common.py` | **删掉 `called_by_of = predecessor_of` 这行覆盖**。改成两个明确函数：`predecessor_of`（读 predecessor）、`caller_of`（读 caller）。各 layout 模块按需调对的那个 |
| `graph_layout/{tier,lane,depth,topology}.py` | tier 用 `caller_of`（子调用缩进）；lane/depth/topology 用 `predecessor_of`（对话链） |

### 前端改动

| 文件 | 改什么 |
|---|---|
| `dag/types.ts` | `GNode`：删 `parent_id`（死字段）；`called_by` → `predecessor`；保留 `caller`。`layoutParent` 返回 `n.predecessor` |
| `dag/pipeline.ts` | `n.caller` 判 internal（不变）；第 202 行 `m.called_by \|\| m.called_by` 修成 `m.predecessor`；`_signature` 用 `predecessor` |
| `dag/render/{edges,nodes,badges}.ts` | `called_by` → `predecessor` |
| `conversations.ts` 等 | msg/branch dict 的 `called_by` → `predecessor`（WS 协议同步） |

### WS 协议

后端 graph dict 和前端读取要同步改键名（`called_by` → `predecessor`）。这是
跨前后端的 breaking change，必须同一批改 + rebuild。

## 四、不做兼容（旧数据已删）

旧 session 数据已全部删除（`~/.openprogram/sessions/local_*`）。**不保留任何
向后兼容逻辑** —— 代码只认新字段名 `caller` / `predecessor`，不读旧 `called_by`。

- `Call` dataclass 字段直接改名，不留 `called_by` 别名
- `_msg_to_node` / `_node_to_msg` 只处理新键名
- `from_dict` 不做旧键回填
- 旧字段 `called_by` 在整个代码库里彻底消失（前端 `parent_id` 死字段一并删）

## 五、落地步骤（每步独立验证）

| 步 | 做什么 | 验证 |
|---|---|---|
| 1 | `_common.py`：删覆盖行，拆成 `predecessor_of` + `caller_of` 两个明确函数 | tier 用 caller、lane/depth 用 predecessor，单测 |
| 2 | 后端 `Call.called_by` → `caller`（无兼容别名） | 全量 Python 测试通过 |
| 3 | `_msg_adapter` + `session_store` + `graph_builder` 用新键名输出 | 测试通过 + 手动看 graph dict 键名 |
| 4 | 前端 GNode/layoutParent/pipeline/render 用 `predecessor`，删 `parent_id` | build 通过 |
| 5 | WS 协议键名同步，rebuild + restart | 浏览器：新建会话 → 聊天 → fork → DAG 画图、分支、折叠全正常 |

## 六、待讨论的设计决策

1. ~~**conv 边叫什么？**~~ **已定：`predecessor`。**（caller=谁调用我，predecessor=对话链上我的前驱）

2. **caller 边保持 `caller` 还是叫 `invoked_by`？**
   `caller` 短、前端已在用。倾向保持 `caller`。

3. **要不要顺手把前端死字段 `parent_id` 删掉？**
   GNode 里 `parent_id` 没人写，纯死字段。倾向本次一起删。

4. ~~**磁盘兼容做多久？**~~ **已定：不兼容，旧数据已删。** 代码只认新键名。

5. **设计文档 `session-dag.md` 要不要同步更新？** 那里写的是"唯一的边
   `called_by`"，与实际两条边不符。倾向本次一并修正文档，把两条边写清楚。
