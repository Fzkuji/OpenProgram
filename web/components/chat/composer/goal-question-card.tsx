"use client";

/**
 * Goal waiting_user question card — floats ABOVE the composer while the
 * session's goal loop is suspended on a question (goal.status ===
 * "waiting_user").
 *
 * Look = QuestionMode's card family: same question-mode.module.css
 * header/badge/prompt/options classes, hosted in the composer's own
 * .inputWrapper box style. Free-form answering is the composer itself
 * (a plain chat message resumes the goal loop), so the card carries NO
 * input of its own — only the question, the option pills, and a hint
 * line. Clicking an option sends its label through the normal chat send
 * path (Composer passes `onPick`) and optimistically hides the card;
 * the authoritative hide is the goal_update that flips status away from
 * waiting_user.
 */

import { useState } from "react";
import { Target } from "lucide-react";

import { useSessionGoal } from "../goal-chip";
import { useTranslation } from "@/lib/i18n";
import q from "./modes/question/question-mode.module.css";
import form from "./modes/question/form-mode.module.css";
import styles from "./composer.module.css";

export function GoalQuestionCard({
  sessionId,
  onPick,
}: {
  sessionId: string | null;
  /** Send `label` as a normal chat message; true = sent (hide the card). */
  onPick: (label: string) => boolean;
}) {
  const { text } = useTranslation();
  const goal = useSessionGoal(sessionId);
  // Optimistic hide after picking an option — keyed per question so a
  // LATER waiting_user (next turn) shows the card again even if the
  // goal_update for this answer hasn't landed yet.
  const [answeredKey, setAnsweredKey] = useState<string | null>(null);

  if (!goal || goal.status !== "waiting_user" || !goal.last_question) {
    return null;
  }
  const key = `${sessionId}:${goal.turns_used ?? 0}:${goal.last_question}`;
  if (answeredKey === key) return null;

  const options = goal.last_question_options ?? [];
  const pick = (label: string) => {
    if (onPick(label)) setAnsweredKey(key);
  };

  return (
    <div className={`${styles.inputWrapper} ${styles.goalCard}`}>
      <div className={q.header}>
        <div className={q.badge}>
          <Target size={13} strokeWidth={2} className={styles.goalCardIcon} />
          {text("Goal — your answer is needed", "goal · 等你回答")}
        </div>
      </div>
      <div className={q.body}>
        <div className={q.prompt}>{goal.last_question}</div>
        {options.length > 0 && (
          <div className={q.options}>
            {options.map((o) => (
              <button
                key={o.label}
                type="button"
                className={q.opt}
                onClick={() => pick(o.label)}
                title={o.description || undefined}
              >
                {o.label}
                {o.description ? (
                  <span className={form.hint}> · {o.description}</span>
                ) : null}
              </button>
            ))}
          </div>
        )}
        <div className={`${form.label} ${form.hint}`}>
          {text(
            "You can also just type your answer below.",
            "也可以直接在下方输入框输入回答",
          )}
        </div>
      </div>
    </div>
  );
}
