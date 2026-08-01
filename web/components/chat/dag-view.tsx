"use client";

/**
 * DagView — the session context DAG as a CENTER perspective.
 *
 * The graph used to be a right-sidebar tab, squeezed into a 288px rail.
 * It now takes the whole center column: same host DOM (`#historyPanel`
 * + `.history-body`), which is what `lib/runtime-bridge/dag` selects
 * imperatively, so the renderer needed no change — only a wider box.
 * `_wirePanelResize` (render/visibility.ts) already re-lays-out on host
 * width changes, so the switch and any window resize re-flow the graph.
 *
 * Mounted alongside the transcript and toggled with `display`, not
 * unmounted: the renderer writes into this DOM on every WebSocket
 * capture regardless of which perspective is showing, so tearing the
 * host down would drop the graph until the next capture.
 */

import { useState } from "react";

import { useTranslation } from "@/lib/i18n";
import { setHistoryHighlightMode } from "@/lib/runtime-bridge/dag";
import { BranchesPanel } from "../right-sidebar/branches";

export function DagView({ visible }: { visible: boolean }) {
  return (
    <div
      id="historyPanel"
      className="dag-view"
      style={{ display: visible ? "flex" : "none" }}
      aria-hidden={visible ? undefined : true}
    >
      <div className="dag-view-rail">
        <BranchesPanel />
        <HighlightModeToggle />
      </div>
      <div className="history-body"></div>
    </div>
  );
}

/** Toggle: white-fill on DAG nodes follows the chat scroll position
 *  (viewport) or the next-LLM-call context range (context). Drives
 *  ``setHistoryHighlightMode`` in the DAG module. */
function HighlightModeToggle() {
  const { t } = useTranslation();
  const [mode, setMode] = useState<"viewport" | "context">("viewport");
  function pick(next: "viewport" | "context") {
    setMode(next);
    setHistoryHighlightMode(next);
  }
  const style = (active: boolean): React.CSSProperties => ({
    flex: 1,
    padding: "4px 8px",
    fontSize: 12,
    fontFamily: "inherit",
    border: "1px solid var(--border)",
    background: active ? "var(--bg-hover)" : "transparent",
    color: active ? "var(--text-bright)" : "var(--text-muted)",
    cursor: "pointer",
    borderRadius: 6,
    transition: "background 0.15s, color 0.15s",
  });
  return (
    <div
      style={{
        display: "flex",
        gap: 4,
        padding: "6px 8px",
        borderBottom: "1px solid var(--border)",
      }}
    >
      <button
        type="button"
        onClick={() => pick("viewport")}
        style={style(mode === "viewport")}
        title={t("right.viewport_tooltip")}
      >
        {t("right.viewport")}
      </button>
      <button
        type="button"
        onClick={() => pick("context")}
        style={style(mode === "context")}
        title={t("right.context_highlight_tooltip")}
      >
        {t("right.context")}
      </button>
    </div>
  );
}
