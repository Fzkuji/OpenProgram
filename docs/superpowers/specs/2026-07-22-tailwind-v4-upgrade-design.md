# Tailwind CSS v3 → v4 升级

**日期**: 2026-07-22
**范围**: `web/` 前端
**目标**: 升级到 Tailwind v4,保留全站视觉零变化,着眼未来。

## 背景

菜单阴影 bug 暴露了一个语法陷阱:项目跑在 Tailwind **v3**,但代码里零星写了 v4-only 的圆括号任意值语法 `shadow-(--shadow-popover)`,在 v3 下被静默丢弃 → `box-shadow: none` → 菜单全无阴影(context 面板用内联 style 幸免)。已用方括号 `shadow-[var(...)]` 修复。

借此升到 v4:v4 是官方新默认(cal.com、Supabase 等活跃项目已迁),圆括号语法原生可用,面向未来。

## 现状调研

- **版本**: `tailwindcss ^3.4.1`,`postcss ^8`,插件 `tailwindcss-animate ^1.0.7`。
- **配置**: `web/tailwind.config.ts`(139 行)—— 把 `app/styles/base.css` 的所有 CSS 变量 token 映射成 utility 别名(颜色 `text-bright`/`bg-hover`/`accent-*`、圆角、字号 `fs-*`、间距 `composer-*`/`sidebar-w`、动画 accordion/spin-refresh)。
- **入口**: `web/app/globals.css` —— `@tailwind base/components/utilities` 三行 + `@import` 字体 + shadcn token bridge (`:root`)。
- **`@apply` 用量**: **0 处**(省掉 v4 最大迁移坑)。
- **shadcn/ui**: 组件源码复制进 `components/ui/*.tsx`,非依赖,v4 兼容,不存在版本冲突。
- **暗色模式**: `darkMode: ["class"]`。

## 迁移步骤

### 1. 依赖
- `tailwindcss`: `^3.4.1` → `^4`
- 新增 `@tailwindcss/postcss`
- `tailwindcss-animate ^1.0.7` → `tw-animate-css`(v4 生态等价物,承载 accordion 动画)

### 2. `postcss.config.mjs`
`tailwindcss: {}` → `"@tailwindcss/postcss": {}`(v4 把 PostCSS 插件独立成包)。

### 3. `app/globals.css` 入口
- `@tailwind base; @tailwind components; @tailwind utilities;` → `@import "tailwindcss";`
- 动画插件: `@import "tw-animate-css";`
- 暗色模式: 加 `@custom-variant dark (&:where(.dark, .dark *));`(替代 config 的 `darkMode: ["class"]`)

### 4. config → CSS `@theme`
把 `tailwind.config.ts` 里 `theme.extend` 的全部 token 别名翻译进 globals.css 的 `@theme` 块:
- 颜色 → `--color-*`(如 `--color-text-bright: var(--text-bright)`)
- 圆角 → `--radius-*`
- 字号 → `--text-*`(注意 v4 命名)
- 间距 → `--spacing-*`
- 动画/keyframes → `@theme` 内 `--animate-*` + `@keyframes`

删除 `tailwind.config.ts`(v4 无需 JS config;`content` 自动检测)。**这步量最大、最需逐条核对别名不漏。**

### 5. Breaking-change 回归
v4 改了若干默认值,全站排查并补回原视觉:
- **默认边框色**: v3=`gray-200`,v4=`currentColor`。凡是只写 `border` 不带颜色的地方需显式给色。
- **`ring` 默认**: 宽度 3px→1px、色从 blue→currentColor。检查 focus ring。
- **`shadow`/`rounded`/`blur` 默认刻度重命名**: `shadow-sm`→`shadow-xs` 等。grep 排查裸用。
- **`space-*` / 预设 `outline`** 行为微调。

### 6. 收尾
- slider/kbd 等圆括号语法保留(v4 原生支持)。
- `tsc --noEmit` + `next build` 通过。
- **视觉回归**: CDP 逐个读关键面板(菜单 MENU_PANEL、context 面板、composer、sidebar、tooltip、effort 卡)的 computed 样式,和升级前基线对比,确保零偏差。

## 风险

集中在步骤 4、5。token 搬家漏一个名字、或默认值变化未补回 → 零星样式偏差。缓解:逐条核对 + CDP computed 样式对比 + 逐屏人工回归。

## 验证标准

- `next build` exit 0。
- 关键面板 computed 样式与升级前一致(阴影、边框、圆角、字号、间距)。
- 全站深/浅两主题人工过一遍无可见偏差。
- dev 18200 实测。
