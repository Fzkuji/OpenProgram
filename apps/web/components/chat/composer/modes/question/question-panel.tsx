"use client";

/**
 * QuestionPanel —— 输入框顶部向上生长的提问附加区。composer 的 textarea /
 * 底栏 / env-chip 行全部原样不动，只有这块面板出现让输入框长高（inputArea
 * 是 bottom-anchored absolute，长高只向上顶消息流）。
 *
 * 两个场景共用（真 pending ask 优先，路由在 index.tsx）：
 *   - ask_user_question（runtime.ask/confirm）：点选项 pill = 立即
 *     question_reply；在下方输入框打字回车也作为答案改道 question_reply。
 *     goal waiting_user 的提问经 runtime.ask → question.asked 走同一路径。
 *   - goal 面板独立出现（question.asked 尚未到达 / 已丢失）：没有 qid 可以
 *     回答，选项禁用（disabled），等事件到达后转为 ask 面板。
 *
 * 纯呈现组件——badge / 问题 / 选项从 props 来，点击回调由挂载方接线。
 * 选项 pill 复用 question-mode 的 .opt 家族样式（不重造样式语言）。
 */

import React from "react";

import q from "./question-mode.module.css";
import form from "./form-mode.module.css";
import styles from "./question-panel.module.css";

export interface QuestionPanelOption {
  label: string;
  description?: string;
}

export function QuestionPanel({
  badge,
  prompt,
  options,
  onPick,
  disabled = false,
}: {
  /** 单行来源标识（图标 + 文案），超长省略号。 */
  badge: React.ReactNode;
  prompt: string;
  options: QuestionPanelOption[];
  /** 点选项 = 立即提交该 label（点击即时反馈，无「选中再发送」两步）。 */
  onPick: (label: string) => void;
  /** 回答通道未就绪（goal 面板独立出现、拿不到 qid）时禁用选项。 */
  disabled?: boolean;
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
              disabled={disabled}
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
