"use client";

/**
 * fn-form field state for the composer.
 *
 * Mirrors the four bits of state that drive the function form below
 * the chat input:
 *
 *   - `values`   — { paramName: stringValue }, seeded with each
 *                  visible param's default whenever the function
 *                  changes.
 *   - `workdir`  — current value of the workdir input (reset on fn
 *                  change).
 *   - `error`    — name of the param that should highlight red when
 *                  the user tries to submit with required fields
 *                  empty (`__workdir` for the workdir input). Cleared
 *                  automatically when the user starts editing that
 *                  field again.
 *   - `closing`  — true between the close click and the wrapper
 *                  height transition end; the composer uses this to
 *                  hide the close button and pass a fade-out flag
 *                  down to the form.
 *
 * The hook returns ready-made `setValue` / `setWorkdir` handlers that
 * also clear matching error highlights, so callers don't repeat the
 * "set new value AND clear error if it matches this field" pattern.
 */

import { useCallback, useEffect, useState } from "react";

import type { AgenticFunction } from "@/lib/session-store";

import { defaultParamValue, visibleParams } from "./fn-form";

export interface FnFormStateHook {
  values: Record<string, string>;
  workdir: string;
  error: string | null;
  closing: boolean;
  setValue: (name: string, v: string) => void;
  setWorkdir: (v: string) => void;
  setError: (name: string | null) => void;
  setClosing: (v: boolean) => void;
}

export function useFnFormState(fn: AgenticFunction | null): FnFormStateHook {
  const [values, setValuesRaw] = useState<Record<string, string>>({});
  const [workdir, setWorkdirRaw] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [closing, setClosing] = useState(false);

  // Seed defaults each time the function changes; also resets workdir
  // / error / closing so a fresh fn never inherits a previous form's
  // state.
  useEffect(() => {
    if (!fn) {
      setValuesRaw({});
      setWorkdirRaw("");
      setError(null);
      setClosing(false);
      return;
    }
    const seed: Record<string, string> = {};
    for (const p of visibleParams(fn)) {
      const v = defaultParamValue(p);
      if (v) seed[p.name] = v;
    }
    setValuesRaw(seed);
    setWorkdirRaw("");
    setError(null);
  }, [fn]);

  const setValue = useCallback(
    (name: string, v: string) => {
      setValuesRaw((s) => ({ ...s, [name]: v }));
      setError((cur) => (cur === name ? null : cur));
    },
    [],
  );

  const setWorkdir = useCallback((v: string) => {
    setWorkdirRaw(v);
    setError((cur) => (cur === "__workdir" && v.trim() ? null : cur));
  }, []);

  return {
    values,
    workdir,
    error,
    closing,
    setValue,
    setWorkdir,
    setError,
    setClosing,
  };
}
