"use client";

import { useRef, type ReactNode } from "react";

import { GoalChip } from "../../goal-chip";
import { ProjectBadge, WorkingDirChips } from "../../top-bar";
import { ConnectionStatusChip } from "./chips/connection-status-chip";
import { WebPreviewChip } from "./chips/web-preview-chip";
import { WebSurfaceChip } from "./chips/web-surface-chip";
import { useCompactEnvironmentRow } from "./use-compact-environment-row";
import styles from "./environment-row.module.css";

interface EnvironmentRowProps {
  sessionId: string | null;
  toolsEnabled: boolean;
  owningStatusId: boolean;
  onToggleAccess(): void;
  trailingControls?: ReactNode;
}

export function EnvironmentRow({
  sessionId,
  toolsEnabled,
  owningStatusId,
  onToggleAccess,
  trailingControls,
}: EnvironmentRowProps) {
  const rowRef = useRef<HTMLDivElement | null>(null);
  useCompactEnvironmentRow(rowRef);

  return (
    <div ref={rowRef} className={styles.envChips} data-environment-row>
      <ConnectionStatusChip owningId={owningStatusId} />
      <WebSurfaceChip
        sessionId={sessionId}
        toolsEnabled={toolsEnabled}
        onToggleAccess={onToggleAccess}
      />
      <WebPreviewChip />
      <ProjectBadge />
      <WorkingDirChips />
      <GoalChip />
      {trailingControls ? (
        <div className={styles.trailingControls}>{trailingControls}</div>
      ) : null}
    </div>
  );
}
