/**
 * Pasted-content chip row for the composer.
 *
 * Stacked above the textarea (its own row in the wrapper's flex
 * column), one chip per still-referenced paste token in the current
 * draft. Each chip shows the paste id + line count, a tooltip with a
 * short content preview, and an × button that strips both the chip
 * and the token from the textarea in one motion.
 *
 * Chips for tokens whose backing entry has been lost (reload past the
 * persistence cap, manual store wipe) render in a "missing" state.
 * The composer refuses to submit while any chip is missing so the
 * user notices the data loss instead of silently sending a half-
 * stripped message.
 */
"use client";

import React from "react";

import type { PastedEntry } from "./paste-store";
import styles from "../composer.module.css";

interface PasteChipsProps {
  entries: PastedEntry[];
  /** Set of entry ids whose backing paste has been lost. Marked red. */
  missing: Set<number>;
  /** Remove a paste (also strips the token from the textarea). */
  onRemove: (id: number) => void;
}

export function PasteChips({ entries, missing, onRemove }: PasteChipsProps) {
  if (entries.length === 0) return null;
  return (
    <div className={styles.pasteChipRow}>
      {entries.map((p) => {
        const isMissing = missing.has(p.id);
        return (
          <span
            key={p.id}
            className={
              isMissing
                ? `${styles.pasteChip} ${styles.missing}`
                : styles.pasteChip
            }
            title={
              isMissing
                ? `Pasted #${p.id} — content lost. Remove the token, then re-paste.`
                : p.content.slice(0, 500)
                    + (p.content.length > 500 ? "…" : "")
            }
          >
            {isMissing ? "Pasted #" + p.id + " · lost" : (
              <>Pasted #{p.id} · +{p.numLines} lines</>
            )}
            <button
              type="button"
              onClick={() => onRemove(p.id)}
              aria-label={`Remove paste #${p.id}`}
              className={styles.pasteChipRemove}
            >
              ×
            </button>
          </span>
        );
      })}
    </div>
  );
}
