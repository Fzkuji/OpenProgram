"use client";

/**
 * ApprovalMode —— 工具批准在输入框里的形态。
 *
 * question mode 的衍生（docs/design/ui/composer-interaction-modes.md 步4）：
 * 同样的"系统等用户决定 → 输入框变形"范式，但呈现成批准专属样子：危险标签
 * + 工具名 + 参数摘要 + [允许]/[拒绝] + 可选"拒绝并附理由"（理由变成工具
 * 错误文本回给模型，opencode 做法）。
 *
 * 后端审批已合流到 QuestionRegistry（kind="approval"），所以这里跟 question
 * mode 走同一套 WS action：允许 = question_reply「允许」，拒绝 = question_reject
 * （带可选 reason）。
 */

import { useState } from "react";

import type { PendingDecision } from "@/lib/session-store";

import styles from "./approval-mode.module.css";

function wsSend(payload: unknown): void {
  const w = window as unknown as { ws?: WebSocket };
  if (w.ws && w.ws.readyState === WebSocket.OPEN) {
    w.ws.send(JSON.stringify(payload));
  }
}

interface ApprovalModeProps {
  decision: PendingDecision;
  onResolve: (id: string) => void;
}

export function ApprovalMode({ decision: q, onResolve }: ApprovalModeProps) {
  const [reason, setReason] = useState("");
  const [showReason, setShowReason] = useState(false);

  function approve() {
    wsSend({ action: "question_reply", id: q.id, answer: "允许" });
    onResolve(q.id);
  }
  function reject() {
    wsSend({ action: "question_reject", id: q.id, reason: reason.trim() || undefined });
    onResolve(q.id);
  }

  return (
    <div className={styles.host} data-fn-form-body>
      <div className={styles.badge} data-fn-form-header>
        需要批准
      </div>
      <div className={styles.prompt}>{q.prompt}</div>
      {q.detail ? <pre className={styles.summary}>{q.detail}</pre> : null}

      {showReason ? (
        <input
          className={styles.input}
          value={reason}
          placeholder="拒绝理由（会作为错误反馈给模型，可留空）…"
          onChange={(e) => setReason(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              reject();
            }
          }}
          autoFocus
        />
      ) : null}

      <div className={styles.actions}>
        {showReason ? (
          <button className={styles.reject} type="button" onClick={reject}>
            确认拒绝
          </button>
        ) : (
          <button
            className={styles.reject}
            type="button"
            onClick={() => setShowReason(true)}
          >
            拒绝
          </button>
        )}
        <button className={styles.approve} type="button" onClick={approve}>
          允许
        </button>
      </div>
    </div>
  );
}
