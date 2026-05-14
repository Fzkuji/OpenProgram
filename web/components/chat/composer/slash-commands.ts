/**
 * Slash-command catalogue used by the Composer.
 *
 * Each command has a `run` that gets the rest of the input string plus
 * a context object the Composer wires up. Adding a new command means
 * adding an entry here — no Composer changes needed.
 */

export interface SlashContext {
  sessionId: string | null;
  send: (payload: unknown) => boolean;
  newConversation: () => void;
  setInput: (value: string, focus?: boolean) => void;
}

export interface SlashCommand {
  name: string;
  args?: string;
  description: string;
  run: (rest: string, ctx: SlashContext) => boolean;
}

export const SLASH_COMMANDS: SlashCommand[] = [
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
