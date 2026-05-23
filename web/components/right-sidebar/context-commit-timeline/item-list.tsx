/** Render the items inside an opened commit with a soft cap so a
 *  thousand-tool turn doesn't drop a thousand DOM nodes at once.
 *  Shows the first ITEM_PAGE items with a "Show N more" affordance
 *  that progressively reveals the rest. */
import { useEffect, useState } from "react";
import { ItemRow } from "./item-row";
import type { CommitItem } from "./types";

const ITEM_PAGE = 100;

export function ItemList(props: { items: CommitItem[] }) {
  const { items } = props;
  const [shown, setShown] = useState(ITEM_PAGE);
  // Reset window when the items array identity changes (different commit).
  useEffect(() => { setShown(ITEM_PAGE); }, [items]);
  const visible = items.slice(0, shown);
  const remaining = items.length - visible.length;
  return (
    <>
      {visible.map((it, idx) => (
        <ItemRow key={`${it.source_node_id}-${idx}`} item={it} />
      ))}
      {remaining > 0 && (
        <div
          onClick={() => setShown((n) => n + ITEM_PAGE)}
          role="button"
          style={{
            padding: "6px 12px",
            fontSize: 11,
            color: "var(--text-muted)",
            cursor: "pointer",
            textAlign: "center",
            userSelect: "none",
          }}
        >
          Show {Math.min(ITEM_PAGE, remaining)} more ({remaining} left)
        </div>
      )}
    </>
  );
}
