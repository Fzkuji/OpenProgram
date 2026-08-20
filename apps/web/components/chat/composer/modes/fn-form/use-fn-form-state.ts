"use client";

/**
 * fn-form field state for the composer.
 *
 * Mirrors the three bits of state that drive the function form below
 * the chat input:
 *
 *   - `values`   — { paramName: stringValue }, seeded with each
 *                  visible param's default whenever the function
 *                  changes.
 *   - `error`    — name of the param that should highlight red when
 *                  the user tries to submit with required fields
 *                  empty. Cleared automatically when the user starts
 *                  editing that field again.
 *   - `closing`  — true between the close click and the wrapper
 *                  height transition end; the composer uses this to
 *                  hide the close button and pass a fade-out flag
 *                  down to the form.
 *
 * The hook returns a ready-made `setValue` handler that also clears
 * matching error highlights.
 */

import { useCallback, useEffect, useState } from "react";

import { useSessionStore, type AgenticFunction } from "@/lib/session-store";

import { defaultParamValue, visibleParams } from "./fn-form";

export interface FnFormStateHook {
  values: Record<string, string>;
  error: string | null;
  closing: boolean;
  setValue: (name: string, v: string) => void;
  setError: (name: string | null) => void;
  setClosing: (v: boolean) => void;
}

export function useFnFormState(fn: AgenticFunction | null): FnFormStateHook {
  const [values, setValuesRaw] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [closing, setClosing] = useState(false);

  // Seed defaults each time the function changes; also resets error /
  // closing so a fresh fn never inherits a previous form's state.
  useEffect(() => {
    if (!fn) {
      setValuesRaw({});
      setError(null);
      setClosing(false);
      return;
    }
    // "修改后重新运行"路径带上次调用的参数（fnFormPrefill）；普通打开
    // 用 schema 默认值。开表单时一次性读取即可，不需要响应式订阅。
    const prefill = useSessionStore.getState().fnFormPrefill;
    const seed: Record<string, string> = {};
    for (const p of visibleParams(fn)) {
      const v = prefill?.[p.name] ?? defaultParamValue(p);
      if (v) seed[p.name] = v;
    }
    setValuesRaw(seed);
    setError(null);
  }, [fn]);

  const setValue = useCallback(
    (name: string, v: string) => {
      setValuesRaw((s) => ({ ...s, [name]: v }));
      setError((cur) => (cur === name ? null : cur));
    },
    [],
  );

  return {
    values,
    error,
    closing,
    setValue,
    setError,
    setClosing,
  };
}
