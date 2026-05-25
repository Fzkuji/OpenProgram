"use client";

/**
 * Slash-menu state machine for the composer.
 *
 * Triggered when the textarea value starts with `/` and contains no
 * space (e.g. user is typing `/he` to filter for `/help`). Filters
 * `SLASH_COMMANDS` by prefix. `runCommand` dispatches the matching
 * command's `run(rest, ctx)` if the input is a full slash command.
 *
 * The matching close animation is debounced by `ANIM_MS` so the
 * filter list can fade out before unmount; `openMenu` cancels any
 * pending close timer so re-typing while closing doesn't drop the
 * incoming filter.
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type RefObject,
} from "react";

import { useSessionStore } from "@/lib/session-store";
import { useSkills } from "@/lib/skills-store";

import { SLASH_COMMANDS, type SlashCommand, type SlashContext } from "./slash-commands";

interface PluginCommand {
  plugin: string;
  name: string;
  description: string;
  prompt: string;
}

// Unified-registry view (see openprogram/commands). Phase-1 wires the
// file-backed user/project layers + plugin adapter through this list;
// skills and MCP still come in through their own slash injection
// paths until Phase 3-4 lands.
interface UnifiedCommand {
  name: string;
  source: string;          // "builtin" | "plugin" | "user" | "project" | ...
  source_label: string;
  description: string;
  argument_hint: string;
  user_invocable: boolean;
  hidden: boolean;
}

const ANIM_MS = 380;

interface UseSlashMenuArgs {
  input: string;
  textareaRef: RefObject<HTMLTextAreaElement>;
  send: (payload: unknown) => boolean;
}

export interface SlashMenuHook {
  query: string | null;
  closing: boolean;
  matches: SlashCommand[];
  visible: boolean;
  /** Index of the keyboard-highlighted command in `matches`. */
  activeIndex: number;
  /** Move the highlight by `delta`, wrapping around the list. */
  move: (delta: number) => void;
  close: () => void;
  /** Tell the menu whether the composer textarea is focused — the menu
   *  is only shown while focused, and re-appears on re-focus if the
   *  input still holds a `/…` query. */
  setFocused: (focused: boolean) => void;
  runCommand: (text: string) => boolean;
}

export function useSlashMenu({ input, textareaRef, send }: UseSlashMenuArgs): SlashMenuHook {
  const currentSessionId = useSessionStore((s) => s.currentSessionId);
  const setCurrentConv = useSessionStore((s) => s.setCurrentConv);
  const setComposerInput = useSessionStore((s) => s.setComposerInput);
  const skills = useSkills((s) => s.skills);
  const fetchSkills = useSkills((s) => s.fetchSkills);

  // Unified slash-command list — plugin + user + project layers
  // arrive through ``/api/commands``. The legacy
  // ``/api/plugins/commands`` is also fetched as a fallback for any
  // host that hasn't restarted onto the new registry yet.
  const [unifiedCommands, setUnifiedCommands] = useState<UnifiedCommand[]>([]);
  const [pluginCommands, setPluginCommands] = useState<PluginCommand[]>([]);

  useEffect(() => {
    if (skills.length === 0) fetchSkills();
    fetch("/api/commands")
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (d && Array.isArray(d.commands)) {
          setUnifiedCommands(d.commands);
          return true;
        }
        return false;
      })
      .then((hit) => {
        if (hit) return;
        // Fallback: pre-Phase-1 backend.
        fetch("/api/plugins/commands")
          .then((r) => (r.ok ? r.json() : null))
          .then((d) => {
            if (d && Array.isArray(d.commands)) setPluginCommands(d.commands);
          })
          .catch(() => { /* offline / not wired — ignore */ });
      })
      .catch(() => { /* offline / not wired — ignore */ });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const [query, setQuery] = useState<string | null>(null);
  const [closing, setClosing] = useState(false);
  const [focused, setFocused] = useState(false);
  const closeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const open = useCallback((q: string) => {
    if (closeTimerRef.current) {
      clearTimeout(closeTimerRef.current);
      closeTimerRef.current = null;
    }
    setClosing(false);
    setQuery(q);
    document.body.classList.add("slash-menu-open");
  }, []);

  const close = useCallback(() => {
    setClosing(true);
    document.body.classList.remove("slash-menu-open");
    if (closeTimerRef.current) clearTimeout(closeTimerRef.current);
    closeTimerRef.current = setTimeout(() => {
      setQuery(null);
      setClosing(false);
      closeTimerRef.current = null;
    }, ANIM_MS);
  }, []);

  // The menu is shown only while the textarea is focused AND the input
  // holds a bare `/…` query. Re-runs on focus changes too, so clicking
  // back into a textarea that still contains `/foo` re-opens it, and
  // clicking away closes it.
  useEffect(() => {
    const v = input.trim();
    if (focused && v.startsWith("/") && !v.includes(" ")) {
      open(v.toLowerCase());
    } else if (query !== null) {
      close();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [input, focused]);

  // Legacy skill→slash synthesis. Once the unified ``/api/commands``
  // registry is online its ``source: "skill"`` entries supersede
  // these — without the gate the menu would show every skill twice.
  // We keep the legacy path as a fallback for hosts that haven't
  // restarted onto the Phase-3 backend yet.
  const skillCommands = useMemo<SlashCommand[]>(() => {
    if (unifiedCommands.length > 0) return [];
    return skills
      .filter((s) => s.enabled)
      .map<SlashCommand>((s) => ({
        name: "/" + s.name,
        description: s.description || `Activate the ${s.name} skill`,
        run(rest, { setInput }) {
          const trail = rest ? " " + rest : " ";
          setInput("/skill " + s.name + trail, true);
          return true;
        },
      }));
  }, [skills, unifiedCommands]);

  // Unified registry — every entry is invoked by POSTing to
  // ``/api/commands/invoke`` which resolves + renders + returns the
  // rendered prompt. The composer drops the rendered text into the
  // textarea so the user can review before sending (consistent with
  // how the legacy plugin slash commands behaved).
  const unifiedSlashCommands = useMemo<SlashCommand[]>(() => {
    return unifiedCommands
      .filter((c) => c.user_invocable !== false && !c.hidden)
      .map<SlashCommand>((c) => ({
        name: c.name.startsWith("/") ? c.name : "/" + c.name,
        description:
          c.description || `${c.source_label || c.source} command`,
        args: c.argument_hint || undefined,
        run(rest, { setInput, sessionId }) {
          const text = "/" + c.name + (rest ? " " + rest : "");
          void fetch("/api/commands/invoke", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ text, session_id: sessionId }),
          })
            .then((r) => (r.ok ? r.json() : null))
            .then((res) => {
              if (res && res.ok && res.kind === "prompt") {
                setInput(res.rendered || "", true);
              } else {
                // Render-time failure — drop the raw text so the user
                // can see what they typed and edit instead of losing it.
                setInput(text + " ", true);
              }
            })
            .catch(() => setInput(text + " ", true));
          return true;
        },
      }));
  }, [unifiedCommands]);

  // Legacy fallback (only populated if /api/commands didn't respond).
  const pluginSlashCommands = useMemo<SlashCommand[]>(() => {
    return pluginCommands.map<SlashCommand>((c) => ({
      name: c.name.startsWith("/") ? c.name : "/" + c.name,
      description: c.description || `Plugin command from ${c.plugin}`,
      run(rest, { setInput }) {
        const trail = rest ? " " + rest : " ";
        setInput((c.prompt || c.name) + trail, true);
        return true;
      },
    }));
  }, [pluginCommands]);

  const allCommands = useMemo<SlashCommand[]>(
    () => [
      ...SLASH_COMMANDS,
      ...unifiedSlashCommands,
      ...pluginSlashCommands,
      ...skillCommands,
    ],
    [unifiedSlashCommands, pluginSlashCommands, skillCommands],
  );

  const matches = useMemo<SlashCommand[]>(() => {
    if (query === null) return [];
    // Substring match on either name or description so that a query
    // like "/pdf" finds anthropic-skills/pdf even when the user can't
    // remember the full namespace.
    return allCommands.filter((c) => {
      const n = c.name.toLowerCase();
      if (n.startsWith(query)) return true;
      // Skip pure "/" → return everything (no extra filtering needed).
      if (query === "/") return true;
      const term = query.slice(1); // drop leading "/"
      if (!term) return true;
      return n.includes(term) || (c.description || "").toLowerCase().includes(term);
    });
  }, [query, allCommands]);

  // Keyboard highlight — starts at -1 ("no highlight yet"). The first
  // ArrowDown / ArrowUp lights up an item; before that, nothing in
  // the menu is highlighted, so the user's eye isn't drawn to a
  // command they didn't pick. Filter changes also reset to -1 — the
  // previously-highlighted index would point at a different command
  // after the filter, which is confusing.
  const [activeIndex, setActiveIndex] = useState<number>(-1);
  useEffect(() => {
    setActiveIndex(-1);
  }, [query]);

  const move = useCallback(
    (delta: number) => {
      setActiveIndex((i) => {
        const n = matches.length;
        if (n === 0) return -1;
        // First move from "no highlight" lands on either the top or
        // bottom item depending on direction, instead of wrapping
        // from -1 (which would be confusing).
        if (i < 0) return delta > 0 ? 0 : n - 1;
        return (i + delta + n) % n;
      });
    },
    [matches.length],
  );

  const slashContext = useMemo<SlashContext>(
    () => ({
      sessionId: currentSessionId,
      send,
      newConversation: () => {
        setCurrentConv(null);
        setComposerInput("");
      },
      setInput: (value, focus) => {
        setComposerInput(value);
        if (focus) {
          requestAnimationFrame(() => textareaRef.current?.focus());
        }
      },
    }),
    [currentSessionId, send, setCurrentConv, setComposerInput, textareaRef],
  );

  const runCommand = useCallback(
    (text: string): boolean => {
      if (!text.startsWith("/")) return false;
      const space = text.indexOf(" ");
      const cmdName = space === -1 ? text : text.slice(0, space);
      const rest = space === -1 ? "" : text.slice(space + 1);
      const cmd = allCommands.find((c) => c.name === cmdName);
      if (!cmd) return false;
      cmd.run(rest, slashContext);
      return true;
    },
    [slashContext, allCommands],
  );

  return {
    query,
    closing,
    matches,
    visible: query !== null && matches.length > 0,
    activeIndex: Math.min(activeIndex, Math.max(0, matches.length - 1)),
    move,
    close,
    setFocused,
    runCommand,
  };
}
