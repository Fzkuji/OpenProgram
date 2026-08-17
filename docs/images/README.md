# Image Sources

This directory keeps the editable image sources used by documentation and the WebUI. Runtime copies live under `web/` and end up in the static export (`apps/web/out/`) the worker serves.

## WebUI Tab Icon

Canonical source:

- `docs/images/openprogram-tab-icon.source.svg`

Runtime files:

- `apps/web/app/icon.svg`
- `apps/web/app/favicon.ico`

Code reference:

- `apps/web/app/layout.tsx`

Current design:

- 64 x 64 SVG.
- Rounded square background.
- Background gradient: near-black -> deep red -> red -> orange-yellow.
- Foreground: code brackets, center node, and short orange vertical strokes.

Sync rule:

- Edit `docs/images/openprogram-tab-icon.source.svg` first.
- Copy the same SVG to `apps/web/app/icon.svg`.
- Regenerate `apps/web/app/favicon.ico` from `apps/web/app/icon.svg`.

## WebUI Sidebar Logo

Canonical source:

- `docs/images/logo.svg`

Runtime copy:

- `apps/web/public/images/logo.svg`

Code references:

- `apps/web/components/sidebar/sidebar.tsx`
- `apps/web/public/html/_sidebar.html`

Documentation references:

- `docs/README.md`
- `docs/archive/README_DRAFT.md`

Sync rule:

- Edit `docs/images/logo.svg` first.
- Copy the same SVG to `apps/web/public/images/logo.svg`.

## Documentation Logo PNG

Canonical file:

- `docs/images/logo.png`

Use:

- Static documentation image export.
- Keep it as a rendered asset; do not treat it as the editable source.

## Welcome Screen Text Logo

This is not an image file.

Code references:

- `apps/web/components/chat/welcome-screen.tsx`
- `apps/web/components/chat/welcome-screen.module.css`

Use:

- Renders the animated `{LLM}` text mark on the empty chat screen.
- Edit the component and CSS directly if that mark changes.
