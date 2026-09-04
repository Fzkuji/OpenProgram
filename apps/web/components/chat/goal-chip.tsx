"use client";

import { useEffect, useRef, useState } from "react";
import { Pause, Play, Square, Target } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { api } from "@/lib/net/api";
import { HttpError } from "@/lib/net/fetch-client";
import { runtimeState } from "@/lib/runtime-bridge/state";
import { updateSessionGoal } from "@/lib/runtime-bridge/goal-state";
import { useSessionStore } from "@/lib/session-store";
import { useTranslation } from "@/lib/i18n";

import styles from "./goal-chip.module.css";

export interface GoalState {
  schema_version?: number;
  roles?: Record<"work" | "judge", {
    provider: string;
    model: string;
    model_provider: string;
    effort: string;
    timeout_s: number;
  }>;
  roles_origin?: string;
  goal_id?: string;
  run_id?: string;
  revision?: number;
  version?: number;
  text?: string;
  spec?: string;
  status?: string;
  phase?: string;
  turns_used?: number;
  max_turns?: number;
  budget?: {
    max_turns?: number | null;
    max_tokens?: number | null;
    max_elapsed_s?: number | null;
    max_cost_usd?: number | null;
  };
  usage?: {
    total_tokens?: number;
    cost_usd?: number;
    cost_known?: boolean;
    active_elapsed_s?: number;
  };
  checkpoint?: { phase?: string; round?: number; at?: number };
  checklist?: { text: string; done: boolean }[] | null;
  last_reason?: string;
  last_question?: string;
  last_question_at?: number;
  last_question_options?: { label: string; description: string }[];
  questions?: {
    id: string;
    prompt: string;
    reason?: string;
    status: "pending" | "answered" | "superseded";
    options?: { label: string; description?: string }[];
    can_continue?: boolean;
  }[];
  interaction_mode?: "attended" | "unattended";
  recoverable?: boolean;
  pause_reason?: string;
}

const runningStatuses = new Set(["refining", "active", "running", "evaluating"]);
const terminalStatuses = new Set(["achieved", "impossible", "cancelled", "cleared"]);
const resumableStatuses = new Set([
  "paused", "paused_recoverable", "waiting_external", "blocked", "stalled",
  "budget_exhausted", "capped", "error", "failed",
]);

type BudgetDraft = Record<
  "max_turns" | "max_tokens" | "max_elapsed_s" | "max_cost_usd",
  string
>;

const emptyBudget: BudgetDraft = {
  max_turns: "",
  max_tokens: "",
  max_elapsed_s: "",
  max_cost_usd: "",
};

function budgetDraft(goal: GoalState | null): BudgetDraft {
  return Object.fromEntries(
    Object.keys(emptyBudget).map((key) => {
      const value = goal?.budget?.[key as keyof BudgetDraft];
      return [key, value == null ? "" : String(value)];
    }),
  ) as BudgetDraft;
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

function statusLabel(status: string | undefined, zh: boolean) {
  const labels: Record<string, [string, string]> = {
    refining: ["Refining", "完善中"], active: ["Active", "进行中"],
    running: ["Running", "执行中"], evaluating: ["Evaluating", "判定中"],
    paused: ["Paused", "已暂停"], paused_recoverable: ["Paused after restart", "重启后可继续"],
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

function GoalDetails({ sessionId, goal }: { sessionId: string; goal: GoalState }) {
  const { locale, text } = useTranslation();
  const zh = locale.startsWith("zh");
  const [open, setOpen] = useState(false);
  const [confirmCancel, setConfirmCancel] = useState(false);
  const draft = useGoalDraft(goal.text ?? "", goal.revision ?? 1);
  const limits = useGoalDraft(budgetDraft(goal), goal.revision ?? 1);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
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
  const pendingQuestions = (goal.questions ?? []).filter((item) => item.status === "pending");
  const done = checklist.filter((item) => item.done).length;
  const progress = checklist.length
    ? `${done}/${checklist.length}`
    : `${goal.turns_used ?? 0}${goal.max_turns ? `/${goal.max_turns}` : ""}`;
  const running = runningStatuses.has(goal.status || "");
  const resumable = resumableStatuses.has(goal.status || "");
  const terminal = terminalStatuses.has(goal.status || "");
  useEffect(() => { if (terminal) setConfirmCancel(false); }, [terminal]);
  if (terminal && !open) return null;

  async function mutate(action: string, values: Record<string, unknown> = {}) {
    if (pending.current || (action === "edit" && draft.conflict) || (action === "budget" && limits.conflict)
      || (action === "resume" && (draft.dirty || limits.dirty))) return;
    pending.current = true;
    setBusy(true);
    setError("");
    try {
      const result = await api.mutateGoal(sessionId, { action, ...values, expected: {
        goal_id: goal.goal_id ?? "", revision: goal.revision ?? 1,
        run_id: goal.run_id ?? "", version: goal.version ?? 0,
      } });
      updateSessionGoal(sessionId, result.goal);
      if (mounted.current) {
        if (action === "edit") draft.reset();
        if (action === "budget") limits.reset();
        if (action === "answer") setAnswers((current) => {
          const next = { ...current }; delete next[String(values.question_id)]; return next;
        });
      }
      if ((action === "resume" || action === "answer") && result.invoke && !draft.dirty && !limits.dirty) {
        const response = await api.runFunction(result.invoke.name, { ...result.invoke.kwargs, session_id: sessionId });
        if (response.error) throw new Error(response.error);
      }
      if (mounted.current && action === "cancel") setOpen(false);
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

  return (
    <>
      {!terminal ? <button
        ref={trigger}
        type="button"
        className={`runtime-badge workdir-badge ${styles.trigger}`}
        onClick={() => setOpen(true)}
        aria-label={text("Open Goal details", "打开 Goal 详情")}
      >
        <Target size={14} strokeWidth={2} className="workdir-icon" />
        <span className="badge-short">Goal · {statusLabel(goal.status, zh)} · {progress}</span>
      </button> : null}
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

          <label className={styles.field}>
            <span>{text("Goal", "目标")}</span>
            <textarea disabled={busy} value={draft.value} onChange={(event) => draft.set(event.target.value)} rows={4} />
          </label>
          {draft.conflict ? <div role="status" className={styles.reason}>
            <p>{text("The goal changed elsewhere. Your unsaved text is preserved.", "目标已在其他位置修改，未保存的正文已保留。")}</p>
            <Button variant="outline" disabled={busy} onClick={draft.reset}>{text("Use latest goal", "采用最新目标")}</Button>
          </div> : null}

          <div className={styles.metrics}>
            <div><span>{text("Status", "状态")}</span><strong>{statusLabel(goal.status, zh)}</strong></div>
            <div><span>{text("Progress", "进度")}</span><strong>{progress}</strong></div>
            <div><span>{text("Tokens", "Token")}</span><strong>{goal.usage?.total_tokens ?? 0}</strong></div>
            <div><span>{text("Cost", "成本")}</span><strong>{goal.usage?.cost_known === true && Number.isFinite(goal.usage.cost_usd)
              ? `$${goal.usage.cost_usd!.toFixed(4)}` : text("Unknown", "未知")}</strong></div>
            <div><span>{text("Active time", "执行时间")}</span><strong>{formatElapsed(goal.usage?.active_elapsed_s)}</strong></div>
          </div>

          {goal.roles ? <section className={styles.roles} aria-label={text("Goal roles", "Goal 角色")}>
            {(["work", "judge"] as const).map((name) => {
              const role = goal.roles![name];
              if (!role) return <div key={name}>{name}: {text("Unavailable", "不可用")}</div>;
              return <div key={name}>
                <span>{name === "work" ? text("Working agent", "工作 Agent") : text("Judge", "判定 Agent")}</span>
                <strong>{role.provider}/{role.model}</strong>
                <span>{role.effort} · {role.timeout_s}s</span>
              </div>;
            })}
            {goal.roles_origin === "legacy-resolved" ? <p>{text("Roles resolved on first resume of this legacy Goal.", "旧目标在首次恢复时解析角色配置。")}</p> : null}
          </section> : null}

          <details className={styles.budget}>
            <summary>{text("Execution limits", "执行限制")}</summary>
            <div className={styles.budgetGrid}>
              {([
                ["max_turns", text("Turns", "轮次")],
                ["max_tokens", "Tokens"],
                ["max_elapsed_s", text("Active seconds", "执行秒数")],
                ["max_cost_usd", text("Cost (USD)", "成本（USD）")],
              ] as const).map(([key, label]) => (
                <label key={key}>
                  <span>{label}</span>
                  <input
                    type="number"
                    disabled={busy}
                    min="0"
                    step={key === "max_cost_usd" ? "0.01" : "1"}
                    inputMode="decimal"
                    value={limits.value[key]}
                    placeholder={text("No limit", "无限制")}
                    onChange={(event) => limits.set({
                      ...limits.value,
                      [key]: event.target.value,
                    })}
                  />
                </label>
              ))}
            </div>
            <Button
              variant="outline"
              disabled={busy || !limits.dirty || limits.conflict}
              onClick={() => void mutate("budget", limits.value)}
            >{text("Save limits", "保存限制")}</Button>
            {limits.conflict ? <div role="status">
              <p>{text("Limits changed elsewhere. Your unsaved values are preserved.", "限制已在其他位置修改，未保存的数值已保留。")}</p>
              <Button variant="outline" disabled={busy} onClick={limits.reset}>{text("Use latest limits", "采用最新限制")}</Button>
            </div> : null}
          </details>

          {goal.last_reason ? <p className={styles.reason}>{goal.last_reason}</p> : null}
          {pendingQuestions.length ? (
            <section className={styles.questionQueue} aria-label={text("Pending Goal questions", "Goal 待答问题")}>
              <header>
                <strong>{text("Pending questions", "待答问题")} · {pendingQuestions.length}</strong>
                <span>{goal.interaction_mode === "attended" ? text("Attended · asynchronous", "有人值守 · 异步") : text("Unattended · asynchronous", "无人值守 · 异步")}</span>
              </header>
              {pendingQuestions.map((question) => {
                const answer = answers[question.id] ?? "";
                return (
                  <div className={styles.waiting} key={question.id}>
                    <strong>{question.prompt}</strong>
                    {question.reason ? <span>{question.reason}</span> : null}
                    {question.options?.length ? (
                      <div className={styles.answerOptions}>
                        {question.options.map((option) => (
                          <button
                            key={option.label}
                            type="button"
                            disabled={busy}
                            onClick={() => setAnswers((current) => ({ ...current, [question.id]: option.label }))}
                          >{option.label}</button>
                        ))}
                      </div>
                    ) : null}
                    <textarea
                      disabled={busy}
                      aria-label={`${text("Answer", "回答")}: ${question.prompt}`}
                      value={answer}
                      onChange={(event) => setAnswers((current) => ({ ...current, [question.id]: event.target.value }))}
                      rows={2}
                      placeholder={text("Answer this question when convenient", "方便时回答这个问题")}
                    />
                    <Button
                      disabled={busy || !answer.trim()}
                      onClick={() => void mutate("answer", { question_id: question.id, answer: answer.trim() })}
                    >
                      <Play size={14} />{goal.status === "waiting_user" && !draft.dirty && !limits.dirty ? text("Answer and resume", "回答并继续") : text("Submit answer", "提交回答")}
                    </Button>
                  </div>
                );
              })}
            </section>
          ) : null}
          {checklist.length ? (
            <ul className={styles.checklist}>
              {checklist.map((item, index) => <li key={`${index}:${item.text}`} data-done={item.done}>{item.done ? "✓" : "○"} {item.text}</li>)}
            </ul>
          ) : null}
          {error ? <p className={styles.error} role="alert">{error}</p> : null}

          {confirmCancel ? <section aria-label={text("End Goal confirmation", "终止目标确认")}>
            <p>{text("End this Goal? Saved work and history will remain.", "终止此目标？已保存的工作和历史记录会保留。")}</p>
            <DialogFooter className={styles.actions}>
              <Button autoFocus variant="outline" disabled={busy} onClick={() => setConfirmCancel(false)}>{text("Keep goal", "保留目标")}</Button>
              <Button variant="destructive" disabled={busy} onClick={() => void mutate("cancel")}>{text("Confirm end", "确认终止")}</Button>
            </DialogFooter>
          </section> : <DialogFooter className={styles.actions}>
            {!terminal ? <Button ref={endButton} variant="destructive" disabled={busy} onClick={() => setConfirmCancel(true)}><Square size={14} />{text("End", "终止")}</Button> : null}
            {running ? <Button variant="outline" disabled={busy} onClick={() => void mutate("pause")}><Pause size={14} />{text("Pause", "暂停")}</Button> : null}
            <Button variant="outline" disabled={busy || !draft.dirty || draft.conflict || !draft.value.trim()} onClick={() => void mutate("edit", { prompt: draft.value.trim() })}>{text("Save edit", "保存修改")}</Button>
            {resumable ? <Button disabled={busy || draft.dirty || limits.dirty} onClick={() => void mutate("resume")}><Play size={14} />{text("Resume", "继续")}</Button> : null}
          </DialogFooter>}
          {draft.dirty || limits.dirty ? <p className={styles.draftNotice} role="status">{text("Save or discard unsaved changes before resuming.", "继续前请保存或放弃未保存的修改。")}
            <Button variant="ghost" disabled={busy} onClick={() => { draft.reset(); limits.reset(); }}>{text("Discard changes", "放弃修改")}</Button>
          </p> : null}
        </DialogContent>
      </Dialog>
    </>
  );
}
