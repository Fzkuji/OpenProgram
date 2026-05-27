# Image Sources

This directory keeps the editable image sources used by documentation and the WebUI. Runtime copies live under `web/` so Next.js can serve them.

## WebUI Tab Icon

Canonical source:

- `docs/images/openprogram-tab-icon.source.svg`

Runtime files:

- `web/app/icon.svg`
- `web/app/favicon.ico`

Code reference:

- `web/app/layout.tsx`

Current design:

- 64 x 64 SVG.
- Rounded square background.
- Background gradient: near-black -> deep red -> red -> orange-yellow.
- Foreground: code brackets, center node, and short orange vertical strokes.

Sync rule:

- Edit `docs/images/openprogram-tab-icon.source.svg` first.
- Copy the same SVG to `web/app/icon.svg`.
- Regenerate `web/app/favicon.ico` from `web/app/icon.svg`.

## WebUI Sidebar Logo

Canonical source:

- `docs/images/logo.svg`

Runtime copy:

- `web/public/images/logo.svg`

Code references:

- `web/components/sidebar/sidebar.tsx`
- `web/public/html/_sidebar.html`

Documentation references:

- `docs/README_CN.md`
- `docs/archive/README_DRAFT.md`

Sync rule:

- Edit `docs/images/logo.svg` first.
- Copy the same SVG to `web/public/images/logo.svg`.

## Documentation Logo PNG

Canonical file:

- `docs/images/logo.png`

Use:

- Static documentation image export.
- Keep it as a rendered asset; do not treat it as the editable source.

## Welcome Screen Text Logo

This is not an image file.

Code references:

- `web/components/chat/welcome-screen.tsx`
- `web/components/chat/welcome-screen.module.css`

Use:

- Renders the animated `{LLM}` text mark on the empty chat screen.
- Edit the component and CSS directly if that mark changes.
