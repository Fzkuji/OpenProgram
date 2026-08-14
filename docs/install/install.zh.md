# 安装

OpenProgram 分别提供桌面 release 安装和 CLI/server release 安装。source checkout 安装只用于开发。

## 支持矩阵

| 平台 | 桌面 | CLI / Server | 浏览器客户端 |
|---|---|---|---|
| macOS arm64 / x64 | DMG | 支持 | 本地或远程 |
| Linux x86_64 | AppImage | 支持 | 本地或远程 |
| Linux arm64 | 无桌面产物 | 支持 | 本地或远程 |
| Windows | 不支持 | 不支持 | 可以连接受支持的远程主机 |
| iOS / Android / iPadOS | 无原生应用 | 不适用 | 可以连接受支持的远程主机；不承诺移动端布局 |

只有发布在 [GitHub Release](https://github.com/Fzkuji/OpenProgram/releases) 中的产物才属于 release 安装。CI artifact 和 source checkout 构建不属于 stable release。

## 桌面安装

桌面产物包含 Electron、受控 CPython runtime、OpenProgram Python 依赖和预构建 Web UI，运行时不读取系统 Python、Node.js 或 Git。

### macOS

1. 从 GitHub Releases 下载与机器架构对应的 DMG。
2. 用 release checksum 文件验证 SHA-256。
3. 打开 DMG，把 `OpenProgram.app` 复制到 `/Applications`。
4. 从 Applications 启动。正式发布的应用必须通过 Gatekeeper 验证。

### Linux x86_64

1. 从 GitHub Releases 下载 x86_64 AppImage 和 checksum 文件。
2. 验证 SHA-256。
3. 增加当前用户执行权限并启动：

```bash
chmod u+x OpenProgram-*-linux-x64.AppImage
./OpenProgram-*-linux-x64.AppImage
```

AppImage 不要求 root。目前不发布 Linux arm64 桌面产物。

## CLI 和服务器安装

release installer 支持 macOS 和 Linux。它在 `~/.openprogram/runtime/cli/releases/<version>` 下安装固定 uv、受控 CPython runtime 和一个精确版本的 OpenProgram wheel，不克隆仓库，也不构建 JavaScript。

installer 必须与安装版本使用同一个不可变 release tag：

```bash
curl --proto '=https' --tlsv1.2 -LsSf \
  https://raw.githubusercontent.com/Fzkuji/OpenProgram/v0.6.1/scripts/install-release.sh \
  | OPENPROGRAM_VERSION=0.6.1 sh
```

命令创建 `~/.local/bin/openprogram`。如果该目录不在 `PATH`，可以使用绝对路径，或把它加入 shell 配置。

验证安装：

```bash
~/.local/bin/openprogram --version
~/.local/bin/openprogram doctor
~/.local/bin/openprogram web
```

Web UI 地址是 `http://localhost:18100`。release wheel 已包含预构建 Web UI，不需要 Node.js。

## Programs 与可选组件

agent Program 不属于基础桌面或 CLI 产物。只有当对应 release 明确记录 Program environment 支持时，才使用 `openprogram programs install <name-or-git-source>`。当前 Program installer 会修改活动 Python 环境，因此尚未作为 immutable desktop package 内的受支持操作。

浏览器模型、GUI-agent 权重、OCR 数据和第三方 Program 可能需要单独下载。缺少这些可选内容不影响基础安装验收。

## 开发 checkout

贡献者使用 source checkout：

```bash
git clone https://github.com/Fzkuji/OpenProgram.git
cd OpenProgram
./scripts/install.sh
```

该开发 installer 可以安装工具链、使用 editable Python package，并通过 npm 构建 Web 和 Ink 界面。它不适用于普通用户，也不定义 `stable` channel。

## 数据与移除

配置、会话、日志、Program 和缓存位于 `~/.openprogram`；替换桌面应用或 CLI runtime 不会删除这些数据。

- 桌面：删除 `OpenProgram.app` 或下载的 AppImage。
- CLI runtime：删除 `~/.local/bin/openprogram` 和 `~/.openprogram/runtime/cli`。
- 只有在备份后显式 purge `~/.openprogram`，才会删除用户数据。

版本变更见[升级](upgrade.zh.md)，隔离状态目录见[Profiles](profiles.zh.md)。
