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

type LineKind = "add" | "del" | "hunk" | "meta" | "ctx";

interface DiffLine {
  kind: LineKind;
  text: string;
  /** Line number in the old file, blank for added lines. */
  oldNo: string;
  /** Line number in the new file, blank for removed lines. */
  newNo: string;
}

const HUNK = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/;

/**
 * Split unified-diff text into renderable rows, tracking line numbers.
 *
 * Counters restart at each `@@` header (that's what the header is for);
 * before the first hunk every row is file metadata and gets no numbers.
 */
export function parseUnifiedDiff(text: string): DiffLine[] {
  const rows: DiffLine[] = [];
  let oldNo = 0;
  let newNo = 0;
  let inHunk = false;

  const lines = text.split("\n");
  // A trailing newline leaves one empty tail element; drop it rather
  // than render a blank numbered row.
  if (lines.length && lines[lines.length - 1] === "") lines.pop();

  for (const raw of lines) {
    const hunk = HUNK.exec(raw);
    if (hunk) {
      oldNo = Number(hunk[1]);
      newNo = Number(hunk[2]);
      inHunk = true;
      rows.push({ kind: "hunk", text: raw, oldNo: "", newNo: "" });
      continue;
    }
    if (!inHunk || raw.startsWith("+++") || raw.startsWith("---") ||
        raw.startsWith("diff ") || raw.startsWith("index ")) {
      rows.push({ kind: "meta", text: raw, oldNo: "", newNo: "" });
      continue;
    }
    if (raw.startsWith("+")) {
      rows.push({ kind: "add", text: raw, oldNo: "", newNo: String(newNo++) });
    } else if (raw.startsWith("-")) {
      rows.push({ kind: "del", text: raw, oldNo: String(oldNo++), newNo: "" });
    } else if (raw.startsWith("\\")) {
      // "\ No newline at end of file" — belongs to neither side.
      rows.push({ kind: "meta", text: raw, oldNo: "", newNo: "" });
    } else {
      rows.push({
        kind: "ctx", text: raw,
        oldNo: String(oldNo++), newNo: String(newNo++),
      });
    }
  }
  return rows;
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
