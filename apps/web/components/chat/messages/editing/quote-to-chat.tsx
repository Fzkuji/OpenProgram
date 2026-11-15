"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { QuoteIcon } from "@/components/animated-icons";
import { useActionIconAnimation } from "../bubbles/use-action-icon-animation";
import { useSessionStore } from "@/lib/session-store";
import { useTranslation } from "@/lib/i18n";
import { Button } from "@/components/ui/button";
import styles from "./message-editor.module.css";

export function quoteToChat(sessionId: string, content: string) {
  const selected = content.trim();
  if (!selected) return;
  appendToComposer(sessionId, selected.split("\n").map(line => `> ${line}`).join("\n"));
}

/** Add a block to the end of the session's composer draft. Review line
 *  comments pass focus=false so a run of comments stays in the diff. */
export function appendToComposer(sessionId: string, block: string, focus = true) {
  const store = useSessionStore.getState();
  const draft = store.composerDrafts[sessionId] || "";
  store.setComposerInputFor(sessionId, `${draft}${draft ? "\n\n" : ""}${block}\n\n`);
  if (!focus) return;
  requestAnimationFrame(() => {
    const host = Array.from(document.querySelectorAll<HTMLElement>("[data-composer-session]"))
      .find(node => node.dataset.composerSession === sessionId);
    // The composer field is a markdown editor exposing a textarea-like
    // handle on its content element (composer/input/markdown-editor.tsx).
    const input = host?.querySelector<HTMLElement & { composerInput?: HTMLTextAreaElement }>(
      "[data-composer-input]",
    )?.composerInput;
    input?.focus();
    input?.setSelectionRange(input.value.length, input.value.length);
  });
}

/** Branch points of the retired in-conversation branch, once kept per
 *  viewer under this key: dropped the first time a transcript mounts. */
const RETIRED_BRANCH_POINTS_KEY = "openprogram.branch-points.v1";
let retiredKeyDropped = false;
function dropRetiredBranchPoints() {
  if (retiredKeyDropped) return;
  retiredKeyDropped = true;
  try {
    localStorage.removeItem(RETIRED_BRANCH_POINTS_KEY);
  } catch {
    /* storage unavailable: nothing was kept */
  }
}

/** One selection listener per transcript, not per message. */
export function SelectionQuote({ sessionId }: { sessionId: string | null }) {
  const { text } = useTranslation();
  const anchor = useRef<HTMLSpanElement>(null);
  const popup = useRef<HTMLDivElement>(null);
  const [selection, setSelection] = useState<{ content: string; left: number; top: number } | null>(null);
  const quoteIcon = useActionIconAnimation(!selection);
  useEffect(dropRetiredBranchPoints, []);
  useEffect(() => {
    const root = anchor.current?.parentElement;
    if (!root || !sessionId) return;
    const hide = () => setSelection(null);
    const update = () => {
      const selected = window.getSelection();
      if (!selected || selected.isCollapsed || !selected.rangeCount) return hide();
      const element = (node: Node | null) => node instanceof Element ? node : node?.parentElement;
      const start = element(selected.anchorNode)?.closest(".message-content");
      const end = element(selected.focusNode)?.closest(".message-content");
      if (!start || start !== end || !root.contains(start) || start.closest(".is-editing")) return hide();
      const content = selected.toString().trim();
      if (!content) return hide();
      const rect = selected.getRangeAt(0).getBoundingClientRect();
      setSelection({ content, left: Math.max(12, Math.min(rect.left, window.innerWidth - 320)),
        top: rect.top > 48 ? rect.top - 42 : rect.bottom + 6 });
    };
    let pointerDown = false;
    const inPopup = (event: Event) => event.target instanceof Node && popup.current?.contains(event.target);
    const beginSelection = (event: PointerEvent) => {
      if (inPopup(event)) return;
      pointerDown = true;
      hide();
    };
    const endSelection = (event: PointerEvent) => {
      if (inPopup(event)) return;
      pointerDown = false;
      update();
    };
    const cancelSelection = () => { pointerDown = false; hide(); };
    const selectionChanged = () => {
      // Selection changes repeatedly while dragging. Only release/key-up opens the toolbar.
      if (pointerDown || window.getSelection()?.isCollapsed) hide();
    };
    const keyboardSelection = () => { if (!pointerDown) update(); };
    document.addEventListener("pointerdown", beginSelection, true);
    document.addEventListener("pointerup", endSelection, true);
    document.addEventListener("pointercancel", cancelSelection, true);
    root.addEventListener("keyup", keyboardSelection);
    document.addEventListener("selectionchange", selectionChanged);
    document.addEventListener("scroll", hide, true);
    window.addEventListener("resize", hide);
    window.addEventListener("blur", cancelSelection);
    return () => {
      document.removeEventListener("pointerdown", beginSelection, true);
      document.removeEventListener("pointerup", endSelection, true);
      document.removeEventListener("pointercancel", cancelSelection, true);
      root.removeEventListener("keyup", keyboardSelection);
      document.removeEventListener("selectionchange", selectionChanged);
      document.removeEventListener("scroll", hide, true);
      window.removeEventListener("resize", hide);
      window.removeEventListener("blur", cancelSelection);
    };
  }, [sessionId]);
  return <><span ref={anchor} hidden />{selection && sessionId && createPortal(
    <div ref={popup} className={styles.quotePopup} role="group" aria-label={text("Quote selection", "引用选中文字")}
      style={{ left: selection.left, top: selection.top }} onPointerDown={e => e.preventDefault()}>
      <Button type="button" variant="ghost" size="sm" {...quoteIcon.handlers} onClick={() => {
        quoteToChat(sessionId, selection.content); window.getSelection()?.removeAllRanges(); setSelection(null);
      }}><QuoteIcon ref={quoteIcon.ref} size={16} />{text("Add to chat", "加入当前聊天")}</Button>
    </div>, document.body,
  )}</>;
}
