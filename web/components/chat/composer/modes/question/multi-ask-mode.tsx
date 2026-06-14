"use client";

/**
 * MultiAskMode —— runtime.ask_many 的"一组问题一屏切换"形态。
 *
 * 大模型一次打包问多个问题（kind="ask_many"，data.questions 是问题数组）。
 * 这里一屏只显示当前一题，顶部给"第 N / M 题"进度 + ‹上一题 / 下一题›
 * 切换，用户可前后翻看修改；每题各自选项/多选/自由文本。全部答完后右下角
 * 发送按钮（经 onAction 上报）亮起，一次提交整组答案（list，每题一项）。
 *
 * 结构 / 配色与 question / fn-form 一致（复用 question-mode 样式）。
 * 设计：docs/design/runtime/user-input-requests.md（Phase 5 ask_many）。
 */

import { useEffect, useState } from "react";

import type { PendingDecision, AskOne } from "@/lib/session-store";

import type { DecisionAction } from "./question-mode";
import styles from "./question-mode.module.css";
import multi from "./multi-ask-mode.module.css";

function wsSend(payload: unknown): void {
  const w = window as unknown as { ws?: WebSocket };
  if (w.ws && w.ws.readyState === WebSocket.OPEN) {
    w.ws.send(JSON.stringify(payload));
  }
}

interface MultiAskModeProps {
  decision: PendingDecision;
  onResolve: (id: string) => void;
  onAction: (a: DecisionAction | null) => void;
}

type OneAnswer = { picked: Set<string>; custom: string };

function answered(q: AskOne, a: OneAnswer): boolean {
  return a.picked.size > 0 || a.custom.trim().length > 0;
}

/** Collapse one question's working state to its final answer value. */
function finalize(q: AskOne, a: OneAnswer): string | string[] {
  const arr = Array.from(a.picked);
  if (a.custom.trim()) arr.push(a.custom.trim());
  if (q.multi) return arr;
  return arr[0] ?? ""; // single: first pick or the custom text
}

export function MultiAskMode({ decision: q, onResolve, onAction }: MultiAskModeProps) {
  const questions = q.questions ?? [];
  const [idx, setIdx] = useState(0);
  // One working answer per question, by index.
  const [answers, setAnswers] = useState<OneAnswer[]>(() =>
    questions.map(() => ({ picked: new Set<string>(), custom: "" })),
  );

  const cur = questions[idx];
  const curAns = answers[idx] ?? { picked: new Set<string>(), custom: "" };
  const allAnswered = questions.every((qq, i) => answered(qq, answers[i]!));
  const atFirst = idx === 0;
  const atLast = idx === questions.length - 1;

  const patch = (i: number, next: Partial<OneAnswer>) =>
    setAnswers((cur) => cur.map((a, k) => (k === i ? { ...a, ...next } : a)));

  function submit() {
    const value = questions.map((qq, i) => finalize(qq, answers[i]!));
    wsSend({ action: "question_reply", id: q.id, answer: value });
    onResolve(q.id);
  }

  // The bottom-right send button submits the whole group, enabled once
  // every question has an answer.
  useEffect(() => {
    onAction({ run: submit, canSubmit: allAnswered });
    return () => onAction(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [allAnswered, q.id]);

  function toggle(opt: string) {
    if (!cur) return;
    if (cur.multi) {
      const next = new Set(curAns.picked);
      next.has(opt) ? next.delete(opt) : next.add(opt);
      patch(idx, { picked: next });
    } else {
      // single: pick replaces, then auto-advance to the next unanswered.
      patch(idx, { picked: new Set([opt]), custom: "" });
      if (!atLast) setIdx(idx + 1);
    }
  }

  if (!cur) return null;

  return (
    <>
      <div className={styles.header} data-fn-form-header>
        <div className={styles.badge}>需要你的输入</div>
        <div className={multi.progress}>
          {questions.map((_, i) => (
            <span
              key={i}
              className={
                multi.dot +
                (i === idx ? " " + multi.dotActive : "") +
                (answered(questions[i]!, answers[i]!) ? " " + multi.dotDone : "")
              }
              onClick={() => setIdx(i)}
              title={`第 ${i + 1} 题`}
            />
          ))}
          <span className={multi.count}>
            {idx + 1}/{questions.length}
          </span>
        </div>
      </div>
      <div className={styles.body} data-fn-form-body>
        {q.prompt ? <div className={multi.groupTitle}>{q.prompt}</div> : null}
        <div className={styles.prompt}>{cur.prompt}</div>

        {cur.options.length > 0 ? (
          <div className={styles.options}>
            {cur.options.map((opt) => (
              <button
                key={opt}
                type="button"
                className={
                  styles.opt + (curAns.picked.has(opt) ? " " + styles.optPicked : "")
                }
                onClick={() => toggle(opt)}
              >
                {cur.multi && curAns.picked.has(opt) ? "✓ " : ""}
                {opt}
              </button>
            ))}
          </div>
        ) : null}

        {cur.allow_custom ? (
          <input
            className={styles.input}
            value={curAns.custom}
            placeholder={cur.options.length ? "或自己输入…" : "输入你的回答…"}
            onChange={(e) => patch(idx, { custom: e.target.value })}
          />
        ) : null}

        <div className={multi.nav}>
          <button
            type="button"
            className={multi.navBtn}
            disabled={atFirst}
            onClick={() => setIdx(idx - 1)}
          >
            ‹ 上一题
          </button>
          <button
            type="button"
            className={multi.navBtn}
            disabled={atLast}
            onClick={() => setIdx(idx + 1)}
          >
            下一题 ›
          </button>
        </div>
      </div>
    </>
  );
}
