# CLI 命令参考

`openprogram` 全部子命令的速查表。每条命令都可以用 `openprogram <command> -h` 查看自己的帮助；子命令的动词再套一层，如 `openprogram logs tail -h`。

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

## 聊天与运行

| 命令 | 作用 | 关键参数 |
|------|------|----------|
| `openprogram` | 打开终端聊天 UI；没有 worker 时自动拉起 | — |
| `openprogram web` | 启动服务并打开浏览器 UI（`http://localhost:18100`） | `--port`（backend，默认 18109）、`--web-port`（frontend，默认 18100）、`--no-browser` |

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
| `ports` | 查看 / 持久化 Web UI 端口 | `--backend PORT`（默认 18109）、`--frontend PORT`（默认 18100） |
| `completion` | 输出 shell 补全脚本 | `bash` / `zsh` / `powershell` / `pwsh` |

### providers —— LLM provider 与凭据

| 动词 | 作用 |
|------|------|
| `login <provider>` | 登录一个 provider；`--api-key` / `--api-key-stdin` 非交互提供 key，`--profile` 指定凭据 profile |
| `logout` | 移除一个 provider 的凭据 |
| `list` | 按 profile 列出凭据池 |
| `available`（别名 `search`） | 列出全部可配置的 provider，可加 QUERY 过滤 |
| `status` | 检查一个 provider 当前的凭据 |
| `use` | 设置一个 provider 用哪个账号（profile） |
| `discover` / `adopt` | 扫描外部来源的凭据 / 收编进凭据库 |
| `doctor` | 诊断凭据（过期、刷新、冷却、冲突） |
| `setup` | 交互式首次配置 |
| `aliases` | 列出 provider 短名别名 |
| `profiles` | 凭据 profile 管理 |
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
| `install` | 安装浏览器工具依赖（Playwright + Chromium、patchright/camoufox、agent-browser），可选一个目标或 `all` |
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
| `attach` / `detach` | 把频道用户的消息路由进某会话 / 取消别名 |
| `aliases` | 列出全部会话与频道用户的别名 |

### programs

| 动词 | 作用 |
|------|------|
| `run <name>` | 运行一个 program；`--arg key=value`（可重复）、`--provider`、`--model` |
| `list` | 列出保存的 program |
| `available` | 列出可安装的 program 与已装的第三方 harness |
| `install` / `uninstall` | 安装 / 卸载 program（gui/research/wiki/all）或第三方 harness（git URL / owner/repo） |

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

### memory —— 持久记忆

| 动词 | 作用 |
|------|------|
| `status` | 路径、条目数、上次 sleep 时间 |
| `recall` | 搜索 wiki + 近期 journal，打印原始片段 |
| `show` / `edit` | 打印 / 用 `$EDITOR` 编辑一个 wiki 页 |
| `sleep` | 立即跑一轮 sleep 整理（light → deep → REM） |
| `reflections` | 打印 `wiki/reflections.md` 最新条目 |
| `export` | 把整个记忆目录 tar+gzip 到指定路径 |

## 维护

| 命令 | 作用 | 关键参数 / 动词 |
|------|------|----------|
| `doctor` | 端到端健康检查 | `--json` 输出 JSON |
| `rescue` | 诊断问题并直接打印修复命令 | — |
| `logs` | 查看日志 | `list`；`tail [name]`（`-n` 行数、`-f` 跟踪）；`path [name]`。name 为 worker / runtime / ink，默认 worker |
| `update` | 检查并应用更新 | `--check` 只检查；`--force` 绕过 6 小时节流 |
