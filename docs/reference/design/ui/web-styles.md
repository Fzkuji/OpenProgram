# Web style organization

The web app's CSS is organized to mirror the component tree: one file per
component (or per tightly-scoped concern), grouped into directories that
match the UI's own structure. Styling a component means opening exactly one
file, named after it.

## Layout

```
apps/web/app/styles/
  base.css          design tokens + global primitives (unchanged, global)
  themes/           theme token overrides (unchanged, global)
  chat/             one file per chat component
    transcript.css bubbles.css execution-strip.css attach-card.css
    agent-branch-banner.css message-rail.css message-actions.css
    inline-tree.css stream-blocks.css turn-files-card.css file-diff.css
    ... (25 files)
  dag/              one file per DAG-renderer concern
    view-host.css canvas.css nodes.css edges.css badges.css
    tooltip.css inspector.css hud.css dag-flash.css
  right-dock/       right-sidebar panels
    view-host.css bookmarks.css branches-panel.css web-history.css
    detail.css
```

`apps/web/app/styles.css` imports the tree in the original cascade order
(base → chat → detail-era files → right-dock/dag), so specificity ties
resolve exactly as they did before the split.

## Rules

- **One component, one file.** A file styles the component it is named
  after, nothing else. Every file opens with a comment naming the
  component and its tsx/ts source path.
- **Placement follows the component tree.** A DAG node style belongs in
  `dag/nodes.css` because the drawer lives in
  `lib/runtime-bridge/dag/render/nodes.ts`; a chat bubble style belongs in
  `chat/bubbles.css`. New components get new files, not appended sections.
- **Genuinely global rules stay global.** Tokens and theme overrides live
  in `base.css` / `themes/`; the chat column skeleton (`.main`,
  `#chatView`) is `chat/layout.css` — the one deliberately cross-component
  file, named for what it is.
- **Module CSS stays module CSS.** Components already using
  `*.module.css` (the composer) keep it; this tree covers the global
  stylesheet layer only.
- Guard scripts that assert on style sources read the per-component file
  (`check-dag-subagent.mjs` reads `dag/nodes.css` for the head-glow
  keyframes).
