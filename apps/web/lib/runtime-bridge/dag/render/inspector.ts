/**
 * Renderer: the node verbs (and the raw-JSON layer they open).
 *
 * The node has ONE window — the card in ../tooltip.ts. Hover shows its
 * brief state; right-click calls ``expandTooltip`` to deepen that same
 * card in place, and what THIS module contributes is the verb list
 * appended below the detail rows:
 *
 *   * **Right-click a node** → the card expands with checkout, fork,
 *     fork-and-edit (user turns only), copy id, raw JSON at the bottom
 *     (dag/rendering.md §11).
 *
 * Every action is an existing operation, reached by its existing
 * route. Nothing here invents a verb:
 *
 *   * checkout / fork → `POST /api/chat/checkout`. Fork is checkout
 *     plus intent — moving HEAD onto a node is exactly what makes the
 *     next turn a sibling of whatever followed it, which is how the
 *     transcript's own "branch from here" button works.
 *   * fork & edit → the same checkout, then the message text into the
 *     composer. The user edits and sends; the send is an ordinary send
 *     against the new HEAD, so it forks with no protocol change and no
 *     "send from node X" concept to maintain.
 *
 * Built imperatively because the graph is: these float over an SVG the
 * renderer owns, keyed to node geometry, outside React's tree.
 */

/* eslint-disable @typescript-eslint/no-explicit-any */

import type { GNode } from "../types";
import { getSocket, runtimeState } from "../../state";
import { useSessionStore } from "../../../session-store";
import { showToast } from "@/lib/format-utils/toast";
import { translateText } from "@/lib/i18n";
import { _bodyText, closeTooltipDetail, expandTooltip } from "../interaction/tooltip";

function _roleLabel(node: GNode, el?: Element): string {
  if (node.display === "root") return "root";
  if (Array.isArray((node as Record<string, unknown>).covers_ids)) {
    return "context/summary";
  }
  // A spawn root is a user turn by role, but "user" is the wrong answer
  // to "what am I looking at" — it is the head of another agent's chain
  // (dag/rendering.md §12). The name comes off the DOM because the fold
  // pass is what resolves it, and the picture must not disagree.
  if ((node as Record<string, unknown>).source === "agent_spawn"
      && !node.predecessor) {
    const nm = (el?.getAttribute("data-spawn-name") || "").trim();
    return nm
      ? translateText(`sub-agent · ${nm}`, `子 agent · ${nm}`)
      : translateText("sub-agent", "子 agent");
  }
  if (node.role === "tool") {
    const name = (node.name as string | undefined) || node.function;
    return name ? `tool · ${name}` : "tool";
  }
  return String(node.role || "node");
}

// ── shared shell ───────────────────────────────────────────────────

let _layer: HTMLElement | null = null;

function _closeLayer(): void {
  if (_layer && _layer.parentElement) _layer.parentElement.removeChild(_layer);
  _layer = null;
}

/** Mount ``el`` as the one floating layer, anchored near ``rect`` and
 *  clamped inside the viewport. A second call replaces the first —
 *  only ever one popover, so a click elsewhere never leaves a trail. */
function _openLayer(el: HTMLElement, rect: DOMRect): void {
  _closeLayer();
  el.style.position = "fixed";
  el.style.visibility = "hidden";
  document.body.appendChild(el);
  _layer = el;
  const w = el.offsetWidth;
  const h = el.offsetHeight;
  let left = rect.right + 12;
  if (left + w > window.innerWidth - 8) left = Math.max(8, rect.left - 12 - w);
  let top = rect.top - 8;
  if (top + h > window.innerHeight - 8) top = Math.max(8, window.innerHeight - 8 - h);
  el.style.left = `${left}px`;
  el.style.top = `${top}px`;
  el.style.visibility = "visible";
}

export function closeNodeLayers(): void {
  _closeLayer();
  closeTooltipDetail();
}

// ── actions ────────────────────────────────────────────────────────

function _copy(text: string, toast: string): void {
  const done = () => showToast(toast);
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).then(done, done);
  } else {
    done();
  }
}

/** A checkout/fork target is a CHAIN-level turn. On disk those carry
 *  ``caller`` of "ROOT" (user turns, ROOT-hung records) or "" (reply
 *  nodes); a node whose caller is ANOTHER CALL is function-internal
 *  machinery, which the backend rejects — so the actions are not
 *  offered there in the first place. Must mirror the caller-based gate
 *  in webui/_chat_routes.py. */
function _isChainTurn(node: GNode): boolean {
  if (node.display === "root") return false;
  const c = node.caller;
  return c == null || c === "" || c === "ROOT";
}

/** Move HEAD onto ``id``. This is checkout AND fork: the difference is
 *  only what you do next, because the next turn sent from a HEAD that
 *  has children is by definition a sibling of them. */
async function _checkoutTo(id: string): Promise<boolean> {
  const sid = runtimeState.currentSessionId;
  if (!sid) return false;
  try {
    const r = await fetch("/api/chat/checkout", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sid, msg_id: id }),
    });
    if (!r.ok) {
      const j = await r.json().catch(() => null);
      showToast(j?.error || translateText("Checkout failed", "checkout 失败"),
        { tone: "error" });
      return false;
    }
  } catch {
    showToast(translateText("Checkout failed", "checkout 失败"),
      { tone: "error" });
    return false;
  }
  runtimeState._postCheckoutScrollTo = id;
  const sock = getSocket();
  if (sock && sock.readyState === WebSocket.OPEN) {
    sock.send(JSON.stringify({ action: "load_session", session_id: sid }));
  }
  return true;
}

/**
 * Fork and edit a user turn.
 *
 * Checkout to the turn's PREDECESSOR, then drop its text in the
 * composer. Sending from there writes a sibling of the original — the
 * same shape `POST /api/chat/edit` produces, reached with the routes
 * that already exist. The predecessor is the fork point precisely
 * because the edited message has to stand beside the original, not
 * after it.
 *
 * A first turn carries the "ROOT" sentinel as its predecessor, which
 * the checkout route accepts like any other node.
 *
 * Strictly the conversation edge — no `caller` fallback. A spawn
 * branch root has predecessor=None and caller=<spawning node in the
 * PARENT branch>, and forking there must not check out into a
 * different branch.
 */
export async function forkAndEditNode(node: GNode): Promise<void> {
  const pivot = node.predecessor;
  if (!pivot) {
    showToast(translateText("Fork point not found", "找不到分叉点"),
      { tone: "error" });
    return;
  }
  if (!(await _checkoutTo(String(pivot)))) return;
  const store = useSessionStore.getState();
  store.setComposerInput(_bodyText(node));
  store.focusComposer();
  showToast(translateText(
    "Back at the fork point — edit and send to start the new branch",
    "已回到分叉点，改完发送即开新分支"));
}

function _showRawJson(node: GNode, rect: DOMRect): void {
  const box = document.createElement("div");
  box.className = "dag-inspector dag-inspector-raw";
  const head = document.createElement("div");
  head.className = "dag-inspector-title";
  head.textContent = `${_roleLabel(node)} · raw`;
  box.appendChild(head);
  const pre = document.createElement("pre");
  pre.className = "dag-inspector-raw-body";
  pre.textContent = JSON.stringify(node, _dropRenderKeys, 2);
  box.appendChild(pre);
  const acts = document.createElement("div");
  acts.className = "dag-inspector-actions";
  acts.appendChild(_actionButton(translateText("Copy", "复制"), () => {
    _copy(JSON.stringify(node, _dropRenderKeys, 2),
      translateText("JSON copied", "已复制 JSON"));
  }));
  box.appendChild(acts);
  _openLayer(box, rect);
}

/** Drop the renderer's own scratch fields from the raw view. ``_depth``
 *  / ``_lane`` / ``children`` are layout output, not node data, and
 *  ``children`` is cyclic enough to blow up ``JSON.stringify``. */
function _dropRenderKeys(key: string, value: unknown): unknown {
  if (key === "children" || key.startsWith("_")) return undefined;
  return value;
}

function _actionButton(label: string, onClick: () => void): HTMLElement {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "dag-inspector-action";
  b.textContent = label;
  b.addEventListener("click", (e) => {
    e.stopPropagation();
    onClick();
  });
  return b;
}

// ── context menu ───────────────────────────────────────────────────

function _menuItem(label: string, onClick: () => void, muted = false): HTMLElement {
  const d = document.createElement("button");
  d.type = "button";
  d.className = "dag-menu-item" + (muted ? " is-muted" : "");
  d.textContent = label;
  d.addEventListener("click", (e) => {
    e.stopPropagation();
    closeTooltipDetail();
    onClick();
  });
  return d;
}

/** Expand the node's card with the verb list at its bottom. The card
 *  itself (rows, position, lifetime) is ../tooltip.ts's — this builds
 *  only the verbs. */
export function showNodeMenu(node: GNode, el: Element): void {
  const menu = document.createElement("div");
  menu.className = "dag-menu-verbs";
  menu.addEventListener("click", (e) => e.stopPropagation());

  const id = String(node.id);
  // Only chain-level turns are checkout/fork targets — a
  // function-internal node (caller nested under another call) is
  // execution machinery, and the backend rejects it. Offering the
  // action and then toasting the rejection was worse than not
  // offering it.
  if (_isChainTurn(node)) {
    menu.appendChild(_menuItem(
      translateText("Checkout this branch", "checkout 到此分支"),
      () => { void _checkoutTo(id); }));
    menu.appendChild(_menuItem(
      translateText("Fork from this node", "从此节点 fork"),
      () => { void _checkoutTo(id); }));
    // Editing means writing a REPLACEMENT for a message, so it only has
    // meaning where a message is the user's own words.
    if (node.role === "user" && node.display !== "root") {
      menu.appendChild(_menuItem(
        translateText("Fork & edit this message", "fork 并编辑此消息"),
        () => { void forkAndEditNode(node); }));
    }
    // This separator divides the chain verbs from the generic ones —
    // with no chain verbs above it, it would stack against the card's
    // own info/verbs divider as a doubled line.
    const sep = document.createElement("div");
    sep.className = "dag-menu-sep";
    menu.appendChild(sep);
  }
  menu.appendChild(_menuItem(translateText("Copy node id", "复制节点 id"), () => {
    _copy(id, translateText("Node id copied", "已复制节点 id"));
  }, true));
  menu.appendChild(_menuItem(translateText("View raw JSON", "查看原始 JSON"), () => {
    _showRawJson(node, el.getBoundingClientRect());
  }, true));

  expandTooltip(node, el, menu);
}
