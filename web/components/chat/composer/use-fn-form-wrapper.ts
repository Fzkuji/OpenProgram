"use client";

/**
 * Wrapper-height transition + outgoing crossfade for the fn-form.
 *
 * Three intertwined concerns the composer used to inline:
 *
 *  1. Chat-mode height caching. We need a known starting value for the
 *     CSS height transition so it can interpolate FROM the textarea
 *     wrapper's natural chat-mode size. Cached every render while idle.
 *  2. Open / close height transition. Snap to chat-height, set inline
 *     target to fn-form natural, let CSS animate. Close reverses; the
 *     form stays mounted until `transitionend` so the close visual
 *     mirrors the open (height shrinks + content fades together).
 *  3. Crossfade on A → B fn switch. The previous fn's header + body
 *     gets captured into `outgoingFn` so it renders as an absolutely
 *     positioned overlay above the new form while both fade together.
 *
 * The hook owns the wrapper inline `height` + the send button's
 * inline `top` (driven so the button glides between chat-mode top
 * and fn-form bottom). It returns the public bits the composer
 * needs to render — `outgoingFn`, the wrapper / send button refs are
 * still owned by the composer (it passes them in).
 */

import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type RefObject,
} from "react";

import type { AgenticFunction } from "@/lib/session-store";

// Pixels: action-button offset (16) + size (32). The fn-form steady
// state pins the button to `wrapper.height - ACTION_BTN_BOTTOM_OFFSET`
// so its bottom edge sits `--composer-button-offset` above the
// wrapper's bottom. Kept here as a constant to avoid reading the
// CSS variable in JS — the tokens it maps to live in 01-base.css.
const ACTION_BTN_BOTTOM_OFFSET = 48;
// Crossfade slack: keep the outgoing layer mounted slightly longer
// than the composer's fade animation so the unmount happens after
// the visual transition has fully completed.
const OUTGOING_TTL_MS = 300;

interface UseFnFormWrapperArgs {
  fnFormFunction: AgenticFunction | null;
  fnFormClosing: boolean;
  onCloseComplete: () => void;
  wrapperRef: RefObject<HTMLDivElement>;
  sendBtnRef: RefObject<HTMLButtonElement>;
}

export interface FnFormWrapperHook {
  outgoingFn: AgenticFunction | null;
}

export function useFnFormWrapper({
  fnFormFunction,
  fnFormClosing,
  onCloseComplete,
  wrapperRef,
  sendBtnRef,
}: UseFnFormWrapperArgs): FnFormWrapperHook {
  const [outgoingFn, setOutgoingFn] = useState<AgenticFunction | null>(null);
  const prevFnRef = useRef<AgenticFunction | null>(null);
  const chatHeightRef = useRef<number>(98);
  const [transitioning, setTransitioning] = useState(false);

  // Capture outgoing fn before React swaps the FunctionForm child.
  useLayoutEffect(() => {
    const prev = prevFnRef.current;
    prevFnRef.current = fnFormFunction;
    if (prev && fnFormFunction && prev !== fnFormFunction) {
      setOutgoingFn(prev);
    }
  }, [fnFormFunction]);

  // Drop the outgoing overlay after the fade.
  useEffect(() => {
    if (!outgoingFn) return;
    const id = setTimeout(() => setOutgoingFn(null), OUTGOING_TTL_MS);
    return () => clearTimeout(id);
  }, [outgoingFn]);

  // Cache the wrapper's natural chat-mode height while idle so the
  // transition has a known origin.
  useEffect(() => {
    if (fnFormFunction || transitioning) return;
    const el = wrapperRef.current;
    if (!el || el.style.height) return;
    chatHeightRef.current = el.offsetHeight;
  });

  // Open / close height transition. See in-line comments for the
  // open vs close branches; the actual measurement trick lives in
  // `measureFnFormHeight()` below so this block reads as
  // "snap-start, compute target, set, listen for end".
  useLayoutEffect(() => {
    const el = wrapperRef.current;
    if (!el) return;
    if (fnFormClosing) {
      return runCloseTransition(el, sendBtnRef.current, chatHeightRef.current, () => {
        setTransitioning(false);
        onCloseComplete();
      }, setTransitioning);
    }
    if (fnFormFunction) {
      return runOpenTransition(el, sendBtnRef.current, chatHeightRef.current, setTransitioning);
    }
  }, [fnFormFunction, fnFormClosing, onCloseComplete, sendBtnRef, wrapperRef]);

  // After the form unmounts, drop the inline `height` we left behind
  // during the close transition so the wrapper can size itself
  // naturally for chat-mode content (textarea auto-resize, etc.).
  useEffect(() => {
    if (fnFormFunction) return;
    const el = wrapperRef.current;
    if (!el || !el.style.height) return;
    el.style.height = "";
  }, [fnFormFunction, wrapperRef]);

  return { outgoingFn };
}

/* ---- transition primitives ---------------------------------------- */

/**
 * Close — animate wrapper shrink WITH the form still mounted, so the
 * body / header retreat downward into the bottom row (mirror image of
 * the open animation where they emerge upward out of it). The
 * `.closing` class on header/body fades opacity 1→0 in parallel; the
 * action button glides back to the chat-mode top in lockstep.
 *
 * On transitionend we unmount the form (via `onComplete`, which the
 * hook wires to `closeFnFormStore() + setClosing(false)`) — only
 * then can the inline `height` be cleared, otherwise the wrapper
 * momentarily snaps back to fn-form natural height while React is
 * still committing the unmount.
 */
function runCloseTransition(
  el: HTMLDivElement,
  btn: HTMLButtonElement | null,
  chatHeight: number,
  onComplete: () => void,
  setTransitioning: (v: boolean) => void,
): () => void {
  setTransitioning(true);
  const current = el.offsetHeight;
  el.style.height = `${current}px`;
  void el.offsetHeight;
  el.style.height = `${chatHeight}px`;
  if (btn) btn.style.top = "16px";
  const onEnd = (ev: TransitionEvent) => {
    if (ev.target !== el || ev.propertyName !== "height") return;
    el.removeEventListener("transitionend", onEnd);
    onComplete();
  };
  el.addEventListener("transitionend", onEnd);
  return () => el.removeEventListener("transitionend", onEnd);
}

/**
 * Open / switch — animate wrapper grow to the fn-form's natural size.
 * Starting height:
 *   * chat → fn-form: wrapper has no inline height (chat-mode auto-
 *     sizes). Snap to the cached `chatHeight` + force a reflow so the
 *     browser registers it as the transition origin.
 *   * fn-form A → fn-form B: wrapper already has an inline height
 *     equal to A's natural size. Leave it untouched and transition
 *     straight to B's natural size.
 */
function runOpenTransition(
  el: HTMLDivElement,
  btn: HTMLButtonElement | null,
  chatHeight: number,
  setTransitioning: (v: boolean) => void,
): () => void {
  setTransitioning(true);
  if (!el.style.height) {
    el.style.height = `${chatHeight}px`;
    void el.offsetHeight;
  }
  const natural = measureFnFormHeight(el);
  el.style.height = `${natural}px`;
  // Glide the action button from its current top to the new fn-form
  // bottom (natural − offset). Same CSS curve as the wrapper height.
  if (btn) btn.style.top = `${natural - ACTION_BTN_BOTTOM_OFFSET}px`;
  const onEnd = (ev: TransitionEvent) => {
    if (ev.target !== el || ev.propertyName !== "height") return;
    el.removeEventListener("transitionend", onEnd);
    setTransitioning(false);
  };
  el.addEventListener("transitionend", onEnd);
  return () => el.removeEventListener("transitionend", onEnd);
}

/**
 * Compute the wrapper's target height by measuring the form contents
 * directly. We can't trust `body.scrollHeight` here: body has
 * `flex:1 + overflow-y:auto`, so when the wrapper's inline height is
 * currently large (e.g. a previous, taller fn is still showing), the
 * body is also large and the new fn's smaller content fits inside —
 * scrollHeight ends up equal to body's box height, not its content
 * size, which would lock the wrapper at the old big height.
 *
 * Workaround: temporarily take body out of the flex constraint and
 * let it size to its content, read its `offsetHeight`, then restore.
 */
function measureFnFormHeight(el: HTMLDivElement): number {
  const header = el.querySelector(
    "[data-fn-form-header]",
  ) as HTMLElement | null;
  const body = el.querySelector(
    "[data-fn-form-body]",
  ) as HTMLElement | null;
  const padBottom = parseFloat(getComputedStyle(el).paddingBottom);
  if (!header || !body) return el.scrollHeight;
  const prevBodyStyle = body.getAttribute("style") || "";
  body.style.flex = "0 0 auto";
  body.style.height = "auto";
  body.style.maxHeight = "none";
  body.style.minHeight = "auto";
  body.style.overflow = "visible";
  const bodyContentH = body.offsetHeight;
  if (prevBodyStyle) {
    body.setAttribute("style", prevBodyStyle);
  } else {
    body.removeAttribute("style");
  }
  return header.offsetHeight + bodyContentH + padBottom;
}
