"use client";

import { useCallback, useEffect, useState, type RefObject } from "react";
import { useTranslation } from "@/lib/i18n";
import type { PendingDecision } from "@/lib/session-store/types";

import { approvalDisplayText, readSandboxEscalation } from "./approval-display-text";

interface Options {
  decision: PendingDecision | null;
  sessionKey: string;
  input: string;
  setInput(value: string): void;
  decline(decision: PendingDecision): void;
  dequeue(id: string): void;
  textareaRef: RefObject<HTMLTextAreaElement>;
}

export function useDecisionDiscussion({
  decision, sessionKey, input, setInput, decline, dequeue, textareaRef,
}: Options) {
  const { text } = useTranslation();
  const [focusSession, setFocusSession] = useState<string | null>(null);

  useEffect(() => {
    if (focusSession === null) return;
    if (focusSession !== sessionKey) {
      setFocusSession(null);
      return;
    }
    // Another pending decision may still own the composer. Focus only
    // after this session's ordinary input has mounted.
    if (decision || !textareaRef.current) return;
    textareaRef.current.focus();
    textareaRef.current.setSelectionRange?.(input.length, input.length);
    setFocusSession(null);
  }, [focusSession, sessionKey, decision, textareaRef, input]);

  return useCallback(() => {
    if (!decision) return;
    decline(decision);
    const { prompt, summary } = approvalDisplayText(
      decision.prompt, decision.detail,
      decision.kind === "approval" ? readSandboxEscalation(decision.args) : undefined, text,
    );
    const context = [text("About this request:", "关于这次请求："), prompt, summary]
      .filter(Boolean).join("\n");
    setInput(`${context}\n\n${input}`);
    setFocusSession(sessionKey);
    dequeue(decision.id);
  }, [decision, decline, dequeue, input, sessionKey, setInput, text]);
}
