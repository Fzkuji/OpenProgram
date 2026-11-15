"use client";

/**
 * Launchpad — the new-tab launcher, laid out like the classic macOS
 * Launchpad: a 7 × 5 grid of large icons on pages, a search field that
 * takes typing, folders made by dropping one icon on another, and an edit
 * mode where icons wobble and removable ones show ×.
 *
 * Pointer input comes from dnd-kit; where things land is computed here
 * from the grid geometry, and framer-motion animates icons into their new
 * places. Gestures trigger fixed animations (a page slides a whole page),
 * nothing follows the finger.
 */

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type CSSProperties, type PointerEvent as ReactPointerEvent } from "react";
import { DndContext, DragOverlay, PointerSensor, useDraggable, useSensor, useSensors, type DragMoveEvent, type DragStartEvent } from "@dnd-kit/core";
import { AnimatePresence, animate, motion, useMotionValue, useReducedMotion } from "framer-motion";
import { Search, X } from "lucide-react";
import { WheelGestures } from "wheel-gestures";
import { Button } from "@/components/ui/button";

import { useTranslation } from "@/lib/i18n";
import {
  COLUMNS, ROWS, findFolderOf, mergeInto, moveEntry, moveWithinFolder, reconcile, renameFolder,
  type Entry, type FolderEntry, type LaunchpadLayout,
} from "@/lib/tabs/launchpad/launchpad-layout";
import { setLaunchpadLayout, useLaunchpadLayout } from "@/lib/tabs/launchpad/launchpad-store";
import { LaunchpadIcon, type IconArt, type Tone } from "./launchpad-icons";
import styles from "./launchpad.module.css";

export interface LaunchItem {
  id: string;
  title: string;
  art: IconArt;
  /** The tile colour of the generic drawing; drawn icons carry their own. */
  tone?: Tone;
  open: () => void;
  /** Present when the item can be taken off the Launchpad in edit mode. */
  remove?: () => void;
}

type Intent =
  | { type: "insert"; page: number; index: number }
  | { type: "merge"; target: string }
  | { type: "folder"; index: number };

const FOLDER_COLUMNS = 5;
const PLACEHOLDER = "\u0000placeholder";
const REORDER_DWELL = 120;
const MERGE_DWELL = 320;
const EDGE_DWELL = 650;
const LONG_PRESS = 600;
/** easeOutQuint: the end-of-strip nudge starts quickly and settles slowly. */
const PAGE_EASE = [0.22, 1, 0.36, 1] as const;
/** A page settles into place the way a scroll view's paging does. */
const SETTLE = { type: "spring", stiffness: 320, damping: 38, mass: 0.9 } as const;
/** Past the first or last page the strip only gives a little, as Launchpad's
 *  does: a pull of any length moves it at most END_GIVE px. */
const END_GIVE = 44;
/** Opening an item: the Launchpad fades and the icon grows for this long, then the item opens. */
const LAUNCH_MS = 240;
const rubberBand = (pull: number) => Math.sign(pull) * (Math.abs(pull) * END_GIVE) / (Math.abs(pull) + END_GIVE);

function useBoxSize<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  useLayoutEffect(() => {
    const node = ref.current;
    if (!node) return;
    const observer = new ResizeObserver(([entry]) => setSize({ width: entry.contentRect.width, height: entry.contentRect.height }));
    observer.observe(node);
    return () => observer.disconnect();
  }, []);
  return [ref, size] as const;
}

function Tile({ item, size }: { item: LaunchItem; size: number }) {
  return (
    <span className={styles.tile} style={{ width: size, height: size, borderRadius: size * 0.3 }}>
      <LaunchpadIcon art={item.art} tone={item.tone} size={size} />
    </span>
  );
}

function FolderTile({ folder, items, size, open }: { folder: FolderEntry; items: Map<string, LaunchItem>; size: number; open?: boolean }) {
  const mini = size * 0.24;
  return (
    <span className={styles.folderTile} data-open={open || undefined} style={{ width: size, height: size, borderRadius: size * 0.225, gridTemplateColumns: `repeat(3, ${mini}px)`, gridAutoRows: mini, gap: size * 0.045 }}>
      {folder.items.slice(0, 9).map(id => {
        const item = items.get(id);
        if (!item) return null;
        return <span key={id} className={`${styles.mini} grid place-items-center`}><LaunchpadIcon art={item.art} tone={item.tone} size={mini} /></span>;
      })}
    </span>
  );
}

interface CellProps {
  entry: Entry;
  items: Map<string, LaunchItem>;
  icon: number;
  editing: boolean;
  selected: boolean;
  mergeTarget: boolean;
  launching: boolean;
  wobble: number;
  canDrag: boolean;
  label: string;
  onActivate: () => void;
  onLongPress: () => void;
  removeLabel: (title: string) => string;
}

function Cell({ entry, items, icon, editing, selected, mergeTarget, launching, wobble, canDrag, label, onActivate, onLongPress, removeLabel }: CellProps) {
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({ id: entry.id, disabled: !canDrag });
  const press = useRef<{ timer: number; fired: boolean } | null>(null);
  const item = entry.kind === "item" ? items.get(entry.id) : undefined;
  const clear = () => { if (press.current) window.clearTimeout(press.current.timer); };
  const onPointerDown = (event: ReactPointerEvent<HTMLButtonElement>) => {
    listeners?.onPointerDown?.(event);
    clear();
    const state = { timer: 0, fired: false };
    state.timer = window.setTimeout(() => { state.fired = true; onLongPress(); }, LONG_PRESS);
    press.current = state;
  };
  if (isDragging) clear();
  return (
    <div className={`${styles.cell} relative w-full h-full flex items-center justify-center`} data-wobble={editing ? wobble % 2 : undefined} style={{ animationDelay: `${-(wobble % 5) * 0.07}s` }}>
      <button
        ref={setNodeRef}
        type="button"
        className={styles.cellButton}
        data-selected={selected || undefined}
        data-merge={mergeTarget || undefined}
        data-launching={launching || undefined}
        data-dragging={isDragging || undefined}
        {...attributes}
        aria-roledescription={undefined}
        aria-label={label}
        onPointerDown={onPointerDown}
        onPointerUp={clear}
        onPointerLeave={clear}
        onClick={() => {
          if (press.current?.fired) { press.current = null; return; }
          onActivate();
        }}
      >
        {entry.kind === "folder" ? <FolderTile folder={entry} items={items} size={icon} open={mergeTarget} /> : item && <Tile item={item} size={icon} />}
        <span className={styles.label} style={{ maxWidth: icon * 1.5 }}>{label}</span>
      </button>
      {editing && item?.remove && (
        <button type="button" className={styles.remove} aria-label={removeLabel(item.title)} onClick={item.remove} style={{ left: `calc(50% - ${icon / 2 + 7}px)`, top: `calc(50% - ${(icon + 24) / 2 + 7}px)` }}>
          <X size={12} strokeWidth={3} />
        </button>
      )}
    </div>
  );
}

export function Launchpad({ items: itemList }: { items: LaunchItem[] }) {
  const { text } = useTranslation();
  const reduceMotion = useReducedMotion();
  const stored = useLaunchpadLayout();
  const items = useMemo(() => new Map(itemList.map(item => [item.id, item])), [itemList]);
  const ids = useMemo(() => itemList.map(item => item.id), [itemList]);
  const layout = useMemo(() => reconcile(stored, ids), [stored, ids]);

  const [rootRef, box] = useBoxSize<HTMLDivElement>();
  const [page, setPage] = useState(0);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<number | null>(null);
  const [editing, setEditing] = useState(false);
  const [openFolderId, setOpenFolderId] = useState<string | null>(null);
  const [launching, setLaunching] = useState<string | null>(null);
  const [dragId, setDragId] = useState<string | null>(null);
  const [dragFromFolder, setDragFromFolder] = useState<string | null>(null);
  const [intent, setIntent] = useState<Intent | null>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const folderPanelRef = useRef<HTMLDivElement>(null);
  const pending = useRef<{ key: string; timer: number } | null>(null);
  const edge = useRef<{ side: number; timer: number } | null>(null);
  const stripX = useMotionValue(0);
  const stripWidth = useRef(0);
  const pointer = useRef({ x: 0, y: 0 });

  const pageCount = layout.pages.length;
  // While dragging, one empty page past the last lets an icon start a new page.
  const shownPages = dragId ? [...layout.pages, []] : layout.pages;
  const current = Math.min(page, shownPages.length - 1);
  useEffect(() => { if (!dragId && page > pageCount - 1) setPage(pageCount - 1); }, [dragId, page, pageCount]);

  // Geometry: the grid keeps 7 × 5 and scales its icons to the tab.
  const top = 92, bottom = 64;
  const side = Math.max(24, box.width * 0.06);
  const cellW = Math.min(Math.max(box.width - side * 2, 0) / COLUMNS, 168);
  const cellH = Math.min(Math.max(box.height - top - bottom, 0) / ROWS, 150);
  const icon = Math.round(Math.max(36, Math.min(cellW * 0.6, cellH * 0.6, 96)));
  const gridLeft = (box.width - cellW * COLUMNS) / 2;
  const gridTop = top + Math.max(0, (box.height - top - bottom - cellH * ROWS) / 2);

  const save = useCallback((next: LaunchpadLayout) => setLaunchpadLayout(next), []);
  const openFolder = openFolderId ? layout.pages.flat().find((entry): entry is FolderEntry => entry.kind === "folder" && entry.id === openFolderId) ?? null : null;
  useEffect(() => { if (openFolderId && !openFolder && !dragId) setOpenFolderId(null); }, [openFolderId, openFolder, dragId]);

  const labelOf = (entry: Entry) => entry.kind === "folder" ? entry.name || text("Folder", "文件夹") : items.get(entry.id)?.title ?? "";
  // Opening an item: the rest of the page fades where it is while the chosen
  // icon grows about its own centre, and the item opens as it goes.
  // Should this tab still be here afterwards (the item opened a dialog or
  // failed), the page comes back.
  const launch = useCallback((item: LaunchItem) => {
    if (reduceMotion || launching) { if (!launching) item.open(); return; }
    setLaunching(item.id);
    window.setTimeout(() => {
      item.open();
      window.setTimeout(() => setLaunching(null), 400);
    }, LAUNCH_MS);
  }, [reduceMotion, launching]);
  const activate = (entry: Entry) => {
    if (entry.kind === "folder") { setOpenFolderId(entry.id); return; }
    const item = items.get(entry.id);
    if (!editing && item) launch(item);
  };

  const results = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return null;
    return layout.pages.flat().flatMap(entry => entry.kind === "folder" ? entry.items : [entry.id])
      .map(id => items.get(id)).filter((item): item is LaunchItem => !!item && item.title.toLowerCase().includes(needle));
  }, [query, layout, items]);
  useEffect(() => setSelected(results?.length ? 0 : null), [results]);

  const goPage = useCallback((next: number) => {
    setPage(Math.max(0, Math.min(next, (dragId ? pageCount : pageCount - 1))));
    setSelected(null);
  }, [dragId, pageCount]);

  // The strip settles on the current page from wherever it is: a full page
  // away after a key, dot or wheel notch, part-way after a trackpad swipe.
  useEffect(() => {
    const resized = stripWidth.current !== box.width;
    stripWidth.current = box.width;
    const target = -current * box.width;
    if (resized || reduceMotion) { stripX.stop(); stripX.set(target); return; }
    const controls = animate(stripX, target, SETTLE);
    return () => controls.stop();
  }, [current, box.width, reduceMotion, stripX]);
  // Keys and wheel notches turn whole pages; past the first or last page the
  // strip gives a short rubber-band nudge instead, as Launchpad does.
  const turn = (direction: number) => {
    const next = current + direction;
    if (next >= 0 && next <= pageCount - 1) { goPage(next); return; }
    if (reduceMotion) return;
    const base = -current * box.width;
    void animate(stripX, [stripX.get(), base - direction * END_GIVE * 0.8, base], { duration: 0.46, times: [0, 0.32, 1], ease: ["easeOut", PAGE_EASE] });
  };

  // ---- drag -------------------------------------------------------------
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 6 } }));
  const clearTimers = () => {
    if (pending.current) window.clearTimeout(pending.current.timer);
    if (edge.current) window.clearTimeout(edge.current.timer);
    pending.current = null; edge.current = null;
  };
  const propose = (next: Intent | null, dwell: number) => {
    const key = JSON.stringify(next);
    if (pending.current?.key === key) return;
    if (pending.current) window.clearTimeout(pending.current.timer);
    if (JSON.stringify(intent) === key) { pending.current = null; return; }
    pending.current = { key, timer: window.setTimeout(() => { pending.current = null; setIntent(next); }, dwell) };
  };

  const onDragStart = (event: DragStartEvent) => {
    const id = String(event.active.id);
    const start = event.activatorEvent as PointerEvent;
    pointer.current = { x: start.clientX, y: start.clientY };
    setDragId(id);
    const folder = findFolderOf(layout, id);
    setDragFromFolder(folder?.id ?? null);
    setEditing(true);
    // The icon's own slot stays open until the pointer settles somewhere else.
    const page = layout.pages.findIndex(entries => entries.some(entry => entry.id === id));
    if (folder) setIntent({ type: "folder", index: folder.items.indexOf(id) });
    else setIntent(page >= 0 ? { type: "insert", page, index: layout.pages[page].findIndex(entry => entry.id === id) } : null);
  };

  const onDragMove = (event: DragMoveEvent) => {
    const start = event.activatorEvent as PointerEvent;
    const x = start.clientX + event.delta.x, y = start.clientY + event.delta.y;
    pointer.current = { x, y };
    const root = rootRef.current?.getBoundingClientRect();
    if (!root || !dragId) return;
    const panel = folderPanelRef.current?.getBoundingClientRect();
    if (openFolder && panel) {
      if (x >= panel.left && x <= panel.right && y >= panel.top && y <= panel.bottom) {
        const grid = folderPanelRef.current!.querySelector("[data-folder-grid]")!.getBoundingClientRect();
        const col = Math.max(0, Math.min(FOLDER_COLUMNS - 1, Math.floor((x - grid.left) / cellW)));
        const row = Math.max(0, Math.floor((y - grid.top) / cellH));
        propose({ type: "folder", index: Math.min(row * FOLDER_COLUMNS + col, openFolder.items.length - 1) }, REORDER_DWELL);
      } else {
        // Leaving the panel takes the icon out of the folder, as Launchpad does.
        setOpenFolderId(null);
        setIntent(null);
      }
      return;
    }
    const lx = x - root.left, ly = y - root.top;
    const towards = lx < 40 ? -1 : lx > root.width - 40 ? 1 : 0;
    if (towards !== (edge.current?.side ?? 0)) {
      if (edge.current) window.clearTimeout(edge.current.timer);
      edge.current = null;
      if (towards) {
        const flip = () => {
          setPage(value => Math.max(0, Math.min(value + towards, pageCount)));
          setIntent(null);
          edge.current = { side: towards, timer: window.setTimeout(flip, EDGE_DWELL * 1.4) };
        };
        edge.current = { side: towards, timer: window.setTimeout(flip, EDGE_DWELL) };
      }
    }
    const col = Math.floor((lx - gridLeft) / cellW), row = Math.floor((ly - gridTop) / cellH);
    if (col < 0 || col >= COLUMNS || row < 0 || row >= ROWS) return;
    const shown = previewPage(current);
    const index = row * COLUMNS + col;
    const under = shown[index];
    const base = shown.filter(entry => entry.id !== PLACEHOLDER);
    if (under && under.id !== PLACEHOLDER) {
      const cx = gridLeft + (col + 0.5) * cellW, cy = gridTop + row * cellH + icon / 2 + (cellH - icon - 22) / 2;
      const draggedIsFolder = layout.pages.flat().some(entry => entry.id === dragId && entry.kind === "folder");
      if (!draggedIsFolder && Math.hypot(lx - cx, ly - cy) < icon * 0.34) {
        propose({ type: "merge", target: under.id }, MERGE_DWELL);
        return;
      }
    }
    propose({ type: "insert", page: current, index: Math.min(index, base.length) }, REORDER_DWELL);
  };

  const endDrag = (commit: boolean) => {
    clearTimers();
    const id = dragId;
    if (commit && id && intent) {
      if (intent.type === "insert") save(moveEntry(layout, id, intent.page, intent.index));
      else if (intent.type === "merge") save(mergeInto(layout, id, intent.target, `folder:${crypto.randomUUID()}`));
      else if (intent.type === "folder" && dragFromFolder) save(moveWithinFolder(layout, dragFromFolder, id, intent.index));
    }
    if (commit && intent?.type === "insert") setPage(intent.page);
    setDragId(null);
    setDragFromFolder(null);
    setIntent(null);
  };

  /** A page as it shows during a drag: the dragged entry out, a gap where it would land. */
  function previewPage(index: number): Entry[] {
    const entries = (shownPages[index] ?? []).filter(entry => entry.id !== dragId);
    if (intent?.type === "insert" && intent.page === index) {
      entries.splice(Math.min(intent.index, entries.length), 0, { kind: "item", id: PLACEHOLDER });
    }
    return entries;
  }
  function previewFolder(folder: FolderEntry): string[] {
    const list = folder.items.filter(id => id !== dragId);
    if (dragId && intent?.type === "folder") list.splice(Math.min(intent.index, list.length), 0, PLACEHOLDER);
    return list;
  }

  // ---- keys and wheel ---------------------------------------------------
  const visible: Entry[] = results ? results.map(item => ({ kind: "item", id: item.id })) : layout.pages[current] ?? [];
  const escape = useCallback(() => {
    if (openFolderId) setOpenFolderId(null);
    else if (editing) setEditing(false);
    else if (query) setQuery("");
    else return false;
    return true;
  }, [openFolderId, editing, query]);
  // Escape still backs out when focus fell to the body, e.g. after a drag
  // or after the × that had focus took its icon away.
  useEffect(() => {
    if (!openFolderId && !editing && !query) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || document.activeElement !== document.body) return;
      if (rootRef.current?.offsetParent && escape()) event.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [openFolderId, editing, query, escape, rootRef]);
  const onKeyDown = (event: React.KeyboardEvent) => {
    const typing = event.target instanceof HTMLInputElement;
    if (event.key === "Escape") {
      if (escape()) event.preventDefault();
      return;
    }
    if (openFolderId && typing) return;
    if ((event.metaKey || event.ctrlKey) && (event.key === "ArrowLeft" || event.key === "ArrowRight")) {
      turn(event.key === "ArrowRight" ? 1 : -1);
      event.preventDefault();
      return;
    }
    const step = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -COLUMNS, ArrowDown: COLUMNS }[event.key];
    if (step && !openFolderId && visible.length) {
      event.preventDefault();
      const next = selected === null ? 0 : selected + step;
      if (!results && next >= visible.length && step === 1 && current < pageCount - 1) { setPage(current + 1); setSelected(0); return; }
      if (!results && next < 0 && step === -1 && current > 0) { setPage(current - 1); setSelected(layout.pages[current - 1].length - 1); return; }
      setSelected(Math.max(0, Math.min(next, visible.length - 1)));
      return;
    }
    if (event.key === "Enter" && selected !== null && visible[selected] && !openFolderId) {
      event.preventDefault();
      const entry = visible[selected];
      if (entry.kind === "folder") setOpenFolderId(entry.id); else { const item = items.get(entry.id); if (item) launch(item); }
      return;
    }
    if (!typing && !openFolderId && event.key.length === 1 && !event.metaKey && !event.ctrlKey && !event.altKey) {
      searchRef.current?.focus();
    }
  };
  // Trackpad and wheel paging, as a paging NSScrollView does it: while the
  // fingers are on the trackpad the strip follows them, at most a page and
  // with a rubber band past the ends; when they lift it settles on the page
  // they moved towards, if they moved far enough, else back. The momentum
  // after the lift never turns another page. A mouse wheel notch, one big
  // step, turns a page outright. Wheel events carry no phases, so
  // wheel-gestures (the library Embla pages with) rebuilds the began /
  // momentum / ended phases from them.
  const live = useRef({ current, pageCount, width: box.width, blocked: false, goPage, turn, reduceMotion });
  live.current = { current, pageCount, width: box.width, blocked: !!results || !!openFolderId, goPage, turn, reduceMotion };
  useEffect(() => {
    const node = rootRef.current;
    if (!node) return;
    const gestures = WheelGestures({ reverseSign: false });
    let phase: "start" | "follow" | "done" = "done";
    let anchor = 0;
    const off = gestures.on("wheel", state => {
      const { current, pageCount, width, blocked, goPage, turn, reduceMotion } = live.current;
      if (state.isStart) { phase = blocked ? "done" : "start"; anchor = -current * width; }
      if (phase === "done") return;
      const [x, y] = state.axisMovement;
      const moved = Math.abs(x) > Math.abs(y) ? x : y;
      if (state.isMomentum || state.isEnding) {
        if (phase === "follow") {
          // A page turns on distance, or on a flick: a short fast swipe whose
          // momentum (axisVelocity, px per ms) is still brisk when the fingers lift.
          const direction = Math.sign(moved), next = current + direction;
          const [vx, vy] = state.axisVelocity;
          const flick = Math.abs(moved) >= 32 && Math.abs(Math.abs(x) > Math.abs(y) ? vx : vy) >= 0.6;
          if ((Math.abs(moved) >= Math.max(48, width * 0.15) || flick) && next >= 0 && next <= pageCount - 1) goPage(next);
          else void animate(stripX, -current * width, reduceMotion ? { duration: 0 } : SETTLE);
        }
        phase = "done";
        return;
      }
      if (phase === "start") {
        const [dx, dy] = state.axisDelta;
        if (Math.max(Math.abs(dx), Math.abs(dy)) >= 40) { phase = "done"; turn(Math.sign(moved)); return; }
        phase = "follow";
        // A swipe that starts while the strip still settles carries on from there.
        stripX.stop();
        anchor = stripX.get();
      }
      const past = (current === 0 && moved < 0) || (current >= pageCount - 1 && moved > 0);
      const offset = past ? rubberBand(moved) : Math.max(-width, Math.min(width, moved));
      stripX.set(anchor - offset);
    });
    gestures.observe(node);
    return () => { off(); gestures.disconnect(); };
  }, [rootRef, stripX]);
  useEffect(() => {
    if (document.activeElement === document.body) rootRef.current?.focus({ preventScroll: true });
  }, [rootRef]);

  // ---- render -----------------------------------------------------------
  const shift = reduceMotion ? { duration: 0 } : { type: "spring" as const, stiffness: 520, damping: 40 };
  const gridStyle: CSSProperties = { gridTemplateColumns: `repeat(${COLUMNS}, ${cellW}px)`, gridAutoRows: cellH, left: gridLeft, top: gridTop };
  const removeLabel = (title: string) => text(`Remove ${title}`, `移除 ${title}`);
  const renderEntry = (entry: Entry, index: number, selectedIndex: number | null) => entry.id === PLACEHOLDER
    ? <motion.div key={PLACEHOLDER} layout transition={shift} className={`${styles.cell} relative w-full h-full flex items-center justify-center`} />
    : (
      <motion.div key={entry.id} layout={!!dragId} transition={shift}>
        <Cell
          entry={entry} items={items} icon={icon} editing={editing} wobble={index} canDrag={!results}
          selected={selectedIndex === index}
          mergeTarget={intent?.type === "merge" && intent.target === entry.id}
          launching={launching === entry.id}
          label={labelOf(entry)}
          onActivate={() => activate(entry)}
          onLongPress={() => setEditing(true)}
          removeLabel={removeLabel}
        />
      </motion.div>
    );
  const dragged = dragId ? (layout.pages.flat().find(entry => entry.id === dragId) ?? { kind: "item" as const, id: dragId }) : null;

  return (
    <div
      ref={rootRef}
      className={styles.root}
      tabIndex={-1}
      onKeyDown={onKeyDown}
      onClick={event => { if (event.target === event.currentTarget) setEditing(false); }}
      data-launching={launching ? "" : undefined}
      data-testid="launchpad"
    >
      <div className={styles.backdrop} aria-hidden="true" />
      <div className={styles.searchRow} data-dim={!!openFolder || undefined}>
        <label className={styles.search} data-active={!!query || undefined}>
          <Search size={14} aria-hidden="true" />
          <input
            ref={searchRef}
            value={query}
            placeholder={text("Search", "搜索")}
            aria-label={text("Search", "搜索")}
            onChange={event => { setQuery(event.target.value); setOpenFolderId(null); }}
          />
        </label>
      </div>
      {editing && <Button type="button" variant="elevated" className={styles.done} onClick={() => setEditing(false)}>{text("Done", "完成")}</Button>}

      <DndContext sensors={sensors} onDragStart={onDragStart} onDragMove={onDragMove} onDragEnd={() => endDrag(true)} onDragCancel={() => endDrag(false)} autoScroll={false}>
        {results ? (
          <div className="absolute grid" style={gridStyle} onClick={event => { if (event.target === event.currentTarget) setEditing(false); }}>
            {results.map((item, index) => renderEntry({ kind: "item", id: item.id }, index, selected))}
            {results.length === 0 && <p className={styles.empty}>{text("No results", "没有结果")}</p>}
          </div>
        ) : (
          <div className={styles.viewport} data-dim={!!openFolder || undefined}>
            <motion.div className={styles.strip} style={{ x: stripX }}>
              {shownPages.map((_, index) => (
                <div key={index} className="absolute top-[0] bottom-[0]" style={{ left: index * box.width, width: box.width }} onClick={event => { if (event.target === event.currentTarget) setEditing(false); }}>
                  <div className="absolute grid" style={gridStyle} onClick={event => { if (event.target === event.currentTarget) setEditing(false); }}>
                    {(dragId ? previewPage(index) : shownPages[index]).map((entry, at) => renderEntry(entry, at, index === current ? selected : null))}
                  </div>
                </div>
              ))}
            </motion.div>
          </div>
        )}

        {!results && shownPages.length > 1 && (
          <div className={styles.dots} data-dim={!!openFolder || undefined}>
            {shownPages.map((_, index) => (
              <button key={index} type="button" className={styles.dot} data-on={index === current || undefined}
                aria-label={text(`Page ${index + 1}`, `第 ${index + 1} 页`)} aria-current={index === current ? "page" : undefined}
                onClick={() => goPage(index)} />
            ))}
          </div>
        )}

        <AnimatePresence>
          {openFolder && (
            <motion.div key="folder" className={styles.folderLayer} initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: reduceMotion ? 0 : 0.2 }}
              onClick={event => { if (event.target === event.currentTarget) setOpenFolderId(null); }}>
              <motion.div className="flex flex-col items-center gap-[18px] max-w-[calc(100%_-_48px)]" initial={{ scale: 0.86 }} animate={{ scale: 1 }} exit={{ scale: 0.9 }} transition={reduceMotion ? { duration: 0 } : { type: "spring", stiffness: 420, damping: 34 }}>
                <input
                  key={openFolder.id}
                  className={styles.folderTitle}
                  defaultValue={openFolder.name}
                  placeholder={text("Folder", "文件夹")}
                  aria-label={text("Folder name", "文件夹名称")}
                  onBlur={event => { if (event.target.value.trim() !== openFolder.name) save(renameFolder(layout, openFolder.id, event.target.value)); }}
                  onKeyDown={event => { if (event.key === "Enter") event.currentTarget.blur(); }}
                />
                <div ref={folderPanelRef} className={styles.folderPanel}>
                  <div data-folder-grid className="grid" style={{ gridTemplateColumns: `repeat(${Math.min(FOLDER_COLUMNS, Math.max(openFolder.items.length, 1))}, ${cellW}px)`, gridAutoRows: cellH }}>
                    {previewFolder(openFolder).map((id, index) => id === PLACEHOLDER
                      ? <motion.div key={PLACEHOLDER} layout transition={shift} className={`${styles.cell} relative w-full h-full flex items-center justify-center`} />
                      : renderEntry({ kind: "item", id }, index, null))}
                  </div>
                </div>
              </motion.div>
            </motion.div>
          )}
        </AnimatePresence>

        <DragOverlay dropAnimation={reduceMotion ? null : { duration: 180, easing: "cubic-bezier(.25,.8,.25,1)" }}>
          {dragged && (
            <div className={styles.lifted}>
              {dragged.kind === "folder" ? <FolderTile folder={dragged} items={items} size={icon} /> : items.get(dragged.id) && <Tile item={items.get(dragged.id)!} size={icon} />}
            </div>
          )}
        </DragOverlay>
      </DndContext>
    </div>
  );
}
