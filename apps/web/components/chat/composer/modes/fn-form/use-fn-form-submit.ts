"use client";

import { useCallback } from "react";

import { showToast } from "@/lib/format-utils/toast";
import { normalizeFunctionArguments } from "@/lib/function-invocation";
import { useTranslation } from "@/lib/i18n";
import { useSessionStore } from "@/lib/session-store";

import type { FunctionDispatcher } from "./use-function-dispatch";
import type { useFnFormState } from "./use-fn-form-state";

type FnFormFunction = NonNullable<
  ReturnType<typeof useSessionStore.getState>["fnFormFunction"]
>;

export interface FnFormSubmitOptions {
  fnFormFunction: FnFormFunction | null;
  fnForm: ReturnType<typeof useFnFormState>;
  dispatchFunction: FunctionDispatcher;
  /** Start the close animation after the shared dispatcher accepts the run. */
  handleFnFormClose(): void;
}

export function useFnFormSubmit({
  fnFormFunction,
  fnForm,
  dispatchFunction,
  handleFnFormClose,
}: FnFormSubmitOptions) {
  const { text } = useTranslation();

  return useCallback(() => {
    if (!fnFormFunction) return;
    const enteredValues = Object.fromEntries(
      Object.entries(fnForm.values).filter(
        ([, value]) => typeof value !== "string" || value.trim() !== "",
      ),
    );
    const normalized = normalizeFunctionArguments(
      fnFormFunction,
      enteredValues,
    );
    if (!normalized.ok) {
      if (normalized.errorParam) fnForm.setError(normalized.errorParam);
      showToast(text(
        `Invalid function call: ${normalized.error}`,
        `函数参数无效：${normalized.error}`,
      ), { tone: "error" });
      return;
    }
    const localAction = useSessionStore.getState().fnFormSubmitAction;
    if (localAction) {
      const result = localAction.submit(normalized.kwargs);
      if (result?.error) {
        if (result.errorParam) fnForm.setError(result.errorParam);
        showToast(result.error, { tone: "error" });
      } else {
        handleFnFormClose();
      }
      return;
    }
    const accepted = dispatchFunction(
      fnFormFunction,
      normalized.kwargs,
      { forkOf: useSessionStore.getState().fnFormForkOf },
    );
    if (accepted) handleFnFormClose();
  }, [
    dispatchFunction,
    fnForm,
    fnFormFunction,
    handleFnFormClose,
    text,
  ]);
}
