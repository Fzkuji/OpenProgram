/**
 * History DAG node tooltip.
 *
 * Lifecycle and design choices:
 *
 *   * **Visibility is driven only by hover-on-node.** Mouse over the
 *     node → card shows. Mouse leaves the node → card hides
 *     immediately. The card itself is ``pointer-events: none`` so
 *     moving the cursor across it doesn't "stick" the popup.
 *   * **Collapsed by default, expanded after a dwell.** The first
 *     200ms of hover shows a compact card (1 input line + 1 output
 *     line). If the cursor stays on the node for ``DWELL_MS``
 *     additional time, the card expands to show every schema field.
 *   * **Position next to the node, not the mouse.** Default to the
 *     right of the node's bounding box; flip to the left if it
 *     would overflow the panel. Vertical anchor follows the node's
 *     top, clamped inside the panel.
 *
 * No row labels are reinvented — each line uses the actual schema
 * key (``name`` / ``input`` / ``output`` / ``label`` / ``head_id`` /
 * ``source_commit_id`` / ``embed_count`` / ``embed_tokens`` / ...).
 */

/* eslint-disable @typescript-eslint/no-explicit-any */

import { type GNode } from "./types";

const DWELL_MS = 3000;       // hover-stay before expansion
const COLLAPSED_VAL = 80;    // chars per row in collapsed view
const EXPANDED_VAL = 600;    // chars per block in expanded view
const GAP = 10;              // px gap between node and card

let _tooltip: HTMLDivElement | null = null;
let _dwellTimer = 0;
let _currentId: string | null = null;

export function ensureTooltip(body: HTMLElement): HTMLDivElement {
  if (_tooltip && _tooltip.parentElement === body) return _tooltip;
  _tooltip = document.createElement("div");
  _tooltip.className = "history-tooltip";
  body.appendChild(_tooltip);
  return _tooltip;
}

export function hideTooltip(): void {
  if (_dwellTimer) {
    window.clearTimeout(_dwellTimer);
    _dwellTimer = 0;
  }
  _currentId = null;
  if (_tooltip) {
    _tooltip.classList.remove("visible");
    _tooltip.classList.remove("expanded");
  }
}

export function resetTooltip(): void {
  _tooltip = null;
  _currentId = null;
  if (_dwellTimer) {
    window.clearTimeout(_dwellTimer);
    _dwellTimer = 0;
  }
}

/** Show the tooltip for ``node`` next to ``nodeRect`` (in viewport
 *  coordinates). The function is idempotent on repeated calls with
 *  the same node — it only rebuilds the DOM when the node changes,
 *  so a fast-moving cursor over the same node doesn't strobe. */
export function showTooltip(
  body: HTMLElement,
  node: GNode,
  nodeRect: DOMRect,
): void {
  const tip = ensureTooltip(body);
  const id = String(node.id || "");

  if (id !== _currentId) {
    _currentId = id;
    _render(tip, node, /* expanded */ false);
    if (_dwellTimer) window.clearTimeout(_dwellTimer);
    _dwellTimer = window.setTimeout(() => {
      // Only expand if we're still hovering the same node.
      if (_currentId === id && _tooltip) {
        _render(_tooltip, node, true);
        _tooltip.classList.add("expanded");
        // Width change is a CSS transition; offsetWidth reads the
        // pre-transition value until the next frame. Re-position
        // across a few frames so the card never overflows the panel
        // right edge while expanding.
        _position(_tooltip, body, nodeRect);
        requestAnimationFrame(() => {
          if (_currentId === id && _tooltip) _position(_tooltip, body, nodeRect);
        });
        window.setTimeout(() => {
          if (_currentId === id && _tooltip) _position(_tooltip, body, nodeRect);
        }, 220);
      }
    }, DWELL_MS);
  }
  tip.classList.add("visible");
  _position(tip, body, nodeRect);
}

// ── render ─────────────────────────────────────────────────────────

type Row =
  | { kind: "kv"; key: string; value: string }
  | { kind: "block"; key: string; value: string };

function _render(tip: HTMLElement, node: GNode, expanded: boolean): void {
  tip.innerHTML = "";
  _appendHeader(tip, node);
  const rows = expanded ? _rowsExpanded(node) : _rowsCollapsed(node);
  rows.forEach((row) => {
    if (row.kind === "block") _appendBlock(tip, row.key, row.value, expanded);
    else _appendKv(tip, row.key, row.value);
  });
}

function _kindLabel(node: GNode): string {
  const fn = node.function;
  // function_call header carries the function name inline so the
  // user can identify it without expanding the card.
  if (fn === "attach") return "function call · attach";
  if (fn === "merge") return "function call · merge";
  if (node.role === "tool") {
    const name = (node.name as string | undefined) || fn;
    return name ? `function call · ${name}` : "function call";
  }
  return (node.role || "?").toString();
}

function _appendHeader(tip: HTMLElement, node: GNode): void {
  const header = document.createElement("div");
  header.className = "history-tooltip-header";
  const title = document.createElement("div");
  title.className = "history-tooltip-kind";
  title.textContent = _kindLabel(node);
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

/** Collapsed view: ≤ 2 lines of body content (input + output, or just
 *  output when no input). Each value clamped to ``COLLAPSED_VAL``. */
function _rowsCollapsed(node: GNode): Row[] {
  const rows: Row[] = [];
  const fn = node.function;
  const role = node.role;
  const out = _outputText(node);

  if (role === "tool") {
    if (typeof node.input === "string" && node.input) {
      rows.push(_block("input", _clamp(node.input, COLLAPSED_VAL)));
    }
    if (out) rows.push(_block("output", _clamp(out, COLLAPSED_VAL)));
    return rows;
  }
  if (fn === "attach" || fn === "merge") {
    if (node.attach_label) {
      rows.push(_kv("label", String(node.attach_label)));
    } else if (node.attach_ref) {
      rows.push(_kv("head_id", String(node.attach_ref).slice(0, COLLAPSED_VAL)));
    }
    if (out) rows.push(_block("output", _clamp(out, COLLAPSED_VAL)));
    return rows;
  }
  // user / llm — just output
  if (out) rows.push(_block("output", _clamp(out, COLLAPSED_VAL * 2)));
  return rows;
}

/** Expanded view: every schema field that has a value. */
function _rowsExpanded(node: GNode): Row[] {
  const rows: Row[] = [];
  const fn = node.function;
  const role = node.role;

  if (role === "tool") {
    if (node.name) rows.push(_kv("name", String(node.name)));
    if (typeof node.input === "string" && node.input) {
      rows.push(_block("input", node.input));
    }
    rows.push(_block("output", _outputText(node)));
    return rows;
  }

  if (fn === "attach" || fn === "merge") {
    rows.push(_kv("name", fn));
    if (node.attach_manual) rows.push(_kv("manual", "true"));
    if (node.attach_label) rows.push(_kv("label", String(node.attach_label)));
    if (node.attach_ref) {
      rows.push(_kv("head_id", String(node.attach_ref)));
    }
    if (node.attach_source_commit_id) {
      rows.push(_kv("source_commit_id", String(node.attach_source_commit_id)));
    }
    if (typeof node.attach_embed_count === "number") {
      rows.push(_kv("embed_count", String(node.attach_embed_count)));
    }
    if (typeof node.attach_embed_tokens === "number") {
      rows.push(_kv("embed_tokens", String(node.attach_embed_tokens)));
    }
    const out = _outputText(node);
    if (out) rows.push(_block("output", out));
    return rows;
  }

  if (role === "assistant" || role === "llm") {
    const meta = (node.llm || {}) as Record<string, unknown>;
    if (typeof meta.model === "string" && meta.model) {
      rows.push(_kv("model", meta.model));
    }
    if (typeof meta.input_tokens === "number") {
      rows.push(_kv("input_tokens", String(meta.input_tokens)));
    }
    if (typeof meta.output_tokens === "number") {
      rows.push(_kv("output_tokens", String(meta.output_tokens)));
    }
    rows.push(_block("output", _outputText(node)));
    return rows;
  }

  // user msg
  rows.push(_block("output", _outputText(node)));
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

function _appendBlock(
  tip: HTMLElement,
  key: string,
  value: string,
  expanded: boolean,
): void {
  const wrap = document.createElement("div");
  wrap.className = "history-tooltip-block";
  const lbl = document.createElement("div");
  lbl.className = "history-tooltip-label";
  lbl.textContent = key;
  wrap.appendChild(lbl);
  const bod = document.createElement("div");
  bod.className = "history-tooltip-body";
  bod.textContent = expanded
    ? _clamp(value || "(empty)", EXPANDED_VAL)
    : (value || "(empty)");
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
 *  region of the page, including the chat area and left sidebar.
 *
 *  Order of preference (each clamped to viewport):
 *    1. Below the node (default).
 *    2. Above the node (if below would clip the bottom of the viewport).
 *    3. Side fallback (if both above/below would clip).
 *
 *  Horizontally we center under the node, then nudge into the viewport
 *  if the card would spill off either edge. */
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
    // Below would clip — try above.
    const aboveTop = nodeRect.top - GAP - th;
    if (aboveTop >= 6) {
      topPx = aboveTop;
    } else {
      // Both clip — pin to the side and clamp inside viewport.
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
