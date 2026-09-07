"use client";

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
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { MENU_PANEL } from "@/components/chat/top-bar/menu-styles";
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
  const resumeError = resumeErrorFor(resource.resourceId);
  const connected = browserConnectionOpen();
  const resumeDisabled = !connected || state !== "paused";
  const pauseDisabled = state === "yielding" || state === "unknown" || state === "stop_unconfirmed" || !connected;
  return (
    <div className={styles.browserControl} data-compact={compact ? "true" : "false"}>
      <span className={styles.browserControlStatus}>{statusLabel(state, text)}</span>
      {resumeError && <span className={styles.browserControlStatus}>{resumeError}</span>}
      <button
        type="button"
        className={styles.webToolbarBtn}
        aria-pressed={showActionsEnabled()}
        title={showLabel}
        onClick={() => { toggleShowActions(); }}
      >
        {showLabel}
      </button>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button type="button" className={styles.webToolbarBtn} title={historyLabel} aria-label={historyLabel}>
            {historyLabel}
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent className={MENU_PANEL} data-native-view-occluder="true">
          {history.length === 0
            ? <DropdownMenuItem disabled>{text("No operations yet.", "尚无操作。")}</DropdownMenuItem>
            : history.map(item => (
              <DropdownMenuItem key={item.id} disabled>
                {item.action} · {item.phase}{item.error ? ` · ${item.error}` : ""}
              </DropdownMenuItem>
            ))}
        </DropdownMenuContent>
      </DropdownMenu>
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
        {pauseLabel}
      </button>
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
