# OpenProgram CLI / TUI 重新设计

> **状态：已实现（2026-06）。** schema（`openprogram/config_schema.py`）
> 是唯一事实来源；它在全部四个界面中渲染——
> `openprogram config` CLI、`setup` 向导（`prompt_schema_group`）、
> TUI 的 `/config` 面板（`cli/src/components/SettingsPanel.tsx`，也可
> 通过 Ctrl+K 命令面板进入），以及 web 的 **System** 标签页
> （`/settings/system` ← `/api/settings`）。已覆盖的配置项：**Ports、
> Memory、Search、Tools**（逐工具开关）。Model / effort / theme /
> providers 通过面板的操作行进入，这些行会启动现有的
> 选择器/流程（`/model`、`/effort`、`/theme`、`/login`），而不是
> 重复实现它们。本文档着手解决的四路碎片化问题已经
> 收口：一个新的 `SettingSpec` 会出现在 CLI + 向导 + TUI + web 中，无需
> 任何按界面单独编写的代码。下文各节是最初的设计；除按键绑定编辑外，行为
> 均与之相符，而按键绑定编辑仍然搁置（opencode 也未提供）。

动机：今天只能藏在 CLI 标志之后的设置（最突出的是 **ports**）必须能从一个可视化的应用内界面进行编辑。对我们自己代码库的审计确认了核心问题：设置碎片化分散在四个界面（argparse 标志、questionary `setup` 向导、不完整的 web `/settings` 页面，以及 TUI 选择器），而 TUI 的 `/config` 斜杠命令只是一个桩，会把用户重定向回 shell（`cli/src/commands/handler.ts:601-612`）。本文档提出一个主要基于 opencode、其次基于 openclaw 的修复方案。

不可妥协的设计原则，取自 opencode：**opencode 没有仅限 web 的设置编辑器。** 它的 TUI 通过 `DialogSelect` / `DialogThemeList` 对话框编辑实时状态（主题随光标移动预览、model/agent/provider 选择器）。web 仪表盘存在，但不是更改设置的唯一途径。我们的错误在于把 web `/settings` 页面与 TUI 选择器构建成了相互独立、各自只覆盖一部分、且没有共享后端的界面。我们将它们统一到一个 schema 上。

## 1. 命令模型——保留动词文法，补齐可发现性缺口

我们已经做对、且应当保留的部分：

- 单动词模型（`openprogram <verb> <subverb>`，openclaw/`gh`/`docker` 风格）是正确的，且与两个参考实现都一致。opencode 是名词在根 / 动词在子命令（`session list`、`mcp add`、`console login`）；openclaw 也一样（`config get/set`、`channels accounts add`）。我们的 `cli.py` 已经用 argparse 子解析器做到了这一点（`programs run`、`skills list`、`mcp add`、`channels accounts add`）。
- 弃用 `--tui` / `--web` / `--cli` 模式标志、改用动词（`openprogram web`，裸 `openprogram` = chat）是正确的决定——opencode 正是这么做的（`$0` 位置参数启动 TUI，`web`/`serve` 是动词）。
- `openprogram ports` 是一个好的、聚焦的命令，而 `_ports.py` 确实很扎实：它对应了 openclaw 的三段式端口处理（存活探测、身份探测 `backend_is_ours`/`frontend_is_ours`、占用方诊断 `describe_port_owner`/`port_owner_hint`）以及“是我们的就复用 / 不是就报告”的立场。**在此基础上扩展，而不是替换它。**

需要修复的两个具体缺口，每个都基于 opencode 的某种模式：

**(a) 容器动词必须始终展示它们的子命令。** opencode 使用 `yargs.command(A).command(B).demandCommand()`，因此裸 `opencode session` 会打印子命令列表而不是什么都不做。我们的若干 argparse 容器解析器（例如当 `programs_verb`/`memory_verb` 为 `None` 时）依赖各自零散、不统一的 `print_help()` 调用。增加一个统一的辅助函数，每个容器动词的派发器在其 `*_verb` 为 `None` 时都调用它：打印该子解析器的帮助并以非零退出。对 `cli.py` 扫一遍即可。

> **已完成（2026-06）。** `cli._need_subcommand(parser)`（打印帮助 + `sys.exit(2)`）；每个纯容器动词（programs / skills / plugins / sessions / channels / memory / worker / mcp / browser / subagent，以及 `agents.py` 内联部分）的“无子命令”分支都经由它路由，于是它们全部以 2 退出并附带动词列表——此前有一半是以 0 退出的。带有真实默认动作的动词（`providers`→pools、`ports`→show、`config`→list）有意保留各自的默认行为。另外，整个 CLI 中的每个 `add_argument`/`add_parser` 现在都带有 `help=`（全包扫描：0 处无文档说明），且顶层 `--help` 增加了一段“常用命令”尾注以提升可发现性。

**(b) `openprogram config` 成为一等概念，而不是 `setup` 的别名。** 今天 `config` 在 TUI 绕过词表中（`cli.py:91`），但实际命令是 `setup [menu|<section>]`。将其改名/设别名为 `openprogram config`：
- `openprogram config`（无参数）→ schema 驱动的选择器菜单（我们在 `wizard.py:265` 已有的 openclaw `run_configure_menu` 选择循环）。
- `openprogram config get <dot.path>` / `config set <dot.path> <value>` → 非交互式，与 openclaw 基于 `parseConfigPath` 的 `config get/set/unset` 完全一致。这提供了可脚本化的编辑能力，以及一个稳定的文档目标。
- `openprogram config <section>` → 跳转到某一节（已支持）。

命名保持名词在前；`setup` 保留为首次运行线性流程（`run_full_setup`）的别名。每个动词的帮助文本已经存在；唯一新增的是由 §3 schema 支撑的 `get`/`set` 叶子命令。

## 2. 可视化的设置体验——一个 schema 驱动、跑在现有 WebSocket 之上的 TUI 设置面板

决定：**给 Ink TUI 加一个真正的 Settings 面板**，由 `/config`（以及将来的 Ctrl+K 命令面板入口）打开。不是“又一个选择器”——而是一个分组编辑器。基于参考实现的理由：

- opencode 在终端中的设置 UX 是*编辑实时状态的对话框*（`DialogThemeList`、用于 model/agent/provider 的 `DialogSelect`）。它不会为了 theme/model/provider 把用户甩到浏览器里。我们的 TUI 已经具备完全相同的底座：`Picker` 组件（`cli/src/components/Picker.tsx`）是一个自包含的浮层，带过滤 + 方向键选择，而 `openPicker(kind)` 已经在 `REPL.tsx` 中接好。设置面板就是同一套浮层机制，在其上叠加一个 group→field→editor 的结构。
- openclaw 的 `configure` 是一个带可组合分节（auth、models、gateway、channels、plugins）的 TUI 向导。这验证了“在终端中进行应用内、分节的设置编辑”作为一种形态是可行的——但 openclaw 的是一次性向导，而不是常驻面板。我们采用 opencode 的*常驻、实时编辑对话框*模型，因为会话中途编辑（切换某个工具、更改模型、修正端口）正是审计指出的真实用户需求（“会话中途的用户想在不退出的情况下切换工具或更改模型”）。

### 面板编辑哪些内容，以及每个字段如何生效

面板按组组织。每个字段声明它是**实时**生效（本会话即生效）还是**下次启动**生效（worker/web 必须重启）。这个“实时 vs 下次启动”的标志是 UX 诚实性的关键：`setup.py:150` 的 `set_ui_ports` 已经写明“下次启动时生效——此处不会实时重新绑定任何东西”。我们把它按字段显式呈现出来，而不是埋起来。

```
Group        Field                 Widget        Apply        Backing
Ports        backend port          number-input  next-start   ui.port      (set_ui_ports)
             frontend port         number-input  next-start   ui.web_port  (set_ui_ports)
             open browser          toggle        next-start   ui.open_browser
Model        default model         picker        live         default_provider/default_model
             thinking effort       picker        live         agent.thinking_effort (per default agent)
Providers    <provider> key set?   status+action live*        api_keys.* (POST /api/config)
Theme        color theme           picker+preview live         (TUI-local: setTheme)
Tools        enabled/disabled      checkbox      live          tools.disabled
Channels     <channel> enabled     status+action mixed         channels.* (status only in panel; login stays a flow)
Search       default backend       picker        live         search.default_provider
Memory       backend               picker        next-start*   memory.backend
```

基于我们的代码和参考实现的说明：
- **Ports** 是重头戏。number-input 部件是新的（Picker 今天只支持枚举）；它是一个小巧的 Ink 输入框，复用 `LineInput`。编辑通过 `set_ui_ports` 写入，面板显示“已保存——下次 `openprogram web`/`worker` 启动时生效”，复用 CLI 已经打印的完全相同的措辞。当用户输入端口时，用 `_ports.port_in_use` + `describe_port_owner` 校验，并在端口被非我们的进程占用时发出警告——这正是我们的 `_ports.py` 已经实现的 openclaw “不是就报告”的立场。
- **Theme** 采用 opencode 的 `DialogThemeList` **随移动预览 / 取消时回滚**模式：光标移动时设置主题，按 ESC 时恢复原主题，没有单独的 Apply 按钮（`setTheme` 已经是 `SlashContext` 中的实时 TUI 回调）。
- **Providers / Channels** 展示*状态和一个操作*，而不是内联的明文密钥输入。opencode 在它的 provider 编辑器里对 API key 做掩码处理；我们已经有返回掩码 key 的 `/api/config` 以及 `/api/config/verify`。channel 登录（二维码 / token）保持为引导式流程——openclaw 把凭据收集放在向导适配器里，而不是单个字段里。面板的职责是显示“Anthropic: key set ✓ / not set ✗”并启动现有流程，而不是在一个文本框里重新实现 OAuth。
- **按键绑定有意不在面板范围内。** opencode 本身**不**提供可视化按键绑定编辑器——按键绑定只能用文件配置（`tui.json`）。我们的 Ink TUI 有固定的按键绑定，且没有按键绑定配置文件。凭空发明一个并无调研支持；搁置（见 §4 P2-optional）。

### 可发现性：覆盖斜杠命令注册表的命令面板（P2）

opencode 的 `command-palette.tsx`（Ctrl/Cmd+K）按命名空间/可见性过滤地列出命令并带有按键提示。我们在 `registry.ts` 里有 40+ 个斜杠命令，但它们只能通过 `/help` 文本被发现。一个把 `SLASH_COMMANDS`（名称 + 描述）通过现有 `Picker` 浮层渲染出来的 Ctrl+K 面板，能在不改变文法的情况下提升可发现性，而且这里也是 `/config`、`/model`、`/theme` 统一呈现的地方。这是增量的、低风险的；它在面板之后落地。

## 3. 端到端的配置查看/编辑——一个 schema、三个渲染器、一个写入者

碎片化的根本原因是每个界面都直接戳配置字典：`setup.py` 有 `read_ui_prefs`/`set_ui_ports`/`read_search_default_provider`；`webui/server.py` 有它自己的 `_load_config`/`_save_config`；`routes/config.py` 直接写 `api_keys`；`_setup_sections/sections.py` 里每个 questionary 分节读写它自己的键。不存在一个对“有哪些设置存在”的共享描述。

**引入 `openprogram/config_schema.py`**——一个单一的有序注册表，就像 openclaw 把配置集中在 `parseConfigPath`/`setConfigValueAtPath` 之后（通过 `isBlockedObjectKey` 做原型污染防护），以及 opencode 把配置集中在带类型的 `Config.Service` + 类 Zod schema 之后。

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

单个 `SETTINGS: list[SettingSpec]` 是事实来源。两个函数取代所有零散访问：
- `get_settings() -> list[ResolvedSetting]`——只读一次 `config.json`，解析每个 spec 的当前值（惰性计算 `choices()`），对密钥做掩码。
- `set_setting(key, value) -> {applied: 'live'|'next-start', error?}`——对照 spec 校验，在存在*既有*带类型辅助函数时经由它写入（`ui.*` 用 `set_ui_ports`，search 用 `write_search_default_provider`，`api_keys` 用 `/api/config` 写入器），否则回退到通用的点路径写入（带 openclaw 的受阻键防护）。

带原型污染拦截名单的点路径修改直接取自 openclaw 的 `parseConfigPath` + `setConfigValueAtPath`/`unsetConfigValueAtPath`。正是这一点让 `config set ui.port 19000` 安全，并让 TUI 面板和 web 页面成为*同一条*经校验路径的写入者。

**唯一事实来源 = `~/.openprogram/config.json`**，通过 `get_config_path()` 读取（已经经由 `setup.py:35` 中的 `_ConfigPathProxy` 实现 profile 感知）。逐 agent 的设置（model、effort、skills）继续存在于 agent 记录中；schema 的 `set_setting` 对这些键的处理委派给 `agents.manager`，与 `read_agent_prefs`/`read_disabled_skills` 已有做法完全一致。schema 不会把 agent 状态压平进全局配置——它按 spec 路由到正确的写入者。这尊重了审计指出的既有划分，而不是把它糊过去。

**三个渲染器，零重复的字段逻辑：**
1. questionary 分节（`_setup_sections/sections.py`）遍历 schema 分组，而不是手写提示。
2. TUI Settings 面板遍历同样的分组。
3. web `/settings` 页面渲染同样的分组（通过数据驱动来完成那些不完整的页面）。

**实时 vs 下次启动，由 schema 显式表达。** `set_setting` 返回 `applied`。对于 `live` 字段（theme、effort、model、search-default、工具开关——今天都已经在每次使用时重新读取），更改立即生效；TUI 面板无需重启即可反映。对于 `next-start` 字段（`ui.port`、`ui.web_port`、`memory.backend`、backend-exec），面板显示“下次启动时生效”那一行，并在相关情况下，若新端口已被占用则显示 `_ports.port_owner_hint`。这与两个参考实现都吻合：opencode 惰性读取配置，因此大多数更改是实时的；端口/服务器绑定本质上是启动期才发生的。

**TUI 面板的传输层 = 现有的 worker WebSocket。** 增加 `openprogram/webui/ws_actions/settings.py`，导出一个带 `get_settings` 和 `set_setting` 的 `ACTIONS` 字典，接入 `webui/server.py:1067-1108` 处的派发表（一行 `table.update(_ws_settings.ACTIONS)`，与其他每个 action 模块的注册方式完全一致）。处理函数调用 `config_schema.get_settings()` / `set_setting()`。Ink 面板通过它在 `list_models`/`set_default_agent` 中已经使用的同一个 `BackendClient` 发送 `{action:'get_settings'}` 和 `{action:'set_setting', key, value}`——没有新传输层，没有新进程。web 页面调用 REST（把 `/api/config` 扩展到通用 schema，或者一个镜像这些 WS action 的轻量 `/api/settings`）。

## 4. 面向我们架构（argparse + questionary + Ink-over-WS）的分阶段计划（P0 / P1 / P2）

**P0——schema + ports 可在 TUI 中编辑（用户真正的诉求）。约 2–3 天。**
- `openprogram/config_schema.py`：`SettingSpec`、`SETTINGS`（从 Ports、Model、Theme、Tools、Search 分组开始）、`get_settings`、带 openclaw 风格点路径写入 + 受阻键防护的 `set_setting`。把 `ui.*` 委派给既有的 `set_ui_ports`。*（小–中）*
- `webui/ws_actions/settings.py` + `server.py` 派发表里的一行。*（小）*
- Ink 的 `SettingsPanel.tsx` 浮层（enum/checkbox 复用 `Picker`；为 ports 通过 `LineInput` 加一个小的 number/text 字段）。把 `handler.ts` 里的 `/config` 接成打开它，替代第 601-612 行的桩；在 `REPL.tsx` 里增加 `PickerKind`/面板状态。*（中）*
- Ports 字段：用 `_ports.port_in_use` + `describe_port_owner` 校验，显示下次启动生效的提示。*（小）*

退出标准：在运行中的 TUI 会话里，`/config` → Ports → 更改 backend 端口 → 看到“已保存，下次启动时生效”，若已被占用则带冲突警告。仅此一项就满足了最初的动机性需求。

**P1——把其他界面统一到 schema 上；主题实时预览。约 2–3 天。**
- 重写 `_setup_sections/sections.py` 的 runner，使其遍历 schema 分组（questionary 部件由 `spec.widget` 选择）。新增设置 = 一个 `SettingSpec`，自动出现在向导 + TUI 中。*（中）*
- 在 `cli.py` 中增加由 `config_schema` 支撑的 `openprogram config get/set` 叶子命令。*（小）*
- TUI 面板中的 Theme 分组采用 opencode 的随移动预览 / 按 ESC 回滚。*（中）*
- 通过 `/api/settings` 以 schema 数据驱动来完成 web `/settings` 页面。*（中）*——补上审计指出的“web 页面不完整”缺口。

**P2——可发现性 + 打磨。约 2 天。**
- 覆盖 `registry.ts` 的 Ctrl+K 命令面板（opencode `command-palette.tsx`），带按键提示。*（中）*
- `cli.py` 中容器动词帮助的一致性（opencode 的 `.demandCommand()` 行为）。*（小）*
- 面板中的 Providers/Channels 状态与操作行（状态来自 `/api/config` 的掩码 key + channels 列表；操作启动现有流程）。*（中）*

**明确搁置（无调研支持）：** 一个可视化的按键绑定编辑器。opencode 不提供（只能用文件 `tui.json`）；我们的 TUI 根本没有按键绑定配置文件。如果出现需求，再增加一个由新的 `cfg['tui']['keybinds']` 映射支撑的 `keybinds` 分组，采用 opencode 的“按上下文分 schema”做法——但仅在那时。

### 为什么这特别契合我们的技术栈
- 它**不增加任何新运行时**：TUI 面板搭乘已经在用的 worker WebSocket；schema 就是既有配置模块里的纯 Python；questionary 和 Ink Picker 原样复用。
- 它通过让 `config_schema.py` 成为唯一写入者来消除四路碎片化，就像 opencode 集中在 `Config.Service`、openclaw 集中在 `parseConfigPath`/`mutateConfigFile` 上一样。
- 它保留了我们已经做得好的部分——动词文法、`openprogram ports`，以及 `_ports.py` 的所有权诊断——并让它们可以从应用内部触达，而不再只能从 shell 触达。
