"use client";

/**
 * QuestionMode —— runtime.ask / confirm 在输入框里的形态（不再是浮窗）。
 *
 * 系统停下来等用户决定时（use-ws 把 question.asked 写进 store 的
 * pendingDecisions 队列），composer 渲染本组件占据输入区。结构与 fn-form
 * 一致：header（标题 + 分割线）/ body（问题 + 选项 + 可选自由文本）。
 *
 * 主操作（提交）不在 body 里画——经 `onAction` 把 {run, canSubmit} 报给
 * composer，由右下角那个发送按钮承担（跟 fn-form 同一个按钮位）。次操作
 * （取消/拒绝）用左上角 ✕ 关闭键（composer 渲染，调 onResolve+reject）。
 * 单选 / confirm 点选项即直接提交，不必动发送按钮。
 *
 * 设计：docs/design/ui/composer-interaction-modes.md。
 */

import { useEffect, useState } from "react";

import type { PendingDecision } from "@/lib/session-store";

import { FormMode } from "./form-mode";
import styles from "./question-mode.module.css";

function wsSend(payload: unknown): void {
  const w = window as unknown as { ws?: WebSocket };
  if (w.ws && w.ws.readyState === WebSocket.OPEN) {
    w.ws.send(JSON.stringify(payload));
  }
}

export interface DecisionAction {
  run: () => void;
  canSubmit: boolean;
}

interface QuestionModeProps {
  decision: PendingDecision;
  /** 答完/拒完后把这条从队列摘除（本地即时收回，不等后端广播回来）。 */
  onResolve: (id: string) => void;
  /** 把"提交动作 + 能否提交"报给 composer，让右下角发送按钮当提交键。
   *  传 null 表示本 mode 此刻没有需要发送按钮的提交（如纯单选直接交）。 */
  onAction: (a: DecisionAction | null) => void;
}

export function QuestionMode({ decision: q, onResolve, onAction }: QuestionModeProps) {
  // runtime.form (kind="form") is a multi-field form — delegate to its
  // own renderer. Single-prompt ask/confirm stay below.
  if (q.kind === "form") {
    return <FormMode decision={q} onResolve={onResolve} onAction={onAction} />;
  }
  return <SinglePromptQuestion decision={q} onResolve={onResolve} onAction={onAction} />;
}

function SinglePromptQuestion({ decision: q, onResolve, onAction }: QuestionModeProps) {
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [custom, setCustom] = useState("");

  function submit(answer: string | string[]) {
    wsSend({ action: "question_reply", id: q.id, answer });
    onResolve(q.id);
  }

  const toggle = (opt: string) => {
    if (q.multi) {
      setPicked((cur) => {
        const next = new Set(cur);
        next.has(opt) ? next.delete(opt) : next.add(opt);
        return next;
      });
    } else {
      submit(opt); // 单选/confirm 的选项直接提交
    }
  };

  function submitMultiOrCustom() {
    if (q.multi) {
      const arr = Array.from(picked);
      if (custom.trim()) arr.push(custom.trim());
      if (arr.length) submit(arr);
    } else if (custom.trim()) {
      submit(custom.trim());
    }
  }

  const canSubmit = q.multi
    ? picked.size > 0 || custom.trim().length > 0
    : custom.trim().length > 0;
  // 需要发送按钮的情形：多选，或允许自由文本且无选项（纯文本输入）。
  // 纯单选/confirm（点选项即交）不占用发送按钮。
  const needsSubmitButton = q.multi || (q.allow_custom && q.options.length === 0);

  // 把提交动作报给 composer 的发送按钮；canSubmit 变了要重报，所以进 deps。
  useEffect(() => {
    onAction(needsSubmitButton ? { run: submitMultiOrCustom, canSubmit } : null);
    return () => onAction(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [needsSubmitButton, canSubmit, q.id]);

  return (
    // fn-form 同款两段式：header（标题 + 下分割线）/ body（内容 + 下分割线）。
    <>
      <div className={styles.header} data-fn-form-header>
        <div className={styles.badge}>
          {q.kind === "confirm" || q.kind === "approval" ? "需要确认" : "需要你的输入"}
        </div>
      </div>
      <div className={styles.body} data-fn-form-body>
        <div className={styles.prompt}>{q.prompt}</div>
        {q.detail ? <div className={styles.detail}>{q.detail}</div> : null}

        {q.options.length > 0 ? (
          <div className={styles.options}>
            {q.options.map((opt) => (
              <button
                key={opt}
                type="button"
                className={
                  styles.opt + (picked.has(opt) ? " " + styles.optPicked : "")
                }
                onClick={() => toggle(opt)}
              >
                {q.multi && picked.has(opt) ? "✓ " : ""}
                {opt}
              </button>
            ))}
          </div>
        ) : null}

        {q.allow_custom ? (
          <input
            className={styles.input}
            value={custom}
            placeholder={q.options.length ? "或自己输入…" : "输入你的回答…"}
            onChange={(e) => setCustom(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !q.multi && custom.trim()) {
                e.preventDefault();
                submit(custom.trim());
              }
            }}
            autoFocus
          />
        ) : null}
      </div>
    </>
  );
}
