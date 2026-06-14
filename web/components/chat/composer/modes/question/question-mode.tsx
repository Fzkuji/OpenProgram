"use client";

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
 * 右上角「Chat about this」= 放弃作答、直接聊（composer 渲染）。
 *
 * 设计：docs/design/ui/composer-interaction-modes.md。
 */

import { useEffect, useState } from "react";

import type { PendingDecision, AskOne, FormFieldSchema } from "@/lib/session-store";

import styles from "./question-mode.module.css";
import multi from "./multi-ask-mode.module.css";
import approvalStyles from "../approval/approval-mode.module.css";
import formStyles from "./form-mode.module.css";

function wsSend(payload: unknown): void {
  const w = window as unknown as { ws?: WebSocket };
  if (w.ws && w.ws.readyState === WebSocket.OPEN) {
    w.ws.send(JSON.stringify(payload));
  }
}

/** 提示语收尾：已是问号/冒号/句号等终止符就原样；否则补一个中文冒号「：」，
 *  让「请填写名字」这类祈使句读起来像在等你输入。 */
function withColon(s: string): string {
  const t = s.trimEnd();
  if (!t) return t;
  return /[?？:：。.!！]$/.test(t) ? t : t + "：";
}

/** 一颗右下角的操作按钮（发送 / 上一题 / 下一题）。 */
export interface DecisionButton {
  label: string;
  onClick: () => void;
  disabled?: boolean;
  primary?: boolean;
}

/** 每个 decision 报给 composer 的右下角按钮组（统一文字 pill）。 */
export interface DecisionAction {
  navButtons: DecisionButton[];
}

interface QuestionModeProps {
  decision: PendingDecision;
  onResolve: (id: string) => void;
  onAction: (a: DecisionAction | null) => void;
}

/** 一步（一道题）的统一形状。kind 决定 body 怎么渲染、答案怎么收集。 */
type Step =
  | { kind: "choice"; prompt: string; options: string[]; multi: boolean; allowCustom: boolean }
  | { kind: "approval"; prompt: string; detail?: string }
  | { kind: "form"; prompt: string; detail?: string; schema: Record<string, FormFieldSchema> };

/** 一步的工作答案。choice → 选中集 + 自由文本；approval → allow/deny；
 *  form → 字段值对象。 */
type Answer =
  | { picked: Set<string>; custom: string }
  | { pick: "allow" | "deny" | null }
  | { fields: Record<string, string | number | boolean> };

/** 把一个 decision 拍平成统一的 steps 数组。单题 → 1 步；ask_many → N 步。 */
function toSteps(q: PendingDecision): Step[] {
  if (q.kind === "form") {
    return [{ kind: "form", prompt: q.prompt, detail: q.detail, schema: q.schema ?? {} }];
  }
  if (q.kind === "approval") {
    return [{ kind: "approval", prompt: q.prompt, detail: q.detail }];
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

export function QuestionMode({ decision: q, onResolve, onAction }: QuestionModeProps) {
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
      wsSend({ action: "question_reply", id: q.id, answer });
      onResolve(q.id);
      return;
    }
    if (q.kind === "approval") {
      const pick = (answers[0] as { pick: "allow" | "deny" | null }).pick;
      if (pick === "allow") wsSend({ action: "question_reply", id: q.id, answer: "允许" });
      else if (pick === "deny") wsSend({ action: "question_reject", id: q.id });
      else return;
      onResolve(q.id);
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
      wsSend({ action: "question_reply", id: q.id, answer: value });
      onResolve(q.id);
      return;
    }
    // ask / confirm —— 单题选择。
    const aa = answers[0] as { picked: Set<string>; custom: string };
    const arr = Array.from(aa.picked);
    if (aa.custom.trim()) arr.push(aa.custom.trim());
    const answer: string | string[] = q.multi ? arr : (arr[0] ?? "");
    wsSend({ action: "question_reply", id: q.id, answer });
    onResolve(q.id);
  }

  // 底部统一按钮组：单步 → [发送]；多步 → [‹上一题, 下一题›/发送]。
  useEffect(() => {
    const prev = { label: "‹ 上一题", onClick: () => setIdx((i) => Math.max(0, i - 1)), disabled: atFirst };
    const nextOrSend = atLast
      ? { label: "发送", onClick: submit, disabled: !allAnswered, primary: true }
      : { label: "下一题 ›", onClick: () => setIdx((i) => Math.min(steps.length - 1, i + 1)), primary: true };
    onAction({ navButtons: steps.length > 1 ? [prev, nextOrSend] : [nextOrSend] });
    return () => onAction(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [idx, atFirst, atLast, allAnswered, q.id]);

  if (!cur) return null;

  return (
    <>
      <div className={styles.header} data-fn-form-header>
        <div className={styles.badge}>需要你的输入</div>
        {/* 进度点 + 几分之几 —— 哪怕只有 1 步也显示（统一）。 */}
        <div className={multi.progress}>
          {steps.map((_, i) => (
            <span
              key={i}
              className={
                multi.dot +
                (i === idx ? " " + multi.dotActive : "") +
                (stepAnswered(steps[i], answers[i]) ? " " + multi.dotDone : "")
              }
              onClick={() => setIdx(i)}
              title={`第 ${i + 1} 题`}
            />
          ))}
          <span className={multi.count}>
            {idx + 1}/{steps.length}
          </span>
        </div>
      </div>
      <div className={styles.body} data-fn-form-body>
        <StepBody step={cur} answer={curAns} onChange={(a) => patch(idx, a)} />
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
    const pick = (answer as { pick: "allow" | "deny" | null }).pick;
    return (
      <>
        <div className={styles.prompt}>{withColon(step.prompt)}</div>
        {step.detail ? <pre className={approvalStyles.summary}>{step.detail}</pre> : null}
        <div className={styles.options}>
          {(["allow", "deny"] as const).map((p) => (
            <button
              key={p}
              type="button"
              className={styles.opt + (pick === p ? " " + styles.optPicked : "")}
              onClick={() => onChange({ pick: pick === p ? null : p })}
            >
              {pick === p ? "✓ " : ""}{p === "allow" ? "允许" : "拒绝"}
            </button>
          ))}
        </div>
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
          placeholder={step.options.length ? "或自己输入…" : "输入你的回答…"}
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
