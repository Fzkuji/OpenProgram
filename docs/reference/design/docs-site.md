# Design Docs Site (Unified Documentation Website)

> The markdown files and hand-written html files under `docs/` are served as a single static documentation site with one consistent style:
> left-side directory tree · top search bar · right-side per-page anchors · light/dark dual themes. Changing the skin in one place keeps the whole site consistent.
> Embedded dynamic animations render verbatim — they are part of the page content, not an afterthought.

## 1. Goals and Non-Goals

### Goals

1. **One shell, consistent across the site**: navigation, color scheme, typography, and code-block styling are defined once and reused by every doc.
2. **Zero runtime framework**: the output is pure static html/css/js, which can be served directly by the worker (single-port route) or any static server, without introducing a Vite/Vue/React runtime.
3. **Light/dark dual themes**: one set of CSS variables driving two color schemes, following the system + manual toggle + remembered preference (localStorage).
4. **Automatic navigation**: the left-side directory tree is generated automatically from the `docs/` directory structure; group titles come from the top-level heading of each level's `README.md`.
5. **Per-page anchors**: the right-side "On this page" is generated automatically from each doc's `##/###` headings, highlighting the current section while scrolling.
6. **Full-text search**: a lightweight search index (titles + body) is generated at build time, with pure-JS search on the front end and no backend.
7. **Dynamic animations render verbatim**: `<script>/<canvas>/<svg>/<style>` embedded in md is passed through verbatim; charts from the 11 hand-written html files can be embedded into the new shell as whole blocks without loss.

### Non-Goals

- No editor / CMS; the docs are still authoritative as source files (md/html), and the site is a read-only output.
- No multi-language switching framework (the docs themselves mix Chinese and English; i18n is not enforced).
- No replacement of `docs/slides/` (slides are a separate format and stay as-is).

## 2. Why a Custom Script Instead of VitePress / MkDocs

| Dimension | Custom script | VitePress | MkDocs Material |
|---|---|---|---|
| Adding custom dynamic animations later | **Highest**: templates/CSS/JS are all our own, native html/js written directly | High, but must be written as Vue components | Low, the theme is closed and fights with raw html |
| Runtime dependencies | None (pure static) | Vite/Vue | None (but heavy at build time) |
| Consistency with the single-port hosting route | Yes | Needs extra build-output integration | Yes |
| Embedding the 11 hand-written html files | Direct passthrough | Must be rewritten as components | Hard |
| Cost of standard features (sidebar/search/anchors) | Write it once yourself | Out of the box | Out of the box |

The requirement is a unified docs site **and** the freedom to add dynamic animations. A framework either limits animations (MkDocs) or forces a migration into a component system (VitePress). The hand-written html files already contain custom charts and animations, so what the site needs is a shell that allows native html/js. Writing the sidebar, search, and anchors once buys that freedom permanently.

## 3. Technology Choices

- **Build language: Python**. The repo's primary language is Python, the worker is already Python, and there's no need to add a Node toolchain.
- **Markdown rendering: `markdown-it-py`** + plugins (`mdit-py-plugins`: anchors, footnote, deflist, tasklists). Reason: it preserves raw html passthrough (`html=True`), which is what lets embedded animations render at all; GitHub-flavored tables/code fences are fully supported.
- **Code highlighting: Pygments** (rendered at build time into class-tagged spans, with zero runtime cost; one Pygments stylesheet for each of the light/dark themes).
- **Search: generate `search-index.json` at build time**, with a minimal inverted-index/substring match on the front end. A corpus of a few hundred docs does not need a heavy library like lunr or flexsearch.
- **Math formulas**: a KaTeX integration point exists, disabled by default.

Dependency control: only three pure-Python packages are added — `markdown-it-py`, `mdit-py-plugins`, `Pygments` — placed in a separate optional `docs-build` dependency group so the main dependencies aren't polluted.

## 4. Directory and Output Layout

```
docs/                         ← source files (untouched)
  design/runtime/dag/rendering.md
  design/proactive/event-layer.html   ← hand-written html
  ...

scripts/docs_site/              ← new: build script (one small module)
  build.py                    entry point: scan docs/ → render → write _site/
  template.py                 html shell template (shell + injection points)
  nav.py                      generate navigation data from directory tree + README
  search.py                   generate search-index.json
  assets/
    site.css                  site-wide styles + light/dark dual-theme variables
    site.js                   theme toggle + anchor highlighting + search + mobile drawer
    pygments-light.css
    pygments-dark.css

docs/_site/                   ← build output
  index.html
  design/runtime/dag/rendering.html
  search-index.json
  assets/...
```

Build command: `python -m scripts.docs_site.build`.

## 5. Page Skeleton (Three Columns)

```
┌────────────────────────────────────────────────────────────┐
│  OpenProgram Docs            [🔍 search ⌘K]      [☀/🌙]      │  top bar, fixed
├──────────────┬───────────────────────────────┬─────────────┤
│ dir tree      │  # page title                  │ On this page │
│  Design       │  body…                         │  · 1. Goals  │
│   Runtime     │  ```code```                    │  · 2. …      │
│    > current  │  <canvas> animation passthrough│  · 3. …      │
│   Context     │                                │ highlight    │
│ (collapsible) │                                │ current sec. │
└──────────────┴───────────────────────────────┴─────────────┘
left col collapsible/remembered expand state   body max-width≈820px   right col hidden on narrow screens
```

Narrow screens (< 900px): the left column collapses into a drawer (toggled by a hamburger button), and the right column is hidden.

## 6. Light/Dark Dual Themes

One set of CSS variables, with `:root` as the light default and `[data-theme="dark"]` overriding it for dark. Toggle logic:

1. On first visit, read `prefers-color-scheme` to follow the system.
2. User clicks toggle → write `localStorage.theme` → set `<html data-theme>`.
3. Anti-flicker: inline a small synchronous script in `<head>` that fixes the theme before the DOM renders.

Color palette:

| Role | Light | Dark |
|---|---|---|
| Background | `#ffffff` / sidebar `#f7f7f5` | `#16181d` / sidebar `#1b1e24` |
| Body text | `#1f2328` | `#d8dae0` |
| Secondary text | `#656d76` | `#8b929c` |
| Accent | `#3b82f6` (blue) | `#5aa2ff` |
| Code background | `#f6f8fa` | `#21262d` |
| Border | `#d0d7de` | `#30363d` |

Style baseline: light-first, aligned with the restrained, professional feel of technical docs like Stripe/Vercel/Linear; dark is not pure black, to avoid eye strain.

## 7. Dynamic Animations as a First-Class Concern (Key Design)

This is the biggest difference from an ordinary docs site, so the implementation mechanism is described separately:

1. **md embedded passthrough**: `markdown-it-py` runs with `html=True`, so `<canvas>`, `<svg>`, `<script>`, `<style>` blocks written in md go into the output verbatim, without being escaped. If an author wants to add an interactive demo to a given doc, they just write it inside that md.
2. **Page-level extra resources**: by convention, an md file can declare `scripts: [foo.js]` / `styles: [foo.css]` in its frontmatter; at build time these files are copied to the output and `<script>/<link>` tags are injected into that page. Complex animations are split into separate js so the body isn't polluted.
3. **Handling hand-written html (preserving content embedded into the new shell)**: the 11 hand-written html files go through a dedicated pipeline — extract their `<body>` content + collect their `<style>` (adding a page-level scoping prefix to avoid conflicts with site-wide styles), and stuff the whole thing into the body area of the unified shell, preserving the original charts/animations. Their own `<script>` is preserved as well. This pipeline is separate from the markdown one and each file is verified for no visual regression.
4. **Theme-aware animations**: a global `documentThemeChange` event lets animation scripts adapt to light/dark. Listening is optional.

## 8. Navigation Generation Rules

- Scan all `*.md` under `docs/` plus the 11 hand-written `*.html` files.
- Directory = group: `docs/reference/design/runtime/` → group "Runtime"; the group title prefers the top-level heading of that directory's `README.md`, falling back to a prettified directory name if absent.
- Ordering within a group: `README.md` first, the rest by filename.
- Exclusions: `docs/_site/`, `docs/images/`, `docs/slides/`, and directories whose name starts with an underscore.
- Top-level loose pages (`docs/*.md` such as GETTING_STARTED, install) go into the "Guides" group.

## 9. Hosting and Output

The build output `docs/_site/` is committed to git and served by the worker's single-port route `/docs`, so the docs need no separate server or deployment step.

## Appendix: Implementation Status

The site is built and served. Two residual items:

- `docs/reference/design/proactive/_research_archive/` still holds three files (`evaluation.md`, `replay.md`, `threat-model.md`). They are excluded from the site by the leading-underscore rule rather than by deletion.
- The build has no `--watch` mode; every rebuild is a full run of `python -m scripts.docs_site.build`.
