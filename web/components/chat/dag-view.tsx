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
 *
 * Layout, top to bottom: the branch strip, the canvas, and — supplied
 * by the pane, not by this component — the composer. The composer is a
 * singleton anchored inside `#chatView`, which the graph perspective
 * deliberately leaves mounted (it hides `#chatArea` instead), so
 * sending a message from the graph runs the same code path as sending
 * it from the transcript and the new node appears on the next capture.
 *
 * The white fill means context coverage here, with no mode switch to
 * offer (dag/rendering.md §8). Viewport highlighting answers "which
 * bubbles are on screen", and whenever this component is visible it
 * owns its pane with no transcript beside it: a lone session tab gets
 * the chat shell and this graph, and two session tabs split into two
 * `PeerSessionPane`s where the shell — and therefore this graph — is
 * not rendered at all. So the question has no reading to give, and
 * coverage is simply what the fill means.
 */

import { useEffect } from "react";

import { enterExclusiveCoverageMode } from "@/lib/runtime-bridge/dag";
import { BranchesPanel } from "../right-sidebar/branches";

export function DagView({ visible }: { visible: boolean }) {
  useEffect(() => {
    if (visible) enterExclusiveCoverageMode();
  }, [visible]);
  return (
    <div
      id="historyPanel"
      className="dag-view"
      style={{ display: visible ? "flex" : "none" }}
      aria-hidden={visible ? undefined : true}
    >
      <BranchesPanel variant="chips" />
      <div className="history-body"></div>
    </div>
  );
}
