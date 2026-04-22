import { create } from "zustand";

export type MessageStatus = "pending" | "streaming" | "done" | "error" | "cancelled";

export interface ChatMsg {
  id: string;                  // msg_id from server, or local generated for user msgs
  role: "user" | "assistant" | "system";
  content: string;
  status?: MessageStatus;
  function?: string;           // if this was /run
  display?: "runtime" | "normal";
  timestamp?: number;
  attempts?: { content: string; timestamp: number }[];
  current_attempt?: number;
}

export interface ConvSummary {
  id: string;
  title: string;
  created_at?: number;
}

interface RunningTask {
  conv_id: string;
  msg_id: string;
  func_name?: string;
  started_at?: number;
}

export interface TreeNode {
  id?: string;
  type?: string;
  name?: string;
  status?: string;
  inputs?: Record<string, unknown>;
  outputs?: unknown;
  elapsed_ms?: number;
  children?: TreeNode[];
  node_type?: string;
  _in_progress?: boolean;
  [k: string]: unknown;
}

interface ConvState {
  /** WS status for UI. */
  wsStatus: "connecting" | "open" | "closed";
  /** Summary for sidebar Recents list. */
  conversations: Record<string, ConvSummary>;
  /** Full messages per conversation, loaded on demand. */
  messages: Record<string, ChatMsg[]>;
  /** Currently active conversation id. */
  currentConvId: string | null;
  /** Currently running task (show Stop button). */
  runningTask: RunningTask | null;
  /** Paused flag. */
  paused: boolean;
  /** Provider info shown in header. */
  providerInfo: { provider?: string; model?: string; type?: string } | null;
  /** Latest live Context tree per conversation. */
  trees: Record<string, TreeNode>;
  setTree: (convId: string, tree: TreeNode) => void;

  setWsStatus: (s: ConvState["wsStatus"]) => void;
  setConversations: (list: ConvSummary[]) => void;
  upsertConversation: (c: ConvSummary) => void;
  removeConversation: (id: string) => void;
  clearConversations: () => void;
  setCurrentConv: (id: string | null) => void;
  setMessages: (convId: string, msgs: ChatMsg[]) => void;
  appendMessage: (convId: string, msg: ChatMsg) => void;
  updateMessage: (convId: string, msgId: string, patch: Partial<ChatMsg>) => void;
  /** Truncate messages at and after msgId. Used by retry to drop the
   *  stale reply before the new one streams in. */
  truncateFrom: (convId: string, msgId: string) => void;
  setRunningTask: (t: RunningTask | null) => void;
  setPaused: (p: boolean) => void;
  setProviderInfo: (p: ConvState["providerInfo"]) => void;
}

export const useConvStore = create<ConvState>((set) => ({
  wsStatus: "connecting",
  conversations: {},
  messages: {},
  currentConvId: null,
  runningTask: null,
  paused: false,
  providerInfo: null,
  trees: {},
  setTree: (convId, tree) =>
    set((s) => ({ trees: { ...s.trees, [convId]: tree } })),

  setWsStatus: (s) => set({ wsStatus: s }),

  setConversations: (list) =>
    set({
      conversations: Object.fromEntries(list.map((c) => [c.id, c])),
    }),

  upsertConversation: (c) =>
    set((s) => ({ conversations: { ...s.conversations, [c.id]: c } })),

  removeConversation: (id) =>
    set((s) => {
      const rest = { ...s.conversations };
      delete rest[id];
      const msgs = { ...s.messages };
      delete msgs[id];
      return {
        conversations: rest,
        messages: msgs,
        currentConvId: s.currentConvId === id ? null : s.currentConvId,
      };
    }),

  clearConversations: () => set({ conversations: {}, messages: {}, currentConvId: null }),

  setCurrentConv: (id) => set({ currentConvId: id }),

  setMessages: (convId, msgs) =>
    set((s) => ({ messages: { ...s.messages, [convId]: msgs } })),

  appendMessage: (convId, msg) =>
    set((s) => ({
      messages: { ...s.messages, [convId]: [...(s.messages[convId] ?? []), msg] },
    })),

  updateMessage: (convId, msgId, patch) =>
    set((s) => {
      const list = s.messages[convId];
      if (!list) return {};
      const next = list.map((m) => (m.id === msgId ? { ...m, ...patch } : m));
      return { messages: { ...s.messages, [convId]: next } };
    }),

  truncateFrom: (convId, msgId) =>
    set((s) => {
      const list = s.messages[convId];
      if (!list) return {};
      const idx = list.findIndex((m) => m.id === msgId);
      if (idx < 0) return {};
      return { messages: { ...s.messages, [convId]: list.slice(0, idx) } };
    }),

  setRunningTask: (t) => set({ runningTask: t }),
  setPaused: (p) => set({ paused: p }),
  setProviderInfo: (p) => set({ providerInfo: p }),
}));
