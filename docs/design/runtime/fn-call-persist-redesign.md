# 函数调用节点设计（重设计）

## 核心原则

**调用了什么函数，那个函数就是 DAG 里的节点。** 不管是 agentic function、普通函数、还是 LLM 调用——调用了就加一个 code 节点，直接挂在调用者下面。不需要任何 anchor、placeholder、或其他辅助节点。

## 现状（要删的）

用户手动调用函数时，系统写 3 个节点：

```
ROOT
└── anchor (role=user, display=runtime)        ← 假 user 消息，删
    └── placeholder (role=llm, display=runtime) ← 假 agent 回复，删
        └── code gui_agent                      ← 真正的函数调用
```

anchor 和 placeholder 是旧聊天 UI 要求 user→assistant 交替的遗留。新 DAG 模型不需要交替。

## 新设计

只写 1 个节点：调用的函数本身。

### 三种调用场景

**场景 1：用户手动调用函数**（点 "Run gui_agent" 或在输入框写 `/run gui_agent`）

```
ROOT
├── user "你好"
│   └── llm "你好呀"
└── code gui_agent (called_by=ROOT)
    ├── code gui_step
    │   └── code plan_next_action
    │       └── llm
    └── code conclusion
        └── llm
```

code gui_agent 直接挂在 ROOT 下面，跟 user 消息平级。

**场景 2：LLM 调用函数**（agent 在聊天中决定调用工具）

```
ROOT
└── user "帮我调研 X"
    └── llm "好的，我来调用 wiki_agent"
        └── code wiki_agent (called_by=llm)
            └── llm (内部 LLM 调用)
```

code 节点挂在触发它的 llm 节点下面。

**场景 3：用户调用一个普通函数**（不是 agentic，就是一个简单函数）

```
ROOT
├── user "你好"
│   └── llm "你好呀"
└── code my_simple_tool (called_by=ROOT)
```

一样的规则——调用了什么就挂什么，没有子调用就没有 children。

### 节点写入

```
函数开始执行时写入:
  Call(
    role    = ROLE_CODE,
    name    = "gui_agent",          # 函数名
    called_by = <调用者>,            # 用户调用: ROOT
                                    # LLM 调用: assistant_msg_id
    input   = {task: "...", ...},   # 函数参数
    output  = null,                 # 还没返回
    status  = "running",
  )

函数返回后更新:
  output = <返回值>
  status = "completed" / "error"
```

### 聊天 UI 渲染

| 节点 | 渲染成什么 |
|---|---|
| `role=user` | User 气泡 |
| `role=llm` 无 function | Agent 气泡（文字回复） |
| `role=code` | Function call 卡片（显示函数名 + 参数 + 进度树 + 结果） |

Function call 卡片直接从 code 节点渲染：
- 函数名从 `name` 读
- 参数从 `input` 读
- 进度树从子节点构建（现有的 `build_exec_dag`）
- 结果从 `output` 读

### DAG 侧边栏

| 节点 | 形状 |
|---|---|
| ROOT | 菱形 |
| user（真实用户消息） | 圆形 |
| llm（大模型回复） | 三角形 |
| code（任何函数调用） | 方形 |

### 要删的

| 概念 | 位置 | 说明 |
|---|---|---|
| fn-form anchor | `server.py _append_msg` | 不再写 `display=runtime` 的假 user 消息到 SessionStore |
| RuntimeBlock placeholder | `runtime_attach.py` | 不再写 `display=runtime` 的假 assistant 到 SessionStore |
| `display=runtime` 过滤 | `graph_layout/filter.py` | 不会再有 runtime 节点了，删掉过滤 |
| `collapseRuntimePlaceholders` pass | 前端 passes/ | 没有 placeholder 就不需要折叠 |

### 要改的

| 文件 | 改什么 |
|---|---|
| `runtime_attach.py` | `_call_id` 设成真正的调用者（ROOT 或 assistant_msg_id），不设成 placeholder id |
| `function.py` | code 节点的 `called_by` 自动从 `_call_id` 读，不需要改（上游改了就行） |
| 前端 message-list | code 节点 → 渲染为 Function call 卡片（不再靠 `display=runtime` 判断） |
| `build_exec_dag` | 入口改成用 code 节点 id 查找子节点 |

### 不改的

| 什么 | 为什么不改 |
|---|---|
| 普通聊天（user→llm） | 不涉及函数调用 |
| code 节点内部的子调用链 | `called_by` 自然嵌套，不需要改 |
| DAG 布局算法 | code 节点跟其他节点一样参与 tier/depth/lane 计算 |
| WS 实时广播 | `build_exec_dag` 推送进度树不变 |
