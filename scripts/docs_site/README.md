# 文档站构建器

将 `docs/` 中的 Markdown、HTML 片段和独立 HTML 页面构建为静态文档站。所有页面均以 HTML 展示，共享站点导航、搜索和语言入口。

当前源码格式、HTML 设计规范、语言配对、导航和打包服务行为，统一维护在[文档 HTML 展示与编写规范](../../docs/reference/design/docs-site.zh.html)。本页只保留操作入口。

在仓库根目录运行：

```sh
python -m scripts.docs_site.build
python -m scripts.docs_site.checklinks
python -m scripts.docs_site.checklang
python -m scripts.docs_site.check_landing
```

构建依赖和 `uv run` 命令见[贡献指南](../../.github/CONTRIBUTING.md)。必须先构建再检查链接；检查器读取 `docs/_site/`。该目录是 Git 忽略的生成产物，不手改、不提交。

默认本地访问地址为 `http://localhost:18100/docs/`。源码 worker 使用当前源码实例的构建目录；已安装 runtime 可能使用打包的 `_frontend/docs/`，构建另一个 checkout 不会更新它。验收时核对实际显示的页面。完成应用更新统一使用 `scripts/refresh-local-app.sh`，具体要求见根目录 `AGENTS.md`。
