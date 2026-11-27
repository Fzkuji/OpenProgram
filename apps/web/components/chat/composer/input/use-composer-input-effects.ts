"use client";

import { useEffect, useRef, type RefObject } from "react";
import { COMPOSER_TUCK_SHIFT_EVENT, visibleComposerHeight } from "@/lib/chat/history/chat-scroll";

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
    // The covered height: a tucked row's empty strip above the box is
    // left to the transcript (use-tuck-backdrop.ts). It is written on the
    // chat view, which holds the transcript and the Jump to latest chip
    // that read it, not on the page root: the tuck slide writes it in
    // every frame, and a property changed on the root restyles the whole
    // page, about 35 ms a frame (traced), where the chat view takes 1.5.
    const host = element.closest<HTMLElement>("#chatView") ?? document.documentElement;
    let published = "";
    const publishHeight = () => {
      if (element.offsetHeight > 0) {
        const value = `${Math.round(visibleComposerHeight(element))}px`;
        if (value === published) return;
        published = value;
        host.style.setProperty("--main-composer-height", value);
      }
    };
    publishHeight();
    const observer = new ResizeObserver(publishHeight);
    observer.observe(element);
    element.addEventListener(COMPOSER_TUCK_SHIFT_EVENT, publishHeight);
    return () => {
      observer.disconnect();
      element.removeEventListener(COMPOSER_TUCK_SHIFT_EVENT, publishHeight);
      host.style.removeProperty("--main-composer-height");
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
