"use client";

import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { flushSync } from "react-dom";
import { CircleHelp, ExternalLink, MoreVertical, X } from "lucide-react";

import { desktopBridge } from "@/lib/desktop/desktop-bridge";
import { ActionCueTravel } from "../browser/toolbar/browser-control-bar";
import { useTranslation } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { activeThemeId } from "@/lib/prefs/appearance/theme-pref";
import { buttonVariants } from "@/components/ui/button";
import { HoverTip, TipBody } from "@/components/ui/tooltip";
import { MenuOptionContent } from "@/components/ui/menu/option-content";
import { MenuRow } from "@/components/ui/menu/menu-parts";
import { itemCls, MENU_PANEL } from "@/components/ui/menu/menu-styles";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
  DropdownMenuSub,
  DropdownMenuSubTrigger,
  DropdownMenuSubContent,
} from "@/components/ui/dropdown-menu";
import { type SidebarMenuItem } from "@/components/sidebar/use-sidebar-menu";
import { useCenterTabs } from "@/lib/tabs/center-tabs-store";
import { useCanvasOverview } from "@/lib/tabs/canvas/canvas-overview";
import {
  browserTakeoverKind,
  controlResourceFromSession,
  presentedControlState,
  liveOperationMarker,
  operationHistory,
  resumeErrorFor,
  revealPendingApproval,
  showActionsEnabled,
  toggleShowActions,
  useBrowserControlStore,
} from "@/lib/browser/control/browser-control";
import { fittedImageRect, mapOperationPoint } from "@/lib/browser/control/browser-marker-geometry";
import { cueCancelKey, prefersCueReducedMotion } from "@/lib/browser/control/browser-action-cue";
import {
  followCurrentBranch,
  getPreviewPreference,
  latestFollowTarget,
  listedBrowserResources,
  previewTabId,
  selectResourcePreview,
  useBrowserResourceStore,
  viewedBranchFor,
  type SessionResource,
} from "@/lib/chat/resources/session-resources";
import { revealExistingWebTab } from "@/lib/browser/web-tab/web-page-management";
import { closePagePreview, hidePagePreview } from "@/lib/browser/browser-resource-projection";
import { foldTarget, PIP_FOLD, PIP_HOLD_WAIT_MS, PIP_UNFOLD, towardTarget } from "@/lib/browser/web-tab/web-tab-pip-motion";
import type { PipHoldDetail } from "./web-tab-pip-surface";
import {
  clampPipRect,
  followPipBox,
  getSnapshot,
  PIP_DEFAULT_HEIGHT,
  PIP_DEFAULT_WIDTH,
  PIP_MIN_HEIGHT,
  PIP_MIN_WIDTH,
  PIP_RESIZE_DIRS,
  pipChatRect,
  pipHostMode,
  resizePipRect,
  setSnapshot,
  startWebTabCaptureLoop,
  useWebTabPip,
  type PipResizeDir,
  type WebTabPipRect,
} from "@/lib/browser/web-tab/web-tab-pip-store";
import type { WebTabCaptureLoop } from "@/lib/browser/web-tab/web-tab-capture-loop";
import { dockClip, dockColumn, dockTarget, nearDock, PIP_DOCK_SPRING, pulledOut } from "@/lib/browser/web-tab/web-tab-pip-dock";
import { animate, type AnimationPlaybackControls } from "framer-motion";

import styles from "../browser/browser.module.css";
import { WebTabPipSurface } from "./web-tab-pip-surface";

/** The floating frame is a popover-coloured card: hairline ring, shadow,
 *  no border. Its 10px radius is pinned to the native page view's corner
 *  radius (the PiP corner radius in apps/desktop/main/web-views/web-views.js): the
 *  native surface is not clipped by the DOM, so a larger frame radius would
 *  let page pixels show past the bottom corners. One shadow at rest, under
 *  the pointer too; the deeper one only while it is dragged or resized
 *  (.webPipDragging), so grabbing it is what lifts it. Geometry (position,
 *  size) stays in the CSS module and inline styles. */
export const PIP_FRAME = "rounded-lg bg-popover text-popover-foreground ring-1 ring-(color:--popover-ring) shadow-(--popover-shadow) transition-shadow";
export const PIP_STAGE = "rounded-b-lg";
/** Header icon buttons are the official ghost icon Button (`icon-xs`, 21px
 *  at the app's 14px root) carried on plain <button>s so the menu trigger
 *  and drag handle keep their refs. Icons stay 14px like the toolbar's.
 *  Ghost hover paints `bg-muted`, which this app maps to the same token as
 *  the frame's `bg-popover`, so on this surface the hover was invisible;
 *  the fill is mixed toward the foreground instead. */
export const PIP_ICON_BUTTON = cn(
  buttonVariants({ variant: "ghost", size: "icon-xs" }),
  "hover:bg-[color-mix(in_oklch,var(--popover),var(--foreground)_9%)]",
  "aria-expanded:bg-[color-mix(in_oklch,var(--popover),var(--foreground)_9%)]",
  "dark:hover:bg-[color-mix(in_oklch,var(--popover),var(--foreground)_12%)]",
);
export const PIP_ICON = "size-[14px]";

/** How far a pressed pointer may wander and still be a click. */
const PIP_PRESS_SLOP = 3;

type PipDrag = {
  kind: "move" | "resize";
  /** The frame has started to move or change size. Until it does, a press
   *  is only a press: the live page stays up and nothing is redrawn. */
  moved?: boolean;
  /** Started on the docked preview: it resists until pulled out. */
  docked?: boolean;
  /** Started docked: leaving the dock re-renders the preview at its free
   *  place, so moves write the geometry rather than a transform. */
  fromDock?: boolean;
  /** The column's top it is snapped to, while it is. */
  snapped?: boolean;
  /** Pulled out of the dock, it may snap back only once it has been
   *  clear of the snap distance, or it would fall straight back in. */
  armed?: boolean;
  /** Where the preview was last drawn, for a spring to start from. */
  shown?: WebTabPipRect;
  /** Where it belongs now, before the spring's lag. */
  place?: WebTabPipRect;
  /** The spring's lag behind where it belongs: shown = target + lag. */
  lag?: WebTabPipRect;
  spring?: AnimationPlaybackControls;
  /** The free size it takes once out of the dock. */
  freeSize?: { width: number; height: number };
  pointerId: number;
  startX: number;
  startY: number;
  origin: WebTabPipRect;
  bounds: WebTabPipRect;
  tabId: string;
  target: HTMLElement;
  dir?: PipResizeDir;
};

/** Commits `follow` synchronously until the layout `read` describes stays
 *  the same across the commit. A synchronous commit can also commit other
 *  pending updates (React renders pending input updates along with a
 *  synchronous one), and one of them can move the pane the frame was just
 *  placed in: the first step of a sidebar drag lands its new width there.
 *  Following again places the frame in the pane that stands, so it is
 *  never painted, nor its page measured, at the place of a pane that is
 *  gone. */
export function followUntilSettled(read: () => string, follow: () => void): void {
  for (let pass = 0; pass < 3; pass += 1) {
    const before = read();
    flushSync(follow);
    if (read() === before) return;
  }
}

/** The pane box as a comparable value. */
export function boxKey(box: WebTabPipRect): string {
  return `${box.x},${box.y},${box.width},${box.height}`;
}

export function containerBox(el: HTMLElement): WebTabPipRect {
  const box = rawContainerBox(el);
  // Keep 12px outward corner targets clear of the adjacent 8px sidebar hit area,
  // plus 2px separation. This is outside the preview, never page padding.
  const insetX = Math.min(22, box.width / 2);
  const insetY = Math.min(22, box.height / 2);
  return { x: box.x + insetX, y: box.y + insetY,
    width: box.width - insetX * 2, height: box.height - insetY * 2 };
}

function ownerPane(el: HTMLElement, parent: HTMLElement): HTMLElement | null {
  const owner = el.dataset.pipOwnerTabId;
  return [...parent.querySelectorAll<HTMLElement>("[data-center-tab-id]")]
    .find(pane => pane.dataset.centerTabId === owner)
    ?? parent.querySelector<HTMLElement>(".center-pane-chat");
}

function rawContainerBox(el: HTMLElement): WebTabPipRect {
  const parent = el.offsetParent as HTMLElement | null;
  if (!parent) {
    return { x: 0, y: 0, width: window.innerWidth, height: window.innerHeight };
  }
  const parentRect = parent.getBoundingClientRect();
  const chat = ownerPane(el, parent);
  if (chat instanceof HTMLElement && getComputedStyle(chat).display !== "none") {
    const rect = chat.getBoundingClientRect();
    return {
      x: rect.left - parentRect.left,
      y: rect.top - parentRect.top,
      width: rect.width,
      height: rect.height,
    };
  }
  return { x: 0, y: 0, width: parentRect.width, height: parentRect.height };
}

function withLag(rect: WebTabPipRect, lag: WebTabPipRect | undefined): WebTabPipRect {
  return lag
    ? { x: rect.x + lag.x, y: rect.y + lag.y, width: rect.width + lag.width, height: rect.height + lag.height }
    : rect;
}

function measuredRect(el: HTMLElement): WebTabPipRect {
  const parent = el.offsetParent as HTMLElement | null;
  const er = el.getBoundingClientRect();
  if (!parent) {
    return { x: er.left, y: er.top, width: er.width, height: er.height };
  }
  const pr = parent.getBoundingClientRect();
  return {
    x: er.left - pr.left,
    y: er.top - pr.top,
    width: er.width,
    height: er.height,
  };
}

function resourceForTab(tabId: string): SessionResource | undefined {
  return listedBrowserResources().find(row => previewTabId(row) === tabId);
}

function statusLabel(
  state: ReturnType<typeof presentedControlState>,
  text: (en: string, zh: string) => string,
  connected: boolean,
): string {
  // The page's edge glows while the Agent holds it; the header speaks only
  // when the reader is needed or the state cannot be told.
  if (state === "waiting") return text("Needs your confirmation", "需要你确认");
  if (state === "unknown") {
    return connected
      ? text("Could not confirm status", "无法确认状态")
      : text("Connection lost", "连接已断开");
  }
  return "";
}

function PipActionMark({
  point,
  body,
  image,
  resourceId,
  generation,
  geometryRevision,
  operationKey,
}: {
  point: { x: number; y: number; width?: number; height?: number };
  body: HTMLElement | null;
  image: { width: number; height: number };
  resourceId: string;
  generation: number;
  geometryRevision?: number;
  operationKey?: string;
}) {
  if (!body) return null;
  const box = body.getBoundingClientRect();
  const fitted = fittedImageRect({ width: box.width, height: box.height }, image);
  const pos = mapOperationPoint(point, fitted, image);
  const mapped = pos ? { x: pos.left, y: pos.top } : null;
  return (
    <ActionCueTravel
      point={mapped}
      resourceKey={resourceId}
      operationKey={operationKey}
      cancelKey={cueCancelKey({
        resourceId,
        generation,
        geometryRevision,
        navKey: "pip",
      })}
      reducedMotion={prefersCueReducedMotion()}
    />
  );
}

function PipMoreMenu({
  historyItems,
  historyLabel,
  showLabel,
  following,
  onToggleFollow,
}: {
  historyItems: SidebarMenuItem[];
  historyLabel: string;
  showLabel: string;
  following: boolean;
  onToggleFollow: () => void;
}) {
  const { text } = useTranslation();
  const prefix = `pip-menu:${useId()}:`;
  const overlay = desktopBridge()?.mainMenu;
  const [open, setOpen] = useState(false);
  const opened = useRef(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const dispatch = useRef<(id: string) => void>(() => {});
  const moreLabel = text("More", "更多");
  const followLabel = text("Automatically show the page the Agent is using", "自动显示 Agent 正在操作的网页");
  const description = text(
    "When enabled, the preview switches to the page the Agent is using. When disabled, it keeps the current page. This only changes the preview, not the Agent's actions.",
    "开启后，小窗随 Agent 的操作切换网页；关闭后，保持当前网页。只改变显示内容，不影响 Agent 执行。",
  );
  const items: SidebarMenuItem[] = [
    { id: `${prefix}follow-page`, glyph: "eye", label: followLabel, description, checked: following, keepOpen: true, onSelect: onToggleFollow },
    { id: `${prefix}show-actions`, glyph: "cursor", label: showLabel, checked: showActionsEnabled(), keepOpen: true, onSelect: () => { toggleShowActions(); } },
    { id: `${prefix}history`, glyph: "history", label: historyLabel, separatorBefore: true,
      children: historyItems.map(item => ({ glyph: "clock-circle" as const, ...item, id: `${prefix}history:${item.id}` })) },
  ];
  const serializedItems = JSON.stringify(items);
  useEffect(() => {
    if (open && opened.current) overlay?.updateItems?.(JSON.parse(serializedItems));
  }, [open, overlay, serializedItems]);
  dispatch.current = id => items.find(item => item.id === id)?.onSelect?.();
  useEffect(() => {
    if (!overlay) return;
    const action = overlay.onAction(id => {
      if (opened.current && id.startsWith(prefix)) dispatch.current(id);
    });
    const closed = overlay.onClosed?.(() => {
      if (!opened.current) return;
      opened.current = false;
      setOpen(false);
      trigger.current?.focus();
    });
    return () => {
      action();
      closed?.();
      if (opened.current) overlay.close();
      opened.current = false;
    };
  }, [overlay, prefix]);
  const tip = <TipBody title={moreLabel}
    detail={text("Auto-follow, action markers and operation history", "自动跟随、操作标记与操作历史")} />;
  const button = <button ref={trigger} type="button" className={PIP_ICON_BUTTON}
    aria-label={moreLabel} aria-haspopup="menu" aria-expanded={open}
    onClick={overlay ? () => {
      if (opened.current) { overlay.close(); return; }
      const rect = trigger.current!.getBoundingClientRect();
      overlay.open({
        theme: activeThemeId(), width: 360,
        anchor: { x: Math.max(8, rect.right - 360), y: rect.bottom + 4, vw: window.innerWidth, vh: window.innerHeight },
        items: items.map(({ onSelect: _onSelect, ...item }) => item),
      });
      opened.current = true;
      setOpen(true);
    } : undefined}><MoreVertical size={14} className={PIP_ICON} aria-hidden="true" /></button>;
  if (overlay) return <HoverTip label={tip}>{button}</HoverTip>;
  return <DropdownMenu open={open} onOpenChange={setOpen}>
    <HoverTip label={tip}><DropdownMenuTrigger asChild>{button}</DropdownMenuTrigger></HoverTip>
    <DropdownMenuContent className={`${MENU_PANEL} w-[360px] max-w-[calc(100vw-16px)]`}>
      {items.slice(0, 2).map(item => <DropdownMenuItem key={item.id}
        role="menuitemcheckbox" aria-checked={item.checked} className={itemCls(false)} onSelect={event => { event.preventDefault(); item.onSelect?.(); }}>
        <MenuOptionContent glyph={item.glyph} label={item.label} description={item.description} checked={item.checked} />
      </DropdownMenuItem>)}
      <DropdownMenuSub>
        <DropdownMenuSubTrigger className={itemCls(false)}><MenuRow glyph="history" label={historyLabel} submenu /></DropdownMenuSubTrigger>
        <DropdownMenuSubContent className={`${MENU_PANEL} max-w-[calc(100vw-16px)]`}>
          {historyItems.map(item => <DropdownMenuItem key={item.id} disabled className={itemCls(false)}><MenuRow glyph="clock-circle" label={item.label} /></DropdownMenuItem>)}
        </DropdownMenuSubContent>
      </DropdownMenuSub>
    </DropdownMenuContent>
  </DropdownMenu>;

}

/** The preview's frame: where it is drawn, and following its pane (and the
 *  dock slot while docked) as layout moves them. It is its own component so
 *  that a move commits only the frame's place: the header, menus and page
 *  surface inside are the `children` WebTabPip rendered, the same elements
 *  from one move to the next, so React leaves them alone while the frame
 *  follows a sidebar or panel frame by frame. */
function PipFrame({
  frameRef,
  dragRef,
  presentedRef,
  live,
  expanded,
  tabId,
  ownerTabId,
  paneSet,
  activeId,
  dockedHere,
  dockSlot,
  title,
  state,
  children,
}: {
  frameRef: React.RefObject<HTMLDivElement>;
  dragRef: React.MutableRefObject<PipDrag | null>;
  presentedRef: React.MutableRefObject<WebTabPipRect | null>;
  live: boolean;
  expanded: boolean;
  tabId: string;
  ownerTabId: string | null;
  paneSet: string;
  activeId: string | null;
  dockedHere: boolean;
  dockSlot: HTMLElement | null;
  title: string;
  state: string;
  children: React.ReactNode;
}) {
  const rect = useWebTabPip((s) => s.rect);
  const expandedSize = useWebTabPip((s) => s.expandedSize);
  const rootRef = frameRef;
  const [chatBox, setChatBox] = useState<WebTabPipRect | null>(null);
  /** Over the graph column's slot while docked, with how much of it the
   *  column's scroll hides. */
  const [dockBox, setDockBox] = useState<{ rect: WebTabPipRect; top: number; bottom: number } | null>(null);
  /** Reads the dock slot's place now, while docked (set by the dock effect). */
  const measureDockRef = useRef<(() => void) | null>(null);

  useLayoutEffect(() => {
    const el = rootRef.current;
    if (!el || !live) {
      setChatBox(null);
      return;
    }
    const next = containerBox(el);
    setChatBox((prev) => (
      prev
        && prev.x === next.x
        && prev.y === next.y
        && prev.width === next.width
        && prev.height === next.height
        ? prev
        : next
    ));
  }, [rootRef, live, expanded, rect, expandedSize, paneSet, activeId, tabId, ownerTabId]);

  useEffect(() => {
    const el = rootRef.current;
    if (!el || !live) return;
    const parent = el.offsetParent;
    if (!(parent instanceof HTMLElement)) return;
    const follow = () => {
      const box = containerBox(el);
      // Layout hydration and split resizing also move a preview that has no
      // saved drag rectangle. Always refresh its owner bounds first.
      setChatBox(prev => prev && prev.x === box.x && prev.y === box.y
        && prev.width === box.width && prev.height === box.height ? prev : box);
      // An expanded preview keeps its place unclamped, as before.
      followPipBox(box, !expanded, !!dragRef.current);
    };
    // Where the frame is placed from: its pane, and the dock slot while docked.
    const layout = () => {
      const slot = measureDockRef.current ? useWebTabPip.getState().dockSlot?.getBoundingClientRect() : null;
      return `${boxKey(containerBox(el))};${slot ? boxKey({ x: slot.left, y: slot.top, width: slot.width, height: slot.height }) : ""}`;
    };
    // Put the frame where this frame's layout wants it, synchronously. A
    // drag draws the preview itself, so nothing is committed under it.
    const settle = () => {
      if (dragRef.current) { follow(); return; }
      followUntilSettled(layout, () => { measureDockRef.current?.(); follow(); });
    };
    // The observer reports after layout and before paint. Committing there
    // keeps the preview still on screen in the frame its pane moved: a
    // sidebar that slides open would otherwise drag it along one frame late.
    // The page inside is told once the frame is in place, never before.
    const ro = new ResizeObserver(() => {
      settle();
      if (!dragRef.current) el.dispatchEvent(new Event("op:pip-geometry"));
    });
    ro.observe(parent);
    ro.observe(el);
    const chat = ownerPane(el, parent);
    if (chat instanceof HTMLElement) ro.observe(chat);
    // The surface asks for this before it measures where the page goes
    // (web-tab-pip-surface.tsx): measured ahead of the observer, the frame
    // still has last frame's offset inside a pane that already moved.
    el.addEventListener("op:pip-settle", settle);
    follow();
    return () => {
      ro.disconnect();
      el.removeEventListener("op:pip-settle", settle);
    };
  }, [rootRef, dragRef, live, expanded, tabId, paneSet, activeId, ownerTabId]);

  // Docked: follow the slot every frame — the column scrolls, the minimap
  // above it comes and goes, the pane resizes — and publish the change
  // only when it moves.
  useLayoutEffect(() => {
    const el = rootRef.current;
    if (!dockedHere || !el || !dockSlot) {
      setDockBox(null);
      return;
    }
    let frame = 0;
    const measure = () => {
      if (dragRef.current) return;
      const parent = el.offsetParent as HTMLElement | null;
      const column = dockSlot.closest<HTMLElement>(".dag-side-column");
      if (!parent || !column || !dockSlot.isConnected) return;
      const origin = parent.getBoundingClientRect();
      const slot = dockSlot.getBoundingClientRect();
      const clip = dockClip(slot, column.getBoundingClientRect());
      const next = { rect: { x: slot.left - origin.left, y: slot.top - origin.top, width: slot.width, height: slot.height }, ...clip };
      setDockBox(prev => prev && prev.rect.x === next.rect.x && prev.rect.y === next.rect.y
        && prev.rect.width === next.rect.width && prev.rect.height === next.rect.height
        && prev.top === next.top && prev.bottom === next.bottom ? prev : next);
    };
    const follow = () => {
      frame = window.requestAnimationFrame(follow);
      measure();
    };
    measureDockRef.current = measure;
    follow();
    return () => {
      window.cancelAnimationFrame(frame);
      measureDockRef.current = null;
    };
  }, [rootRef, dragRef, dockedHere, dockSlot]);

  const presented = dockedHere && dockBox
    ? dockBox.rect
    : live && chatBox
      ? pipChatRect(rect, expanded, chatBox, expandedSize)
      : null;
  const clipped = !!dockBox && dockedHere && (dockBox.top > 0 || dockBox.bottom > 0);
  const pipStyle = !presented ? undefined : {
    left: presented.x,
    top: presented.y,
    width: presented.width,
    height: presented.height,
    right: "auto",
    bottom: "auto",
    clipPath: clipped && dockBox ? `inset(${dockBox.top}px 0 ${dockBox.bottom}px 0)` : undefined,
  };

  useLayoutEffect(() => {
    presentedRef.current = presented;
  });

  return (
    <div
      ref={rootRef}
      className={cn(styles.webPip, PIP_FRAME, expanded && styles.webPipExpanded)}
      data-pip="true"
      data-pip-host="chat"
      data-pip-owner-tab-id={ownerTabId ?? undefined}
      data-pip-docked={dockedHere ? "true" : undefined}
      data-pip-clipped={clipped ? "true" : undefined}
      role="complementary"
      aria-label={title}
      data-state={state}
      style={pipStyle}
    >
      {children}
    </div>
  );
}

export function WebTabPip() {
  const { text } = useTranslation();
  const tabId = useWebTabPip((s) => s.tabId);
  const ownerTabId = useWebTabPip((s) => s.ownerTabId);
  const ownerSessionId = useWebTabPip((s) => s.ownerSessionId);
  const setRect = useWebTabPip((s) => s.setRect);
  const docked = useWebTabPip((s) => s.docked);
  const dockSlot = useWebTabPip((s) => s.dockSlot);
  // The slot sits in the main chat view. A conversation shown in a split's
  // peer pane leaves that view hidden, and the slot with it: a docked
  // preview then stands at its free place in its pane and docks again once
  // the slot is laid out, rather than shrinking to the hidden slot's empty box.
  const [dockSlotShown, setDockSlotShown] = useState(false);
  useLayoutEffect(() => {
    if (!dockSlot) { setDockSlotShown(false); return; }
    const check = () => {
      const slot = dockSlot.getBoundingClientRect();
      setDockSlotShown(slot.width > 0 || slot.height > 0);
    };
    check();
    const ro = new ResizeObserver(check);
    ro.observe(dockSlot);
    return () => ro.disconnect();
  }, [dockSlot]);
  const tabs = useCenterTabs((s) => s.tabs);
  const activeId = useCenterTabs((s) => s.activeId);
  const groups = useCenterTabs((s) => s.groups);
  const splitWebTabId = useCenterTabs((s) => s.splitWebTabId);
  const tab = tabId
    ? tabs.find((item) => item.id === tabId && item.kind === "web")
    : undefined;
  const sessionId = ownerSessionId;
  const branchId = sessionId ? viewedBranchFor(sessionId) : null;
  const pref = sessionId ? getPreviewPreference(sessionId, branchId) : null;
  const connected = useBrowserResourceStore(s => s.connected);
  useBrowserResourceStore(s => s.ingestClock);
  useBrowserResourceStore(s => s.preferences);
  useBrowserControlStore(s => s.showActions);
  useBrowserControlStore(s => s.pending);
  useBrowserControlStore(s => s.resumeError);
  useBrowserControlStore(s => s.history);
  useBrowserControlStore(s => s.running);
  const resource = tabId ? resourceForTab(tabId) : undefined;
  const control = resource ? controlResourceFromSession(resource) : null;
  const center = { tabs, activeId, groups, splitWebTabId };
  // Which panes are on the canvas, not their sizes: a divider drag changes
  // the sizes every frame, and the ResizeObserver below follows those
  // after layout instead of forcing one per frame here.
  const paneSet = groups.map((group) => group.visibleIds.join(",")).join("|");
  // The window overview shows each window as it is, without the floating
  // preview over it; the preview comes back when the overview closes.
  const overview = useCanvasOverview((s) => s.mode !== null);
  const live = !overview && pipHostMode(tabId, ownerTabId, center) === "chat";
  const expanded = !!pref?.expanded;
  const rootRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<PipDrag | null>(null);
  const pendingRectRef = useRef<WebTabPipRect | null>(null);
  const rafRef = useRef(0);
  const shotRef = useRef<HTMLImageElement>(null);
  const captureGenRef = useRef(0);
  const captureLoopRef = useRef<WebTabCaptureLoop | null>(null);
  const dragFinishingRef = useRef(false);
  const endActiveDragRef = useRef<(persist: boolean) => void>(() => {});
  const [freshness, setFreshness] = useState<"live" | "last-frame" | "unavailable">("unavailable");
  /** Where the frame is drawn, as PipFrame last committed it. */
  const presentedRef = useRef<WebTabPipRect | null>(null);
  const [, render] = useState(0);
  const bridge = desktopBridge();
  const nativeSurface = typeof bridge?.webTab.syncVisible === "function";
  const interactive = nativeSurface || !bridge;
  const url = tab?.url || (tabId?.startsWith("w:") ? tabId.slice(2) : "");
  const marker = resource?.resourceId
    ? liveOperationMarker(resource.resourceId, { generation: resource.generation || 0 })
    : null;

  const showShot = (dataUrl: string | null) => {
    const img = shotRef.current;
    if (!img) return;
    if (dataUrl) {
      img.src = dataUrl;
      img.style.display = "block";
      return;
    }
    img.removeAttribute("src");
    img.style.display = "none";
  };

  useEffect(() => () => {
    endActiveDragRef.current(false);
  }, []);

  useEffect(() => {
    if (live) return;
    endActiveDragRef.current(false);
    captureGenRef.current += 1;
  }, [live]);

  useLayoutEffect(() => {
    if (!tabId || !live) return;
    showShot(getSnapshot(tabId) ?? null);
  }, [tabId, live]);

  useEffect(() => {
    if (!tabId || !live || interactive) return;
    const gen = ++captureGenRef.current;
    const capture = bridge?.webTab.capture;
    showShot(getSnapshot(tabId) ?? null);
    if (typeof capture !== "function") {
      setFreshness(getSnapshot(tabId) ? "last-frame" : "unavailable");
      return;
    }
    const loop = startWebTabCaptureLoop({
      tabId,
      generation: gen,
      isCurrent: () => {
        if (captureGenRef.current !== gen) return null;
        const pip = useWebTabPip.getState();
        if (pip.tabId !== tabId || pip.ownerSessionId !== ownerSessionId
            || pipHostMode(tabId, pip.ownerTabId, useCenterTabs.getState()) !== "chat") return null;
        return { tabId, generation: gen };
      },
      capture,
      onFrame: (id, dataUrl) => {
        if (captureGenRef.current !== gen || dragRef.current) return;
        setSnapshot(id, dataUrl);
        showShot(dataUrl);
        setFreshness("live");
      },
      onUnavailable: (id) => {
        if (captureGenRef.current !== gen || dragRef.current) return;
        setFreshness(getSnapshot(id) ? "last-frame" : "unavailable");
      },
    });
    captureLoopRef.current = loop;
    return () => {
      loop.stop();
      if (captureLoopRef.current === loop) captureLoopRef.current = null;
      endActiveDragRef.current(false);
      if (captureGenRef.current === gen) captureGenRef.current += 1;
    };
  }, [bridge, tabId, live, ownerSessionId, interactive]);

  useEffect(() => {
    // A native page can take the release before React's captured pointer sees
    // it. Finish at the window boundary as well so the next gesture can start.
    const finish = () => endActiveDragRef.current(true);
    const released = (event: MouseEvent) => { if (event.buttons === 0) finish(); };
    window.addEventListener("pointerup", finish);
    window.addEventListener("mouseup", finish);
    window.addEventListener("blur", finish);
    window.addEventListener("mousemove", released, true);
    return () => {
      window.removeEventListener("pointerup", finish);
      window.removeEventListener("mouseup", finish);
      window.removeEventListener("blur", finish);
      window.removeEventListener("mousemove", released, true);
      endActiveDragRef.current(false);
    };
  }, [tabId]);

  const motion = useWebTabPip((s) => s.motion);
  // The frame folds into the composer's web page chip before it closes,
  // and unfolds out of it as it opens (web-tab-pip-motion.ts); its page
  // shows as a still picture meanwhile (the surface's `motion` hold).
  useLayoutEffect(() => {
    const el = rootRef.current;
    if (!motion) return;
    // With no chip naming this page on screen, the preview fades where it is.
    const target = foldTarget(tabId);
    const still = "translate(0px, 0px) scale(1)";
    if (!el || !live) {
      if (motion === "folding") closePagePreview();
      else useWebTabPip.setState({ motion: null });
      return;
    }
    let cancelled = false;
    let running: AnimationPlaybackControls | null = null;
    const hold = (reason: PipHoldDetail["reason"]) => new Promise<void>((resolve) => {
      window.setTimeout(resolve, PIP_HOLD_WAIT_MS);
      el.dispatchEvent(new CustomEvent<PipHoldDetail>("op:pip-hold", { detail: { reason, ready: resolve } }));
    });
    const settle = () => {
      el.style.transform = "";
      el.style.opacity = "";
      el.style.transformOrigin = "";
    };
    if (motion === "unfolding") {
      el.style.transformOrigin = "50% 50%";
      const from = target ? towardTarget(el, target) : still;
      el.style.transform = from;
      el.style.opacity = "0";
      running = animate(el, { transform: [from, still], opacity: [0, 1] }, PIP_UNFOLD);
      void running.finished.catch(() => {}).then(() => {
        if (cancelled) return;
        settle();
        el.dispatchEvent(new CustomEvent<PipHoldDetail>("op:pip-release", { detail: { reason: "motion" } }));
        useWebTabPip.setState({ motion: null });
      });
    } else {
      void (nativeSurface ? hold("motion") : Promise.resolve()).then(() => {
        if (cancelled) return;
        el.style.transformOrigin = "50% 50%";
        // Both ends are named: from the frame's computed `none`, motion makes
        // a zeroed start (scale 0), so a page's first fold vanished and then
        // moved; later ones started from the value the unfold left behind.
        running = animate(el, { transform: [still, target ? towardTarget(el, target) : still], opacity: [1, 0] }, PIP_FOLD);
        return running.finished.catch(() => {});
      }).then(() => { if (!cancelled) closePagePreview(); });
    }
    return () => {
      cancelled = true;
      running?.stop();
      settle();
    };
  // `live` and the surface are read when the motion starts, not followed.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [motion]);

  const dockedHere = live && docked && !expanded && !!dockSlot && dockSlotShown;
  if (!tabId || !tab || !live) return null;

  // A record names the page it last saw; once the tab has navigated away
  // (a stale record after restore), the tab's live title is the right one.
  const recordCurrent = !resource?.target || !tab.url || resource.target === tab.url;
  const title = (recordCurrent ? resource?.title : "") || tab.title || resource?.title || url;
  const openPage = text("Open page", "打开页面");
  const openPageHint = text("Switch to this tab in the centre", "在中间区域切换到这个标签");
  const reviewLabel = text("Review request", "查看请求");
  const reviewHint = text("The agent is waiting for your confirmation", "Agent 正在等待你的确认");
  const hideLabel = text("Close preview", "关闭小窗");
  const hideHint = text("Hides the preview; the agent keeps working", "只隐藏小窗；Agent 继续工作");
  const resizeLabels: Record<PipResizeDir, string> = {
    n: text("Resize from top", "从顶部调整大小"),
    ne: text("Resize from top right", "从右上角调整大小"),
    e: text("Resize from right", "从右侧调整大小"),
    se: text("Resize from bottom right", "从右下角调整大小"),
    s: text("Resize from bottom", "从底部调整大小"),
    sw: text("Resize from bottom left", "从左下角调整大小"),
    w: text("Resize from left", "从左侧调整大小"),
    nw: text("Resize from top left", "从左上角调整大小"),
  };
  const pinned = pref?.mode === "manual";
  const controlState = control ? presentedControlState(control) : null;
  const stateText = controlState ? statusLabel(controlState, text, connected) : "";
  const resumeError = control ? resumeErrorFor(control.resourceId) : undefined;
  const frameState = !connected && freshness === "live" ? "last-frame" : freshness;
  const freshLabel = frameState === "live"
    ? text("Read-only image mirror", "只读图像镜像")
    : frameState === "last-frame"
      ? text("Last frame", "最后一帧")
      : text("Image preview unavailable", "无法预览图像");
  const takeoverKind = browserTakeoverKind(controlState);
  const history = control ? operationHistory(control.resourceId) : [];
  const historyLabel = text("Operation history", "操作历史");
  const showLabel = text("Show actions", "显示操作");
  const historyItems: SidebarMenuItem[] = history.length === 0
    ? [{ id: "empty", label: text("No operations yet.", "尚无操作。"), disabled: true }]
    : history.map(item => ({
      id: item.id,
      label: `${item.action} · ${item.phase}${item.error ? ` · ${item.error}` : ""}`,
      disabled: true,
    }));

  const togglePinnedPreview = () => {
    if (!sessionId) return;
    if (pinned) {
      const next = followCurrentBranch(sessionId, branchId);
      const target = listedBrowserResources().find(row => row.id === (next.targetId || latestFollowTarget(sessionId, branchId)));
      const nextTab = previewTabId(target);
      if (nextTab && ownerTabId) useWebTabPip.getState().show(nextTab, ownerTabId);
    } else if (resource?.id) {
      selectResourcePreview(sessionId, branchId, resource.id);
    }
    render(value => value + 1);
  };

  const liveRect = (el: HTMLElement) => presentedRef.current ?? useWebTabPip.getState().rect ?? measuredRect(el);

  const previewRect = (
    el: HTMLElement,
    drag: PipDrag,
    next: WebTabPipRect,
  ) => {
    drag.shown = next;
    if (drag.kind === "move" && !drag.fromDock && !drag.lag && drag.snapped === undefined
        && next.width === drag.origin.width && next.height === drag.origin.height) {
      el.style.transform = `translate(${next.x - drag.origin.x}px, ${next.y - drag.origin.y}px)`;
      el.dispatchEvent(new Event("op:pip-geometry"));
      return;
    }
    el.style.transform = "";
    el.style.left = `${next.x}px`;
    el.style.top = `${next.y}px`;
    el.style.width = `${next.width}px`;
    el.style.height = `${next.height}px`;
    el.style.right = "auto";
    el.style.bottom = "auto";
    el.dispatchEvent(new Event("op:pip-geometry"));
  };

  const applyInlineRect = (el: HTMLElement, next: WebTabPipRect) => {
    el.style.transform = "";
    el.style.willChange = "";
    el.classList.remove(styles.webPipDragging);
    el.style.left = `${next.x}px`;
    el.style.top = `${next.y}px`;
    el.style.width = `${next.width}px`;
    el.style.height = `${next.height}px`;
    el.style.right = "auto";
    el.style.bottom = "auto";
    el.dispatchEvent(new Event("op:pip-geometry"));
  };

  const persistRect = (
    el: HTMLElement | null,
    next: WebTabPipRect,
    kind: "move" | "resize",
  ) => {
    if (el) applyInlineRect(el, next);
    const rect = useWebTabPip.getState().rect;
    const collapsed = {
      x: next.x,
      y: next.y,
      width: rect?.width ?? PIP_DEFAULT_WIDTH,
      height: rect?.height ?? PIP_DEFAULT_HEIGHT,
    };
    if (expanded) {
      if (kind === "resize") {
        useWebTabPip.setState({
          rect: collapsed,
          expandedSize: { width: next.width, height: next.height },
        });
        return;
      }
      setRect(collapsed);
      return;
    }
    setRect(next);
  };

  const finishDrag = (persist: boolean) => {
    const drag = dragRef.current;
    if (!drag || dragFinishingRef.current) return;
    dragFinishingRef.current = true;
    dragRef.current = null;
    drag.spring?.stop();
    if (rafRef.current) {
      window.cancelAnimationFrame(rafRef.current);
      rafRef.current = 0;
    }
    const next = pendingRectRef.current;
    pendingRectRef.current = null;
    const el = rootRef.current;
    if (drag.target.hasPointerCapture(drag.pointerId)) {
      drag.target.releasePointerCapture(drag.pointerId);
    }
    const pip = useWebTabPip.getState();
    const commit = persist && !!next && live && pip.tabId === drag.tabId;
    if (commit && pip.dockHover) {
      // Dropped at the column: it docks, and keeps its free place for later.
      // Land exactly on the slot: React may not restyle a preview whose
      // docked place did not change while it was dragged.
      useWebTabPip.setState({ docked: true, dockHover: false });
      const slot = useWebTabPip.getState().dockSlot;
      const parent = el?.offsetParent as HTMLElement | null;
      if (el && slot && parent) {
        const box = slot.getBoundingClientRect();
        const origin = parent.getBoundingClientRect();
        applyInlineRect(el, { x: box.left - origin.left, y: box.top - origin.top, width: box.width, height: box.height });
      } else if (el) {
        applyInlineRect(el, drag.place ?? next ?? drag.origin);
      }
    } else if (commit && next && !drag.docked) {
      if (drag.fromDock) useWebTabPip.setState({ docked: false });
      persistRect(el, clampPipRect(next, el ? containerBox(el) : drag.bounds), drag.kind);
    } else if (el) {
      // Let go before it came out of the dock: back into its place.
      applyInlineRect(el, drag.origin);
    }
    if (pip.dockHover) useWebTabPip.setState({ dockHover: false });
    el?.dispatchEvent(new Event("op:pip-gesture-end"));
    if (persist) captureLoopRef.current?.resume();
    dragFinishingRef.current = false;
  };
  endActiveDragRef.current = finishDrag;

  const onDragPointerDown = (
    kind: "move" | "resize",
    event: React.PointerEvent<HTMLElement>,
    dir?: PipResizeDir,
  ) => {
    if (event.button !== 0 || dragRef.current || motion) return;
    const el = rootRef.current;
    if (!el) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    const fromDock = kind === "move" && dockedHere;
    const rect = useWebTabPip.getState().rect;
    dragRef.current = {
      kind,
      docked: fromDock,
      fromDock,
      freeSize: fromDock
        ? { width: rect?.width ?? PIP_DEFAULT_WIDTH, height: rect?.height ?? PIP_DEFAULT_HEIGHT }
        : undefined,
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      origin: liveRect(el),
      bounds: containerBox(el),
      tabId,
      target: event.currentTarget,
      dir,
    };
    pendingRectRef.current = dragRef.current.origin;
    el.classList.add(styles.webPipDragging);
    captureLoopRef.current?.pause();
  };

  /** Draw the drag's current place: where it belongs plus the spring's lag. */
  const drawDrag = (drag: PipDrag) => {
    const node = rootRef.current;
    if (!node || !drag.place || dragRef.current !== drag) return;
    const next = withLag(drag.place, drag.lag);
    pendingRectRef.current = next;
    previewRect(node, drag, next);
  };

  /** Start a spring from `from` (where it is drawn) to `to` (where it now
   *  belongs): the lag between them decays to nothing while `to` keeps
   *  following the pointer. */
  const springFrom = (drag: PipDrag, from: WebTabPipRect, to: WebTabPipRect) => {
    drag.spring?.stop();
    const start = { x: from.x - to.x, y: from.y - to.y, width: from.width - to.width, height: from.height - to.height };
    drag.lag = start;
    drag.place = to;
    drag.spring = animate(1, 0, {
      ...PIP_DOCK_SPRING,
      onUpdate: (k) => {
        drag.lag = { x: start.x * k, y: start.y * k, width: start.width * k, height: start.height * k };
        drawDrag(drag);
      },
      onComplete: () => { drag.lag = { x: 0, y: 0, width: 0, height: 0 }; drawDrag(drag); },
    });
  };

  const onDragPointerMove = (event: React.PointerEvent<HTMLElement>) => {
    const drag = dragRef.current;
    const el = rootRef.current;
    if (!drag || drag.pointerId !== event.pointerId || !el) return;
    const dx = event.clientX - drag.startX;
    const dy = event.clientY - drag.startY;
    if (!drag.moved) {
      // A press moves nothing until the pointer has gone past a click's
      // jitter, or a docked preview's pull distance; then the page's
      // picture stands in for it (the surface's `gesture` hold).
      if (drag.docked ? !pulledOut(dx, dy) : Math.hypot(dx, dy) < PIP_PRESS_SLOP) return;
      drag.moved = true;
      el.style.willChange = drag.kind === "move" ? "transform" : "left, top, width, height";
      el.dispatchEvent(new Event("op:pip-gesture-start"));
    }
    if (drag.docked) {
      // Docked: it holds still until the pointer has gone the pull
      // distance, then comes out at its free size and springs to the
      // pointer, keeping the pointer's offset in the header where it fits.
      const free = drag.freeSize ?? { width: drag.origin.width, height: drag.origin.height };
      const offset = drag.startX - (el.offsetParent as HTMLElement).getBoundingClientRect().left - drag.origin.x;
      // The store stays docked until the drop, so the column keeps the
      // place open and nothing re-lays the preview out under the drag.
      drag.docked = false;
      drag.armed = false;
      drag.origin = { x: drag.origin.x + offset - Math.min(offset, free.width - 24), y: drag.origin.y, ...free };
      springFrom(drag, drag.shown ?? drag.origin, clampPipRect(
        { ...drag.origin, x: drag.origin.x + dx, y: drag.origin.y + dy }, drag.bounds));
    }
    const column = drag.kind === "move" && el.offsetParent
      ? dockColumn(ownerPane(el, el.offsetParent as HTMLElement))
      : null;
    const free = drag.kind === "move"
      ? clampPipRect(
        { ...drag.origin, x: drag.origin.x + dx, y: drag.origin.y + dy },
        drag.bounds,
      )
      : null;
    const target = column && free && !expanded
      ? dockTarget(column, (el.offsetParent as HTMLElement).getBoundingClientRect())
      : null;
    if (target && free && drag.armed === false && !nearDock(free, target)) drag.armed = true;
    const snapping = drag.armed !== false && !!target && !!free && nearDock(free, target, !!drag.snapped);
    if (drag.kind === "move" && snapping !== !!drag.snapped) {
      // Snapping in or coming off: spring from where it is drawn.
      const was = drag.shown ?? drag.origin;
      drag.snapped = snapping;
      springFrom(drag, was, snapping && target ? target : free!);
    }
    if (snapping !== useWebTabPip.getState().dockHover) useWebTabPip.setState({ dockHover: snapping });
    const base = drag.kind === "move"
      ? (snapping && target ? target : free!)
      : resizePipRect(
        drag.origin,
        dx,
        dy,
        drag.bounds,
        PIP_MIN_WIDTH,
        PIP_MIN_HEIGHT,
        drag.dir ?? "se",
      );
    drag.place = base;
    const next = withLag(base, drag.lag);
    pendingRectRef.current = next;
    if (rafRef.current) return;
    rafRef.current = window.requestAnimationFrame(() => {
      rafRef.current = 0;
      const liveDrag = pendingRectRef.current;
      const current = dragRef.current;
      const node = rootRef.current;
      if (!liveDrag || !current || !node) return;
      previewRect(node, current, liveDrag);
    });
  };

  const onDragPointerUp = (event: React.PointerEvent<HTMLElement>) => {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    onDragPointerMove(event);
    finishDrag(true);
  };

  const onDragPointerCancel = (event: React.PointerEvent<HTMLElement>) => {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    finishDrag(true);
  };

  return (
    <PipFrame
      frameRef={rootRef}
      dragRef={dragRef}
      presentedRef={presentedRef}
      live={live}
      expanded={expanded}
      tabId={tabId}
      ownerTabId={ownerTabId}
      paneSet={paneSet}
      activeId={activeId}
      dockedHere={dockedHere}
      dockSlot={dockSlot}
      title={title}
      state={controlState || "readonly"}
    >
      <div
        className={styles.webPipChrome}
        onPointerDown={(event) => onDragPointerDown("move", event)}
        onPointerMove={onDragPointerMove}
        onPointerUp={onDragPointerUp}
        onPointerCancel={onDragPointerCancel}
        onLostPointerCapture={onDragPointerCancel}
      >
        <span
          className={cn(styles.webPipTitle, "truncate text-fs-xs font-medium text-text-primary")}
        >
          {title}
        </span>
        {/* The header names the page; a status shows only while the Agent
            acts or needs the reader. Auto or fixed preview is set in More. */}
        {stateText ? (
          <small
            className={cn(styles.webPipMode, "truncate text-fs-2xs text-text-muted")}
            aria-label={stateText}
          >
            {stateText}
          </small>
        ) : null}
        <div className={styles.webPipActions} onPointerDown={(event) => event.stopPropagation()}>
          <HoverTip label={<TipBody title={openPage} detail={openPageHint} />}>
            <button
              type="button"
              className={PIP_ICON_BUTTON}
              onClick={() => revealExistingWebTab(tabId, useCenterTabs.getState())}
              aria-label={openPage}
            >
              <ExternalLink size={14} className={PIP_ICON} aria-hidden="true" />
            </button>
          </HoverTip>
          {takeoverKind === "reveal" && control ? (
            <HoverTip label={<TipBody title={reviewLabel} detail={reviewHint} />}>
              <button type="button" className={PIP_ICON_BUTTON} aria-label={reviewLabel}
                onClick={() => revealPendingApproval(control)}>
                <CircleHelp size={14} className={PIP_ICON} aria-hidden="true" />
              </button>
            </HoverTip>
          ) : null}
          <PipMoreMenu
            key={`${sessionId}:${branchId}`}
            historyItems={historyItems}
            historyLabel={historyLabel}
            showLabel={showLabel}
            following={!pinned}
            onToggleFollow={togglePinnedPreview}
          />
          <HoverTip label={<TipBody title={hideLabel} detail={hideHint} />}>
            <button
              type="button"
              className={cn(PIP_ICON_BUTTON, "-mr-1")}
              onClick={hidePagePreview}
              aria-label={hideLabel}
            >
              <X size={14} className={PIP_ICON} aria-hidden="true" />
            </button>
          </HoverTip>
        </div>
      </div>
      {resumeError ? (
        <span
          className={cn(styles.webPipNotice, "truncate text-fs-2xs text-destructive")}
          data-resume-error="true"
          role="status"
          aria-live="polite"
        >
          {resumeError}
        </span>
      ) : null}
      <div className={cn("overflow-hidden relative flex-1 min-h-0 flex flex-col", PIP_STAGE)}>
        {interactive ? <WebTabPipSurface key={tabId} tabId={tabId} url={url} native={nativeSurface} holdOnMount={motion === "unfolding"} /> : <>
        <div className={styles.webPipBody}>
          <img ref={shotRef} className={styles.webPipShot} alt="" />
          {frameState !== "live" && (
            <div className={cn(styles.webPipFallback, "text-fs-2xs text-text-muted")}>{freshLabel}</div>
          )}
          {marker?.point && resource?.resourceId ? (
            <PipActionMark
              point={marker.point}
              body={shotRef.current?.parentElement ?? null}
              image={{
                width: shotRef.current?.naturalWidth || marker.point.width || 0,
                height: shotRef.current?.naturalHeight || marker.point.height || 0,
              }}
              resourceId={resource.resourceId}
              generation={resource.generation || 0}
              geometryRevision={marker.geometry_revision}
              operationKey={marker.id}
            />
          ) : null}
        </div>
        </>}
      </div>
      {dockedHere ? null : PIP_RESIZE_DIRS.map((dir) => (
        <div
          key={dir}
          className={styles.webPipResize}
          data-dir={dir}
          data-pip-resize={dir}
          role="separator"
          aria-orientation={dir === "e" || dir === "w" ? "vertical" : "horizontal"}
          aria-label={resizeLabels[dir]}
          onPointerDown={(event) => onDragPointerDown("resize", event, dir)}
          onPointerMove={onDragPointerMove}
          onPointerUp={onDragPointerUp}
          onPointerCancel={onDragPointerCancel}
          onLostPointerCapture={onDragPointerCancel}
        />
      ))}
    </PipFrame>
  );
}
