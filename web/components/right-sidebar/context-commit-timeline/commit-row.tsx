/** One row per commit. Closed = plain bordered strip; open = same
 *  strip in bg-hover tint, with a roll-down popout below (grid-rows
 *  0fr↔1fr transition smoothly pushes following rows). */
import styles from "./styles.module.css";
import { CommitMetaContent } from "./commit-meta";
import { ItemList } from "./item-list";
import type { CommitDetail, CommitMeta } from "./types";

export function CommitRow(props: {
  meta: CommitMeta;
  switcher?: React.ReactNode;
  open: boolean;
  detail?: CommitDetail;
  onToggle: () => void;
}) {
  const { meta, switcher, open, detail, onToggle } = props;
  const counts = meta.state_counts || {};
  return (
    <div style={{ borderBottom: "1px solid var(--border)" }}>
      <div
        onClick={onToggle}
        role="button"
        style={{
          padding: "8px 10px",
          cursor: "pointer",
          display: "flex",
          flexDirection: "column",
          gap: 3,
          background: open ? "var(--bg-hover, rgba(255,255,255,0.04))" : "transparent",
          transition: "background 0.15s",
        }}
      >
        <CommitMetaContent meta={meta} counts={counts} switcher={switcher} />
      </div>
      {/* Wrapper always mounted so the open/close transition runs
          both directions; grid-template-rows 0fr↔1fr animates the
          height which smoothly pushes everything below. */}
      <div className={styles.popoutWrap + (open ? " " + styles.open : "")}>
        <div className={styles.popoutClip}>
          <div className={styles.popout}>
            {!detail && (
              <div className={styles.empty}>Loading…</div>
            )}
            {detail?.error && (
              <div className={styles.empty} style={{ color: "var(--red, #f85149)" }}>
                {detail.error}
              </div>
            )}
            {detail?.items && (
              <ItemList items={detail.items} />
            )}
            {detail && detail.items && detail.items.length === 0 && !detail.error && (
              <div className={styles.empty}>(empty)</div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
