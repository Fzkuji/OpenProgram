/**
 * ContextGraphPort — thin adapter over existing checkout / fold APIs.
 * Canvas emits intents; this port mutates session state via WS/HTTP.
 */

import { useSessionStore } from "@/lib/session-store";
import {
  _checkout,
  _scrollChatTo,
} from "../interaction/nodes";
import {
  forkAndEditNode,
  showNodeMenu,
  closeNodeLayers,
} from "../render/inspector";
import {
  _headAncestorSet,
  _lastGraph,
  toggleSummaryExpanded,
  toggleThreadOpen,
  setLastSignature,
} from "../store/globals";
import type { GNode } from "../types";
import type { ProjectionNode } from "./types";

function graphNode(id: string): GNode | null {
  if (!_lastGraph) return null;
  for (const n of _lastGraph) if (String(n.id) === id) return n;
  return null;
}

export type CanvasIntent =
  | { type: "select"; nodeId: string }
  | { type: "checkout"; nodeId: string }
  | { type: "fork_edit"; nodeId: string }
  | { type: "toggle_summary"; nodeId: string }
  | { type: "toggle_thread"; nodeId: string }
  | { type: "scroll_owner"; ownerId: string };

export async function dispatchCanvasIntent(
  intent: CanvasIntent,
  opts?: { node?: ProjectionNode; rerender?: () => void },
): Promise<void> {
  const id = "nodeId" in intent ? intent.nodeId : "";
  const gn = id ? graphNode(id) : null;
  const doRerender = opts?.rerender;

  switch (intent.type) {
    case "select": {
      if (!gn) return;
      const outRaw = gn.preview ?? gn.content ?? gn.output ?? "";
      const out = typeof outRaw === "string" ? outRaw : String(outRaw);
      const isTool = gn.role === "tool";
      useSessionStore.getState().populateDetail({
        path: String(gn.id),
        name:
          (isTool && typeof gn.name === "string" && gn.name ? gn.name : "")
          || (typeof gn.function === "string" ? gn.function : "")
          || (typeof gn.role === "string" ? gn.role : "node"),
        status: gn.is_error ? "error" : String(gn.status || "success"),
        output: gn.is_error ? undefined : out || undefined,
        error: gn.is_error ? out || "error" : undefined,
        node_type: isTool ? "tool" : String(gn.role || ""),
      });
      return;
    }
    case "checkout": {
      if (_headAncestorSet[id]) {
        _scrollChatTo(id);
      } else {
        await _checkout(id);
      }
      return;
    }
    case "fork_edit": {
      if (!gn) return;
      closeNodeLayers();
      await forkAndEditNode(gn);
      return;
    }
    case "toggle_summary": {
      toggleSummaryExpanded(id);
      setLastSignature(null);
      doRerender?.();
      _scrollChatTo(id + "_card");
      return;
    }
    case "toggle_thread": {
      toggleThreadOpen(id);
      setLastSignature(null);
      doRerender?.();
      return;
    }
    case "scroll_owner": {
      _scrollChatTo(intent.ownerId);
      return;
    }
    default:
      return;
  }
}

/** Single-click behaviour matching interaction/nodes.ts. */
export function handleNodeClick(
  node: ProjectionNode,
  rerenderFn: () => void,
): void {
  void dispatchCanvasIntent({ type: "select", nodeId: node.id });
  closeNodeLayers();
  if (node.isSummary && node.summaryCount > 0 && !node.isInert) {
    void dispatchCanvasIntent(
      { type: "toggle_summary", nodeId: node.id },
      { rerender: rerenderFn },
    );
    return;
  }
  if (node.threadCount > 0) {
    void dispatchCanvasIntent(
      { type: "toggle_thread", nodeId: node.id },
      { rerender: rerenderFn },
    );
    return;
  }
  if (node.isInternal && node.ownerId) {
    void dispatchCanvasIntent({ type: "scroll_owner", ownerId: node.ownerId });
  }
}

/** Double-click → checkout / fork&edit (ContextGraphPort). */
export function handleNodeDblClick(
  node: ProjectionNode,
  rerenderFn: () => void,
): void {
  if (node.source === "agent_spawn" && node.display !== "root"
      && !graphNode(node.id)?.predecessor) {
    return;
  }
  if (node.role === "user" && node.display !== "root") {
    void dispatchCanvasIntent({ type: "fork_edit", nodeId: node.id });
    return;
  }
  if (node.isSummary) {
    if (node.summaryCount > 0 && !node.isInert) {
      void dispatchCanvasIntent(
        { type: "toggle_summary", nodeId: node.id },
        { rerender: rerenderFn },
      );
    }
    return;
  }
  void dispatchCanvasIntent({ type: "checkout", nodeId: node.id });
}

export function handleNodeContextMenu(
  node: ProjectionNode,
  anchor: Element,
): void {
  const gn = graphNode(node.id);
  if (!gn) return;
  showNodeMenu(gn, anchor);
}

