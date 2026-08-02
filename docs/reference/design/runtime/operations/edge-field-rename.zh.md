# DAG 边字段命名

> 一个 DAG 节点带两种不同的父关系。它们分别存在两个不同的名字下——`caller` 和
> `predecessor`——这样任何一处代码都不必猜某个字段指的是哪种关系。本文说明这两条边、
> 为什么需要分开命名，以及这两个名字在后端、前端和 WS 协议里各出现在哪里。

## 一、两种父关系

一个节点要表达**两种不同的父子关系**：

| 关系 | 含义 | 字段 | 举例 |
|---|---|---|---|
| **caller** | 谁调用了我（子调用边） | 节点顶层 `Call.caller` | 工具被哪个 LLM 调起；ROOT 调起顶层节点 |
| **conv 前驱** | 聊天顺序上我接在谁后面（对话链边） | 顶层 `Call.predecessor` | 第二轮 user 接在第一轮 reply 后 |

### 为什么需要两个

两者经常不一样。典型例子是顶层 user 节点：

- caller = `ROOT`（它不是被谁调用的，是会话发起）
- conv 前驱 = 上一轮的 reply（聊天顺序）

只有一个字段没法同时表达"挂在根上"和"接在上一句后面"。分支区分**靠的是 conv 前驱**
（同一个 conv 前驱有多个孩子 = fork），不是 caller。

两种关系用同一个名字，正是代码里容易混淆的原因。当两者都叫 `called_by`（一个在节点
顶层、一个在 metadata 里）时，一个按 caller 命名的布局辅助函数实际读的是 conv 前驱；
一处本想在两个字段间取回退的渲染表达式，两边读的是同一个字段。名字分开就消除了这
一整类错误。

## 二、命名

| 关系 | 名字 |
|---|---|
| 子调用边（谁调用我） | **`caller`** |
| 对话链边（聊天前驱） | **`predecessor`** |

理由：

- `caller` 是前端已经在用的名字（msg dict 的 `caller` key、`_node_caller`），前后端
  统一到同一个词。
- `predecessor` 准确表达"对话链上的父"，且不和 caller 撞名。

## 三、两个名字出现在哪里

### 后端

| 文件 | 符号 | 作用 |
|---|---|---|
| `context/nodes.py` | `Call.caller` | dataclass 的边字段，语义 = caller |
| `store/session/_msg_adapter.py` | `_msg_to_node` | msg 的 `caller` → `Call.caller`；msg 的 `predecessor` → `Call.predecessor`（从 metadata 里 pop 掉，不留镜像） |
| `store/session/_msg_adapter.py` | `_node_to_msg` | 反向：输出 `caller` + `predecessor` 两个明确的 key |
| `store/session/session_store.py` | `_node_conv_predecessor` | 读 `Call.predecessor` |
| `store/session/session_store.py` | `_node_caller` | 读 `Call.caller` |
| `store/session/memory_index.py` | `append(node, predecessor, caller)` | 两个索引：`children_by_predecessor`（conv）/ `children_by_caller`（caller） |
| `webui/graph_builder.py` | `build_session_graph` | 构建 graph dict，用 `predecessor` + `caller` 两个明确 key |
| `webui/graph_layout/_common.py` | `predecessor_of` / `caller_of` | 两个明确的访问函数，各 layout 模块按需调对应的那个 |
| `webui/graph_layout/tier.py` | — | 用 `caller_of`（子调用缩进） |
| `webui/graph_layout/{lane,depth,topology}.py` | — | 用 `predecessor_of`（对话链） |

### 前端

| 文件 | 符号 | 作用 |
|---|---|---|
| `dag/types.ts` | `GNode` | 带 `predecessor`（conv）和 `caller`（子调用） |
| `dag/types.ts` | `layoutParent(n)` | 返回 `n.predecessor`（conv 前驱），构建树用 |
| `dag/pipeline.ts` | `render` | `n.caller` 判 internal；`m.predecessor` 驱动对话链；`_signature` 用 `predecessor` |
| `dag/render/{edges,nodes,badges}.ts` | — | 读 `predecessor` 画连线 / 判分支 |
| `conversations.ts` | `LegacyMessage` / `BranchRow` | msg/branch dict 流转，两个 key 都透传 |

### WS 协议

后端 graph dict 和前端读取共用 `caller` 和 `predecessor` 这两个键名。两端是同一套
协议：任一键名的改动都要同一批落到两侧，并重新 build。

## 四、不做磁盘兼容层

代码只认 `caller` / `predecessor`。不为旧键名留别名，也不做回填：

- `Call` dataclass 字段就是 `caller`，背后不留别名
- `_msg_to_node` / `_node_to_msg` 只处理这两个键名
- `from_dict` 不从其他键回填
- 前端 `GNode` 不带无人写入的父字段

## 实现状态

前后端均已实现。`Call.caller` 与 `Call.predecessor` 是仅有的两个边字段；
`graph_builder` 和 WS graph dict 输出两个明确的 key；`_common.py` 提供
`predecessor_of` 和 `caller_of` 两个独立访问函数，tier 读 caller，
lane/depth/topology 读 predecessor。会话数据模型以
[`../dag/overview.zh.md`](../dag/overview.zh.md) 为权威，其中记录了这两条边。
