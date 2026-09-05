# 终端 TUI

工具审批行为及运行中切换请参见[工具权限模式](../capabilities/permissions.zh.md)。

不离开终端使用 OpenProgram 的完整聊天界面。本页覆盖进入退出、按键和斜杠命令。

![终端 TUI](../images/tui_hero.png)

## 进入与退出

```bash
openprogram tui      # 直接进入终端聊天（别名：openprogram chat）
openprogram          # 裸命令会先询问：进终端 UI 还是 Web UI
```

release CLI 始终包含 Python 终端界面。macOS 和 Linux 的 source development 安装还可以构建 Node.js Ink 界面。两者都通过 WebSocket 连接同一个本地 worker，并与 Web UI 共用会话。Windows release 安装不受支持。

退出：`/quit`，或空闲时快速按两次 `Ctrl-C`。

续聊历史会话：TUI 内用 `/resume` 挑选；会话 id 可用 `openprogram sessions list` 查。（`openprogram --resume <id>` 参数目前只对 `--print` 单发生效，启动交互式 TUI 时不生效。）

## 按键

| 按键 | 作用 |
|---|---|
| `Enter` | 发送 |
| `Alt+Enter` | 换行 |
| `Esc` | 清空输入行；生成中则中止本轮 |
| `Ctrl-C`（生成中） | 三段式停止：第一次提示、第二次优雅停止、第三次强制停止 |
| `Ctrl-C` 双击（空闲） | 退出 |
| `↑` / `↓` | 历史输入回溯；补全菜单打开时上下选择 |
| `Tab` | 接受文件 / 斜杠命令补全 |
| `→`（行尾）或 `Ctrl+E` | 接受自动补全建议 |
| `Ctrl+R` | 搜索已保存上下文 |
| `Shift+Tab` | 循环切换权限档（ask → acceptEdits → plan → auto） |
| `Ctrl+K` | 命令面板（覆盖全部斜杠命令） |
| `PageUp` / `PageDown`、`Ctrl+U` / `Ctrl+D` | 回滚翻页 / 半页 |
| `Home` / `End` | 跳到最上 / 最下 |

## 斜杠命令

输入 `/` 触发补全。常用：

| 命令 | 作用 |
|---|---|
| `/help` | 命令列表 |
| `/model`、`/fetch-models` | 切换模型、重新拉取模型列表 |
| `/effort` | 调整 thinking effort（档位见 [thinking effort](../models/thinking-effort.md)） |
| `/new`、`/resume`、`/sessions`、`/session` | 新会话、续聊、会话列表、当前会话信息 |
| `/rewind` | 回退会话到某条消息 |
| `/compact`、`/context`、`/clear` | 压缩上下文、查看上下文、清屏 |
| `/permissions`、`/sandbox` | 权限档与沙箱 |
| `/login <provider>`、`/logout` | provider 登录 / 登出（见[认证与凭据](../models/auth.md)） |
| `/agents`、`/agent` | 管理 / 切换 agent |
| `/mcp`、`/tools`、`/memory` | 与 Web UI 对应页面同源的数据 |
| `/cost` | 本会话 token 用量 |
| `/export`、`/copy` | 导出会话、复制回复 |
| `/config`、`/theme`、`/bell` | 设置、主题、提示音 |
| `/doctor` | 健康检查 |
| `/channel`、`/attach`、`/detach`、`/connections` | 聊天渠道接入与会话路由 |
| `/quit` | 退出 |

另有 `/search`、`/review`、`/diff`、`/init`、`/browser`、`/welcome`。完整清单以 `/help` 输出为准。

除这些内置命令外，补全菜单还会列出统一命令注册表里的全部命令——skill、MCP prompt、插件命令，以及你放在 `~/.openprogram/commands/` 或 `<项目>/.openprogram/commands/` 下的自定义命令文件（markdown，可带 YAML frontmatter）。执行时命令正文展开后作为消息发送，与 Web composer 完全一致：TUI 和 Web UI 读同一张注册表，命令定义一次两边都有。
