"use client";

/**
 * Unified-diff renderer for the right rail's Details view.
 *
 * Fed by `session-store.fileDiff`, which a turn file card's "Review"
 * button populates over the `turn_file_diff` WS action.
 *
 * Hand-rolled on purpose — a unified diff is line-prefixed text, so
 * colouring it is a parse of the first character plus a hunk-header
 * regex. No diff library is worth the bundle for that.
 */
import { useMemo } from "react";

import { useSessionStore } from "@/lib/session-store";
import { useTranslation } from "@/lib/i18n";

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

export function FileDiffView() {
  const { text } = useTranslation();
  const diff = useSessionStore((s) => s.fileDiff);
  const close = useSessionStore((s) => s.closeFileDiff);
  const rows = useMemo(
    () => (diff?.diff ? parseUnifiedDiff(diff.diff) : []),
    [diff?.diff],
  );

  if (!diff) return null;

  return (
    <div className="file-diff">
      <div className="file-diff-head">
        <span className="file-diff-path" title={diff.path}>{diff.rel}</span>
        <button
          type="button"
          className="file-diff-close"
          onClick={close}
          aria-label={text("Close diff", "关闭差异")}
        >
          ×
        </button>
      </div>
      {diff.approximate ? (
        <div className="file-diff-note">
          {text(
            "Approximate — compared against the file's current contents, so later edits may appear.",
            "近似差异——与文件当前内容比较，可能包含后续轮次的改动。",
          )}
        </div>
      ) : null}
      {diff.loading ? (
        <div className="file-diff-empty">{text("Loading diff…", "正在加载差异…")}</div>
      ) : diff.error ? (
        <div className="file-diff-empty is-error">{diff.error}</div>
      ) : rows.length === 0 ? (
        <div className="file-diff-empty">
          {text("No textual changes.", "没有文本改动。")}
        </div>
      ) : (
        <pre className="file-diff-body">
          {rows.map((r, i) => (
            <div key={i} className={`file-diff-line is-${r.kind}`}>
              <span className="file-diff-no">{r.oldNo}</span>
              <span className="file-diff-no">{r.newNo}</span>
              <span className="file-diff-text">{r.text || " "}</span>
            </div>
          ))}
        </pre>
      )}
    </div>
  );
}
