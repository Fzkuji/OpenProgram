"use client";

import { Clock3, Eye, Pause, Play } from "lucide-react";
import {
  displayedControlState,
  liveOperationMarker,
  operationHistory,
  requestExplicitPause,
  requestResumeAgent,
  resumeErrorFor,
  showActionsEnabled,
  toggleShowActions,
  useBrowserControlStore,
  type BrowserControlResource,
} from "@/lib/state/browser-control";
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
import styles from "./center-tabs.module.css";

function statusLabel(
  state: ReturnType<typeof displayedControlState>,
  text: (en: string, zh: string) => string,
): string {
  if (state === "yielding") return text("Yielding", "正在让出");
  if (state === "paused") return text("Paused", "已暂停");
  if (state === "stop_unconfirmed") return text("Stop unconfirmed", "停止未确认");
  if (state === "unknown") return text("Unknown", "未知");
  if (state === "idle" || state === "closed") return text("Idle", "空闲");
  return text("Active", "活动中");
}

export function BrowserControlBar({
  resource,
  compact = false,
}: {
  resource: BrowserControlResource | null;
  compact?: boolean;
}) {
  const { text } = useTranslation();
  const historyMenu = useSidebarMenu();
  useBrowserControlStore(s => s.showActions);
  useBrowserControlStore(s => s.pending);
  useBrowserControlStore(s => s.resumeError);
  useBrowserControlStore(s => s.history);
  useBrowserResourceStore(s => s.ingestClock);
  useBrowserResourceStore(s => s.connected);
  if (!resource) return null;
  const live = listedResource(resource);
  const state = displayedControlState(live);
  const pauseLabel = state === "paused"
    ? text("Resume Agent", "恢复 Agent")
    : state === "yielding"
      ? text("Yielding", "正在让出")
      : text("Pause Agent and take over", "暂停 Agent 并接管");
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
  const connected = browserConnectionOpen();
  const resumeDisabled = !connected || state !== "paused";
  const pauseDisabled = state === "yielding" || state === "unknown" || state === "stop_unconfirmed" || !connected;
  const showTakeover = state !== "idle" && state !== "closed";
  const nativeHistory = typeof window !== "undefined" && !!window.openprogramDesktop?.contextMenu;
  return (
    <div className={styles.browserControl} data-compact={compact ? "true" : "false"}>
      <span className={styles.browserControlStatus}>{statusLabel(state, text)}</span>
      {resumeError && <span className={styles.browserControlStatus}>{resumeError}</span>}
      <button
        type="button"
        className={styles.webToolbarBtn}
        aria-pressed={showActionsEnabled()}
        aria-label={showLabel}
        title={showLabel}
        onClick={() => { toggleShowActions(); }}
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
          disabled={state === "paused" ? resumeDisabled : pauseDisabled}
          title={pauseLabel}
          aria-label={pauseLabel}
          onClick={() => {
            void (state === "paused" ? requestResumeAgent(live) : requestExplicitPause(live));
          }}
        >
          {state === "paused"
            ? <Play size={14} aria-hidden="true" />
            : <Pause size={14} aria-hidden="true" />}
        </button>
      ) : null}
    </div>
  );
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
