/** What the Review pane shows of a file's diff, as plain data so tests
 *  can load it without the React renderer. */
import { parseUnifiedDiff, type DiffLine } from "../../chat/messages/turn-files/unified-diff-parse.ts";
import { fileCapabilities, fileExtension } from "../../../lib/documents/file-formats.ts";

export type { DiffLine };

const HUNK = /^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@ ?(.*)$/;
const FILE_HEADER = /^(---|\+\+\+|diff |index |new file mode|deleted file mode|old mode|new mode|similarity index|rename from|rename to|Binary files)/;

/** "Lines 1–232" for a hunk, counted on the side that has lines. */
export function hunkRange(header: string): { from: number; to: number; context: string } | null {
  const match = HUNK.exec(header);
  if (!match) return null;
  const oldStart = Number(match[1]);
  const oldLength = match[2] === undefined ? 1 : Number(match[2]);
  const newStart = Number(match[3]);
  const newLength = match[4] === undefined ? 1 : Number(match[4]);
  const [start, length] = newLength > 0 ? [newStart, newLength] : [oldStart, oldLength];
  return { from: start, to: start + Math.max(length, 1) - 1, context: match[5].trim() };
}

/** Rows worth showing: the pane's file header already names the file. */
export function reviewRows(diff: string): DiffLine[] {
  return parseUnifiedDiff(diff).filter((row) => row.kind !== "meta" || !FILE_HEADER.test(row.text));
}

/** Files that read better rendered than as a diff: documents and images. */
export function canPreview(file: { path: string; op?: string }): boolean {
  if (file.op === "delete") return false;
  if (["md", "markdown", "mdx"].includes(fileExtension(file.path))) return true;
  const kind = fileCapabilities(file.path).preview;
  return kind === "image" || kind === "pdf";
}

/** One hunk as the server's `review_revert_hunk` takes it: the old side
 *  (context and removed lines) and the new side (context and added lines). */
export interface ReviewHunk {
  new_start: number;
  old_lines: string[];
  new_lines: string[];
  /** Whole on this page, in the working file's segment, plain text. */
  revertible: boolean;
}

/** The hunks of a diff page, in the order its hunk rows appear. A hunk
 *  cut by the page edge, a staged (base-to-index) segment of a workspace
 *  diff, or a hunk with "\ No newline" is shown but cannot be reverted. */
export function reviewHunks(diff: string, op?: string): ReviewHunk[] {
  const hunks: (ReviewHunk & { oldCount: number; newCount: number })[] = [];
  let staged = false;
  let current: (typeof hunks)[number] | null = null;
  for (const row of parseUnifiedDiff(diff)) {
    if (row.kind === "meta") {
      if (row.text.startsWith("+++")) staged = row.text.includes("[base-to-index]");
      else if (row.text.startsWith("\\") && current) current.revertible = false;
      continue;
    }
    if (row.kind === "hunk") {
      const match = HUNK.exec(row.text);
      current = {
        new_start: match ? Number(match[3]) : 0,
        old_lines: [],
        new_lines: [],
        revertible: !!match && !staged && op !== "add" && op !== "delete",
        oldCount: match ? (match[2] === undefined ? 1 : Number(match[2])) : -1,
        newCount: match ? (match[4] === undefined ? 1 : Number(match[4])) : -1,
      };
      hunks.push(current);
      continue;
    }
    if (!current) continue;
    const text = row.text.slice(1);
    if (row.kind !== "add") current.old_lines.push(text);
    if (row.kind !== "del") current.new_lines.push(text);
  }
  return hunks.map(({ oldCount, newCount, ...hunk }) => ({
    ...hunk,
    revertible: hunk.revertible && hunk.old_lines.length === oldCount && hunk.new_lines.length === newCount,
  }));
}

/** A line comment as it goes into the next message. */
export function lineCommentText(rel: string, row: Pick<DiffLine, "kind" | "text" | "oldNo" | "newNo">, comment: string): string {
  const where = row.kind === "del"
    ? `\`${rel}\` line ${row.oldNo} (removed)`
    : `\`${rel}\` line ${row.newNo}`;
  const quoted = (row.text.slice(1) || " ").split("\n").map((line) => `> ${line}`).join("\n");
  return `${where}:\n${quoted}\n${comment.trim()}`;
}
