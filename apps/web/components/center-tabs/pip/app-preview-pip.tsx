"use client";

import { useLayoutEffect, useRef, useState } from "react";
import { X } from "lucide-react";

import { useTranslation } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { HoverTip, TipBody } from "@/components/ui/tooltip";
import { useCenterTabs } from "@/lib/tabs/center-tabs-store";
import {
  appPreviewTarget,
  hideResourcePreview,
  NATIVE_WINDOW_TARGET,
  TERMINAL_TARGET,
  useBrowserResourceStore,
  viewedBranchFor,
} from "@/lib/chat/resources/session-resources";
import { nativeWindowFrameUrl, useNativeWindows } from "@/lib/browser/control/native-window-store";
import { useTerminalResources } from "@/lib/desktop/terminal-resources";
import { followPipBox, pipChatRect, useWebTabPip, type WebTabPipRect } from "@/lib/browser/web-tab/web-tab-pip-store";
import { TerminalMirror } from "@/components/session-resources/terminal-resources";

import styles from "../browser/browser.module.css";
import { boxKey, containerBox, followUntilSettled, PIP_FRAME, PIP_ICON, PIP_ICON_BUTTON, PIP_STAGE } from "./web-tab-pip";

/** The chat preview of a desktop app window or terminal the Agent operates.
 *  Same frame and place as the web Page preview. An app window shows the
 *  latest screenshot the Agent took of it, refreshed after every step; a
 *  terminal shows its live output, read-only. */
export function AppPreviewPip() {
  const { text } = useTranslation();
  const tabs = useCenterTabs(s => s.tabs);
  const activeId = useCenterTabs(s => s.activeId);
  const rect = useWebTabPip(s => s.rect);
  const windows = useNativeWindows(s => s.windows);
  const terminals = useTerminalResources(s => s.rows);
  useBrowserResourceStore(s => s.followEpoch);
  useBrowserResourceStore(s => s.preferences);
  useBrowserResourceStore(s => s.viewedBranch);
  const rootRef = useRef<HTMLDivElement>(null);
  const [box, setBox] = useState<WebTabPipRect | null>(null);
  const [failedFrame, setFailedFrame] = useState<string | null>(null);

  const owner = tabs.find(tab => tab.id === activeId && tab.kind === "session");
  const sessionId = owner?.kind === "session" ? owner.sessionId || null : null;
  const branchId = sessionId ? viewedBranchFor(sessionId) : null;
  const target = sessionId ? appPreviewTarget(sessionId, branchId) : null;
  const operated = sessionId ? windows[sessionId] : undefined;
  const appWindow = target?.startsWith(NATIVE_WINDOW_TARGET)
    && operated && `${NATIVE_WINDOW_TARGET}${operated.windowId}` === target ? operated : null;
  const terminal = target?.startsWith(TERMINAL_TARGET)
    ? terminals[target.slice(TERMINAL_TARGET.length)] : undefined;
  const shownTerminal = terminal && sessionId && terminal.session_ids.includes(sessionId)
    && terminal.status === "running" ? terminal : null;
  const shown = !!(appWindow || shownTerminal);

  useLayoutEffect(() => {
    const el = rootRef.current;
    if (!el || !shown) return;
    const follow = () => {
      const next = containerBox(el);
      setBox(prev => prev && prev.x === next.x && prev.y === next.y
        && prev.width === next.width && prev.height === next.height ? prev : next);
      followPipBox(next, true);
    };
    follow();
    const parent = el.offsetParent;
    if (!(parent instanceof HTMLElement)) return;
    // Committed before paint, so the preview stays still on screen in the
    // frame its pane moved (web-tab-pip.tsx does the same).
    const ro = new ResizeObserver(() => followUntilSettled(() => boxKey(containerBox(el)), follow));
    ro.observe(parent);
    const pane = parent.querySelector(".center-pane-chat");
    if (pane) ro.observe(pane);
    return () => ro.disconnect();
  }, [shown, rect, activeId]);

  if (!sessionId || !owner || !shown) return null;
  const placed = box ? pipChatRect(rect, false, box) : null;
  const title = appWindow ? appWindow.appName
    : shownTerminal?.preset === "claude" ? "Claude Code" : text("Terminal", "终端");
  const kindLabel = appWindow ? text("Desktop app", "桌面应用") : text("Terminal", "终端");
  const frame = appWindow ? nativeWindowFrameUrl(appWindow) : null;
  const hideLabel = text("Close preview", "关闭小窗");
  const hideHint = text("Hides the preview; the agent keeps working", "只隐藏小窗；Agent 继续工作");

  return (
    <div
      ref={rootRef}
      className={cn(styles.webPip, PIP_FRAME)}
      data-pip="true"
      data-pip-host="chat"
      data-pip-kind={appWindow ? "native-window" : "terminal"}
      data-pip-owner-tab-id={owner.id}
      role="complementary"
      aria-label={title}
      style={placed ? {
        left: placed.x, top: placed.y, width: placed.width, height: placed.height,
        right: "auto", bottom: "auto",
      } : undefined}
    >
      <div className={styles.webPipChrome}>
        <span className={cn(styles.webPipTitle, "truncate text-fs-xs font-medium text-text-primary")}>{title}</span>
        {title !== kindLabel && (
          <small className={cn(styles.webPipMode, "truncate text-fs-2xs text-text-muted")}>{kindLabel}</small>
        )}
        <div className={styles.webPipActions}>
          <HoverTip label={<TipBody title={hideLabel} detail={hideHint} />}>
            <button
              type="button"
              className={cn(PIP_ICON_BUTTON, "-mr-1")}
              onClick={() => hideResourcePreview(sessionId, branchId)}
              aria-label={hideLabel}
            >
              <X size={14} className={PIP_ICON} aria-hidden="true" />
            </button>
          </HoverTip>
        </div>
      </div>
      <div className={cn("overflow-hidden relative flex-1 min-h-0 flex flex-col", PIP_STAGE)}>
        <div className={styles.webPipBody}>
          {shownTerminal ? (
            <TerminalMirror key={`${shownTerminal.terminal_id}:${shownTerminal.generation}`} resource={shownTerminal} />
          ) : frame && failedFrame !== frame ? (
            <img className={styles.webPipShot} style={{ display: "block" }} src={frame} alt=""
              onError={() => setFailedFrame(frame)} />
          ) : (
            <div className={cn(styles.webPipFallback, "text-fs-2xs text-text-muted")}>
              {text("Waiting for the next screenshot", "等待下一张画面")}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
