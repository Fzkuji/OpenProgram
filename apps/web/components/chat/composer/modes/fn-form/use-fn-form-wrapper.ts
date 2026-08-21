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
 * The hook owns the wrapper inline `height`. (The send/run button no
 * longer glides — it lives in the form header at a fixed spot, so the
 * only animated property is the wrapper height itself.) It returns
 * the public bits the composer needs to render — `outgoingFn`; the
 * wrapper ref is still owned by the composer (it passes it in).
 */

import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type RefObject,
} from "react";

import type { AgenticFunction } from "@/lib/session-store";

// Crossfade slack: keep the outgoing layer mounted slightly longer
// than the composer's fade animation so the unmount happens after
// the visual transition has fully completed.
const OUTGOING_TTL_MS = 300;

interface UseFnFormWrapperArgs {
  fnFormFunction: AgenticFunction | null;
  fnFormClosing: boolean;
  onCloseComplete: () => void;
  wrapperRef: RefObject<HTMLDivElement>;
  /** A system decision (question/approval/form) occupies the input. It
   *  uses the same header/body two-段 structure as fn-form, so the
   *  wrapper-grow transition must run for it too. Identity changes
   *  (one decision → next) re-run the open transition so the wrapper
   *  re-measures the new content height. */
  decisionKey: string | null;
}

export interface FnFormWrapperHook {
  outgoingFn: AgenticFunction | null;
}

export function useFnFormWrapper({
  fnFormFunction,
  fnFormClosing,
  onCloseComplete,
  wrapperRef,
  decisionKey,
}: UseFnFormWrapperArgs): FnFormWrapperHook {
  // Any morphed state (fn-form OR a system decision) needs the grow
  // transition. fn-form keeps its closing/outgoing machinery; the
  // decision path only needs open-transition + height cleanup.
  const morphed = fnFormFunction !== null || decisionKey !== null;
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
  // fn-form transition has a known origin. Was deliberately
  // dep-array-less so it re-read on every render, but
  // ``el.offsetHeight`` forces a synchronous layout reflow — at row-
  // wrap boundaries in the attachment strips, that compounded with
  // the chip's mount animation enough to introduce a visible stutter
  // that only un-stuck on the next mouse move. Track via
  // ResizeObserver so the cache stays current without per-render
  // forced reflows.
  useEffect(() => {
    if (typeof ResizeObserver === "undefined") return;
    const el = wrapperRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => {
      if (morphed || transitioning) return;
      const w = wrapperRef.current;
      if (!w || w.style.height) return;
      chatHeightRef.current = w.offsetHeight;
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [morphed, transitioning, wrapperRef]);

  // Open / close height transition. See in-line comments for the
  // open vs close branches; the actual measurement trick lives in
  // `measureFnFormHeight()` below so this block reads as
  // "snap-start, compute target, set, listen for end".
  useLayoutEffect(() => {
    const el = wrapperRef.current;
    if (!el) return;
    if (fnFormClosing) {
      return runCloseTransition(el, chatHeightRef.current, () => {
        setTransitioning(false);
        onCloseComplete();
      }, setTransitioning);
    }
    // fn-form OR a system decision: grow the wrapper to content height.
    // decisionKey in deps so switching one decision → the next re-runs
    // the open transition against the new content height.
    if (fnFormFunction || decisionKey) {
      return runOpenTransition(el, chatHeightRef.current, setTransitioning);
    }
  }, [fnFormFunction, decisionKey, fnFormClosing, onCloseComplete, wrapperRef]);

  // After the form unmounts, drop the inline `height` we left behind
  // during the close transition so the wrapper can size itself
  // naturally for chat-mode content (textarea auto-resize, etc.).
  useEffect(() => {
    if (morphed) return;
    const el = wrapperRef.current;
    if (!el || !el.style.height) return;
    el.style.height = "";
    el.style.maxHeight = "";
  }, [morphed, wrapperRef]);

  // Type-to-grow up to ~1/4 of the chat view. Expand grows the whole
  // card to ~1/2. Animate only on the expand toggle so typing does
  // not flash, and never mutate body styles while measuring.
  useEffect(() => {
    if (!morphed || fnFormClosing) return;
    const el = wrapperRef.current;
    if (!el) return;
    let last = 0;
    let lastExpanded = el.querySelector("[data-expanded]") != null;
    let measuring = false;
    const apply = (animate: boolean) => {
      if (measuring) return;
      measuring = true;
      const host = hostViewHeight(el);
      const avail = availableComposerHeight(el);
      const expanded = !!el.querySelector("[data-expanded]");
      const half = Math.max(160, host * 0.5);
      const next = expanded
        ? Math.min(half, avail)
        : Math.min(measureFnFormHeight(el), avail);
      measuring = false;
      if (expanded) el.setAttribute("data-form-expanded", "");
      else el.removeAttribute("data-form-expanded");
      if (Math.abs(next - last) < 1 && expanded === lastExpanded) return;
      last = next;
      lastExpanded = expanded;
      if (!animate) el.style.transition = "none";
      el.style.height = `${next}px`;
      el.style.maxHeight = `${avail}px`;
      if (!animate) {
        void el.offsetHeight;
        el.style.transition = "";
      }
    };
    const onResize = () => apply(false);
    const id = requestAnimationFrame(() => apply(false));
    const ro = new ResizeObserver(() => {
      if (el.querySelector("[data-expanded]")) return;
      apply(false);
    });
    const body = el.querySelector("[data-fn-form-body]");
    if (body) ro.observe(body);
    const mo = new MutationObserver(() => apply(true));
    mo.observe(el, { subtree: true, attributes: true, attributeFilter: ["data-expanded"] });
    window.addEventListener("resize", onResize);
    return () => {
      cancelAnimationFrame(id);
      ro.disconnect();
      mo.disconnect();
      window.removeEventListener("resize", onResize);
    };
  }, [morphed, fnFormClosing, fnFormFunction, decisionKey, wrapperRef]);



  return { outgoingFn };
}

/* ---- transition primitives ---------------------------------------- */

/**
 * Close — animate wrapper shrink WITH the form still mounted, so the
 * body / header retreat downward (mirror image of the open animation
 * where they emerge upward). The `.closing` class on header/body
 * fades opacity 1→0 in parallel.
 *
 * On transitionend we unmount the form (via `onComplete`, which the
 * hook wires to `closeFnFormStore() + setClosing(false)`) — only
 * then can the inline `height` be cleared, otherwise the wrapper
 * momentarily snaps back to fn-form natural height while React is
 * still committing the unmount.
 */
function runCloseTransition(
  el: HTMLDivElement,
  chatHeight: number,
  onComplete: () => void,
  setTransitioning: (v: boolean) => void,
): () => void {
  setTransitioning(true);
  const current = el.offsetHeight;
  el.style.height = `${current}px`;
  void el.offsetHeight;
  el.style.height = `${chatHeight}px`;
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
  const raw = header.offsetHeight + body.offsetHeight + padBottom;
  return raw;
}

function hostViewHeight(el: HTMLDivElement): number {
  const host = (
    (el.closest("#chatView") as HTMLElement | null)
    || ((el.closest("[data-composer-input-area]") as HTMLElement | null)
      ?.offsetParent as HTMLElement | null)
  );
  const h = host?.clientHeight ?? 0;
  return h >= 80 ? h : window.innerHeight;
}

/** Room left for the wrapper after env chips, the detached controls
 *  row, and inputArea padding. Keeps send / close and those chips
 *  on screen. */
function availableComposerHeight(el: HTMLDivElement): number {
  const area = el.closest("[data-composer-input-area]") as HTMLElement | null;
  const host = (
    (area?.offsetParent as HTMLElement | null)
    || (el.closest("#chatView") as HTMLElement | null)
    || (area?.parentElement as HTMLElement | null)
  );
  const env = area?.querySelector("[data-environment-row]") as HTMLElement | null;
  const controls = area?.querySelector(".composer-bottom-row") as HTMLElement | null;
  const cs = area ? getComputedStyle(area) : null;
  const pad = cs
    ? parseFloat(cs.paddingTop) + parseFloat(cs.paddingBottom)
    : 0;
  const envH = env
    ? env.offsetHeight + parseFloat(getComputedStyle(env).marginBottom || "0")
    : 0;
  const controlsH = controls
    ? controls.offsetHeight + parseFloat(getComputedStyle(controls).marginTop || "0")
    : 0;
  let hostH = host?.clientHeight ?? 0;
  if (hostH < 80 && area) {
    const rect = area.getBoundingClientRect();
    hostH = Math.max(hostH, rect.bottom);
  }
  if (hostH < 80) hostH = window.innerHeight;
  return Math.max(160, hostH - envH - controlsH - pad);
}

