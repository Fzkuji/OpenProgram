"use client";

/**
 * QuestionMode —— runtime.ask / confirm 在输入框里的形态（不再是浮窗）。
 *
 * 系统停下来等用户决定时（use-ws 把 question.asked 写进 store 的
 * pendingDecisions 队列），composer 渲染本组件占据输入区：问题正文 + 选项
 * 按钮 + （可选）自由文本框 + 取消。答 → 发 question_reply，拒 → question_reject，
 * 都从队列摘除（后端 _resolve_question 收口 + 广播收回）。
 *
 * 设计：docs/design/ui/composer-interaction-modes.md（步 2）。
 */

import { useState } from "react";

import type { PendingDecision } from "@/lib/session-store";

import { FormMode } from "./form-mode";
import styles from "./question-mode.module.css";

function wsSend(payload: unknown): void {
  const w = window as unknown as { ws?: WebSocket };
  if (w.ws && w.ws.readyState === WebSocket.OPEN) {
    w.ws.send(JSON.stringify(payload));
  }
}

interface QuestionModeProps {
  decision: PendingDecision;
  /** 答完/拒完后把这条从队列摘除（本地即时收回，不等后端广播回来）。 */
  onResolve: (id: string) => void;
}

export function QuestionMode({ decision: q, onResolve }: QuestionModeProps) {
  // runtime.form (kind="form") is a multi-field form — delegate to its
  // own renderer. Single-prompt ask/confirm stay below.
  if (q.kind === "form") {
    return <FormMode decision={q} onResolve={onResolve} />;
  }
  return <SinglePromptQuestion decision={q} onResolve={onResolve} />;
}

function SinglePromptQuestion({ decision: q, onResolve }: QuestionModeProps) {
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [custom, setCustom] = useState("");

  function submit(answer: string | string[]) {
    wsSend({ action: "question_reply", id: q.id, answer });
    onResolve(q.id);
  }
  function reject() {
    wsSend({ action: "question_reject", id: q.id });
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
  const showSubmit = q.multi || (q.allow_custom && q.options.length === 0);

  return (
    <div className={styles.host} data-fn-form-body>
      <div className={styles.badge} data-fn-form-header>
        {q.kind === "confirm" ? "需要确认" : "需要你的输入"}
      </div>
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

      <div className={styles.actions}>
        <button className={styles.reject} type="button" onClick={reject}>
          {q.kind === "confirm" ? "取消" : "拒绝"}
        </button>
        {showSubmit && (
          <button
            className={styles.submit}
            type="button"
            disabled={!canSubmit}
            onClick={submitMultiOrCustom}
          >
            提交
          </button>
        )}
      </div>
    </div>
  );
}
