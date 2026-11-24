"use client";

/**
 * The Review pane's diff body. Same parse as the chat's inline diff
 * (parseUnifiedDiff), laid out for reading a whole file: the file
 * headers (---/+++/diff/index) are dropped because the pane's file
 * header already names the file, each hunk header becomes "Lines a–b",
 * long lines wrap instead of running off the edge, and an added or
 * deleted file shows only the line-number column it has.
 *
 * A hunk header can revert that hunk in the working file and, in the
 * Unstaged and Staged scopes, stage or unstage it. The line under the
 * pointer offers a comment; a saved comment stays pinned under its line
 * as a card (the line gets a dot at its end) until the pane sends all of
 * them. Findings of Review with agent sit under their lines the same way.
 * Only the hovered line renders its comment button, so long diffs do not
 * carry one control per line.
 */
import { Fragment, useMemo, useState, type ReactNode } from "react";
import { MessageSquarePlus, Minus, Plus, Undo2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { HoverTip } from "@/components/ui/tooltip";
import { useTranslation } from "@/lib/i18n";
import { hunkRange, reviewHunks, reviewRows, rowLine, type DiffLine, type ReviewHunk } from "./review-diff-rows.ts";
import type { ReviewComment, ReviewFinding } from "./review-tab-types";
import styles from "./review-diff.module.css";

const lineKey = (side: "old" | "new", line: number) => `${side}:${line}`;

export function ReviewDiff({
  diff,
  op,
  onRevertHunk,
  staging,
  onStageHunk,
  onComment,
  comments = [],
  onEditComment,
  onDeleteComment,
  findings = [],
  onDismissFinding,
  onFixFinding,
}: {
  diff: string;
  op?: string;
  /** Resolves once the revert finished, whatever its outcome. */
  onRevertHunk?: (hunk: ReviewHunk) => Promise<void>;
  /** "stage" in the Unstaged scope, "unstage" in the Staged one. */
  staging?: "stage" | "unstage";
  onStageHunk?: (hunk: ReviewHunk) => Promise<void>;
  onComment?: (row: DiffLine, comment: string) => void;
  /** Pending comments on this file in this scope. */
  comments?: readonly ReviewComment[];
  onEditComment?: (id: string, body: string) => void;
  onDeleteComment?: (id: string) => void;
  /** Agent findings on this file in this scope. */
  findings?: readonly ReviewFinding[];
  onDismissFinding?: (id: string) => void;
  onFixFinding?: (finding: ReviewFinding) => void;
}) {
  const { text } = useTranslation();
  const rows = useMemo(() => reviewRows(diff), [diff]);
  const hunks = useMemo(() => reviewHunks(diff, op), [diff, op]);
  const [hovered, setHovered] = useState(-1);
  const [commenting, setCommenting] = useState(-1);
  const [draft, setDraft] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [busyHunk, setBusyHunk] = useState(-1);
  const showOld = op !== "add";
  const showNew = op !== "delete";
  const columns = showOld && showNew ? "" : styles.oneNumber;

  // Cards by the line they sit under. A finding names a new-file line,
  // or an old one for a removed line; one on no shown line goes on top.
  const placed = useMemo(() => {
    const keys = new Set<string>();
    for (const row of rows) {
      if (row.kind === "add" || row.kind === "del" || row.kind === "ctx") {
        const { side, line } = rowLine(row);
        keys.add(lineKey(side, line));
      }
    }
    const commentsAt = new Map<string, ReviewComment[]>();
    for (const comment of comments) {
      const key = lineKey(comment.side, comment.line);
      commentsAt.set(key, [...(commentsAt.get(key) ?? []), comment]);
    }
    const findingsAt = new Map<string, ReviewFinding[]>();
    const loose: ReviewFinding[] = [];
    for (const finding of findings) {
      const key = [lineKey("new", finding.line), lineKey("old", finding.line)].find((candidate) => keys.has(candidate));
      if (key) findingsAt.set(key, [...(findingsAt.get(key) ?? []), finding]);
      else loose.push(finding);
    }
    return { commentsAt, findingsAt, loose };
  }, [rows, comments, findings]);

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

  const findingCard = (finding: ReviewFinding) => (
    <NoteCard
      key={finding.id}
      agent
      title={`${text("Agent review", "Agent 审阅")} · ${severityLabel(finding.severity, text)}`}
      severity={finding.severity}
      body={finding.body}
      actions={
        <>
          {onDismissFinding ? (
            <Button type="button" variant="ghost" size="xs" onClick={() => onDismissFinding(finding.id)}>
              {text("Dismiss", "忽略")}
            </Button>
          ) : null}
          {onFixFinding ? (
            <Button type="button" variant="elevated" size="xs" onClick={() => onFixFinding(finding)}>
              {text("Fix", "修复")}
            </Button>
          ) : null}
        </>
      }
    />
  );

  const commentCard = (comment: ReviewComment) => editing === comment.id ? (
    <CommentForm
      key={comment.id}
      initial={comment.body}
      submitLabel={text("Save", "保存")}
      onCancel={() => setEditing(null)}
      onSubmit={(body) => {
        onEditComment?.(comment.id, body);
        setEditing(null);
      }}
    />
  ) : (
    <NoteCard
      key={comment.id}
      title={text("You", "你")}
      body={comment.body}
      actions={
        <>
          {onEditComment ? (
            <Button type="button" variant="ghost" size="xs" onClick={() => setEditing(comment.id)}>
              {text("Edit", "编辑")}
            </Button>
          ) : null}
          {onDeleteComment ? (
            <Button type="button" variant="ghost" size="xs" onClick={() => onDeleteComment(comment.id)}>
              {text("Delete", "删除")}
            </Button>
          ) : null}
        </>
      }
    />
  );

  let hunkIndex = -1;
  return (
    <div className={`${styles.diff} ${columns}`} onMouseLeave={() => setHovered(-1)}>
      {placed.loose.map(findingCard)}
      {rows.map((row, index) => {
        if (row.kind === "hunk") {
          hunkIndex += 1;
          const hunk = hunks[hunkIndex];
          const at = hunkIndex;
          const range = hunkRange(row.text);
          const canStage = staging && onStageHunk && hunk?.stageable;
          const canRevert = onRevertHunk && hunk?.revertible;
          return (
            <div key={index} className={styles.hunk}>
              {range
                ? text(`Lines ${range.from}–${range.to}`, `第 ${range.from}–${range.to} 行`)
                : row.text}
              {range?.context ? <span className={styles.hunkContext}>{range.context}</span> : null}
              {canStage || canRevert ? (
                <span className={styles.hunkActions}>
                  {canRevert ? (
                    <HoverTip label={text("Put these lines back as they were", "把这些行恢复成改动前的样子")}>
                      <Button
                        type="button"
                        variant="ghost"
                        size="xs"
                        className={styles.hunkAction}
                        disabled={busyHunk !== -1}
                        onClick={() => {
                          setBusyHunk(at);
                          void onRevertHunk(hunk).finally(() => setBusyHunk(-1));
                        }}
                      >
                        <Undo2 aria-hidden="true" />
                        {text("Revert", "撤销")}
                      </Button>
                    </HoverTip>
                  ) : null}
                  {canStage ? (
                    <HoverTip
                      label={staging === "stage"
                        ? text("Add these lines to the next commit", "把这些行加入下一次提交")
                        : text("Take these lines out of the next commit", "把这些行移出下一次提交")}
                    >
                      <Button
                        type="button"
                        variant="ghost"
                        size="xs"
                        className={styles.hunkAction}
                        disabled={busyHunk !== -1}
                        onClick={() => {
                          setBusyHunk(at);
                          void onStageHunk(hunk).finally(() => setBusyHunk(-1));
                        }}
                      >
                        {staging === "stage" ? <Plus aria-hidden="true" /> : <Minus aria-hidden="true" />}
                        {staging === "stage" ? text("Stage", "暂存") : text("Unstage", "取消暂存")}
                      </Button>
                    </HoverTip>
                  ) : null}
                </span>
              ) : null}
            </div>
          );
        }
        if (row.kind === "meta") {
          return <div key={index} className={styles.note}>{row.text.replace(/^\\\s*/, "")}</div>;
        }
        const sign = row.kind === "add" ? "+" : row.kind === "del" ? "−" : "";
        const { side, line } = rowLine(row);
        const key = lineKey(side, line);
        const lineComments = placed.commentsAt.get(key) ?? [];
        const lineFindings = placed.findingsAt.get(key) ?? [];
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
              {lineComments.length ? <span className={styles.dot} aria-hidden="true" /> : null}
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
            {lineFindings.map(findingCard)}
            {lineComments.map(commentCard)}
            {commenting === index ? (
              <CommentForm
                value={draft}
                onChange={setDraft}
                submitLabel={text("Comment", "评论")}
                onCancel={closeComment}
                onSubmit={() => submitComment(row)}
              />
            ) : null}
          </Fragment>
        );
      })}
    </div>
  );
}

function severityLabel(severity: ReviewFinding["severity"], text: (en: string, zh: string) => string): string {
  return severity === "high" ? text("high", "高") : severity === "medium" ? text("medium", "中") : text("low", "低");
}

/** A note pinned under a line: your comment, or a finding of the agent. */
function NoteCard({ agent = false, title, severity, body, actions }: {
  agent?: boolean;
  title: string;
  severity?: ReviewFinding["severity"];
  body: string;
  actions: ReactNode;
}) {
  return (
    <div className={styles.card} data-note={agent ? "finding" : "comment"} data-severity={severity}>
      <span className={agent ? styles.avatarAgent : styles.avatar} aria-hidden="true">{agent ? "✦" : title.slice(0, 1)}</span>
      <div className={styles.cardMain}>
        <div className={styles.cardTitle}>{title}</div>
        <div className={styles.cardBody}>{body}</div>
        <div className={styles.cardActions}>{actions}</div>
      </div>
    </div>
  );
}

/** Writing or editing a line comment. ⌘Enter saves, Escape cancels. */
function CommentForm({ initial, value, onChange, submitLabel, onCancel, onSubmit }: {
  initial?: string;
  value?: string;
  onChange?: (value: string) => void;
  submitLabel: string;
  onCancel: () => void;
  onSubmit: (body: string) => void;
}) {
  const { text } = useTranslation();
  const [own, setOwn] = useState(initial ?? "");
  const current = value ?? own;
  const change = onChange ?? setOwn;
  const submit = () => {
    if (current.trim()) onSubmit(current.trim());
  };
  return (
    <form
      className={styles.comment}
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
    >
      <Textarea
        autoFocus
        value={current}
        onChange={(event) => change(event.target.value)}
        onKeyDown={(event) => {
          if (event.nativeEvent.isComposing) return;
          if (event.key === "Escape") {
            event.preventDefault();
            onCancel();
          } else if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
            event.preventDefault();
            submit();
          }
        }}
        placeholder={text("Comment for the agent", "给 agent 的评论")}
        aria-label={text("Comment on this line", "评论这一行")}
      />
      <div className={styles.commentActions}>
        <Button type="button" variant="ghost" size="sm" onClick={onCancel}>
          {text("Cancel", "取消")}
        </Button>
        <Button type="submit" size="sm" disabled={!current.trim()}>
          {submitLabel}
        </Button>
      </div>
    </form>
  );
}
