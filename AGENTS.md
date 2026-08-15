# AGENTS.md

## 本地运行实例

- App、Web 和本地验收统一使用默认 profile、端口 `18100` 和状态目录 `~/.openprogram`。
- 不为日常开发或验收启动 `OPENPROGRAM_PROFILE=dev`、`18200` 或另一套前端实例。
- App 启动和可视验收只使用 `/Applications/OpenProgram.app`；禁止运行 `desktop/node_modules/electron`、`npm --prefix desktop run dev` 或临时 E2E Electron 作为用户可见 App。
- 一个功能完成并提交后统一运行 `scripts/refresh-local-app.sh`。它从当前 checkout 构建一个 wheel，同时覆盖 PATH 中的 OpenProgram、App 内置 runtime、静态前端和 Electron archive，然后重启默认 worker；禁止从旧 worktree 或旧环境单独执行 `openprogram worker restart`。
- 禁止在 `post-commit` 中自动重启 worker；连续开发只在功能完成后刷新一次，避免中断正在执行的会话。
- 本地只保留 `/Applications/OpenProgram.app` 这一份可见 App；更新时覆盖现有内容，不创建按 commit 命名的 App 副本。
- CLI 的 profile 功能属于产品能力，保留；本仓库的本地运行不使用额外 profile。

## 文档管理规则

`docs/` 是**产品文档站**（http://localhost:18100/docs），按用户问题组织成九个 Tab：
start / install / capabilities / interfaces / models / integrations / server / reference / design。
design 是虚拟 Tab——没有 docs/design/ 目录，nav.py 的 tab_of() 把 reference/design/ 下的
工程记录归档路由到它。
两类文字，去处不同：

1. **产品文档**（写给读者）：落到对应 Tab 目录。每一页因为"用户会问这个问题"而存在，
   不因为"仓库里有这块代码"。所有命令、参数、路径必须对着当前代码或 CLI `-h` 核实。
   功能改动时同步更新涉及的产品文档页——它和测试一样跟代码一起维护。
   新页要在 `tools/docs_site/nav.py` 的 `PAGE_ORDER` 里登记侧栏顺序。
2. **工程记录**（设计稿、方案对比、实施计划、研究笔记，写给开发者自己）：
   放在 `docs/reference/design/`，按子系统组织，**持续维护、与代码同步**：
   一个主题只有一份文档，用现在时正面陈述当前设计；修订设计就改写文档本体
   （演变历史看 git），禁止 v2 副本、"Decision N/裁决"式变更记录、日期与
   commit sha。未实现的设计写进正文，在文末"实现状态"附录集中标注。
   不要把工程记录写进产品 Tab。
3. **站点根静态文件**（`docs/_static_root/`）：原样拷到站点根，不进导航、不进
   sitemap。放搜索引擎所有权验证文件、`robots.txt` 这类必须能从确切根 URL
   取到的文件。
4. **生成参考页**（`docs/reference/cli/`、`config-keys.md`、`provider-registry.md`）：
   构建时由 `tools/docs_site/generate_reference.py` 从代码（argparse 树、
   config_schema.SETTINGS、provider.json）重新生成，已 gitignore，禁止手改——
   要改内容就改代码里的 help/说明文字。

双语约定：`xxx.md` 是**英文默认版**（站点主体，必须 100% 英文，不掺任何中文），
`xxx.zh.md` 是中文对照版（语言切换按钮跳转）。英文版为准；改内容先改英文版，再同步中文版。

硬性约束：
- 禁止把同一份文档复制到第二个位置（历史上因此产生过两份同名文档各自分叉）。
- 站内链接用相对路径指向 `.md`；指向 docs/ 之外仓库文件的链接一律写 GitHub URL
  （`https://github.com/Fzkuji/OpenProgram/blob/main/...`），站上解析不了相对上跳。
- 改完文档跑 `python -m tools.docs_site.checklinks`，保持 0 死链。
- `docs/_site/` 是构建产物，已 gitignore，不要提交。
