"use client";

import type { PendingDecision } from "@/lib/session-store";
import { useTranslation } from "@/lib/i18n";
import styles from "./question-mode.module.css";

/** Present the actual submitted values alongside their original questions. */
export function AnswerSummary({ decision, answer }: { decision: PendingDecision; answer: unknown }) {
  const { text } = useTranslation();
  function valueText(value: unknown): string {
    if (value === undefined || value === null || value === "") return text("Not provided", "未填写");
    if (typeof value === "boolean") return value ? text("Yes", "是") : text("No", "否");
    if (Array.isArray(value)) return value.map(valueText).join("\n");
    if (typeof value === "object") return Object.entries(value).map(([key, item]) => `${key}: ${valueText(item)}`).join("\n");
    return String(value);
  }
  const values = answer !== null && typeof answer === "object" && !Array.isArray(answer)
    ? answer as Record<string, unknown> : {};
  let items: { prompt: string; value: unknown }[];
  let intro = "";
  if (decision.kind === "ask_many") {
    intro = decision.prompt;
    items = (decision.questions ?? []).map((question, index) => ({
      prompt: question.prompt, value: Array.isArray(answer) ? answer[index] : undefined,
    }));
  } else if (decision.kind === "form") {
    intro = decision.prompt;
    items = Object.entries(decision.schema ?? {}).map(([key, field]) => ({
      prompt: field.title || key, value: values[key],
    }));
  } else {
    const approval = decision.kind === "approval" && values.answer === "approve"
      ? values.scope === "once" ? text("Allow once", "允许一次")
        : values.scope === "session" ? text("Allowed for this conversation", "允许此对话")
        : values.scope === "always" ? text("Always allow", "始终允许")
          : values.scope === "always_path" ? text("Always allow this path", "始终允许此路径")
            : text("Approved", "已同意")
      : answer;
    items = [{ prompt: decision.prompt, value: approval }];
  }
  return <div className={styles.questionList}>
    {intro && <div className={styles.groupPrompt}>{intro}</div>}
    {items.map((item, index) => <div className={styles.summaryItem} key={index}>
      <div className={styles.summaryPrompt}>{item.prompt}</div>
      {answer !== undefined && <div className={styles.summaryAnswer}>{valueText(item.value)}</div>}
    </div>)}
  </div>;
}
