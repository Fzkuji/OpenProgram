"use client";

/** The confirmation for a rewind: which message, what to restore, and what
 *  the check found. It appears only when the check has something to show or
 *  takes long enough to be noticed. */
import { useEffect, useRef } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { SimpleSelect } from "@/components/ui/select";
import { useTranslation } from "@/lib/i18n";
import { cancelRewind, confirmRewind, setRewindMode, stopBlockingThenRewind } from "@/lib/chat/rewind/rewind-actions";
import {
  noCodeLine,
  promptPreview,
  skippedLine,
  statsLine,
  type RewindMode,
} from "@/lib/chat/rewind/rewind-logic";
import { useRewindStore } from "@/lib/chat/rewind/rewind-store";
import { parseAttachments } from "../messages/attachments/user-attachments";

export function RewindDialog() {
  const { text } = useTranslation();
  const flow = useRewindStore(s => s.flow);
  const cancelButton = useRef<HTMLButtonElement>(null);
  const isSlow = flow?.phase === "slow";
  useEffect(() => {
    // Rewind is disabled now, so the only way out is Close.
    if (isSlow) cancelButton.current?.focus();
  }, [isSlow]);
  if (!flow || !flow.visible) return null;
  const { preview, phase, mode, error, blocking } = flow;
  const running = phase === "running";
  const slow = phase === "slow";
  const checking = preview === "pending";
  const noCode = !checking && preview.kind === "no_code";
  const showSelect = checking || preview.kind !== "none";
  const options: { value: RewindMode; label: string; disabled?: boolean }[] = [
    { value: "code_and_conversation", label: text("Code and conversation", "代码和对话"), disabled: noCode },
    { value: "conversation", label: text("Conversation only", "仅对话") },
    { value: "code", label: text("Code only", "仅代码"), disabled: noCode },
  ];
  const prompt = parseAttachments(flow.text);
  const stats = checking
    ? text("Checking for file changes…", "正在检查文件改动…")
    : preview.kind === "files"
      ? statsLine(preview, text)
      : noCode
        ? noCodeLine(preview.reason, text)
        : "";
  const skipped = !checking && preview.kind === "files" ? skippedLine(preview.skipped, text) : noCode ? skippedLine(flow.plan?.skipped ?? [], text) : "";
  // The block keeps its height when hidden, so changing the option does not
  // resize the dialog under the pointer.
  const statsHidden = mode === "conversation" && !noCode;
  return (
    <Dialog
      open
      onOpenChange={next => {
        if (!next && !running) cancelRewind();
      }}
    >
      <DialogContent
        showCloseButton={false}
        aria-label={text("Rewind to this message?", "回退到这条消息？")}
        onOpenAutoFocus={event => {
          event.preventDefault();
          cancelButton.current?.focus();
        }}
      >
        <DialogHeader>
          <DialogTitle>{text("Rewind to this message?", "回退到这条消息？")}</DialogTitle>
          <DialogDescription className="sr-only">
            {text(
              "This message and everything after it are withdrawn from the current branch. No new branch is created. Undo brings the messages back without changing files.",
              "撤回当前分支中这条消息及之后的内容，不会新建分支。撤销回退可找回消息，但不会改变文件。",
            )}
          </DialogDescription>
        </DialogHeader>
        <div className="rounded-lg bg-muted px-3 py-2 text-fs-base">
          <span className="sr-only">{text("You", "你")}</span>
          <span className="line-clamp-2 whitespace-pre-wrap">
            {promptPreview(prompt.text, prompt.attachments.map(item => item.filename))}
          </span>
        </div>
        {showSelect ? (
          <div className="flex items-center justify-between gap-3">
            <span className="text-fs-base">{text("Restore", "恢复")}</span>
            <SimpleSelect<RewindMode>
              value={mode}
              onValueChange={setRewindMode}
              options={options}
              label={text("Restore", "恢复")}
              disabled={checking || running || slow}
            />
          </div>
        ) : null}
        {showSelect ? (
          <div className={statsHidden ? "invisible grid gap-1" : "grid gap-1"}>
            <p className="min-h-5 text-fs-sm text-text-muted tabular-nums">{stats}</p>
            {skipped ? <p className="text-fs-xs text-text-muted">{skipped}</p> : null}
            <p className={noCode ? "hidden" : checking ? "invisible text-fs-xs text-text-muted" : "text-fs-xs text-text-muted"}>
              {text(
                "Only changes made by edit tools are put back. Files changed only by shell commands stay, and commits stay.",
                "只恢复编辑工具做的改动。只被 Shell 命令改过的文件保持原样，提交也会保留。",
              )}
            </p>
          </div>
        ) : null}
        {slow ? (
          <p className="text-fs-sm text-text-muted" role="status">
            {text(
              "Restoring is taking longer than expected. Your files may still change.",
              "恢复比预期慢，文件可能还会变化。",
            )}
          </p>
        ) : null}
        {error ? <p className="text-fs-sm text-destructive" role="alert">{error}</p> : null}
        <DialogFooter>
          <Button ref={cancelButton} variant="ghost" disabled={running} onClick={() => cancelRewind()}>
            {slow ? text("Close", "关闭") : text("Cancel", "取消")}
          </Button>
          {blocking ? (
            <Button disabled={running || slow} onClick={() => void stopBlockingThenRewind()}>
              {text("Stop it, then rewind", "停止它再回退")}
            </Button>
          ) : (
            <Button disabled={checking || running || slow} onClick={() => void confirmRewind()}>
              {running ? text("Rewinding…", "正在回退…") : text("Rewind", "回退")}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
