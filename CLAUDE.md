# CLAUDE.md

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
   追加到 `docs/reference/design/` 归档，按子系统放，只增不改。不要把它们写进产品 Tab。
3. **生成参考页**（`docs/reference/cli/`、`config-keys.md`、`provider-registry.md`）：
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
