"use client";

/**
 * The transcript's marker for a branch that runs, or ran, in its own git
 * worktree: one line at the top of the branch, in the same strip as the
 * sub-agent branch banner. While the worktree is open it says where the
 * branch works and how many files it changed; after a merge, keep or
 * discard it says which. Merging and discarding live on the input box's
 * worktree chip; this line only marks the branch.
 */
import { useSessionStore } from "@/lib/session-store";
import { useTranslation } from "@/lib/i18n";
import { useSessionWorktrees } from "@/lib/worktrees/session-worktrees";
import { branchWorktree, markerText } from "@/lib/worktrees/worktree-model";

export function WorktreeBranchMarker() {
  const { text } = useTranslation();
  const sessionId = useSessionStore((s) => s.currentSessionId);
  const branchId = useSessionStore((s) => (sessionId ? s.composerSettingsBySession[sessionId]?.active_branch_id ?? null : null));
  const { rows } = useSessionWorktrees(sessionId);
  const row = sessionId ? branchWorktree(rows, branchId) : null;
  if (!row) return null;
  return (
    <div className="agent-branch-banner" data-worktree-marker={row.status}>
      <span className="agent-branch-banner-label">{markerText(row, text)}</span>
    </div>
  );
}
