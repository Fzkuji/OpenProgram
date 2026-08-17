# Source checkout 升级

> 范围：本文定义开发/source checkout 的 Git 门禁更新路径。Desktop 与 managed
> CLI/server release 的当前设计见[正式版本自动更新](../distribution/automatic-updates.html)，
> 安装和打包合同仍由[安装、打包、发布与升级](../distribution/installation-packaging.html)
> 定义。

## 产品边界

`openprogram upgrade` 先检测安装类型，再选择两个相互独立的实现之一：

| 安装类型 | `stable` 的含义 | 升级路径 |
|---|---|---|
| Managed release | 最新 non-draft、non-prerelease GitHub Release | 验证完整平台 runtime 后原子切换 `current`；不重启运行中的 worker |
| Source checkout | `origin/main` | 执行下述 Git 门禁流程；候选 cold-start probe 通过后才重启 |

未知安装类型会被拒绝。source checkout 不把 `main` commit 描述成已发布产品版本；
managed installation 不回退到 Git、PyPI、wheel 或 npm package。

## 问题

source checkout 通常以 editable Python project 安装，因此该 checkout 的改动会在
下次 worker 重启时成为运行代码。未经验证的 `git pull` 再执行
`openprogram restart`，可能用无法 import、build 或启动的代码替换可用 worker。

开发应在独立 worktree 中进行。服务 checkout 在候选完成常规测试和复核前保持不变；
upgrade 命令在重启前再执行一次面向 runtime 的门禁。

## 已实现的 source-checkout 流程

`openprogram upgrade` 依次执行：

1. **Preflight**：拒绝 dirty checkout，解析配置的 ref，降级前要求确认。
2. **Checkout**：fetch 并 fast-forward 到目标 commit。
3. **Dependencies**：只有相关依赖文件变化时才执行 editable Python install 或
   `npm ci`。
4. **Build**：Web source 变化时重新构建 Web export。
5. **Probe**：在隔离的临时 profile 下启动候选，等待 `/healthz`，并运行适用的
   doctor checks。
6. **Restart**：除非传入 `--no-restart`，否则重启真实 worker。
7. **Verify**：轮询 `/healthz`，要求其报告新的 Git SHA。

restart 之前的失败不改变运行中的 worker。verify 失败返回 `verify-failed`；普通文本
输出还会打印精确的手动 checkout/restart 恢复命令。自动回滚与 chat sentinel
报告尚未实现。

## 命令与 channel

```bash
openprogram upgrade status
openprogram upgrade --dry-run
openprogram upgrade
```

对于 source checkout，内置 `stable` 明确解析为 `origin/main`；该名称只在 source
路径中表示开发 channel。managed release 中的 `stable` 表示最新已发布 GitHub
Release。`--channel` 持久化 source checkout 选择的 ref；managed release 只接受
`stable`。

`openprogram update` 保留为 `openprogram upgrade` 的兼容别名。完整的命令、参数、
失败原因和手动恢复说明见[服务器升级](../../../server/upgrading.zh.md)。

## 失败语义

| 失败 | 结果 |
|---|---|
| dirty checkout 或 ref 无法解析 | checkout 前停止 |
| dependency、build、doctor 或 cold-start probe 失败 | restart 前停止；现有 worker 继续运行 |
| restart 失败 | 返回结构化非零结果 |
| 重启后的服务报告错误 SHA | 返回 `verify-failed`；普通文本输出还会打印 previous-SHA 恢复命令 |

进度记录在 `~/.openprogram/upgrade-state.json`。未显式传入 `--channel` 时，
`--dry-run` 不修改 checkout、worker 或 upgrade state；同时传入 `--channel` 仍会
持久化该 source-channel 选择。`--json` 在所有退出路径只输出一个可解析 JSON
document。

## 实现状态

source checkout gate、status/dry-run、持久化 channel、隔离 probe、restart、SHA
验证、结构化失败和手动恢复指令均已在 `openprogram/cli/commands/upgrade.py` 中实现。
自动回滚仍明确不属于当前实现范围。
