/** One row per CommitItem inside an expanded commit's popout.
 *  Collapsed = single line (role label + ellipsised preview + token
 *  count); expanded = full text + state / anchor / locked / reason
 *  chips. Mirrors the composer slash-command palette aesthetic. */
import { useState } from "react";
import styles from "../styles.module.css";
import { StateBadge } from "../state-badge";
import type { CommitItem } from "../types";
import { activateOnKey } from "@/lib/utils";

export function ItemRow(props: { item: CommitItem }) {
  const it = props.item;
  const [open, setOpen] = useState(false);
  const oneLine = (it.rendered || "").replace(/\s+/g, " ").trim();
  return (
    <div
      className={styles.item + (open ? " " + styles.itemOpen : "")}
      onClick={() => setOpen((v) => !v)}
      role="button"
      tabIndex={0}
      aria-expanded={open}
      onKeyDown={activateOnKey(() => setOpen((v) => !v))}
    >
      <div className="flex items-center gap-[8px] min-w-0">
        <span className={styles.itemLabel}>{it.role}</span>
        <span className={styles.itemPreview}>
          {oneLine || "(empty)"}
        </span>
        <span className={styles.itemTokens}>{it.tokens}t</span>
      </div>
      {open && (
        <div className={styles.itemBody}>
          <div className="flex gap-[6px] items-center flex-wrap">
            <StateBadge state={it.state} />
            {it.is_anchor && (
              <span style={{ color: "var(--commit-anchor)", fontSize: "var(--right-panel-meta-size, var(--fs-2xs))" }}>anchor</span>
            )}
            {it.locked && (
              <span style={{ color: "var(--text-muted)", fontSize: "var(--right-panel-meta-size, var(--fs-2xs))" }}>locked</span>
            )}
            {it.reason && (
              <span style={{ color: "var(--text-muted)", fontSize: "var(--right-panel-meta-size, var(--fs-2xs))" }}>
                reason: {it.reason}
              </span>
            )}
          </div>
          <div className={styles.itemText}>
            {it.rendered || <span style={{ color: "var(--text-muted)" }}>(empty)</span>}
          </div>
        </div>
      )}
    </div>
  );
}
