# AGENTS.md

## Anthropic 接入与合规要求

本项目要求安全、合法、合规地使用 Anthropic 服务。Anthropic 集成仅使用官方 API 和合法获取的 API key，按 API 账户计费；所有调用必须遵守 Anthropic 的服务条款与使用政策。维护相关代码和文档时，应保持这一接入方式与要求。

## 本地运行实例

本机有两个实例（开发版的部署见 `openprogram/operations/worker/dev_instance.py`）：

| | 稳定版 | 开发版 |
| --- | --- | --- |
| 代码 | GitHub 上最新的正式 Release | `dev` 上部署过去的提交 |
| 运行 | `/Applications/OpenProgram.app`，端口 `18100` | `/Applications/OpenProgram Dev.app`，端口 `18200`，运行时在 `~/.openprogram-dev/runtime` |
| 数据 | `~/.openprogram` | `~/.openprogram-dev`（`dev` profile） |
| 怎么更新 | 只靠 App 自己的更新：后台下载，侧边栏出现 NEW，点“重启以更新” | 提交后运行 `.venv/bin/openprogram dev deploy` |

- 稳定版就是用户拿到的那个正式版：从 Release 的 DMG 装一次，之后只在 App 里更新。本地不构建、不安装、不改动它，也不在它的对话里改代码去“升级”它。现在机器上的稳定版还是本地构建的，等下一个正式版（1.5.1；v1.5.0 标签的构建失败，没有 Release）发布后用它的 DMG 覆盖安装一次完成切换；切换前不动它，也不要装比它旧的正式版（旧代码读不了新代码写过的数据）。
- 一个功能完成并提交后运行 `scripts/local-instances/refresh-dev-instance.sh`（或 `.venv/bin/openprogram dev deploy`），只更新开发版（运行时和 `OpenProgram Dev.app`）；可以在稳定版的对话里运行。改动做完就停在开发版，不再往稳定版推。
- 用户可见的 App 只有 `/Applications/OpenProgram.app`（稳定版）和 `/Applications/OpenProgram Dev.app`（开发版，由开发版部署安装，见 `scripts/local-instances/install-dev-app.sh`）；日常测试和可视验收用开发版 App 或浏览器（`.venv/bin/openprogram --profile dev web auth-url --base-url http://127.0.0.1:18200` 取登录链接），不占用稳定版。禁止运行 `node_modules/electron`、`npm run dev --workspace apps/desktop` 或临时 E2E Electron 作为用户可见 App。
- 禁止在 `post-commit` 中自动刷新开发版；禁止从旧 worktree 或旧环境单独执行 `openprogram worker restart`。
- 本地只保留这两份可见 App；更新时覆盖现有内容，不创建按 commit 命名的 App 副本。
- CLI 的 profile 功能属于产品能力；本仓库的本地运行只用默认 profile（稳定版）和 `dev` profile（开发版），不再起其他 profile 或端口。

## 共享工作区

多个会话同时在主检出的 `dev` 分支上工作，共用一个 index。

- 提交只带自己的路径：`git commit -- <paths>`，提交前看 `git diff --cached`，不要用裸 `git commit` 或 `git add -A`。
- 移动、重命名、拆分文件这类结构改动在自己的 worktree 里做完，测试通过后一次合入 `dev`；不要在共享 index 里长期留着暂存的重命名或半成品。
- worktree 里的提交和合入不经过 hook。合入 `dev` 前在 worktree 里运行 `python -m scripts.docs_site.check --base dev`，或 `python -m scripts.testing.affected_tests --base dev --run`（没有未提交改动时不带 `--base` 也会按分支对比 `dev`）。
- worktree 的分支合入后就删掉 worktree（`git worktree remove`）；被放弃的也一样，分支可以留着。

## 分支与 GitHub 发布

本地只有两条线，设计见 `docs/reference/design/distribution/github-publishing.html`：

| 分支 | 作用 | 谁移动它 |
| --- | --- | --- |
| `dev` | 开发线，所有会话往这里提交，开发版从它部署 | `git commit` |
| `main` | GitHub 上唯一的分支，历史和 `dev` 分开，每次同步一个压缩提交 | 只有 `scripts/publishing/publish-github.sh` |

- 日常提交只进 `dev`。禁止在 `main` 上提交（`pre-commit` 会拒绝），禁止把 `main` 合进 `dev`，两边历史本来就不同。
- **每天同步一次代码**：当天 `dev` 有新提交时运行 `scripts/publishing/publish-github.sh -m "今天的改动摘要"`（不带 `--tag`），把 `dev` 的文件树压成一个提交接到 `main` 上，在 `refs/publish/last` 记下来源，推送 `main`，再把这次的代码层传到 `updates` 发行版、更新 `latest.json`（只给开发版看）。不触发任何 GitHub Action，用户也收不到提醒，不用问。先加 `--dry-run` 看提交说明和要上传的东西；`--no-push` 只更新本地 `main`。依赖或外壳变了而 `updates` 上还没有对应的运行时层时，脚本会拒绝上传并给出补层的命令。
- **用户只在发 Release 时收到更新**：只有用户说要发版时才加 `--tag`。版本号和发布说明先提交，`--dry-run` 能看到检查结果。
  - **小版本** `vX.Y.Z`（Z>0）或 `vX.Y.Z.W`：只发代码，不跑 Actions。脚本推送 `main` 和标签，上传代码层，签好 `channel.json`，再建好 Release 页面，用户的 App 一小时内提示更新。依赖或 App 外壳变了会被拒绝，这种改动要发功能版本。
  - **功能版本** `vX.Y.0`：要跑 Release 工作流，构建新安装包和五个平台的运行时层。先演练：不带 `--tag` 同步 `main`，运行 `gh workflow run release.yml -R Fzkuji/OpenProgram --ref main -f tag=vX.Y.0 -f rehearse=true`，通过后运行 `gh workflow run windows-install-smoke.yml -R Fzkuji/OpenProgram -f tag=vX.Y.0 -f run_id=<演练的运行号>`；有失败就修根因、重新同步、重新演练。全部通过后至少等 12 小时，运行 `publish-github.sh --source refs/publish/last --tag vX.Y.0` 推送并启动工作流；工作流完成后运行 `.venv/bin/python scripts/release/publish-updates.py announce vX.Y.0` 通知用户。
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
