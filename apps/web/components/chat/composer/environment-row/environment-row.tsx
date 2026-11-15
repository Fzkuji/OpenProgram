"use client";

import { useRef } from "react";

import { GoalChip } from "../../badges/goal-chip";
import { ProjectBadge, WorkingDirChips } from "../../top-bar";
import { ChannelChip } from "./chips/channel-chip";
import { EnvironmentChip } from "./chips/environment-chip";
import { WebSurfaceChip } from "./chips/web-surface-chip";
import { WorktreeChip } from "./chips/worktree-chip";
import { useCompactEnvironmentRow } from "./use-compact-environment-row";
import styles from "./environment-row.module.css";

interface EnvironmentRowProps {
  sessionId: string | null;
}

export function EnvironmentRow({ sessionId }: EnvironmentRowProps) {
  const rowRef = useRef<HTMLDivElement | null>(null);
  useCompactEnvironmentRow(rowRef);

  return (
    <div ref={rowRef} className={styles.envChips} data-environment-row>
      <EnvironmentChip />
      {/* Checkout or the branch's own worktree (Codex's Local / Worktree). */}
      <WorktreeChip />
      <ChannelChip />
      <ProjectBadge />
      <WorkingDirChips />
      <GoalChip />
      {/* Web tab access; restores the floating preview: right-aligned (CSS). */}
      <WebSurfaceChip sessionId={sessionId} />
    </div>
  );
}
