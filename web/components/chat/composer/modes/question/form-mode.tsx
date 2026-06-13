"use client";

/**
 * FormMode —— runtime.form 的多字段表单形态（question mode 的衍生）。
 *
 * runtime.form(prompt, fields) 发一个 kind="form" 的 question.asked，data
 * 带一个 flat-object 字段 schema（字段名 → {type,title,enum,default,…}）。
 * 这里逐字段渲染输入控件，收集成一个对象，提交时发
 * `question_reply` 带 answer={字段名:值}（后端 _resolve_question 原样
 * resolve，registry 把 dict 交回 runtime.form）。拒绝走 question_reject。
 *
 * 字段类型（MCP-elicitation flat schema）：
 *   string（带 enum → 下拉；否则文本）/ integer·number（数字）/ boolean（勾选）。
 *
 * 设计：docs/design/runtime/user-input-requests.md（Phase 4a）。
 */

import { useState } from "react";

import type { PendingDecision, FormFieldSchema } from "@/lib/session-store";

import styles from "./question-mode.module.css";
import formStyles from "./form-mode.module.css";

function wsSend(payload: unknown): void {
  const w = window as unknown as { ws?: WebSocket };
  if (w.ws && w.ws.readyState === WebSocket.OPEN) {
    w.ws.send(JSON.stringify(payload));
  }
}

interface FormModeProps {
  decision: PendingDecision;
  onResolve: (id: string) => void;
}

type FieldValue = string | number | boolean;

/** Seed a field's initial value from its schema default (or a type zero). */
function seedValue(f: FormFieldSchema): FieldValue {
  if (f.default !== undefined) return f.default;
  if (f.type === "boolean") return false;
  if (f.type === "integer" || f.type === "number") return "";
  if (f.enum && f.enum.length) return f.enum[0];
  return "";
}

export function FormMode({ decision: q, onResolve }: FormModeProps) {
  const fields = q.schema ?? {};
  const names = Object.keys(fields); // insertion order = display order
  const [values, setValues] = useState<Record<string, FieldValue>>(() => {
    const init: Record<string, FieldValue> = {};
    for (const n of names) init[n] = seedValue(fields[n]);
    return init;
  });

  const set = (name: string, v: FieldValue) =>
    setValues((cur) => ({ ...cur, [name]: v }));

  function submit() {
    // Coerce number-typed fields from their string inputs.
    const answer: Record<string, unknown> = {};
    for (const n of names) {
      const f = fields[n];
      const v = values[n];
      if ((f.type === "integer" || f.type === "number") && typeof v === "string") {
        answer[n] = v === "" ? null : Number(v);
      } else {
        answer[n] = v;
      }
    }
    wsSend({ action: "question_reply", id: q.id, answer });
    onResolve(q.id);
  }

  function reject() {
    wsSend({ action: "question_reject", id: q.id });
    onResolve(q.id);
  }

  return (
    <div className={styles.host} data-fn-form-body>
      <div className={styles.badge} data-fn-form-header>
        需要你填写
      </div>
      <div className={styles.prompt}>{q.prompt}</div>
      {q.detail ? <div className={styles.detail}>{q.detail}</div> : null}

      <div className={formStyles.fields}>
        {names.map((name) => {
          const f = fields[name];
          const label = f.title || name;
          return (
            <label key={name} className={formStyles.field}>
              <span className={formStyles.label}>
                {label}
                {f.description ? (
                  <span className={formStyles.hint}> · {f.description}</span>
                ) : null}
              </span>
              {f.type === "boolean" ? (
                <input
                  type="checkbox"
                  checked={Boolean(values[name])}
                  onChange={(e) => set(name, e.target.checked)}
                  className={formStyles.checkbox}
                />
              ) : f.enum && f.enum.length ? (
                <select
                  className={styles.input}
                  value={String(values[name])}
                  onChange={(e) => set(name, e.target.value)}
                >
                  {f.enum.map((opt) => (
                    <option key={opt} value={opt}>
                      {opt}
                    </option>
                  ))}
                </select>
              ) : (
                <input
                  className={styles.input}
                  type={f.type === "integer" || f.type === "number" ? "number" : "text"}
                  value={String(values[name])}
                  min={f.minimum}
                  max={f.maximum}
                  onChange={(e) => set(name, e.target.value)}
                />
              )}
            </label>
          );
        })}
      </div>

      <div className={styles.actions}>
        <button className={styles.reject} type="button" onClick={reject}>
          取消
        </button>
        <button className={styles.submit} type="button" onClick={submit}>
          提交
        </button>
      </div>
    </div>
  );
}
