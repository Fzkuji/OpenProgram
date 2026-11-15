# Image Sources

This directory keeps the editable image sources used by documentation and the WebUI. Runtime copies live under `apps/web/` and end up in the static export (`apps/web/out/`) the worker serves.

## App Icon

The mark is an indigo tile holding a lavender cell with three flat discs. The
[App icon specification](../reference/design/ui/desktop/app-icon.html) defines its
geometry, colours and every output.

Canonical sources:

- `docs/images/openprogram-app-icon.svg`: the flat macOS tile on the Big Sur grid
  (an 824 px tile at offset 100 on a 1024 canvas, outlined with Apple's continuous
  corner: radius 185.4 px, 60% corner smoothing, the same outline as the system icons). `apps/desktop/build/icon.icns`
  is rendered from it.
- `apps/desktop/build/AppIcon.icon`: the same artwork as four Icon Composer layers
  (cell, indigo disc, violet disc, sky disc).

## WebUI Tab Icon

Canonical source:

- `docs/images/openprogram-tab-icon.source.svg`

Runtime files:

- `apps/web/app/icon.svg`
- `apps/web/app/favicon.ico`
- `scripts/docs_site/assets/mark.svg`
- `apps/web/public/icons/icon-192.png`, `icon-512.png`, `icon-512-maskable.png`

Code reference:

- `apps/web/app/layout.tsx`
- `apps/web/app/manifest.ts`

Current design:

- 64 x 64 SVG: the App icon mark on a rounded tile that fills the canvas.

Sync rule:

- Edit `docs/images/openprogram-tab-icon.source.svg` first.
- Copy the same SVG to `apps/web/app/icon.svg` and `scripts/docs_site/assets/mark.svg`.
- Regenerate `apps/web/app/favicon.ico` (16, 32, 48, 64, 128 and 256 px) and the
  192 and 512 px PWA icons from it. The maskable icon uses a full-bleed tile with
  the cell inside the 80% safe circle.

## Lockups

The mark beside the `OpenProgram` wordmark (Instrument Sans Bold) and the
`FINAL SOLUTION TO AGENT` tagline (Geist Mono). All text is converted to outlines,
so the files render the same in `<img>` tags, on GitHub and in librsvg.

Files:

- `docs/images/logo-lockup-dark.svg` and `logo-lockup-light.svg`: 776 x 174 vector
  lockups for dark and light backgrounds. `website/index.html` uses the dark one.
- `docs/images/logo-lockup.gif` and `logo-lockup-light.gif`: the README header
  animation. The three discs pop in back to front, then the full lockup holds.

## Wordmark

Canonical source:

- `docs/images/logo.svg`: the mark and the light-theme wordmark, without the tagline.

Runtime copy:

- `apps/web/public/images/logo.svg`

Code references:

- `apps/web/public/html/_sidebar.html`

Sync rule:

- Edit `docs/images/logo.svg` first.
- Copy the same SVG to `apps/web/public/images/logo.svg`.

## README Figures

The three concept figures in `README.md` and `README.zh.md` live in
`docs/images/highlights/`: `00-agent-class`, `01-dag-context` and
`02-self-evolving`, each as a dark `.svg` and a `-light.svg`. The READMEs pick one with `<picture>` and `prefers-color-scheme`.

They are generated; do not edit the SVG files by hand. Change
`scripts/readme_figures.py` and run:

```bash
python -m scripts.readme_figures
```

The script converts all text to outlines with fontTools (Instrument Sans and
Geist Mono, looked up in `~/Library/Fonts`, `/Library/Fonts` or
`$OPENPROGRAM_FIGURE_FONTS`), so GitHub's `<img>` sandbox, which loads no web
fonts, shows the same shapes. Motion uses SMIL with one loop per figure; a viewer
without SMIL shows every element at once. Figure text is English only, so both
READMEs use the same files.

## Social Card

- `docs/images/openprogram-social-card.svg` is the source; `openprogram-social-card.png`
  (1200 x 630) is its render. The docs site and `website/index.html` reference the PNG
  as the Open Graph and Twitter image, and the docs build copies it unchanged.

## Documentation Logo PNG

Canonical file:

- `docs/images/logo.png`: a 1024 px render of `docs/images/openprogram-app-icon.svg`.

Use:

- Static documentation image export.
- Keep it as a rendered asset; do not treat it as the editable source.

## Welcome Screen Text Logo

This is not an image file.

Code references:

- `apps/web/components/chat/welcome/welcome-screen.tsx`
- `apps/web/components/chat/welcome/welcome-screen.module.css`

Use:

- Renders the animated `{LLM}` text mark on the empty chat screen.
- Edit the component and CSS directly if that mark changes.
