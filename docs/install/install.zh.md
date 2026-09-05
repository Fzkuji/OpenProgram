# 安装

OpenProgram 分别提供桌面 release 安装和 CLI/server release 安装。所有受支持的 release 安装都包含相同的 product runtime，只有启动外壳不同。可选 backend 依赖的边界见下文。source checkout 安装只用于开发。

## 支持矩阵

| 平台 | 桌面 | CLI / Server | 浏览器客户端 |
|---|---|---|---|
| macOS arm64 / x64 | DMG | 支持 | 本地或远程 |
| Linux x86_64 | 不发布桌面产物 | 支持 | 本地或远程 |
| Linux arm64 | 不发布桌面产物 | 支持 | 本地或远程 |
| Windows x86_64 / arm64 | release 附带时提供带签名 EXE | 支持 | 本地或远程 |
| iOS / Android / iPadOS | 无原生应用 | 不适用 | 可以连接受支持的远程主机；不承诺移动端布局 |

只有发布在 [GitHub Release](https://github.com/Fzkuji/OpenProgram/releases) 中的产物才属于 release 安装。CI artifact 和 source checkout 构建不属于 stable release。

## 桌面安装

受支持的 macOS 和 Windows 桌面产物包含 Electron 和平台 product runtime。runtime 内含受控 CPython、OpenProgram、预构建 Web UI、providers、channels、search、Playwright Chromium、GPA detector 权重，以及 GUI、Research、Wiki 三项第一方 Programs。GUI Program 已注册，但 product runtime 明确不含 PyTorch、OpenCV 和 EasyOCR；需要这些依赖的 GUI perception 路径必须单独配置 backend 或 development overlay。runtime 不读取系统 Python 或 Node.js。Session 与 Memory 历史需要 Git，`openprogram doctor` 会检查 Git。Linux 当前使用同一套 CLI/server runtime，因为 AppImage 未通过打包门禁；不发布精简的 Linux 桌面产物。

### macOS

1. 从 GitHub Releases 下载与机器架构对应且文件名包含 `unsigned` 的 DMG。
2. 用 release checksum 文件验证 SHA-256。
3. 打开 DMG，把 `OpenProgram.app` 复制到 `/Applications`。
4. 从 Applications 启动。当前 release 没有使用 Apple Developer ID 签名，macOS 可能阻止首次启动。打开“系统设置 → 隐私与安全性”，找到 OpenProgram 提示并选择“仍要打开”。checksum 只验证下载内容，不能说明该应用已经通过 Apple 验证。

### Windows

1. 在 GitHub Releases 中确认该 release 包含适配本机的 `OpenProgram-<version>-win-x64.exe` 或 `OpenProgram-<version>-win-arm64.exe`。如果没有，请使用下文受支持的 CLI/server 安装；未签名 CI artifact 或 source build 不是 release installer。
2. 下载 EXE，并根据 `SHA256SUMS-win-x86_64` 或 `SHA256SUMS-win-arm64` 验证 SHA-256。
3. 运行前打开文件属性，确认“数字签名”显示有效签名。
4. 运行按用户安装的向导。可以选择安装目录，不要求管理员账户。

Windows App 使用内置 runtime，Terminal Pane 使用 Windows PowerShell/ConPTY，Browser、Files、聊天和多窗口界面与 macOS 保持一致。Windows Desktop 更新会先验证 release metadata、文件长度、SHA-256 和 Authenticode，再打开下一版 installer。

## CLI 和服务器安装

release installer 支持 macOS、Linux 和 Windows x86_64/arm64。它下载完整的平台 runtime archive，验证 SHA-256 和 capability manifest，再安装到 `~/.openprogram/runtime/cli/releases/<version>`。在 macOS 上，同一 archive 也作为 Desktop 构建输入。它不在用户机器上解析产品依赖、克隆仓库或构建 JavaScript。

安装最新 stable release：

```bash
curl -fsSL https://openprogram.io/install | sh
```

短 bootstrap 先解析最新 stable GitHub Release，再执行该不可变 tag 下的 installer。需要可复现地安装指定版本时，把版本传给 shell 进程：

```bash
curl -fsSL https://openprogram.io/install | OPENPROGRAM_VERSION=0.8.1 sh
```

命令创建 `~/.local/bin/openprogram`。如果该目录不在 `PATH`，可以使用绝对路径，或把它加入 shell 配置。

发布的 Linux runtime 是面向 glibc 系统的原生 CLI/server 包，不是 Desktop
安装包。x86_64 与 arm64 都会在干净的 Ubuntu 22.04（glibc 2.35）镜像中完成消费
验证；更新的 glibc 发行版预期可用，Alpine 等 musl 系统不属于 release 目标。主机需要
`curl`、`tar`、SHA-256 工具、Git，以及 Chromium 使用的标准共享库。若 Browser/runtime
探测失败，installer 会在切换活动版本前明确报错。无需系统 Python、Node.js 或 npm。

Windows 使用 PowerShell bootstrap：

```powershell
irm https://openprogram.io/install.ps1 | iex
```

指定不可变 release：

```powershell
$env:OPENPROGRAM_VERSION = "X.Y.Z"
irm https://openprogram.io/install.ps1 | iex
```

Windows installer 下载带 SHA-256 校验的 release ZIP，在解压前逐项验证 archive
路径，验证完整 runtime，并在激活前执行 worker cold-start。版本化 runtime 安装到
`%USERPROFILE%\.openprogram\runtime\cli\releases`，launcher 创建在
`%LOCALAPPDATA%\OpenProgram\bin\openprogram.cmd`。Installer 会把 launcher 目录加入
用户 `PATH`；当前终端未识别时，请打开新的终端。

Release 和源码 installer 都提供 `openprogram.ps1` 与 `openprogram.cmd`，
无需修改系统语言即可支持中文等 Unicode 安装路径。PowerShell 使用脚本 launcher
保留原始参数；CMD 使用 batch launcher 和 CMD 的常规引号规则。Installer 不修改
脚本执行策略：如果策略阻止 PowerShell launcher，可以显式执行 `openprogram.cmd`。
Batch launcher 会恢复原控制台代码页，并保留命令的退出码。

installer 在切换 `current` 前会验证架构匹配的 manifest 和内置 Ink TUI，再使用操作系统分配的 loopback 端口执行 worker cold-start/health check。安装事务由每用户锁串行化，文件在不可变 release 目录之外暂存，所有检查通过后才原子切换 `current`；探测失败时旧版本仍保持选中。安装后可以执行：

```bash
~/.local/bin/openprogram --version
~/.local/bin/openprogram web
~/.local/bin/openprogram doctor
```

Web UI 地址是 `http://localhost:18100`。runtime 已包含预构建 Web UI，不需要 Node.js。激活前，installer 会验证 Web、providers、MCP、memory、channels、search、Chromium、GPA detector 权重，以及三项第一方 Programs 的注册和导入。`doctor` 仍可能报告尚未配置 provider credential 等用户配置问题。

## 已包含产品能力与额外扩展

GUI Agent、Research Agent 和 Wiki Agent 属于每个受支持的 release 安装。其 Program package、GPA detector 权重和 Playwright Chromium 已包含。GUI Program 使用不解析依赖的方式安装：product runtime 不含 PyTorch、OpenCV 和 EasyOCR，因此使用这些依赖的 GUI perception 路径需要单独配置 backend 或 development overlay。

第三方 Program 是用户主动增加的额外功能，存放在只读 product runtime 之外。第一方 Program editable source、诊断工具、本地前端构建，以及 OCR/Browser 后端替换属于开发者附加能力。只有使用 product runtime 未包含依赖的 GUI perception 路径才需要替换 backend 或补充依赖；这些附加项不会改变基础 runtime manifest。

## 开发 checkout

贡献者使用 source checkout：

```bash
git clone https://github.com/Fzkuji/OpenProgram.git
cd OpenProgram
./scripts/install.sh
```

Windows 使用 PowerShell：

```powershell
git clone https://github.com/Fzkuji/OpenProgram.git
Set-Location OpenProgram
.\scripts\install.ps1 -Yes
```

Windows 开发 installer 会创建隔离的 `.venv`，按 npm lockfile 安装 frontend
workspaces，构建浏览器 UI 与完整 Ink 终端 UI，并安装可选的 Browser 和 Channel 依赖。它还会创建
`%LOCALAPPDATA%\OpenProgram\bin` 下的 `openprogram.cmd` 与 `openprogram.ps1`，并把该目录加入用户 `PATH`，
因此新的 PowerShell 无需激活 checkout 环境就能运行 `openprogram`。经过验证的
版本是 Node.js 22 LTS。`-Minimal` 只安装 Python CLI/server，不安装或构建 frontend
依赖。

该开发 installer 安装 editable CLI/server source 与浏览器 UI。Windows Desktop
开发包由 Desktop workspace 的 `dist:win` 命令单独封装，不由 `scripts/install.ps1`
安装。Release 与开发安装都包含完整 Ink 终端 UI，并以 Python Rich 界面作为能力回退。Windows 沙箱是可选能力：缺少带 bubblewrap 的 WSL2
时，`auto` 让原生命令继续可用；显式 `workspace-write` 则要求该后端。Windows 开发
Source build 不适用于普通用户，也不定义 `stable` channel。

## 数据与移除

配置、会话、日志、Program 和缓存位于 `~/.openprogram`；替换桌面应用或 CLI runtime 不会删除这些数据。

- macOS Desktop：删除 `OpenProgram.app`。
- macOS/Linux CLI runtime：删除 `~/.local/bin/openprogram` 和 `~/.openprogram/runtime/cli`。
- Windows CLI runtime：删除 `%LOCALAPPDATA%\OpenProgram\bin` 和 `%USERPROFILE%\.openprogram\runtime\cli`。
- Windows Desktop：从“已安装的应用”卸载 OpenProgram；除非显式 purge，否则保留用户状态。
- 只有在备份后显式 purge `~/.openprogram`，才会删除用户数据。

版本变更见[升级](upgrade.zh.md)，隔离状态目录见[Profiles](profiles.zh.md)。
