/**
 * Composer — chat input area.
 *
 * Owns: input value, attachments, slash menu, plus menu (Tools / Web
 * Search), thinking-effort selector, token badge, send/stop button.
 * Submits chat turns directly via the WS channel; no legacy globals.
 *
 * Styling lives in ./composer.module.css. The page-level chat layout
 * (chat-area, welcome screen, message list, etc.) is still rendered
 * by the legacy template for the moment and will be migrated in
 * subsequent slices.
 */
"use client";

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";

import { useSessionStore, type AgenticFunction } from "@/lib/session-store";

import { ContextBadge } from "../context-badge";
import { FunctionForm, visibleParams } from "./fn-form";
import {
  CaretIcon,
  PlusIcon,
  SendIcon,
  StopIcon,
  ToolsIcon,
  WebSearchIcon,
} from "./icons";
import { PlusMenuItem, ToolChip } from "./menu-pieces";
import { type SlashCommand } from "./slash-commands";
import { useFnFormState } from "./use-fn-form-state";
import { useSlashMenu } from "./use-slash-menu";
import { useThinkingEffort } from "./use-thinking-effort";
import { useToolsToggles } from "./use-tools-toggles";
import styles from "./composer.module.css";

/* Single shared WebSocket. The legacy chat-ws.js script opens it as
   `window.ws`; this is the only point in the React layer that touches
   the global. When the WS layer is migrated (next slice), this helper
   is replaced by ``useWS().send`` and the call sites stay identical. */
function wsSend(payload: unknown): boolean {
  const w = window as Window & { ws?: WebSocket };
  if (!w.ws || w.ws.readyState !== WebSocket.OPEN) return false;
  w.ws.send(typeof payload === "string" ? payload : JSON.stringify(payload));
  return true;
}

const noop = () => {};

export function Composer() {
  const currentSessionId = useSessionStore((s) => s.currentSessionId);
  const runningTask = useSessionStore((s) => s.runningTask);
  const input = useSessionStore((s) => s.composerInput);
  const setInput = useSessionStore((s) => s.setComposerInput);
  const focusTick = useSessionStore((s) => s.composerFocusTick);
  const fnFormFunction = useSessionStore((s) => s.fnFormFunction);
  const closeFnFormStore = useSessionStore((s) => s.closeFnForm);
  const send = wsSend;

  const isRunning = runningTask !== null;
  const fnFormActive = fnFormFunction !== null;

  // Thinking-effort + plus-menu + tools toggles each live in their own
  // dedicated hooks now — see ./use-thinking-effort, ./use-tools-toggles.
  const {
    thinking,
    options: thinkingOptions,
    menuOpen: thinkingMenuOpen,
    setMenuOpen: setThinkingMenuOpen,
    pick: pickThinking,
  } = useThinkingEffort();
  const [plusMenuOpen, setPlusMenuOpen] = useState(false);
  const {
    tools: toolsEnabled,
    webSearch: webSearchEnabled,
    toggleTools,
    toggleWebSearch,
  } = useToolsToggles();
  // Slash-menu state lives in its own hook (./use-slash-menu).
  // fn-form field state (values, workdir, error highlight, closing
  // flag) is owned by `./use-fn-form-state`; it also runs the
  // default-value seeding effect on fn change.
  const fnForm = useFnFormState(fnFormFunction);
  // `displayFn` lags the store's `fnFormFunction` by one render so we
  // can capture the previous fn into `outgoingFn` before React replaces
  // its DOM. Outgoing renders as an absolutely-positioned overlay that
  // fades out while the new fn-form fades in underneath, giving a
  // proper crossfade instead of a snap.
  const [outgoingFn, setOutgoingFn] = useState<AgenticFunction | null>(null);
  // Track the previous store value so we can spot A → B transitions.
  const prevFnRef = useRef<AgenticFunction | null>(null);

  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const wrapperRef = useRef<HTMLDivElement>(null);
  const sendBtnRef = useRef<HTMLButtonElement>(null);

  // Crossfade on fn-form switch: when the store flips from fn A to fn
  // B (both non-null), stash A in `outgoingFn` so its DOM stays
  // mounted as an absolute overlay while the new fn-form fades in
  // underneath. The overlay's CSS animation drops opacity 1 → 0 over
  // one composer fade duration; we clear `outgoingFn` shortly after
  // (slightly longer than the fade so the unmount happens after the
  // visual transition completes).
  useLayoutEffect(() => {
    const prev = prevFnRef.current;
    prevFnRef.current = fnFormFunction;
    if (prev && fnFormFunction && prev !== fnFormFunction) {
      setOutgoingFn(prev);
    }
  }, [fnFormFunction]);

  useEffect(() => {
    if (!outgoingFn) return;
    const id = setTimeout(() => setOutgoingFn(null), 300);
    return () => clearTimeout(id);
  }, [outgoingFn]);

  // Wrapper height transition — declarative CSS transition + a single
  // rAF to commit the "starting" height for the browser to interpolate
  // FROM. No state, no rAF juggling, no transitionend race:
  //
  // Open: useLayoutEffect runs after React commits the panel into the
  // DOM (panel intrinsic height is now measurable). We synchronously
  // pin wrapper.style.height to the previously-cached chat-mode
  // height. That gives the next paint a known starting point. One
  // rAF later we set height to panel.scrollHeight + 54 — the CSS
  // transition does the rest.
  //
  // Close: previous effect left inline height at fn-form-h. The new
  // run sets it to the cached chat-mode height; the CSS transition
  // shrinks the wrapper, and on transitionend we clear inline so the
  // wrapper goes back to natural-auto.
  const chatHeightRef = useRef<number>(98);
  // True from the moment a wrapper-height transition starts until
  // `transitionend` fires. Currently only used to gate chat-height
  // caching (don't measure a value that's mid-animation).
  const [wrapperTransitioning, setWrapperTransitioning] = useState(false);
  // Pixels: action-button offset (16) + size (32). The fn-form steady
  // state pins the button to `wrapper.height - ACTION_BTN_BOTTOM_OFFSET`
  // so its bottom edge sits `--composer-button-offset` above the
  // wrapper's bottom. Kept here as a constant to avoid reading the
  // CSS variable in JS — the tokens it maps to live in 01-base.css.
  const ACTION_BTN_BOTTOM_OFFSET = 48;

  useEffect(() => {
    // Cache natural chat-mode height while we're in chat mode AND not
    // currently animating (no inline `height`). Updated continuously
    // so textarea auto-resize doesn't desync.
    if (fnFormFunction || wrapperTransitioning) return;
    const el = wrapperRef.current;
    if (!el || el.style.height) return;
    chatHeightRef.current = el.offsetHeight;
  });

  useLayoutEffect(() => {
    const el = wrapperRef.current;
    if (!el) return;
    if (fnForm.closing) {
      // Close — animate wrapper shrink WITH the form still mounted, so
      // the body / header retreat downward into the bottom row (mirror
      // image of the open animation where they emerge upward out of
      // it). The `.closing` class on header/body fades opacity 1→0 in
      // parallel with the height transition. The action button glides
      // back to the chat-mode top in lockstep (same transition curve).
      // Once height settles we unmount the form (closeFnFormStore) —
      // only then can the inline `height` be cleared, otherwise the
      // wrapper momentarily snaps back to fn-form natural height while
      // React is still committing the unmount.
      setWrapperTransitioning(true);
      const current = el.offsetHeight;
      el.style.height = `${current}px`;
      void el.offsetHeight;
      el.style.height = `${chatHeightRef.current}px`;
      const btn = sendBtnRef.current;
      if (btn) btn.style.top = `${16}px`;
      const onEnd = (ev: TransitionEvent) => {
        if (ev.target !== el || ev.propertyName !== "height") return;
        el.removeEventListener("transitionend", onEnd);
        closeFnFormStore();
        fnForm.setClosing(false);
        setWrapperTransitioning(false);
      };
      el.addEventListener("transitionend", onEnd);
      return () => el.removeEventListener("transitionend", onEnd);
    }
    if (fnFormFunction) {
      setWrapperTransitioning(true);
      // Starting height for the CSS transition:
      //   * chat → fn-form: wrapper has no inline height (chat-mode
      //     auto-sizes). Snap to the cached `chatHeight` first +
      //     force a reflow so the browser registers it as the
      //     transition origin.
      //   * fn-form A → fn-form B: wrapper already has an inline
      //     height equal to A's natural size — leave it untouched and
      //     just transition straight to B's natural size below.
      if (!el.style.height) {
        el.style.height = `${chatHeightRef.current}px`;
        void el.offsetHeight;
      }
      // Compute the target wrapper height by measuring the form
      // contents directly. We CAN'T trust `body.scrollHeight` here:
      // body has `flex:1 + overflow-y:auto`, so when the wrapper's
      // inline height is currently large (e.g. a previous, taller fn
      // is still showing), the body is also large and any small
      // content (the new fn) fits inside it — scrollHeight ends up
      // equal to body's box height, not its content size, which
      // would lock the wrapper at the old big height.
      //
      // Workaround: temporarily take body out of the flex constraint
      // and let it size to its content (`height:auto`, `flex:0 0
      // auto`, `overflow:visible`), read its `offsetHeight`, then
      // restore the inline styles.
      const header = el.querySelector(
        "[data-fn-form-header]",
      ) as HTMLElement | null;
      const body = el.querySelector(
        "[data-fn-form-body]",
      ) as HTMLElement | null;
      const padBottom = parseFloat(getComputedStyle(el).paddingBottom);
      let natural: number;
      if (header && body) {
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
        natural = header.offsetHeight + bodyContentH + padBottom;
      } else {
        natural = el.scrollHeight;
      }
      el.style.height = `${natural}px`;
      // Glide the action button from its current top (chat: 16, or
      // previous fn-form's bottom) to the new fn-form bottom
      // (natural − offset). Same CSS transition curve as the wrapper
      // height so they stay in sync visually.
      const btn = sendBtnRef.current;
      if (btn) btn.style.top = `${natural - ACTION_BTN_BOTTOM_OFFSET}px`;
      const onEnd = (ev: TransitionEvent) => {
        if (ev.target !== el || ev.propertyName !== "height") return;
        setWrapperTransitioning(false);
        el.removeEventListener("transitionend", onEnd);
      };
      el.addEventListener("transitionend", onEnd);
      return () => {
        el.removeEventListener("transitionend", onEnd);
      };
    }
  }, [fnFormFunction, fnForm, closeFnFormStore]);

  // After the form unmounts, drop the inline `height` we left behind
  // during the close transition so the wrapper can size itself
  // naturally for chat-mode content (textarea auto-resize, etc.).
  useEffect(() => {
    if (fnFormFunction) return;
    const el = wrapperRef.current;
    if (!el || !el.style.height) return;
    el.style.height = "";
  }, [fnFormFunction]);

  // No ResizeObserver here: with hover-expand replaced by native
  // tooltips, body content size doesn't change at runtime — the
  // wrapper height only needs to be set once per fn (by the open /
  // switch useLayoutEffect above) and stays put.

  // Auto-resize the textarea as content changes.
  useEffect(() => {
    const t = textareaRef.current;
    if (!t) return;
    t.style.height = "auto";
    t.style.height = `${Math.min(t.scrollHeight, 200)}px`;
  }, [input]);

  // External focus requests via the store (welcome buttons,
  // retry helpers, etc.).
  useEffect(() => {
    if (focusTick === 0) return;
    textareaRef.current?.focus();
  }, [focusTick]);

  // Close any open popovers when clicking outside.
  useEffect(() => {
    function onDoc(ev: MouseEvent) {
      const t = ev.target as Node | null;
      if (!t) return;
      const wrapper = textareaRef.current?.closest(`.${styles.inputWrapper}`);
      if (wrapper && !wrapper.contains(t)) {
        setPlusMenuOpen(false);
        setThinkingMenuOpen(false);
      }
    }
    document.addEventListener("click", onDoc);
    return () => document.removeEventListener("click", onDoc);
  }, []);

  // Slash menu (state + open/close timing + command dispatch).
  const slash = useSlashMenu({ input, textareaRef, send });

  /* ---- Submit -------------------------------------------------------- */

  const submit = useCallback(() => {
    if (isRunning) return;
    const trimmed = input.trim();
    if (!trimmed) return;
    if (slash.query !== null && slash.runCommand(trimmed)) {
      setInput("");
      slash.close();
      return;
    }
    const ok = send({
      action: "chat",
      text: trimmed,
      session_id: currentSessionId ?? null,
      thinking_effort: thinking,
      tools: toolsEnabled,
      web_search: webSearchEnabled,
    });
    if (!ok) return; // ws not open — leave input intact so the user can retry
    setInput("");
    slash.close();
  }, [
    currentSessionId,
    input,
    isRunning,
    send,
    setInput,
    slash,
    thinking,
    toolsEnabled,
    webSearchEnabled,
  ]);

  function stop() {
    if (!currentSessionId) return;
    send({ action: "stop", session_id: currentSessionId });
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  }

  function onMenuItemClick(cmd: SlashCommand) {
    setInput(cmd.args ? `${cmd.name} ` : cmd.name);
    requestAnimationFrame(() => textareaRef.current?.focus());
  }

  /* ---- Function form submit ---------------------------------------- */

  // Close = mirror of open. Flip `fnFormClosing` so the
  // wrapper-height useLayoutEffect runs its shrink branch while the
  // form is still mounted; header/body fade out in parallel via the
  // `.closing` class. Store unmount happens after the height
  // transition ends (handled inside the useLayoutEffect).
  const handleFnFormClose = useCallback(() => {
    fnForm.setClosing(true);
  }, [fnForm]);

  const submitFnForm = useCallback(() => {
    if (!fnFormFunction || isRunning) return;
    const fn = fnFormFunction;
    const workdirMode = fn.workdir_mode ?? "optional";
    const wd = fnForm.workdir.trim();
    if (workdirMode === "required" && !wd) {
      fnForm.setError("__workdir");
      return;
    }

    const parts: string[] = ["run", fn.name];
    for (const p of visibleParams(fn)) {
      const isBool = p.type === "bool" || p.type === "boolean";
      let v = (fnForm.values[p.name] ?? "").trim();
      if (!v && isBool) v = "False";
      if (!v && !p.required) continue;
      if (!v && p.required) {
        fnForm.setError(p.name);
        return;
      }
      if (v.indexOf(" ") !== -1 || v.indexOf('"') !== -1) {
        parts.push(`${p.name}=${JSON.stringify(v)}`);
      } else {
        parts.push(`${p.name}=${v}`);
      }
    }
    if (workdirMode !== "hidden") {
      if (wd.indexOf(" ") !== -1 || wd.indexOf('"') !== -1) {
        parts.push(`work_dir=${JSON.stringify(wd)}`);
      } else {
        parts.push(`work_dir=${wd}`);
      }
    }

    const command = parts.join(" ");
    const ok = send({
      action: "chat",
      text: command,
      session_id: currentSessionId ?? null,
      thinking_effort: thinking,
      tools: toolsEnabled,
      web_search: webSearchEnabled,
    });
    if (!ok) return;
    handleFnFormClose();
  }, [
    currentSessionId,
    fnFormFunction,
    fnForm,
    handleFnFormClose,
    isRunning,
    send,
    thinking,
    toolsEnabled,
    webSearchEnabled,
  ]);

  const onSendButtonClick = fnFormActive ? submitFnForm : submit;
  // In chat mode: disabled when textarea is empty.
  // In fn-form mode: disabled when any required param has no value,
  // OR when workdir is required and empty.
  const sendDisabled = fnFormActive
    ? (() => {
        const fn = fnFormFunction!;
        const workdirMode = fn.workdir_mode ?? "optional";
        if (workdirMode === "required" && !fnForm.workdir.trim()) return true;
        for (const p of visibleParams(fn)) {
          if (!p.required) continue;
          const v = (fnForm.values[p.name] ?? "").trim();
          if (!v) return true;
        }
        return false;
      })()
    : !input.trim();
  const sendTitle = fnFormActive ? "Run" : "Send message";

  /* ---- Render -------------------------------------------------------- */

  const anyToolActive = toolsEnabled || webSearchEnabled;

  return (
    <div className={styles.inputArea}>
      <div className={styles.slashClip}>
        {slash.visible && (
          <div
            className={`${styles.slashMenu} ${slash.closing ? styles.closing : styles.opening}`}
          >
            {slash.matches.map((c) => (
              <div
                key={c.name}
                className={styles.slashMenuItem}
                onMouseDown={(e) => {
                  e.preventDefault();
                  onMenuItemClick(c);
                }}
              >
                <span className={styles.slashMenuName}>{c.name}</span>
                {c.args ? (
                  <>
                    {" "}
                    <span className={styles.slashMenuArgs}>{c.args}</span>
                  </>
                ) : null}
                <div className={styles.slashMenuDesc}>{c.description}</div>
              </div>
            ))}
          </div>
        )}
      </div>

      <div
        ref={wrapperRef}
        className={`${styles.inputWrapper} ${fnFormActive ? styles.fnFormMode : ""}`}
      >
        {fnFormFunction ? (
          <FunctionForm
            // `key` ties to fn name so React re-mounts on every
            // switch — the freshly mounted header/body run their own
            // fadeIn animation, completing the crossfade with the
            // outgoing overlay below.
            key={fnFormFunction.name}
            fn={fnFormFunction}
            values={fnForm.values}
            setValue={fnForm.setValue}
            workdir={fnForm.workdir}
            setWorkdir={fnForm.setWorkdir}
            errorParam={fnForm.error}
            closing={fnForm.closing}
            onClose={handleFnFormClose}
            onSubmit={submitFnForm}
          />
        ) : (
          <div key="top-half" className={styles.inputTopRow}>
            <textarea
              ref={textareaRef}
              id="composer-chat-input"
              name="chat_input"
              autoComplete="off"
              className={styles.chatInput}
              placeholder=" create / run / edit or ask anything... (type / for commands)"
              rows={1}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={onKeyDown}
            />
          </div>
        )}
        {/* Outgoing fn-form overlay — only present during a fn → fn
            switch. Rendered AFTER the main form so that
            `querySelector('[data-fn-form-header]')` in the wrapper
            height measurement matches the main form first (the
            outgoing layer's cloned header/body would otherwise lock
            wrapper height to the previous form's size). Absolute +
            z-index 1 still puts it visually on top during the fade. */}
        {outgoingFn && (
          <div
            key={`${outgoingFn.name}-outgoing`}
            className={styles.outgoingLayer}
            aria-hidden="true"
          >
            <FunctionForm
              fn={outgoingFn}
              values={{}}
              setValue={noop}
              workdir=""
              setWorkdir={noop}
              errorParam={null}
              onClose={noop}
              onSubmit={noop}
            />
          </div>
        )}

        <div key="bottom-row" className={styles.inputBottomRow}>
          <div className={styles.inputOptions}>
            <button
              className={`${styles.plusBtn} ${anyToolActive ? styles.hasActive : ""}`}
              onClick={(e) => {
                e.stopPropagation();
                setPlusMenuOpen((v) => !v);
                setThinkingMenuOpen(false);
              }}
              title="Add tools, files, and more"
              aria-label="More options"
              type="button"
            >
              <PlusIcon />
            </button>

            <div className={styles.activeToolChips}>
              {toolsEnabled && (
                <ToolChip
                  icon={<ToolsIcon size={16} />}
                  label="Tools"
                  onRemove={toggleTools}
                />
              )}
              {webSearchEnabled && (
                <ToolChip
                  icon={<WebSearchIcon size={16} />}
                  label="Web Search"
                  onRemove={toggleWebSearch}
                />
              )}
            </div>

            {plusMenuOpen && (
              <div
                className={styles.plusMenu}
                onClick={(e) => e.stopPropagation()}
              >
                <PlusMenuItem
                  active={toolsEnabled}
                  onClick={toggleTools}
                  icon={<ToolsIcon />}
                  label="Tools"
                  title="Shell, read/write/edit, grep/glob, list, patch, todo"
                />
                <PlusMenuItem
                  active={webSearchEnabled}
                  onClick={toggleWebSearch}
                  icon={<WebSearchIcon />}
                  label="Web Search"
                  title="Give the agent web search this turn"
                />
              </div>
            )}

            <div
              className={`${styles.thinkingSelector} ${thinkingMenuOpen ? styles.open : ""}`}
              onClick={(e) => {
                e.stopPropagation();
                setThinkingMenuOpen((v) => !v);
                setPlusMenuOpen(false);
              }}
            >
              <span>effort: {thinking}</span>
              <CaretIcon className={styles.thinkingArrow} />
            </div>
            {thinkingMenuOpen && (
              <div className={styles.thinkingMenu}>
                {thinkingOptions.map((opt) => (
                  <button
                    key={opt.value}
                    className={`${styles.thinkingMenuItem} ${opt.value === thinking ? styles.active : ""}`}
                    onClick={() => pickThinking(opt.value)}
                    type="button"
                    title={opt.desc ?? ""}
                  >
                    {opt.value}
                  </button>
                ))}
              </div>
            )}
          </div>
          <div className={styles.inputBottomRight}>
            <ContextBadge />
          </div>
        </div>

        {/* Single send/stop button anchored at the wrapper level.
            `top` is mutated via inline style by the wrapper-height
            useLayoutEffect so the button glides between its chat-mode
            position (top: 16) and the fn-form position
            (top: wrapper.height − 48) over the same 0.3s curve as the
            wrapper itself — one continuous motion instead of a row-to
            -row teleport. */}
        <button
          ref={sendBtnRef}
          className={`${styles.actionBtn} ${isRunning ? styles.stopBtn : styles.sendBtn}`}
          onClick={isRunning ? stop : onSendButtonClick}
          disabled={!isRunning && sendDisabled}
          title={isRunning ? "Stop" : sendTitle}
          type="button"
        >
          {isRunning ? <StopIcon /> : <SendIcon />}
        </button>

        {/* Close button — wrapper-level so it stays mounted across
            fn-form switches (no blink on the icon when the header
            unmounts/remounts with a new key). Only visible while
            fn-form is open and not in the middle of closing. */}
        {fnFormActive && !fnForm.closing && (
          <button
            className={styles.closeBtn}
            type="button"
            onClick={handleFnFormClose}
            onMouseDown={(e) => e.preventDefault()}
            tabIndex={-1}
            title="Close"
            aria-label="Close"
          >
            <svg viewBox="0 0 12 12" width="14" height="14" aria-hidden="true">
              <path
                d="M2 2L10 10M10 2L2 10"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.6"
                strokeLinecap="round"
              />
            </svg>
          </button>
        )}
      </div>
    </div>
  );
}

