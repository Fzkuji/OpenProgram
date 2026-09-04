import type { DurableWait } from "./execution-debugger";

export const APPROVE_ANSWER = "approve";

type FormField = NonNullable<NonNullable<DurableWait["request"]>["schema"]>[string];

function stringList(value: unknown): string[] | null {
  if (!Array.isArray(value) || value.some((item) => typeof item !== "string")) return null;
  return value as string[];
}

function typedFormValue(field: FormField, value: unknown): unknown {
  if (field.type === "boolean") {
    if (typeof value !== "boolean") throw new Error("Boolean form fields require true or false.");
    return value;
  }
  if (field.type === "integer" || field.type === "number") {
    if (typeof value !== "number" || !Number.isFinite(value)) throw new Error("Numeric form fields require a finite number.");
    if (field.type === "integer" && !Number.isInteger(value)) throw new Error("Integer form fields require a whole number.");
    if (field.minimum != null && value < field.minimum) throw new Error("Form value is below the allowed minimum.");
    if (field.maximum != null && value > field.maximum) throw new Error("Form value is above the allowed maximum.");
    return value;
  }
  if (typeof value !== "string") throw new Error("String form fields require text.");
  if (field.enum && !field.enum.includes(value)) throw new Error("Form value is not one of the allowed options.");
  return value;
}

/** Build the exact answer value expected by the durable wait consumer. */
export function buildWaitAnswer(
  wait: DurableWait,
  value: unknown,
  approvalScope?: string,
): unknown {
  if (wait.kind === "approval") {
    const scopes = wait.policy_snapshot?.allowed_scopes || [];
    if (!approvalScope || !scopes.includes(approvalScope)) throw new Error("Choose an allowed approval scope.");
    return { answer: APPROVE_ANSWER, scope: approvalScope };
  }
  if (wait.kind === "ask_many") {
    const answers = Array.isArray(value) ? value : null;
    const questions = wait.request?.questions || [];
    if (!answers || answers.length !== questions.length) throw new Error("Answer every question before submitting.");
    return answers.map((answer, index) => {
      const question = questions[index];
      if (question.multi) {
        const values = stringList(answer);
        if (!values || values.length === 0) throw new Error("Select at least one option for every multi-select question.");
        return values;
      }
      if (typeof answer !== "string" || !answer.trim()) throw new Error("Every question requires an answer.");
      return answer;
    });
  }
  if (wait.kind === "form") {
    if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Form answers must be an object.");
    const fields = wait.request?.schema || {};
    const answer: Record<string, unknown> = {};
    for (const [name, field] of Object.entries(fields)) {
      if (!(name in (value as Record<string, unknown>))) throw new Error(`Form field ${name} is missing.`);
      answer[name] = typedFormValue(field, (value as Record<string, unknown>)[name]);
    }
    return answer;
  }
  if (wait.request?.multi) {
    const values = stringList(value);
    if (!values || values.length === 0) throw new Error("Select at least one answer.");
    return values;
  }
  if (typeof value !== "string" || !value.trim()) throw new Error("Enter an answer before responding.");
  return value.trim();
}
