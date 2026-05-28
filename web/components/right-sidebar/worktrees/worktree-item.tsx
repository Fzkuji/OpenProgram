"use client";

import { useState } from "react";

import { useTranslation } from "@/lib/i18n";

import {
  relativeTime,
  shortPath,
  wsSend,
  type Worktree,
} from "./types";

const STATUS_LABELS: Record<string, { en: string; zh: string }> = {
  active: { en: "active", zh: "活动中" },
  committing: { en: "committing", zh: "提交中" },
  merged: { en: "merged", zh: "已合并" },
  discarded: { en: "discarded", zh: "已丢弃" },
  kept: { en: "kept", zh: "已保留" },
  errored: { en: "errored", zh: "出错" },
};

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
  const { t, locale } = useTranslation();
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
    const ok = window.confirm(locale === "zh"
      ? `丢弃 worktree ${wt.branch_name}？这会删除 ${wt.worktree_path} 并删除分支。此操作无法撤销。`
      : `Discard worktree ${wt.branch_name}? This removes ${wt.worktree_path} and deletes the branch. This cannot be undone.`);
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
  const statusLabel = STATUS_LABELS[wt.status]?.[locale] || wt.status;

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
          title={`${t("right.status")}: ${statusLabel}`}
        >
          {statusLabel}
        </span>
        <span className="worktree-item-branch" title={wt.branch_name}>
          {wt.branch_name}
        </span>
        <span className="worktree-item-time" title={`${t("right.created")} ${new Date(wt.created_at * 1000).toLocaleString()}`}>
          {relativeTime(wt.created_at, locale)}
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
            {locale === "zh"
              ? `${wt.files_changed} ${t("right.files")}`
              : `${wt.files_changed} ${wt.files_changed === 1 ? t("right.file") : t("right.files")}`}
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
              title={locale === "zh"
                ? `合并到 ${shortPath(wt.source_repo)}（${wt.merge_strategy || "ff-only"}）`
                : `Merge into ${shortPath(wt.source_repo)} (${wt.merge_strategy || "ff-only"})`}
            >
              {t("right.merge")}
            </button>
          ) : null}
          {isErrored ? (
            <button
              type="button"
              className="worktree-btn worktree-btn-keep"
              onClick={keep}
              disabled={busy}
              title={t("right.keep_worktree_title")}
            >
              {t("right.keep")}
            </button>
          ) : null}
          <button
            type="button"
            className="worktree-btn worktree-btn-discard"
            onClick={discard}
            disabled={busy}
            title={t("right.discard_worktree_title")}
          >
            {t("right.discard")}
          </button>
        </div>
      ) : null}
      {isCommitting ? (
        <div className="worktree-item-actions worktree-item-actions-hint">
          {t("right.merging")}
        </div>
      ) : null}
    </div>
  );
}
