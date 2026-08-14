"use client";

import { Globe2 } from "lucide-react";

import { HoverTip } from "@/components/ui/tooltip";
import { useTranslation } from "@/lib/i18n";
import { surfaceRefForChat } from "@/lib/desktop-bridge";
import { useCenterTabs } from "@/lib/state/center-tabs-store";
import styles from "./composer.module.css";

export function SurfaceChip({
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
  const title = surface.title || new URL(surface.url || "about:blank").hostname || "Web";
  return (
    <HoverTip label={`${stateLabel} · right · ${title}`}>
      <button
        type="button"
        className={`status-badge ${styles.surfaceChip} ${toolsEnabled ? "" : "paused"}`}
        aria-label={`${stateLabel}: ${title}`}
        aria-pressed={toolsEnabled}
        onClick={onToggleAccess}
      >
        <Globe2 size={14} aria-hidden="true" />
        <span className={styles.surfaceChipLabel}>
          {text("Right", "右侧")} · {title}
        </span>
      </button>
    </HoverTip>
  );
}
