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
  useMemo,
  useRef,
  useState,
} from "react";

import { useSessionStore } from "@/lib/session-store";

import { ContextBadge } from "../context-badge";
import {
  FunctionForm,
  defaultParamValue,
  visibleParams,
} from "./fn-form";
import {
  CaretIcon,
  PlusIcon,
  SendIcon,
  StopIcon,
  ToolsIcon,
  WebSearchIcon,
} from "./icons";
import { PlusMenuItem, ToolChip } from "./menu-pieces";
import {
  SLASH_COMMANDS,
  type SlashCommand,
  type SlashContext,
} from "./slash-commands";
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

// Default options when legacy providers.js hasn't populated
// `window._thinkingConfig` yet. Real list comes from the backend per
// chat-agent provider and is read live in the Composer below.
const THINKING_LEVELS_FALLBACK = [
  "low",
  "medium",
  "high",
  "xhigh",
] as const;
type ThinkingEffort = string;

interface ThinkingOption {
  value: string;
  desc?: string;
}

const DEFAULT_THINKING: ThinkingEffort = "medium";
const ANIM_MS = 380;

function readThinkingOptions(): ThinkingOption[] {
  const w = window as unknown as {
    _thinkingConfig?: { options?: ThinkingOption[] };
  };
  const opts = w._thinkingConfig?.options;
  if (Array.isArray(opts) && opts.length > 0) return opts;
  return THINKING_LEVELS_FALLBACK.map((v) => ({ value: v }));
}

function persistBool(key: string, value: boolean) {
  try {
    localStorage.setItem(key, value ? "1" : "0");
  } catch {
    /* ignore */
  }
}

function readPersistedBool(key: string): boolean {
  try {
    return localStorage.getItem(key) === "1";
  } catch {
    return false;
  }
}

export function Composer() {
  const currentSessionId = useSessionStore((s) => s.currentSessionId);
  const setCurrentConv = useSessionStore((s) => s.setCurrentConv);
  const runningTask = useSessionStore((s) => s.runningTask);
  const input = useSessionStore((s) => s.composerInput);
  const setInput = useSessionStore((s) => s.setComposerInput);
  const focusTick = useSessionStore((s) => s.composerFocusTick);
  const fnFormFunction = useSessionStore((s) => s.fnFormFunction);
  const closeFnFormStore = useSessionStore((s) => s.closeFnForm);
  const send = wsSend;

  const isRunning = runningTask !== null;
  const fnFormActive = fnFormFunction !== null;

  const [thinking, setThinking] = useState<ThinkingEffort>(DEFAULT_THINKING);
  const [thinkingMenuOpen, setThinkingMenuOpen] = useState(false);
  const [thinkingOptions, setThinkingOptions] = useState<ThinkingOption[]>(
    () => readThinkingOptions(),
  );
  const [plusMenuOpen, setPlusMenuOpen] = useState(false);
  const [toolsEnabled, setToolsEnabled] = useState(false);
  const [webSearchEnabled, setWebSearchEnabled] = useState(false);
  const [slashQuery, setSlashQuery] = useState<string | null>(null);
  const [slashClosing, setSlashClosing] = useState(false);
  const [fnFormValues, setFnFormValues] = useState<Record<string, string>>({});
  const [fnFormWorkdir, setFnFormWorkdir] = useState("");
  const [fnFormError, setFnFormError] = useState<string | null>(null);
  const [fnFormClosing, setFnFormClosing] = useState(false);

  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const wrapperRef = useRef<HTMLDivElement>(null);
  const closeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Seed field state with defaults each time the function changes; also
  // clears errors / workdir between forms.
  useEffect(() => {
    if (!fnFormFunction) {
      setFnFormValues({});
      setFnFormWorkdir("");
      setFnFormError(null);
      setFnFormClosing(false);
      return;
    }
    const seed: Record<string, string> = {};
    for (const p of visibleParams(fnFormFunction)) {
      const v = defaultParamValue(p);
      if (v) seed[p.name] = v;
    }
    setFnFormValues(seed);
    setFnFormWorkdir("");
    setFnFormError(null);
  }, [fnFormFunction]);

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
  // `transitionend` fires. While true, the send button stays anchored
  // to the bottom row even if `fnFormFunction` has already flipped to
  // null — that prevents the button from riding up/down with the
  // wrapper as it animates back to chat height.
  const [wrapperTransitioning, setWrapperTransitioning] = useState(false);

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
    if (fnFormFunction) {
      setWrapperTransitioning(true);
      el.style.height = `${chatHeightRef.current}px`;
      const raf = requestAnimationFrame(() => {
        const prev = el.style.height;
        el.style.height = "";
        const natural = el.scrollHeight;
        el.style.height = prev;
        void el.offsetHeight;
        el.style.height = `${natural}px`;
      });
      const onEnd = (ev: TransitionEvent) => {
        if (ev.target !== el || ev.propertyName !== "height") return;
        setWrapperTransitioning(false);
        el.removeEventListener("transitionend", onEnd);
      };
      el.addEventListener("transitionend", onEnd);
      return () => {
        cancelAnimationFrame(raf);
        el.removeEventListener("transitionend", onEnd);
      };
    } else {
      if (!el.style.height) return;
      setWrapperTransitioning(true);
      el.style.height = `${chatHeightRef.current}px`;
      const onEnd = (ev: TransitionEvent) => {
        if (ev.target !== el || ev.propertyName !== "height") return;
        el.style.height = "";
        setWrapperTransitioning(false);
        el.removeEventListener("transitionend", onEnd);
      };
      el.addEventListener("transitionend", onEnd);
      return () => el.removeEventListener("transitionend", onEnd);
    }
  }, [fnFormFunction]);

  // Hydrate tools / web-search toggles from localStorage.
  useEffect(() => {
    setToolsEnabled(readPersistedBool("agentic_tools_enabled"));
    setWebSearchEnabled(readPersistedBool("agentic_web_search_enabled"));
  }, []);

  // Poll for legacy `window._thinkingConfig` updates — providers.js
  // writes it after agent_settings_changed arrives. Polling at 500ms
  // is plenty (the value only changes on agent switch).
  useEffect(() => {
    let prevSig = "";
    const tick = () => {
      const opts = readThinkingOptions();
      const sig = opts.map((o) => o.value).join("|");
      if (sig !== prevSig) {
        prevSig = sig;
        setThinkingOptions(opts);
        // Snap the selected effort to the new option list's default if
        // the current pick isn't available anymore.
        const w = window as unknown as {
          _thinkingConfig?: { default?: string };
        };
        setThinking((cur) =>
          opts.some((o) => o.value === cur)
            ? cur
            : w._thinkingConfig?.default ?? opts[0]?.value ?? cur,
        );
      }
    };
    tick();
    const id = setInterval(tick, 500);
    return () => clearInterval(id);
  }, []);

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

  /* ---- Slash menu ---------------------------------------------------- */

  const openMenu = useCallback((q: string) => {
    if (closeTimerRef.current) {
      clearTimeout(closeTimerRef.current);
      closeTimerRef.current = null;
    }
    setSlashClosing(false);
    setSlashQuery(q);
    document.body.classList.add("slash-menu-open");
  }, []);

  const closeMenu = useCallback(() => {
    setSlashClosing(true);
    document.body.classList.remove("slash-menu-open");
    if (closeTimerRef.current) clearTimeout(closeTimerRef.current);
    closeTimerRef.current = setTimeout(() => {
      setSlashQuery(null);
      setSlashClosing(false);
      closeTimerRef.current = null;
    }, ANIM_MS);
  }, []);

  // Drive slash-menu open/close off the controlled input value.
  useEffect(() => {
    const v = input.trim();
    if (v.startsWith("/") && !v.includes(" ")) {
      openMenu(v.toLowerCase());
    } else if (slashQuery !== null) {
      closeMenu();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [input]);

  const slashMatches = useMemo(() => {
    if (slashQuery === null) return [];
    return SLASH_COMMANDS.filter((c) =>
      c.name.toLowerCase().startsWith(slashQuery),
    );
  }, [slashQuery]);

  const slashContext = useMemo<SlashContext>(
    () => ({
      sessionId: currentSessionId,
      send,
      newConversation: () => {
        // Until the welcome-screen migration lands we just clear the
        // active conversation; subsequent navigation handles the rest.
        setCurrentConv(null);
        setInput("");
      },
      setInput: (value, focus) => {
        setInput(value);
        if (focus) {
          requestAnimationFrame(() => textareaRef.current?.focus());
        }
      },
    }),
    [currentSessionId, send, setCurrentConv],
  );

  const handleSlashCommand = useCallback(
    (text: string): boolean => {
      if (!text.startsWith("/")) return false;
      const space = text.indexOf(" ");
      const cmdName = space === -1 ? text : text.slice(0, space);
      const rest = space === -1 ? "" : text.slice(space + 1);
      const cmd = SLASH_COMMANDS.find((c) => c.name === cmdName);
      if (!cmd) return false;
      cmd.run(rest, slashContext);
      return true;
    },
    [slashContext],
  );

  /* ---- Plus + effort menus ------------------------------------------ */

  function toggleTools() {
    setToolsEnabled((v) => {
      const next = !v;
      persistBool("agentic_tools_enabled", next);
      return next;
    });
  }

  function toggleWebSearch() {
    setWebSearchEnabled((v) => {
      const next = !v;
      persistBool("agentic_web_search_enabled", next);
      return next;
    });
  }

  function pickThinking(level: ThinkingEffort) {
    setThinking(level);
    setThinkingMenuOpen(false);
  }

  /* ---- Submit -------------------------------------------------------- */

  const submit = useCallback(() => {
    if (isRunning) return;
    const trimmed = input.trim();
    if (!trimmed) return;
    if (slashQuery !== null && handleSlashCommand(trimmed)) {
      setInput("");
      closeMenu();
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
    closeMenu();
  }, [
    closeMenu,
    currentSessionId,
    handleSlashCommand,
    input,
    isRunning,
    send,
    slashQuery,
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

  // Two-phase close: fade .fn-form-header / .fn-form-body first (the
  // FunctionForm reads `closing` and applies the fade-out class), then
  // unmount via the store so the wrapper's height transition runs on
  // the now-empty content. 130ms = 120ms fade + 10ms slack.
  const handleFnFormClose = useCallback(() => {
    setFnFormClosing(true);
    setTimeout(() => {
      closeFnFormStore();
    }, 130);
  }, [closeFnFormStore]);

  const submitFnForm = useCallback(() => {
    if (!fnFormFunction || isRunning) return;
    const fn = fnFormFunction;
    const workdirMode = fn.workdir_mode ?? "optional";
    const wd = fnFormWorkdir.trim();
    if (workdirMode === "required" && !wd) {
      setFnFormError("__workdir");
      return;
    }

    const parts: string[] = ["run", fn.name];
    for (const p of visibleParams(fn)) {
      const isBool = p.type === "bool" || p.type === "boolean";
      let v = (fnFormValues[p.name] ?? "").trim();
      if (!v && isBool) v = "False";
      if (!v && !p.required) continue;
      if (!v && p.required) {
        setFnFormError(p.name);
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
    fnFormValues,
    fnFormWorkdir,
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
        if (workdirMode === "required" && !fnFormWorkdir.trim()) return true;
        for (const p of visibleParams(fn)) {
          if (!p.required) continue;
          const v = (fnFormValues[p.name] ?? "").trim();
          if (!v) return true;
        }
        return false;
      })()
    : !input.trim();
  const sendTitle = fnFormActive ? "Run" : "Send message";

  /* ---- Render -------------------------------------------------------- */

  const menuVisible = slashQuery !== null && slashMatches.length > 0;
  const anyToolActive = toolsEnabled || webSearchEnabled;

  return (
    <div className={styles.inputArea}>
      <div className={styles.slashClip}>
        {menuVisible && (
          <div
            className={`${styles.slashMenu} ${slashClosing ? styles.closing : styles.opening}`}
          >
            {slashMatches.map((c) => (
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
            key="top-half"
            fn={fnFormFunction}
            values={fnFormValues}
            setValue={(name, v) => {
              setFnFormValues((s) => ({ ...s, [name]: v }));
              if (fnFormError === name) setFnFormError(null);
            }}
            workdir={fnFormWorkdir}
            setWorkdir={(v) => {
              setFnFormWorkdir(v);
              if (fnFormError === "__workdir" && v.trim()) setFnFormError(null);
            }}
            errorParam={fnFormError}
            closing={fnFormClosing}
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
              placeholder="create / run / edit or ask anything... (type / for commands)"
              rows={1}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={onKeyDown}
            />
            {/* Chat-mode send/stop button: top-right next to textarea.
                Suppressed while the wrapper is mid-transition back from
                fn-form so the button doesn't visibly slide up here from
                the bottom — it stays anchored in the bottom row until
                the height animation completes, then teleports up. */}
            {!wrapperTransitioning &&
              (isRunning ? (
                <button
                  key="corner-btn-top"
                  className={styles.stopBtn}
                  onClick={stop}
                  title="Stop"
                  type="button"
                >
                  <StopIcon />
                </button>
              ) : (
                <button
                  key="corner-btn-top"
                  className={styles.sendBtn}
                  onClick={onSendButtonClick}
                  disabled={sendDisabled}
                  title={sendTitle}
                  type="button"
                >
                  <SendIcon />
                </button>
              ))}
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
            {/* Bottom-row send/stop button — shown whenever fn-form is
                active OR while the wrapper is mid-transition (so the
                button stays put down here during the close animation
                and only teleports up to the top row after the height
                animation fully finishes). */}
            {(fnFormFunction || wrapperTransitioning) &&
              (isRunning ? (
                <button
                  key="corner-btn-bottom"
                  className={styles.stopBtn}
                  onClick={stop}
                  title="Stop"
                  type="button"
                >
                  <StopIcon />
                </button>
              ) : (
                <button
                  key="corner-btn-bottom"
                  className={styles.sendBtn}
                  onClick={onSendButtonClick}
                  disabled={sendDisabled}
                  title={sendTitle}
                  type="button"
                >
                  <SendIcon />
                </button>
              ))}
          </div>
        </div>
      </div>
    </div>
  );
}

