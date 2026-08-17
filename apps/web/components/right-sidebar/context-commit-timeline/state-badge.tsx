/** Tiny coloured pill rendering an item or count badge by state. */
import { STATE_COLOR, type StateName } from "./types";

export function StateBadge(props: { state: StateName; count?: number }) {
  const { state, count } = props;
  const c = STATE_COLOR[state];
  return (
    <span
      style={{
        background: c.bg,
        color: c.fg,
        padding: "1px 6px",
        borderRadius: 3,
        fontSize: 10,
        whiteSpace: "nowrap",
      }}
    >
      {state}{count !== undefined ? ` ${count}` : ""}
    </span>
  );
}
