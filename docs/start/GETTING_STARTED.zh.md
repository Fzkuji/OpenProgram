# 快速上手

这页带你在五分钟内完成：安装、接入一个 LLM provider、打开界面、发出第一条消息，并装上第一个现成的 agent 程序。

## 第 1 步：安装

一条命令的安装脚本会装好 Python 包、网页 UI、终端 UI、浏览器工具和 channels：

```bash
git clone https://github.com/Fzkuji/OpenProgram.git && cd OpenProgram
./scripts/install.sh              # macOS/Linux   ·   Windows:  .\scripts\install.ps1
```

需要 Python ≥ 3.11、Node ≥ 20、git（缺了脚本会尽力代装）。脚本幂等，任何时候都可以重跑。agent 程序（GUI / Research / Wiki）默认不装，安装时的交互菜单里可挑选，也可以之后再补（见第 5 步）。完整参数与依赖矩阵见 [安装](../install/install.md)。

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

它会同时启动后端和 Next.js 前端，并打开浏览器到 **http://localhost:18100**（不是 :18109，那是后端 API 端口）。改端口用 `openprogram ports --backend <p> --frontend <p>`。

## 第 4 步：发第一条消息

在终端聊天界面或 web 输入框里直接输入即可。想快速验证一条命令也行：

```bash
openprogram --print "用一句话介绍你自己"
```

它发送一条消息、打印回复、然后退出。之前的会话可以用 `openprogram --resume <session_id>` 续上，id 来自 `openprogram sessions list` 或 web 侧栏。

## 第 5 步：装一个现成的 agent 程序

OpenProgram 是宿主，agent 程序装进来就能在 web UI 和函数列表里出现：

```bash
openprogram programs install research     # 或 wiki / gui
openprogram programs available            # 查看安装状态
```

`research` / `wiki` 是纯 Python，装得很快；`gui` 会下载 PyTorch 和模型权重，体积较大。装完后 `openprogram restart`（或在 Functions 页面点 Refresh），程序就会出现在界面里。

## 下一步

- [模型与 provider](../models/README.md) — 各 provider 的接入方式、多账户与密钥轮换
- [Agentic Programming](../capabilities/agentic-programming/README.md) — 写你自己的 `@agentic_function`
- [界面](../interfaces/README.md) — 终端 TUI、web UI 与 channels
- [日常操作](daily-use.md) — 会话管理、分支与回退
