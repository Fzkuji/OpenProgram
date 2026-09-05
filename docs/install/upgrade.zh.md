# 升级

升级行为取决于安装类型。stable 安装只在已发布版本之间变更，不跟随 `origin/main`。

0.7.0 是从 v0.6.6 进入 updater release 线的一次性过渡：Desktop 用户手动安装 v0.7.0 DMG；CLI/server 用户重新执行一次完整 release installer：

```bash
curl -fsSL https://openprogram.io/install | sh
```

安装 v0.7.0 后，后续 stable release 可使用下面的 Desktop 设置和 `openprogram upgrade` 命令。

## 桌面 release

Desktop 会自动检查最新 stable GitHub Release，也可以在“设置 → General → Application → 立即检查”手动检查。

- macOS：有新版本时选择“下载并打开 DMG”。OpenProgram 会选择与架构匹配的完整 `unsigned` DMG，下载到用户指定位置，验证字节数与 SHA-256 后打开。退出 OpenProgram 并替换 `OpenProgram.app`；macOS 可能再次要求通过“隐私与安全性 → 仍要打开”授权。
- Linux：从目标不可变 tag 重新执行 release installer。当前不发布 Linux 桌面包。

应用外壳与完整 product runtime 一起替换；`~/.openprogram` 下的状态保持不变。

## CLI 和服务器 release

检查或升级到最新 stable release：

```bash
openprogram upgrade --check
openprogram upgrade
```

需要指定不可变 release 时使用：

```bash
curl -fsSL https://openprogram.io/install | OPENPROGRAM_VERSION=X.Y.Z sh
```

Windows 对应命令为：

```powershell
$env:OPENPROGRAM_VERSION = "X.Y.Z"
irm https://openprogram.io/install.ps1 | iex
```

命令从不可变 release tag 取得版本化 installer。installer 下载同平台 runtime archive，在暂存目录验证 checksum 和完整 capability manifest，并在发布或激活该版本前执行 worker cold-start。macOS/Linux 会串行化升级并原子切换 `current` symlink；Windows 原子替换 PowerShell launcher，并保留上一份 launcher。激活前失败时，旧版本仍保持选中，尚未发布的暂存 runtime 会被删除。版本目录会保留，因此回滚时用上一版 `OPENPROGRAM_VERSION` 重跑同一命令即可；运行中的 worker 不会自动重启。

升级后重启登录服务：

```bash
openprogram worker restart
```

## 开发 checkout

在 source checkout 中，同一命令执行开发升级流程，而不是 release installer。它验证 Git 目标，仅在相关源文件变化时更新依赖与构建产物，probe 新 checkout，并且只在 probe 成功后重启 worker：

```bash
openprogram upgrade --check
openprogram upgrade --dry-run
openprogram upgrade
```

历史 `openprogram update` 命令作为 `openprogram upgrade` 的兼容别名保留。

source checkout 的恢复细节见[服务器升级](../server/upgrading.zh.md)。
持续维护的架构、信任边界、界面状态和实现证据见
[正式版本自动更新](../reference/design/distribution/automatic-updates.html)。
