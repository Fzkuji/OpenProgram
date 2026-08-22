"use client";

import { ChromeIcon } from "@/components/animated-icons";
import { HoverTip } from "@/components/ui/tooltip";
import { surfaceRefForChat } from "@/lib/desktop-bridge";
import { useTranslation } from "@/lib/i18n";
import { useCenterTabs } from "@/lib/state/center-tabs-store";
import styles from "../environment-row.module.css";

export function WebSurfaceChip({
  sessionId,
  toolsEnabled,
  onToggleAccess,
}: {
  sessionId: string | null;
  toolsEnabled: boolean;
  onToggleAccess(): void;
}) {
  const { text } = useTranslation();
  useCenterTabs((state) => state.activeId);
  useCenterTabs((state) => state.splitWebTabId);
  useCenterTabs((state) => state.tabs);
  useCenterTabs((state) => state.groups);
  const surface = surfaceRefForChat(sessionId, toolsEnabled);
  if (!surface) return null;

  const stateLabel = toolsEnabled
    ? text("Agent can access", "Agent 可访问")
    : text("Web control disabled", "网页控制未启用");
  const title =
    surface.title || new URL(surface.url || "about:blank").hostname || "Web";
  const regionLabel = surface.region === "left"
    ? text("Left", "左侧")
    : surface.region === "right"
      ? text("Right", "右侧")
      : text("Center", "中间");
  return (
    <HoverTip label={`${stateLabel} · ${regionLabel} · ${title}`}>
      <button
        type="button"
        className={`status-badge ${styles.surfaceChip} ${toolsEnabled ? "" : "paused"}`}
        aria-label={`${stateLabel}: ${regionLabel} · ${title}`}
        aria-pressed={toolsEnabled}
        onClick={onToggleAccess}
      >
        <ChromeIcon size={14} aria-hidden="true" />
        <span className={styles.surfaceChipLabel}>
          {regionLabel} · {title}
        </span>
      </button>
    </HoverTip>
  );
}
