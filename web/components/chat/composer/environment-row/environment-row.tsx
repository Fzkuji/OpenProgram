"use client";

import { useRef } from "react";

import { GoalChip } from "../../goal-chip";
import { ProjectBadge, WorkingDirChips } from "../../top-bar";
import { StatusChip } from "./status-chip";
import { SurfaceChip } from "./surface-chip";
import { useCompactEnvironmentRow } from "./use-compact-environment-row";
import styles from "./environment-row.module.css";

interface EnvironmentRowProps {
  sessionId: string | null;
  toolsEnabled: boolean;
  owningStatusId: boolean;
  onToggleAccess(): void;
}

export function EnvironmentRow({
  sessionId,
  toolsEnabled,
  owningStatusId,
  onToggleAccess,
}: EnvironmentRowProps) {
  const rowRef = useRef<HTMLDivElement | null>(null);
  useCompactEnvironmentRow(rowRef);

  return (
    <div ref={rowRef} className={styles.envChips} data-environment-row>
      <StatusChip owningId={owningStatusId} />
      <SurfaceChip
        sessionId={sessionId}
        toolsEnabled={toolsEnabled}
        onToggleAccess={onToggleAccess}
      />
      <ProjectBadge />
      <WorkingDirChips />
      <GoalChip />
      <div id="dagHudSlot" className={styles.dagHudSlot} />
    </div>
  );
}
