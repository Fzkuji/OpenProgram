"use client";

/** What the reader can do to a run: the action rows under its events, and the
 *  Technical details row with everything rarer. Both share one set of state,
 *  so Continue uses the code policy chosen in the details. */
import { useEffect, useState, type ReactNode } from "react";
import { CircleX, GitBranch, Pause, Play, SquarePen, StepForward, type LucideIcon } from "lucide-react";
import {
  availableExecutionActions,
  buildExecutionCommand,
  newCommandId,
  type CommandResult,
  type ExecutionCommand,
  type ExecutionCommandAction,
  type ExecutionSnapshot,
} from "@/lib/execution/execution-debugger";
import { ExecutionBranchDialog, ExecutionCommandNotice, ExecutionSteerDialog } from "../debugger/execution-dialogs";
import { ExecutionCodePolicySelect, ExecutionStandingNote, ExecutionTechnicalDetails } from "../debugger/execution-technical-details";
import { ExecutionWaitForms } from "../debugger/execution-wait-forms";
import type { ActivityPanelContext } from "./activity-page-types";
import { ActivityActionRow, ActivityDetail, ActivityKeyValue, ActivityRow } from "./activity-page-row";
import type { RunInspection } from "./use-run-inspection";
import styles from "./activity-page.module.css";

export function useExecutionControls(snapshot: ExecutionSnapshot, inspection: RunInspection, context: ActivityPanelContext) {
  const [commandResults, setCommandResults] = useState<Record<string, CommandResult>>({});
  const [pendingActions, setPendingActions] = useState<Set<string>>(new Set());
  const [codePolicy, setCodePolicy] = useState("");
  const [editor, setEditor] = useState<"steer" | "branch" | null>(null);
  const { events } = inspection;

  useEffect(() => {
    setCommandResults((current) => {
      let next = current;
      for (const event of events) {
        const value = event.payload?.command;
        if (!value || typeof value !== "object") continue;
        const command = value as Record<string, unknown>;
        const id = command.command_id;
        const status = command.status;
        if (typeof id !== "string" || !["accepted", "applying", "applied", "rejected"].includes(String(status))) continue;
        const entry = Object.entries(next).find(([, result]) => result.command_id === id);
        if (!entry || entry[1].status === status) continue;
        next = { ...next, [entry[0]]: { ...entry[1], status: status as CommandResult["status"], rejection_code: typeof command.rejection_code === "string" ? command.rejection_code : null } };
      }
      return next;
    });
  }, [events]);

  async function submit(command: ExecutionCommand): Promise<CommandResult | void> {
    setPendingActions((current) => new Set(current).add(command.action));
    try {
      const result = await context.controller.command(command);
      if (result) setCommandResults((current) => ({ ...current, [command.action]: result }));
      void inspection.refresh();
      return result;
    } catch (error) {
      setCommandResults((current) => ({
        ...current,
        [command.action]: { command_id: command.command_id, status: "rejected", rejection_code: error instanceof Error ? error.message : "command_failed" },
      }));
      return undefined;
    } finally {
      setPendingActions((current) => { const next = new Set(current); next.delete(command.action); return next; });
    }
  }

  const run = (action: ExecutionCommandAction, payload: Record<string, unknown> = {}) => {
    if (!context.connected) return;
    void submit(buildExecutionCommand(snapshot, action, newCommandId(), payload));
  };
  const draft = inspection.drafts.slice().reverse().find((item) => item.source_execution_id === snapshot.execution_id);
  return { snapshot, inspection, context, commandResults, pendingActions, codePolicy, setCodePolicy, editor, setEditor, submit, run, draft };
}

export type ExecutionControls = ReturnType<typeof useExecutionControls>;

/** Pause, Continue, Add instruction and Cancel, each only when the run allows it. */
export function ExecutionActionRows({ controls, indent }: { controls: ExecutionControls; indent?: number }) {
  const { snapshot, inspection, context, commandResults, pendingActions, codePolicy, editor, setEditor, submit, run, draft } = controls;
  const { text, connected } = context;
  const available = availableExecutionActions(snapshot);
  const row = (action: ExecutionCommandAction, icon: LucideIcon, label: string, note: string, payload?: Record<string, unknown>) =>
    available.includes(action) ? <ActivityActionRow key={action} icon={icon} label={label} note={note} indent={indent}
      disabled={!connected} busy={pendingActions.has(`execution.${action}`)} onClick={() => run(action, payload)} /> : null;
  const notices = Object.values(commandResults);
  const respondAndRead = {
    onCreateDraft: async (input: Parameters<typeof context.controller.createDraft>[0]) => { await context.controller.createDraft(input); await inspection.refresh(); },
    onUpdateDraft: async (item: Parameters<typeof context.controller.updateDraft>[0], changes: Parameters<typeof context.controller.updateDraft>[1]) => { await context.controller.updateDraft(item, changes); await inspection.refresh(); },
    onDraftAction: async (item: Parameters<typeof context.controller.draftAction>[0], action: Parameters<typeof context.controller.draftAction>[1]) => { await context.controller.draftAction(item, action); await inspection.refresh(); },
  };
  return <>
    {row("pause", Pause, text("Pause", "暂停"), text("Holds at the next safe point", "在下一个安全点停住"))}
    {row("continue", Play, text("Continue", "继续"), text("Picks up where it stopped", "从停下的地方接着做"), codePolicy ? { code_change_policy: codePolicy } : undefined)}
    {available.includes("steer") ? <ActivityActionRow icon={SquarePen} label={text("Add instruction", "补充指令")} note={text("Applied at the next safe point", "在下一个安全点生效")} indent={indent} disabled={!connected} onClick={() => setEditor("steer")} /> : null}
    {row("cancel", CircleX, text("Cancel", "取消"), text("Stops this run", "停止这次运行"))}
    {notices.length > 0 ? <ActivityDetail indent={indent}>{notices.map((result) => <ExecutionCommandNotice key={result.command_id} result={result} />)}</ActivityDetail> : null}
    <ExecutionSteerDialog open={editor === "steer"} onOpenChange={(open) => setEditor(open ? "steer" : null)} snapshot={snapshot} pending={pendingActions.has("execution.steer")}
      result={commandResults["execution.steer"] || null} onCommand={connected ? submit : undefined} />
    <ExecutionBranchDialog open={editor === "branch"} onOpenChange={(open) => setEditor(open ? "branch" : null)} snapshot={snapshot} draft={draft} {...respondAndRead} />
  </>;
}

/** Everything rarer about a run, folded: Step, Create branch, the code policy,
 *  ids, wait forms the chat cannot show, and the raw record. `extra` rows go first. */
export function ExecutionTechnicalRow({ id, controls, branchFrom, indent, extra }: {
  id: string; controls: ExecutionControls; branchFrom: string | null; indent?: number; extra?: ReactNode;
}) {
  const { snapshot, inspection, context, commandResults, pendingActions, codePolicy, setCodePolicy, setEditor, run, draft } = controls;
  const { text, connected } = context;
  const available = availableExecutionActions(snapshot);
  const canBranch = Boolean(draft || (snapshot.capabilities.fork && snapshot.checkpoint_head_id));
  const decision = context.waitingFor(snapshot);
  const openWaits = inspection.waits.filter((wait) => wait.execution_id === snapshot.execution_id && ["open", "claimed"].includes(wait.status));
  const formWaits = decision?.answerLabel ? [] : openWaits;
  const checkpoint = snapshot.checkpoint_head_id ? inspection.checkpoints.find((item) => item.checkpoint_id === snapshot.checkpoint_head_id) : undefined;
  const respond = async (input: Parameters<typeof context.controller.respondWait>[0]) => { await context.controller.respondWait(input); await inspection.refresh(); };
  const lastEventCursor = inspection.cursor;
  return <ActivityRow id={id} status={text("Technical details", "技术详情")} indent={indent}>
    {() => <>
      {available.includes("step") ? <ActivityActionRow icon={StepForward} label={text("Step", "单步")} note={text("Runs to the next safe point, then holds", "运行到下一个安全点后停住")} indent={indent} disabled={!connected} busy={pendingActions.has("execution.step")} onClick={() => run("step")} /> : null}
      {canBranch ? <ActivityActionRow icon={GitBranch} label={text("Create branch", "创建分支")} note={branchFrom ? text(`Try a change from ${branchFrom}`, `从 ${branchFrom} 试一个改动`) : text("Try a change from the saved point", "从保存点试一个改动")} indent={indent} onClick={() => setEditor("branch")} /> : null}
      <ActivityDetail indent={indent}>
        {extra}
        <div className={styles.tech}><ExecutionStandingNote snapshot={snapshot} unresolvedEffects={inspection.unresolvedEffects} /></div>
        {snapshot.status === "paused" ? <div className={styles.tech}><ExecutionCodePolicySelect value={codePolicy} onChange={setCodePolicy} /></div> : null}
        {snapshot.reason_code ? <ActivityKeyValue label={text("Reason code", "原因代码")} value={snapshot.reason_code} /> : null}
        <ActivityKeyValue label={text("Execution ID", "执行 ID")} value={snapshot.execution_id} />
        <ActivityKeyValue label={text("Run ID", "运行 ID")} value={snapshot.run_id} />
        <ActivityKeyValue label={text("Revision", "修订")} value={snapshot.revision_id} />
        <ActivityKeyValue label={text("State version", "状态版本")} value={String(snapshot.status_version)} />
        {lastEventCursor ? <ActivityKeyValue label={text("Event cursor", "事件游标")} value={String(lastEventCursor.next_sequence)} /> : null}
        {snapshot.checkpoint_head_id ? <ActivityKeyValue label={text("Saved point", "保存点")} value={snapshot.checkpoint_head_id} /> : null}
        <div className={styles.tech}>
          <ExecutionWaitForms waits={formWaits} onRespond={connected ? respond : undefined} />
          <ExecutionTechnicalDetails snapshot={snapshot} events={inspection.events} connection={{ cursor: lastEventCursor }} commandResults={commandResults} checkpoint={checkpoint} identity={false} eventListClassName={styles.techEvents} />
        </div>
      </ActivityDetail>
    </>}
  </ActivityRow>;
}
