/** The four lines of metadata shown on the closed commit row:
 *  id + relative time, token / item count + rules version, optional
 *  summary blurb, and the per-state badge strip. Used by CommitRow.
 *
 *  When a commit row belongs to a multi-attempt group, the caller
 *  passes a switcher element which is rendered next to the id. */
import type { CommitMeta as Meta, StateName } from "./types";
import { fmtRelTime } from "./utils";
import { StateBadge } from "./state-badge";

export function CommitMetaContent(props: {
  meta: Meta;
  counts: Partial<Record<StateName, number>>;
  switcher?: React.ReactNode;
}) {
  const { meta, counts, switcher } = props;
  return (
    <>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 6 }}>
        <span style={{ color: "var(--text-bright)" }}>
          {meta.id.slice(0, 12)}
          {switcher}
        </span>
        <span style={{ color: "var(--text-muted)" }}>{fmtRelTime(meta.created_at)}</span>
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", color: "var(--text-muted)" }}>
        <span>{meta.total_tokens.toLocaleString()} tok · {meta.item_count} items</span>
        <span>{meta.rules_version || ""}</span>
      </div>
      {meta.summary && (
        <div style={{ color: "var(--text-muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {meta.summary}
        </div>
      )}
      <div style={{ display: "flex", gap: 4, flexWrap: "wrap", marginTop: 2 }}>
        {(Object.keys(counts) as StateName[])
          .filter((k) => (counts[k] || 0) > 0)
          .map((k) => (
            <StateBadge key={k} state={k} count={counts[k] || 0} />
          ))}
      </div>
    </>
  );
}
