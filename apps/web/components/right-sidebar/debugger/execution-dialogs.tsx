"use client";

/** The controls that act on an execution and the two editors behind them:
 *  Add instruction, and the branch dialog with its revision steps. */
import { useEffect, useRef, useState } from "react";
import {
  availableExecutionActions,
  buildExecutionCommand,
  newCommandId,
  type CommandResult,
  type ExecutionCommand,
  type ExecutionCommandAction,
  type ExecutionSnapshot,
  type RevisionDraft,
} from "@/lib/execution/execution-debugger";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import { useTranslation } from "@/lib/i18n";
import styles from "./debugger-panel.module.css";

type CommandSubmit = (command: ExecutionCommand) => Promise<CommandResult | void> | CommandResult | void;

const ACTION_LABELS: Record<ExecutionCommandAction, string> = {
  pause: "Pause",
  continue: "Continue",
  step: "Step",
  steer: "Steer",
  fork: "Fork",
  retry: "Retry",
  cancel: "Cancel",
};

export function ExecutionActionButton({
  action,
  snapshot,
  pending,
  payload = {},
  ready = true,
  onCommand,
}: {
  action: ExecutionCommandAction;
  snapshot: ExecutionSnapshot;
  pending: boolean;
  payload?: Record<string, unknown>;
  ready?: boolean;
  onCommand?: CommandSubmit;
}) {
  const available = availableExecutionActions(snapshot).includes(action);
  const disabled = !available || !ready || !onCommand || pending;
  async function submit() {
    if (disabled) return;
    const command = buildExecutionCommand(snapshot, action, newCommandId(), payload);
    await onCommand?.(command);
  }
  return (
    <Button variant="ghost"
      type="button"
      className={action === "cancel" ? styles.cancelButton : undefined}
      onClick={() => void submit()}
      disabled={disabled}
    >
      {pending ? "Submitting…" : ACTION_LABELS[action]}
    </Button>
  );
}

export function ExecutionCommandNotice({ result }: { result: CommandResult | null }) {
  const { text } = useTranslation();
  if (!result) return null;
  const errors: Record<string, [string, string]> = {
    continuation_contract_mismatch: ["The saved runtime differs from the current tools or settings. Restore the matching runtime before resuming.", "保存时的运行环境与当前工具或设置不一致。恢复对应环境后才能继续。"],
    version_conflict: ["The task changed before this request arrived. Its status updates automatically; try again once it is available.", "提交请求前任务状态已改变。状态将自动更新，恢复后可重试。"],
    stale_version: ["The task changed before this request arrived. Its status updates automatically; try again once it is available.", "提交请求前任务状态已改变。状态将自动更新，恢复后可重试。"],
    permission_denied: ["Your current permissions do not allow this action.", "当前权限不允许此操作。"],
    checkpoint_not_found: ["The saved point is unavailable. Wait for the task status to update before choosing another action.", "保存点不可用。请等待任务状态自动更新后选择其他操作。"],
  };
  const message = result.status === "rejected"
    ? text(...(errors[result.rejection_code || ""] || ["The request was rejected. Task status updates automatically; Technical details contains the recorded reason.", "请求被拒绝。任务状态会自动更新；技术详情中保留了具体原因。"] as [string, string]))
    : text(...({ accepted: ["Request accepted; waiting for a safe boundary.", "请求已接受，等待安全执行边界。"], applying: ["Applying request…", "正在应用请求…"], applied: ["Request applied.", "请求已应用。"] }[result.status] as [string, string]));
  return (
    <div className={`${styles.commandNotice} ${result.status === "rejected" ? styles.noticeDanger : ""}`} role="status">
      <span>{message}</span>
    </div>
  );
}

export function ExecutionSteerDialog({ open, onOpenChange, snapshot, pending, result, onCommand }: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  snapshot: ExecutionSnapshot;
  pending: boolean;
  result: CommandResult | null;
  /** Absent while the page is not connected. */
  onCommand?: (command: ExecutionCommand) => Promise<CommandResult | void>;
}) {
  const { text } = useTranslation();
  const [value, setValue] = useState("");
  // An answer that arrives after the reader edited the text or reopened the
  // dialog must not clear their new words or close what they reopened.
  const session = useRef(0);
  const revision = useRef(0);
  useEffect(() => { session.current += 1; }, [open]);
  const instruction = value.trim();
  return <Dialog open={open} onOpenChange={onOpenChange}>
    <DialogContent>
      <DialogHeader><DialogTitle>{text("Add instruction", "补充指令")}</DialogTitle>
        <DialogDescription>{text("Applied at the next safe execution boundary. Work already completed is retained.", "在下一个安全执行边界应用，已经完成的操作保持不变。")}</DialogDescription></DialogHeader>
      <label className={styles.editorLabel}>{text("Instruction", "指令")}<Textarea maxLength={4096} value={value} onChange={event => { revision.current += 1; setValue(event.target.value); }} placeholder={text("Describe the change", "描述需要调整的内容")} /></label>
      <ExecutionCommandNotice result={result} />
      <DialogFooter><ExecutionActionButton action="steer" snapshot={snapshot} pending={pending} payload={instruction ? { message: instruction } : undefined} ready={Boolean(instruction)}
        onCommand={onCommand ? async command => {
          const submittedSession = session.current;
          const submittedRevision = revision.current;
          const answer = await onCommand(command);
          if (answer && answer.status !== "rejected" && revision.current === submittedRevision) {
            setValue("");
            if (session.current === submittedSession) onOpenChange(false);
          }
          return answer;
        } : undefined} /></DialogFooter>
    </DialogContent>
  </Dialog>;
}

export function ExecutionBranchDialog({ open, onOpenChange, snapshot, draft, onCreateDraft, onUpdateDraft, onDraftAction }: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  snapshot: ExecutionSnapshot;
  draft: RevisionDraft | undefined;
  onCreateDraft?: (input: { execution_id: string; source_checkpoint_id: string; preparation?: { instructions: string; rationale?: string } }) => Promise<void> | void;
  onUpdateDraft?: (draft: RevisionDraft, changes: RevisionDraft["changes"] | { instructions: string; rationale?: string }) => Promise<void> | void;
  onDraftAction?: (draft: RevisionDraft, action: "validate" | "approve" | "publish" | "fork") => Promise<void> | void;
}) {
  const { text } = useTranslation();
  const [draftText, setDraftText] = useState<string | null>(null);
  const [draftPending, setDraftPending] = useState(false);
  const [draftError, setDraftError] = useState<string | null>(null);
  useEffect(() => { setDraftText(null); setDraftError(null); }, [draft?.draft_id]);
  return <Dialog open={open} onOpenChange={onOpenChange}>
    <DialogContent className={styles.editorDialog}>
      <DialogHeader><DialogTitle>{text("Create branch", "创建分支")}</DialogTitle>
        <DialogDescription>{text("Continue from this saved point with new instructions. The original execution stays unchanged.", "从此保存点按新指令继续，原执行保持不变。")}</DialogDescription></DialogHeader>
    {(!draft || draft.editor) ? <label className={styles.editorLabel}>
      {text("Instructions for the new branch", "新分支的指令")}
      <Textarea maxLength={4096} value={draftText ?? draft?.editor?.instructions ?? ""} onChange={(event) => setDraftText(event.target.value)} disabled={draftPending} readOnly={Boolean(draft && ["published", "discarded"].includes(draft.status))} />
    </label> : <p className={styles.muted}>{text("This revision was prepared by another client.", "此修订由其他客户端准备。")}</p>}
    {draft && <p className={styles.muted}>{({ draft: "Draft", validated: "Validated", approved: "Approved", published: "Ready to create branch", discarded: "Discarded", rejected: "Needs changes" })[draft.status]}</p>}
    <DialogFooter>
      {draft?.editor && ["published", "discarded"].includes(draft.status) && snapshot.checkpoint_head_id && <Button variant="ghost" disabled={draftPending || !onCreateDraft} onClick={async () => {
        setDraftError(null); setDraftPending(true);
        try { await onCreateDraft?.({ execution_id: snapshot.execution_id, source_checkpoint_id: snapshot.checkpoint_head_id!, preparation: { instructions: draft.editor!.instructions } }); }
        catch (error) { setDraftError(error instanceof Error ? error.message : "Could not prepare new instructions."); }
        finally { setDraftPending(false); }
      }}>{text("Edit as new draft", "编辑为新草稿")}</Button>}
      {(!draft || (!["published", "discarded"].includes(draft.status) && draft.editor)) && <Button variant={draft ? "ghost" : "default"} disabled={draftPending || (draft ? !onUpdateDraft : !onCreateDraft) || !(draftText ?? draft?.editor?.instructions ?? "").trim() || Boolean(draft && (draftText === null || draftText === draft.editor?.instructions))} onClick={async () => {
        setDraftError(null); setDraftPending(true);
        try {
          const preparation = { instructions: (draftText ?? draft?.editor?.instructions ?? "").trim() };
          if (draft) await onUpdateDraft?.(draft, preparation);
          else await onCreateDraft?.({ execution_id: snapshot.execution_id, source_checkpoint_id: snapshot.checkpoint_head_id!, preparation });
          setDraftText(null);
        } catch (error) { setDraftError(error instanceof Error ? error.message : "Could not save instructions."); }
        finally { setDraftPending(false); }
      }}>{draft ? text("Save instructions", "保存指令") : text("Prepare branch", "准备分支")}</Button>}
      {draft && (["validate", "approve", "publish", "fork"] as const).filter((action) => ({ validate: "draft", approve: "validated", publish: "approved", fork: "published" })[action] === draft.status).map((action) => <Button variant="default" key={action} disabled={draftPending || !onDraftAction || (draftText !== null && draftText !== draft.editor?.instructions)} onClick={async () => {
        setDraftError(null); setDraftPending(true);
        try { await onDraftAction?.(draft, action); }
        catch (error) { setDraftError(error instanceof Error ? error.message : "Could not apply revision action."); }
        finally { setDraftPending(false); }
      }}>{({ validate: "Check compatibility", approve: "Approve revision", publish: "Publish revision", fork: "Create branch" })[action]}</Button>)}
    </DialogFooter>
    {draftError && <div className={styles.formError} role="alert">{draftError}</div>}
    </DialogContent>
  </Dialog>;
}
