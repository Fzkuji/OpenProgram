"use client";

import { memo, useRef } from "react";

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

// Memoized: the composer redraws on every keystroke and settings frame;
// the chips read their own state.
export const EnvironmentRow = memo(function EnvironmentRow({ sessionId }: EnvironmentRowProps) {
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
});
