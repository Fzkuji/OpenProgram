"use client";

import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { motion, type Transition } from "framer-motion";

import { topLevelTabs } from "@/lib/browser/web-tab/web-page-management";
import { canvasGeometry, canvasViewport, leaves, removePane, rowLayout, type LayoutNode } from "@/lib/tabs/canvas/canvas-layout";
import { desktopBridge } from "@/lib/desktop/desktop-bridge";
import { commitCanvasDrop, type CanvasDrop } from "@/lib/tabs/canvas/canvas-drag";
import { canvasPanesMoving } from "@/lib/tabs/canvas/canvas-motion";
import { captureVisiblePanes, captureWebTabs, forgetSnapshots, pruneSnapshots, restoreSnapshots, useWindowSnapshots } from "@/lib/tabs/canvas/window-snapshots";
import { browseOverview, closeOverview, dismissOverview, holdOverviewLanding, openOverview, overviewDropAt, overviewLandingFrom, setOverviewZoomOut, useCanvasOverview, type OverviewSide } from "@/lib/tabs/canvas/canvas-overview";
import { centerTabStripEntries } from "@/lib/tabs/center-tab-groups";
import { captureCanvasLanding, type CanvasEnter } from "@/lib/tabs/canvas/canvas-transition";
import { useCenterTabs, type CenterTab } from "@/lib/tabs/center-tabs-store";
import { useTranslation } from "@/lib/i18n";
import { useEffectEvent } from "@/lib/hooks/use-effect-event";
import { CompoundMemberIcon, labelOf } from "../strip/tab-items";
import styles from "./canvas-overview.module.css";

/** `first`: the card's first tab in the strip, which a window moved in
 *  front of the card goes before. */
type Card = { key: string; targetId: string; activateId: string; root: LayoutNode; title: string; first: string };

const ZONE: Record<OverviewSide, React.CSSProperties> = {
  left: { left: 0, top: 0, width: "50%", height: "100%" },
  right: { left: "50%", top: 0, width: "50%", height: "100%" },
  top: { left: 0, top: 0, width: "100%", height: "50%" },
  bottom: { left: 0, top: "50%", width: "100%", height: "50%" },
};
const GAP = 24, CAPTION = 26;
const ZOOM_MS = 340, ZOOM_EASING = "cubic-bezier(.2, .8, .2, 1)";
const ZOOM: Transition = { duration: ZOOM_MS / 1000, ease: [0.2, 0.8, 0.2, 1] };
/** Every other layout change of a picture (a resize) just jumps. */
const INSTANT: Transition = { duration: 0 };
/** Cards move to their new places on a spring when the grid changes (a
 *  card picked up, dropped back, split or merged), and a card that comes
 *  in grows out of its place. */
const REFLOW: Transition = { type: "spring", stiffness: 380, damping: 32, mass: 0.8 };
const ENTER = { opacity: 0, scale: 0.94 };
type Box = { left: number; top: number; width: number; height: number };

/** A card is the window shrunk whole: the center area it fills (the
 *  overview covers the same area), so zooming is a plain scale and every
 *  edge and corner lands on itself. A lone pane sits flush in the center
 *  card (1rem corners); split panes are cards of their own, 1px inside
 *  their slots with 16px corners (app-shell.tsx, styles/base/layout.css). */
function windowBox(canvas: HTMLElement): Box {
  return { left: 0, top: 0, width: canvas.clientWidth, height: canvas.clientHeight };
}
const PANE_INSET = 1, PANE_RADIUS = 12;
// The centre card's corner (styles/base/layout.css .center-col: var(--radius-xl)).
const centerRadius = () => parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--radius-xl")) || 12;
const reducedMotion = () => typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/** Columns and card width that make n cards of this aspect as large as
 *  the area allows, never wider than half of it (Mission Control keeps
 *  even a lone window visibly smaller than the desktop). */
function fit(n: number, width: number, height: number, aspect: number) {
  let best = 0;
  for (let cols = 1; cols <= n; cols++) {
    const rows = Math.ceil(n / cols);
    best = Math.max(best, Math.min((width - GAP * (cols - 1)) / cols, ((height - GAP * (rows - 1)) / rows - CAPTION) * aspect));
  }
  return Math.max(96, Math.min(best, width * 0.5));
}

/** A trackpad pinch arrives as a wheel event with ctrlKey. Its legacy
 *  wheelDeltaY is ±120 like a mouse notch, so the two cannot be told apart;
 *  Control with a mouse wheel zooms the same way, as in a browser. */
function isPinch(e: WheelEvent) {
  return e.ctrlKey;
}

/** Every window in the strip as a card that keeps its split, over the
 *  center area. See lib/tabs/canvas/canvas-overview.ts for when it opens. */
export function CanvasOverview() {
  const { t, text } = useTranslation();
  const { mode, sourceIds, hover, label, landing, landed, slot } = useCanvasOverview();
  const tabs = useCenterTabs(s => s.tabs), groups = useCenterTabs(s => s.groups), activeId = useCenterTabs(s => s.activeId);
  const rootRef = useRef<HTMLDivElement>(null);
  // How this overview opened, kept until it closes.
  const openedAs = useRef<"drag" | "browse" | null>(null);
  const openedAt = useRef(0);
  if (!mode) openedAs.current = null;
  else if (!openedAs.current) { openedAs.current = mode; openedAt.current = performance.now(); }
  const [area, setArea] = useState({ width: 0, height: 0, canvasWidth: 0, canvasHeight: 0 });

  // Pinch in on the center area (or the strip) opens the overview, pinch
  // out closes it, as in Safari. Content that zooms itself on a pinch
  // (the context graph) prevents the default first and keeps the gesture.
  useEffect(() => {
    let total = 0, spent = false, timer: number | undefined;
    const wheel = (e: WheelEvent) => {
      if (!isPinch(e) || e.defaultPrevented) return;
      const target = e.target as Element | null;
      if (!target?.closest?.("[data-canvas-root], [data-canvas-overview], .desktop-tab-row, [role='tablist']")) return;
      window.clearTimeout(timer);
      timer = window.setTimeout(() => { total = 0; spent = false; }, 220);
      if (spent) return;
      total += e.deltaY;
      const open = useCanvasOverview.getState().mode;
      if (!open && total > 30) { spent = true; void captureVisiblePanes().then(() => openOverview("browse")); }
      else if (open === "browse" && total < -30) { spent = true; dismissOverview(); }
    };
    window.addEventListener("wheel", wheel, { passive: true });
    // A web page is its own native view, so its pinch comes from the desktop
    // shell, only when the page is at its normal size.
    const offWebPinch = desktopBridge()?.webTab.onCommand?.(command => {
      if (command.command === "overview" && !useCanvasOverview.getState().mode) void captureVisiblePanes().then(() => openOverview("browse"));
    });
    return () => { window.removeEventListener("wheel", wheel); window.clearTimeout(timer); offWebPinch?.(); };
  }, []);

  // A window on screen is pictured once it settles (after a switch or a
  // layout change), when the App loses focus or hides, and every 30s while
  // nobody is using it, so its card (saved across restarts) looks like it
  // did when it was last seen.
  useEffect(() => {
    let timer: number | undefined, lastInput = Date.now();
    const input = () => { lastInput = Date.now(); };
    const idle = window.setInterval(() => {
      if (document.visibilityState === "visible" && Date.now() - lastInput > 5000 && !canvasPanesMoving()) void captureVisiblePanes([], 260, { background: true });
    }, 30000);
    const hidden = () => { if (document.visibilityState === "hidden") now(); };
    const later = () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(() => { if (!canvasPanesMoving()) void captureVisiblePanes([], 260, { background: true }); }, 900);
    };
    const now = () => { if (!canvasPanesMoving()) void captureVisiblePanes([], 260, { background: true }); };
    void restoreSnapshots();
    later();
    const off = useCenterTabs.subscribe((s, prev) => {
      if (s.activeId !== prev.activeId || s.groups !== prev.groups) later();
      if (s.tabs !== prev.tabs) pruneSnapshots(new Set(s.tabs.map(tab => tab.id)));
    });
    // Pictures in another theme would flash against the live window.
    const look = new MutationObserver(() => { forgetSnapshots(); later(); });
    look.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme", "data-theme-mode"] });
    window.addEventListener("blur", now);
    document.addEventListener("visibilitychange", hidden);
    window.addEventListener("pointerdown", input, true);
    window.addEventListener("keydown", input, true);
    window.addEventListener("wheel", input, { capture: true, passive: true });
    return () => {
      off(); look.disconnect(); window.clearTimeout(timer); window.clearInterval(idle);
      window.removeEventListener("blur", now);
      document.removeEventListener("visibilitychange", hidden);
      window.removeEventListener("pointerdown", input, true);
      window.removeEventListener("keydown", input, true);
      window.removeEventListener("wheel", input, true);
    };
  }, []);

  useEffect(() => {
    if (mode !== "browse") return;
    // Escape here must not also stop a running reply in the chat below.
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") { e.preventDefault(); e.stopImmediatePropagation(); dismissOverview(); } };
    // Anything outside (a tab, a sidebar) dismisses it; the strip button toggles.
    const press = (e: PointerEvent) => {
      const target = e.target as Element | null;
      if (!target?.closest?.("[data-canvas-overview], [data-overview-toggle]")) closeOverview();
    };
    window.addEventListener("keydown", key, true);
    window.addEventListener("pointerdown", press, true);
    // Focus the overview itself, not a card: a card would wear a focus
    // ring as it zooms in. Tab still reaches the cards.
    rootRef.current?.focus({ preventScroll: true });
    return () => { window.removeEventListener("keydown", key, true); window.removeEventListener("pointerdown", press, true); };
  }, [mode]);

  useLayoutEffect(() => {
    const el = rootRef.current, canvas = el?.parentElement;
    if (!el || !canvas) return;
    // Both modes keep the hint's room, so cards keep their size and place
    // when a drag turns into browsing and back.
    const measure = () => setArea({ width: el.clientWidth - 64, height: el.clientHeight - 112, canvasWidth: canvas.clientWidth, canvasHeight: canvas.clientHeight });
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [mode]);

  const shots = useWindowSnapshots(s => s.shots);
  // Web pages keep a live view even off screen; picture them fresh.
  useEffect(() => {
    if (!mode) return;
    const { tabs: all, groups: grouped } = useCenterTabs.getState();
    const onScreen = new Set([...document.querySelectorAll<HTMLElement>("[data-canvas-root] [data-canvas-content]")].filter(pane => pane.offsetWidth > 0).map(pane => pane.dataset.canvasContent));
    const listed = new Set(topLevelTabs(all, grouped).map(tab => tab.id).concat(grouped.flatMap(group => group.memberIds)));
    captureWebTabs(all.filter(tab => tab.kind === "web" && listed.has(tab.id) && !onScreen.has(tab.id)).map(tab => tab.id));
  }, [mode]);

  // `home`: where the dragged window stood, or right after the window it
  // is being taken out of.
  const { cards, home } = useMemo(() => {
    const cards: Card[] = [];
    let home = -1;
    if (!mode) return { cards, home };
    const byId = new Map(tabs.map(tab => [tab.id, tab]));
    const order = new Map(tabs.map((tab, i) => [tab.id, i]));
    const name = (id: string) => { const tab = byId.get(id); return tab ? labelOf(tab, t, text) : ""; };
    const leaving = new Set(sourceIds);
    for (const entry of centerTabStripEntries({ tabIds: topLevelTabs(tabs, groups).map(tab => tab.id), groups })) {
      let root: LayoutNode = entry.kind === "group" ? (entry.group.canvas ?? rowLayout(entry.group.memberIds)).root : rowLayout([entry.tabId]).root;
      const left = leaves(root).some(p => p.content && leaving.has(p.content));
      for (const p of leaves(root)) if (p.content && leaving.has(p.content)) root = removePane(root, p.id);
      const contents = leaves(root).flatMap(p => p.content && byId.has(p.content) && !byId.get(p.content)!.canvasAnchor ? [p.content] : []);
      if (left && home < 0) home = cards.length + (contents.length ? 1 : 0);
      if (!contents.length) continue;
      const activateId = entry.kind === "group" && contents.includes(entry.group.focusedId) ? entry.group.focusedId : contents[0];
      const first = [...contents].sort((a, b) => order.get(a)! - order.get(b)!)[0];
      cards.push({ key: entry.id, targetId: contents[0], activateId, root, title: contents.map(name).join(" + "), first });
    }
    return { cards, home: home < 0 ? cards.length : home };
  }, [mode, sourceIds, tabs, groups, t, text]);
  // A dragged window keeps a gap among the cards, where it would stand on
  // its own, so the other cards never change size: picking a card up
  // leaves its place empty, as in Mission Control, and the cards move
  // aside wherever the gap goes.
  const gapAt = mode && sourceIds.length ? Math.min(slot ?? home, cards.length) : -1;

  const activeKey = cards.find(card => leaves(card.root).some(p => p.content === activeId))?.key;
  const frame = area.canvasWidth > 0 && area.canvasHeight > 0 ? { width: area.canvasWidth, height: area.canvasHeight } : { width: 1000, height: 625 };
  const viewport = canvasViewport(frame.width, frame.height);
  const aspectNow = frame.width / frame.height;
  const widthNow = area.width > 0 ? fit(Math.max(1, cards.length + (gapAt < 0 ? 0 : 1)), area.width, area.height, aspectNow) : 0;

  // The window zooms between the center area and its card. A card that is
  // "out" or "closing" sits over the window's own place; framer-motion
  // animates its layout as it moves into the grid ("in") or back out.
  const [zoom, setZoom] = useState<{ key: string; box: Box; phase: "out" | "in" | "closing" } | null>(null);
  const zoomAt = (key: string | undefined, phase: "out" | "closing") => {
    const root = rootRef.current, canvas = root?.parentElement;
    const card = key && root ? root.querySelector<HTMLElement>(`[data-overview-card="${CSS.escape(key)}"]`) : null;
    if (!key || !card || !canvas || reducedMotion()) return false;
    setZoom({ key, box: windowBox(canvas), phase });
    return true;
  };

  // Opening: the window on screen shrinks into its card, like Mission
  // Control, so nothing else needs to mark which one it is.
  const entered = useRef(false);
  const enterOverview = useEffectEvent(() => {
    // A landing merge keeps its zoom: the merge itself widens the canvas
    // (a split's cards reach past the center card's edge), which changes
    // widthNow mid-zoom.
    // A drag overview needs no entry zoom, and one that turns into
    // browsing (a drop on empty space) is already open.
    if (mode !== "browse") { entered.current = mode === "drag"; if (!landing) setZoom(null); return; }
    if (entered.current || !widthNow) return;
    entered.current = true;
    if (zoomAt(activeKey, "out")) requestAnimationFrame(() => setZoom(z => z?.phase === "out" ? { ...z, phase: "in" } : z));
  });
  useLayoutEffect(() => enterOverview(), [mode, widthNow]);

  // Closing: the chosen card (or the current one) grows back to fill the
  // center area, then the overview goes. Picking a card switches to its
  // window first, so the live window is ready underneath when it lands.
  const zoomOutTo = (pick: Card | null) => {
    if (zoom?.phase === "closing") return;
    if (pick) window.dispatchEvent(new CustomEvent("op-activate-center-tab", { detail: { tabId: pick.activateId } }));
    if (!zoomAt(pick?.key ?? activeKey, "closing")) closeOverview();
  };
  // framer-motion reports the end of the zoom; should a frame never move
  // (nothing to animate), the zoom still finishes on time.
  // A landing also waits for its merge to commit, so it gets longer.
  const zoomDeadline = useEffectEvent(() => landing ? 2000 : 700);
  useEffect(() => {
    if (zoom?.phase !== "in" && zoom?.phase !== "closing") return;
    const timer = window.setTimeout(() => { if (zoom.phase === "closing") closeOverview(); else setZoom(null); }, zoomDeadline());
    return () => window.clearTimeout(timer);
  }, [zoom]);
  // A drop onto a window: from the release its card zooms out of the grid
  // to fill the center area, as picking a card does when browsing, showing
  // the window as it is, while that window comes up live underneath. The
  // merge waits on the desktop shell (~50ms); as soon as it is ready it
  // commits, and the live canvas takes the zoom over from the card where it
  // has got to: the same curve goes on, now on real content (the pictures
  // are small JPEGs and would blur grown to full size), and at the same
  // time the window's panes narrow from where the card had them while the
  // docked tab slides in beside them, their edges one edge. So the zoom
  // and the split play together. The zoom is played by hand, one uniform
  // scale.
  const growFrom = useRef<DOMRect | null>(null);
  const grow = useRef<Animation | null>(null);
  const [growing, setGrowing] = useState(false);
  const startLanding = useEffectEvent(() => {
    setGrowing(false);
    grow.current = null;
    if (!landing) return;
    const card = cards.find(c => leaves(c.root).some(p => p.content === landing));
    const mini = card && rootRef.current?.querySelector<HTMLElement>(`[data-overview-card="${CSS.escape(card.key)}"] [data-overview-mini]`);
    if (!card || !mini || reducedMotion()) { closeOverview(); return; }
    growFrom.current = mini.getBoundingClientRect();
    if (!zoomAt(card.key, "closing")) { closeOverview(); return; }
    // The window comes up under the overview while the card zooms, as
    // picking a card does, so its panes have mounted and loaded by the
    // time the merge commits and they make room.
    window.dispatchEvent(new CustomEvent("op-activate-center-tab", { detail: { tabId: card.activateId } }));
  });
  useLayoutEffect(() => startLanding(), [landing]);
  useLayoutEffect(() => {
    if (!landing || zoom?.phase !== "closing" || grow.current) return;
    const root = rootRef.current;
    const el = root?.querySelector<HTMLElement>(`[data-overview-card="${CSS.escape(zoom.key)}"] [data-zoomed]`);
    const from = growFrom.current, to = el?.getBoundingClientRect();
    if (!root || !el || !from || !to?.width || !to.height) { closeOverview(); return; }
    const k = from.width / to.width;
    grow.current = el.animate([
      { transformOrigin: "0 0", transform: `translate(${from.left - to.left}px, ${from.top - to.top}px) scale(${k})` },
      { transformOrigin: "0 0", transform: "none" },
    ], { duration: ZOOM_MS, easing: ZOOM_EASING, fill: "forwards" });
    // Started on an explicit clock: a compositor animation otherwise stays
    // "pending" to the main thread while the compositor already plays it,
    // and the canvas taking it over could not tell how far it had got.
    grow.current.startTime = document.timeline.currentTime;
    setGrowing(true);
  }, [landing, zoom]);
  const finishLanding = useEffectEvent(() => {
    if (!growing || !landed) return;
    const held = overviewLandingFrom();
    const card = zoom && rootRef.current?.querySelector<HTMLElement>(`[data-overview-card="${CSS.escape(zoom.key)}"] [data-zoomed]`);
    const canvas = rootRef.current?.parentElement;
    if (held && card && canvas && growFrom.current) {
      // Where the card has each pane now, in the canvas's own frame, is
      // where it starts on the canvas.
      const box = card.getBoundingClientRect(), frame = canvas.getBoundingClientRect();
      const s = box.width / frame.width || 1;
      const rects = new Map<string, DOMRect>();
      for (const pane of card.querySelectorAll<HTMLElement>("[data-overview-pane]")) {
        const r = pane.getBoundingClientRect();
        rects.set(pane.dataset.overviewPane!, new DOMRect(frame.left + (r.left - box.left) / s, frame.top + (r.top - box.top) / s, r.width / s, r.height / s));
      }
      const side = held.drop.side === "center" ? "right" : held.drop.side as CanvasEnter;
      const startTime = grow.current?.startTime;
      captureCanvasLanding(rects, Object.fromEntries(held.sourceIds.map(id => [id, side])), { from: growFrom.current, startTime: typeof startTime === "number" ? startTime : null, duration: ZOOM_MS, easing: ZOOM_EASING });
    }
    // Closing applies the held merge; the shell plays the capture as the
    // two commit together.
    closeOverview();
  });
  useEffect(() => finishLanding(), [growing, landed]);
  const zoomOutRef = useRef(zoomOutTo);
  zoomOutRef.current = zoomOutTo;
  useEffect(() => {
    if (mode !== "browse") return;
    setOverviewZoomOut(() => zoomOutRef.current(null));
    return () => setOverviewZoomOut(null);
  }, [mode]);

  // Browsing, any card can be picked up like a dragged tab: dropped on a
  // card's side it merges (and the landing zoom follows, as from the
  // strip), on empty space it stands on its own where its gap is, and
  // anything but a merge leaves the overview browsing. A pane of a split
  // card comes up alone.
  const pickedUp = useRef(false);
  const pickUp = (e: React.PointerEvent<HTMLElement>, card: Card) => {
    if (mode !== "browse" || e.button !== 0 || zoom?.phase === "closing") return;
    const split = leaves(card.root).length > 1;
    const pane = split ? (e.target as Element).closest<HTMLElement>("[data-overview-pane]") : null;
    const ids = pane?.dataset.overviewPane ? [pane.dataset.overviewPane] : leaves(card.root).flatMap(p => p.content ? [p.content] : []);
    const source = pane ?? e.currentTarget.querySelector<HTMLElement>("[data-overview-mini]");
    if (!ids.length || !source) return;
    const pointerId = e.pointerId, start = { x: e.clientX, y: e.clientY };
    let ghost: HTMLElement | null = null, grab = { x: 0, y: 0 };
    const follow = (x: number, y: number) => { if (ghost) ghost.style.transform = `translate(${x - grab.x}px, ${y - grab.y}px)`; };
    const move = (ev: PointerEvent) => {
      if (ev.pointerId !== pointerId) return;
      if (!ghost) {
        if (Math.hypot(ev.clientX - start.x, ev.clientY - start.y) < 6) return;
        pickedUp.current = true;
        // The card itself, at its own size as in Mission Control, under
        // the pointer where it was held.
        const r = source.getBoundingClientRect();
        grab = { x: start.x - r.left, y: start.y - r.top };
        ghost = source.cloneNode(true) as HTMLElement;
        for (const node of [ghost, ...ghost.querySelectorAll<HTMLElement>("*")]) {
          node.removeAttribute("data-overview-pane");
          node.removeAttribute("data-overview-mini");
        }
        ghost.classList.add(styles.ghost);
        Object.assign(ghost.style, { position: "fixed", left: "0", top: "0", width: `${r.width}px`, height: `${r.height}px`, margin: "0" });
        ghost.setAttribute("aria-hidden", "true");
        document.body.append(ghost);
        openOverview("drag", ids);
      }
      follow(ev.clientX, ev.clientY);
      ghost.toggleAttribute("data-over", overviewDropAt(ev.clientX, ev.clientY)?.action === "dock");
    };
    const finish = (drop: CanvasDrop | null) => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("pointercancel", cancel);
      window.removeEventListener("keydown", key, true);
      if (!ghost) return;
      ghost.remove();
      // The click that follows the release is not a pick.
      window.setTimeout(() => { pickedUp.current = false; }, 0);
      if (drop?.action === "dock") { holdOverviewLanding(drop, ids); commitCanvasDrop(drop, ids); return; }
      // Empty space: the window moves to the gap (a pane comes out of its
      // split there).
      if (drop?.action === "fill") commitCanvasDrop(drop, ids);
      browseOverview();
    };
    const up = (ev: PointerEvent) => { if (ev.pointerId === pointerId) finish(ghost ? overviewDropAt(ev.clientX, ev.clientY) : null); };
    const cancel = () => finish(null);
    const key = (ev: KeyboardEvent) => { if (ev.key === "Escape" && ghost) { ev.preventDefault(); ev.stopImmediatePropagation(); finish(null); } };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", cancel);
    window.addEventListener("keydown", key, true);
  };

  if (!mode) return null;
  const activeCard = cards.find(card => leaves(card.root).some(p => p.content === activeId))?.key;
  const aspect = aspectNow, cardWidth = widthNow, radius = centerRadius();
  // A card pictures the canvas viewport: each pane sits where it sits on
  // screen, so the zoom lands every pane on itself.
  const byId = new Map(tabs.map(tab => [tab.id, tab]));
  const opening = performance.now() - openedAt.current < 150;
  const activate = (card: Card) => { if (!pickedUp.current) zoomOutTo(card); };
  // Only a drag says where it will land; browsing needs no instructions,
  // but keeps the hint's room (as a landing does while it fades), so the
  // grid never re-centres.
  const hint = mode === "drag"
    ? label || text("Drop on a window to merge · on empty space to open on its own · outside the window for a new window", "拖到某个窗口上合并 · 空白处单独打开 · 拖出窗口新开一个")
    : "";

  // The gap is a card with nothing in it; a drop there opens the window
  // on its own. It never wears a frame, as in Mission Control.
  const gapCard = (
    <motion.div key="overview-gap" className={styles.card} style={{ width: cardWidth || undefined }} aria-hidden="true"
      data-overview-slot=""
      layout={!landing} initial={false} transition={REFLOW}>
      <span className="block w-full" style={{ aspectRatio: `${aspect}`, borderRadius: radius * (cardWidth ? cardWidth / frame.width : 1) }} />
      <span className={styles.caption}>{"\u00a0"}</span>
    </motion.div>
  );

  return (
    <div
      ref={rootRef}
      className={styles.overview}
      data-canvas-overview={mode}
      data-opened-as={openedAs.current ?? undefined}
      data-native-view-occluder="true"
      data-leaving={zoom?.phase === "closing" || undefined}
      data-landing={landing ? "" : undefined}
      role={mode === "browse" ? "dialog" : undefined}
      tabIndex={mode === "browse" ? -1 : undefined}
      aria-label={text("Window overview", "窗口总览")}
    >
      <div className={styles.grid} data-overview-grid="">
        {cards.flatMap((card, i) => {
          const gap = i === gapAt ? [gapCard] : [];
          const geometry = canvasGeometry(card.root, viewport);
          const hot = hover?.card === card.key;
          const zoomed = zoom?.key === card.key && zoom.phase !== "in";
          // Drawn at the size it shows, everything scaled alike: the zoom
          // then only scales, and corners never jump when it lands.
          const k = zoomed || !cardWidth ? 1 : cardWidth / frame.width;
          const split = leaves(card.root).length > 1, inset = split ? PANE_INSET : 0;
          const body = (
            <>
              <motion.span className={`${styles.zoomer} block`} layout={mode === "browse"}
                transition={zoom?.key === card.key && zoom.phase !== "out" ? ZOOM : INSTANT}
                data-zoomed={zoomed || undefined}
                style={zoomed ? { position: "absolute", zIndex: 3, ...(area.canvasWidth > 0 ? { left: 0, top: 0, width: area.canvasWidth, height: area.canvasHeight } : zoom!.box) } : undefined}
                onLayoutAnimationComplete={() => {
                  if (zoom?.key !== card.key) return;
                  if (zoom.phase === "closing") closeOverview(); else if (zoom.phase === "in") setZoom(null);
                }}>
              <span className={styles.mini} data-overview-mini="" data-split={split || undefined} style={{ aspectRatio: `${aspect}`, borderRadius: split ? 0 : radius * k }}>
                {leaves(card.root).map(p => {
                  const r = geometry.panes.get(p.id)!;
                  const tab: CenterTab | undefined = p.content ? byId.get(p.content) : undefined;
                  const shot = tab ? shots[tab.id] : undefined;
                  return (
                    <span key={p.content ?? p.id} className={styles.pane} data-overview-pane={p.content ?? undefined} data-empty={!tab || undefined} data-shot={shot ? "" : undefined} style={{
                      left: (r.left + inset) * k, top: (r.top + inset) * k,
                      width: (r.width - inset * 2) * k, height: (r.height - inset * 2) * k,
                      borderRadius: split ? PANE_RADIUS * k : 0,
                    }}>
                      {/* Not pictured yet: the tab's icon alone, as Mission Control
                          shows an app icon. The name is the caption under the card. */}
                      {shot ? <img className={styles.shot} src={shot} alt="" draggable={false} /> : tab && !tab.canvasAnchor ? (
                        <span className={styles.paneGlyph}><CompoundMemberIcon tab={tab} animate={false} /></span>
                      ) : null}
                    </span>
                  );
                })}
                {hot && hover?.side ? <span className={styles.zone} style={ZONE[hover.side]} /> : null}
              </span>
              </motion.span>
              {zoomed && <span className="block w-full" style={{ aspectRatio: `${aspect}` }} />}
              <span className={styles.caption}>{card.title}</span>
            </>
          );
          const shared = {
            className: styles.card,
            style: { width: cardWidth || undefined },
            "data-overview-card": card.key,
            "data-overview-target": card.targetId,
            "data-overview-title": card.title,
            "data-overview-first": card.first,
            "data-hot": hot || undefined,
            "data-current": card.key === activeCard || undefined,
            "data-zooming": zoom?.key === card.key || undefined,
            "data-split": leaves(card.root).length > 1 || undefined,
          };
          // One element in both modes: a card turning from a drag target
          // into a pickable one keeps its pictures.
          return [...gap, (
            <motion.button key={card.key} type="button" {...shared} aria-label={card.title}
              tabIndex={mode === "browse" ? undefined : -1}
              // The zooming card never moves itself: a transform on it would
              // make it the zooming picture's containing block, and the
              // picture would grow from the card's corner instead of the
              // window's (picking another window resizes the center area,
              // so every card reflows just as the zoom starts).
              layout={!landing && zoom?.key !== card.key}
              // The current window arrives by zooming when browsing opens;
              // the rest fade in just after it, and a card that turns up
              // later grows in where it lands.
              initial={opening && card.key === activeCard && openedAs.current === "browse" ? false : ENTER}
              animate={{ opacity: 1, scale: 1 }}
              transition={{ ...REFLOW, opacity: { duration: 0.22, delay: opening && openedAs.current === "browse" ? 0.07 : 0 } }}
              onPointerDown={e => pickUp(e, card)}
              onClick={() => { if (mode === "browse") activate(card); }}>{body}</motion.button>
          )];
        }).concat(gapAt === cards.length ? [gapCard] : [])}
      </div>
      <div className={styles.hint} role="status" aria-live="polite" data-idle={!hint || undefined} data-alone={(mode === "drag" && hover?.card === null) || undefined}>{hint || "\u00a0"}</div>
    </div>
  );
}
