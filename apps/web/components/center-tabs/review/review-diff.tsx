"use client";

/**
 * The Review pane's diff body. Same parse as the chat's inline diff
 * (parseUnifiedDiff), laid out for reading a whole file: the file
 * headers (---/+++/diff/index) are dropped because the pane's file
 * header already names the file, each hunk header becomes "Lines a–b",
 * long lines wrap instead of running off the edge, and an added or
 * deleted file shows only the line-number column it has.
 *
 * A hunk header can revert that hunk in the working file, and the line
 * under the pointer offers a comment that goes into the next message.
 * Only the hovered line renders its comment button, so long diffs do not
 * carry one control per line.
 */
import { Fragment, useMemo, useState } from "react";
import { MessageSquarePlus, Undo2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { HoverTip } from "@/components/ui/tooltip";
import { useTranslation } from "@/lib/i18n";
import { hunkRange, reviewHunks, reviewRows, type DiffLine, type ReviewHunk } from "./review-diff-rows.ts";
import styles from "./review-diff.module.css";

export function ReviewDiff({ diff, op, onRevertHunk, onComment }: {
  diff: string;
  op?: string;
  /** Resolves once the revert finished, whatever its outcome. */
  onRevertHunk?: (hunk: ReviewHunk) => Promise<void>;
  onComment?: (row: DiffLine, comment: string) => void;
}) {
  const { text } = useTranslation();
  const rows = useMemo(() => reviewRows(diff), [diff]);
  const hunks = useMemo(() => reviewHunks(diff, op), [diff, op]);
  const [hovered, setHovered] = useState(-1);
  const [commenting, setCommenting] = useState(-1);
  const [draft, setDraft] = useState("");
  const [reverting, setReverting] = useState(-1);
  const showOld = op !== "add";
  const showNew = op !== "delete";
  const columns = showOld && showNew ? "" : styles.oneNumber;
  if (rows.length === 0) return null;

  const closeComment = () => {
    setCommenting(-1);
    setDraft("");
  };
  const submitComment = (row: DiffLine) => {
    if (!draft.trim() || !onComment) return;
    onComment(row, draft);
    closeComment();
  };

  let hunkIndex = -1;
  return (
    <div className={`${styles.diff} ${columns}`} onMouseLeave={() => setHovered(-1)}>
      {rows.map((row, index) => {
        if (row.kind === "hunk") {
          hunkIndex += 1;
          const hunk = hunks[hunkIndex];
          const at = hunkIndex;
          const range = hunkRange(row.text);
          return (
            <div key={index} className={styles.hunk}>
              {range
                ? text(`Lines ${range.from}–${range.to}`, `第 ${range.from}–${range.to} 行`)
                : row.text}
              {range?.context ? <span className={styles.hunkContext}>{range.context}</span> : null}
              {onRevertHunk && hunk?.revertible ? (
                <HoverTip label={text("Put these lines back as they were", "把这些行恢复成改动前的样子")}>
                  <Button
                    type="button"
                    variant="ghost"
                    size="xs"
                    className={styles.hunkAction}
                    disabled={reverting !== -1}
                    onClick={() => {
                      setReverting(at);
                      void onRevertHunk(hunk).finally(() => setReverting(-1));
                    }}
                  >
                    <Undo2 aria-hidden="true" />
                    {reverting === at ? text("Reverting…", "正在撤销…") : text("Revert", "撤销")}
                  </Button>
                </HoverTip>
              ) : null}
            </div>
          );
        }
        if (row.kind === "meta") {
          return <div key={index} className={styles.note}>{row.text.replace(/^\\\s*/, "")}</div>;
        }
        const sign = row.kind === "add" ? "+" : row.kind === "del" ? "−" : "";
        return (
          <Fragment key={index}>
            <div
              className={`${styles.line} ${styles[row.kind] ?? ""}`}
              onMouseEnter={onComment ? () => setHovered(index) : undefined}
            >
              {showOld ? <span className={styles.number}>{row.oldNo}</span> : null}
              {showNew ? <span className={styles.number}>{row.newNo}</span> : null}
              <span className={styles.sign} aria-hidden="true">{sign}</span>
              <span className={styles.text}>{row.text.slice(1) || " "}</span>
              {onComment && hovered === index && commenting !== index ? (
                <HoverTip label={text("Comment on this line", "评论这一行")}>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-xs"
                    className={styles.commentButton}
                    tabIndex={-1}
                    aria-label={text("Comment on this line", "评论这一行")}
                    onClick={() => {
                      setCommenting(index);
                      setDraft("");
                    }}
                  >
                    <MessageSquarePlus aria-hidden="true" />
                  </Button>
                </HoverTip>
              ) : null}
            </div>
            {commenting === index ? (
              <form
                className={styles.comment}
                onSubmit={(event) => {
                  event.preventDefault();
                  submitComment(row);
                }}
              >
                <Textarea
                  autoFocus
                  value={draft}
                  onChange={(event) => setDraft(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.nativeEvent.isComposing) return;
                    if (event.key === "Escape") {
                      event.preventDefault();
                      closeComment();
                    } else if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
                      event.preventDefault();
                      submitComment(row);
                    }
                  }}
                  placeholder={text("Comment for the agent", "给 agent 的评论")}
                  aria-label={text("Comment on this line", "评论这一行")}
                />
                <div className={styles.commentActions}>
                  <Button type="button" variant="ghost" size="sm" onClick={closeComment}>
                    {text("Cancel", "取消")}
                  </Button>
                  <Button type="submit" size="sm" disabled={!draft.trim()}>
                    {text("Add to message", "加入消息")}
                  </Button>
                </div>
              </form>
            ) : null}
          </Fragment>
        );
      })}
    </div>
  );
}
