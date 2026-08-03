/**
 * History DAG node hover card — the ONE info surface a node has.
 *
 * There used to be three: a hover tooltip, a second-stage "dwell"
 * expansion of it, and a click-opened inspector popover. Three windows
 * for one node meant the click had two jobs (open a window AND toggle
 * the node's thread), and the popover landed on top of the expansion it
 * had just triggered. Now:
 *
 *   * **Hover** → this card, once, with everything on it. No second
 *     stage, no click popover.
 *   * **Click** → the node's own action only (fold/unfold its thread).
 *   * **Right-click** → the action menu (checkout / fork / copy).
 *
 * Lifecycle:
 *   * Visibility is driven only by hover-on-node: mouse over → card
 *     (after a short delay so sweeping the cursor doesn't strobe);
 *     mouse off → gone. The card is ``pointer-events: none``.
 *   * Position below the node, flipped above when it would clip.
 *
 * No row labels are reinvented — each line uses the actual schema key
 * (``name`` / ``input`` / ``output`` / ``model`` / ``label`` / ...),
 * plus the two facts the old inspector alone carried: the node's
 * context-coverage state and its folded call count, both read off the
 * DOM flags the node drawer stamped so card and picture cannot
 * disagree.
 */

/* eslint-disable @typescript-eslint/no-explicit-any */

import { type GNode } from "./types";
import { _estimateTokens } from "./render/inspector";

const SHOW_DELAY_MS = 450;   // hover-stay before the card appears
const BODY_CHARS = 280;      // chars per body block
const GAP = 10;              // px gap between node and card

let _tooltip: HTMLDivElement | null = null;
let _showTimer = 0;
let _currentId: string | null = null;

export function ensureTooltip(body: HTMLElement): HTMLDivElement {
  if (_tooltip && _tooltip.parentElement === body) return _tooltip;
  _tooltip = document.createElement("div");
  _tooltip.className = "history-tooltip";
  body.appendChild(_tooltip);
  return _tooltip;
}

export function hideTooltip(): void {
  if (_showTimer) {
    window.clearTimeout(_showTimer);
    _showTimer = 0;
  }
  _currentId = null;
  if (_tooltip) _tooltip.classList.remove("visible");
}

export function resetTooltip(): void {
  _tooltip = null;
  _currentId = null;
  if (_showTimer) {
    window.clearTimeout(_showTimer);
    _showTimer = 0;
  }
}

/** Show the card for ``node`` next to ``nodeRect`` (viewport
 *  coordinates). ``el`` is the node's ``<g>`` — the drawer's data-*
 *  flags on it carry the coverage / thread facts. Idempotent on
 *  repeated calls with the same node. */
export function showTooltip(
  body: HTMLElement,
  node: GNode,
  nodeRect: DOMRect,
  el?: Element | null,
): void {
  const tip = ensureTooltip(body);
  const id = String(node.id || "");

  if (id !== _currentId) {
    _currentId = id;
    tip.classList.remove("visible");
    if (_showTimer) window.clearTimeout(_showTimer);
    // 停留 SHOW_DELAY_MS 才出卡——扫过节点不弹窗。
    _showTimer = window.setTimeout(() => {
      if (_currentId !== id || !_tooltip) return;
      _render(_tooltip, node, el || null);
      _tooltip.classList.add("visible");
      _position(_tooltip, body, nodeRect);
      // 内容宽度在下一帧才量得准，再对一次位，防溢出右缘。
      requestAnimationFrame(() => {
        if (_currentId === id && _tooltip) _position(_tooltip, body, nodeRect);
      });
    }, SHOW_DELAY_MS);
  }
  // Repeated mousemoves over the same node only re-position an
  // already-visible card — they must not bypass the show delay.
  if (tip.classList.contains("visible")) _position(tip, body, nodeRect);
}

// ── render ─────────────────────────────────────────────────────────

type Row =
  | { kind: "kv"; key: string; value: string }
  | { kind: "block"; key: string; value: string };

function _render(tip: HTMLElement, node: GNode, el: Element | null): void {
  tip.innerHTML = "";
  _appendHeader(tip, node, el);
  _rows(node, el).forEach((row) => {
    if (row.kind === "block") _appendBlock(tip, row.key, row.value);
    else _appendKv(tip, row.key, row.value);
  });
}

function _kindLabel(node: GNode, el: Element | null): string {
  if (node.display === "root") return "root";
  if (Array.isArray((node as Record<string, unknown>).covers_ids)) {
    return "context/summary";
  }
  // A spawn root is a user turn by role, but "user" is the wrong answer
  // to "what am I looking at" — it is another agent (dag/rendering.md
  // §12), and this card is where its name lives now that the canvas
  // draws no captions.
  if ((node as Record<string, unknown>).source === "agent_spawn"
      && !node.predecessor) {
    const nm = (el?.getAttribute("data-spawn-name") || "").trim();
    return nm ? `子 agent · ${nm}` : "子 agent";
  }
  const fn = node.function;
  if (fn === "attach") return "function call · attach";
  if (fn === "merge") return "function call · merge";
  if (node.role === "tool") {
    const name = (node.name as string | undefined) || fn;
    return name ? `function call · ${name}` : "function call";
  }
  return (node.role || "?").toString();
}

function _appendHeader(
  tip: HTMLElement,
  node: GNode,
  el: Element | null,
): void {
  const header = document.createElement("div");
  header.className = "history-tooltip-header";
  const title = document.createElement("div");
  title.className = "history-tooltip-kind";
  title.textContent = _kindLabel(node, el);
  header.appendChild(title);
  const chips: string[] = [];
  if (node.source && node.source !== "web") chips.push(node.source);
  if (node.is_error) chips.push("error");
  if (chips.length) {
    const meta = document.createElement("div");
    meta.className = "history-tooltip-chips";
    chips.forEach((c) => {
      const chip = document.createElement("span");
      chip.className = "history-tooltip-chip";
      chip.textContent = c;
      meta.appendChild(chip);
    });
    header.appendChild(meta);
  }
  tip.appendChild(header);
}

/** How the node stands relative to the next request, read off the DOM
 *  flags the node drawer stamped — the card can never disagree with
 *  the picture beside it. */
function _coverageText(el: Element): string {
  if (el.getAttribute("data-failed") === "1") return "失败轮 · 已留档";
  if (el.getAttribute("data-ghost") === "1") return "已折叠进摘要";
  if (el.classList.contains("out-of-context")) return "不在覆盖内";
  return "✓ 在覆盖内";
}

/** Every field that has a value — one card, one stage. */
function _rows(node: GNode, el: Element | null): Row[] {
  const rows: Row[] = [];
  const fn = node.function;
  const role = node.role;

  if (role === "tool") {
    if (node.name) rows.push(_kv("name", String(node.name)));
    if (typeof node.input === "string" && node.input) {
      rows.push(_block("input", node.input));
    }
    const out = _outputText(node);
    if (out) rows.push(_block("output", out));
  } else if (fn === "attach" || fn === "merge") {
    if (node.attach_manual) rows.push(_kv("manual", "true"));
    if (node.attach_label) rows.push(_kv("label", String(node.attach_label)));
    if (node.attach_ref) rows.push(_kv("head_id", String(node.attach_ref)));
    if (node.attach_source_commit_id) {
      rows.push(_kv("source_commit_id", String(node.attach_source_commit_id)));
    }
    const out = _outputText(node);
    if (out) rows.push(_block("output", out));
  } else {
    let tokensShown = false;
    if (role === "assistant" || role === "llm") {
      const meta = (node.llm || {}) as Record<string, unknown>;
      if (typeof meta.model === "string" && meta.model) {
        rows.push(_kv("model", meta.model));
      }
      if (typeof meta.input_tokens === "number"
          || typeof meta.output_tokens === "number") {
        rows.push(_kv("tokens",
          `${meta.input_tokens ?? "?"} → ${meta.output_tokens ?? "?"}`));
        tokensShown = true;
      }
    }
    if (!tokensShown && node.display !== "root") {
      const tok = _estimateTokens(node);
      rows.push(_kv(tok.exact ? "tokens" : "tokens（估）",
        tok.n.toLocaleString()));
    }
    const out = _outputText(node);
    if (out) rows.push(_block("output", out));
  }

  // The facts the click-popover alone used to carry (§11/§12):
  if (el) {
    const threadN = el.getAttribute("data-thread");
    if (threadN) rows.push(_kv("调用", `${threadN} 次`));
    const summaryN = el.getAttribute("data-summary");
    if (summaryN) rows.push(_kv("覆盖", `${summaryN} 轮`));
    if (node.display !== "root") {
      rows.push(_kv("上下文", _coverageText(el)));
    }
  }
  if (node.display !== "root") {
    rows.push(_kv("id", String(node.id).slice(0, 12)));
  }
  return rows;
}

function _outputText(node: GNode): string {
  const v = node.preview ?? node.content ?? node.output ?? "";
  return typeof v === "string" ? v : String(v);
}

function _kv(key: string, value: string): Row {
  return { kind: "kv", key, value };
}

function _block(key: string, value: string): Row {
  return { kind: "block", key, value };
}

function _appendKv(tip: HTMLElement, key: string, value: string): void {
  const row = document.createElement("div");
  row.className = "history-tooltip-kv";
  const ks = document.createElement("span");
  ks.className = "history-tooltip-kv-key";
  ks.textContent = key;
  const vs = document.createElement("span");
  vs.className = "history-tooltip-kv-val";
  vs.textContent = value;
  row.appendChild(ks);
  row.appendChild(vs);
  tip.appendChild(row);
}

function _appendBlock(tip: HTMLElement, key: string, value: string): void {
  const wrap = document.createElement("div");
  wrap.className = "history-tooltip-block";
  const lbl = document.createElement("div");
  lbl.className = "history-tooltip-label";
  lbl.textContent = key;
  wrap.appendChild(lbl);
  const bod = document.createElement("div");
  bod.className = "history-tooltip-body";
  bod.textContent = _clamp(value || "(empty)", BODY_CHARS);
  wrap.appendChild(bod);
  tip.appendChild(wrap);
}

function _clamp(s: string, n: number): string {
  const trimmed = s.replace(/\s+/g, " ").trim();
  if (trimmed.length <= n) return trimmed;
  return trimmed.slice(0, n).replace(/\s+\S*$/, "") + "…";
}

// ── position ───────────────────────────────────────────────────────

/** Anchor the card BELOW the node, never overlapping it. The user
 *  is looking at the node when they hover — the card must not
 *  obscure that. Card is ``fixed`` so it can float across any
 *  region of the page.
 *
 *  Order of preference (each clamped to viewport):
 *    1. Below the node (default).
 *    2. Above the node (if below would clip the bottom of the viewport).
 *    3. Side fallback (if both above/below would clip). */
function _position(tip: HTMLElement, body: HTMLElement, nodeRect: DOMRect): void {
  void body;
  tip.style.position = "fixed";
  const tw = tip.offsetWidth;
  const th = tip.offsetHeight;
  const vw = window.innerWidth;
  const vh = window.innerHeight;

  // Vertical: below the node by default.
  let topPx = nodeRect.bottom + GAP;
  if (topPx + th > vh - 6) {
    const aboveTop = nodeRect.top - GAP - th;
    if (aboveTop >= 6) {
      topPx = aboveTop;
    } else {
      topPx = Math.max(6, vh - 6 - th);
    }
  }

  // Horizontal: center under the node, then clamp.
  let leftPx = nodeRect.left + nodeRect.width / 2 - tw / 2;
  if (leftPx + tw > vw - 6) leftPx = vw - 6 - tw;
  if (leftPx < 6) leftPx = 6;

  tip.style.left = leftPx + "px";
  tip.style.top = topPx + "px";
}
