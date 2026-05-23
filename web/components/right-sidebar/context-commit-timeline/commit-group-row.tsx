/** Multi-attempt group row. One commit ⇒ behaves exactly like a
 *  CommitRow. Multiple commits (parallel branches sharing the same
 *  DAG fork point) ⇒ row header gets a `‹ n/N ›` switcher; the
 *  selected attempt drives the expanded popout. Default selection
 *  is the newest attempt. */
import { useState } from "react";
import { CommitRow } from "./commit-row";
import type { CommitDetail, CommitMeta } from "./types";

export function CommitGroupRow(props: {
  attempts: CommitMeta[];
  expandedId: string | null;
  details: Record<string, CommitDetail>;
  onToggle: (id: string) => void;
}) {
  const { attempts, expandedId, details, onToggle } = props;
  const [pick, setPick] = useState(attempts.length - 1); // newest by default
  // Clamp if attempts list shrinks between refreshes.
  const idx = Math.min(pick, attempts.length - 1);
  const meta = attempts[idx];
  const switcher = attempts.length > 1 ? (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
        marginLeft: 8,
        fontSize: 10,
        color: "var(--text-muted)",
      }}
      onClick={(e) => e.stopPropagation()}
    >
      <button
        type="button"
        onClick={() => setPick((p) => Math.max(0, p - 1))}
        disabled={idx === 0}
        style={{
          background: "transparent",
          border: "none",
          color: "inherit",
          cursor: idx === 0 ? "default" : "pointer",
          padding: "0 4px",
          opacity: idx === 0 ? 0.4 : 1,
        }}
      >‹</button>
      <span>{idx + 1}/{attempts.length}</span>
      <button
        type="button"
        onClick={() => setPick((p) => Math.min(attempts.length - 1, p + 1))}
        disabled={idx === attempts.length - 1}
        style={{
          background: "transparent",
          border: "none",
          color: "inherit",
          cursor: idx === attempts.length - 1 ? "default" : "pointer",
          padding: "0 4px",
          opacity: idx === attempts.length - 1 ? 0.4 : 1,
        }}
      >›</button>
    </span>
  ) : null;
  return (
    <CommitRow
      meta={meta}
      switcher={switcher}
      open={expandedId === meta.id}
      detail={details[meta.id]}
      onToggle={() => onToggle(meta.id)}
    />
  );
}
