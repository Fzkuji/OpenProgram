"use client";

/**
 * QuestionPrompt — 函数中途停下来问用户时的浮层卡片
 * (user-input-requests.md Phase 1)。
 *
 * 一个函数体调 runtime.ask/confirm → 后端 emit `question.asked` 帧 →
 * use-ws 转成 `op:question-asked` window 事件 → 本组件显示卡片 →
 * 用户答（question_reply）/ 拒（question_reject）→ 后端 resolve、函数 resume。
 * 收到 `op:question-closed`（别处先答了 / stop）则收回卡片。
 *
 * 在 app-shell 顶层挂一次，fixed 浮在底部居中。
 */

import { useEffect, useState } from "react";
import { createPortal } from "react-dom";

import styles from "./question-prompt.module.css";

interface Question {
  id: string;
  kind: "ask" | "confirm";
  prompt: string;
  options: string[];
  multi: boolean;
  allow_custom: boolean;
  detail?: string;
}

function wsSend(payload: unknown): void {
  const w = window as unknown as { ws?: WebSocket };
  if (w.ws && w.ws.readyState === WebSocket.OPEN) {
    w.ws.send(JSON.stringify(payload));
  }
}

export function QuestionPrompt() {
  const [q, setQ] = useState<Question | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [custom, setCustom] = useState("");
  const [mounted, setMounted] = useState(false);

  useEffect(() => setMounted(true), []);

  useEffect(() => {
    function onAsked(e: Event) {
      const d = (e as CustomEvent<Question>).detail;
      if (!d || !d.id) return;
      setQ(d);
      setPicked(new Set());
      setCustom("");
    }
    function onClosed(e: Event) {
      const d = (e as CustomEvent<{ id: string }>).detail;
      setQ((cur) => (cur && (!d?.id || d.id === cur.id) ? null : cur));
    }
    window.addEventListener("op:question-asked", onAsked);
    window.addEventListener("op:question-closed", onClosed);
    return () => {
      window.removeEventListener("op:question-asked", onAsked);
      window.removeEventListener("op:question-closed", onClosed);
    };
  }, []);

  if (!mounted || !q) return null;

  const toggle = (opt: string) => {
    if (q.multi) {
      setPicked((cur) => {
        const next = new Set(cur);
        next.has(opt) ? next.delete(opt) : next.add(opt);
        return next;
      });
    } else {
      // 单选直接提交（confirm 的"确认/取消"也走这里）
      submit(opt);
    }
  };

  function submit(answer: string | string[]) {
    if (!q) return;
    wsSend({ action: "question_reply", id: q.id, answer });
    setQ(null);
  }

  function reject() {
    if (!q) return;
    wsSend({ action: "question_reject", id: q.id });
    setQ(null);
  }

  function submitMultiOrCustom() {
    if (!q) return;
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

  return createPortal(
    <div className={styles.host} role="dialog" aria-modal="false">
      <div className={styles.card}>
        <div className={styles.badge}>
          {q.kind === "confirm" ? "需要确认" : "需要你的输入"}
        </div>
        <div className={styles.prompt}>{q.prompt}</div>
        {q.detail ? <div className={styles.detail}>{q.detail}</div> : null}

        {q.options.length > 0 ? (
          <div className={styles.options}>
            {q.options.map((opt) => (
              <button
                key={opt}
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
          <div className={styles.customRow}>
            <input
              className={styles.input}
              value={custom}
              placeholder={
                q.options.length ? "或自己输入…" : "输入你的回答…"
              }
              onChange={(e) => setCustom(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !q.multi && custom.trim()) {
                  submit(custom.trim());
                }
              }}
              autoFocus
            />
          </div>
        ) : null}

        <div className={styles.actions}>
          <button className={styles.reject} onClick={reject}>
            {q.kind === "confirm" ? "取消" : "拒绝"}
          </button>
          {(q.multi || (q.allow_custom && q.options.length === 0)) && (
            <button
              className={styles.submit}
              disabled={!canSubmit}
              onClick={submitMultiOrCustom}
            >
              提交
            </button>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}
