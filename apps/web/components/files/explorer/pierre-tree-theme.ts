import type { FileTreeIcons } from "@pierre/trees";
import { SOLAR_BODIES } from "@/components/solar-icons/bodies";

export const pierreTreeIcons: FileTreeIcons = {
  set: "complete", colored: true,
  byFileName: Object.fromEntries(Object.getOwnPropertyNames(Object.prototype).map(name => [name.toLowerCase(), "file-tree-builtin-default"])),
  byFileExtension: Object.fromEntries(Object.getOwnPropertyNames(Object.prototype).map(name => [name.toLowerCase(), "file-tree-builtin-default"])),
  remap: { "file-tree-icon-chevron": "openprogram-folder" },
  // The main sidebar's Solar folder (duotone, --folder-color); the row's
  // expanded state swaps in Solar folder-open.
  spriteSheet: `<svg xmlns="http://www.w3.org/2000/svg" width="0" height="0" aria-hidden="true"><symbol id="openprogram-folder" viewBox="0 0 24 24"><g style="display:var(--op-folder-closed,inline)">${SOLAR_BODIES.folder}</g><g style="display:var(--op-folder-open,none)">${SOLAR_BODIES["folder-open"]}</g></symbol></svg>`,
};
// Like Finder's list, rows sit 6px in from both panel edges (their hover and
// selection fills too) and keep 6px inside, so the first icon and the last
// column's text are both 12px from the edge.
export const fileColumnWidths = { size: 100, modified: 200, kind: 140, padding: 6, inset: 6 };
export const fileColumnGrid = "var(--op-files-name-width, 220px) 100px 200px 140px";
// Below 720px only Name and Size fit, so the row ends after Size (Codex's
// tree likewise keeps every row inside the panel; nothing scrolls sideways).
export const fileColumnsWidth = "calc(var(--op-files-name-width, 220px) + var(--op-files-meta-width, 448px))";

export const pierreTreeCSS = `
:host { --trees-font-family-override: var(--right-panel-font-family, var(--font-sans)); --trees-font-size-override: var(--right-panel-text-size, var(--fs-sm)); --trees-bg-override: var(--files-tree-bg, var(--bg-secondary)); --trees-padding-inline-override: 8px; --trees-fg-override: var(--text-secondary); --trees-indent-guide-bg-override: color-mix(in srgb, var(--text-muted) 60%, transparent); font-family: inherit; }
/* Pierre's own indent guides, one per level on the parent's icon axis, always shown
   (Pierre fades them in only on hover and for the focused row's parent). */
[data-item-section="spacing-item"] { opacity: 1; }
[data-item-type="folder"] > [data-item-section="icon"] > svg { transform: none !important; color: var(--folder-color); }
[data-item-type="folder"][aria-expanded="true"], [data-item-type="folder"][data-file-tree-sticky-row="true"] { --op-folder-closed: none; --op-folder-open: inline; }
[data-item-selected="true"]::before { outline: none !important; }
/* Rows scroll out of sight 6px above the panel edge, as they sit 6px in at the sides
   (Pierre clamps scrolling to the list height, so padding here would never show). */
[data-file-tree-virtualized-scroll="true"] { padding-inline: 0; margin-bottom: 6px; overflow-x: hidden; }
/* The app's overlay thumb (attachOverlayScrollbar) replaces the vertical bar, so rows reach the right edge. */
[data-file-tree-virtualized-scroll="true"] { scrollbar-gutter: auto; }
[data-file-tree-virtualized-scroll="true"]::-webkit-scrollbar:vertical { width: 0; }
[data-type="item"] { width: ${fileColumnsWidth}; box-sizing: border-box; margin-inline: 6px; padding-inline: 6px; }
[data-file-tree-virtualized-list="true"], [data-file-tree-sticky-overlay="true"] { width: calc(${fileColumnsWidth} + 12px); }
[data-item-section="spacing"] { max-width: max(0px, calc(var(--op-files-name-width, 220px) - 96px)); flex-shrink: 0; overflow: hidden; }
[data-item-section="content"] { flex: 1 1 0; }
[data-item-section="decoration"] { width: calc(var(--op-files-meta-width, 448px) - 6px); }
[data-item-section="decoration"] > span { display: grid; grid-template-columns: var(--op-files-meta-columns, 100px 200px 140px); width: calc(var(--op-files-meta-width, 448px) - 6px); max-width: none; }
[data-item-section="decoration"] > span > span:not(:first-child) { display: var(--op-files-more-columns, block); }
[data-item-section="decoration"] > span > span { min-width: 0; box-sizing: border-box; padding: 0 10px 0 12px; overflow: hidden; text-overflow: ellipsis; text-align: left; }
/* Size ends flush with the row when it is the last column. */
[data-item-section="decoration"] > span > span:first-child { text-align: right; padding-right: var(--op-files-size-end, 10px); }
[data-item-section="decoration"] { flex: none; font-size: var(--right-panel-meta-size, var(--fs-2xs)); line-height: var(--right-panel-line-height, normal); color: var(--text-secondary); white-space: nowrap; }
[data-item-section="decoration"] span[style*="--op-size-scanning"] { animation: folder-size-pulse 1.8s ease-in-out infinite; }
@keyframes folder-size-pulse { 0%, 100% { opacity: 1; } 50% { opacity: .45; } }
@media (prefers-reduced-motion: reduce) { [data-item-section="decoration"] span[style*="--op-size-scanning"] { animation: none; } }
`;
