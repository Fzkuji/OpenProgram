/** One row per CommitItem inside an expanded commit's popout.
 *  Collapsed = single line (role label + ellipsised preview + token
 *  count); expanded = full text + state / anchor / locked / reason
 *  chips. Mirrors the composer slash-command palette aesthetic. */
import { useState } from "react";
import styles from "./styles.module.css";
import { StateBadge } from "./state-badge";
import type { CommitItem } from "./types";

export function ItemRow(props: { item: CommitItem }) {
  const it = props.item;
  const [open, setOpen] = useState(false);
  const oneLine = (it.rendered || "").replace(/\s+/g, " ").trim();
  return (
    <div
      className={styles.item + (open ? " " + styles.itemOpen : "")}
      onClick={() => setOpen((v) => !v)}
      role="button"
    >
      <div className={styles.itemHead}>
        <span className={styles.itemLabel}>{it.role}</span>
        <span className={styles.itemPreview}>
          {oneLine || "(empty)"}
        </span>
        <span className={styles.itemTokens}>{it.tokens}t</span>
      </div>
      {open && (
        <div className={styles.itemBody}>
          <div className={styles.itemChips}>
            <StateBadge state={it.state} />
            {it.is_anchor && (
              <span style={{ color: "var(--orange, #e3b341)", fontSize: 10 }}>anchor</span>
            )}
            {it.locked && (
              <span style={{ color: "var(--text-muted)", fontSize: 10 }}>locked</span>
            )}
            {it.reason && (
              <span style={{ color: "var(--text-muted)", fontSize: 10 }}>
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
