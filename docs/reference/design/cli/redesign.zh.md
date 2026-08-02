# CLI / TUI 配置界面 —— 设计

> 本文档是设置项如何被描述、读取和写入的权威设计，覆盖全部界面：
> 命令文法、定义"什么是一个设置项"的 schema、TUI 设置面板，以及
> 把它们连接起来的传输层。单个命令的命名文法见
> [`naming.zh.md`](naming.zh.md)。

## 1. 概览与动机

设置项必须能在用户当下所处的任何地方被编辑 —— shell、首次运行向导、
运行中的 TUI 会话，或 web 仪表盘。端口是最尖锐的例子：它们是启动期
绑定，用户需要在不知道哪个标志或哪个配置文件持有它们的情况下修改。

本设计要排除的失败模式是四个互相独立的设置界面。当 argparse 标志、
`setup` 向导、web `/settings` 页面和 TUI 选择器各自直接改动配置字典时，
每个新设置项都要写四遍，而每个界面各自只覆盖一个子集。不存在
"有哪些设置项"的共享描述，因此没有任何一个界面能做到完整。

**一个 schema 描述所有设置项；每个界面都是它的一个渲染器。** 新增一个
设置项就是一个 `SettingSpec`，它会自动出现在 CLI、向导、TUI 面板和
web 页面中，无需任何按界面单写的代码。

由此得出的推论是：**不存在仅限 web 的设置编辑器**。web 仪表盘存在，
但它永远不是修改某个设置的唯一途径。会话中途想切换工具、更换模型或
修正端口的用户，在终端里就能完成。

## 2. 命令模型

命令是名词在前、动词在后（`openprogram <noun> <verb>`），文法见
[`naming.zh.md`](naming.zh.md)。运行模式是动词而非标志：
`openprogram web` 启动 web UI，裸 `openprogram` 启动 chat。不存在
`--tui` / `--web` / `--cli` 模式标志。

### 容器动词始终展示子命令

纯命名空间性质的动词（`programs`、`skills`、`plugins`、`sessions`、
`channels`、`memory`、`worker`、`mcp`、`browser`、`subagent`）自身
不执行任何动作。裸调用时，它打印子命令列表并以非零码退出，统一
经由 `cli._need_subcommand(parser)` 这一个辅助函数。让每个容器动词
都走同一个辅助函数，正是行为一致性的来源 —— 各派发器里零散的
`print_help()` 调用会走样，其中一半还会以 0 退出。

带有真实默认动作的动词保留其默认行为：`providers` 显示 pools，
`ports` 显示端口表，`config` 列出设置项。区分标准是这个动词命名的
是一个动作，还是仅仅一个命名空间。

命令树中的每个 `add_argument` / `add_parser` 都带 `help=`，顶层
`--help` 带有常用命令尾注，因此命令树在 tab 补全下是自解释的。

## 3. 设置 Schema

`openprogram/config_schema.py` 持有一个单一的有序注册表。每个设置项
就是一条不可变的 spec：

```python
@dataclass(frozen=True)
class SettingSpec:
    key: str                 # 稳定 id，例如 "ui.port"
    path: tuple[str, ...]    # 进入 config.json 的点路径，例如 ("ui","port")
    group: str               # "Ports" | "Model" | "Theme" | ...
    label: str
    widget: str              # "number" | "toggle" | "enum" | "checkbox" | "secret-status"
    apply: str               # "live" | "next-start"
    choices: Callable[[], list[str]] | None = None   # 用于 enum/checkbox，读取时计算
    validate: Callable[[Any], str | None] | None = None  # 返回错误或 None
    secret: bool = False
```

`SETTINGS: list[SettingSpec]` 是事实来源，两个函数是唯一的访问路径：

- `get_settings() -> list[ResolvedSetting]` 只读一次 `config.json`，解析
  每个 spec 的当前值（惰性计算 `choices()`），并对密钥做掩码。
- `set_setting(key, value) -> {applied, error?}` 对照 spec 校验，当某个键
  已有带类型的写入辅助函数时经由它写入（`ui.*` 用 `set_ui_ports`，
  search 用 `write_search_default_provider`，`api_keys` 用 `/api/config`
  写入器），否则回退到通用的点路径写入。

通用点路径写入带有针对原型污染键的拦截防护。正是这一点让
`config set ui.port 19000` 无论由哪个界面发起都是安全的。

### 值存放在哪里

`~/.openprogram/config.json`，通过 `get_config_path()` 读取，它是
profile 感知的。逐 agent 的设置（model、effort、skills）留在 agent 记录
里；`set_setting` 处理这些键时委派给 `agents.manager`。schema 按 spec
路由到正确的写入者，而不是把 agent 状态压平进全局配置 —— 这个划分
是真实存在的，把它写进 schema 正是同一个面板能同时编辑两者的原因。

### 实时 vs 下次启动

每条 spec 声明它的更改是本会话生效还是下次启动生效，`set_setting`
返回实际生效的那一种。每次使用时都重新读取的字段（theme、effort、
model、search 默认值、工具开关）是 `live`。在绑定时只读一次的字段
（`ui.port`、`ui.web_port`、`memory.backend`）是 `next-start`，界面在
用户编辑的当下就说明这一点，而不是让用户自己去发现什么都没发生。

## 4. TUI 设置面板

`/config` 在 TUI 中打开一个分组编辑器浮层，也可从 Ctrl+K 命令面板进入。
它是一个编辑实时状态的常驻面板，而不是一次性向导 —— 会话中途编辑
正是它服务的需求。

面板构建在既有的 `Picker` 浮层机制之上（过滤 + 方向键选择），在其上
叠加 group → field → editor 的结构。enum 和 checkbox 字段直接复用
`Picker`；number 和 text 字段使用 `LineInput`。

| 分组 | 字段 | 部件 | 生效 | 后端 |
|---|---|---|---|---|
| Ports | backend 端口 | number | next-start | `ui.port` |
| | frontend 端口 | number | next-start | `ui.web_port` |
| | 打开浏览器 | toggle | next-start | `ui.open_browser` |
| Model | 默认模型 | picker | live | `default_provider` / `default_model` |
| | thinking effort | picker | live | `agent.thinking_effort` |
| Providers | key 状态 | status+action | live | `api_keys.*` |
| Theme | 配色主题 | picker+preview | live | TUI 本地 `setTheme` |
| Tools | 启用/禁用 | checkbox | live | `tools.disabled` |
| Channels | channel 启用 | status+action | 混合 | `channels.*` |
| Search | 默认后端 | picker | live | `search.default_provider` |
| Memory | 后端 | picker | next-start | `memory.backend` |

值得写明的字段行为：

- **Ports** 在输入时用 `port_in_use` 和 `describe_port_owner` 校验。被
  非我们的进程占用的端口会产生一条指明占用方的警告；面板只报告不抢占，
  与 `_ports.py` 已有的立场一致。
- **Theme** 随光标移动预览，按 ESC 回滚。没有单独的 Apply 步骤，因为
  `setTheme` 本来就是实时回调。
- **Providers 和 Channels** 展示状态和一个操作，绝不内联输入明文密钥。
  面板的职责是显示"key 已设置 / 未设置"并启动既有的登录流程。凭据
  收集保持为引导式流程，因此 OAuth 绝不会在一个文本框里被重新实现。
- **按键绑定不在范围内。** TUI 有固定的按键绑定，且没有按键绑定配置
  文件。要做按键绑定分组，就需要 `cfg['tui']['keybinds']` 以及其背后
  的按上下文划分的 schema；在出现真实需求之前，本设计不构建它。

### 命令面板

Ctrl+K 通过同一个 `Picker` 浮层渲染斜杠命令注册表（名称加描述），并带
按键提示。斜杠命令原本只能通过 `/help` 文本被发现，而命令面板是
`/config`、`/model`、`/theme` 统一呈现的地方。它不改变任何文法。

## 5. 传输层

TUI 面板搭乘已经在用的 worker WebSocket。`webui/ws_actions/settings.py`
导出一个带 `get_settings` 和 `set_setting` 的 `ACTIONS` 字典，以与其他
每个 action 模块相同的方式注册进服务器派发表。面板通过它在
`list_models` 和 `set_default_agent` 中已经使用的同一个 `BackendClient`
发送 `{action:'get_settings'}` 和 `{action:'set_setting', key, value}`。

没有新传输层，也没有新进程：面板是一条已存在连接的客户端。web 页面
通过 REST 的 `/api/settings` 访问同一个 schema。

## 附录：实现状态

schema、四个渲染器和传输层均已实现。已覆盖的配置分组是 Ports、Memory、
Search 和 Tools（逐工具开关）。Model、effort、theme 和 providers 通过
面板的操作行进入，这些行启动既有的 `/model`、`/effort`、`/theme`、
`/login` 流程，而不是重复实现它们。按键绑定编辑是被设计排除的，不是
尚未完成的在建工作（§4）。

权威代码：`openprogram/config_schema.py`、
`openprogram/webui/ws_actions/settings.py`、
`cli/src/components/SettingsPanel.tsx`。
