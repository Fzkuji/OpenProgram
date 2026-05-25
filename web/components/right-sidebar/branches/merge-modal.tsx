"use client";

/**
 * Branch-merge modal — picks a merge mode (equal merge vs attach-into-★)
 * and collects an optional reconciliation instruction. Triggered from
 * the BranchesPanel toolbar once the user has multi-selected ≥2 branches.
 *
 * Owns no state — every value + setter is passed down. Extracted from
 * BranchesPanel so the panel file stays focused on the list rendering.
 */
import type React from "react";

interface MergeModalProps {
  /** Selected branch head ids (≥2 when this modal is open). */
  selected: string[];
  /** Optional ★ base — when non-null the merge happens IN-PLACE on
   *  that branch; otherwise it's an equal merge with a fresh tip. */
  baseHead: string | null;
  /** Free-text instruction passed to the merge agent. */
  mergeInstruction: string;
  setBaseHead: (id: string | null) => void;
  setMergeInstruction: (s: string) => void;
  onCancel: () => void;
  onMerge: () => void;
}

export function MergeModal({
  selected,
  baseHead,
  mergeInstruction,
  setBaseHead,
  setMergeInstruction,
  onCancel,
  onMerge,
}: MergeModalProps) {
  return (
    <div className="branches-merge-modal-backdrop" onClick={onCancel}>
      <div
        className="branches-merge-modal"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="branches-merge-modal-title">
          Merge {selected.length} branches
        </div>
        <div className="branches-merge-mode">
          <label className="branches-merge-mode-row">
            <input
              type="radio"
              name="merge-mode"
              checked={!baseHead}
              onChange={() => setBaseHead(null)}
            />
            <span>
              <strong>Equal merge</strong> — write a new turn whose
              parents are all selected branches. Reply lands as a
              fresh branch tip.
            </span>
          </label>
          <label className="branches-merge-mode-row">
            <input
              type="radio"
              name="merge-mode"
              checked={!!baseHead}
              onChange={() => {
                // Default base to first selected if nothing is
                // ⌘-clicked yet — the radio is only useful when
                // a base exists.
                if (!baseHead && selected.length > 0) {
                  setBaseHead(selected[0]);
                }
              }}
            />
            <span>
              <strong>Attach into ★ base</strong> — reply continues
              the ★ branch, the other selections feed in as
              context. ⌘-click a row to pick the base.
            </span>
          </label>
        </div>
        <textarea
          className="branches-merge-modal-input"
          placeholder="Optional instruction for the merge agent (how to reconcile)"
          value={mergeInstruction}
          onChange={(e) => setMergeInstruction(e.target.value)}
          autoFocus
          rows={4}
          onKeyDown={(e: React.KeyboardEvent<HTMLTextAreaElement>) => {
            if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
              e.preventDefault();
              onMerge();
            }
          }}
        />
        <div className="branches-merge-modal-hint">
          ⌘/Ctrl + Enter to merge
        </div>
        <div className="branches-merge-modal-actions">
          <button
            type="button"
            className="branches-merge-cancel"
            onClick={onCancel}
          >
            Cancel
          </button>
          <button
            type="button"
            className="branches-merge-go"
            onClick={onMerge}
          >
            Merge
          </button>
        </div>
      </div>
    </div>
  );
}
