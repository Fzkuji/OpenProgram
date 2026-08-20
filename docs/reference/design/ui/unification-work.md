# UI Unification Work

This document records UI inconsistencies across OpenProgram surfaces and outlines prioritized work for a unified visual identity.

## Context

OpenProgram consists of multiple UI surfaces:
- **Web App** (`apps/web/`) — the main browser-based interface
- **CLI/TUI** (`apps/cli/`) — terminal interface
- **Docs Site** (`scripts/docs_site/`) — documentation website
- **Marketing Page** (`website/index.html`) — landing page
- **Desktop Chrome** (`apps/desktop/main.js`) — Electron window chrome

Each surface evolved independently, resulting in divergent color palettes, typography, and design tokens.

## Current Appearance

Settings opens on General. Appearance follows an Obsidian-like stack and keeps schema 3 (mode × named package):

- **Mode**: Auto / Light / Dark. Auto follows the system; it is a mode, not a theme.
- **Theme package**: Beige / Neutral / Aurora only. Each package has a light and a dark `data-theme`. Custom is not a fourth skin.
- **Accent color**: a color input, presets taken from each package's default `--accent-orange`, and Reset to theme default. Empty/default uses the current package accent. The override writes `--accent-orange`, `--accent-fill`, and a derived `--accent-orange-hover`.
- **CSS overlay**: user CSS sits on top of the selected package, gated by Enable custom CSS. Insert template writes a starter that targets `html` / current `data-theme` values. Clear empties the snippet. Old `[data-theme="custom"]` / `[data-theme="custom-light"]` selectors are rewritten onto `data-custom-css` so they keep applying.

Storage stays schema 3 (`agentic_theme_style` + `agentic_theme_mode`). Accent uses `agentic_theme_accent`. Overlay enable uses `agentic_custom_css_enabled`. Combined `agentic_theme` values still migrate:

- `auto` → beige + auto
- `beige-dark` / `beige-light` → beige + dark/light
- `dark` / `light` (schema 2) → neutral + dark/light
- `aurora` / `aurora-light` → aurora + dark/light
- `custom` / `custom-light` → beige + the saved mode; overlay turns on when any custom CSS is saved

`custom` / `custom-light` remain fallback theme ids and CSS files for old snippets. They do not appear as style cards. The theme contract still validates the six built-in package token files; default accents stay sourced from those CSS files.

## Later Work (Prioritized TODO)

The following inconsistencies remain and should be addressed in future PRs.

### 1. Web Token Cleanup (High Priority)

**Issue:** `:root` fallback in `base.css` mixes beige surfaces with neutral blue accents.

```css
/* base.css :root claims to be beige-dark but has: */
--accent-orange: #6ea8fe;  /* This is neutral blue, not beige coral */
```

**Files:**
- `apps/web/app/styles/base.css` — fallback palette should be consistent beige-dark

**Action:** Decide whether `:root` fallback should be beige or neutral, then reconcile accent colors.

---

### 2. Docs Site Palette Drift (High Priority)

**Issue:** Docs site (`scripts/docs_site/assets/site.css`) claims to follow `base.css` but values diverge:

| Token | Docs light | Web beige-light | Docs dark | Web beige-dark |
|-------|------------|-----------------|-----------|----------------|
| `--acc` / `--accent-orange` | `#b8651f` | `#c15f3c` | `#d19a66` | `#d97757` |
| `--bg` / `--bg-primary` | — | — | `#1f1f1e` | `#262624` |

**Files:**
- `scripts/docs_site/assets/site.css`
- Storage key is `op-docs-theme` (light/dark only), independent of app theme

**Additional drift:**
- Docs body type is 15.5px vs app `--fs-base: 14px`

**Action:** Align docs palette with Web's beige theme, or explicitly document that docs site is independent.

---

### 3. CLI/TUI Theme Mismatch (Medium Priority)

**Issue:** CLI/TUI always uses Claude orange `#d97757` regardless of Web theme. Python/Rich setup wizard doesn't read Web tokens.

**Files:**
- `apps/cli/src/theme/themes.ts` — TypeScript theme definitions
- `apps/cli/python/openprogram_cli/_impl/repl/banner.py` — Rich colors (bright_blue, rainbow)
- Setup wizard comments mention "OpenClaw-style"

**Action:** Either:
- Make CLI/TUI read Web's theme preference and apply consistent colors, or
- Document that CLI is intentionally a separate brand

---

### 4. Marketing Page Independent Brand (Low Priority)

**Issue:** Marketing page (`website/index.html`) has a third brand:

```css
--bg: #07080a
--teal: #5eead4
--violet: #a78bfa
```

Desktop icon SVG uses blue/purple (`#4A9FE1` / `#915FD5`).

**Action:** Decide whether marketing page should align with Web beige/neutral or remain independent.

---

### 5. Desktop Chrome Follows the Resolved Theme

The first pixel of the Electron window matches the already resolved web theme. `BrowserWindow` is created with `--bg-primary` from that theme — not `#141416`.

Resolution uses the same schema-3 keys the web app writes (`agentic_theme_style` + `agentic_theme_mode`, plus legacy `agentic_theme`). Desktop reads Chromium localStorage under userData, then a `theme-prefs.json` cache written on theme change. `auto` follows `nativeTheme.shouldUseDarkColors`.

`apps/desktop/theme-chrome.js` holds the `--bg-primary` / `--accent-orange` map copied from `apps/web/app/styles/themes/*.css`. Stored `custom` / `custom-light` resolve to the beige pair (`#262624` / `#faf9f5`). Worker error pages and directory-listing HTML use the same chrome tokens; when an accent override is set, listing/error link color uses that accent, while window `backgroundColor` stays the package `--bg-primary`. After a theme change the renderer calls `theme.setChrome`, which updates every `BrowserWindow` background so the next show/reload does not flash the old color.

Window-state persistence (normal bounds vs maximize/fullscreen, display fallback, titlebar resize hit-testing) is unchanged; see [window-state.md](window-state.md).

---

### 6. Ghost Tokens / Spec Drift (Low Priority)

**Issue:** Tokens used in code but not in the 58-token contract, or documented in specs but not implemented:

**Used but not contracted:**
- `--text-dim` (used in manage-page, files-panel, plugins, skills; contract has `--text-muted`)

**Documented but not implemented:**
- `--bg-surface` (mentioned in `surface-system.md`)
- `--text-on-accent` (mentioned in specs)

**Files:**
- `docs/reference/design/ui/surface-system.md`

**Action:** Either add missing tokens to contract or remove references.

---

### 7. Button Spec Drift (Low Priority)

**Issue:** `button.tsx` hover uses `bg-secondary` instead of brand fill. Comments mention 36px / `--ui-button-h` but `base.css` is 30px. Manage-page tabs use `border-radius: 6px` vs `--ui-button-radius` 10px.

**Action:** Reconcile button heights and radii across components.

---

### 8. Naming Debt (Low Priority)

**Issue:**
- `--accent-orange` used for blue/teal themes (confusing name)
- shadcn `--accent` means `--bg-hover`, not primary color
- Stale comments in `dark.css` / `aurora.css` still mention "coral"

**Action:** Rename `--accent-orange` → `--accent-primary` or similar. Update stale comments.

---

### 9. Mixed Component Kits (Low Priority)

**Issue:**
- Composer menus use Base UI
- Rest of app uses Radix/shadcn
- Tailwind v3 config (`tailwind.config.ts`) and v4 `@theme` both exist
- Effort color logic (`effort-color.ts`) uses independent HSL, not theme accent
- Browser glyphs (`center-tabs.module.css`) hardcode `#8b5cf6`

**Action:** Converge on one component library (Radix) and one Tailwind version (v4).

---

## Stale Branch Note

`origin/codex/theme-style-mode` started mode × style separation under the old `web/` tree. This PR supersedes that branch; it can be archived.

---

## Prioritized Roadmap

1. **Web token cleanup** — fix `:root` fallback, ensure beige-dark is consistent default
2. **Docs palette** — align with Web beige or document independence
3. **CLI/TUI colors** — either integrate with Web theme or document separate brand
4. **Ghost tokens** — add to contract or remove from code/specs
5. **Marketing/icon** — decide alignment or independence
6. **Button/naming debt** — reconcile heights, radii, and token names
7. **Component kit** — converge on Radix + Tailwind v4

---

## Implementation Status

- **Settings Appearance**: ✅ Complete (Mode × Beige/Neutral/Aurora × accent picker × CSS overlay; stored Custom migrates to beige)
- **Web token cleanup**: ❌ Not started
- **Docs palette**: ❌ Not started
- **Desktop window-state**: ✅ Complete
- **Desktop chrome color flash**: ✅ Complete (first pixel and chrome HTML follow resolved `--bg-primary`)
- **CLI/TUI colors**: ❌ Not started
- **Ghost tokens**: ❌ Not started
- **Marketing/icon**: ❌ Not started
- **Button/naming debt**: ❌ Not started
- **Component kit**: ❌ Not started
