# 设计文档站（统一文档网页）

Status: **draft（待确认）** · Created: 2026-06-29

> 把 `docs/` 下 154 篇 markdown + 11 篇手写 html 统一成同一套风格的静态文档站：
> 左侧目录树 · 顶部搜索 · 右侧本页锚点 · 深浅双主题。一处改皮肤全站统一。
> 同时把"以后能随便加动态动画"作为一等公民支持。

## 一、目标与非目标

### 目标

1. **一套外壳，全站统一**：导航、配色、排版、代码块样式只定义一次，全部文档复用。
2. **零运行时框架**：产物是纯静态 html/css/js，可直接由 worker（单端口路线）或任意静态服务器托管，不引入 Vite/Vue/React 运行时。
3. **深浅双主题**：一套 CSS 变量两套配色，跟随系统 + 手动切换 + 记忆偏好（localStorage）。
4. **自动导航**：左侧目录树从 `docs/` 目录结构自动生成，分组标题取自各级 `README.md` 的一级标题。
5. **本页锚点**：右侧 "On this page" 从每篇的 `##/###` 标题自动生成，滚动高亮当前节。
6. **全文搜索**：构建期生成轻量搜索索引（标题 + 正文），前端纯 JS 检索，无后端。
7. **动态动画一等公民**：md 中内嵌的 `<script>/<canvas>/<svg>/<style>` 原样透传；11 篇手写 html 的图表能整块嵌入新壳不丢失。

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

决策依据：用户的核心诉求是"统一文档站" **且** "以后随便加动态动画"。框架方案要么限制动画（MkDocs），要么逼迁移到组件体系（VitePress）。现有 11 篇手写 html 已含自定义图表/动效 —— 这本身就证明需要的是"能自由写原生 html/js 的壳"。一次性自己写侧栏/搜索/锚点，换取第二阶段不被框架卡住，划算。

## 三、技术选型

- **构建语言：Python**。仓库主语言是 Python，worker 已是 Python，无需新增 Node 工具链。
- **Markdown 渲染：`markdown-it-py`** + 插件（`mdit-py-plugins`：anchors、footnote、deflist、tasklists）。理由：保留原始 html 透传（`html=True`），这是动画一等公民的前提；GitHub 风格表格/代码围栏齐全。
- **代码高亮：Pygments**（构建期渲染成带 class 的 span，运行时零开销；深浅主题各一套 Pygments 样式表）。
- **搜索：构建期生成 `search-index.json`**，前端用极简倒排/子串匹配（数百篇规模无需 lunr/flexsearch 这种重库；够用即可，后期再升级）。
- **数学公式（若需要）**：留 KaTeX 接入点，默认不启用。

依赖控制：只新增 `markdown-it-py`、`mdit-py-plugins`、`Pygments` 三个纯 Python 包，放进一个独立 `docs-build` 可选依赖组，不污染主依赖。

## 四、目录与产物布局

```
docs/                         ← 源文件（不动）
  design/runtime/dag/dag-rendering.md
  design/proactive/event-layer.html   ← 手写 html
  ...

tools/docs_site/              ← 新增：构建脚本（一个小模块）
  build.py                    入口：扫描 docs/ → 渲染 → 写 _site/
  template.py                 html 外壳模板（壳 + 注入点）
  nav.py                      从目录树 + README 生成导航数据
  search.py                   生成 search-index.json
  assets/
    site.css                  全站样式 + 深浅双主题变量
    site.js                   主题切换 + 锚点高亮 + 搜索 + 移动端抽屉
    pygments-light.css
    pygments-dark.css

docs/_site/                   ← 构建产物（git 忽略或按需提交）
  index.html
  design/runtime/dag/dag-rendering.html
  search-index.json
  assets/...
```

构建命令：`python -m tools.docs_site.build`（可加 `--watch` 后期再做）。

## 五、页面骨架（三栏）

```
┌────────────────────────────────────────────────────────────┐
│  OpenProgram Docs            [🔍 搜索 ⌘K]        [☀/🌙]      │  顶栏 固定
├──────────────┬───────────────────────────────┬─────────────┤
│ 目录树        │  # 页面标题                    │ On this page │
│  Design       │  Status: draft                 │  · 一、目标  │
│   Runtime     │  正文…                         │  · 二、…     │
│    > 当前页   │  ```code```                    │  · 三、…     │
│   Providers   │  <canvas> 动画原样透传         │             │
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

配色基调（待你确认，先给默认）：

| 角色 | 浅色 | 深色 |
|---|---|---|
| 背景 | `#ffffff` / 侧栏 `#f7f7f5` | `#16181d` / 侧栏 `#1b1e24` |
| 正文 | `#1f2328` | `#d8dae0` |
| 次要文字 | `#656d76` | `#8b929c` |
| 强调色 | `#3b82f6`（蓝） | `#5aa2ff` |
| 代码底 | `#f6f8fa` | `#21262d` |
| 边框 | `#d0d7de` | `#30363d` |

风格基调：浅色为主、对齐 Stripe/Vercel/Linear 那类技术文档的克制专业感；深色不是纯黑，避免刺眼。

## 七、动态动画一等公民（关键设计）

这是与普通文档站的最大差异点，单独说明落地机制：

1. **md 内嵌透传**：`markdown-it-py` 开 `html=True`，md 里写的 `<canvas>`、`<svg>`、`<script>`、`<style>` 块原样进入产物，不被转义。作者想给某篇加交互 demo，直接在该 md 内写即可。
2. **页面级附加资源**：约定 md 文件可在 frontmatter 声明 `scripts: [foo.js]` / `styles: [foo.css]`，构建期把这些文件拷到产物并在该页注入 `<script>/<link>`。复杂动画拆成独立 js，不污染正文。
3. **手写 html 的处理（保留内容嵌入新壳）**：11 篇手写 html 走专门管道 —— 提取其 `<body>` 内容 + 收集其 `<style>`（加页面级作用域前缀避免与全站样式冲突），整块塞进统一外壳的正文区，原图表/动效保留。其自带的 `<script>` 一并保留。这条管道单独实现，逐篇验证视觉无回归。
4. **主题感知动画（可选后期）**：暴露一个全局事件 `documentThemeChange`，动画脚本可监听以适配深浅。首版不强制。

## 八、导航生成规则

- 扫描 `docs/` 下所有 `*.md` 与 11 篇手写 `*.html`。
- 目录即分组：`docs/design/runtime/` → 分组 "Runtime"，组标题优先取该目录 `README.md` 的一级标题，无则用目录名美化。
- 组内排序：`README.md` 置顶，其余按文件名；后期可支持 frontmatter `order`。
- 排除：`docs/_site/`、`docs/images/`、`docs/slides/`、`*/archive/`（归档默认折叠或排除，待确认）。
- 顶层散页（`docs/*.md` 如 GETTING_STARTED、install）归入 "Guides" 组。

## 九、实施步骤（每步可独立验证）

1. **脚手架 + 单页渲染** → verify：跑 `build.py`，`dag-rendering.md` 生成正确 html，标题/代码/表格无误。
2. **三栏外壳 + 双主题** → verify：浏览器打开，切换深浅正常、防闪烁、排版克制专业。
3. **导航树自动生成** → verify：左栏完整覆盖所有 md，分组/置顶/当前页高亮正确。
4. **本页锚点 + 滚动高亮** → verify：右栏锚点点击跳转、滚动时高亮跟随。
5. **搜索** → verify：输入关键词命中标题/正文，跳转正确。
6. **手写 html 嵌入管道** → verify：11 篇逐篇打开，图表/动效保留，样式不污染全站。
7. **全量构建 + 自检** → verify：154 篇全部生成无报错，抽查 5–8 篇（含含图表页、含表格页、深浅各一遍）。

每步完成即 commit（遵循 main 直接提交）。

## 十、开放项（已确认）

1. **配色** ✅：由实现方按"护眼"自定（浅色不刺眼、深色非纯黑）。
2. **archive 目录** ✅：直接删除，不进站点。已删 `docs/archive/`、`docs/design/archive/`、`docs/design/proactive/_research_archive/` 共 18 文件。
3. **产物入库** ✅：`docs/_site/` 提交进 git。
4. **托管方式** ✅：做到最优 —— 接进 worker 单端口路由 `/docs`。
