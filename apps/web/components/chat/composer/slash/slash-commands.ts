/**
 * Slash-command catalogue used by the Composer.
 *
 * Each command has a `run` that gets the rest of the input string plus
 * a context object the Composer wires up. Adding a new command means
 * adding an entry here — no Composer changes needed.
 */

import { translateText } from "@/lib/i18n";
import { showToast } from "@/lib/format-utils/toast";
import { openRewindPicker } from "@/lib/chat/rewind/rewind-actions";

export interface SlashContext {
  sessionId: string | null;
  /** Raw socket write; false when the socket did not take the frame. */
  send: (payload: unknown) => boolean;
  /** Send `text` as a chat turn through the composer's normal send path
   *  (optimistic bubble, the chat's permission mode and Agent). During a
   *  run, while a request waits or while disconnected it is queued for
   *  the next turn. */
  sendChat: (text: string) => boolean;
  /** Send `text` as a backend-run command (/goal, /rename …) of this
   *  composer's conversation. When idle it is that conversation's message:
   *  a draft's first one carries the draft's own id and settings, so the
   *  command runs in the draft and the tab becomes that conversation, and
   *  its bubble shows at once. During a run or a waiting request it goes
   *  at once beside the turn (the server runs it before reserving one). */
  sendCommand: (text: string) => boolean;
  /** This conversation has a turn running or a request waiting. */
  busy: boolean;
  newConversation: () => void;
  setInput: (value: string, focus?: boolean) => void;
  /** Show `text` as a transient system row in this conversation. */
  showLocalReply: (text: string) => void;
  /** 打开 /context 分类分解面板（token breakdown）。 */
  openContextPanel?: () => void;
}

export interface SlashCommand {
  name: string;
  args?: string;
  description: string;
  /** Where the command comes from: "builtin" (also when absent), "skill",
   *  "plugin", "user", "project" — picks the menu's default icon. */
  source?: string;
  /** Changes the conversation's history, so it waits for an idle chat. */
  needsIdle?: boolean;
  /** While a turn runs the command does nothing and says nothing. */
  ignoredWhileBusy?: boolean;
  /** False when the command could not be carried out (the socket refused
   *  its frame); the draft then stays for a retry. */
  run: (rest: string, ctx: SlashContext) => boolean;
}

/** What a submit did with a slash command: ran it, or recognised it but
 *  could not run it (the draft stays). `null`: the text names no command. */
export type SlashOutcome = "ran" | "failed" | null;

const startFirst = () =>
  showToast(translateText("Start a conversation first", "先开始一段对话再执行此命令"), { tone: "warn" });

export const SLASH_COMMANDS: SlashCommand[] = [
  {
    name: "/compact",
    args: "[keep_recent_tokens]",
    description:
      "Summarise older history; keep recent N tokens verbatim (default: window-adaptive)",
    needsIdle: true,
    run(rest, { sessionId, send }) {
      if (!sessionId) {
        startFirst();
        return true;
      }
      const n = parseInt(rest.trim(), 10);
      return send({
        action: "compact",
        session_id: sessionId,
        ...(Number.isFinite(n) && n > 0 ? { keep_recent_tokens: n } : {}),
      });
    },
  },
  {
    name: "/context",
    description:
      "Show this conversation's input-token breakdown (messages / system / tools / per-tool)",
    run(_rest, { openContextPanel }) {
      openContextPanel?.();
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
    description:
      "Name the current branch (this conversation's head). No name = "
      + "let the model summarise one",
    run(rest, { sessionId, send }) {
      if (!sessionId) {
        startFirst();
        return true;
      }
      // A branch in this DAG IS a named leaf — there is no separate
      // "create" step, a fork happens when a turn is written off a
      // non-tip node. So naming the active head is what "/branch" means;
      // both handlers resolve head_msg_id from the session's head.
      const name = rest.trim();
      return send(
        name
          ? { action: "rename_branch", session_id: sessionId, name }
          : { action: "auto_name_branch", session_id: sessionId },
      );
    },
  },
  {
    name: "/skill",
    args: "<name>",
    description: "Run a registered skill by name",
    run(rest, { sendChat }) {
      const name = rest.trim();
      if (!name) return true;
      return sendChat(`/skill ${name}`);
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
      + "job to the background runner — the Branches panel "
      + "animates the row while it runs and you can keep chatting. "
      + "Without --async this blocks until the spawned agent "
      + "finishes (legacy behaviour).",
    run(rest, { sendChat }) {
      return sendChat("/task " + rest);
    },
  },
  {
    name: "/merge",
    args: "<sid|sid:head> [...]: <message>",
    description:
      "Merge N peer branches into a reply on this session. "
      + "Same-session: 'sid:head_id'. Mark base with '*' prefix.",
    run(rest, { sendChat }) {
      return sendChat("/merge " + rest);
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
    name: "/sandbox",
    description: "Toggle system sandbox — restrict bash to cwd writes only",
    run(_rest, { send }) {
      return send({ action: "sandbox" });
    },
  },
  {
    name: "/rewind",
    description: "Go back to an earlier message: pick it from a list",
    ignoredWhileBusy: true,
    run(rest, { sessionId }) {
      if (!sessionId) {
        startFirst();
        return true;
      }
      if (rest.trim()) {
        showToast(translateText("/rewind takes no arguments. Send it alone and pick a message.", "/rewind 不带参数，直接发送后选择消息。"), { tone: "warn" });
        return true;
      }
      void openRewindPicker(sessionId);
      return true;
    },
  },
  {
    name: "/doctor",
    description:
      "Run health checks: python, node, skills, plugins, providers, mcp, cache",
    // A local check: the report is shown here, never sent to the model.
    run(_rest, { sessionId, showLocalReply }) {
      (async () => {
        try {
          const r = await fetch("/api/doctor");
          const data = await r.json() as {
            results: Array<{ ok: boolean; label: string; detail: string }>;
            all_ok: boolean;
          };
          const lines = data.results.map(
            (x) => `${x.ok ? "✓" : "✗"} ${x.label} - ${x.detail}`,
          );
          const text =
            translateText("Doctor report", "Doctor 检查报告") + "\n\n" + lines.join("\n") +
            (data.all_ok
              ? "\n\n" + translateText("All checks passed.", "所有检查通过。")
              : "\n\n" + translateText("Some checks failed.", "部分检查失败。"));
          if (sessionId) showLocalReply(text);
          else showToast(text, { tone: "info" });
        } catch (e) {
          showToast(translateText("Doctor check failed: ", "Doctor 检查失败：") + String(e), { tone: "error" });
        }
      })();
      return true;
    },
  },
];
