# 终端 TUI

工具审批行为及运行中切换请参见[工具权限模式](../capabilities/permissions.zh.md)。

不离开终端使用 OpenProgram 的完整聊天界面。本页覆盖进入退出、按键和斜杠命令。

![终端 TUI](../images/tui_hero.png)

## 进入与退出

```bash
openprogram tui      # 直接进入终端聊天（别名：openprogram chat）
openprogram          # 裸命令会先询问：进终端 UI 还是 Web UI
openprogram tui --no-alt-screen  # 行内模式，保留终端滚屏
openprogram tui --screen-reader  # 无鼠标追踪的行内无障碍模式
```

Windows、macOS 和 Linux 的 release CLI 都自带独立的 Node.js Ink 界面。启动时检测终端实际是否支持 raw input，而不是按操作系统一刀切：Windows Terminal、ConPTY 下的 PowerShell 和现代 IDE 集成终端会进入完整全屏界面；终端无法提供 raw input 时，会给出明确提示并回退到内置 Python Rich 界面。两种实现都通过 WebSocket 连接同一个本地 worker，并与 Web UI 共用会话。

全屏界面要求 stdin 和 stdout 都连接终端。重定向或管道调用会在写出任何 ANSI frame 前跳过 Ink，进入行式 fallback；自动化调用请使用 `openprogram --print "..."`。Alternate screen 不再清除 shell 主屏内容，退出时会原样恢复。光标移动和删除以完整 Unicode grapheme cluster 为单位，因此编辑 emoji、组合字符和中日韩文字时不会把字符拆开。

`/copy` 在 Wayland 会话使用 `wl-copy`，在 X11 会话使用 `xclip` 或 `xsel`，在 WSL 中使用 `clip.exe`。通过 SSH 使用时不会误写远端图形剪贴板，而改用 OSC 52；在 tmux 中还会使用 passthrough 与 paste buffer。原生剪贴板命令带有超时，失效的 display 不会卡死 TUI。

Windows 推荐使用 Windows Terminal。运行在 MinTTY 中的 Git Bash 可能回退到 Rich；Windows Terminal 内的 PowerShell 可使用完整界面。Release 已携带 Node.js executable，用户不需要为 TUI 另装 Node.js。

退出：`/quit`，或空闲时快速按两次 `Ctrl-C`。

续聊历史会话：TUI 内用 `/resume` 挑选，也可以直接运行 `openprogram --resume <id>`。会话 id 可用 `openprogram sessions list` 查。

使用 `--profile <名称>` 时，启动诊断写入该 profile 的状态目录（`~/.openprogram-<名称>/logs/ink-startup.log`），不会混入默认 profile。

## 按键

| 按键 | 作用 |
|---|---|
| `Enter` | 发送 |
| 空输入时按 `?` | 打开键盘快捷键参考 |
| `Alt+Enter` | 换行 |
| `Esc` | 清空输入行；生成中则中止本轮 |
| `Ctrl-C`（生成中） | 三段式停止：第一次提示、第二次优雅停止、第三次强制停止 |
| `Ctrl-C` 双击（空闲） | 退出 |
| `↑` / `↓` | 历史输入回溯；补全菜单打开时上下选择 |
| `Tab` | 接受文件 / 斜杠命令补全 |
| `→`（行尾）或 `Ctrl+E` | 接受自动补全建议 |
| `Ctrl+A` / `Ctrl+E` | 没有待接受建议时移到输入开头 / 结尾 |
| `Ctrl+W` | 删除前一个单词 |
| `Ctrl+R` | 搜索已保存上下文 |
| `Shift+Tab` | 循环切换权限档（ask → acceptEdits → plan → auto） |
| `Ctrl+K` | 命令面板（覆盖全部斜杠命令） |
| `PageUp` / `PageDown`、`Ctrl+U` / `Ctrl+D` | 回滚翻页 / 半页 |
| `Home` / `End` | 跳到最上 / 最下 |

## 斜杠命令

输入 `/` 触发补全。常用：

| 命令 | 作用 |
|---|---|
| `/help`、`/keybindings` | 命令列表与键盘快捷键参考 |
| `/model`、`/fetch-models` | 切换模型、重新拉取模型列表 |
| `/effort` | 调整 thinking effort（档位见 [thinking effort](../models/thinking-effort.md)） |
| `/new`、`/resume`、`/sessions`、`/session` | 新会话、续聊、会话列表、当前会话信息 |
| `/rewind` | 回退会话到某条消息 |
| `/compact`、`/context`、`/clear` | 压缩上下文、查看上下文、清屏 |
| `/permissions`、`/sandbox` | 权限档与沙箱 |
| `/login <provider>`、`/logout` | provider 登录 / 登出（见[认证与凭据](../models/auth.md)） |
| `/agents`、`/agent` | 管理 / 切换 agent |
| `/mcp`、`/tools`、`/memory` | 查看和管理与 Web UI 对应页面同源的数据 |
| `/cost` | 本会话 token 用量 |
| `/export`、`/copy` | 导出会话、复制回复 |
| `/config`、`/theme`、`/bell` | 设置、主题、提示音 |
| `/doctor` | 健康检查 |
| `/channel`、`/attach`、`/detach`、`/connections` | 聊天渠道接入与会话路由 |
| `/quit` | 退出 |

另有 `/search`、`/review`、`/diff`、`/init`、`/browser`、`/welcome`。完整清单以 `/help` 输出为准。

除这些内置命令外，补全菜单还会列出统一命令注册表里的全部命令——skill、MCP prompt、插件命令，以及你放在 `~/.openprogram/commands/` 或 `<项目>/.openprogram/commands/` 下的自定义命令文件（markdown，可带 YAML frontmatter）。执行时命令正文展开后作为消息发送，与 Web composer 完全一致：TUI 和 Web UI 读同一张注册表，命令定义一次两边都有。
