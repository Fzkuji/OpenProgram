"use client";

/**
 * QuestionPanel —— 输入框顶部向上生长的提问附加区。composer 的 textarea /
 * 底栏 / env-chip 行全部原样不动，只有这块面板出现让输入框长高（inputArea
 * 是 bottom-anchored absolute，长高只向上顶消息流）。
 *
 * 两个场景共用（真 pending ask 优先，路由在 index.tsx）：
 *   - ask_user_question（runtime.ask/confirm）：点选项 pill = 立即
 *     question_reply；在下方输入框打字回车也作为答案改道 question_reply。
 *   - goal waiting_user：点 pill = 立即把 label 当普通聊天消息发出；打字
 *     回车 = 普通聊天消息（goal 循环本来就收任意用户消息）。
 *
 * 纯呈现组件——badge / 问题 / 选项从 props 来，点击回调由挂载方接线。
 * 选项 pill 复用 question-mode 的 .opt 家族样式（不重造样式语言）。
 */

import React from "react";

import q from "./modes/question/question-mode.module.css";
import form from "./modes/question/form-mode.module.css";
import styles from "./composer.module.css";

export interface QuestionPanelOption {
  label: string;
  description?: string;
}

export function QuestionPanel({
  badge,
  prompt,
  options,
  onPick,
}: {
  /** 单行来源标识（图标 + 文案），超长省略号。 */
  badge: React.ReactNode;
  prompt: string;
  options: QuestionPanelOption[];
  /** 点选项 = 立即提交该 label（点击即时反馈，无「选中再发送」两步）。 */
  onPick: (label: string) => void;
}) {
  return (
    <div className={styles.questionPanel}>
      <div className={styles.questionPanelBadge}>{badge}</div>
      <div className={q.prompt}>{prompt}</div>
      {options.length > 0 && (
        <div className={q.options}>
          {options.map((o) => (
            <button
              key={o.label}
              type="button"
              className={`${q.opt} ${styles.questionPanelOpt}`}
              onClick={() => onPick(o.label)}
            >
              {o.label}
              {o.description ? (
                <span className={form.hint}> · {o.description}</span>
              ) : null}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
