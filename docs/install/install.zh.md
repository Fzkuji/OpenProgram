# 安装

OpenProgram 分别提供桌面 release 安装和 CLI/server release 安装。所有受支持的 release 安装都包含相同的 product runtime，只有启动外壳不同。可选 backend 依赖的边界见下文。source checkout 安装只用于开发。

## 支持矩阵

| 平台 | 桌面 | CLI / Server | 浏览器客户端 |
|---|---|---|---|
| macOS arm64 / x64 | DMG | 支持 | 本地或远程 |
| Linux x86_64 | 不发布桌面产物 | 支持 | 本地或远程 |
| Linux arm64 | 不发布桌面产物 | 支持 | 本地或远程 |
| Windows | 暂缓到后续 release 决策 | 暂缓到后续 release 决策 | 可以连接受支持的远程主机 |
| iOS / Android / iPadOS | 无原生应用 | 不适用 | 可以连接受支持的远程主机；不承诺移动端布局 |

只有发布在 [GitHub Release](https://github.com/Fzkuji/OpenProgram/releases) 中的产物才属于 release 安装。CI artifact 和 source checkout 构建不属于 stable release。

## 桌面安装

受支持的 macOS 桌面产物包含 Electron 和平台 product runtime。runtime 内含受控 CPython、OpenProgram、预构建 Web UI、providers、channels、search、Playwright Chromium、GPA detector 权重，以及 GUI、Research、Wiki 三项第一方 Programs。GUI Program 已注册，但 product runtime 明确不含 PyTorch、OpenCV 和 EasyOCR；需要这些依赖的 GUI perception 路径必须单独配置 backend 或 development overlay。runtime 不读取系统 Python 或 Node.js。Session 与 Memory 历史需要 Git，`openprogram doctor` 会检查 Git。Linux 当前使用同一套 CLI/server runtime，因为 AppImage 未通过打包门禁；不发布精简的 Linux 桌面产物。

### macOS

1. 从 GitHub Releases 下载与机器架构对应且文件名包含 `unsigned` 的 DMG。
2. 用 release checksum 文件验证 SHA-256。
3. 打开 DMG，把 `OpenProgram.app` 复制到 `/Applications`。
4. 从 Applications 启动。当前 release 没有使用 Apple Developer ID 签名，macOS 可能阻止首次启动。打开“系统设置 → 隐私与安全性”，找到 OpenProgram 提示并选择“仍要打开”。checksum 只验证下载内容，不能说明该应用已经通过 Apple 验证。

## CLI 和服务器安装

release installer 支持 macOS 和 Linux。它下载完整的平台 runtime archive，验证 SHA-256 和 capability manifest，再安装到 `~/.openprogram/runtime/cli/releases/<version>`。在 macOS 上，同一 archive 也作为 Desktop 构建输入。它不在用户机器上解析产品依赖、克隆仓库或构建 JavaScript。

安装最新 stable release：

```bash
curl -fsSL https://openprogram.io/install | sh
```

短 bootstrap 先解析最新 stable GitHub Release，再执行该不可变 tag 下的 installer。需要可复现地安装指定版本时，把版本传给 shell 进程：

```bash
curl -fsSL https://openprogram.io/install | OPENPROGRAM_VERSION=0.8.1 sh
```

命令创建 `~/.local/bin/openprogram`。如果该目录不在 `PATH`，可以使用绝对路径，或把它加入 shell 配置。

installer 在切换 `current` 前会自动执行版本检查和 worker cold-start/health check；失败时不会切换当前版本。安装后可以执行：

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

该开发 installer 先安装同一套产品能力，再增加工具链、editable source、测试、诊断、本地 Web/Ink 构建和后端替换选项。它不适用于普通用户，也不定义 `stable` channel。

## 数据与移除

配置、会话、日志、Program 和缓存位于 `~/.openprogram`；替换桌面应用或 CLI runtime 不会删除这些数据。

- macOS Desktop：删除 `OpenProgram.app`。
- CLI runtime：删除 `~/.local/bin/openprogram` 和 `~/.openprogram/runtime/cli`。
- 只有在备份后显式 purge `~/.openprogram`，才会删除用户数据。

版本变更见[升级](upgrade.zh.md)，隔离状态目录见[Profiles](profiles.zh.md)。
