"use client";

import { CircleHelp, Clock3, Eye, Pause, Play, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  browserTakeoverKind,
  displayedControlState,
  operationHistory,
  resumeErrorFor,
  revealPendingApproval,
  showActionsEnabled,
  toggleShowActions,
  useBrowserControlStore,
  type BrowserControlResource,
} from "@/lib/state/browser-control";
import {
  clampFloatPosition,
  controlSurfaceVisible,
  cueTravelDurationMs,
  defaultFloatPosition,
  FLOAT_CONTROL_SIZE,
  FLOAT_DRAG_THRESHOLD,
  lerpCuePoint,
  nextCueTravel,
  prefersCueReducedMotion,
  type CuePoint,
} from "@/lib/state/browser-action-cue";
import { browserConnectionOpen, useBrowserResourceStore } from "@/lib/state/session-resources";
import { useTranslation } from "@/lib/i18n";
import { MENU_PANEL } from "@/components/chat/top-bar/menu-styles";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useSidebarMenu, type SidebarMenuItem } from "@/components/sidebar/use-sidebar-menu";
import { CursorClickIcon } from "@/components/animated-icons";
import styles from "./center-tabs.module.css";

function statusLabel(
  state: ReturnType<typeof displayedControlState>,
  text: (en: string, zh: string) => string,
  connected: boolean,
): string {
  if (state === "yielding") return text("Pausing…", "正在暂停…");
  if (state === "waiting") return text("Needs your confirmation", "需要你确认");
  if (state === "paused") return text("Paused", "已暂停");
  if (state === "stop_unconfirmed") return text("Could not pause. Try again", "暂停失败，请重试");
  if (state === "unknown") {
    return connected
      ? text("Could not confirm status", "无法确认状态")
      : text("Connection lost", "连接已断开");
  }
  if (state === "idle" || state === "closed") return text("Ready to use", "可直接操作");
  return text("Active", "活动中");
}

function listedResource(resource: BrowserControlResource): BrowserControlResource {
  const rows = useBrowserResourceStore.getState().rows;
  const match = Object.values(rows).find(row => row.resourceId === resource.resourceId || row.id === resource.id);
  if (!match) return resource;
  return {
    ...resource,
    generation: match.generation || resource.generation,
    controlState: match.controlState,
  };
}

export function BrowserControlBar({
  resource,
  compact = false,
  surface = "float",
  viewport,
}: {
  resource: BrowserControlResource | null;
  compact?: boolean;
  surface?: "float" | "toolbar";
  viewport?: { width: number; height: number };
}) {
  const { text } = useTranslation();
  const historyMenu = useSidebarMenu();
  useBrowserControlStore(s => s.showActions);
  useBrowserControlStore(s => s.pending);
  useBrowserControlStore(s => s.resumeError);
  useBrowserControlStore(s => s.history);
  useBrowserResourceStore(s => s.ingestClock);
  useBrowserResourceStore(s => s.connected);
  const [collapsed, setCollapsed] = useState(true);
  const [noticeOpen, setNoticeOpen] = useState(true);
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null);
  const drag = useRef<{
    id: number;
    startX: number;
    startY: number;
    origL: number;
    origT: number;
    moved: boolean;
  } | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);

  if (!resource) return null;
  const live = listedResource(resource);
  const state = displayedControlState(live);
  if (!controlSurfaceVisible(state) && surface === "float") return null;
  const connected = browserConnectionOpen();
  const shownStatus = statusLabel(state, text, connected);
  const takeoverKind = browserTakeoverKind(state);
  const pauseLabel = takeoverKind === "reveal"
    ? text("Review request", "查看请求")
    : takeoverKind === "resume"
    ? text("Continue Agent", "让 Agent 继续")
    : takeoverKind === "yielding"
      ? text("Pausing…", "正在暂停…")
      : takeoverKind === "retry"
        ? text("Retry pause", "重试暂停")
        : text("I will operate", "我来操作");
  const showLabel = text("Show actions", "显示操作");
  const historyLabel = text("Operation history", "操作历史");
  const history = operationHistory(resource.resourceId);
  const historyItems: SidebarMenuItem[] = history.length === 0
    ? [{ id: "empty", label: text("No operations yet.", "尚无操作。"), disabled: true }]
    : history.map(item => ({
      id: item.id,
      label: `${item.action} · ${item.phase}${item.error ? ` · ${item.error}` : ""}`,
      disabled: true,
    }));
  const resumeError = resumeErrorFor(resource.resourceId);
  const resumeDisabled = !connected || (state !== "paused" && state !== "waiting");
  const pauseDisabled = state === "yielding" || state === "unknown" || !connected;
  const showTakeover = takeoverKind === "reveal";
  const nativeHistory = typeof window !== "undefined" && !!window.openprogramDesktop?.contextMenu;
  const noticeText = resumeError
    || (state === "stop_unconfirmed" ? shownStatus : null)
    || (state === "unknown" ? shownStatus : null);
  const size = collapsed
    ? { width: FLOAT_CONTROL_SIZE, height: FLOAT_CONTROL_SIZE }
    : { width: 280, height: 40 };
  const vp = viewport || { width: 640, height: 400 };
  const placed = pos
    ? clampFloatPosition(pos.left, pos.top, size, vp)
    : defaultFloatPosition(size, vp);

  const pointerFromAction = (event: { target: EventTarget | null; currentTarget: EventTarget }) => {
    const target = event.target as { closest?: (selector: string) => unknown } | null;
    if (!target || target === event.currentTarget) return false;
    return typeof target.closest === "function" && !!target.closest("button");
  };
  const onPointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    if (pointerFromAction(event)) return;
    drag.current = {
      id: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      origL: placed.left,
      origT: placed.top,
      moved: false,
    };
    try { event.currentTarget.setPointerCapture(event.pointerId); } catch { /* jsdom / already captured */ }
  };
  const onPointerMove = (event: React.PointerEvent<HTMLDivElement>) => {
    const current = drag.current;
    if (!current || current.id !== event.pointerId) return;
    const dx = event.clientX - current.startX;
    const dy = event.clientY - current.startY;
    if (!current.moved && Math.hypot(dx, dy) < FLOAT_DRAG_THRESHOLD) return;
    current.moved = true;
    setPos(clampFloatPosition(current.origL + dx, current.origT + dy, size, vp));
  };
  const endPointer = (event: React.PointerEvent<HTMLDivElement>, toggle: boolean) => {
    const current = drag.current;
    if (!current || current.id !== event.pointerId) return;
    drag.current = null;
    try { event.currentTarget.releasePointerCapture(event.pointerId); } catch { /* already released */ }
    if (toggle && !current.moved && !pointerFromAction(event)) setCollapsed((value) => !value);
  };
  const onPointerUp = (event: React.PointerEvent<HTMLDivElement>) => {
    endPointer(event, true);
  };
  const onPointerCancel = (event: React.PointerEvent<HTMLDivElement>) => {
    endPointer(event, false);
  };

  const onKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.target !== event.currentTarget) return;
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      setCollapsed((value) => !value);
      return;
    }
    const step = event.shiftKey ? 24 : 8;
    let next = placed;
    if (event.key === "ArrowLeft") next = { left: placed.left - step, top: placed.top };
    else if (event.key === "ArrowRight") next = { left: placed.left + step, top: placed.top };
    else if (event.key === "ArrowUp") next = { left: placed.left, top: placed.top - step };
    else if (event.key === "ArrowDown") next = { left: placed.left, top: placed.top + step };
    else return;
    event.preventDefault();
    setPos(clampFloatPosition(next.left, next.top, size, vp));
  };

  const actions = (
    <>
      <span className={styles.browserControlStatus} title={shownStatus}>{shownStatus}</span>
      {noticeOpen && noticeText ? (
        <span className={styles.browserControlNotice} role="status">
          {noticeText}
          <button
            type="button"
            className={styles.webToolbarBtn}
            aria-label={text("Dismiss", "关闭")}
            onClick={(event) => {
              event.stopPropagation();
              setNoticeOpen(false);
            }}
          >
            <X size={12} aria-hidden="true" />
          </button>
        </span>
      ) : null}
      <button
        type="button"
        className={styles.webToolbarBtn}
        aria-pressed={showActionsEnabled()}
        aria-label={showLabel}
        title={showLabel}
        onClick={(event) => {
          event.stopPropagation();
          toggleShowActions();
        }}
      >
        <Eye size={14} aria-hidden="true" />
      </button>
      {nativeHistory ? (
        <button
          type="button"
          className={styles.webToolbarBtn}
          title={historyLabel}
          aria-label={historyLabel}
          aria-haspopup="menu"
          aria-expanded={historyMenu.open}
          onClick={(event) => {
            event.stopPropagation();
            if (historyMenu.open) historyMenu.close();
            else historyMenu.show(event, historyItems);
          }}
        >
          <Clock3 size={14} aria-hidden="true" />
        </button>
      ) : (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button type="button" className={styles.webToolbarBtn} title={historyLabel} aria-label={historyLabel}>
              <Clock3 size={14} aria-hidden="true" />
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent className={MENU_PANEL}>
            {history.length === 0
              ? <DropdownMenuItem disabled>{text("No operations yet.", "尚无操作。")}</DropdownMenuItem>
              : history.map(item => (
                <DropdownMenuItem key={item.id} disabled>
                  {item.action} · {item.phase}{item.error ? ` · ${item.error}` : ""}
                </DropdownMenuItem>
              ))}
          </DropdownMenuContent>
        </DropdownMenu>
      )}
      {showTakeover ? (
        <button
          type="button"
          className={styles.webToolbarBtn}
          disabled={resumeDisabled}
          title={pauseLabel}
          aria-label={pauseLabel}
          onClick={(event) => {
            event.stopPropagation();
            revealPendingApproval(live);
          }}
        >
          <CircleHelp size={14} aria-hidden="true" />
        </button>
      ) : null}
    </>
  );

  if (surface !== "float") {
    return (
      <div className={styles.browserControl} data-compact={compact ? "true" : "false"}>
        {actions}
      </div>
    );
  }

  return (
    <div
      ref={rootRef}
      className={styles.browserFloat}
      data-browser-control="float"
      data-collapsed={collapsed ? "true" : "false"}
      data-compact={compact ? "true" : "false"}
      role={collapsed ? "button" : "group"}
      tabIndex={0}
      aria-label={collapsed
        ? text("Small draggable Agent button. Click to expand. Drag to move.", "可拖动的 Agent 按钮。点击展开。拖动移动。")
        : text("Agent controls. Click to fold. Drag to move.", "Agent 控制。点击收起。拖动移动。")}
      style={{ left: placed.left, top: placed.top }}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerCancel}
      onKeyDown={onKeyDown}
    >
      {collapsed ? (
        <>
          <CursorClickIcon size={20} play="hover" />
          {noticeOpen && noticeText ? (
            <span className={styles.browserControlNotice} role="status" data-resume-error={resumeError ? "true" : undefined}>
              {noticeText}
            </span>
          ) : null}
        </>
      ) : actions}
    </div>
  );
}

export function ActionCueTravel({
  point,
  cancelKey,
  resourceKey,
  operationKey,
  reducedMotion,
}: {
  point: CuePoint | null;
  cancelKey: string;
  resourceKey: string;
  operationKey?: string;
  reducedMotion?: boolean;
}) {
  const [pos, setPos] = useState<CuePoint | null>(null);
  const [play, setPlay] = useState<"once" | "never">("never");
  const [playSeq, setPlaySeq] = useState(0);
  const lastRendered = useRef<CuePoint | null>(null);
  const lastResource = useRef(resourceKey);
  const lastCancel = useRef(cancelKey);
  const anim = useRef(0);

  useEffect(() => {
    const reduce = reducedMotion ?? prefersCueReducedMotion();
    const stopAnim = () => {
      if (anim.current) {
        cancelAnimationFrame(anim.current);
        anim.current = 0;
      }
    };
    if (lastResource.current !== resourceKey) {
      lastResource.current = resourceKey;
      lastRendered.current = null;
      stopAnim();
    }
    const cancelChanged = lastCancel.current !== cancelKey;
    lastCancel.current = cancelKey;
    if (cancelChanged) stopAnim();
    if (!point) {
      stopAnim();
      setPos(null);
      setPlay("never");
      return () => stopAnim();
    }
    const from = lastRendered.current;
    const travel = nextCueTravel(from, point, reduce);
    if (!travel.animateMove || !travel.from) {
      lastRendered.current = point;
      setPos(point);
      setPlay(reduce ? "never" : "once");
      setPlaySeq((n) => n + 1);
      return () => stopAnim();
    }
    const duration = cueTravelDurationMs(travel.from, travel.to);
    const started = Date.now();
    setPlay("never");
    lastRendered.current = travel.from;
    setPos(travel.from);
    const tick = () => {
      const t = Math.min(1, (Date.now() - started) / duration);
      const next = lerpCuePoint(travel.from!, travel.to, t);
      lastRendered.current = next;
      setPos(next);
      if (t < 1) {
        anim.current = requestAnimationFrame(tick);
        return;
      }
      anim.current = 0;
      lastRendered.current = travel.to;
      setPos(travel.to);
      setPlay(reduce ? "never" : "once");
      setPlaySeq((n) => n + 1);
    };
    anim.current = requestAnimationFrame(tick);
    return () => stopAnim();
  }, [point?.x, point?.y, cancelKey, resourceKey, operationKey, reducedMotion]);

  if (!pos) return null;
  return (
    <div
      className={styles.browserActionCue}
      data-browser-action-cue="true"
      data-cue-x={String(pos.x)}
      data-cue-y={String(pos.y)}
      data-cue-play={play}
      data-cue-seq={String(playSeq)}
      style={{ left: pos.x, top: pos.y }}
      aria-hidden="true"
    >
      <CursorClickIcon size={28} play={play} playSeq={playSeq} />
    </div>
  );
}

export function BrowserPageCue({
  x,
  y,
  play,
}: {
  x: number;
  y: number;
  play: "once" | "never";
}) {
  return (
    <ActionCueTravel
      point={{ x, y }}
      cancelKey={`${x}:${y}:${play}`}
      resourceKey="page"
      operationKey={`${x}:${y}:${play}`}
      reducedMotion={play === "never"}
    />
  );
}

export function useFloatViewport(el: HTMLElement | null): { width: number; height: number } {
  const [size, setSize] = useState({ width: 640, height: 400 });
  const update = useCallback(() => {
    if (!el) return;
    const box = el.getBoundingClientRect();
    setSize({ width: box.width, height: box.height });
  }, [el]);
  useEffect(() => {
    update();
    if (!el) return;
    const observer = new ResizeObserver(update);
    observer.observe(el);
    return () => observer.disconnect();
  }, [el, update]);
  return size;
}
