import { readFileSync } from "node:fs";

/**
 * The center tab strip is split across one component file and its
 * submodules (drag engine, context menu, lifecycle, drop actions, tab
 * rendering). The strip assertions in the check scripts read the strip as
 * ONE source text, so concatenate the parts in the order the original
 * single file had them: strip shell → drag subject helpers → pointer drag
 * engine → drop actions → menu state/actions → menu panel → lifecycle →
 * tab items. Slice sentinels used by the assertions (onPrepareDrag,
 * onPointerDragMove, onPointerDragUp, targetBeforeId, applyDrop,
 * onTabListKeyDown, onTabListWheel, moveMenuTab,
 * onTabClick, onOpenNewTab, finishClose, labelOf, CompoundTabItem,
 * TabItem) keep their relative order under this concatenation.
 */
const STRIP_PARTS = [
  "center-tab-strip.tsx",
  "tab-drag-subject.ts",
  "use-tab-pointer-drag.ts",
  "use-tab-drop-actions.ts",
  "use-tab-menu.ts",
  "tab-context-menu.tsx",
  "use-tab-lifecycle.ts",
  "tab-items.tsx",
];

export function readCenterTabStripSource(importMetaUrl) {
  return STRIP_PARTS.map((name) =>
    readFileSync(
      new URL(`../components/center-tabs/${name}`, importMetaUrl),
      "utf8",
    ).replaceAll("\r\n", "\n"),
  ).join("\n");
}
