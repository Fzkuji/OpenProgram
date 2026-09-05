# 升级

`openprogram upgrade` 根据安装类型选择行为。managed release 安装最新 stable
GitHub Release，但在用户显式重启前不改变运行中的 worker。source checkout 使用本文
记录的 Git 门禁流程：只有证明新代码能够启动后，才更新代码并重启服务。

Desktop 与 managed CLI/server 的 release 升级，以及从 v0.6.6 到 v0.7.0 的一次性过渡，见
[升级 release 安装](../install/upgrade.zh.md)。

## Source checkout 行为

对于 source checkout，`upgrade` 是"`git pull` 然后 `openprogram restart`"的安全替代。

它解决的问题是：OpenProgram 以可编辑安装（editable install）的方式装在仓库里，
所以它运行的仓库就是它开发的仓库。一个坏提交在下次重启前都看不出来，
而等到那时，本来能用来修复它的工具正好就是坏掉的那个。`upgrade` 把这个失败提前，
挪到一个没人依赖的临时进程里。

## 快速参考

```bash
openprogram upgrade status          # 会变什么？只读
openprogram upgrade --dry-run       # 打印计划，不移动代码或 worker
openprogram upgrade                 # 执行
```

## 执行过程

七个步骤，按顺序执行。第一个失败会中断整条链并打印原因。

| 步骤 | 作用 |
|---|---|
| preflight | 拒绝脏工作区，解析目标提交，降级前先询问 |
| checkout | 将检出快进到目标提交 |
| deps | `pyproject.toml` 变了就刷新源码 checkout 的 Python 环境；`package-lock.json` 变了就安装 frontend workspaces |
| build | `apps/web`、`apps/cli` 或 `package-lock.json` 变更时重建对应 frontend workspace |
| probe | 用隔离 profile 在临时端口冷启动新代码，等待 `/healthz`，跑 doctor 检查，然后杀掉 |
| restart | 重启真正的服务 |
| verify | 轮询 `/healthz` 直到它报告新的提交 sha |

**restart** 之前的一切都不会碰到正在运行的实例。语法错误、配置 schema 损坏、
前端构建失败，都由 probe 步骤拦下，你的服务继续用旧代码提供服务。

## 决定升级前先确认

`status` 告诉你有没有可更新的内容：

```console
$ openprogram upgrade status
  channel        stable (origin/main)
  head           f5671fd25e4c6ae89e6d77f3fcffc4d4a2c0570a
  target         a2d7f95633527e182ac850e40aca727aa0f6a3e6
  update         available
```

加 `--json` 得到机器可读输出（`head_sha`、`target_sha`、`update_available`）。

`--dry-run` 解析目标并打印将要执行的步骤，不修改 checkout、worker 或 upgrade
state；如果同时显式传入 `--channel`，该 source-channel 选择仍会持久化：

```console
$ openprogram upgrade --dry-run
  [OK  ] preflight  stable → origin/main: 80d77d1ed44c → 1a4101433b13
  [OK  ] checkout   planned (dry run)
  [OK  ] deps       planned (dry run)
  [OK  ] build      planned (dry run)
  [OK  ] probe      planned (dry run)
  [OK  ] restart    planned (dry run)
  [OK  ] verify     planned (dry run)
```

## 参数

| 参数 | 效果 |
|---|---|
| `--dry-run` | 打印计划步骤，不修改 checkout、worker 或 upgrade state；显式传入的 `--channel` 仍会持久化 |
| `--no-restart` | 在 probe 之后停止。检出会移动、代码会被验证，但运行中的服务在你手动重启前仍用旧代码 |
| `--yes`、`-y` | 跳过降级所需的确认 |
| `--channel NAME` | 跟随另一条发布线，并记住它 |
| `--json` | 输出包含每个步骤的机器可读结果 |

## Source checkout 通道（Channel）

source-checkout channel 是要跟踪的 ref 名称。`stable` 跟随 `origin/main`，也是唯一
内置的通道；这里的 stable 是开发 ref，不是 managed installation 使用的 stable GitHub
Release。`--channel` 会把选择持久化为 `update.channel` 设置，之后运行不必重复传参。

## 某个步骤失败时

失败会打印原因码（`dirty-worktree`、`probe-failed`、`build-failed`、
`verify-failed` 等）并以非零状态退出。

- **`dirty-worktree`** — 提交或 stash 你的改动。`upgrade` 不会移动一个还有未完成
  工作的检出。
- **`downgrade-needs-confirmation`** — 目标比你正在运行的版本旧。旧代码可能读不懂
  新代码写出的配置；确实要这么做就加 `--yes`。
- **`probe-failed`** — 新代码启动不了。什么都没有重启，你的服务完好无损；
  问题要在上游修。
- **`verify-failed`** — 重启发生了，但服务没有报告新的 sha。自动回滚尚未实现，
  所以命令会打印手动的逃生出口：

  ```bash
  git -C <repo> checkout <previous-sha> && openprogram restart
  ```

每一步之后进度都会写入 `~/.openprogram/upgrade-state.json`，
升级中途挂掉时就去那里查。

## 相关

- [故障排查](troubleshooting.zh.md)：与更新无关的问题。
- `openprogram update` 是 `openprogram upgrade` 的兼容别名。managed release
  使用 stable GitHub Release 路径；source checkout 使用上文的 Git 升级流程。
