"use client";

import { useEffect, useState } from "react";
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
import { runtimeState } from "@/lib/runtime-bridge/state";
import { updateSessionGoal } from "@/lib/runtime-bridge/goal-state";
import { useSessionStore } from "@/lib/session-store";
import { useTranslation } from "@/lib/i18n";

import styles from "./goal-chip.module.css";

export interface GoalState {
  schema_version?: number;
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
  const [goal, setGoal] = useState<GoalState | null>(() => readGoalFromRuntime(sessionId));
  useEffect(() => setGoal(readGoalFromRuntime(sessionId)), [sessionId]);
  useEffect(() => {
    const onGoalState = (event: Event) => {
      const detail = (event as CustomEvent).detail as { session_id?: string; goal?: GoalState | null } | undefined;
      if (detail?.session_id === sessionId) setGoal(detail.goal ?? null);
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
  return goal;
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
  const { locale, text } = useTranslation();
  const zh = locale.startsWith("zh");
  const sessionId = useSessionStore((state) => state.currentSessionId);
  const goal = useSessionGoal(sessionId);
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const [limits, setLimits] = useState<BudgetDraft>(emptyBudget);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => setDraft(goal?.text ?? ""), [goal?.text, goal?.revision]);
  useEffect(() => setLimits(budgetDraft(goal)), [goal?.version]);
  // Terminal goals remain in execution history, not in the active composer.
  if (!goal || terminalStatuses.has(goal.status || "")) return null;

  const checklist = goal.checklist ?? [];
  const pendingQuestions = (goal.questions ?? []).filter((item) => item.status === "pending");
  const done = checklist.filter((item) => item.done).length;
  const progress = checklist.length
    ? `${done}/${checklist.length}`
    : `${goal.turns_used ?? 0}${goal.max_turns ? `/${goal.max_turns}` : ""}`;
  const running = runningStatuses.has(goal.status || "");
  const resumable = resumableStatuses.has(goal.status || "");

  async function mutate(action: string, values: Record<string, unknown> = {}) {
    if (!sessionId) return;
    setBusy(true);
    setError("");
    try {
      const result = await api.mutateGoal(sessionId, { action, ...values });
      updateSessionGoal(sessionId, result.goal);
      if ((action === "resume" || action === "answer") && result.invoke) {
        await api.runFunction(result.invoke.name, { ...result.invoke.kwargs, session_id: sessionId });
      }
      if (action === "cancel") setOpen(false);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <button
        type="button"
        className={`runtime-badge workdir-badge ${styles.trigger}`}
        onClick={() => setOpen(true)}
        aria-label={text("Open Goal details", "打开 Goal 详情")}
      >
        <Target size={14} strokeWidth={2} className="workdir-icon" />
        <span className="badge-short">Goal · {statusLabel(goal.status, zh)} · {progress}</span>
      </button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className={styles.dialog}>
          <DialogHeader>
            <DialogTitle>{text("Goal details", "Goal 详情")}</DialogTitle>
            <DialogDescription>
              {goal.goal_id ? `${goal.goal_id.slice(0, 8)} · ` : ""}
              {text("revision", "修订")} {goal.revision ?? 1} · version {goal.version ?? 0}
            </DialogDescription>
          </DialogHeader>

          <label className={styles.field}>
            <span>{text("Goal", "目标")}</span>
            <textarea value={draft} onChange={(event) => setDraft(event.target.value)} rows={4} />
          </label>

          <div className={styles.metrics}>
            <div><span>{text("Status", "状态")}</span><strong>{statusLabel(goal.status, zh)}</strong></div>
            <div><span>{text("Progress", "进度")}</span><strong>{progress}</strong></div>
            <div><span>{text("Tokens", "Token")}</span><strong>{goal.usage?.total_tokens ?? 0}</strong></div>
            <div><span>{text("Cost", "成本")}</span><strong>${(goal.usage?.cost_usd ?? 0).toFixed(4)}</strong></div>
            <div><span>{text("Active time", "执行时间")}</span><strong>{formatElapsed(goal.usage?.active_elapsed_s)}</strong></div>
          </div>

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
                    min="0"
                    step={key === "max_cost_usd" ? "0.01" : "1"}
                    inputMode="decimal"
                    value={limits[key]}
                    placeholder={text("No limit", "无限制")}
                    onChange={(event) => setLimits((current) => ({
                      ...current,
                      [key]: event.target.value,
                    }))}
                  />
                </label>
              ))}
            </div>
            <Button
              variant="outline"
              disabled={busy}
              onClick={() => void mutate("budget", limits)}
            >{text("Save limits", "保存限制")}</Button>
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
                            onClick={() => setAnswers((current) => ({ ...current, [question.id]: option.label }))}
                          >{option.label}</button>
                        ))}
                      </div>
                    ) : null}
                    <textarea
                      value={answer}
                      onChange={(event) => setAnswers((current) => ({ ...current, [question.id]: event.target.value }))}
                      rows={2}
                      placeholder={text("Answer this question when convenient", "方便时回答这个问题")}
                    />
                    <Button
                      disabled={busy || !answer.trim()}
                      onClick={() => void mutate("answer", { question_id: question.id, answer: answer.trim() })}
                    >
                      <Play size={14} />{goal.status === "waiting_user" ? text("Answer and resume", "回答并继续") : text("Submit answer", "提交回答")}
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

          <DialogFooter className={styles.actions}>
            {!terminalStatuses.has(goal.status || "") ? <Button variant="destructive" disabled={busy} onClick={() => void mutate("cancel")}><Square size={14} />{text("End", "终止")}</Button> : null}
            {running ? <Button variant="outline" disabled={busy} onClick={() => void mutate("pause")}><Pause size={14} />{text("Pause", "暂停")}</Button> : null}
            <Button variant="outline" disabled={busy || draft.trim() === (goal.text || "").trim() || !draft.trim()} onClick={() => void mutate("edit", { prompt: draft.trim() })}>{text("Save edit", "保存修改")}</Button>
            {resumable ? <Button disabled={busy} onClick={() => void mutate("resume")}><Play size={14} />{text("Resume", "继续")}</Button> : null}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
