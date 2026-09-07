"use client";

import { approvalDisplayText, readSandboxEscalation, type SandboxEscalation } from "./approval-display-text";

/**
 * QuestionMode —— 一切「问用户」的唯一组件（不再分 single / multi / form /
 * approval 几套）。所有 runtime.ask / confirm / approval / form / ask_many
 * 都归一成「一组步骤 steps」：单题就是 1 步，ask_many 是 N 步，form 是 1 步
 * （那一步渲染字段表单）。一套外壳：
 *
 *   header：标题「需要你的输入」+ 进度点 ● ○ ○ + 几分之几（哪怕只有 1 步也
 *           显示 ● 1/1，统一）。
 *   body  ：当前步的内容（选项 / 表单字段 / 批准摘要 + 选项）。
 *   底部  ：经 onAction 报给 composer 的「‹ 上一题 / 下一题 ›」一组，最后一步
 *           「下一题」变「发送」。单步时只有一颗「发送」。
 *
 * 选项一律「只选中」（可再点取消、可切换），点底部按钮才推进 / 提交。
 * 底部右侧「Chat about this」位于发送左侧，打开反馈输入框，发送后才结束等待并继续讨论。
 *
 * 设计：docs/design/ui/composer-interaction-modes.md。
 */

import { useState } from "react";
import { ChevronRight } from "lucide-react";
import { OperationCode } from "./operation-code";
import { Textarea } from "@/components/ui/textarea";

import type { PendingDecision, AskOne, FormFieldSchema } from "@/lib/session-store";
import { useTranslation } from "@/lib/i18n";

import styles from "./question-mode.module.css";
import { useWaitAnswer } from "./use-wait-answer";
import multi from "./multi-ask-mode.module.css";
import approvalStyles from "../approval/approval-mode.module.css";
import formStyles from "./form-mode.module.css";

/** Wire value meaning "approved" in the approval protocol. Matches the
 *  locale-independent token accepted by ``_approval.await_user_approval``.
 *  Not user-visible — the button labels are translated separately. */
const APPROVE_ANSWER = "approve";

/** 提示语收尾：已是问号/冒号/句号等终止符就原样；否则补一个中文冒号「：」，
 *  让「请填写名字」这类祈使句读起来像在等你输入。 */
function withColon(s: string): string {
  const t = s.trimEnd();
  if (!t) return t;
  return /[?？:：。.!！]$/.test(t) ? t : t + "：";
}

interface QuestionModeProps {
  decision: PendingDecision;
  onResolve: (id: string) => void;
  onChatAbout: (feedback: string) => void | Promise<void>;
}

/** Wire pick for an approval card. ``always_path`` is sandbox-escalation only. */
type ApprovalPick = "once" | "always" | "always_path" | "deny";

/** 一步（一道题）的统一形状。kind 决定 body 怎么渲染、答案怎么收集。 */
type Step =
  | { kind: "choice"; prompt: string; options: string[]; multi: boolean; allowCustom: boolean }
  | {
      kind: "approval";
      prompt: string;
      detail?: string;
      risk?: "low" | "medium" | "high";
      escalation?: SandboxEscalation;
      allowedScopes?: string[];
      tool?: string;
      args?: Record<string, unknown>;
    }
  | { kind: "form"; prompt: string; detail?: string; schema: Record<string, FormFieldSchema> };

/** 一步的工作答案。choice → 选中集 + 自由文本；approval → 允许一次/总是允许/拒绝；
 *  form → 字段值对象。 */
type Answer =
  | { picked: Set<string>; custom: string }
  | { pick: ApprovalPick | null }
  | { fields: Record<string, string | number | boolean> };

/** 把一个 decision 拍平成统一的 steps 数组。单题 → 1 步；ask_many → N 步。 */
function toSteps(q: PendingDecision): Step[] {
  if (q.kind === "form") {
    return [{ kind: "form", prompt: q.prompt, detail: q.detail, schema: q.schema ?? {} }];
  }
  if (q.kind === "approval") {
    return [{
      kind: "approval",
      prompt: q.prompt,
      detail: q.detail,
      risk: q.risk_level,
      escalation: readSandboxEscalation(q.args),
      allowedScopes: q.allowedScopes,
      tool: q.tool,
      args: q.args,
    }];
  }
  if (q.kind === "ask_many") {
    const qs: AskOne[] = q.questions ?? [];
    return qs.map((one) => ({
      kind: "choice",
      prompt: one.prompt,
      options: one.options,
      multi: one.multi,
      allowCustom: one.allow_custom,
    }));
  }
  // ask / confirm —— 单题选择。
  return [{
    kind: "choice",
    prompt: q.prompt,
    options: q.options,
    multi: q.multi,
    allowCustom: q.allow_custom,
  }];
}

function seedField(f: FormFieldSchema): string | number | boolean {
  if (f.default !== undefined) return f.default;
  if (f.type === "boolean") return false;
  if (f.enum && f.enum.length) return f.enum[0];
  return "";
}

function seedAnswer(step: Step): Answer {
  if (step.kind === "choice") return { picked: new Set<string>(), custom: "" };
  if (step.kind === "approval") return { pick: null };
  const fields: Record<string, string | number | boolean> = {};
  for (const name of Object.keys(step.schema)) fields[name] = seedField(step.schema[name]);
  return { fields };
}

function stepAnswered(step: Step, a: Answer): boolean {
  if (step.kind === "choice") {
    const aa = a as { picked: Set<string>; custom: string };
    return aa.picked.size > 0 || aa.custom.trim().length > 0;
  }
  if (step.kind === "approval") return (a as { pick: unknown }).pick !== null;
  return true; // form 字段都有默认/可空，恒算已答
}

export function QuestionMode({ decision: q, onResolve, onChatAbout }: QuestionModeProps) {
  const { text } = useTranslation();
  const [discussionPending, setDiscussionPending] = useState(false);
  const { sendAnswer, answerPending, answerLocked } = useWaitAnswer(q, onResolve);

  const [discussionOpen, setDiscussionOpen] = useState(false);
  const [feedback, setFeedback] = useState("");
  const [feedbackLocked, setFeedbackLocked] = useState(false);
  async function sendDiscussion() {
    if (discussionPending || !feedback.trim()) return;
    setFeedbackLocked(true);
    setDiscussionPending(true);
    try { await onChatAbout(feedback); } finally { setDiscussionPending(false); }
  }
  const steps = toSteps(q);
  const [idx, setIdx] = useState(0);
  const [answers, setAnswers] = useState<Answer[]>(() => steps.map(seedAnswer));

  const cur = steps[idx];
  const curAns = answers[idx];
  const atFirst = idx === 0;
  const atLast = idx === steps.length - 1;
  const allAnswered = steps.every((s, i) => stepAnswered(s, answers[i]));

  const patch = (i: number, next: Answer) =>
    setAnswers((cur) => cur.map((a, k) => (k === i ? next : a)));

  function submit() {
    if (discussionOpen || discussionPending || answerPending) return;
    // 按原 decision kind 收集成后端期望的格式。
    if (q.kind === "form") {
      const step = steps[0] as Extract<Step, { kind: "form" }>;
      const fields = (answers[0] as { fields: Record<string, string | number | boolean> }).fields;
      const answer: Record<string, unknown> = {};
      for (const name of Object.keys(step.schema)) {
        const f = step.schema[name];
        const v = fields[name];
        if ((f.type === "integer" || f.type === "number") && typeof v === "string") {
          answer[name] = v === "" ? null : Number(v);
        } else {
          answer[name] = v;
        }
      }
      void sendAnswer("execution.wait.answer", answer);
      return;
    }
    if (q.kind === "approval") {
      const pick = (answers[0] as { pick: ApprovalPick | null }).pick;
      // ``APPROVE_ANSWER`` is a PROTOCOL value, not UI text — it must never
      // follow the user's locale. ``_approval.await_user_approval`` accepts
      // it verbatim; a localized string would fail the comparison in every
      // language it doesn't happen to list.
      if (pick === "once") void sendAnswer("execution.wait.answer", { answer: APPROVE_ANSWER, scope: "once" });
      else if (pick === "always") void sendAnswer("execution.wait.answer", { answer: APPROVE_ANSWER, scope: "always" });
      else if (pick === "always_path") void sendAnswer("execution.wait.answer", { answer: APPROVE_ANSWER, scope: "always_path" });
      else if (pick === "deny") void sendAnswer("execution.wait.decline");
      else return;
      return;
    }
    if (q.kind === "ask_many") {
      const value = steps.map((s, i) => {
        const aa = answers[i] as { picked: Set<string>; custom: string };
        const arr = Array.from(aa.picked);
        if (aa.custom.trim()) arr.push(aa.custom.trim());
        const sc = s as Extract<Step, { kind: "choice" }>;
        return sc.multi ? arr : (arr[0] ?? "");
      });
      void sendAnswer("execution.wait.answer", value);
      return;
    }
    // ask / confirm —— 单题选择。
    const aa = answers[0] as { picked: Set<string>; custom: string };
    const arr = Array.from(aa.picked);
    if (aa.custom.trim()) arr.push(aa.custom.trim());
    const answer: string | string[] = q.multi ? arr : (arr[0] ?? "");
    void sendAnswer("execution.wait.answer", answer);
  }

  // 底部按钮组：单步 → [发送]；多步 → [‹上一题, 下一题›/发送]。就是 body
  // 的最后一行、右对齐——普通表单排法，不再上报 composer 做绝对定位。
  const navButtons = (() => {
    const prev = {
      label: text("‹ Previous", "‹ 上一题"),
      onClick: () => setIdx((i) => Math.max(0, i - 1)),
      disabled: atFirst || answerLocked,
      primary: false,
    };
    const nextOrSend = atLast
      ? { label: answerPending ? text("Sending…", "发送中…") : answerLocked ? text("Retry", "重试") : text("Send", "发送"), onClick: submit, disabled: !allAnswered, primary: true }
      : {
          label: text("Next ›", "下一题 ›"),
          onClick: () => setIdx((i) => Math.min(steps.length - 1, i + 1)),
          disabled: false,
          primary: true,
        };
    return steps.length > 1 ? [prev, nextOrSend] : [nextOrSend];
  })();

  // Enter submits (last step) or advances (earlier steps), so the
  // autofocused free-text input has a keyboard path at all — previously
  // Enter did nothing and the mouse was the only way to send.
  // Ctrl/Cmd+Enter submits outright, matching fn-form.
  function onKey(e: React.KeyboardEvent) {
    if (e.key !== "Enter") return;
    // Enter commits an IME candidate (CN/JP/KR) — never treat that as send.
    const native = e.nativeEvent as KeyboardEvent;
    if (native.isComposing || native.keyCode === 229) return;
    if (discussionOpen) {
      if (e.ctrlKey || e.metaKey) {
        e.preventDefault();
        void sendDiscussion();
      }
      return;
    }
    if (e.shiftKey) return;
    const t = e.target as HTMLElement;
    if (t.tagName === "TEXTAREA") return;
    if (t.closest("button") && !e.ctrlKey && !e.metaKey) return;
    e.preventDefault();
    if (e.ctrlKey || e.metaKey) {
      if (allAnswered) submit();
      return;
    }
    if (atLast) {
      if (allAnswered) submit();
    } else {
      setIdx((i) => Math.min(steps.length - 1, i + 1));
    }
  }

  if (!cur) return null;

  return (
    <>
      <div className={styles.header} data-fn-form-header data-decision onKeyDown={onKey}>
        <div className={styles.badge}>{discussionOpen ? text("Discuss this operation", "讨论这项操作") : text("Your input is needed", "需要你的输入")}</div>
        {/* 进度点 + 几分之几 —— 哪怕只有 1 步也显示（统一）。 */}
        {!discussionOpen && <div className={multi.progress}>
          {steps.map((_, i) => (
            <button
              type="button"
              disabled={answerLocked}
              aria-current={i === idx ? "step" : undefined}
              key={i}
              className={
                multi.dot +
                (i === idx ? " " + multi.dotActive : "") +
                (stepAnswered(steps[i], answers[i]) ? " " + multi.dotDone : "")
              }
              onClick={() => { if (!discussionOpen && !answerLocked) setIdx(i); }}
              title={text(`Question ${i + 1}`, `第 ${i + 1} 题`)}
            />
          ))}
          <span className={multi.count}>
            {idx + 1}/{steps.length}
          </span>
        </div>}
      </div>
      <div className={styles.body} data-fn-form-body onKeyDown={onKey}>
        {cur.kind === "approval" ? (
          <StepBody step={cur} answer={curAns} onChange={(a) => patch(idx, a)} />
        ) : discussionOpen ? (
          <div className={styles.prompt}>{cur.prompt}</div>
        ) : (
          <fieldset disabled={discussionPending || answerLocked} className="m-0 flex min-w-0 flex-col gap-3 border-0 p-0">
            <StepBody step={cur} answer={curAns} onChange={(a) => patch(idx, a)} />
          </fieldset>
        )}
        {discussionOpen && (
          <label className={formStyles.field}>
            <span className="sr-only">{text("Discussion", "讨论内容")}</span>
            <Textarea className={styles.discussionInput} autoFocus rows={4} value={feedback} readOnly={feedbackLocked}
              onChange={(event) => setFeedback(event.target.value)}
              placeholder={text("Add your question, concern, or a different approach…", "写下你的问题、顾虑，或希望调整的地方…")} />
            {feedbackLocked && !discussionPending && <span className={formStyles.hint}>
              {text("Not confirmed yet. Retry to confirm the same feedback.", "尚未确认，请重试发送同一条反馈。")}
            </span>}
          </label>
        )}
      </div>
        <div className={`${styles.actions} ${styles.footer}`} onKeyDown={onKey} role="group" aria-label={text("Decision actions", "答复操作")}>
          {cur.kind === "approval" && !discussionOpen && (
            <div className={styles.actionButtons}>
              <button type="button" className={styles.navBtn}
                disabled={answerPending || (answerLocked && (curAns as { pick: string }).pick !== "deny")}
                onClick={() => { patch(idx, { pick: "deny" }); void sendAnswer("execution.wait.decline"); }}>
                {text("Deny", "拒绝")}
              </button>
              <button type="button" className={`${styles.navBtn} ${styles.navBtnPrimary}`}
                disabled={answerPending || (answerLocked && (curAns as { pick: string }).pick !== "once")}
                aria-busy={answerPending}
                onClick={() => { patch(idx, { pick: "once" }); void sendAnswer("execution.wait.answer", { answer: APPROVE_ANSWER, scope: "once" }); }}>
                {answerPending ? text("Sending…", "提交中…") : text("Allow once", "同意")}
              </button>
            </div>
          )}
          {cur.kind !== "approval" && <div className={styles.actionButtons}>
            {discussionOpen ? <>
              <button type="button" className={styles.navBtn} disabled={feedbackLocked}
                onClick={() => setDiscussionOpen(false)}>{text("Cancel", "取消")}</button>
              <button type="button" className={`${styles.navBtn} ${styles.navBtnPrimary}`}
                disabled={discussionPending || !feedback.trim()} aria-busy={discussionPending}
                onClick={() => void sendDiscussion()}>
                {discussionPending ? text("Sending…", "发送中…") : feedbackLocked
                  ? text("Retry discussion", "重试讨论") : text("Send discussion", "发送讨论")}
              </button>
            </> : <button type="button" className={styles.navBtn} disabled={answerLocked}
              onClick={() => setDiscussionOpen(true)} title={text("Discuss before proceeding", "先讨论再继续")}>
              {text("Chat about this", "Chat about this")}
            </button>}
          {!discussionOpen && navButtons.map((b, i) => (
            <button
              key={i}
              type="button"
              className={`${styles.navBtn} ${b.primary ? styles.navBtnPrimary : ""}`}
              onClick={b.onClick}
              disabled={discussionPending || answerPending || b.disabled}
            >
              {b.label}
            </button>
          ))}
          </div>}
        </div>
    </>
  );
}

/** 当前步的 body —— 按 step.kind 渲染。外壳（header/底部）已统一在上面。 */
function StepBody({
  step,
  answer,
  onChange,
}: {
  step: Step;
  answer: Answer;
  onChange: (a: Answer) => void;
}) {
  const { text } = useTranslation();
  if (step.kind === "form") {
    const fields = (answer as { fields: Record<string, string | number | boolean> }).fields;
    const setField = (name: string, v: string | number | boolean) =>
      onChange({ fields: { ...fields, [name]: v } });
    return (
      <>
        <div className={styles.prompt}>{withColon(step.prompt)}</div>
        {step.detail ? <div className={styles.detail}>{step.detail}</div> : null}
        <div className={formStyles.fields}>
          {Object.keys(step.schema).map((name) => {
            const f = step.schema[name];
            const label = f.title || name;
            // boolean：标签 + 勾选框同一行（标签在左、勾选框在右）；
            // 其它字段：标签在上、控件在下。
            if (f.type === "boolean") {
              return (
                <label key={name} className={formStyles.fieldRow}>
                  <span className={formStyles.label}>
                    {label}
                    {f.description ? <span className={formStyles.hint}> · {f.description}</span> : null}
                  </span>
                  <input
                    type="checkbox"
                    checked={Boolean(fields[name])}
                    onChange={(e) => setField(name, e.target.checked)}
                    className={formStyles.checkbox}
                  />
                </label>
              );
            }
            return (
              <label key={name} className={formStyles.field}>
                <span className={formStyles.label}>
                  {label}
                  {f.description ? <span className={formStyles.hint}> · {f.description}</span> : null}
                </span>
                {f.enum && f.enum.length ? (
                  <select
                    className={styles.input}
                    value={String(fields[name])}
                    onChange={(e) => setField(name, e.target.value)}
                  >
                    {f.enum.map((opt) => (
                      <option key={opt} value={opt}>{opt}</option>
                    ))}
                  </select>
                ) : (
                  <input
                    className={styles.input}
                    type={f.type === "integer" || f.type === "number" ? "number" : "text"}
                    value={String(fields[name])}
                    min={f.minimum}
                    max={f.maximum}
                    onChange={(e) => setField(name, e.target.value)}
                  />
                )}
              </label>
            );
          })}
        </div>
      </>
    );
  }

  if (step.kind === "approval") {
    const esc = step.escalation;
    const { prompt, summary } = approvalDisplayText(step.prompt, step.detail, esc, text);
    const command = !esc && ["bash", "process", "shell", "exec_command"].includes(step.tool ?? "")
      && typeof step.args?.command === "string" ? step.args.command : null;
    return (
      <>
        <div className={styles.prompt}>{esc ? prompt : withColon(prompt)}</div>
        {command ? <OperationCode value={command} language="bash" /> : null}
        {esc && summary ? <pre className={approvalStyles.summary}>{summary}</pre> : null}
        {step.args && Object.keys(step.args).length > 0 ? (
          command ? (
            <details className={approvalStyles.executionDetails}>
              <summary><ChevronRight size={16} aria-hidden="true" />{text("Execution details", "查看执行详情")}</summary>
              <OperationCode value={JSON.stringify(step.args, null, 2)} language="json" />
            </details>
          ) : <OperationCode value={JSON.stringify(step.args, null, 2)} language="json" />
        ) : !esc && summary ? <OperationCode value={summary} language="text" /> : null}
      </>
    );
  }

  // choice —— 选项 + 可选自由文本。
  const aa = answer as { picked: Set<string>; custom: string };
  const toggle = (opt: string) => {
    if (step.multi) {
      const next = new Set(aa.picked);
      next.has(opt) ? next.delete(opt) : next.add(opt);
      onChange({ picked: next, custom: aa.custom });
    } else {
      // 单选：点已选的 → 取消；点别的 → 切换。
      onChange({ picked: aa.picked.has(opt) ? new Set() : new Set([opt]), custom: "" });
    }
  };
  return (
    <>
      <div className={styles.prompt}>{withColon(step.prompt)}</div>
      {step.options.length > 0 ? (
        <div className={styles.options}>
          {step.options.map((opt) => (
            <button
              key={opt}
              type="button"
              className={styles.opt + (aa.picked.has(opt) ? " " + styles.optPicked : "")}
              onClick={() => toggle(opt)}
            >
              {aa.picked.has(opt) ? "✓ " : ""}{opt}
            </button>
          ))}
        </div>
      ) : null}
      {step.allowCustom ? (
        <input
          className={styles.input}
          value={aa.custom}
          placeholder={step.options.length
            ? text("Or type your own…", "或自己输入…")
            : text("Type your answer…", "输入你的回答…")}
          onChange={(e) => {
            const custom = e.target.value;
            // 打字进自由文本时清掉单选已选项（避免两个答案打架）。
            const picked = !step.multi && aa.picked.size ? new Set<string>() : aa.picked;
            onChange({ picked, custom });
          }}
          autoFocus
        />
      ) : null}
    </>
  );
}
