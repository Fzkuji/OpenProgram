# 快速上手

这页带你在五分钟内完成：安装、接入一个 LLM provider、打开界面、发出第一条消息，并装上第一个现成的 agent 程序。

## 第 1 步：安装

在 macOS 或 Linux 安装精确版本的 release wheel 和受控 Python runtime：

```bash
curl --proto '=https' --tlsv1.2 -LsSf \
  https://raw.githubusercontent.com/Fzkuji/OpenProgram/v0.6.1/scripts/install-release.sh \
  | OPENPROGRAM_VERSION=0.6.1 sh
```

installer 提供自己的 Python 并包含 Web UI；运行时不需要 Node.js 和 Git。macOS 桌面用户使用 GitHub Releases 中的 DMG。Linux 用户使用带 Web UI 或 TUI 的完整 CLI/server runtime；当前不发布 Linux 桌面包。平台范围和开发 checkout 安装见[安装](../install/install.zh.md)。

## 第 2 步：首次运行，接入 provider

```bash
openprogram
```

第一次运行会进入 setup 向导，引导你完成 provider 配置——从已登录的 Claude Code / Codex / Gemini CLI 导入凭据，或输入一个 API key——随后直接打开终端聊天界面。随时可以用 `openprogram setup` 重新运行向导。

也可以用环境变量跳过向导：

```bash
export ANTHROPIC_API_KEY=sk-ant-...     # Claude
export OPENAI_API_KEY=sk-...            # GPT
export GEMINI_API_KEY=...               # Gemini（GOOGLE_API_KEY 也可以）
```

确认检查：`openprogram providers` 会列出检测到的凭据。

## 第 3 步：打开 web 界面

```bash
openprogram web
```

它会启动后台 worker 并打开浏览器到 **http://localhost:18100**——web UI、API 和 WebSocket 共用这一个端口。改端口用 `openprogram ports --port <p>`。

## 第 4 步：发第一条消息

在终端聊天界面或 web 输入框里直接输入即可。想快速验证一条命令也行：

```bash
openprogram --print "用一句话介绍你自己"
```

它发送一条消息、打印回复、然后退出。之前的会话可以用 `openprogram --resume <session_id>` 续上，id 来自 `openprogram sessions list` 或 web 侧栏。

## 第 5 步：使用内置 agent Program

每个 release 都包含 GUI、Research 和 Wiki Program，无需单独安装，启动后会出现在 Web UI 和函数列表中。可用 `openprogram programs available` 查看注册状态。

Program installer 只用于第三方 Program 和开发者源码 overlay，不用于区分普通用户版本的功能范围。

## 下一步

- [模型与 provider](../models/README.md) — 各 provider 的接入方式、多账户与密钥轮换
- [Agentic Programming](../capabilities/agentic-programming/README.md) — 写你自己的 `@agentic_function`
- [界面](../interfaces/README.md) — 终端 TUI、web UI 与 channels
- [日常操作](daily-use.md) — 会话管理、分支与回退
