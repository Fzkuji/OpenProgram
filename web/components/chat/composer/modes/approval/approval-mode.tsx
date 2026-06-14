"use client";

/**
 * ApprovalMode —— 工具批准在输入框里的形态（question mode 的衍生）。
 *
 * 跟 question / form 完全同构：header（"需要批准" + 分割线）/ body（提示 +
 * 危险摘要：工具名 + 参数）。主操作"允许"走右下角那个发送按钮（经 onAction
 * 报给 composer）；"拒绝"走左上角 ✕（composer 调 onReject）。结构、配色、
 * 按钮位都与 fn-form 一致（用 question-mode 的样式 + 一个危险摘要专属类）。
 *
 * 后端审批已合流到 QuestionRegistry（kind="approval"）：允许 = question_reply
 * 「允许」，拒绝 = question_reject。
 */

import { useEffect } from "react";

import type { PendingDecision } from "@/lib/session-store";

import type { DecisionAction } from "../question/question-mode";
import qStyles from "../question/question-mode.module.css";
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
  onAction: (a: DecisionAction | null) => void;
}

export function ApprovalMode({ decision: q, onResolve, onAction }: ApprovalModeProps) {
  function approve() {
    wsSend({ action: "question_reply", id: q.id, answer: "允许" });
    onResolve(q.id);
  }

  // 主操作"允许"挂到右下角发送按钮（恒可点）。
  useEffect(() => {
    onAction({ run: approve, canSubmit: true });
    return () => onAction(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q.id]);

  return (
    <>
      <div className={qStyles.header} data-fn-form-header>
        <div className={qStyles.badge}>需要批准</div>
      </div>
      <div className={qStyles.body} data-fn-form-body>
        <div className={qStyles.prompt}>{q.prompt}</div>
        {q.detail ? <pre className={styles.summary}>{q.detail}</pre> : null}
      </div>
    </>
  );
}
