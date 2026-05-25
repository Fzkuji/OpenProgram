"use client";

import { useState } from "react";

import {
  relativeTime,
  shortPath,
  wsSend,
  type Worktree,
} from "./types";

/**
 * Single row in the Worktrees panel. Pure presentational — parent
 * owns the worktree list state.
 *
 * Action visibility per status:
 *   active     → Merge / Discard
 *   committing → (no actions; row is locked, status pill is the cue)
 *   errored    → error message visible, Keep / Discard offered
 *   merged     → read-only, fades after a few minutes
 *   discarded  → read-only, fades after a few minutes
 *   kept       → read-only, fades after a few minutes
 *
 * Discard prompts with a confirm() since it nukes the on-disk
 * directory. Merge is one click — failure is non-destructive (the
 * worktree returns to active with `.error` populated).
 */
export function WorktreeItem({ wt }: { wt: Worktree }) {
  // While a destructive action is in-flight we visually lock the row
  // and disable buttons so a double-click can't send two requests
  // against the same id.
  const [busy, setBusy] = useState(false);

  function merge() {
    if (busy) return;
    setBusy(true);
    wsSend({
      action: "merge_worktree",
      worktree_id: wt.id,
      strategy: wt.merge_strategy || "ff-only",
      delete_branch: false,
    });
    // The WS broadcast (worktree_status) will eventually flip the
    // row out of "active". We optimistically clear the local busy
    // flag in ~6s as a safety net for the case where the broadcast
    // is dropped — the row will still reflect the real backend
    // state from the next list_worktrees the parent issues.
    setTimeout(() => setBusy(false), 6000);
  }

  function discard() {
    if (busy) return;
    const force = wt.status === "errored";
    const ok = window.confirm(
      `Discard worktree ${wt.branch_name}? This removes ${wt.worktree_path} and deletes the branch. This cannot be undone.`,
    );
    if (!ok) return;
    setBusy(true);
    wsSend({
      action: "discard_worktree",
      worktree_id: wt.id,
      force,
      delete_branch: true,
    });
    setTimeout(() => setBusy(false), 6000);
  }

  function keep() {
    if (busy) return;
    setBusy(true);
    wsSend({ action: "keep_worktree", worktree_id: wt.id });
    setTimeout(() => setBusy(false), 6000);
  }

  const isActive = wt.status === "active";
  const isErrored = wt.status === "errored";
  const isCommitting = wt.status === "committing";

  return (
    <div
      className={
        "worktree-item worktree-status-" + wt.status +
        (busy ? " is-busy" : "")
      }
      data-worktree-id={wt.id}
      title={wt.worktree_path}
    >
      <div className="worktree-item-row">
        <span
          className={"worktree-status-pill worktree-status-pill-" + wt.status}
          title={`Status: ${wt.status}`}
        >
          {wt.status}
        </span>
        <span className="worktree-item-branch" title={wt.branch_name}>
          {wt.branch_name}
        </span>
        <span className="worktree-item-time" title={`Created ${new Date(wt.created_at * 1000).toLocaleString()}`}>
          {relativeTime(wt.created_at)}
        </span>
      </div>
      <div className="worktree-item-row worktree-item-meta">
        <span
          className="worktree-item-repo"
          title={wt.source_repo}
        >
          {shortPath(wt.source_repo)}
        </span>
        {wt.files_changed > 0 ? (
          <span className="worktree-item-files">
            {wt.files_changed} file{wt.files_changed === 1 ? "" : "s"}
          </span>
        ) : null}
      </div>
      {isErrored && wt.error ? (
        <div className="worktree-item-error" title={wt.error}>
          {wt.error}
        </div>
      ) : null}
      {(isActive || isErrored) ? (
        <div className="worktree-item-actions">
          {isActive ? (
            <button
              type="button"
              className="worktree-btn worktree-btn-merge"
              onClick={merge}
              disabled={busy}
              title={`Merge into ${shortPath(wt.source_repo)} (${wt.merge_strategy || "ff-only"})`}
            >
              Merge
            </button>
          ) : null}
          {isErrored ? (
            <button
              type="button"
              className="worktree-btn worktree-btn-keep"
              onClick={keep}
              disabled={busy}
              title="Keep the on-disk worktree, detach from OpenProgram"
            >
              Keep
            </button>
          ) : null}
          <button
            type="button"
            className="worktree-btn worktree-btn-discard"
            onClick={discard}
            disabled={busy}
            title="Remove worktree dir + delete branch"
          >
            Discard
          </button>
        </div>
      ) : null}
      {isCommitting ? (
        <div className="worktree-item-actions worktree-item-actions-hint">
          merging…
        </div>
      ) : null}
    </div>
  );
}
