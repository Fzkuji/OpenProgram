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

/** One hunk as the server's `review_revert_hunk` and `review_stage` take
 *  it: the old side (context and removed lines), the new side (context and
 *  added lines) and the body as shown, each line with its sign. */
export interface ReviewHunk {
  old_start: number;
  new_start: number;
  old_lines: string[];
  new_lines: string[];
  lines: string[];
  /** Whole on this page, in the working file's segment, plain text. */
  revertible: boolean;
  /** Whole on this page and plain text: Stage / Unstage can take it. A
   *  new or deleted file is staged as a whole file instead. */
  stageable: boolean;
}

/** The hunks of a diff page, in the order its hunk rows appear. A hunk
 *  cut by the page edge or with "\ No newline" is shown but can be neither
 *  reverted nor staged; a staged (base-to-index) hunk cannot be reverted. */
export function reviewHunks(diff: string, op?: string): ReviewHunk[] {
  type Draft = ReviewHunk & { oldCount: number; newCount: number; inIndex: boolean; plain: boolean };
  const hunks: Draft[] = [];
  let staged = false;
  let current: Draft | null = null;
  for (const row of parseUnifiedDiff(diff)) {
    if (row.kind === "meta") {
      if (row.text.startsWith("+++")) staged = row.text.includes("[base-to-index]");
      else if (row.text.startsWith("\\") && current) current.plain = false;
      continue;
    }
    if (row.kind === "hunk") {
      const match = HUNK.exec(row.text);
      current = {
        old_start: match ? Number(match[1]) : 0,
        new_start: match ? Number(match[3]) : 0,
        old_lines: [],
        new_lines: [],
        lines: [],
        revertible: false,
        stageable: false,
        inIndex: staged,
        plain: !!match && op !== "add" && op !== "delete",
        oldCount: match ? (match[2] === undefined ? 1 : Number(match[2])) : -1,
        newCount: match ? (match[4] === undefined ? 1 : Number(match[4])) : -1,
      };
      hunks.push(current);
      continue;
    }
    if (!current) continue;
    const text = row.text.slice(1);
    current.lines.push(row.text);
    if (row.kind !== "add") current.old_lines.push(text);
    if (row.kind !== "del") current.new_lines.push(text);
  }
  return hunks.map(({ oldCount, newCount, inIndex, plain, ...hunk }) => {
    const whole = plain && hunk.old_lines.length === oldCount && hunk.new_lines.length === newCount;
    return { ...hunk, revertible: whole && !inIndex, stageable: whole };
  });
}

/** Where a diff row sits in its file: a removed line by its old number,
 *  any other by its new one. */
export function rowLine(row: Pick<DiffLine, "kind" | "oldNo" | "newNo">): { side: "old" | "new"; line: number } {
  return row.kind === "del"
    ? { side: "old", line: Number(row.oldNo) }
    : { side: "new", line: Number(row.newNo) };
}

function commentBlock(rel: string, side: "old" | "new", line: number | string, quote: string, comment: string): string {
  const where = side === "old" ? `\`${rel}\` line ${line} (removed)` : `\`${rel}\` line ${line}`;
  const quoted = (quote || " ").split("\n").map((text) => `> ${text}`).join("\n");
  return `${where}:\n${quoted}\n${comment.trim()}`;
}

/** A line comment as it goes into a message: file and line, the quoted
 *  line, then the comment. */
export function lineCommentText(rel: string, row: Pick<DiffLine, "kind" | "text" | "oldNo" | "newNo">, comment: string): string {
  const { side } = rowLine(row);
  return commentBlock(rel, side, side === "old" ? row.oldNo : row.newNo, row.text.slice(1), comment);
}

/** The one message that sends every pending line comment to the agent. */
export function reviewCommentsMessage(comments: readonly {
  rel: string; side: "old" | "new"; line: number; quote: string; body: string;
}[]): string {
  const blocks = comments.map((comment) => commentBlock(comment.rel, comment.side, comment.line, comment.quote, comment.body));
  return ["Please address my review comments on these changes:", ...blocks].join("\n\n");
}

/** The message a finding's Fix sends. */
export function findingFixText(finding: { rel: string; line: number; severity: string; body: string }): string {
  return `Please fix this review finding (${finding.severity}) in \`${finding.rel}\` line ${finding.line}:\n\n${finding.body.trim()}`;
}
