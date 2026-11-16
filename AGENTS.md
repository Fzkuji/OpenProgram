# AGENTS.md

## Anthropic 接入与合规要求

本项目要求安全、合法、合规地使用 Anthropic 服务。Anthropic 集成仅使用官方 API 和合法获取的 API key，按 API 账户计费；所有调用必须遵守 Anthropic 的服务条款与使用政策。维护相关代码和文档时，应保持这一接入方式与要求。

## 本地运行实例

本机有两个实例，从同一个仓库构建（关系见 `openprogram/operations/worker/dev_instance.py`）：

| | 稳定版 | 开发版 |
| --- | --- | --- |
| 代码 | `stable` 分支，只靠晋升移动 | `dev` 上部署过去的提交 |
| 运行 | `/Applications/OpenProgram.app`，端口 `18100` | `/Applications/OpenProgram Dev.app`，端口 `18200`，运行时在 `~/.openprogram-dev/runtime` |
| 数据 | `~/.openprogram` | `~/.openprogram-dev`（`dev` profile） |

- 一个功能完成并提交后运行 `scripts/local-instances/refresh-dev-instance.sh`（或 `openprogram dev deploy`），只更新开发版（运行时和 `OpenProgram Dev.app`）；它不碰稳定版的 App、运行时和 worker，可以在稳定版的对话里运行。
- 稳定版只通过晋升更新：`openprogram dev promote` 要求提交正是开发版正在服务、且健康的那个，只做 fast-forward，先等稳定版没有运行中的工作（最多 10 分钟，超时拒绝并列出还在跑什么），再备份 `~/.openprogram` 并运行 `scripts/local-instances/refresh-local-app.sh`：新 App 先在旁边构建、校验、签名，再停旧 worker 改名切换；新 worker 健康后才快进 `stable`，任何一步失败都放回原来的 App 和 worker，`stable` 不动，不重试。
- `scripts/local-instances/refresh-local-app.sh` 只安装 `refs/heads/stable`（晋升时安装要晋升的那个快进提交）；从 `dev` 或其他提交运行会被拒绝。禁止手动移动 `stable` 绕过晋升。
- 用户可见的 App 只有 `/Applications/OpenProgram.app`（稳定版）和 `/Applications/OpenProgram Dev.app`（开发版，由开发版部署安装，见 `scripts/local-instances/install-dev-app.sh`）；日常测试和可视验收用开发版 App 或浏览器（`openprogram --profile dev web auth-url --base-url http://127.0.0.1:18200` 取登录链接），不占用稳定版。禁止运行 `node_modules/electron`、`npm run dev --workspace apps/desktop` 或临时 E2E Electron 作为用户可见 App。
- 禁止在 `post-commit` 中自动刷新任一实例；禁止从旧 worktree 或旧环境单独执行 `openprogram worker restart`。
- 本地只保留这两份可见 App；更新时覆盖现有内容，不创建按 commit 命名的 App 副本。
- CLI 的 profile 功能属于产品能力；本仓库的本地运行只用默认 profile（稳定版）和 `dev` profile（开发版），不再起其他 profile 或端口。

## 共享工作区

多个会话同时在主检出的 `dev` 分支上工作，共用一个 index。

- 提交只带自己的路径：`git commit -- <paths>`，提交前看 `git diff --cached`，不要用裸 `git commit` 或 `git add -A`。
- 移动、重命名、拆分文件这类结构改动在自己的 worktree 里做完，测试通过后一次合入 `dev`；不要在共享 index 里长期留着暂存的重命名或半成品。
- worktree 里的提交和合入不经过 hook。合入 `dev` 前在 worktree 里运行 `python -m scripts.docs_site.check --base dev`，或 `python -m scripts.testing.affected_tests --base dev --run`（没有未提交改动时不带 `--base` 也会按分支对比 `dev`）。
- worktree 的分支合入后就删掉 worktree（`git worktree remove`）；被放弃的也一样，分支可以留着。

## 分支与 GitHub 发布

本地只有三条线，设计见 `docs/reference/design/distribution/github-publishing.html`：

| 分支 | 作用 | 谁移动它 |
| --- | --- | --- |
| `dev` | 开发线，所有会话往这里提交，开发版从它部署 | `git commit` |
| `stable` | 晋升线，稳定版 App 运行它 | 只有 `openprogram dev promote` |
| `main` | 慢线，GitHub 上唯一的分支，积累很多次晋升后才出一个版本，一个版本一个提交 | 只有 `scripts/publishing/publish-github.sh` |

- 日常提交只进 `dev`。禁止在 `main` 上提交（`pre-commit` 会拒绝），禁止把 `main` 合进 `dev` 或 `stable`，两边历史本来就不同。
- 发布只用 `scripts/publishing/publish-github.sh -m "版本摘要"`：它把 `stable` 的文件树压成一个提交接到 `main` 上，在 `refs/publish/last` 记下来源，再推送 `main`。先加 `--dry-run` 看提交说明；`--no-push` 只更新本地 `main`。发版时加 `--tag vX.Y.Z`：推送 `main` 后给这个提交打标签并推送标签，由此启动 Release 工作流；版本号或发布说明对不上会先拒绝，`--dry-run` 能看到这些检查和标签。
- 禁止手动 `git push`；`.git/hooks/pre-push` 只放行脚本推送的 `main` 和指向它的标签，其他分支一律拒绝。不要从本地分支向 GitHub 开 PR。
- worktree 分支合入 `dev` 或放弃后删除，想留着的放到 `refs/archive/`。

## 文档管理规则

`docs/` 是产品文档站，按用户问题分九个 Tab；页面写法见 `docs/reference/design/docs-site/authoring.html`。结束前跑一次，按输出处理：

```sh
python -m scripts.docs_site.check <自己改的路径>   # 日常，只看这些改动
python -m scripts.docs_site.check --all            # 全站
```

- `error`：按输出的文件、行号和修法改掉。
- 受影响页面：逐页读，要改就改；确认不用改，就记下：`python -m scripts.docs_site.check <路径> --ack PAGE ... --reason "原因"`。
- 共享检出里不带路径只报告不拦截，因为工作区里有别的会话的页面；要拦截就写明路径。
- `python -m scripts.testing.affected_tests --run` 已包含这一步。什么时候自动跑、哪里拦截，见 `docs/reference/design/docs-site/governance.html`。

文字的去处，程序判断不了，自己判断：

1. **产品文档**（写给读者）：放进对应 Tab。每一页因为"用户会问这个问题"而存在，不因为"仓库里有这块代码"。
2. **工程记录**（设计稿、方案对比、计划、研究笔记）：放 `docs/reference/design/`，按子系统组织。一个主题一份文档，现在时写当前设计，修订就改写本体，不写 v2 副本、变更记录、日期或 commit sha；未实现的部分放文末"实现状态"。
3. **站点根静态文件**：`docs/_static_root/`，原样拷到站点根，放验证文件、`robots.txt` 这类。
4. **生成参考页**（`docs/reference/cli/`、`config-keys.md`、`provider-registry.md`）：构建时从代码生成，已 gitignore，要改就改代码里的说明文字。

英文页 `xxx.html` 为准，`xxx.zh.html` 是结构相同的中文版，先改英文。改名时旧 URL 写进 `docs/redirects.json`。指向 `docs/` 之外仓库文件的链接写 GitHub URL。
