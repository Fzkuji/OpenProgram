# 安装 OpenProgram

## 模型概念 —— 请先阅读

**OpenProgram 是宿主。你只需安装它一次，然后把 agent *程序*（programs）加进去。**

```
OpenProgram  (the host runtime — install this first, anywhere you like)
└── openprogram/functions/agentics/      ← programs live here, auto-discovered
    ├── GUI-Agent-Harness/               ← `gui_agent`      (clone in + run its installer)
    ├── Research-Agent-Harness/          ← `research_agent` (openprogram programs install research)
    └── Wiki-Agent-Harness/              ← `wiki_agent`     (openprogram programs install wiki)
```

放入 `functions/agentics/` 的程序会在启动时被**自动注册**
（`import_installed_programs()` 导入它的 `agentics` 子包，触发
`@agentic_function` 装饰器）—— 因此它会出现在 **web UI** 和函数
列表中，无需任何额外接线。所以安装顺序始终是：**先装 OpenProgram，
再装程序。**

> ⚠️ 只安装 Python 包**并不是**全部工作 —— 它不会
> 构建 web UI（需要 `npm`）、不会拉取 GUI agent 的模型权重，也不会预热
> OCR 模型。**下面的安装脚本才是权威来源** —— 它会
> 把这些都做好。

---

## 一条命令（推荐）

**macOS / Linux**
```bash
curl -fsSL https://raw.githubusercontent.com/Fzkuji/OpenProgram/main/scripts/install.sh | bash
# from a checkout: ./scripts/install.sh   # everything · bare host: --minimal
```

**Windows (PowerShell)**
```powershell
iwr -useb https://raw.githubusercontent.com/Fzkuji/OpenProgram/main/scripts/install.ps1 | iex
# from a checkout: .\scripts\install.ps1   # everything · bare host: -Minimal
```

不在 checkout 里运行时，脚本会先把仓库 clone 到 `~/OpenProgram`（`--target DIR` 可改），再接力安装。默认安装装好 host 的**全部轻量内容**：web UI（已构建）、终端 UI、浏览器工具 + channels。agent 程序（GUI / Research / Wiki）**不随默认安装** —— 有终端时脚本会弹菜单让你挑，或之后用 `openprogram programs install <research|wiki|gui>` 单独装（GUI 会下载 PyTorch），也可 `openprogram setup` → programs。`--minimal` 则改为安装一个裸宿主。

然后直接启动它 —— **首次运行会引导你完成 provider 配置**，随后
打开聊天界面：
```bash
openprogram                                   # first run = guided provider setup, then chat
openprogram web                               # or the browser UI -> http://localhost:18100
```

安装脚本是**幂等的** —— 任何时候都可以重新运行以修复或更新。

---

## 安装脚本做了什么

| 步骤 | 操作 | 说明 |
|------|--------|-------|
| 1 | 校验 / 安装 **Python 3.11+, Node 20+, git** | macOS `brew` / Linux `apt`·`dnf`·`pacman` / Windows `winget`。尽力而为。 |
| 2 | **Python 环境** | 若存在活动的 `venv`/conda 则使用，否则创建 `./.venv`。覆盖方式：`--python` / `-Python`。这就是那个“你想放哪儿就放哪儿”的位置。 |
| 3 | **OpenProgram** 可编辑安装（`pip install -e .`） | 宿主 + 基础依赖。 |
| 4 | **Web UI** —— 在 `web/` 中执行 `npm install && npm run build` | Next.js 前端运行在 **:18100**，后端运行在 **:18109**。`--minimal` 会跳过构建（worker 会在首次启动时构建）。 |
| 5 | **Ink TUI** —— 在 `cli/` 中执行 `npm install && npm run build` | 仅限 POSIX；Windows 使用 Rich REPL。`--minimal` 跳过。 |
| 6 | **agent 程序（可选，opt-in）** —— 有终端时弹菜单挑，或 `--programs <research\|wiki\|gui\|all>` | **默认不装任何程序。** 选中后：`research` / `wiki` 是纯 Python，克隆进 `functions/agentics/`（可编辑、自动注册；除 openprogram 外无其他依赖）；`gui` 会拉取 PyTorch（约 300 MB；无 GPU 的 Linux 自动选 CPU wheel，仅 CUDA 机器约 3 GB）。装完后随时可用 `openprogram programs install <name>` 再补。 |
| 7 | **浏览器工具 + channels** | `pip install -e .[all]` + `playwright install chromium`（约 150 MB）。`--minimal` 跳过。更重的 stealth 浏览器 / agent-browser 仍需主动开启 —— 见 [Extras](#extras)。 |

---

## 命令行参数

完整参数矩阵（`install.sh --help` / `install.ps1 -Yes` 也会打印）：

| 参数 (POSIX) | 参数 (Windows) | 控制什么 | 默认 |
|--------------|----------------|----------|------|
| `--minimal` | `-Minimal` | 裸宿主：跳过 web 构建 / TUI / 程序 / extras | 关（装全部轻量内容） |
| `--python /path/python` | `-Python C:\path\python.exe` | 指定 Python 解释器 | 自动探测（活动 venv/conda，否则建 `./.venv`） |
| `--stealth` | `-Stealth` | 额外装 stealth 浏览器（patchright + camoufox，约 350 MB） | 关 |
| `--agent-browser` | `-AgentBrowser` | 额外装全局 npm `agent-browser`（约 150 MB） | 关 |
| `--programs <gui\|research\|wiki\|all>` | `-Programs <…>` | 安装时非交互地一并装 agent 程序（可重复或逗号分隔） | 无（首次运行向导里再选） |
| `--target DIR` | `-Target DIR` | 从网页运行时 clone 到哪里 | `~/OpenProgram`（Win：`$HOME\OpenProgram`） |
| `--yes` / `-y` | `-Yes` | 跳过所有提示、全部取默认值 | 关（有终端时弹菜单） |

为 GUI harness 显式指定 CUDA/CPU 版 PyTorch：在宿主安装完成后运行它
自己的安装脚本 —— `openprogram/functions/agentics/GUI-Agent-Harness/scripts/install.sh --cuda cu124`。

### 非交互 / AI agent 安装

给 agent 驱动安装用 —— **不必特意加参数**：那条 `curl … | bash` 一行命令本身就能无人值守跑。没有终端（管道、CI）时它自动取默认值；即便有终端，每个 `/dev/tty` 读取也有 60 秒超时，到点自动回落到默认值（并打印一行 `(no input in 60s — using default)`）—— 所以**任何提示都不会永久卡住**。用 `OPENPROGRAM_PROMPT_TIMEOUT=<秒>` 可改超时时长。

想立即取默认值、不等超时，就加 `--yes` / `-y`；想顺带非交互地装上 agent 程序，再加 `--programs all`（或 `gui` / `research` / `wiki`）。以下**环境变量**与 `--yes` 等价 —— 命中任意一个就全部取默认值、不弹任何提示：

| 环境变量 | 生效条件 |
|----------|----------|
| `CI` | 非空（GitHub Actions 等 CI 通用约定） |
| `DEBIAN_FRONTEND` | 等于 `noninteractive`（Debian/Ubuntu 通用约定） |
| `OPENPROGRAM_INSTALL_YES` | 非空（本项目自带的开关） |

一条命令即可完全非交互、并顺带装上 agent 程序：

```bash
curl -fsSL https://raw.githubusercontent.com/Fzkuji/OpenProgram/main/scripts/install.sh | bash -s -- -y --programs all
```

> Windows 的 `Read-Host` 没有超时机制，所以 `install.ps1` 的提示**不会**自动
> 取默认值 —— agent 在 Windows 上必须传 `-Yes` 或设上表任一环境变量。

---

## 添加 agent 程序

程序总是落在 `functions/agentics/<Repo>/`，并在下次
启动时自动注册。**通用**方式 —— 既适用于已编目的 harness，*也适用于你自己的* ——
就是把一个 repo 克隆进该文件夹，然后运行它的安装脚本：

```bash
cd openprogram/functions/agentics
git clone <harness-repo>
cd <Harness>
./scripts/install.sh          # if it ships one (Windows: .\scripts\install.ps1)
```

**GUI agent** 有原生依赖（PyTorch、检测器权重、OCR），因此它附带了
自己的分平台安装脚本 —— 按上面的步骤使用它；完整指南见它的
[安装章节](../openprogram/functions/agentics/GUI-Agent-Harness#1-install)。
（选装了 GUI 时 —— 菜单里勾选或 `--programs gui`/`all` —— 安装脚本会把它克隆进来并拉 PyTorch；之后运行该 harness 自己的安装脚本来配资产或指定 CUDA/CPU torch。）

对于**纯 Python** 的已编目 harness，有一条单行快捷命令，会为你完成克隆、
安装并注册：
```bash
openprogram programs install research     # or: wiki / all
openprogram programs available            # see install status
```
`programs install` 执行的是**非可编辑**安装（依赖装到 site-packages，代码在
源码树内运行）；它**不会**拉取像 GUI 的 YOLO 权重或 OCR 这类原生资产 —— 所以
GUI agent 改用它自己的安装脚本（见上文）。

执行上述任意操作后，重启 worker（或在 Functions 页面点击 **Refresh**），
该程序就会出现在 web UI 中。第三方 harness 的方式相同：
[installing-harnesses.md](installing-harnesses.md)。

---

## Extras

**浏览器工具 + 聊天 channels 默认安装**（即 `[all]` extra），且
安装脚本会为你拉取 Playwright Chromium 二进制 —— 无需任何主动开启。
传入 `--minimal` / `-Minimal` 以跳过它们（例如 CI / 隔离网络 / 带宽受限场景）。

| 默认 extra | 安装 | 安装后（自动化） | 大小 |
|---------------|----------|--------------------------|------|
| browser (`[browser]`) | `playwright` | `playwright install chromium` | 约 150 MB |
| channels (`[channels]`) | `discord.py`, `slack_sdk`, `qrcode` | *(在 `~/.openprogram/config.json` 中设置 token)* | 较小 |

更重、仍需主动开启（加上对应参数）：

| 参数 / extra | 安装 | 安装后（自动化） | 大小 |
|--------------|----------|--------------------------|------|
| `--stealth` · `[browser-stealth]` | `patchright`, `camoufox` | `patchright install chromium`, `camoufox fetch` | 约 350 MB |
| `--agent-browser` · `[agent-browser]` | 全局 npm `agent-browser` | `agent-browser install` | 约 150 MB |

Provider SDK（`anthropic`、`openai`、`google-genai`）已包含在基础安装中 ——
无需额外 extra。

---

## Providers / 凭据

进行任何聊天回合前，至少需要一个 provider：
```bash
openprogram providers login openai-codex      # ChatGPT subscription (recommended)
openprogram providers login anthropic          # Claude
export ANTHROPIC_API_KEY=sk-ant-...             # …or an API key (Windows: $env:ANTHROPIC_API_KEY="...")
```
会自动采用已安装的 Claude Code / Codex / Gemini CLI。用 `openprogram doctor` 检查。

---

## 端口

| 端口 | 服务 | 说明 |
|------|---------|-------|
| **18100** | Next.js **前端** —— 打开这个 | `http://localhost:18100` |
| **18109** | FastAPI **后端**（API + WebSocket） | 由前端代理；无 HTML 页面 |

使用 `openprogram ports --backend <p> --frontend <p>` 修改。

---

## 完整依赖矩阵

`pip` 之外的全部内容。安装脚本会处理每一行标记为 “auto” 的项。

### 宿主（OpenProgram）

| 项目 | 用于 | 方式 | 平台 | 自动？ |
|------|--------------|-----|----------|-------|
| Python ≥ 3.11 | 所有功能 | system / pyenv / conda | 全部 | 校验 |
| Node.js ≥ 20 + npm | web UI、TUI | nodejs.org / 包管理器 | 全部 | 安装 |
| git | 会话即 git 仓库 | 包管理器 | 全部 | 安装 |
| `web/node_modules` | web UI (:18100) | 在 `web/` 中执行 `npm install` | 全部 | **auto** |
| `cli/` Ink bundle | TUI | 在 `cli/` 中执行 `npm install && npm run build` | macOS/Linux | **auto** |
| provider 凭据 | 任何聊天回合 | `openprogram providers login`（或设置界面） | 全部 | 手动 |
| Playwright / patchright / camoufox / agent-browser | 浏览器工具 | 上面的参数 | 全部 | 参数 |

### GUI-Agent-Harness 程序（opt-in，选装后 —— 见 [添加 agent 程序](#adding-agent-programs)）

| 项目 | 用于 | 方式 | 平台 | 自动？ |
|------|--------------|-----|----------|-------|
| PyTorch（+ torchvision） | YOLO / OCR | pip 解析默认构建；该 harness 自己的安装脚本会自动检测 NVIDIA GPU → CUDA（用 `--cpu` / `--cuda cuXXX` 强制） | 全部 | **auto** |
| harness Python 依赖 | 核心 | `pip install -e .[ocr]`（ultralytics、opencv、pynput、easyocr） | 全部 | **auto** |
| **GPA YOLO 权重** `model.pt` | 元素检测 | `Salesforce/GPA-GUI-Detector` → `~/GPA-GUI-Detector/model.pt` | 全部 | **auto** |
| EasyOCR 模型（en + ch_sim） | 文本检测 | 预热（`~/.EasyOCR/model`，约 300 MB） | Win/Linux | **auto** |
| `xclip`（+ wmctrl/xdotool/scrot） | 剪贴板、窗口 | `apt install …` | Linux | **auto** |
| Xcode CLT（Swift） | Apple Vision OCR | `xcode-select --install` | macOS | 尽力而为* |
| 屏幕录制 + 辅助功能 | 截图、点击 | 系统设置 → 隐私 | macOS | 手动 |
| Win32 + PowerShell 剪贴板 | 所有功能 | 内置 | Windows | 不适用 |

\* EasyOCR 作为跨平台回退方案被安装，所以 GUI agent 在
没有 Xcode CLT 的 macOS 上也能工作 —— Apple Vision 只是更快而已。完整的 GUI 细节：
[GUI-Agent-Harness/docs/install.md](../openprogram/functions/agentics/GUI-Agent-Harness/docs/install.md)。

---

## 故障排查

- **`openprogram web` 显示了一个加载不出来的页面 / 只有后端起来了。**
  Next.js 的 `node_modules` 没有安装。重新运行安装脚本，然后打开
  **http://localhost:18100**（不是 :18109）。
- **`pip` 无法重装：`WinError 32 … openprogram.exe is being used`。**
  先停掉正在运行的 `openprogram web` / worker，然后重新运行。
- **`gui_agent` 没有出现在 UI 中。** 重启 worker（或在 Functions 页面点
  Refresh）。用 `openprogram programs available` 确认它已注册。
- **NVIDIA GPU 未被使用。** 安装脚本会自动检测它；如果它选了 CPU（安装时没有驱动，或你传了 `--cpu`）：执行 `pip uninstall -y torch torchvision`，然后重新运行安装脚本。
- **GPA 权重没有下载下来**（离线）：`hf download Salesforce/GPA-GUI-Detector model.pt --local-dir ~/GPA-GUI-Detector`。

---

## 手动 / 进阶

```bash
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e .                                  # host
( cd web && npm install )                         # web UI
( cd cli && npm install && npm run build )         # TUI (POSIX)
# GUI program (editable, in-tree → auto-registers):
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
pip install -e "openprogram/functions/agentics/GUI-Agent-Harness[ocr]"
hf download Salesforce/GPA-GUI-Detector model.pt --local-dir ~/GPA-GUI-Detector
python -c "import easyocr; easyocr.Reader(['en','ch_sim'], gpu=False)"
```

多 repo 本地开发（并排编辑多个 harness）：
[troubleshooting.md → Local-development install](troubleshooting.md#local-development-install-multi-repo)。
