/**
 * Client cache of per-node execution_stream state.
 * Frontend cache is a display replica — never authoritative.
 */
import { create } from "zustand";
import {
  applyExecutionStreamEvent,
  emptyNodeState,
  selectReasoningPreview,
  selectReplyPreview,
} from "./reducer";
import type { ExecutionStreamEvent, NodeStreamState } from "./types";

function nodeKey(sessionId: string, executionId: string, nodeId: string): string {
  return `${sessionId}\0${executionId}\0${nodeId}`;
}

type SnapshotRequester = (event: ExecutionStreamEvent) => void;

interface ExecutionStreamStore {
  byNode: Record<string, NodeStreamState>;
  /** node_id → latest key (for TreeStep lookups when execution_id known loosely). */
  byNodeId: Record<string, string>;
  applyEvent: (event: ExecutionStreamEvent) => NodeStreamState | null;
  getNode: (
    sessionId: string,
    executionId: string,
    nodeId: string,
  ) => NodeStreamState | undefined;
  getByNodeId: (nodeId: string) => NodeStreamState | undefined;
  clearSession: (sessionId: string) => void;
  setSnapshotRequester: (fn: SnapshotRequester | null) => void;
}

let snapshotRequester: SnapshotRequester | null = null;

export const useExecutionStreamStore = create<ExecutionStreamStore>((set, get) => ({
  byNode: {},
  byNodeId: {},

  setSnapshotRequester(fn) {
    snapshotRequester = fn;
  },

  applyEvent(event) {
    if (!event?.node_id || !event.session_id || !event.execution_id) return null;
    const key = nodeKey(event.session_id, event.execution_id, event.node_id);
    const prev = get().byNode[key] || null;
    const result = applyExecutionStreamEvent(prev, event);
    if (result.requestSnapshot && snapshotRequester) {
      try {
        snapshotRequester(event);
      } catch {
        /* ignore */
      }
    }
    if (result.state === prev) return prev;
    set((s) => ({
      byNode: { ...s.byNode, [key]: result.state },
      byNodeId: { ...s.byNodeId, [event.node_id]: key },
    }));
    return result.state;
  },

  getNode(sessionId, executionId, nodeId) {
    return get().byNode[nodeKey(sessionId, executionId, nodeId)];
  },

  getByNodeId(nodeId) {
    const key = get().byNodeId[nodeId];
    return key ? get().byNode[key] : undefined;
  },

  clearSession(sessionId) {
    set((s) => {
      const byNode: Record<string, NodeStreamState> = {};
      const byNodeId: Record<string, string> = {};
      for (const [k, v] of Object.entries(s.byNode)) {
        if (v.session_id === sessionId) continue;
        byNode[k] = v;
        byNodeId[v.node_id] = k;
      }
      return { byNode, byNodeId };
    });
  },
}));

export function applyExecutionStreamFromChat(
  data: Record<string, unknown>,
): NodeStreamState | null {
  if (data?.type !== "execution_stream") return null;
  return useExecutionStreamStore.getState().applyEvent(data as unknown as ExecutionStreamEvent);
}

export function streamPreviewForNode(nodeId: string | undefined): {
  text: string;
  reasoning: string;
  phase?: string;
  syncing?: boolean;
  attemptLabel?: string;
} {
  if (!nodeId) return { text: "", reasoning: "" };
  const st = useExecutionStreamStore.getState().getByNodeId(nodeId);
  if (!st) return { text: "", reasoning: "" };
  const cur = st.attempts.find((a) => a.attempt_id === st.current_attempt_id);
  const attemptLabel =
    st.syncing
      ? "resync"
      : cur && cur.attempt_index > 0
        ? `retry ${cur.attempt_index + 1}`
        : undefined;
  return {
    text: selectReplyPreview(st),
    reasoning: selectReasoningPreview(st),
    phase: st.phase,
    syncing: st.syncing,
    attemptLabel,
  };
}

export { emptyNodeState, nodeKey };
