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
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { useSessionStore } from "@/lib/session-store";

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

const THINKING_LEVELS = ["low", "medium", "high", "xhigh"] as const;
type ThinkingEffort = (typeof THINKING_LEVELS)[number];

const DEFAULT_THINKING: ThinkingEffort = "medium";
const ANIM_MS = 380;

interface SlashCommand {
  name: string;
  args?: string;
  description: string;
  run: (rest: string, ctx: SlashContext) => boolean;
}

interface SlashContext {
  sessionId: string | null;
  send: (payload: unknown) => boolean;
  newConversation: () => void;
  setInput: (value: string, focus?: boolean) => void;
}

const SLASH_COMMANDS: SlashCommand[] = [
  {
    name: "/compact",
    args: "[keep_recent_tokens]",
    description:
      "Summarise older history; keep recent N tokens verbatim (default: window-adaptive)",
    run(rest, { sessionId, send }) {
      if (!sessionId) return true;
      const n = parseInt(rest.trim(), 10);
      send({
        action: "compact",
        session_id: sessionId,
        ...(Number.isFinite(n) && n > 0 ? { keep_recent_tokens: n } : {}),
      });
      return true;
    },
  },
  {
    name: "/clear",
    description: 'Start a fresh conversation (equivalent to "New chat")',
    run(_rest, { newConversation }) {
      newConversation();
      return true;
    },
  },
  {
    name: "/new",
    description: "Alias of /clear — open a brand-new conversation",
    run(_rest, { newConversation }) {
      newConversation();
      return true;
    },
  },
  {
    name: "/branch",
    args: "[name]",
    description: "Branch the current conversation from this point",
    run(rest, { sessionId, send }) {
      if (!sessionId) return true;
      const name = rest.trim() || undefined;
      send({ action: "create_branch", session_id: sessionId, name });
      return true;
    },
  },
  {
    name: "/skill",
    args: "<name>",
    description: "Run a registered skill by name",
    run(rest, { sessionId, send }) {
      const name = rest.trim();
      if (!name || !sessionId) return true;
      send({ action: "chat", session_id: sessionId, text: `/skill ${name}` });
      return true;
    },
  },
  {
    name: "/memory",
    description: "Open the memory page in a new tab",
    run() {
      window.open("/memory", "_blank");
      return true;
    },
  },
  {
    name: "/help",
    description:
      "Show this command list — type / to browse all available commands",
    run(_rest, { setInput }) {
      setInput("/", true);
      return true;
    },
  },
];

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
  const send = wsSend;

  const isRunning = runningTask !== null;

  const [thinking, setThinking] = useState<ThinkingEffort>(DEFAULT_THINKING);
  const [thinkingMenuOpen, setThinkingMenuOpen] = useState(false);
  const [plusMenuOpen, setPlusMenuOpen] = useState(false);
  const [toolsEnabled, setToolsEnabled] = useState(false);
  const [webSearchEnabled, setWebSearchEnabled] = useState(false);
  const [slashQuery, setSlashQuery] = useState<string | null>(null);
  const [slashClosing, setSlashClosing] = useState(false);

  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const closeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Hydrate tools / web-search toggles from localStorage.
  useEffect(() => {
    setToolsEnabled(readPersistedBool("agentic_tools_enabled"));
    setWebSearchEnabled(readPersistedBool("agentic_web_search_enabled"));
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

      <div className={styles.inputWrapper}>
        {isRunning ? (
          <button
            className={styles.stopBtn}
            onClick={stop}
            title="Stop"
            type="button"
          >
            <svg viewBox="0 0 24 24">
              <rect x="6" y="6" width="12" height="12" rx="2" />
            </svg>
          </button>
        ) : (
          <button
            className={styles.sendBtn}
            onClick={submit}
            disabled={!input.trim()}
            title="Send message"
            type="button"
          >
            <svg viewBox="0 0 24 24">
              <path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z" />
            </svg>
          </button>
        )}

        <div className={styles.inputTopRow}>
          <textarea
            ref={textareaRef}
            className={styles.chatInput}
            placeholder="create / run / edit or ask anything... (type / for commands)"
            rows={1}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKeyDown}
          />
        </div>

        <div className={styles.inputBottomRow}>
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
              <svg
                className={styles.thinkingArrow}
                width="10"
                height="10"
                viewBox="0 0 10 10"
              >
                <path
                  d="M2 3.5L5 6.5L8 3.5"
                  stroke="currentColor"
                  strokeWidth="1.5"
                  fill="none"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </div>
            {thinkingMenuOpen && (
              <div className={styles.thinkingMenu}>
                {THINKING_LEVELS.map((lvl) => (
                  <button
                    key={lvl}
                    className={`${styles.thinkingMenuItem} ${lvl === thinking ? styles.active : ""}`}
                    onClick={() => pickThinking(lvl)}
                    type="button"
                  >
                    {lvl}
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

/* ---- Pieces -------------------------------------------------------- */

function ToolChip({
  icon,
  label,
  onRemove,
}: {
  icon: ReactNode;
  label: string;
  onRemove: () => void;
}) {
  return (
    <div
      className={styles.toolChip}
      onClick={onRemove}
      data-tooltip={label}
      title=""
    >
      <span className={styles.toolChipIcon}>{icon}</span>
      <span className={styles.toolChipClose} aria-label="Remove">
        <svg
          width="12"
          height="12"
          viewBox="0 0 12 12"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.75"
          strokeLinecap="round"
        >
          <line x1="3" y1="3" x2="9" y2="9" />
          <line x1="9" y1="3" x2="3" y2="9" />
        </svg>
      </span>
    </div>
  );
}

function PlusMenuItem({
  active,
  onClick,
  icon,
  label,
  title,
}: {
  active: boolean;
  onClick: () => void;
  icon: ReactNode;
  label: string;
  title?: string;
}) {
  return (
    <div
      className={`${styles.plusMenuItem} ${active ? styles.active : ""}`}
      onClick={onClick}
      title={title}
    >
      <div className={styles.plusMenuLeft}>
        <span className={styles.plusMenuIcon}>{icon}</span>
        <span className={styles.plusMenuLabel}>{label}</span>
      </div>
      <div className={styles.plusMenuRight}>
        {active && (
          <svg width="20" height="20" viewBox="0 0 20 20" fill="currentColor">
            <path d="M15.188 5.11a.5.5 0 0 1 .752.626l-.056.084-7.5 9a.5.5 0 0 1-.738.033l-3.5-3.5-.064-.078a.501.501 0 0 1 .693-.693l.078.064 3.113 3.113 7.15-8.58z" />
          </svg>
        )}
      </div>
    </div>
  );
}

function PlusIcon() {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 20 20"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
      strokeLinecap="round"
    >
      <line x1="10" y1="4" x2="10" y2="16" />
      <line x1="4" y1="10" x2="16" y2="10" />
    </svg>
  );
}

function ToolsIcon({ size = 20 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z" />
    </svg>
  );
}

function WebSearchIcon({ size = 20 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <circle cx="12" cy="12" r="10" />
      <line x1="2" y1="12" x2="22" y2="12" />
      <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" />
    </svg>
  );
}
