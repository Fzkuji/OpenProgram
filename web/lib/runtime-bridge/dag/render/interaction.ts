/**
 * Renderer: click / dblclick / chat-scroll glue.
 *
 * * Single click on a node → toggle collapse if the node has a
 *   collapsible subtree. Internal nodes (caller-edged) without their
 *   own collapsible cluster scroll the chat to the owner runtime
 *   block instead.
 * * Double click on a node OR edge → switch HEAD via
 *   ``POST /api/chat/checkout`` (or scroll-to-bubble when the
 *   target is already on the HEAD chain).
 *
 * Listeners are attached at module load (document-level capture) so
 * they survive every re-render of the SVG.
 */

/* eslint-disable @typescript-eslint/no-explicit-any */

import { HGW, type GNode } from "../types";
import { useSessionStore, type DetailNode } from "../../../session-store";
import {
  _collapsed,
  _currentHead,
  _headAncestorSet,
  _lastGraph,
  _lastHeadId,
  _leafOfNode,
  setLastSignature,
} from "../store/globals";

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
  const sessionId = HGW.currentSessionId;
  if (!sessionId || !msgId) return;
  const target = _leafOfNode[msgId] || msgId;
  if (target === _currentHead) return;
  if (HGW.ws && HGW.ws.readyState === WebSocket.OPEN) {
    HGW._postCheckoutScrollTo = msgId;
    HGW.ws.send(JSON.stringify({
      action: "checkout_branch",
      session_id: sessionId,
      head_msg_id: target,
    }));
    HGW.ws.send(JSON.stringify({
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
 *  ``_collapsed`` state. */
export function _installInteractionHandlers(rerender: () => void): void {
  document.addEventListener("click", (e) => {
    const tgt = e.target as HTMLElement;
    const g = tgt.closest && tgt.closest(".history-node");
    if (!g) return;
    const id = g.getAttribute("data-msg-id");
    if (!id) return;
    // Selecting a node always populates the right rail's Details view.
    // This is orthogonal to collapse/scroll below: a collapsible node
    // both folds and shows its details, matching the pre-DAG-rewrite
    // behaviour where every node click drove `showDetail`.
    const gn = _graphNode(id);
    if (gn) useSessionStore.getState().showDetail(_detailFor(gn), true);
    if (g.getAttribute("data-internal") === "1"
        && g.getAttribute("data-collapsible") !== "1") {
      const owner = g.getAttribute("data-owner");
      if (owner) _scrollChatTo(owner);
      return;
    }
    if (g.getAttribute("data-collapsible") === "1") {
      if (_collapsed[id]) delete _collapsed[id];
      else _collapsed[id] = true;
      if (_lastGraph) {
        setLastSignature(null);
        rerender();
      }
    }
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
