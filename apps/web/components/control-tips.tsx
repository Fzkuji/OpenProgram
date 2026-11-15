"use client";

import { useEffect, useState } from "react";
import { createPortal } from "react-dom";

import { Kbd } from "@/components/ui/kbd";

/** Names a control on hover with the app's own tip, never the system's.
 *  - A collapsed rail shows icons only, so each row names itself, as the
 *    Dock does: its own label (hidden while collapsed), or `data-rail-tip`.
 *    The rail toggles name themselves open too.
 *  - Any other control with a `title` (or `data-tip`) shows that instead
 *    of the native tooltip: the title moves to `data-tip` on first hover,
 *    and becomes the aria-label when the control has none.
 *  A vertical strip puts `data-tip-side="right"` (or left) on its
 *  controls. A control with a key puts it in `data-tip-keys`
 *  (space-separated, e.g. "⇧1" or "⌘ Z"): the keys show as key caps at the
 *  end of the tip, after all of its text, never as text mixed into it. Only controls and glyph-only marks get tips; text keeps no hover tip,
 *  and no element carries a native `title`. The first tip waits so
 *  a pointer passing over never flashes them; moving on to the next
 *  control swaps it at once. */
const RAIL = ".sidebar.collapsed :is(.sidebar-nav-item, [data-rail-tip]), .sidebar .sidebar-toggle";
// An explicit `data-tip` opts any element in: a clickable row built from a
// div, or a glyph-only mark (a capability icon, a status dot) whose meaning
// is otherwise invisible.
const CONTROL = ":is(button, a[href], [role=button], [role=tab], [role=menuitem], [role=switch], summary):is([title], [data-tip]), [data-tip]";
const DWELL_MS = 600, CARRY_MS = 400, MARGIN = 8;

type Side = "left" | "right" | "top" | "bottom";
type Tip = { label: string; keys: string[]; x: number; y: number; side: Side };

function labelOf(el: HTMLElement): string {
  if (el.matches(RAIL)) {
    return (el.dataset.railTip
      || (el.classList.contains("sidebar-toggle") ? el.getAttribute("aria-label") : null)
      || el.querySelector<HTMLElement>(".sidebar-nav-label")?.textContent
      || el.getAttribute("aria-label")
      || "").trim();
  }
  const title = el.getAttribute("title");
  if (title) {
    el.dataset.tip = title;
    el.removeAttribute("title");
    if (!el.hasAttribute("aria-label") && !el.textContent?.trim()) el.setAttribute("aria-label", title);
  }
  return (el.dataset.tip ?? "").trim();
}

function placeTip(el: HTMLElement, label: string): Tip {
  const keys = (el.dataset.tipKeys ?? "").split(/\s+/).filter(Boolean);
  return { ...anchor(el), label, keys };
}

function anchor(el: HTMLElement): Omit<Tip, "label" | "keys"> {
  const r = el.getBoundingClientRect();
  if (el.closest(".sidebar")) {
    const right = !!el.closest(".right-sidebar");
    return { y: r.top + r.height / 2, x: right ? r.left - 10 : r.right + 10, side: right ? "left" : "right" };
  }
  // A vertical strip of controls names them beside it, where the tip
  // covers no neighbour: `data-tip-side`.
  const side = el.dataset.tipSide;
  if (side === "right" || side === "left") {
    return { y: r.top + r.height / 2, x: side === "right" ? r.right + 10 : r.left - 10, side };
  }
  // Below a control near the top of the window (the tab strip), above
  // anything else.
  const below = r.top < 120;
  return { x: r.left + r.width / 2, y: below ? r.bottom + 6 : r.top - 6, side: below ? "bottom" : "top" };
}

export function ControlTips() {
  const [tip, setTip] = useState<Tip | null>(null);
  useEffect(() => {
    let timer: number | undefined, current: HTMLElement | null = null, hiddenAt = 0, shown = false;
    const place = (el: HTMLElement) => {
      const label = labelOf(el);
      if (!label) return;
      shown = true;
      setTip(placeTip(el, label));
    };
    const hide = () => {
      window.clearTimeout(timer);
      if (shown) hiddenAt = performance.now();
      shown = false;
      current = null;
      setTip(null);
    };
    const over = (e: PointerEvent) => {
      const target = e.target as Element | null;
      const el = target?.closest?.<HTMLElement>(RAIL) ?? target?.closest?.<HTMLElement>(CONTROL) ?? null;
      if (el === current) return;
      const carry = shown || performance.now() - hiddenAt < CARRY_MS;
      hide();
      if (!el || e.pointerType === "touch") return;
      current = el;
      // Take the title now, so the system's own tip never comes up.
      if (!el.matches(RAIL)) labelOf(el);
      if (carry) place(el);
      else timer = window.setTimeout(() => { if (current === el && el.isConnected) place(el); }, DWELL_MS);
    };
    // A press acts on the control: its name is no longer needed.
    const press = () => { if (current) { window.clearTimeout(timer); shown = false; setTip(null); } };
    const leave = (e: PointerEvent) => { if (!e.relatedTarget) hide(); };
    document.addEventListener("pointerover", over);
    document.addEventListener("pointerdown", press, true);
    document.addEventListener("pointerout", leave);
    document.addEventListener("scroll", hide, true);
    window.addEventListener("blur", hide);
    return () => {
      window.clearTimeout(timer);
      document.removeEventListener("pointerover", over);
      document.removeEventListener("pointerdown", press, true);
      document.removeEventListener("pointerout", leave);
      document.removeEventListener("scroll", hide, true);
      window.removeEventListener("blur", hide);
    };
  }, []);
  if (!tip) return null;
  const transform = tip.side === "left" ? "translate(-100%, -50%)"
    : tip.side === "right" ? "translate(0, -50%)"
    : tip.side === "bottom" ? "translate(-50%, 0)" : "translate(-50%, -100%)";
  return createPortal(
    <div
      role="tooltip"
      className="hover-tip rail-tip pointer-events-none fixed z-(--z-drag) w-max max-w-[min(360px,calc(100vw-16px))] whitespace-pre-line [overflow-wrap:anywhere] rounded-md px-3 py-1.5 text-fs-xs"
      data-side={tip.side}
      ref={node => {
        // Keep a tip near the window edge inside the window.
        if (!node) return;
        node.style.marginLeft = "";
        const r = node.getBoundingClientRect();
        const dx = r.left < MARGIN ? MARGIN - r.left : r.right > window.innerWidth - MARGIN ? window.innerWidth - MARGIN - r.right : 0;
        if (dx) node.style.marginLeft = `${dx}px`;
      }}
      style={{ top: tip.y, left: tip.x, transform }}
    >
      {tip.keys.length ? <TipWithKeys label={tip.label} keys={tip.keys} /> : tip.label}
    </div>,
    document.body,
  );
}

/** The whole text first, then the key caps at the end of its last line. */
function TipWithKeys({ label, keys }: { label: string; keys: string[] }) {
  return (
    <>
      {label}
      <span className="ml-2 inline-flex translate-y-[-1px] items-center gap-1 align-middle">
        {keys.map((key) => (
          <Kbd key={key} className="bg-[color-mix(in_srgb,var(--text-on-tooltip)_20%,transparent)] text-[var(--text-on-tooltip)]">{key}</Kbd>
        ))}
      </span>
    </>
  );
}
