"use client";
import { isMacPlatform } from "@/lib/shortcut-keys";
import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, ChevronRight, Equal, FileText, Globe, LayoutGrid, Maximize2, MessageSquare, Minus, SquareDashed, Undo2, X, Save } from "lucide-react";
import { useCenterTabs, type CenterTab } from "@/lib/tabs/center-tabs-store";
import { captureCanvasTransition, prepareCanvasFold } from "@/lib/tabs/canvas/canvas-transition";
import { findCenterTabGroup } from "@/lib/tabs/center-tab-groups";
import { canvasCssGeometry, canvasGeometry, canvasViewport, leaves, presetLayout, insertPane, removePane, equalize, resizeSplit, snapResize, tilePane, mapNode, type CanvasLayout, type CanvasPreset, type CanvasTile, type LayoutNode } from "@/lib/tabs/canvas/canvas-layout";
import { smallCanvasPanes, useCenterBodySize } from "@/lib/tabs/canvas/center-body-size";
import { rememberCanvas, takePreviousCanvas } from "@/lib/tabs/canvas/canvas-history";
import { setCanvasDragging, startPaneDrag } from "@/lib/tabs/canvas/canvas-drag";
import { useTranslation } from "@/lib/i18n";
import { PaneDragLabel } from "../strip/tab-items";
import { labelOf } from "../strip/tab-label";
import { Button } from "@/components/ui/button";
import { GROUP_LABEL, MENU_SEPARATOR, SHORTCUT, itemCls } from "@/components/ui/menu/menu-styles";
import { cn } from "@/lib/utils";
import styles from "./canvas-controls.module.css";

/** Glyph cells as [left, top, width, height] fractions of the canvas. */
type Cells = ReadonlyArray<readonly [number, number, number, number]>;
const HALF_L: Cells = [[0, 0, .5, 1]], HALF_R: Cells = [[.5, 0, .5, 1]];
type MoveItem = { id: CanvasTile | "fill"; en: string; zh: string; keys?: string; cells: Cells };
/** macOS green button ▸ Move & Resize: the four halves; Option swaps in the quarters. */
const MOVE: ReadonlyArray<MoveItem> = [
  { id: "left", en: "Left half", zh: "左半边", keys: "⌃⌥←", cells: HALF_L },
  { id: "right", en: "Right half", zh: "右半边", keys: "⌃⌥→", cells: HALF_R },
  { id: "top", en: "Top half", zh: "上半边", keys: "⌃⌥↑", cells: [[0, 0, 1, .5]] },
  { id: "bottom", en: "Bottom half", zh: "下半边", keys: "⌃⌥↓", cells: [[0, .5, 1, .5]] },
];
const QUARTERS: ReadonlyArray<MoveItem> = [
  { id: "top-left", en: "Top-left quarter", zh: "左上四分之一", cells: [[0, 0, .5, .5]] },
  { id: "top-right", en: "Top-right quarter", zh: "右上四分之一", cells: [[.5, 0, .5, .5]] },
  { id: "bottom-left", en: "Bottom-left quarter", zh: "左下四分之一", cells: [[0, .5, .5, .5]] },
  { id: "bottom-right", en: "Bottom-right quarter", zh: "右下四分之一", cells: [[.5, .5, .5, .5]] },
];
/** Fill & Arrange starts with Fill (zoom), then whole-canvas arrangements. */
const FILL: MoveItem = { id: "fill", en: "Fill", zh: "填满", keys: "⌃⌥F", cells: [[0, 0, 1, 1]] };
/** macOS green button ▸ Fill & Arrange: the whole canvas, this pane in the first slot. */
const ARRANGE: ReadonlyArray<{ id: CanvasPreset; en: string; zh: string; keys?: string; cells: Cells }> = [
  { id: "1x2", en: "Left and right", zh: "左右", keys: "⌃⌥⇧←", cells: [...HALF_L, ...HALF_R] },
  { id: "main+2", en: "Left and stacked right", zh: "左 + 右侧上下", cells: [[0, 0, .58, 1], [.58, 0, .42, .5], [.58, .5, .42, .5]] },
  { id: "2x2", en: "Quarters", zh: "四宫格", cells: [[0, 0, .5, .5], [.5, 0, .5, .5], [0, .5, .5, .5], [.5, .5, .5, .5]] },
];
const grid = (n: number): Cells => Array.from({ length: n * n }, (_, i) => [(i % n) / n, Math.floor(i / n) / n, 1 / n, 1 / n] as const);
/** Rarely used grids live under "More layouts". */
const MORE: ReadonlyArray<{ id: CanvasPreset; en: string; zh: string; cells: Cells }> = [
  { id: "2x1", en: "Top and bottom", zh: "上下", cells: [[0, 0, 1, .5], [0, .5, 1, .5]] },
  { id: "1x1", en: "Single", zh: "单屏", cells: [[0, 0, 1, 1]] },
  { id: "3x3", en: "9", zh: "九宫格", cells: grid(3) },
  { id: "4x4", en: "16", zh: "十六宫格", cells: grid(4) },
];
const ARROW_TILE: Partial<Record<string, "left" | "right" | "top" | "bottom">> = { ArrowLeft: "left", ArrowRight: "right", ArrowUp: "top", ArrowDown: "bottom" };
const SNAP_LABEL = (point: number) => point < .4 ? "1/3" : point < .6 ? "1/2" : "2/3";

function Glyph({ cells }: { cells: Cells }) {
  return <span className={styles.glyph} aria-hidden>
    {cells.map(([l, t, w, h], i) => <i key={i} style={{ left: `${l * 100}%`, top: `${t * 100}%`, width: `${w * 100}%`, height: `${h * 100}%` }} />)}
  </span>;
}

function TabIcon({ tab }: { tab: CenterTab | undefined }) {
  if (tab?.kind === "web") return <Globe aria-hidden />;
  if (tab?.kind === "file") return <FileText aria-hidden />;
  if (tab?.kind === "session") return <MessageSquare aria-hidden />;
  return <SquareDashed aria-hidden />;
}

/** A row of the tiling menu: the app's menu row, lit by keyboard focus as by hover. */
const MENU_ITEM = cn(itemCls(false), "w-full text-left whitespace-nowrap outline-none focus-visible:bg-accent focus-visible:text-text-bright");

/** How long a closed menu takes to fold back into its grip (the CSS --motion-slow tier). */
const FOLD_MS = 240;

/** Panes carry no title bar: content fills the pane, and a "•••" grip at the
 *  top centre moves the pane (drag). Under the pointer its dots become the
 *  macOS window buttons: red closes the tab, yellow folds the pane back into
 *  the tab strip, green opens the tab on its own, and resting on green
 *  unfolds the tiling menu from the grip itself. A lone pane shows no chrome. */
export function CanvasControls({ layout, targetId }: { layout: CanvasLayout; targetId: string }) {
  const { t, text } = useTranslation();
  const tabs = useCenterTabs(s => s.tabs), groups = useCenterTabs(s => s.groups);
  const [saved, setSaved] = useState<Array<{ name: string; layout: CanvasLayout }>>([]);
  const [menu, setMenu] = useState<{ paneId: string; hover: boolean } | null>(null);
  const [quarters, setQuarters] = useState(false);
  const [more, setMore] = useState(false);
  const [hint, setHint] = useState("");
  const [saveName, setSaveName] = useState<string | null>(null);
  const [activeDivider, setActiveDivider] = useState<{ key: string; snap: number | null } | null>(null);
  const hoverTimer = useRef<number | undefined>(undefined);
  /** The pane whose menu is folding back into its grip after it closed. */
  const [folding, setFolding] = useState<string | null>(null);
  const foldTimer = useRef<number | undefined>(undefined);
  /** The current press on a grip, and whether it has moved like a drag. */
  const press = useRef<{ x: number; y: number; moved: boolean } | null>(null);
  useEffect(() => { try { const v = JSON.parse(localStorage.getItem("centerCanvasPresets") || "[]"); if (Array.isArray(v)) setSaved(v); } catch {} }, []);
  useEffect(() => () => { window.clearTimeout(hoverTimer.current); window.clearTimeout(foldTimer.current); }, []);
  /** Close the menu: it stays drawn for a moment to fold away, not vanish. */
  const fold = (paneId: string) => {
    window.clearTimeout(foldTimer.current);
    setFolding(paneId);
    foldTimer.current = window.setTimeout(() => setFolding(null), FOLD_MS);
  };
  useEffect(() => {
    if (!menu) return;
    const dismiss = () => { fold(menu.paneId); setMenu(null); setSaveName(null); setQuarters(false); };
    // Option shows the quarters in place of the halves, as on macOS.
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") dismiss(); else if (e.key === "Alt") setQuarters(e.type === "keydown"); };
    window.addEventListener("pointerdown", dismiss);
    window.addEventListener("keydown", key);
    window.addEventListener("keyup", key);
    window.addEventListener("resize", dismiss);
    return () => { window.removeEventListener("pointerdown", dismiss); window.removeEventListener("keydown", key); window.removeEventListener("keyup", key); window.removeEventListener("resize", dismiss); };
  }, [menu]);
  // Pixel geometry is read when an event needs it; rendering depends only on
  // which panes are too small to show, so a sliding rail does not render
  // these controls on every frame.
  const measure = () => { const size = useCenterBodySize.getState(); return canvasGeometry(layout.root, canvasViewport(size.width, size.height)); };
  const smallPanes = new Set(useCenterBodySize(size => smallCanvasPanes(layout.root, size.width, size.height)).split(" "));
  const all = leaves(layout.root), dividers = useMemo(() => canvasGeometry(layout.root, canvasViewport(0, 0)).dividers, [layout.root]);
  /** A divider's span along its split, in pixels, at this moment. */
  const spanOf = (d: { splitId: string; index: number }) => measure().dividers.find(x => x.splitId === d.splitId && x.index === d.index)?.span ?? 0;
  // Placed as shares of the canvas, like the panes' content (app-shell), so
  // the frames move with the canvas in the same layout pass.
  const cssGeometry = useMemo(() => canvasCssGeometry(layout.root), [layout.root]);
  const group = findCenterTabGroup(groups, targetId);
  const historyKeys = [targetId, ...(group ? [group.id, ...group.memberIds] : [])];
  const update = (canvas: CanvasLayout) => useCenterTabs.getState().setCanvas(targetId, canvas);
  const remember = () => rememberCanvas(historyKeys, layout);
  const focus = (paneId: string) => update({ ...layout, focusedPaneId: paneId });
  const zoom = (paneId: string) => { captureCanvasTransition(); update({ ...layout, focusedPaneId: paneId, zoomedPaneId: layout.zoomedPaneId === paneId ? undefined : paneId }); };
  /** Take a pane off the canvas; its tab folds back up into the strip. */
  const close = (paneId: string) => {
    const content = all.find(p => p.id === paneId)?.content;
    const commit = () => {
      captureCanvasTransition(content ? { [content]: "minimize" } : {});
      remember(); update({ ...layout, zoomedPaneId: undefined, root: removePane(layout.root, paneId) });
    };
    // The fold draws a still of the pane, taken before it leaves the canvas.
    const still = content ? prepareCanvasFold(content) : null;
    if (still) void still.then(commit); else commit();
  };
  const split = (side: "right" | "bottom") => { remember(); const next = insertPane(layout.root, layout.focusedPaneId, side, null); update({ root: next.root, focusedPaneId: next.paneId }); };
  const tile = (paneId: string, to: CanvasTile | "fill") => {
    if (to === "fill") { zoom(paneId); return; }
    const next = tilePane(layout.root, paneId, to);
    if (next.root === layout.root) return;
    captureCanvasTransition(); remember(); update({ root: next.root, focusedPaneId: next.paneId });
  };
  /** Lay the canvas out as a preset with `paneId`'s tab first (second when reversed). */
  const arrange = (preset: CanvasPreset, paneId = layout.focusedPaneId, reversed = false) => {
    const lead = all.find(p => p.id === paneId)?.content ?? null;
    const others = all.flatMap(p => p.content && p.content !== lead ? [p.content] : []);
    const contents = lead ? (reversed ? [others[0], lead, ...others.slice(1)].filter((c): c is string => !!c) : [lead, ...others]) : others;
    const next = presetLayout(preset, contents);
    if (reversed && lead && contents.length === 1) next.root = mapNode(next.root, next.root.id, n => n.kind === "split" ? { ...n, children: [...n.children].reverse() } : n);
    captureCanvasTransition(); remember(); update({ ...next, focusedPaneId: leaves(next.root).find(p => p.content === lead)?.id ?? next.focusedPaneId });
  };
  /** macOS "Return to Previous Size": leave Fill first, then step back through layouts. */
  const restore = () => {
    if (layout.zoomedPaneId) { captureCanvasTransition(); update({ ...layout, zoomedPaneId: undefined }); return true; }
    const previous = takePreviousCanvas(historyKeys);
    if (previous) { captureCanvasTransition(); update(previous); }
    return !!previous;
  };
  const closeMenu = () => { window.clearTimeout(hoverTimer.current); if (menu) fold(menu.paneId); setMenu(null); setSaveName(null); setQuarters(false); };
  const openMenu = (paneId: string, hover = false) => {
    window.clearTimeout(hoverTimer.current);
    window.clearTimeout(foldTimer.current); setFolding(null);
    if (!hover) focus(paneId);
    setSaveName(null); setHint(""); setMore(false); setQuarters(false);
    setMenu({ paneId, hover });
  };
  /** A menu unfolded by resting on green folds away when the pointer leaves the grip. */
  const leaveHoverMenu = () => { window.clearTimeout(hoverTimer.current); if (menu?.hover) hoverTimer.current = window.setTimeout(closeMenu, 240); };
  const pin = () => { window.clearTimeout(hoverTimer.current); if (menu?.hover) setMenu({ ...menu, hover: false }); };
  /** Run a window button only for a real click: not the tail of a drag. */
  const pressed = (run: () => void) => (e: React.MouseEvent) => { const current = press.current; press.current = null; if (e.detail > 0 && current?.moved) return; run(); };
  /** Red: close the tab itself, through the strip's own close (it saves files first). */
  const closeTab = (paneId: string, tabId: string | null) => {
    closeMenu();
    if (!tabId) { close(paneId); return; }
    // The strip's close may ask to save first; the capture plays once the
    // tab is really gone, or lapses.
    captureCanvasTransition({ [tabId]: "close" });
    window.dispatchEvent(new CustomEvent("op-close-center-tabs", { detail: { tabIds: [tabId] } }));
  };
  /** Green: leave the split and open the tab on its own, like the top-edge drop. */
  const openAlone = (paneId: string, tabId: string | null) => {
    closeMenu();
    if (!tabId) { zoom(paneId); return; }
    captureCanvasTransition(); remember();
    const s = useCenterTabs.getState();
    s.ungroupTab(tabId); s.setActive(tabId);
  };
  useEffect(() => {
    const mac = isMacPlatform();
    const key = (e: KeyboardEvent) => {
      if (!(e.metaKey || e.ctrlKey)) return;
      // ⌃⌥ + arrows / F / R: macOS tiling shortcuts (Fn⌃ belongs to the system).
      if (mac && e.ctrlKey && e.altKey && !e.metaKey) {
        const dir = ARROW_TILE[e.key];
        if (dir && e.shiftKey) { e.preventDefault(); arrange(dir === "left" || dir === "right" ? "1x2" : "2x1", layout.focusedPaneId, dir === "right" || dir === "bottom"); return; }
        if (dir) { e.preventDefault(); tile(layout.focusedPaneId, dir); return; }
        if (e.code === "KeyF" && all.length > 1) { e.preventDefault(); zoom(layout.focusedPaneId); return; }
        if (e.code === "KeyR") { e.preventDefault(); restore(); return; }
        return;
      }
      if (e.code === "Backslash") { e.preventDefault(); split(e.shiftKey ? "bottom" : "right"); }
      else if (e.shiftKey && e.key === "Enter") { e.preventDefault(); zoom(layout.focusedPaneId); }
      else if (e.key.toLowerCase() === "w" && all.length > 1) { e.preventDefault(); e.stopImmediatePropagation(); close(layout.focusedPaneId); }
      else if (e.altKey && e.key.startsWith("Arrow")) {
        const geometry = measure(), current = geometry.panes.get(layout.focusedPaneId); if (!current) return;
        const axis = e.key === "ArrowLeft" || e.key === "ArrowRight" ? "x" : "y";
        const sign = e.key === "ArrowLeft" || e.key === "ArrowUp" ? -1 : 1;
        const candidates = all.filter(p => p.id !== layout.focusedPaneId).map(p => {
          const r = geometry.panes.get(p.id)!; const dx = r.left+r.width/2-current.left-current.width/2, dy = r.top+r.height/2-current.top-current.height/2;
          return { id:p.id, along: (axis === "x" ? dx : dy)*sign, score: Math.hypot(dx,dy)+Math.abs(axis === "x" ? dy : dx)*2 };
        }).filter(p => p.along > 1).sort((a,b) => a.score-b.score);
        if (candidates[0]) { e.preventDefault(); focus(candidates[0].id); }
      }
    };
    window.addEventListener("keydown", key, true); return () => window.removeEventListener("keydown", key, true);
  });
  const dockable = tabs.filter(t => !t.canvasAnchor && !findCenterTabGroup(groups, t.id));
  const single = all.length === 1 && !!all[0].content;
  // Same names as the tab strip: built-in pages such as Review carry no title of their own.
  const titleOf = (tab: CenterTab | undefined) => tab ? labelOf(tab, t, text) || text("Untitled","未命名") : text("Empty pane","空格");
  const menuPane = menu ? all.find(p => p.id === menu.paneId) : undefined;
  const menuZoomed = !!menuPane && layout.zoomedPaneId === menuPane.id;
  // The ⌃⌥ tiling keys exist on macOS only (the key handler below), so the
  // hint names them only there.
  const tilingKeys = isMacPlatform();
  const describe = (en: string, zh: string, keys?: string) => ({
    "aria-label": text(en, zh),
    onPointerEnter: () => setHint(keys && tilingKeys ? `${text(en, zh)}  ${keys}` : text(en, zh)),
    onFocus: () => setHint(keys && tilingKeys ? `${text(en, zh)}  ${keys}` : text(en, zh)),
  });
  /** The tiling menu, unfolded from the grip it belongs to (macOS green button). */
  const drawer = (paneId: string, closing: boolean) => <div className={styles.drawerWrap} data-folding={closing || undefined} aria-hidden={closing || undefined}><div role="menu" className={styles.drawer} data-native-view-occluder="true"
    onPointerDown={e => { e.stopPropagation(); pin(); }}>
    {all.length > 1 && <>
      <div className={GROUP_LABEL}>{text("Move and resize", "移动与调整大小")}</div>
      <div className="grid grid-cols-[repeat(4,_1fr)] gap-[2px] p-[0_4px_2px]">
        {(quarters ? QUARTERS : MOVE).map(m => <button key={m.id} type="button" role="menuitem" className={styles.tile} {...describe(m.en, m.zh, m.keys)}
          onClick={() => { tile(paneId, m.id); closeMenu(); }}><Glyph cells={m.cells} /></button>)}
      </div>
    </>}
    <div className={GROUP_LABEL}>{text("Fill and arrange", "填满与排列")}</div>
    <div className="grid grid-cols-[repeat(4,_1fr)] gap-[2px] p-[0_4px_2px]">
      {all.length > 1 && <button type="button" role="menuitemcheckbox" className={styles.tile} aria-checked={menuZoomed}
        {...describe(menuZoomed ? "Exit fill" : FILL.en, menuZoomed ? "退出填满" : FILL.zh, FILL.keys)}
        onClick={() => { tile(paneId, "fill"); closeMenu(); }}><Glyph cells={FILL.cells} /></button>}
      {ARRANGE.map(a => <button key={a.id} type="button" role="menuitem" className={styles.tile} {...describe(a.en, a.zh, a.keys)}
        onClick={() => { arrange(a.id, paneId); closeMenu(); }}><Glyph cells={a.cells} /></button>)}
    </div>
    <div className={styles.hint} aria-live="polite">{hint || (all.length > 1 ? text(`Hold ${tilingKeys ? "Option" : "Alt"} for quarters`, `按住 ${tilingKeys ? "Option" : "Alt"} 显示四分之一`) : "")}</div>
    <div className={cn(MENU_SEPARATOR, "-mx-[5px]")} />
    <button type="button" role="menuitem" className={MENU_ITEM} onClick={() => { if (!restore()) setHint(text("No earlier layout", "没有更早的布局")); else closeMenu(); }}><Undo2 aria-hidden />{text("Return to previous layout", "还原上次布局")}{tilingKeys ? <span className={cn(SHORTCUT, "ms-auto ps-3")}>⌃⌥R</span> : null}</button>
    <button type="button" role="menuitem" className={MENU_ITEM} onClick={() => { captureCanvasTransition(); remember(); update({ ...layout, root: equalize(layout.root) }); closeMenu(); }}><Equal aria-hidden />{text("Equalize sizes", "均分大小")}</button>
    <button type="button" role="menuitem" className={MENU_ITEM} aria-expanded={more} onClick={() => setMore(!more)}><LayoutGrid aria-hidden />{text("More layouts", "更多布局")}<span className={cn(SHORTCUT, "ms-auto inline-flex items-center ps-3 [&>svg]:size-[14px]")}>{more ? <ChevronDown aria-hidden /> : <ChevronRight aria-hidden />}</span></button>
    {more && <div className="p-[2px_0_2px_8px]">
      <div className="grid grid-cols-[repeat(3,_1fr)] gap-[2px] p-[2px_2px_4px]">
        {MORE.map(p => <button key={p.id} type="button" role="menuitem" className={styles.preset} onClick={() => { arrange(p.id, paneId); closeMenu(); }}>
          <Glyph cells={p.cells} />{text(p.en, p.zh)}
        </button>)}
      </div>
      {saved.length > 0 && <>
        <div className={GROUP_LABEL}>{text("Saved layouts", "已保存布局")}</div>
        {saved.map((s, i) => <button key={i} type="button" role="menuitem" className={MENU_ITEM} onClick={() => { captureCanvasTransition(); remember(); update(s.layout); closeMenu(); }}><LayoutGrid aria-hidden />{s.name}</button>)}
      </>}
      {saveName === null
        ? <button type="button" role="menuitem" className={MENU_ITEM} onClick={() => setSaveName("")}><Save aria-hidden />{text("Save layout…", "保存布局…")}</button>
        : <form className="flex gap-[4px] p-[4px]" onSubmit={e => { e.preventDefault(); if (!saveName.trim()) return; const next=[...saved.filter(s => s.name !== saveName.trim()), { name:saveName.trim(),layout }]; setSaved(next); localStorage.setItem("centerCanvasPresets",JSON.stringify(next)); closeMenu(); }}>
          <input autoFocus className={styles.saveInput} value={saveName} placeholder={text("Layout name", "布局名称")} aria-label={text("Layout name", "布局名称")} onChange={e => setSaveName(e.target.value)} />
          <Button size="xs">{text("Save", "保存")}</Button>
        </form>}
    </div>}
  </div></div>;
  return <>
    {all.map(p => {
      const visible = !layout.zoomedPaneId || layout.zoomedPaneId === p.id;
      const zoomed = layout.zoomedPaneId === p.id;
      const css = zoomed ? cssGeometry.viewport : cssGeometry.panes.get(p.id)!;
      const tab=tabs.find(t => t.id === p.content), tiny=smallPanes.has(zoomed ? "viewport" : p.id);
      const title = titleOf(tab);
      return <div key={p.id} className={styles.pane} data-canvas-pane={p.id} data-canvas-target={targetId} data-canvas-content={p.content ?? undefined} data-single={single || undefined} data-focused={(!single && layout.focusedPaneId === p.id) || undefined} data-menu-open={menu?.paneId === p.id || folding === p.id || undefined} style={{ ...css, display: visible ? undefined : "none" }}>
        {!p.content
          ? <div className={styles.empty}>
              <div className={styles.emptyCard}>
                <div className={styles.emptyHint}>{dockable.length ? text("Choose a tab, or drag one here", "选一个 tab，或直接拖进来") : text("Drag a tab here", "把 tab 拖到这里")}</div>
                {dockable.map(d => <button key={d.id} type="button" className={styles.emptyItem} onClick={() => useCenterTabs.getState().dockCanvas(d.id,targetId,p.id,"center")}><TabIcon tab={d} /><span>{titleOf(d)}</span></button>)}
              </div>
            </div>
          : tiny ? <button type="button" className={styles.tiny} aria-label={text(`Zoom ${title}`, `放大 ${title}`)} onClick={() => zoom(p.id)}><span><TabIcon tab={tab} /><Maximize2 aria-hidden /></span></button> : null}
        {!single && <div className={styles.handle} data-open={menu?.paneId === p.id || undefined} data-folding={(folding === p.id && menu?.paneId !== p.id) || undefined}
          onPointerEnter={() => { if (menu?.hover && menu.paneId === p.id) window.clearTimeout(hoverTimer.current); }}
          onPointerLeave={() => { if (menu?.hover && menu.paneId === p.id) leaveHoverMenu(); else if (!menu) window.clearTimeout(hoverTimer.current); }}>
          <span className={styles.sheet} aria-hidden="true" />
          <div role="group" className={styles.grip} aria-label={text(`Pane: ${title}`, `格子：${title}`)}
            onPointerDown={e => {
              if (e.button !== 0) return; e.stopPropagation(); if (menu?.hover) closeMenu();
              // A press that moved, even if the drag was then cancelled and the
              // pointer came back, must not click the window button under it.
              const current = { x: e.clientX, y: e.clientY, moved: false };
              press.current = current;
              const track = (ev: PointerEvent) => { if (Math.hypot(ev.clientX - current.x, ev.clientY - current.y) > 6) current.moved = true; };
              window.addEventListener("pointermove", track, true);
              window.addEventListener("pointerup", () => window.removeEventListener("pointermove", track, true), { capture: true, once: true });
              window.addEventListener("pointercancel", () => window.removeEventListener("pointermove", track, true), { capture: true, once: true });
              if (p.content) startPaneDrag(e, p.content, e.currentTarget.querySelector<HTMLElement>("[data-pane-drag-label]"));
            }}
            onContextMenu={e => { e.preventDefault(); e.stopPropagation(); openMenu(p.id); }}>
            {/* At rest the three window buttons are the grip's grey dots; under the
                pointer they grow into the macOS buttons. A press that turns into a
                drag never clicks them. */}
            <span className={styles.lights}>
              <button type="button" className={styles.light} data-pane-light="close" aria-label={p.content ? text(`Close ${title}`, `关闭 ${title}`) : text("Close pane", "关闭格子")}
                onClick={pressed(() => closeTab(p.id, p.content))}><X aria-hidden /></button>
              <button type="button" className={styles.light} data-pane-light="minimize" aria-label={text(`Move ${title} to the tab strip`, `把 ${title} 收回标签栏`)}
                onClick={pressed(() => { closeMenu(); close(p.id); })}><Minus aria-hidden /></button>
              <button type="button" className={styles.light} data-pane-light="zoom" aria-haspopup="menu" aria-expanded={menu?.paneId === p.id}
                aria-label={text(`Open ${title} on its own`, `单独打开 ${title}`)}
                onPointerEnter={e => { if (e.buttons || (menu && !menu.hover)) return; window.clearTimeout(hoverTimer.current); hoverTimer.current = window.setTimeout(() => openMenu(p.id, true), 250); }}
                onPointerLeave={() => { if (!menu) window.clearTimeout(hoverTimer.current); }}
                onKeyDown={e => { if (e.key === "ArrowDown") { e.preventDefault(); openMenu(p.id); } }}
                onClick={pressed(() => openAlone(p.id, p.content))}><Maximize2 aria-hidden /></button>
            </span>
            {tab && <PaneDragLabel tab={tab} />}
          </div>
          {(menu?.paneId === p.id || folding === p.id) && drawer(p.id, menu?.paneId !== p.id)}
        </div>}
      </div>;
    })}
    {!layout.zoomedPaneId && dividers.map((d, dividerIndex) => { const key = `${d.splitId}:${d.index}`; const live = activeDivider?.key === key ? activeDivider : null; return <div key={key} className={styles.divider} data-active={!!live || undefined} role="separator" aria-orientation={d.dir === "row" ? "vertical" : "horizontal"} aria-valuenow={Math.round(d.sizes[d.index]*100)} tabIndex={0}
      style={cssGeometry.dividers[dividerIndex]}
      onDoubleClick={() => { captureCanvasTransition(); remember(); update({ ...layout,root:mapNode(layout.root,d.splitId,n => n.kind === "split" ? { ...n,sizes:n.sizes.map((s,i) => i===d.index || i===d.index+1 ? (n.sizes[d.index]+n.sizes[d.index+1])/2 : s) } : n) }); }}
      onKeyDown={e => { if (!["ArrowLeft","ArrowRight","ArrowUp","ArrowDown"].includes(e.key)) return; e.preventDefault(); update({ ...layout,root:resizeSplit(layout.root,d.splitId,d.index,e.key==="ArrowLeft"||e.key==="ArrowUp"?-.02:.02,spanOf(d)) }); }}
      onPointerDown={e => {
        if(e.button!==0) return; e.preventDefault(); const start=d.dir==="row"?e.clientX:e.clientY, span=spanOf(d);
        // Sizes paint once per frame through the unpersisted preview; the
        // release commits them once. Near a third, a half or two thirds the
        // divider sticks, like a macOS tile edge.
        let moved = false, frame = 0, latest: LayoutNode | null = null, snap: number | null = null, done = false;
        setActiveDivider({ key, snap: null });
        const move=(event:PointerEvent) => {
          if (event.buttons === 0) { end(); return; }
          if (!moved) { moved = true; remember(); setCanvasDragging(true, { cancel: end, release: end }); }
          const snapped = snapResize(d.sizes, d.index, ((d.dir==="row"?event.clientX:event.clientY)-start)/Math.max(1,span), span);
          latest = resizeSplit(layout.root,d.splitId,d.index,snapped.delta,span);
          if (snapped.snap !== snap) { snap = snapped.snap; setActiveDivider({ key, snap }); }
          if (!frame) frame = requestAnimationFrame(() => { frame = 0; if (latest && !done) useCenterTabs.getState().previewCanvas(targetId, latest); });
        };
        const end=() => {
          if (done) return; done = true;
          window.removeEventListener("pointermove",move); window.removeEventListener("pointerup",end); window.removeEventListener("pointercancel",end); window.removeEventListener("blur",end);
          cancelAnimationFrame(frame);
          if (moved) setCanvasDragging(false);
          setActiveDivider(null);
          if (latest) update({ ...layout, root: latest });
        };
        window.addEventListener("pointermove",move); window.addEventListener("pointerup",end); window.addEventListener("pointercancel",end); window.addEventListener("blur",end);
      }}>{live?.snap != null && <span className={styles.snap}>{SNAP_LABEL(live.snap)}</span>}</div>; })}
  </>;
}
