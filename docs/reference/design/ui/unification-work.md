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

## Completed Work (This PR)

### Settings UX Fix

**Problem:** Settings defaulted to LLM Providers tab instead of General. Appearance was a flat list mixing mode (auto/light/dark) with color styles (beige/neutral/aurora).

**Solution (schema 3):**
- Settings now opens to General first
- Appearance split into two independent controls:
  - **Mode**: Auto / Light / Dark (system-following auto is a mode, not a theme)
  - **Color style**: Beige / Neutral / Aurora / Custom
- Each color style now supports both light and dark modes (added `aurora-light.css`)
- Storage migrated from single `agentic_theme` (schema 2) to `agentic_theme_style` + `agentic_theme_mode` (schema 3)
- Theme contract maintained: 6 built-in themes × 58 tokens each

**Files changed:**
- `apps/web/app/(shell)/settings/page.tsx` — redirect to `/settings/general`
- `apps/web/lib/prefs/theme-config.ts` — new file, defines mode × style matrix
- `apps/web/lib/prefs/theme-pref.ts` — refactored to use `style` + `mode` instead of combined `theme`
- `apps/web/lib/prefs/theme-bootstrap.ts` — first-paint script with schema 3 migration
- `apps/web/app/layout.tsx` — uses `THEME_BOOTSTRAP_SCRIPT` for pre-hydration theme
- `apps/web/components/settings/general-section.tsx` — two separate controls (Mode + Color style)
- `apps/web/app/styles/themes/aurora-light.css` — new light variant of aurora theme
- `apps/web/app/globals.css` — imports aurora-light
- `apps/web/scripts/check-theme-contract.mjs` — updated for schema 3
- `apps/desktop/main.js` — MENU_THEME_IDS includes aurora-light

**Migration:** Existing users on schema 2 (combined theme) are automatically migrated to schema 3 (style + mode) on first page load. Legacy `agentic_theme` values map as follows:
- `auto` → beige + auto
- `beige-dark` / `beige-light` → beige + dark/light
- `dark` / `light` (schema 2) → neutral + dark/light
- `aurora` → aurora + dark
- `custom` → custom + dark

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

### 5. Desktop Chrome Hardcoded Colors (Medium Priority)

**Issue:** Electron window chrome doesn't follow theme preference:

**Files:**
- `apps/desktop/main.js` — `backgroundColor: "#141416"` (hardcoded cold gray)
- Worker error page and embedded file-list HTML have their own light/dark hexes

**Symptom:** Cold gray flash before cream theme loads.

**Action:** Read theme preference from storage and apply background color before window shows.

Window-state persistence (normal bounds vs maximize/fullscreen, display fallback, titlebar resize hit-testing) is implemented; see [window-state.md](window-state.md). The cold chrome flash above is still open.

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
3. **Desktop chrome color flash** — respect theme preference, eliminate cold flash. Window-state restore is already in place.
4. **CLI/TUI colors** — either integrate with Web theme or document separate brand
5. **Ghost tokens** — add to contract or remove from code/specs
6. **Marketing/icon** — decide alignment or independence
7. **Button/naming debt** — reconcile heights, radii, and token names
8. **Component kit** — converge on Radix + Tailwind v4

---

## Implementation Status

- **Settings mode × style**: ✅ Complete (this PR)
- **Web token cleanup**: ❌ Not started
- **Docs palette**: ❌ Not started
- **Desktop window-state**: ✅ Complete
- **Desktop chrome color flash**: ❌ Not started
- **CLI/TUI colors**: ❌ Not started
- **Ghost tokens**: ❌ Not started
- **Marketing/icon**: ❌ Not started
- **Button/naming debt**: ❌ Not started
- **Component kit**: ❌ Not started
