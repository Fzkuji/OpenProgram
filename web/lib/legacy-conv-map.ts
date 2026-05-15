/**
 * Map a legacy conversation payload (`conversations[id].messages`, the
 * shape `conversations.js` / `chat-ws.js` build) into the normalized
 * `ChatMsg[]` the React message store consumes.
 *
 * Phase 3 plumbing: `renderSessionMessages` calls this through the
 * `window.__feedStoreFromConv` bridge so the React store mirrors the
 * loaded conversation. The legacy DOM renderer still runs in parallel
 * until the cutover flip — feeding the store is additive.
 */
import type { ChatMsg, ChatToolCall } from "./session-store";

interface LegacyBlock {
  type?: string;
  text?: string;
  tool?: string;
  input?: string;
  result?: unknown;
  is_error?: boolean;
  tool_call_id?: string;
}

interface LegacyMsg {
  role?: string;
  content?: string;
  type?: string;
  function?: string | null;
  display?: string;
  blocks?: LegacyBlock[];
  id?: string;
  timestamp?: number;
  created_at?: number;
}

export function legacyConvToChatMsgs(messages: LegacyMsg[]): ChatMsg[] {
  const out: ChatMsg[] = [];
  messages.forEach((m, i) => {
    if (m.type === "status") return;
    const id = m.id || `hist_${i}`;
    const ts = m.timestamp || m.created_at;

    if (m.role === "user") {
      out.push({
        id,
        role: "user",
        content: m.content || "",
        display: m.display === "runtime" ? "runtime" : undefined,
        status: "done",
        timestamp: ts,
      });
      return;
    }

    if (m.role === "assistant") {
      let thinking: string | undefined;
      const tools: ChatToolCall[] = [];
      (m.blocks || []).forEach((b, bi) => {
        if (b.type === "thinking" && b.text) {
          thinking = (thinking ?? "") + b.text;
        } else if (b.type === "tool") {
          tools.push({
            id: b.tool_call_id || `${id}_t${bi}`,
            tool: b.tool || "?",
            input: b.input || "",
            result:
              b.result === undefined || b.result === null
                ? undefined
                : String(b.result),
            isError: !!b.is_error,
            status: b.is_error ? "error" : "done",
          });
        }
      });
      out.push({
        id,
        role: "assistant",
        content: m.content || "",
        thinking,
        tools: tools.length ? tools : undefined,
        function: m.function || undefined,
        display: m.display === "runtime" ? "runtime" : undefined,
        status: "done",
        timestamp: ts,
      });
      return;
    }

    out.push({ id, role: "system", content: m.content || "", status: "done" });
  });
  return out;
}
