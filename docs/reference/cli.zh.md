# CLI

`openprogram` 全部子命令的速查表。每条命令都可以用 `openprogram <command> -h` 查看自己的帮助；子命令的动词再套一层，如 `openprogram logs tail -h`。

> [!NOTE]
> 侧栏的 **CLI 命令** 分区里每条命令有一页生成文档——完整参数表，
> 每次构建文档站时从参数解析器重新生成，永不与代码脱节。本页是人工整理的总览。

## 全局用法

```bash
openprogram                      # 打开终端聊天 UI（TUI）
openprogram --print "..."        # 一次性 prompt：发送、打印回复、退出
openprogram --resume <id>        # 恢复此前的 CLI 聊天会话
openprogram --profile <name>     # 状态目录 profile，改道到 ~/.openprogram-<name>/
```

| 选项 | 作用 |
|------|------|
| `--print PROMPT` | 一次性 prompt，打印回复后退出 |
| `--profile PROFILE` | 状态目录 profile，等价于环境变量 `OPENPROGRAM_PROFILE` |
| `--resume SESSION_ID` | 恢复会话；id 用 `openprogram sessions list` 或 Web UI 侧栏查 |
| `--no-alt-screen` | 使用行内 TUI 并保留终端滚屏 |
| `--screen-reader` | 使用不启用鼠标追踪的行内无障碍模式 |

## 聊天与运行

| 命令 | 作用 | 关键参数 |
|------|------|----------|
| `openprogram` | 打开聊天；裸跑会先问开终端 UI 还是 Web UI，没有 worker 时自动拉起 | — |
| `openprogram tui`（别名 `chat`） | 在 Windows、macOS 或 Linux 直接启动 Ink 终端 UI；无法提供 raw input 的终端回退到 Rich | `--print`、`--resume`、`--no-alt-screen`、`--screen-reader` 在动词后同样可用 |
| `openprogram web` | 启动服务并打开浏览器 UI（`http://localhost:18100`） | `--port`（默认：已存偏好，否则 18100）、`--web-port`（同一个单端口的旧别名）、`--no-browser` |

## 后台服务

| 命令 | 作用 |
|------|------|
| `status` | 后台服务是否在跑（PID、端口、运行时长） |
| `stop` | 停止后台服务 |
| `restart` | 重启（改了代码 / 配置之后用） |

`worker` 子命令提供更细的控制：

| 命令 | 作用 |
|------|------|
| `worker run` | 前台运行 worker（阻塞），调试用，Ctrl-C 停止 |
| `worker start` | 后台启动一个 worker 并返回 |
| `worker stop` | 停止（SIGTERM，必要时升级为 SIGKILL） |
| `worker restart` | 停掉再起一个新的 |
| `worker status` | 是否在跑、PID、端口、运行时长 |
| `worker install` | 安装为系统服务（macOS launchd / Linux systemd --user），随登录启动、崩溃重启 |
| `worker uninstall` | 移除系统服务 |

## 安装与配置

| 命令 | 作用 | 关键参数 / 动词 |
|------|------|----------|
| `setup` | 首次运行的设置向导 | `menu` 打开交互选择器；给一个分区名直达（model / tools / agent / skills / ui / memory / profile / search / tts / channels / backend） |
| `config` | 查看 / 修改设置 | `list`（全部设置：值、分组、生效方式）、`get <key>`、`set <key> <value>` |
| `ports` | 查看 / 持久化 Web UI 的单端口 | `--frontend PORT`（默认 18100）、`--backend PORT`（`--frontend` 的旧别名，两个端口已合并） |
| `completion` | 输出 shell 补全脚本 | `bash` / `zsh` / `powershell` / `pwsh` |

### providers —— LLM provider 与凭据

`secrets` 是 `providers` 的别名。

| 动词 | 作用 |
|------|------|
| `login <provider>` | 登录一个 provider；`--api-key` / `--api-key-stdin` 非交互提供 key，`--profile` 指定凭据 profile，`--method` 强制指定登录方式 |
| `logout` | 移除一个 provider 的凭据 |
| `list` | 按 profile 列出凭据池 |
| `available`（别名 `search`、`catalog`） | 列出全部可配置的 provider，可加 QUERY 过滤 |
| `status` | 检查一个 provider 当前的凭据 |
| `use` | 设置一个 provider 用哪个账号（profile） |
| `discover` / `adopt` | 扫描外部来源的凭据 / 收编进凭据库 |
| `doctor` | 诊断凭据（过期、刷新、冷却、冲突） |
| `setup` | 交互式首次配置 |
| `aliases` | 列出 provider 短名别名 |
| `profiles` | 凭据 profile 管理（`list` / `create` / `delete`） |
| `migrate` | 把存储的凭据迁移到当前格式 |

不带动词的 `openprogram providers` 打印当前全部凭据的状态表。

### mcp —— MCP server

| 动词 | 作用 |
|------|------|
| `list` | 列出全部已配置的 MCP server 及状态 |
| `show` | 显示一个 server 的工具与完整 schema |
| `add` | 添加 stdio 命令型 server，写入 `mcp_servers.json` 并立即启动 |
| `rm` | 移除（停止 + 删配置） |
| `restart` / `enable` / `disable` | 重启 / 启用并启动 / 停止并标记禁用（保留配置） |
| `edit` | 用 `$EDITOR` 直接编辑 `mcp_servers.json` |
| `test` | 临时启动一个配置，验证能起来并返回工具列表，不落盘 |

### browser —— 浏览器工具

| 动词 | 作用 |
|------|------|
| `install` | 开发者用于增加或替换 Browser backend（patchright/camoufox/agent-browser）的命令。release 安装已包含默认 Playwright Chromium backend。 |
| `status` | 显示安装情况、sidecar Chrome 是否在跑、保存的登录数 |
| `refresh` | 重新把真实 Chrome profile 拷到 sidecar（在主 Chrome 登录新站点后用） |
| `reset` | 完全重置：杀 sidecar、清 profile + 登录态 + 端口文件 |
| `list` / `rm` | 列出 / 删除 `~/.openprogram/browser-states/` 下保存的登录 |

## 内容管理

### agents

| 动词 | 作用 |
|------|------|
| `list` / `show` / `add` / `rm` | 列出 / 查看 / 创建 / 删除 agent（删除会连带其全部会话） |
| `set-default` | 设为默认 agent |

### sessions

| 动词 | 作用 |
|------|------|
| `list` | 列出所有 agent 的全部会话 |
| `resume` | 回答一个等待中的会话 |
| `attach` / `detach` | 把频道用户的消息路由进某会话 / 取消别名（`--channel`、`--peer` 必填；`--account`、`--peer-kind` 可选） |
| `aliases` | 列出全部会话与频道用户的别名 |

### subagent

| 动词 | 作用 |
|------|------|
| `spawn` | 在某会话里生成一个新分支的 agent：`--session` 和 `--prompt` 必填；`--parent-msg` 指定分叉节点，`--label` 命名分支，`--agent` 选 agent profile（默认 `main`），`--context inherit\|clean`（或 `--clean`），`--no-json` 打印人类可读摘要 |
| `merge` | 把多个 subagent 会话合并进目标会话形成新 turn：`--target` 与可重复的 `--branch SID` 必填；`--message` 是合并指令，`--agent` 选合并 agent，`--base N` 把某个分支标记为合并基底，`--no-json` 打印人类可读摘要 |

### programs

| 动词 | 作用 |
|------|------|
| `run <name>` | 运行一个 program；`--arg key=value`（可重复）、`--provider`、`--model` |
| `list` | 列出保存的 program |
| `available` | 列出可安装的 program 与已装的第三方 harness |
| `install` / `uninstall` | 开发者使用的第一方源码 overlay（gui/research/wiki/all），或安装/卸载额外第三方 harness（git URL / owner/repo）；受支持的 release 已包含全部第一方 Program，并拒绝修改 immutable runtime |

### skills

| 动词 | 作用 |
|------|------|
| `list` | 列出发现的技能 |
| `search` / `install` | 在发现源（默认 ClawHub）搜索 / 安装技能 |
| `update` | 重拉过期技能（比对 SKILL.md 哈希） |
| `remove` | 删除已装技能 |
| `doctor` | 扫描技能目录的问题 |

### plugins

| 动词 | 作用 |
|------|------|
| `list` / `search` | 列出已装插件 / 搜索 marketplace |
| `install` / `uninstall` / `update` | 从 pip / npm / git / 路径安装、卸载、升级 |
| `enable` / `disable` | 启用 / 禁用 |

### channels —— 聊天频道机器人

| 动词 | 作用 |
|------|------|
| `list` | 各平台的启用与配置状态 |
| `setup` | 交互向导：选频道、登录（扫码 / token）、绑定 agent |
| `accounts` | 管理频道机器人账号（WeChat、Telegram 等） |
| `bindings` | 把入站频道消息路由到 agent |
| `access` | 谁能进到 agent：`list`、`approve <code>`、`allow <user_id>`、`revoke <user_id>`、`policy pairing\|open`。一个账号可以批准任意多个发信人（见[聊天渠道](../integrations/channels.zh.md#谁能和你的机器人说话)） |

### memory —— 持久记忆

每个实例只有一份工作区，所有agent、所有对话（含聊天渠道）共用。

| 动词 | 作用 |
|------|------|
| `status` | owner 视图：workspace 路径/revision、文件与关系计数、writer 健康状态、承诺计数与记录 |
| `recall` | 搜索 wiki + 近期 journal，打印原始片段；`--days N` 限定 journal 窗口（默认 30） |
| `show` / `edit` | 打印 / 用 `$EDITOR` 编辑一个 wiki 页 |
| `sleep` | 立即跑一轮 sleep 整理（light → deep → REM）；`--phase light\|deep\|rem` 只跑一个阶段 |
| `reflections` | 打印 `wiki/reflections.md` 最新条目 |
| `export` | 把整个记忆目录 tar+gzip 打包；`--out PATH` 指定输出文件（默认 `./openprogram-memory-<date>.tar.gz`） |

## 维护

| 命令 | 作用 | 关键参数 / 动词 |
|------|------|----------|
| `doctor` | 端到端健康检查 | `--json` 输出 JSON |
| `rescue` | 诊断问题并直接打印修复命令 | — |
| `diagnostics` | 生成脱敏支持包 zip（版本、配置、日志、探测），可直接附在故障报告里，见[诊断包](diagnostics.zh.md) | `--output PATH`（默认 `./openprogram-diagnostics-<日期>.zip`） |
| `logs` | 查看日志 | `list`；`tail [name]`（`-n` 行数、`-f` 跟踪）；`path [name]`。name 为 worker / runtime / ink，默认 worker |
| `update` | 检查并应用更新 | `--check` 只检查；`--force` 绕过 6 小时节流 |
| `scheduler-worker`（兼容别名 `cron-worker`） | 前台循环，触发一次性、周期和监控 Scheduler 任务 | `--once` 只评估一个 tick 就退出；`--list` 显示当前任务匹配状态 |
