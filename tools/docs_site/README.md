# 文档站构建器（docs_site）

把 `docs/` 下的文档生成成一个统一风格的静态网站：左侧折叠目录、右侧本页锚点、
顶部搜索、深浅双主题、中/EN 界面切换。产物输出到 `docs/_site/`，由 worker 在
单端口的 `/docs` 路径下托管。

## 怎么重新生成站点

改完任何文档后，在仓库根目录跑：

```bash
python -m tools.docs_site.build
```

它会扫描 `docs/`、重建整个 `docs/_site/`、刷新搜索索引。

- **只改了文档内容**：跑上面这条命令即可，worker 直接读 `docs/_site/` 文件，
  刷新浏览器就能看到。
- **改了 `web/next.config.mjs`（路由）或后端路由**：需要重建前端 + 重启 worker：
  ```bash
  cd web && npm run build && cd ..
  openprogram worker restart
  ```

本地预览：站点资源用的是绝对路径 `/docs/...`，所以直接用 `python -m http.server`
打开会缺样式。直接访问 worker 的 `http://localhost:18100/docs` 最准。

## 写文档的三种方式

放进 `docs/` 下任意子目录的文件会自动收进站点（目录结构 = 左侧导航分组）。

### 1. Markdown（`.md`）— 最常用

照常写 markdown。标题进右侧锚点、代码块语法高亮、表格/引用/列表都有统一样式。
文档里 `[链接](other.md)` 会自动转成站内跳转。

### 2. 手写 HTML 片段（`.html`，不带 `<html>`/`<body>`）— 要自定义/动态内容时用

文件里**只写正文片段**，不写 `<html>`/`<head>`/`<body>` 骨架。构建时自动套上
站点外壳（顶栏、左侧目录、主题、搜索、右侧锚点），你只管写内容：

```html
<h1>我的文档</h1>
<p>正文……</p>

<canvas id="x"></canvas>
<script>/* 任意动画 / 交互，原样执行 */</script>

<style>/* 自定义样式，构建时自动限定到本页，不会影响外壳 */</style>
```

- 标题（`<h1>` 作页面标题，`<h2>/<h3>` 进右侧锚点）、表格、列表等用站点统一样式。
- `<script>` 原样保留并执行——canvas、SVG 动画、交互都能写。
- `<style>` 会被自动加上本页作用域前缀，不会改到站点外壳。
- 想配合深浅主题，监听主题切换事件：
  ```js
  window.addEventListener('documentThemeChange', e => {
    // e.detail.theme === 'dark' | 'light'
  });
  ```

### 3. 完整 HTML 页面（`.html`，带 `<html>`/`<body>`）— 完全独立时用

文件是一个自带 `<head>`、全套样式的独立网页。构建时用 **iframe 隔离嵌入**，
原页面的布局/样式/脚本 100% 保留，但**不套站点外壳**（没有左侧目录、主题、锚点）——
等于一个“画中画”。适合那种自成体系、不想受外壳影响的可视化页面。

判断规则：文件里有 `<html>` 或 `<body>` → 走方式 3（iframe）；否则 → 走方式 2（套壳）。

## 文件结构

```
tools/docs_site/
  build.py        入口：扫描 docs/ → 渲染 → 写 docs/_site/
  nav.py          从目录结构生成左侧导航树 + 根散页归类/显示名
  template.py     HTML 外壳模板（顶栏 + 三栏 + 主题/语言按钮）
  search.py       生成 search-index.json
  assets/
    site.css      全站样式 + 深浅双主题变量
    site.js       主题切换 / 语言切换 / 锚点高亮 / 搜索 / 移动端抽屉
    pygments-*.css  代码高亮（构建时生成，浅/深各一套）
```

## 一些约定

- **排除目录**：`_site/`、`images/`、`slides/` 不进站点。
- **根目录散页**（`docs/*.md` 如 README、install）按文件名归入「快速上手 / 集成 / 参考」
  三组，显示名在 `nav.py` 的 `ROOT_PAGE_GROUPS` 里配置。
- **同名 `foo.md` + `foo.html`**：md 是正文页，html 作为它的「可视化」附属页另存。
- **界面文案的中/EN 翻译**：在 `assets/site.js` 的 `I18N` 表里。正文内容本身不翻译
  （以后若要正文多语言，可加 `xxx.en.md` / `xxx.zh.md` 配对，机制待实现）。
- **挂载路径**：默认 `/docs/`，可用环境变量 `OPENPROGRAM_DOCS_BASE` 覆盖。
