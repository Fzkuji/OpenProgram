"use client";

/**
 * Shared unified-diff renderer — the parse + the coloured rows.
 *
 * Hand-rolled on purpose: a unified diff is line-prefixed text, so
 * colouring it is a parse of the first character plus a hunk-header
 * regex. No diff library is worth the bundle for that.
 *
 * Used inline under a turn's file-edit card (turn-files-chips.tsx).
 * Styling lives on `.file-diff-*` in app/styles/detail.css.
 */
import { useMemo } from "react";

import sbs from "./side-by-side-diff.module.css";

export { parseUnifiedDiff, type DiffLine } from "./unified-diff-parse.ts";
import { parseUnifiedDiff, type DiffLine } from "./unified-diff-parse.ts";

/** One side-by-side row: either a full-width hunk/meta banner, or a
 *  left(old)/right(new) pair where a missing side is a filler cell. */
interface SbsRow {
  span?: DiffLine;
  left?: DiffLine;
  right?: DiffLine;
}

/**
 * Pair the unified rows into two aligned columns.
 *
 * The whole job is the del/add run: a unified diff emits every `-` of
 * a change before every `+`, so buffer the consecutive runs and zip
 * them index-wise, padding the shorter side with fillers. Context rows
 * appear on both sides; hunk/meta rows flush the pending runs first so
 * a change never straddles a header.
 */
export function toSideBySide(rows: DiffLine[]): SbsRow[] {
  const out: SbsRow[] = [];
  let dels: DiffLine[] = [];
  let adds: DiffLine[] = [];
  const flush = () => {
    for (let i = 0; i < Math.max(dels.length, adds.length); i++)
      out.push({ left: dels[i], right: adds[i] });
    dels = [];
    adds = [];
  };
  for (const r of rows) {
    if (r.kind === "del") {
      dels.push(r);
      continue;
    }
    if (r.kind === "add") {
      adds.push(r);
      continue;
    }
    flush();
    if (r.kind === "ctx") out.push({ left: r, right: r });
    else out.push({ span: r });
  }
  flush();
  return out;
}

function SbsCell({ row, side }: { row?: DiffLine; side: "old" | "new" }) {
  if (!row) return <div className={`${sbs.cell} ${sbs.filler}`} />;
  return (
    <div className={`${sbs.cell} ${sbs[row.kind] ?? ""}`}>
      <span className={sbs.no}>{side === "old" ? row.oldNo : row.newNo}</span>
      <span className={sbs.text}>{row.text || " "}</span>
    </div>
  );
}

/** Two-column view of the SAME parse as `UnifiedDiff`. Empty → null. */
export function SideBySideDiff({ diff }: { diff: string }) {
  const rows = useMemo(
    () => (diff ? toSideBySide(parseUnifiedDiff(diff)) : []),
    [diff],
  );
  if (rows.length === 0) return null;
  return (
    <div className={sbs.body}>
      {rows.map((r, i) =>
        r.span ? (
          <div key={i} className={`${sbs.span} ${sbs[r.span.kind] ?? ""}`}>
            {r.span.text || " "}
          </div>
        ) : (
          <div key={i} className={sbs.row}>
            <SbsCell row={r.left} side="old" />
            <SbsCell row={r.right} side="new" />
          </div>
        ),
      )}
    </div>
  );
}

/** The `<pre>` of coloured, numbered diff rows. Empty diff → null. */
export function UnifiedDiff({ diff }: { diff: string }) {
  const rows = useMemo(() => (diff ? parseUnifiedDiff(diff) : []), [diff]);
  if (rows.length === 0) return null;
  return (
    <pre className="file-diff-body">
      {rows.map((r, i) => (
        <div key={i} className={`file-diff-line is-${r.kind}`}>
          <span className="file-diff-no">{r.oldNo}</span>
          <span className="file-diff-no">{r.newNo}</span>
          <span className="file-diff-text">{r.text || " "}</span>
        </div>
      ))}
    </pre>
  );
}
