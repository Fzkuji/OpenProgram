import type { FileTreeIcons } from "@pierre/trees";

export const pierreTreeIcons: FileTreeIcons = {
  set: "complete", colored: true,
  byFileName: Object.fromEntries(Object.getOwnPropertyNames(Object.prototype).map(name => [name.toLowerCase(), "file-tree-builtin-default"])),
  byFileExtension: Object.fromEntries(Object.getOwnPropertyNames(Object.prototype).map(name => [name.toLowerCase(), "file-tree-builtin-default"])),
  remap: { "file-tree-icon-chevron": "openprogram-folder" },
  spriteSheet: `<svg xmlns="http://www.w3.org/2000/svg" width="0" height="0" aria-hidden="true"><symbol id="openprogram-folder" viewBox="0 0 24 24"><g fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path style="display:var(--op-folder-closed,inline)" d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/><path style="display:var(--op-folder-open,none)" d="m6 14 1.5-2.9A2 2 0 0 1 9.24 10H20a2 2 0 0 1 1.94 2.5l-1.54 6a2 2 0 0 1-1.95 1.5H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h3.9a2 2 0 0 1 1.69.9l.81 1.2a2 2 0 0 0 1.67.9H18a2 2 0 0 1 2 2v2"/></g></symbol></svg>`,
};
export const pierreTreeCSS = `
:host { --trees-bg-override: var(--bg-secondary); --trees-padding-inline-override: 4px; --trees-fg-override: var(--text-secondary); font-family: inherit; }
[data-item-type="folder"] > [data-item-section="icon"] > svg { transform: none !important; color: #d4a73e; }
[data-item-type="folder"][aria-expanded="true"], [data-item-type="folder"][data-file-tree-sticky-row="true"] { --op-folder-closed: none; --op-folder-open: inline; }
[data-item-selected="true"]::before { outline: none !important; }
[data-item-section="decoration"] { font-size: 11px; color: var(--text-tertiary); white-space: nowrap; }
`;
