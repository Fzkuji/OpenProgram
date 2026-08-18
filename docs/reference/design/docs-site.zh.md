# 设计文档站（统一文档网页）

> `docs/` 下的 markdown 与手写 html 以同一套风格的静态文档站提供：
> 左侧目录树 · 顶部搜索 · 右侧本页锚点 · 深浅双主题。一处改皮肤，全站保持统一。
> 内嵌动态动画原样渲染——它是页面内容的一部分，不是事后补丁。

## 一、目标与非目标

### 目标

1. **一套外壳，全站统一**：导航、配色、排版、代码块样式只定义一次，全部文档复用。
2. **零运行时框架**：产物是纯静态 html/css/js，可直接由 worker（单端口路线）或任意静态服务器托管，不引入 Vite/Vue/React 运行时。
3. **深浅双主题**：一套 CSS 变量两套配色，跟随系统 + 手动切换 + 记忆偏好（localStorage）。
4. **自动导航**：左侧目录树从 `docs/` 目录结构自动生成，分组标题取自各级 `README.md` 的一级标题。
5. **本页锚点**：右侧 "On this page" 从每篇的 `##/###` 标题自动生成，滚动高亮当前节。
6. **全文搜索**：构建期生成轻量搜索索引（标题 + 正文），前端纯 JS 检索，无后端。
7. **动态动画原样渲染**：md 中内嵌的 `<script>/<canvas>/<svg>/<style>` 原样透传；11 篇手写 html 的图表能整块嵌入新壳不丢失。

### 非目标

- 不做编辑器 / CMS，文档仍以源文件（md/html）为准，站点是只读产物。
- 不做多语言切换框架（文档本身中英混排，不强制 i18n）。
- 不替换 `docs/slides/`（演示稿是独立形态，保持原样）。

## 二、为什么自建脚本，而非 VitePress / MkDocs

| 维度 | 自建脚本 | VitePress | MkDocs Material |
|---|---|---|---|
| 后期加自定义动态动画 | **最高**：模板/CSS/JS 全自有，原生 html/js 直接写 | 高，但须按 Vue 组件写 | 低，主题封闭，与原始 html 打架 |
| 运行时依赖 | 无（纯静态） | Vite/Vue | 无（但构建期重） |
| 与单端口托管路线一致 | 是 | 需额外构建产物对接 | 是 |
| 11 篇手写 html 嵌入 | 直接透传 | 需改写成组件 | 难 |
| 标配（侧栏/搜索/锚点）成本 | 自己写一次 | 开箱 | 开箱 |

需求是统一文档站**且**能自由加动态动画。框架方案要么限制动画（MkDocs），要么逼迁移到组件体系（VitePress）。手写 html 里已含自定义图表与动效，站点需要的正是一个允许写原生 html/js 的壳。侧栏、搜索、锚点自己写一次，换来的是长期的这份自由。

## 三、技术选型

- **构建语言：Python**。仓库主语言是 Python，worker 已是 Python，无需新增 Node 工具链。
- **Markdown 渲染：`markdown-it-py`** + 插件（`mdit-py-plugins`：anchors、footnote、deflist、tasklists）。理由：保留原始 html 透传（`html=True`），内嵌动画能渲染全靠这一点；GitHub 风格表格/代码围栏齐全。
- **代码高亮：Pygments**（构建期渲染成带 class 的 span，运行时零开销；深浅主题各一套 Pygments 样式表）。
- **搜索：构建期生成 `search-index.json`**，前端用极简倒排/子串匹配。数百篇规模无需 lunr/flexsearch 这类重库。
- **数学公式**：留有 KaTeX 接入点，默认不启用。

依赖控制：只新增 `markdown-it-py`、`mdit-py-plugins`、`Pygments` 三个纯 Python 包，放进一个独立 `docs-build` 可选依赖组，不污染主依赖。

## 四、目录与产物布局

```
docs/                         ← 源文件（不动）
  design/runtime/dag/rendering.md
  design/proactive/event-layer.html   ← 手写 html
  ...

scripts/docs_site/              ← 新增：构建脚本（一个小模块）
  build.py                    入口：扫描 docs/ → 渲染 → 写 _site/
  template.py                 html 外壳模板（壳 + 注入点）
  nav.py                      从目录树 + README 生成导航数据
  search.py                   生成 search-index.json
  assets/
    site.css                  全站样式 + 深浅双主题变量
    site.js                   主题切换 + 锚点高亮 + 搜索 + 移动端抽屉
    pygments-light.css
    pygments-dark.css

docs/_site/                   ← 构建产物
  index.html
  design/runtime/dag/rendering.html
  search-index.json
  assets/...
```

构建命令：`python -m scripts.docs_site.build`。

## 五、页面骨架（三栏）

```
┌────────────────────────────────────────────────────────────┐
│  OpenProgram Docs            [🔍 搜索 ⌘K]        [☀/🌙]      │  顶栏 固定
├──────────────┬───────────────────────────────┬─────────────┤
│ 目录树        │  # 页面标题                    │ On this page │
│  Design       │  正文…                         │  · 一、目标  │
│   Runtime     │  ```code```                    │  · 二、…     │
│    > 当前页   │  <canvas> 动画原样透传         │  · 三、…     │
│   Context     │                                │ 滚动高亮当前 │
│ (可折叠分组)  │                                │             │
└──────────────┴───────────────────────────────┴─────────────┘
左栏可折叠/记忆展开态        正文 max-width≈820px       窄屏隐藏右栏
```

窄屏（< 900px）：左栏收成抽屉（汉堡按钮唤出），右栏隐藏。

## 六、深浅双主题

一套 CSS 变量，`:root` 为浅色默认，`[data-theme="dark"]` 覆写为深色。切换逻辑：

1. 首次访问读 `prefers-color-scheme` 跟随系统。
2. 用户点切换 → 写 `localStorage.theme` → 设 `<html data-theme>`。
3. 防闪烁：在 `<head>` 内联一小段同步脚本，DOM 渲染前就定好主题。

配色：

| 角色 | 浅色 | 深色 |
|---|---|---|
| 背景 | `#ffffff` / 侧栏 `#f7f7f5` | `#16181d` / 侧栏 `#1b1e24` |
| 正文 | `#1f2328` | `#d8dae0` |
| 次要文字 | `#656d76` | `#8b929c` |
| 强调色 | `#3b82f6`（蓝） | `#5aa2ff` |
| 代码底 | `#f6f8fa` | `#21262d` |
| 边框 | `#d0d7de` | `#30363d` |

风格基调：浅色为主、对齐 Stripe/Vercel/Linear 那类技术文档的克制专业感；深色不是纯黑，避免刺眼。

## 七、动态动画原样渲染（关键设计）

这是与普通文档站的最大差异点，单独说明落地机制：

1. **md 内嵌透传**：`markdown-it-py` 开 `html=True`，md 里写的 `<canvas>`、`<svg>`、`<script>`、`<style>` 块原样进入产物，不被转义。作者想给某篇加交互 demo，直接在该 md 内写即可。
2. **页面级附加资源**：约定 md 文件可在 frontmatter 声明 `scripts: [foo.js]` / `styles: [foo.css]`，构建期把这些文件拷到产物并在该页注入 `<script>/<link>`。复杂动画拆成独立 js，不污染正文。
3. **手写 html 的处理（保留内容嵌入新壳）**：11 篇手写 html 走专门管道 —— 提取其 `<body>` 内容 + 收集其 `<style>`（加页面级作用域前缀避免与全站样式冲突），整块塞进统一外壳的正文区，原图表/动效保留。其自带的 `<script>` 一并保留。这条管道与 markdown 管道分开，逐篇验证视觉无回归。
4. **主题感知动画**：全局事件 `documentThemeChange` 让动画脚本适配深浅，是否监听由脚本自己决定。

## 八、导航生成规则

- 扫描 `docs/` 下所有 `*.md` 与 11 篇手写 `*.html`。
- 目录即分组：`docs/reference/design/runtime/` → 分组 "Runtime"，组标题优先取该目录 `README.md` 的一级标题，无则用目录名美化。
- 组内排序：`README.md` 置顶，其余按文件名。
- 排除：`docs/_site/`、`docs/images/`、`docs/slides/`，以及名字以下划线开头的目录。
- 顶层散页（`docs/*.md` 如 GETTING_STARTED、install）归入 "Guides" 组。

## 九、托管与产物

构建产物 `docs/_site/` 提交进 git，由 worker 的单端口路由 `/docs` 提供，文档站不需要独立服务器或部署步骤。

## 附录：实现状态

站点已构建并上线。两项残留：

- `docs/reference/design/proactive/_research_archive/` 仍存有三个文件（`evaluation.md`、`replay.md`、`threat-model.md`）。它们靠下划线前缀规则被站点排除，而非删除。
- 构建没有 `--watch` 模式，每次重建都是完整跑一遍 `python -m scripts.docs_site.build`。
