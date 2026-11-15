/** Unified-diff text → numbered rows. Shared by the chat's inline diff
 *  and the Review pane; plain TypeScript so tests can load it. */

export type LineKind = "add" | "del" | "hunk" | "meta" | "ctx";

export interface DiffLine {
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

