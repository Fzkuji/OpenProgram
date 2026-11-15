"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Check, Pause, Play, Square } from "lucide-react";
import { CheckIcon, HistoryIcon } from "@/components/animated-icons";
import { useActionIconAnimation } from "../messages/bubbles/use-action-icon-animation";

import { Button } from "@/components/ui/button";
import { FillIcon } from "@/components/ui/fill-icon";
import { SolarIcon } from "@/components/solar-icons";
import { HoverTip, TipBody } from "@/components/ui/tooltip";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { api, type GoalControls } from "@/lib/net/api";
import { SessionDebugger } from "@/components/right-sidebar/debugger/session-debugger";
import { HttpError } from "@/lib/net/fetch-client";
import { runtimeState } from "@/lib/runtime-bridge/state";
import { updateSessionGoal } from "@/lib/runtime-bridge/actions/goal-state";
import { useSessionStore } from "@/lib/session-store";
import { useTranslation } from "@/lib/i18n";

import styles from "./goal-chip.module.css";

export interface GoalState {
  end_records?: { anchor_id: string | null; goal: GoalState }[];
  execution_mode?: string;
  schema_version?: number;
  goal_id?: string;
  run_id?: string;
  execution_id?: string;
  stop_requested?: boolean;
  revision?: number;
  version?: number;
  text?: string;
  status?: string;
  phase?: string;
  budget?: {
    max_tokens?: number | null;
    max_elapsed_s?: number | null;
    max_cost_usd?: number | null;
  };
  usage?: {
    accounting_pending?: boolean;
    tokens_known?: boolean;
    total_tokens?: number;
    cost_usd?: number;
    cost_known?: boolean;
    unknown_token_requests?: number;
    unknown_cost_requests?: number;
    active_elapsed_s?: number;
    active_time_known?: boolean;
  };
  checklist?: { text: string; done: boolean }[] | null;
  last_reason?: string;
  recoverable?: boolean;
  pause_reason?: string;
}

const terminalStatuses = new Set(["achieved", "impossible", "cancelled", "cleared"]);

function controlReason(reason: string | null | undefined, zh: boolean): string {
  const messages: Record<string, [string, string]> = {
    active_owner: ["Another turn is running. Wait for it to stop before continuing this Goal.", "另一个轮次正在运行，请等待其结束后再继续此 Goal。"],
    budget_exhausted: ["The explicit budget is exhausted. Change the Goal budget before continuing.", "已达到显式预算，请修改 Goal 预算后再继续。"],
    elapsed_time_unknown: ["Interrupted active time is unknown, so the explicit time limit cannot be verified. Remove the time limit to continue.", "中断时段的执行时间未知，无法核实显式时间上限；移除时间上限后可继续。"],
    execution_unavailable: ["Execution status is unavailable. Refresh status to retry.", "无法读取执行状态，请刷新状态重试。"],
    unknown_external_effects: ["Operation outcomes must be checked before verification.", "验收前需要确认外部操作的结果。"],
    unfinished_conversation_execution: ["Related executions have not finished.", "关联执行尚未结束。"],
    managed_work_unfinished: ["Goal-owned background work is still running or its outcome is unknown.", "此 Goal 的后台工作仍在运行，或其结果未知。"],
    pending_wait: ["Answer the pending approval or question in the execution details.", "请在执行详情中处理待答审批或问题。"],
    active_children: ["Child executions are still active. Inspect their status below.", "子任务仍未结束，请在下方查看其状态。"],
    unfinished_todos: ["Complete the remaining todos before verification.", "请先完成剩余待办，再进行验收。"],
    no_completed_work: ["Verification requires a finished working turn.", "验收需要先有已结束的工作轮次。"],
    ordinary_chat_required: ["Verification is available for chat Goals.", "此验收入口适用于聊天 Goal。"],
    execution_not_finished: ["The previous execution has not stopped.", "上一轮执行尚未停止。"],
    goal_not_resumable: ["The Goal is not paused.", "Goal 当前未暂停。"],
    goal_terminal: ["The Goal has ended.", "Goal 已结束。"],
  };
  return reason ? messages[reason]?.[zh ? 1 : 0] ?? reason : "";
}

/** Why a Goal stopped, from the structured pause_reason; the raw last_reason
 *  is the fallback and the detail. */
function stopReason(goal: GoalState, zh: boolean): string {
  const raw = goal.last_reason ?? "";
  const t = (en: string, cn: string) => (zh ? cn : en);
  if (goal.status === "active") return raw;
  switch (goal.pause_reason) {
    case "user": return t("Paused by you.", "你暂停了此 Goal。");
    case "conversation_archived": return t("The conversation was archived. Unarchiving it continues the Goal.", "对话已归档。取消归档后 Goal 会继续。");
    case "always_on_off": return t("Always-on was turned off. Turning it on again continues the Goal.", "常驻已关闭。重新开启后 Goal 会继续。");
    case "edited": return t("The Goal was edited. Resume to work on the new revision.", "目标已修改，继续后按新版本执行。");
    case "idle": {
      const turns = Number.parseInt(raw, 10) || 2;
      return t(`${turns} automatic turns in a row called no tool, so the Goal stopped. Resume to try again, or edit the Goal.`,
        `连续 ${turns} 个自动轮次没有调用任何工具，Goal 已停下。可以继续重试，或修改目标。`);
    }
    case "execution_failed": return t(`The last turn did not finish (${raw}). Resume starts a new turn.`, `上一轮没有正常结束（${raw}），继续会开始新的轮次。`);
    case "plan_mode": return t("A Goal does not continue on its own in Plan mode. Leave Plan mode, then resume.", "Plan 模式下 Goal 不会自动续跑。退出 Plan 模式后再继续。");
    case "verification_blocked": {
      const code = raw.replace(/^Completion cannot be verified:\s*/, "");
      return `${t("Completion cannot be verified yet", "暂时无法验收")}: ${controlReason(code, zh)}`;
    }
    case "restart_policy": return t("OpenProgram restarted, and automatic continuation is off or its time window has passed. Resume to continue.", "OpenProgram 重启后，自动续跑已关闭或超出时限，请手动继续。");
    case "restart_provenance_unknown": return t("This saved Goal has no record of where it last ran. Resume to continue.", "这个 Goal 缺少上次运行位置的记录，请手动继续。");
    default: return raw;
  }
}

function formatElapsed(seconds: number | undefined): string {
  const total = Math.max(0, Math.round(seconds ?? 0));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const rest = total % 60;
  return hours ? `${hours}h ${minutes}m` : minutes ? `${minutes}m ${rest}s` : `${rest}s`;
}

function readGoalFromRuntime(sid: string | null): GoalState | null {
  if (!sid) return null;
  const conv = runtimeState.conversations[sid] as { goal?: GoalState | null } | undefined;
  return conv?.goal ?? null;
}

export function useSessionGoal(sessionId: string | null): GoalState | null {
  const [snapshot, setSnapshot] = useState(() => ({ sessionId, goal: readGoalFromRuntime(sessionId) }));
  useEffect(() => setSnapshot({ sessionId, goal: readGoalFromRuntime(sessionId) }), [sessionId]);
  useEffect(() => {
    const onGoalState = (event: Event) => {
      const detail = (event as CustomEvent).detail as { session_id?: string; goal?: GoalState | null } | undefined;
      if (detail?.session_id === sessionId) setSnapshot({ sessionId, goal: detail.goal ?? null });
    };
    const onWsMessage = (event: Event) => {
      const detail = (event as CustomEvent).detail as
        | { type?: string; data?: { session_id?: string; goal?: GoalState } }
        | undefined;
      if (detail?.type === "goal_update" && detail.data?.session_id) {
        updateSessionGoal(detail.data.session_id, detail.data.goal ?? null);
      }
    };
    window.addEventListener("op:goal-state", onGoalState);
    window.addEventListener("op:ws-message", onWsMessage);
    return () => {
      window.removeEventListener("op:goal-state", onGoalState);
      window.removeEventListener("op:ws-message", onWsMessage);
    };
  }, [sessionId]);
  return snapshot.sessionId === sessionId ? snapshot.goal : readGoalFromRuntime(sessionId);
}

function useGoalDraft<T>(source: T, revision: number) {
  const [draft, setDraft] = useState({ base: source, value: source, revision });
  const dirty = JSON.stringify(draft.value) !== JSON.stringify(draft.base);
  const changed = revision !== draft.revision || JSON.stringify(source) !== JSON.stringify(draft.base);
  useEffect(() => {
    if (!dirty && changed) setDraft({ base: source, value: source, revision });
  }, [source, revision, dirty, changed]);
  return {
    value: draft.value, dirty, conflict: dirty && changed,
    set: (value: T) => setDraft((current) => ({ ...current, value })),
    reset: () => setDraft({ base: source, value: source, revision }),
  };
}

function statusLabel(status: string | undefined, zh: boolean, phase?: string) {
  if (status === "active" && phase === "verifying") return zh ? "验收中" : "Verifying";
  const labels: Record<string, [string, string]> = {
    refining: ["Refining", "完善中"], active: ["Active", "进行中"],
    running: ["Running", "执行中"], evaluating: ["Evaluating", "判定中"],
    paused: ["Paused", "已暂停"], paused_recoverable: ["Paused", "已暂停"],
    waiting_user: ["Waiting for you", "等待回答"], waiting_external: ["Waiting externally", "等待外部事件"],
    blocked: ["Blocked", "已阻塞"], stalled: ["Stalled", "无进展"],
    budget_exhausted: ["Budget exhausted", "预算已用尽"], achieved: ["Achieved", "已达成"],
    impossible: ["Impossible", "当前约束下不可完成"], failed: ["Failed", "执行失败"],
    cancelled: ["Cancelled", "已终止"], cleared: ["Cleared", "已清除"],
  };
  const pair = labels[status || ""];
  return pair ? pair[zh ? 1 : 0] : status || (zh ? "未知" : "Unknown");
}

export function GoalChip() {
  const sessionId = useSessionStore((state) => state.currentSessionId);
  const goal = useSessionGoal(sessionId);
  if (!sessionId || !goal) return null;
  return <GoalDetails key={`${sessionId}:${goal.goal_id || "legacy"}`} sessionId={sessionId} goal={goal} />;
}

function useGoalExecution(sessionId: string, goal: GoalState, enabled: boolean) {
  const connection = useSessionStore((state) => state.wsStatus);
  const identity = `${sessionId}:${goal.run_id}:${goal.execution_id}:${goal.version}`;
  const [observation, setObservation] = useState<{ controls?: GoalControls; identity?: string; status?: string; finished?: boolean | null; can_start_new_turn?: boolean; provider_response_incomplete?: boolean; recovery_mode?: string | null; recovery_reason?: string | null; fresh: boolean }>({ fresh: false });
  const request = useRef(0);
  const controller = useRef<AbortController>();
  const refresh = useCallback(async () => {
    const serial = ++request.current;
    controller.current?.abort();
    const own = controller.current = new AbortController();
    setObservation((v) => ({ ...v, fresh: false }));
    if (!enabled || connection !== "open") return;
    try {
      const result = await api.getGoal(sessionId, AbortSignal.any([own.signal, AbortSignal.timeout(10000)]));
      if (serial !== request.current || own.signal.aborted) return;
      if (result.goal.goal_id !== goal.goal_id
        || Number(result.goal.version ?? 0) < Number(goal.version ?? 0)) return;
      updateSessionGoal(sessionId, result.goal);
      // A newer canonical run refreshes the Goal first; its own keyed request
      // will load controls. Never attach old-run eligibility to the new run.
      if ((result.execution?.execution_id || null) !== (goal.execution_id || null)
        || result.goal.run_id !== goal.run_id) return;
      setObservation({ ...result.execution, controls: result.controls, identity, fresh: true });
    } catch {
      // Keep the Goal and its editor; a read failure is not a stopped execution.
    }
  }, [sessionId, goal.goal_id, goal.run_id, goal.execution_id, goal.version, identity, enabled, connection]);
  useEffect(() => {
    const requests = request;
    void refresh();
    const onExecution = (event: Event) => {
      const execution = (event as CustomEvent).detail?.execution;
      if (execution?.session_id === sessionId) void refresh();
    };
    window.addEventListener("op:execution-update", onExecution);
    return () => { requests.current++; controller.current?.abort(); window.removeEventListener("op:execution-update", onExecution); };
  }, [refresh, sessionId]);
  return { ...observation, fresh: observation.fresh && observation.identity === identity && connection === "open", refresh };
}

export function GoalDetails({ sessionId, goal, historical = false }: { sessionId: string; goal: GoalState; historical?: boolean }) {
  const { locale, text } = useTranslation();
  const zh = locale.startsWith("zh");
  const [open, setOpen] = useState(false);
  const [confirmCancel, setConfirmCancel] = useState(false);
  const draft = useGoalDraft(goal.text ?? "", goal.revision ?? 1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [stopError, setStopError] = useState("");
  const [inspection, setInspection] = useState<string | null>(null);
  const pending = useRef(false);
  const mounted = useRef(true);
  const trigger = useRef<HTMLButtonElement>(null);
  const endButton = useRef<HTMLButtonElement>(null);
  const hadConfirmation = useRef(false);
  useEffect(() => {
    if (hadConfirmation.current && !confirmCancel) endButton.current?.focus();
    hadConfirmation.current = confirmCancel;
  }, [confirmCancel]);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);

  const checklist = goal.checklist ?? [];
  const done = checklist.filter((item) => item.done).length;
  const progress = checklist.length
    ? `${text("Todos", "待办")} ${done}/${checklist.length}`
    : null;
  const completionIcon = useActionIconAnimation();
  const elapsed = goal.usage?.active_elapsed_s;
  const hasElapsed = typeof elapsed === "number" && Number.isFinite(elapsed) && elapsed >= 0;
  const elapsedLabel = goal.usage?.active_time_known === false
    ? text(`at least ${formatElapsed(elapsed)}`, `至少 ${formatElapsed(elapsed)}`) : formatElapsed(elapsed);
  const completionLabel = goal.status === "achieved"
    ? hasElapsed ? text(`Goal achieved in ${elapsedLabel}`, `目标已达成，用时 ${elapsedLabel}`) : text("Goal achieved", "目标已达成")
    : `${text("Goal", "目标")} · ${statusLabel(goal.status, zh, goal.phase)}`;
  const terminal = terminalStatuses.has(goal.status || "");
  const budget = goal.budget ?? {};
  const reason = stopReason(goal, zh);
  const unsaved = draft.dirty;
  const execution = useGoalExecution(sessionId, goal, !historical && (open || !!goal.stop_requested));
  const controls = execution.controls;
  const stopped = execution.fresh && execution.finished === true;
  const canResume = execution.fresh && controls?.can_resume === true;
  const stopPending = !!goal.stop_requested && !!goal.execution_id && !stopped;
  const executionLabel = !goal.execution_id ? text("Stop takes effect at the next Goal boundary; no execution record.", "停止在下一个 Goal 边界生效；无执行记录。")
    : !execution.fresh || execution.status === "unavailable" ? text("Execution status unknown", "执行状态未知")
    : execution.status === "running" && controls?.can_pause && !goal.stop_requested ? text("Execution running", "执行正在运行")
    : execution.recovery_mode === "restricted_new_turn" && canResume ? text("Previous action outcomes are unknown. Resume starts a new turn to inspect current state; new side effects require confirmation.", "旧操作结果未知。继续会开始新轮次检查实际状态；新的副作用操作需要确认。")
    : execution.recovery_reason === "pending_wait" ? text("Answer the pending approval or question in Activity before resuming.", "请先在 Activity 中处理待办审批或问题，再继续。")
    : execution.recovery_reason === "active_children" ? text("Child executions are still active. Inspect their status in Activity.", "子任务仍未结束，请在 Activity 中检查其状态。")
    : stopped ? text("Execution stopped", "执行已停止")
    : execution.provider_response_incomplete && canResume ? text("Previous model response is incomplete. Resume starts a new chat turn without repeating external actions.", "上一轮模型响应记录不完整。继续将开始新聊天轮次，不自动重放外部操作。")
    : execution.status === "reconciliation_required" ? text("An action result needs confirmation. Inspect the execution in Activity before retrying; Resume is disabled to prevent duplicate actions.", "操作结果需要确认。请在 Activity 中检查执行记录后再重试；继续暂不可用，以防重复执行。")
    : execution.status === "paused" ? text("The previous execution is paused. Continue or stop it in Activity before starting a new Goal turn.", "旧执行仍处于暂停状态。请先在 Activity 中继续或停止旧执行，再启动新的 Goal 轮次。")
    : execution.status === "cancelling" ? text("Stopping", "正在停止")
    : text("Stop not confirmed", "停止未确认");
  useEffect(() => { if (terminal) setConfirmCancel(false); }, [terminal]);
  if (!historical && terminal && !stopPending && !open) return null;

  async function mutate(action: string, values: Record<string, unknown> = {}) {
    if (historical) return;
    if (pending.current || (action === "edit" && draft.conflict) || (["resume", "verify"].includes(action) && unsaved)) return;
    pending.current = true;
    setBusy(true);
    setError("");
    setStopError("");
    try {
      const result = await api.mutateGoal(sessionId, { action, ...values, expected: {
        goal_id: goal.goal_id ?? "", revision: goal.revision ?? 1,
        run_id: goal.run_id ?? "", version: goal.version ?? 0,
      } });
      updateSessionGoal(sessionId, result.goal);
      if (mounted.current) {
        if (action === "edit") draft.reset();
      }
      if (mounted.current) void execution.refresh();
      if (result.stop_error) {
        if (mounted.current) setStopError(result.stop_error);
        return;
      }
    } catch (cause) {
      if (mounted.current) setError(cause instanceof Error ? cause.message : String(cause));
      if (cause instanceof HttpError && cause.status === 409) {
        try {
          const latest = await api.getGoal(sessionId);
          updateSessionGoal(sessionId, latest.goal);
        } catch {
          if (mounted.current) setError(text("The request conflicted and the latest Goal could not be loaded. Your draft is preserved.", "请求冲突，且无法读取最新目标；草稿已保留。"));
        }
      }
    } finally {
      pending.current = false;
      if (mounted.current) setBusy(false);
    }
  }

  const goalTip = (
    <TipBody
      title={text("Goal", "目标")}
      detail={<>{goal.text}<br />{text("Click for progress and controls", "点击查看进度与控制")}</>}
    />
  );

  return (
    <>
      {historical ? <HoverTip label={goalTip}><Button ref={trigger} type="button" variant="ghost" size="xs" className="mt-[8px] mb-[18px] -ml-[8px] max-w-full"
        data-goal-id={goal.goal_id} aria-label={text("Open Goal details", "打开 Goal 详情")}
        onClick={() => setOpen(true)} {...completionIcon.handlers}>
        {goal.status === "achieved" ? <CheckIcon ref={completionIcon.ref} size={16} aria-hidden />
          : <HistoryIcon ref={completionIcon.ref} size={16} aria-hidden />}
        <span>{completionLabel}</span>
      </Button></HoverTip> : !terminal || stopPending ? <HoverTip label={goalTip}><button
        ref={trigger}
        type="button"
        className={`runtime-badge workdir-badge ${styles.trigger}`}
        onClick={() => setOpen(true)}
        aria-label={text("Open Goal details", "打开 Goal 详情")}
      >
        <SolarIcon name="target" size={16} fit="fill" className="workdir-icon" />
        <span className="badge-short">Goal · {stopPending ? text("Stop not confirmed", "停止未确认") : statusLabel(goal.status, zh, goal.phase)}{progress ? ` · ${progress}` : null}</span>
      </button></HoverTip> : null}
      <Dialog open={open} onOpenChange={(value) => { setOpen(value); if (!value) setConfirmCancel(false); }}>
        <DialogContent className={styles.dialog} aria-busy={busy} onCloseAutoFocus={(event) => {
          event.preventDefault(); trigger.current?.focus();
        }} onEscapeKeyDown={(event) => {
          if (confirmCancel) { event.preventDefault(); setConfirmCancel(false); }
        }}>
          <DialogHeader>
            <DialogTitle>{text("Goal details", "Goal 详情")}</DialogTitle>
            <DialogDescription>
              {goal.goal_id ? `${goal.goal_id.slice(0, 8)} · ` : ""}
              {text("revision", "修订")} {goal.revision ?? 1} · version {goal.version ?? 0}
            </DialogDescription>
          </DialogHeader>

          {!historical ? <section className={styles.reason} aria-label={text("Execution stop status", "执行停止状态")}>
            <p role="status">{executionLabel}</p>
            <Button variant="elevated" disabled={busy} onClick={() => void execution.refresh()}>{text("Refresh status", "刷新状态")}</Button>
            {goal.execution_id ? <Button variant="elevated" onClick={() => setInspection(goal.execution_id!)}>{text("Inspect execution", "查看执行详情")}</Button> : null}
            {stopPending ? <Button variant="elevated" disabled={busy} onClick={() => void mutate("stop")}>{text("Retry stop", "重试停止")}</Button> : null}
          </section> : null}

          <label className={styles.field}>
            <span>{text("Goal", "目标")}</span>
            <textarea readOnly={historical} disabled={busy} value={draft.value} onChange={(event) => draft.set(event.target.value)} rows={4} />
          </label>
          {draft.conflict ? <div role="status" className={styles.reason}>
            <p>{text("The goal changed elsewhere. Your unsaved text is preserved.", "目标已在其他位置修改，未保存的正文已保留。")}</p>
            <Button variant="elevated" disabled={busy} onClick={draft.reset}>{text("Use latest goal", "采用最新目标")}</Button>
          </div> : null}

          <div className={`${styles.metrics} grid grid-cols-[repeat(auto-fit,_minmax(96px,_1fr))] gap-[8px]`}>
            <div><span>{text("Status", "状态")}</span><strong>{statusLabel(goal.status, zh, goal.phase)}</strong></div>
            {progress ? <div><span>{text("Progress", "进度")}</span><strong>{progress}</strong></div> : null}
            <div><span>{text("Tokens", "Token")}</span><strong>{goal.usage?.unknown_token_requests
              ? text(`Known ${goal.usage.total_tokens ?? 0}; ${goal.usage.unknown_token_requests} request(s) unknown`, `已知 ${goal.usage.total_tokens ?? 0}；另有 ${goal.usage.unknown_token_requests} 次请求未知`)
              : goal.usage?.tokens_known === false ? text("Unknown", "未知") : goal.usage?.total_tokens ?? 0}{limitSuffix(budget.max_tokens)}</strong></div>
            <div><span>{text("Cost", "成本")}</span><strong>{goal.usage?.unknown_cost_requests && Number.isFinite(goal.usage.cost_usd)
              ? text(`Known $${goal.usage.cost_usd!.toFixed(4)}; ${goal.usage.unknown_cost_requests} request(s) unknown`, `已知 $${goal.usage.cost_usd!.toFixed(4)}；另有 ${goal.usage.unknown_cost_requests} 次请求未知`)
              : goal.usage?.cost_known === true && Number.isFinite(goal.usage.cost_usd)
                ? `$${goal.usage.cost_usd!.toFixed(4)}` : text("Unknown", "未知")}{limitSuffix(budget.max_cost_usd, (v) => `$${v}`)}</strong></div>
            <div><span>{text("Active time", "执行时间")}</span><strong>{goal.usage?.active_time_known === false
              ? text(`At least ${formatElapsed(goal.usage.active_elapsed_s)}; interrupted interval unknown`, `至少 ${formatElapsed(goal.usage.active_elapsed_s)}；中断时段未知`)
              : formatElapsed(goal.usage?.active_elapsed_s)}{limitSuffix(budget.max_elapsed_s, formatElapsed)}</strong></div>
          </div>


          {goal.usage?.accounting_pending ? <p role="status">{text("Usage from the interrupted run has not been reconciled. Unknown is not zero; session totals must not be treated as Goal costs.", "中断执行的用量尚未核对。未知不代表零，不能把整个会话的费用当作此 Goal 费用。")}</p> : null}

          {reason ? <p className={styles.reason}>{reason}</p> : null}
          {checklist.length ? (
            <ul className={styles.checklist}>
              {checklist.map((item, index) => <li key={`${index}:${item.text}`} data-done={item.done}>{item.done ? "✓" : "○"} {item.text}</li>)}
            </ul>
          ) : null}
          {controls?.operations?.length ? <section aria-label={text("Unresolved operations", "结果未确认的操作")} className={styles.operations}>
            <strong>{text("Unresolved operations", "结果未确认的操作")}</strong>
            <p>{text("These results are unknown, not cancelled. Inspect the saved execution before repeating an action.", "这些结果尚未确认，不代表已取消。重复操作前请检查保存的执行记录。")}</p>
            {controls.operations.map((operation) => <div key={operation.effect_id}>
              <span>{operation.tool_name || text("Operation", "操作")} · {operation.status}</span>
              <code>{operation.effect_id}</code>
              <span>{new Date((operation.dispatched_at ?? operation.created_at) * 1000).toLocaleString(locale)}</span>
              <Button variant="elevated" onClick={() => setInspection(operation.execution_id)}>{text("Inspect execution", "查看执行详情")}</Button>
            </div>)}
          </section> : null}
          {controls?.waits?.map((wait) => <Button key={wait.wait_id} variant="elevated" onClick={() => setInspection(wait.execution_id)}>{text("Pending response", "待答事项")}: {wait.kind} · {wait.wait_id}</Button>)}
          {controls?.active_children?.map((id) => <Button key={id} variant="elevated" onClick={() => setInspection(id)}>{text("Inspect child", "查看子任务")}: {id}</Button>)}
          {controls?.processes?.map((process) => <div className={styles.operations} key={process.id}>
            <span>{text("Background work", "后台工作")}: {process.status}</span><code>{process.id}</code>
            <Button variant="elevated" onClick={() => setInspection(process.execution_id)}>{text("Inspect execution", "查看执行详情")}</Button>
          </div>)}
          {inspection ? <section className={styles.inspector} aria-label={text("Selected execution", "所选执行")}>
            <Button variant="elevated" onClick={() => setInspection(null)}>{text("Close execution details", "关闭执行详情")}</Button>
            <SessionDebugger key={`${sessionId}:${inspection}`} sessionId={sessionId} active={open} requestedExecutionId={inspection} />
          </section> : null}
          {!historical && !terminal ? <div className={styles.draftNotice} role="status">
            {!execution.fresh || !controls ? <p>{text("Action status unavailable. Refresh status to retry.", "无法读取操作状态，请刷新状态重试。")}</p> : <>
              {controls.reasons.resume && controls.reasons.resume !== "goal_not_resumable" ? <p>{text("Resume", "继续")}: {controlReason(controls.reasons.resume, zh)}</p> : null}
              {controls.reasons.verify ? <p>{text("Verify", "验收")}: {controlReason(controls.reasons.verify, zh)}</p> : null}
            </>}
          </div> : null}
          {error ? <p className={styles.error} role="alert">{error}</p> : null}
          {stopError && !stopped ? <p className={styles.error} role="alert">{stopError}</p> : null}

          {historical ? null : confirmCancel ? <section aria-label={text("End Goal confirmation", "终止目标确认")}>
            <p>{text("End this Goal? Saved work and history will remain.", "终止此目标？已保存的工作和历史记录会保留。")}</p>
            <DialogFooter className={styles.actions}>
              <Button autoFocus variant="elevated" disabled={busy} onClick={() => setConfirmCancel(false)}>{text("Keep goal", "保留目标")}</Button>
              <Button variant="destructive" disabled={busy} onClick={() => void mutate("cancel")}>{text("Confirm end", "确认终止")}</Button>
            </DialogFooter>
          </section> : <DialogFooter className={styles.actions}>
            {!terminal ? <Button ref={endButton} variant="destructive" disabled={busy || (execution.fresh && controls?.can_end === false)} onClick={() => setConfirmCancel(true)}><FillIcon><Square /></FillIcon>{text("End", "终止")}</Button> : null}
            {controls?.can_pause ? <Button variant="elevated" disabled={busy || !execution.fresh} onClick={() => void mutate("pause")}><FillIcon><Pause /></FillIcon>{text("Pause", "暂停")}</Button> : null}
            <Button variant="elevated" disabled={busy || !draft.dirty || draft.conflict || !draft.value.trim()} onClick={() => void mutate("edit", { prompt: draft.value.trim() })}>{text("Save edit", "保存修改")}</Button>
            {!terminal ? <>
              <Button disabled={busy || unsaved || !canResume} onClick={() => void mutate("resume")}><FillIcon><Play /></FillIcon>{text("Resume", "继续")}</Button>
              <Button disabled={busy || unsaved || !execution.fresh || !controls?.can_verify} onClick={() => void mutate("verify")}><FillIcon><Check /></FillIcon>{text("Verify", "验收")}</Button>
            </> : null}
          </DialogFooter>}
          {unsaved ? <p className={styles.draftNotice} role="status">{text("Save or discard unsaved changes before resuming.", "继续前请保存或放弃未保存的修改。")}
            <Button variant="ghost" disabled={busy} onClick={() => { draft.reset(); }}>{text("Discard changes", "放弃修改")}</Button>
          </p> : null}
        </DialogContent>
      </Dialog>
    </>
  );
}

/** " / limit" after a used amount; nothing when the Goal has no such limit. */
function limitSuffix(limit: number | null | undefined, format: (value: number) => string = String): string {
  return limit ? ` / ${format(limit)}` : "";
}

