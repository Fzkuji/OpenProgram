# 升级

升级行为取决于安装类型。stable 安装只在已发布版本之间变更，不跟随 `origin/main`。

## 桌面 release

签名后的跨版本更新验收通过前，不启用桌面自动更新。当前从 GitHub Releases 手动升级：

- macOS：下载新的 notarized DMG，替换 `OpenProgram.app`。
- Linux：下载新的 AppImage，验证 SHA-256，增加执行权限并替换旧文件。

应用代码和内置 Python 一起替换；`~/.openprogram` 下的状态保持不变。

## CLI 和服务器 release

使用目标版本的不可变 tag，并设置相同的 package version：

```bash
curl --proto '=https' --tlsv1.2 -LsSf \
  https://raw.githubusercontent.com/Fzkuji/OpenProgram/v0.6.1/scripts/install-release.sh \
  | OPENPROGRAM_VERSION=0.6.1 sh
```

installer 创建新的版本目录，安装并 probe 精确 wheel，随后切换 `current` symlink。切换前失败时，旧版本仍保持选中状态。

升级后重启登录服务：

```bash
openprogram worker restart
```

## 开发 checkout

`openprogram upgrade` 只用于 source checkout。它验证 Git 目标，仅在相关源文件变化时更新依赖与构建产物，probe 新 checkout，并且只在 probe 成功后重启 worker：

```bash
openprogram upgrade status
openprogram upgrade --dry-run
openprogram upgrade
```

历史 `openprogram update` 命令保留为已有安装的兼容路径，不定义 stable desktop 或受控 CLI release 行为。

source checkout 的恢复细节见[服务器升级](../server/upgrading.zh.md)。
