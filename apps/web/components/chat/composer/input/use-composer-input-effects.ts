"use client";

import { useEffect, useRef, type RefObject } from "react";

interface Options {
  bound: string | null;
  input: string;
  focusTick: number;
  textareaRef: RefObject<HTMLTextAreaElement>;
}

export function useComposerInputEffects({
  bound,
  input,
  focusTick,
  textareaRef,
}: Options) {
  const inputAreaRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (bound !== null) return;
    const element = inputAreaRef.current;
    if (!element || typeof ResizeObserver === "undefined") return;
    const publishHeight = () => {
      if (element.offsetHeight > 0) {
        document.documentElement.style.setProperty(
          "--main-composer-height",
          `${Math.round(element.offsetHeight)}px`,
        );
      }
    };
    publishHeight();
    const observer = new ResizeObserver(publishHeight);
    observer.observe(element);
    return () => {
      observer.disconnect();
      document.documentElement.style.removeProperty("--main-composer-height");
    };
  }, [bound]);

  useEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    textarea.style.height = "auto";
    textarea.style.height = `${Math.min(textarea.scrollHeight, 200)}px`;
    textarea.style.overflowY = textarea.scrollHeight > 200 ? "auto" : "hidden";
  }, [input, textareaRef]);

  useEffect(() => {
    if (focusTick > 0) textareaRef.current?.focus();
  }, [focusTick, textareaRef]);

  return inputAreaRef;
}
