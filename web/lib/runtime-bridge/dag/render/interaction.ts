/**
 * Renderer: click / dblclick / contextmenu / chat-scroll glue.
 *
 * * Single click on a node → the inspector popover (``./inspector``),
 *   plus the node's own click behaviour: a compaction capsule folds or
 *   unfolds the range it covers, a node with an execution subtree
 *   toggles that subtree, and an internal node without its own cluster
 *   scrolls the chat to the owner runtime block.
 * * Right click on a node → the node menu (checkout / fork / fork &
 *   edit / copy id / raw JSON).
 * * Double click on a USER node → fork & edit: HEAD moves to the fork
 *   point and the message text lands in the composer.
 * * Double click on any other node OR an edge → switch HEAD via
 *   ``POST /api/chat/checkout`` (or scroll-to-bubble when the
 *   target is already on the HEAD chain).
 *
 * Listeners are attached at module load (document-level capture) so
 * they survive every re-render of the SVG.
 */

/* eslint-disable @typescript-eslint/no-explicit-any */

import type { GNode } from "../types";
import { getSocket, runtimeState } from "../../state";
import { useSessionStore, type DetailNode } from "../../../session-store";
import {
  _currentHead,
  _headAncestorSet,
  _lastGraph,
  _lastHeadId,
  _leafOfNode,
  setLastSignature,
  toggleSummaryExpanded,
  toggleThreadOpen,
} from "../store/globals";
import {
  closeNodeLayers,
  forkAndEditNode,
  showNodeMenu,
} from "./inspector";

export function _chatBubbleFor(msgId: string): Element | null {
  if (!msgId) return null;
  const container = document.getElementById("chatMessages");
  if (!container) return null;
  const esc = window.CSS && CSS.escape ? CSS.escape(msgId) : msgId;
  return (
    container.querySelector('[data-msg-id="' + esc + '"]') ||
    container.querySelector('[data-msg-ids~="' + esc + '"]')
  );
}

export function _scrollChatTo(msgId: string): void {
  const bubble = _chatBubbleFor(msgId);
  if (!bubble) return;
  bubble.scrollIntoView({ behavior: "smooth", block: "start" });
  bubble.classList.remove("dag-flash");
  void (bubble as HTMLElement).offsetWidth;
  bubble.classList.add("dag-flash");
  window.setTimeout(() => {
    bubble.classList.remove("dag-flash");
  }, 1400);
}

export async function _checkout(msgId: string): Promise<void> {
  const sessionId = runtimeState.currentSessionId;
  if (!sessionId || !msgId) return;
  const target = _leafOfNode[msgId] || msgId;
  if (target === _currentHead) return;
  const sock = getSocket();
  if (sock && sock.readyState === WebSocket.OPEN) {
    runtimeState._postCheckoutScrollTo = msgId;
    sock.send(JSON.stringify({
      action: "checkout_branch",
      session_id: sessionId,
      head_msg_id: target,
    }));
    sock.send(JSON.stringify({
      action: "load_session",
      session_id: sessionId,
    }));
  }
}

export function _onEdgeDblclick(targetId: string): void {
  if (!targetId) return;
  if (_headAncestorSet[targetId]) {
    _scrollChatTo(targetId);
  } else {
    _checkout(targetId);
  }
}

/** Find a graph node by id in the flat ``_lastGraph`` cache. */
function _graphNode(id: string): GNode | null {
  if (!_lastGraph) return null;
  for (const n of _lastGraph) if (String(n.id) === id) return n;
  return null;
}

/**
 * Build the right-rail DetailNode for a DAG node.
 *
 * Field accessors mirror ``dag/tooltip.ts`` (``preview ?? content ??
 * output`` for the body, ``llm`` for model/token meta) so the panel and
 * the hover card never disagree about what a node contains.
 */
function _detailFor(node: GNode): DetailNode {
  const outRaw = node.preview ?? node.content ?? node.output ?? "";
  const out = typeof outRaw === "string" ? outRaw : String(outRaw);
  const isTool = node.role === "tool";
  const meta = (node.llm || {}) as Record<string, unknown>;
  const params: Record<string, unknown> = {};
  if (isTool && typeof node.input === "string" && node.input) {
    params.input = node.input;
  }
  if (node.attach_label) params.label = node.attach_label;
  if (node.attach_ref) params.head_id = node.attach_ref;
  if (node.attach_source_commit_id) {
    params.source_commit_id = node.attach_source_commit_id;
  }
  if (typeof meta.model === "string" && meta.model) params.model = meta.model;
  if (typeof meta.input_tokens === "number") params.input_tokens = meta.input_tokens;
  if (typeof meta.output_tokens === "number") params.output_tokens = meta.output_tokens;
  const name =
    (isTool && typeof node.name === "string" && node.name ? node.name : "") ||
    (typeof node.function === "string" ? node.function : "") ||
    (typeof node.role === "string" ? node.role : "node");
  return {
    path: String(node.id),
    name,
    status: node.is_error ? "error" : String(node.status || "success"),
    params: Object.keys(params).length ? params : undefined,
    output: node.is_error ? undefined : out || undefined,
    error: node.is_error ? out || "error" : undefined,
    node_type: isTool ? "tool" : String(node.role || ""),
  };
}

/** Install document-level click / dblclick listeners. ``rerender`` is
 *  invoked after a collapse toggle so the panel rebuilds with the new
 *  ``_collapsed`` state.
 *
 *  Called at module load, so it has to tolerate a DOM-less host: this
 *  module is now reachable from SSR and from the Node check scripts. */
export function _installInteractionHandlers(rerender: () => void): void {
  if (typeof document === "undefined") return;
  document.addEventListener("click", (e) => {
    const tgt = e.target as HTMLElement;
    // A click that lands anywhere but on a node dismisses the popover.
    // The layer stops propagation on its own clicks, so its buttons
    // never close it out from under themselves.
    const g = tgt.closest && tgt.closest(".history-node");
    if (!g) {
      if (!(tgt.closest && tgt.closest(".dag-inspector, .dag-menu"))) {
        closeNodeLayers();
      }
      return;
    }
    const id = g.getAttribute("data-msg-id");
    if (!id) return;
    // A click is the node's own ACTION, not an info request — info
    // lives on the hover card (tooltip.ts), the one surface per node.
    // The old click-opened inspector popover landed on top of the very
    // expansion the same click had just triggered. The right rail's
    // Details view still fills quietly, for whenever the user opens
    // the rail themselves.
    const gn = _graphNode(id);
    if (gn) useSessionStore.getState().populateDetail(_detailFor(gn));
    // A capsule's click is its fold (dag/rendering.md §9). It takes
    // precedence over the execution-subtree fold below: the capsule
    // stands for a span of the CHAIN, which is the bigger thing the
    // click is about, and a summary node has no sub-calls to hide
    // anyway.
    if (g.getAttribute("data-summary")) {
      toggleSummaryExpanded(id);
      if (_lastGraph) {
        // The expanded set is not part of the render signature, so the
        // repaint would dedup itself away without busting it.
        setLastSignature(null);
        rerender();
      }
      return;
    }
    // A node with a call thread folds and unfolds it (dag/rendering.md
    // §12) — chain turn or spawn head, one vocabulary, recursive.
    if (g.getAttribute("data-thread")) {
      toggleThreadOpen(id);
      if (_lastGraph) {
        setLastSignature(null);
        rerender();
      }
      return;
    }
    if (g.getAttribute("data-internal") === "1") {
      const owner = g.getAttribute("data-owner");
      if (owner) _scrollChatTo(owner);
      return;
    }
  });

  document.addEventListener("contextmenu", (e) => {
    const tgt = e.target as HTMLElement;
    const g = tgt.closest && tgt.closest(".history-node");
    if (!g) return;
    const id = g.getAttribute("data-msg-id");
    if (!id) return;
    const gn = _graphNode(id);
    if (!gn) return;
    e.preventDefault();
    // Anchor the menu at the cursor, not at the node: a right-click is
    // aimed, and a menu that jumps to the node's edge reads as a miss.
    showNodeMenu(gn, g, new DOMRect(e.clientX, e.clientY, 0, 0));
  });

  document.addEventListener("dblclick", (e) => {
    const tgt = e.target as HTMLElement;
    const node = tgt.closest && tgt.closest(".history-node");
    const edgeHit = node
      ? null
      : tgt.closest && (tgt.closest(".history-edge-hit, .history-edge-group, [data-target-id]") as Element | null);
    let id: string | null = null;
    let isInternal = false;
    let owner: string | null = null;
    if (node) {
      id = node.getAttribute("data-msg-id");
      isInternal = node.getAttribute("data-internal") === "1";
      owner = node.getAttribute("data-owner");
    } else if (edgeHit) {
      id = edgeHit.getAttribute("data-target-id");
    }
    if (!id) return;
    if (isInternal) {
      if (owner) _scrollChatTo(owner);
      return;
    }
    // A double click on a user turn is fork & edit (dag/rendering.md
    // §11): the message you double-clicked lands in the composer with
    // HEAD already back at its fork point, so editing and sending
    // writes the sibling. Checkout keeps the other nodes — there is
    // nothing to edit on a reply or a tool result.
    const gn = node ? _graphNode(id) : null;
    if (gn && gn.role === "user" && gn.display !== "root") {
      closeNodeLayers();
      void forkAndEditNode(gn);
      return;
    }
    if (_headAncestorSet[id]) {
      _scrollChatTo(id);
    } else {
      _checkout(id);
    }
  });

  // Reference the unused-but-kept-for-clarity heads-bind so the
  // tree-shaker doesn't strip it.
  void _lastHeadId;
}
