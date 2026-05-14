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

import React, {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";

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
import { Slider } from "@/components/ui/slider";
import { Lightning } from "@phosphor-icons/react/dist/ssr";
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
    set: setThinking,
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
  // Refs:
  //  - `plusTriggerRef` / `plusMenuRef`: the plus menu is still portal'd
  //    into `document.body` to escape `.inputWrapper { overflow: hidden }`.
  //    We measure the trigger to place the popover and use the menu ref
  //    so the click-outside handler treats clicks inside the menu as
  //    "still inside the composer".
  //  - `thinkingTriggerRef`: the effort pill expands inline (no portal).
  //    Since it lives inside `.inputWrapper`, the wrapper-contains check
  //    already covers clicks on it — no separate menu ref needed.
  const thinkingTriggerRef = useRef<HTMLDivElement>(null);
  const plusTriggerRef = useRef<HTMLButtonElement>(null);
  const plusMenuRef = useRef<HTMLDivElement>(null);
  const [plusMenuPos, setPlusMenuPos] = useState<
    { left: number; bottom: number } | null
  >(null);

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

  // Close popovers on outside click — but the two popovers have
  // different "what counts as outside" rules:
  //
  // - Effort pill is INLINE inside the composer wrapper. A click in
  //   the textarea or on any other composer control should collapse
  //   it (otherwise expanded state lingers as the user keeps typing).
  //   So we check `thinkingTriggerRef.contains` directly — anything
  //   outside the pill itself counts as outside.
  //
  // - Plus menu is PORTAL'D into `document.body` to escape
  //   `.inputWrapper { overflow: hidden }`. Its trigger is in the
  //   wrapper but its menu lives at the document root, so the "stays
  //   open" set is `wrapper ∪ plusMenuRef`. Anywhere else closes it.
  useEffect(() => {
    function onDoc(ev: MouseEvent) {
      const t = ev.target as Node | null;
      if (!t) return;
      const wrapper = textareaRef.current?.closest(`.${styles.inputWrapper}`);
      if (!wrapper) return;

      if (
        thinkingTriggerRef.current &&
        !thinkingTriggerRef.current.contains(t)
      ) {
        setThinkingMenuOpen(false);
      }
      if (!wrapper.contains(t) && !plusMenuRef.current?.contains(t)) {
        setPlusMenuOpen(false);
      }
    }
    document.addEventListener("click", onDoc);
    return () => document.removeEventListener("click", onDoc);
  }, []);

  useLayoutEffect(() => {
    if (!plusMenuOpen) {
      setPlusMenuPos(null);
      return;
    }
    const trigger = plusTriggerRef.current;
    if (!trigger) return;
    const rect = trigger.getBoundingClientRect();
    setPlusMenuPos({
      left: rect.left,
      bottom: window.innerHeight - rect.top + 4,
    });
  }, [plusMenuOpen]);

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
              ref={plusTriggerRef}
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

            {plusMenuOpen && plusMenuPos && typeof document !== "undefined"
              ? createPortal(
                  <div
                    ref={plusMenuRef}
                    className={styles.plusMenu}
                    onClick={(e) => e.stopPropagation()}
                    style={{
                      position: "fixed",
                      left: plusMenuPos.left,
                      bottom: plusMenuPos.bottom,
                      top: "auto",
                      marginBottom: 0,
                    }}
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
                  </div>,
                  document.body,
                )
              : null}

            <ThinkingEffortPill
              ref={thinkingTriggerRef}
              expanded={thinkingMenuOpen}
              onToggle={() => {
                setThinkingMenuOpen((v) => !v);
                setPlusMenuOpen(false);
              }}
              options={thinkingOptions}
              value={thinking}
              onChange={setThinking}
            />
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

/**
 * Effort pill — the trigger IS the picker.
 *
 * Collapsed: a pill that reads `effort: medium ⌄`, sized to its content
 * (~131px). Click to expand.
 *
 * Expanded: the same pill animates its width out to ~340px and the
 * caret/text content swaps for `{value}` + an inline `<Slider />`.
 * Dragging the slider only updates the value — it doesn't collapse.
 * Closes when the user clicks anywhere outside the composer wrapper
 * (the document-level click-outside handler in the parent flips
 * `thinkingMenuOpen` back to `false`).
 *
 * Layout: the pill is wrapped in a `position: relative` host that
 * keeps the collapsed footprint reserved in the bottom-row flex flow.
 * The visible pill is `position: absolute` on top of that footprint,
 * so when it expands to 340px it floats over the rest of the row
 * without shoving the context badge / other controls aside.
 */
const ThinkingEffortPill = React.forwardRef<
  HTMLDivElement,
  {
    expanded: boolean;
    onToggle: () => void;
    options: { value: string; desc?: string }[];
    value: string;
    onChange: (v: string) => void;
  }
>(function ThinkingEffortPill(
  { expanded, onToggle, options, value, onChange },
  ref,
) {
  const valueIndex = Math.max(
    0,
    options.findIndex((o) => o.value === value),
  );
  const maxIndex = Math.max(0, options.length - 1);
  // Lightning size scales linearly with effort: from 10px at the
  // `off` end to 22px at `xhigh`. A single bolt rides on the thumb
  // — its position tells "where on the scale" and its size tells
  // "how much effort" at a glance.
  const lightningSize =
    maxIndex > 0
      ? Math.round(10 + (valueIndex / maxIndex) * 12)
      : 10;
  // Effort-level tint for the COLLAPSED pill. Ramps from a faint
  // bright-white wash at `off` (just barely lifting off the panel
  // bg) through accent-yellow / orange / red as effort climbs.
  // Each step is a `color-mix(... XX%, transparent)` so the tint
  // sits softly on the surface — same "糊" feel as the soft blue
  // already used inside the slider. Per-value overrides hardcoded
  // to match the backend's standard effort vocabulary; unknown
  // values fall back to the neutral whitish wash. */
  const collapsedTint =
    {
      off: "color-mix(in srgb, var(--text-bright) 10%, transparent)",
      minimal: "color-mix(in srgb, var(--accent-yellow) 22%, transparent)",
      low: "color-mix(in srgb, var(--accent-yellow) 32%, transparent)",
      medium: "color-mix(in srgb, var(--accent-orange) 28%, transparent)",
      high: "color-mix(in srgb, var(--accent-orange) 38%, transparent)",
      xhigh: "color-mix(in srgb, var(--accent-red) 32%, transparent)",
    }[value] ?? "color-mix(in srgb, var(--text-bright) 10%, transparent)";

  // Active hue for the slider's filled elements (range bar, filled
  // tick dots, thumb, selected end-bolt). Same effort-level ramp as
  // the collapsed pill tint, but at a higher opacity so the colour
  // pops against the unfilled grey track. Passed down via a CSS
  // custom property (`--slider-active`) so the slider's Tailwind
  // classes can pick it up without prop-drilling colours.
  const activeColor =
    {
      off: "color-mix(in srgb, var(--text-bright) 45%, transparent)",
      minimal: "color-mix(in srgb, var(--accent-yellow) 70%, transparent)",
      low: "color-mix(in srgb, var(--accent-yellow) 70%, transparent)",
      medium: "color-mix(in srgb, var(--accent-orange) 70%, transparent)",
      high: "color-mix(in srgb, var(--accent-orange) 70%, transparent)",
      xhigh: "color-mix(in srgb, var(--accent-red) 70%, transparent)",
    }[value] ?? "color-mix(in srgb, var(--accent-yellow) 70%, transparent)";

  // Measure the spacer so the collapsed pill width exactly matches
  // its content. Hard-coding 132px gave the same chip the same
  // footprint regardless of label text — `effort: xhigh` left a
  // ~30px trailing gap. Re-measures whenever the value changes.
  const spacerRef = useRef<HTMLSpanElement>(null);
  const [collapsedWidth, setCollapsedWidth] = useState<number>(120);
  useLayoutEffect(() => {
    if (spacerRef.current) {
      setCollapsedWidth(spacerRef.current.offsetWidth);
    }
  }, [value]);

  return (
    <div
      ref={ref}
      className="relative inline-flex h-[32px] items-center"
    >
      {/* Invisible spacer keeps the collapsed pill's footprint in the
          flex layout so expanding doesn't push the context badge or
          other controls. Mirrors the collapsed pill content exactly. */}
      <span
        ref={spacerRef}
        aria-hidden="true"
        // `whitespace-nowrap` + `shrink-0` keep the spacer measuring
        // its FULL single-line content width even when the parent
        // flex row would otherwise compress it (which would wrap the
        // text and make the spacer report a too-narrow offsetWidth,
        // dragging the pill down with it).
        className="invisible inline-flex shrink-0 items-center gap-[5px] px-[10px] text-[14px] whitespace-nowrap"
      >
        <span>effort: {value}</span>
        <CaretIcon />
      </span>

      {/* Visible pill. Only the WIDTH and the background colour
          animate — the two content layers below switch via
          `display: none` rather than opacity, so there's no
          fade-in/fade-out crossfade (the user explicitly wanted
          this gone). The slider lives in a fixed-260px-wide layer,
          so as the pill expands the slider's internal layout never
          recalculates — `overflow: hidden` on the pill just reveals
          progressively more of the same stable layer.

          Pill widths: 132px collapsed → 260px expanded (narrower
          than the previous 340 since the slider track + icons
          read fine in a tighter footprint). */}
      <div
        className={[
          "absolute left-0 top-0 h-[32px] overflow-hidden",
          "rounded-full cursor-pointer select-none",
          "text-[14px]",
          "transition-[width,background-color] duration-[220ms] ease-out",
          expanded ? "bg-bg-hover text-text-bright" : "text-text-primary",
        ].join(" ")}
        style={{
          width: expanded ? 260 : collapsedWidth,
          // Tint the collapsed pill by current effort level (neutral
          // white-grey at `off`, ramps to soft red at `xhigh`). When
          // expanded we hand the bg back to the Tailwind class
          // (`bg-bg-hover`) above and skip the inline override.
          ...(expanded ? {} : { backgroundColor: collapsedTint }),
          // CSS variable inherited by the slider inside — its
          // range / ticks / thumb / selected end-bolt all read
          // `var(--slider-active)` for their filled colour.
          ["--slider-active" as string]: activeColor,
        } as React.CSSProperties}
        onClick={(e) => {
          e.stopPropagation();
          onToggle();
        }}
      >
        {/* Collapsed content. `hidden` (display: none) when expanded
            so there's no overlap / fade — instant swap on toggle.
            `whitespace-nowrap` keeps the label on a single line at
            the same width the spacer measured. */}
        <div
          className={[
            "h-full flex items-center gap-[5px] px-[10px] whitespace-nowrap",
            expanded ? "hidden" : "",
          ].join(" ")}
        >
          <span>effort: {value}</span>
          <CaretIcon />
        </div>

        {/* Expanded content. Fixed 260px wide so the slider track +
            tick math don't recompute mid-transition. Hidden via
            display:none when collapsed. */}
        <div
          className={[
            "h-full flex items-center gap-[10px] px-[12px]",
            !expanded ? "hidden" : "",
          ].join(" ")}
          style={{ width: 260 }}
        >
          <span className="shrink-0 min-w-[56px] text-center text-text-bright font-medium">
            {value}
          </span>
          <Slider
            min={0}
            max={maxIndex}
            step={1}
            stops={options.length}
            value={[valueIndex]}
            onValueChange={(v) => {
              const idx = v[0] ?? 0;
              const next = options[idx];
              if (next) onChange(next.value);
            }}
            // Stop click from bubbling to the pill's onClick.
            onClick={(e) => e.stopPropagation()}
            // The thumb itself is a Lightning bolt that travels with
            // the value. Size scales from 10px (off) → 22px (xhigh)
            // so position tells you "where" and size tells you
            // "how much" simultaneously. Colour is the same effort
            // hue used by the filled range (via `--slider-active`).
            // `aria-hidden` on the icon since the slider Root
            // already announces value/min/max.
            thumb={
              <>
                {/* Hollow-looking ring around the bolt:
                    - Interior is painted in `bg-bg-hover` (the
                      expanded pill's own background), so it appears
                      "transparent" against the surrounding pill and
                      visually cuts the slider track + any tick at
                      the thumb's position.
                    - The 1px border in soft `text-bright` gives the
                      shape a visible outline so it reads as a ring,
                      not just an invisible mask.
                    Sized 4px wider than the bolt so the cut feels
                    generous and the ring frames the glyph cleanly. */}
                <span
                  aria-hidden="true"
                  // Ring size is fixed at the SMALLEST bolt + 8 (so
                  // 18px). Lightning grows past it as effort rises,
                  // so high-effort glyphs visually burst out of the
                  // ring — gives the slider a much stronger "more
                  // = bigger" signal than a ring that scales lockstep.
                  className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 rounded-full bg-bg-hover border-[3px] border-[color-mix(in_srgb,var(--text-bright)_40%,transparent)]"
                  style={{ width: 18, height: 18 }}
                />
                <Lightning
                  size={lightningSize}
                  weight="fill"
                  className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 text-[var(--slider-active)] pointer-events-none transition-[width,height] duration-150 ease-out"
                  aria-hidden="true"
                />
              </>
            }
            className="flex-1"
          />
        </div>
      </div>
    </div>
  );
});

