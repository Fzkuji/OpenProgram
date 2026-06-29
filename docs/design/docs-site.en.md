# Design Docs Site (Unified Documentation Website)

Status: **draft (pending confirmation)** · Created: 2026-06-29

> Unify the 154 markdown files + 11 hand-written html files under `docs/` into a single static documentation site with one consistent style:
> left-side directory tree · top search bar · right-side per-page anchors · light/dark dual themes. Change the skin in one place and the whole site stays consistent.
> At the same time, treat "being able to freely add dynamic animations later" as a first-class concern.

## 1. Goals and Non-Goals

### Goals

1. **One shell, consistent across the site**: navigation, color scheme, typography, and code-block styling are defined once and reused by every doc.
2. **Zero runtime framework**: the output is pure static html/css/js, which can be served directly by the worker (single-port route) or any static server, without introducing a Vite/Vue/React runtime.
3. **Light/dark dual themes**: one set of CSS variables driving two color schemes, following the system + manual toggle + remembered preference (localStorage).
4. **Automatic navigation**: the left-side directory tree is generated automatically from the `docs/` directory structure; group titles come from the top-level heading of each level's `README.md`.
5. **Per-page anchors**: the right-side "On this page" is generated automatically from each doc's `##/###` headings, highlighting the current section while scrolling.
6. **Full-text search**: a lightweight search index (titles + body) is generated at build time, with pure-JS search on the front end and no backend.
7. **Dynamic animations as a first-class concern**: `<script>/<canvas>/<svg>/<style>` embedded in md is passed through verbatim; charts from the 11 hand-written html files can be embedded into the new shell as whole blocks without loss.

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

Rationale for the decision: the user's core requirement is a "unified docs site" **and** "freely add dynamic animations later." A framework approach either limits animations (MkDocs) or forces a migration into a component system (VitePress). The existing 11 hand-written html files already contain custom charts/animations — which itself proves that what's needed is "a shell that lets you freely write native html/js." Writing the sidebar/search/anchors ourselves once, in exchange for not being blocked by a framework in the second phase, is worth it.

## 3. Technology Choices

- **Build language: Python**. The repo's primary language is Python, the worker is already Python, and there's no need to add a Node toolchain.
- **Markdown rendering: `markdown-it-py`** + plugins (`mdit-py-plugins`: anchors, footnote, deflist, tasklists). Reason: it preserves raw html passthrough (`html=True`), which is the prerequisite for animations as a first-class concern; GitHub-flavored tables/code fences are fully supported.
- **Code highlighting: Pygments** (rendered at build time into class-tagged spans, with zero runtime cost; one Pygments stylesheet for each of the light/dark themes).
- **Search: generate `search-index.json` at build time**, with a minimal inverted-index/substring match on the front end (a corpus of a few hundred docs doesn't need a heavy library like lunr/flexsearch; good enough for now, can be upgraded later).
- **Math formulas (if needed)**: leave a KaTeX integration point, disabled by default.

Dependency control: only three pure-Python packages are added — `markdown-it-py`, `mdit-py-plugins`, `Pygments` — placed in a separate optional `docs-build` dependency group so the main dependencies aren't polluted.

## 4. Directory and Output Layout

```
docs/                         ← source files (untouched)
  design/runtime/dag-viewport.md
  design/proactive/event-layer.html   ← hand-written html
  ...

tools/docs_site/              ← new: build script (one small module)
  build.py                    entry point: scan docs/ → render → write _site/
  template.py                 html shell template (shell + injection points)
  nav.py                      generate navigation data from directory tree + README
  search.py                   generate search-index.json
  assets/
    site.css                  site-wide styles + light/dark dual-theme variables
    site.js                   theme toggle + anchor highlighting + search + mobile drawer
    pygments-light.css
    pygments-dark.css

docs/_site/                   ← build output (git-ignored or committed as needed)
  index.html
  design/runtime/dag-viewport.html
  search-index.json
  assets/...
```

Build command: `python -m tools.docs_site.build` (a `--watch` mode can be added later).

## 5. Page Skeleton (Three Columns)

```
┌────────────────────────────────────────────────────────────┐
│  OpenProgram Docs            [🔍 search ⌘K]      [☀/🌙]      │  top bar, fixed
├──────────────┬───────────────────────────────┬─────────────┤
│ dir tree      │  # page title                  │ On this page │
│  Design       │  Status: draft                 │  · 1. Goals  │
│   Runtime     │  body…                         │  · 2. …      │
│    > current  │  ```code```                    │  · 3. …      │
│   Providers   │  <canvas> animation passthrough│             │
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

Color palette baseline (pending your confirmation; defaults given first):

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
3. **Handling hand-written html (preserving content embedded into the new shell)**: the 11 hand-written html files go through a dedicated pipeline — extract their `<body>` content + collect their `<style>` (adding a page-level scoping prefix to avoid conflicts with site-wide styles), and stuff the whole thing into the body area of the unified shell, preserving the original charts/animations. Their own `<script>` is preserved as well. This pipeline is implemented separately and verified file by file for no visual regression.
4. **Theme-aware animations (optional, later)**: expose a global `documentThemeChange` event that animation scripts can listen to in order to adapt to light/dark. Not enforced in the first version.

## 8. Navigation Generation Rules

- Scan all `*.md` under `docs/` plus the 11 hand-written `*.html` files.
- Directory = group: `docs/design/runtime/` → group "Runtime"; the group title prefers the top-level heading of that directory's `README.md`, falling back to a prettified directory name if absent.
- Ordering within a group: `README.md` first, the rest by filename; a frontmatter `order` can be supported later.
- Exclusions: `docs/_site/`, `docs/images/`, `docs/slides/`, `*/archive/` (archives collapsed or excluded by default, pending confirmation).
- Top-level loose pages (`docs/*.md` such as GETTING_STARTED, install) go into the "Guides" group.

## 9. Implementation Steps (Each Independently Verifiable)

1. **Scaffolding + single-page rendering** → verify: run `build.py`, confirm `dag-viewport.md` produces correct html with titles/code/tables intact.
2. **Three-column shell + dual themes** → verify: open in a browser, confirm light/dark switching works, anti-flicker works, and typography is restrained and professional.
3. **Automatic navigation tree** → verify: the left column fully covers all md, with correct grouping/pinning/current-page highlighting.
4. **Per-page anchors + scroll highlighting** → verify: right-column anchors jump on click, and highlighting follows while scrolling.
5. **Search** → verify: entering a keyword hits titles/body and jumps correctly.
6. **Hand-written html embedding pipeline** → verify: open all 11 files one by one, confirm charts/animations are preserved and styles don't pollute the rest of the site.
7. **Full build + self-check** → verify: all 154 files generate without errors; spot-check 5–8 of them (including a page with charts, a page with tables, once each in light and dark).

Commit at the end of each step (following the commit-directly-to-main convention).

## 10. Open Items (Confirmed)

1. **Color scheme** ✅: chosen by the implementer for "eye comfort" (light not glaring, dark not pure black).
2. **archive directory** ✅: delete outright, don't include in the site. Already deleted `docs/archive/`, `docs/design/archive/`, `docs/design/proactive/_research_archive/`, 18 files in total.
3. **Committing output** ✅: `docs/_site/` is committed into git.
4. **Hosting** ✅: do it the optimal way — wire it into the worker's single-port route `/docs`.
