"use client";

/**
 * The DAG canvas's selection action bar (dag/rendering.md §13).
 *
 * There are no tools to switch: one mouse pans, clicks and (with
 * Shift/⌘) selects. Every view control — zoom, the whole graph, finding
 * the working agent, the legend, the minimap and the button that opens
 * the controls sheet (dag-key-sheet.tsx) — is the column in the top
 * right (dag-view.tsx). The action bar appears at the top left while something is selected,
 * and for a few seconds after a delete, as the way back. The top right
 * belongs to the pane's perspective controls.
 */

import { platformKeyCaps, useIsMacPlatform } from "@/lib/shortcut-keys";
import { useSyncExternalStore } from "react";
import {
  Copy,
  Scan,
  Trash2,
  Undo2,
  X,
} from "lucide-react";

import { useTranslation } from "@/lib/i18n";
import {
  clearSelection,
  selectedIds,
  selectionVersion,
  subscribeSelection,
} from "@/lib/runtime-bridge/dag/interaction/selection/selection";
import {
  deleteSelection,
  deletionPending,
  lastDeletion,
  undoDeletion,
} from "@/lib/runtime-bridge/dag/interaction/selection/node-delete";
import { frameSelection } from "@/lib/runtime-bridge/dag/interaction/view/canvas";

function useSelectionVersion(): number {
  return useSyncExternalStore(subscribeSelection, selectionVersion, selectionVersion);
}

function DagSelectionBar() {
  const { text } = useTranslation();
  const mac = useIsMacPlatform();
  useSelectionVersion();
  const ids = selectedIds();
  const pending = deletionPending();
  const last = lastDeletion();
  if (!ids.length && !last) return null;
  if (!ids.length && last) {
    return (
      <div className="dag-select-bar" role="status">
        <span className="dag-select-count">
          {text(`Deleted ${last.count} node${last.count === 1 ? "" : "s"}`,
            `已删除 ${last.count} 个节点`)}
        </span>
        <span className="dag-select-sep" aria-hidden="true" />
        <button type="button" className="dag-select-act" disabled={pending}
          onClick={() => undoDeletion()} data-tip={text("Undo", "撤销")} data-tip-keys={platformKeyCaps("⌘ Z", mac)}>
          <Undo2 size={14} strokeWidth={2} />
          <span>{text("Undo", "撤销")}</span>
        </button>
      </div>
    );
  }
  return (
    <div className="dag-select-bar" role="toolbar"
      aria-label={text("Selection", "选择")}>
      <span className="dag-select-count">
        {text(`${ids.length} selected`, `已选 ${ids.length}`)}
      </span>
      <span className="dag-select-sep" aria-hidden="true" />
      <button type="button" className="dag-select-act dag-select-icon"
        aria-label={text("Fit the selection", "缩放到所选")}
        data-tip={text("Fit the selection", "缩放到所选")} data-tip-keys="⇧ 2"
        onClick={() => frameSelection(ids)}>
        <Scan size={14} strokeWidth={2} />
      </button>
      <button type="button" className="dag-select-act"
        data-tip={text("Copy node IDs", "复制节点 ID")}
        onClick={() => { void navigator.clipboard?.writeText(ids.join("\n")); }}>
        <Copy size={14} strokeWidth={2} />
        <span>{text("Copy ID", "复制 ID")}</span>
      </button>
      <button type="button" className="dag-select-act dag-select-danger"
        disabled={pending}
        data-tip={text("Delete\nWhat follows joins the node before", "删除\n后续接到前一个节点")}
        data-tip-keys="⌫"
        onClick={() => deleteSelection()}>
        <Trash2 size={14} strokeWidth={2} />
        <span>{text("Delete", "删除")}</span>
      </button>
      <button type="button" className="dag-select-act dag-select-icon"
        aria-label={text("Clear selection", "取消选择")}
        data-tip={text("Clear selection", "取消选择")} data-tip-keys="Esc"
        onClick={() => clearSelection()}>
        <X size={14} strokeWidth={2} />
      </button>
    </div>
  );
}

/** The selection bar, absolutely placed over the canvas host. The
 *  controls sheet sits in the side column (dag-view.tsx DagSideColumn). */
export function DagCanvasTools() {
  return <DagSelectionBar />;
}
