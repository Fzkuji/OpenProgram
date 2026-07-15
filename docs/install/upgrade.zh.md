# 升级

这页说明如何把已安装的 OpenProgram 更新到最新版本，以及什么时候需要重跑安装脚本。

## openprogram update

```bash
openprogram update           # 检查并应用更新
openprogram update --check   # 只检查，不应用
openprogram update --force   # 绕过 6 小时节流，立即检查
```

更新策略按安装方式区分。从 git clone 装的（`pip install -e .`，即安装脚本的默认方式）走 `git fetch` + `git pull --ff-only`：

- 工作树有未提交改动时**拒绝 pull**，避免在你改过的代码上制造合并冲突；
- 只做 fast-forward，本地有自己的提交时不会强行合并。

从 PyPI wheel 装的（`pip install openprogram`）则改走 pip 更新到 PyPI 最新版；`openprogram update` 会自动识别安装方式并选对路径。

更新成功后会写入一条记录，下次启动 `openprogram` 时显示"updated to X"的提示。更新的是代码，正在运行的服务要 `openprogram restart` 才用上新版本。

## 自动更新

worker 启动时会在后台自动检查并应用更新，每 6 小时至多查一次，失败静默、不影响服务。设置 `OPENPROGRAM_NO_AUTO_UPDATE=1` 可关闭。

## 什么时候重跑安装脚本

`openprogram update` 只拉代码，不重装依赖、不重新构建 web 前端。更新后如果出现依赖缺失或页面异常，重跑安装脚本：

```bash
cd OpenProgram && ./scripts/install.sh    # Windows: .\scripts\install.ps1
```

脚本的每一步都是幂等的，任何时候重跑都安全——已装好的步骤会跳过或原地刷新，不会破坏现有配置和会话数据（它们在 `~/.openprogram/`，脚本不碰）。

手动升级等价于这几步：

```bash
git pull
./scripts/install.sh
openprogram restart
```
