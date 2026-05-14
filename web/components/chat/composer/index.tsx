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

import { useCallback, useEffect, useRef, useState } from "react";

import { useSessionStore } from "@/lib/session-store";

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
import { sendChatMessage } from "./legacy-send";
import { useFnFormState } from "./use-fn-form-state";
import { useFnFormWrapper } from "./use-fn-form-wrapper";
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

  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const wrapperRef = useRef<HTMLDivElement>(null);
  const sendBtnRef = useRef<HTMLButtonElement>(null);

  // Wrapper height transition (open / close / A→B switch crossfade)
  // is all in one hook — see `./use-fn-form-wrapper`. `outgoingFn`
  // drives the absolute-positioned crossfade overlay below.
  const { outgoingFn } = useFnFormWrapper({
    fnFormFunction,
    fnFormClosing: fnForm.closing,
    onCloseComplete: useCallback(() => {
      closeFnFormStore();
      fnForm.setClosing(false);
    }, [closeFnFormStore, fnForm]),
    wrapperRef,
    sendBtnRef,
  });

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
    // Delegate to legacy `sendMessage` (chat.js) so the user bubble +
    // welcome-hide + assistant placeholder + isRunning flip all fire
    // before the WS payload goes out. Composer is just the trigger.
    const handled = sendChatMessage({
      text: trimmed,
      thinking,
      toolsEnabled,
      webSearchEnabled,
    });
    if (!handled) {
      // chat.js hasn't loaded yet (shouldn't happen in steady state).
      // Fall back to a raw send so we don't lose the user's text; the
      // welcome-screen / user-bubble update is out of scope here.
      const ok = send({
        action: "chat",
        text: trimmed,
        session_id: currentSessionId ?? null,
        thinking_effort: thinking,
        tools: toolsEnabled,
        web_search: webSearchEnabled,
      });
      if (!ok) return;
    }
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
    // Same legacy hand-off as plain chat — sendMessage detects the
    // `run ...` prefix and renders the runtime block instead of a
    // user message bubble, then writes the WS payload.
    const handled = sendChatMessage({
      text: command,
      thinking,
      toolsEnabled,
      webSearchEnabled,
    });
    if (!handled) {
      const ok = send({
        action: "chat",
        text: command,
        session_id: currentSessionId ?? null,
        thinking_effort: thinking,
        tools: toolsEnabled,
        web_search: webSearchEnabled,
      });
      if (!ok) return;
    }
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

