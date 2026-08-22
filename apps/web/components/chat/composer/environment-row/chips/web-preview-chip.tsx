"use client";

import { PictureInPicture2 } from "lucide-react";

import { HoverTip } from "@/components/ui/tooltip";
import { useTranslation } from "@/lib/i18n";
import { useCenterTabs } from "@/lib/state/center-tabs-store";
import {
  pipCoversCenter,
  useWebTabPip,
} from "@/lib/state/web-tab-pip-store";
import styles from "../environment-row.module.css";

/** Re-open the agent preview after the user closed the floating pane.
 *  Only when a background WebTab still exists and is not already the
 *  active/split page. activate/preview still require that pane visible. */
export function WebPreviewChip() {
  const { text } = useTranslation();
  const tabId = useWebTabPip((s) => s.tabId);
  const backgroundTabId = useWebTabPip((s) => s.backgroundTabId);
  const backgroundOwnerTabId = useWebTabPip((s) => s.backgroundOwnerTabId);
  const show = useWebTabPip((s) => s.show);
  const tabs = useCenterTabs((s) => s.tabs);
  const activeId = useCenterTabs((s) => s.activeId);
  const groups = useCenterTabs((s) => s.groups);
  const restoreId = !tabId && backgroundTabId && backgroundOwnerTabId
    && activeId === backgroundOwnerTabId
    && pipCoversCenter(backgroundTabId, backgroundOwnerTabId, { tabs, activeId, groups })
    ? backgroundTabId
    : null;
  if (!restoreId || !backgroundOwnerTabId) return null;

  const label = text("Show page preview", "显示网页预览");
  return (
    <HoverTip label={label}>
      <button
        type="button"
        className={`status-badge ${styles.surfaceChip}`}
        aria-label={label}
        onClick={() => show(restoreId, backgroundOwnerTabId)}
      >
        <PictureInPicture2 size={14} aria-hidden="true" />
        <span className={styles.surfaceChipLabel}>{label}</span>
      </button>
    </HoverTip>
  );
}
