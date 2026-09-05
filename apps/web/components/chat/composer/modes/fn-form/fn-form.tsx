/**
 * FunctionForm — interactive parameter form rendered inside the
 * Composer when the user picks a program from the sidebar or welcome
 * screen. State lives in the parent Composer so the Send button (which
 * sits at the wrapper level, outside this component) can call submit
 * directly. Field rendering is delegated to ./fn-form-fields.
 */
"use client";

import type { AgenticFunction, FnParam } from "@/lib/session-store";
import { userFunctionParams } from "@/lib/function-invocation";
import { useTranslation } from "@/lib/i18n";

import { FieldRow } from "./fn-form-fields";
import styles from "./fn-form.module.css";

export function visibleParams(fn: AgenticFunction): FnParam[] {
  return userFunctionParams(fn);
}

export function defaultParamValue(p: FnParam): string {
  const isBool = p.type === "bool" || p.type === "boolean";
  const def = (p.default ?? "").replace(/^["']|["']$/g, "");
  if (isBool) return def === "True" ? "True" : "False";
  if (p.options && p.options.length > 0 && def && p.options.includes(def)) {
    return def;
  }
  return "";
}

interface FunctionFormProps {
  fn: AgenticFunction;
  values: Record<string, string>;
  setValue: (name: string, v: string) => void;
  errorParam: string | null;
  closing?: boolean;
  onClose: () => void;
  onSubmit: () => void;
  /** When true, suppress `id` attributes on the form inputs so the
      ghost overlay (rendered alongside the main form during fn→fn
      crossfade) doesn't duplicate input IDs in the DOM. */
  ghost?: boolean;
}

export function FunctionForm({
  fn,
  values,
  setValue,
  errorParam,
  closing,
  onClose,
  onSubmit,
  ghost,
}: FunctionFormProps) {
  const params = visibleParams(fn);
  const primary = params.filter((p) => !p.advanced);
  const advanced = params.filter((p) => p.advanced);
  const { text } = useTranslation();

  const rows = (items: FnParam[]) => items.map((p) => (
    <FieldRow
      key={p.name}
      param={p}
      value={values[p.name] ?? ""}
      setValue={(v) => setValue(p.name, v)}
      error={errorParam === p.name}
      noId={ghost}
    />
  ));

  // The panel's height is its natural content height — no max-height
  // transition. The composer's wrapper height transition (driven from
  // Composer's `useLayoutEffect`) is what slides the form in/out from
  // behind the bottom row.

  function onKey(e: React.KeyboardEvent) {
    if (e.key === "Escape") {
      e.preventDefault();
      onClose();
      return;
    }
    // Skip Enter handling while an IME is composing (CN/JP/KR
    // candidate picker uses Enter to commit a candidate).
    const native = e.nativeEvent as KeyboardEvent;
    if (native.isComposing || native.keyCode === 229) {
      return;
    }
    const t = e.target as HTMLElement;
    const isTextarea = t.tagName === "TEXTAREA";
    if (e.key === "Enter") {
      if (e.ctrlKey || e.metaKey) {
        e.preventDefault();
        onSubmit();
        return;
      }
      if (!e.shiftKey && !isTextarea) {
        e.preventDefault();
        onSubmit();
      }
    }
  }

  // Header / body are two flat siblings inside the input wrapper. No
  // `panel` div wraps body — body is itself a direct wrapper child.
  return (
    <>
      <div
        data-fn-form-header
        className={`${styles.header} ${closing ? styles.closing : ""}`}
        onKeyDown={onKey}
      >
        <div
          className={styles.title}
          title={
            fn.description
              ? `function ${fn.name} – ${fn.description}`
              : `function ${fn.name}`
          }
        >
          <span className={styles.name}>
            <span className={styles.keyword}>function </span>
            {fn.name}
          </span>
          {fn.description ? (
            <>
              <span className={styles.dash}>{" – "}</span>
              <span className={styles.desc}>{fn.description}</span>
            </>
          ) : null}
        </div>
        {/* Close button now lives at the wrapper level (rendered by
            Composer) so it stays mounted across fn switches and
            doesn't blink. The header only carries title content. */}
      </div>
      <div
        data-fn-form-body
        className={`${styles.body} ${closing ? styles.closing : ""}`}
        onKeyDown={onKey}
      >
        {params.length === 0 ? (
          <div className={styles.noParams}>
            No parameters needed — click run to execute
          </div>
        ) : (
          <>
            {rows(primary)}
            {advanced.length > 0 ? (
              <details className={styles.advanced}>
                <summary className={styles.advancedSummary}>
                  {text("Advanced", "高级参数")}
                </summary>
                <div className={styles.advancedFields}>{rows(advanced)}</div>
              </details>
            ) : null}
          </>
        )}
      </div>
    </>
  );
}
