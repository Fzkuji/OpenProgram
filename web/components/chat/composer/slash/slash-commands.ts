/**
 * Slash-command catalogue used by the Composer.
 *
 * Each command has a `run` that gets the rest of the input string plus
 * a context object the Composer wires up. Adding a new command means
 * adding an entry here — no Composer changes needed.
 */

import { translateText } from "@/lib/i18n";

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
    name: "/task",
    args: "[--clean] [--async] [label]: <prompt>",
    description:
      "Run another agent in this session as a new branch. Default "
      + "inherits this conversation; --clean starts at a new root "
      + "(agent sees only the prompt). Add --async to spawn the "
      + "task to the background runner — the Branches panel "
      + "animates the row while it runs and you can keep chatting. "
      + "Without --async this blocks until the spawned agent "
      + "finishes (legacy behaviour).",
    run(rest, { sessionId, send }) {
      if (!sessionId) return true;
      send({
        action: "chat",
        session_id: sessionId,
        text: "/task " + rest,
      });
      return true;
    },
  },
  {
    name: "/merge",
    args: "<sid|sid:head> [...]: <message>",
    description:
      "Merge N peer branches into a reply on this session. "
      + "Same-session: 'sid:head_id'. Mark base with '*' prefix.",
    run(rest, { sessionId, send }) {
      if (!sessionId) return true;
      send({
        action: "chat",
        session_id: sessionId,
        text: "/merge " + rest,
      });
      return true;
    },
  },
  {
    name: "/help",
    description:
      "Open the command list (built-ins, plugin commands, skills)",
    run(_rest, { setInput }) {
      // Just opens the slash menu — the filtered list IS the help.
      setInput("/", true);
      return true;
    },
  },
  {
    name: "/doctor",
    description:
      "Run health checks: python, node, skills, plugins, providers, mcp, cache",
    run(_rest, { sessionId, send }) {
      (async () => {
        try {
          const r = await fetch("/api/doctor");
          const data: {
            results: Array<{ ok: boolean; label: string; detail: string }>;
            all_ok: boolean;
          } = await r.json();
          const lines = data.results.map(
            (x) => `${x.ok ? "✓" : "✗"} ${x.label} - ${x.detail}`,
          );
          const text =
            translateText("Doctor report", "Doctor 检查报告") + "\n\n" + lines.join("\n") +
            (data.all_ok
              ? "\n\n" + translateText("All checks passed.", "所有检查通过。")
              : "\n\n" + translateText("Some checks failed.", "部分检查失败。"));
          if (sessionId) {
            send({ action: "chat", session_id: sessionId, text });
          } else {
            alert(text);
          }
        } catch (e) {
          alert(translateText("Doctor check failed: ", "Doctor 检查失败：") + String(e));
        }
      })();
      return true;
    },
  },
];
