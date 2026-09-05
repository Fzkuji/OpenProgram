"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  buildExecutionCommand,
  newCommandId,
  type CommandResult,
  type EventCursor,
  type ExecutionCommand,
  type ExecutionSnapshot,
  type RevisionChange,
  type RevisionDraft,
  selectDebuggerInspection,
} from "@/lib/execution-debugger";
import {
  ExecutionApiError,
  type PersistedExecutionEvent,
  createRevisionDraft,
  getExecutionDebuggerState,
  getExecutionEvents,
  getSessionExecutions,
  parseRevisionState,
  postExecutionCommand,
  postExecutionWait,
  postRevisionDraftCommand,
} from "@/lib/net/execution-client";
import "@/lib/net/ws-events";

type ExecutionUpdateDetail = {
  execution?: ExecutionSnapshot;
  event_cursor?: EventCursor;
};

export type ExecutionDebuggerController = {
  executions: ExecutionSnapshot[];
  events: PersistedExecutionEvent[];
  fetchedAt: number | null;
  selectedExecutionId: string | null;
  checkpoints: import("@/components/right-sidebar/debugger-panel").CheckpointInspector[];
  waits: import("@/lib/execution-debugger").DurableWait[];
  drafts: RevisionDraft[];
  connection: DebuggerConnection;
  selectExecution: (executionId: string) => void;
  refresh: () => void;
  command: (command: ExecutionCommand) => Promise<CommandResult>;
  respondWait: (input: {
    wait_id: string;
    execution_id: string;
    claim_generation: number;
    outcome: "answer" | "decline";
    value?: unknown;
  }) => Promise<void>;
  createDraft: (input: {
    execution_id: string;
    source_checkpoint_id: string;
    changes?: RevisionChange[];
    frontier_mapping?: Array<Record<string, unknown>>;
  }) => Promise<RevisionDraft>;
  updateDraft: (draft: RevisionDraft, changes: RevisionChange[]) => Promise<void>;
  draftAction: (draft: RevisionDraft, action: "validate" | "approve" | "publish" | "fork") => Promise<void>;
};

type DebuggerConnection = {
  state: "connected" | "reconnecting" | "stale" | "gap" | "conflict";
  cursor?: EventCursor | null;
  expected_sequence?: number | null;
  received_sequence?: number | null;
  message?: string | null;
};

function errorMessage(error: unknown): string {
  return error instanceof ExecutionApiError || error instanceof Error
    ? error.message
    : "Execution request failed.";
}

export function useExecutionDebugger(active: boolean, sessionId: string | null, requestedExecutionId?: string | null): ExecutionDebuggerController {
  const [snapshots, setSnapshots] = useState<Record<string, ExecutionSnapshot>>({});
  const [cursors, setCursors] = useState<Record<string, EventCursor>>({});
  const [events, setEvents] = useState<PersistedExecutionEvent[]>([]);
  const [fetchedAt, setFetchedAt] = useState<number | null>(null);
  const [debuggerData, setDebuggerData] = useState<Record<string, {
    checkpoints: import("@/components/right-sidebar/debugger-panel").CheckpointInspector[];
    waits: import("@/lib/execution-debugger").DurableWait[];
    drafts: RevisionDraft[];
  }>>({});
  const [selectedExecutionId, setSelectedExecutionId] = useState<string | null>(null);
  const [connection, setConnection] = useState<DebuggerConnection>({ state: "reconnecting" });
  const refreshToken = useRef(0);
  const mounted = useRef(true);
  const refreshController = useRef<AbortController | null>(null);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; refreshToken.current++; refreshController.current?.abort(); };
  }, []);

  const loadDebuggerData = useCallback(async (executionId: string, signal?: AbortSignal) => {
    const state = await getExecutionDebuggerState(executionId, signal);
    if (!mounted.current || signal?.aborted) return state;
    setDebuggerData((current) => ({ ...current, [executionId]: {
      checkpoints: (state.checkpoints || []).map((checkpoint) => ({
        ...checkpoint,
        status_version: checkpoint.status_version ?? checkpoint.source_execution_version ?? 0,
        safe_point: checkpoint.safe_point || null,
        frontier: checkpoint.frontier || [],
        effect_receipts: checkpoint.effect_receipts || [],
      })),
      waits: state.waits || [],
      drafts: (state.drafts || []).map(parseRevisionState),
    } }));
    return state;
  }, []);

  const refresh = useCallback(async () => {
    if (!sessionId || !active || refreshController.current) return;
    const token = ++refreshToken.current;
    const controller = new AbortController();
    refreshController.current = controller;
    const signal = AbortSignal.any([controller.signal, AbortSignal.timeout(15000)]);
    try {
      const list = await getSessionExecutions(sessionId, signal);
      const next: Record<string, ExecutionSnapshot> = {};
      const nextCursors: Record<string, EventCursor> = {};
      for (const item of list.items || []) {
        if (item.snapshot?.session_id !== sessionId) continue;
        next[item.snapshot.execution_id] = item.snapshot;
        if (item.event_cursor) nextCursors[item.snapshot.execution_id] = item.event_cursor;
      }
      const inspectionId = [selectedExecutionId, requestedExecutionId].find((id) => id && next[id])
        || Object.values(next).sort((a, b) => b.updated_at - a.updated_at)[0]?.execution_id;
      let history: PersistedExecutionEvent[] = [];
      if (inspectionId) {
        const [replay] = await Promise.all([
          getExecutionEvents(inspectionId, Math.max(0, next[inspectionId].event_sequence - 50), signal),
          loadDebuggerData(inspectionId, signal),
        ]);
        if (replay.snapshot?.session_id !== sessionId) throw new Error("Execution does not belong to this conversation.");
        next[inspectionId] = replay.snapshot;
        if (replay.event_cursor) nextCursors[inspectionId] = replay.event_cursor;
        history = replay.events || [];
      }
      if (!mounted.current || token !== refreshToken.current || signal.aborted) return;
      setSnapshots(next);
      setCursors(nextCursors);
      setEvents(history);
      setFetchedAt(Date.now());
      setConnection({ state: "connected", cursor: inspectionId ? nextCursors[inspectionId] || null : null });
    } catch (error) {
      if (!mounted.current || token !== refreshToken.current || controller.signal.aborted) return;
      setConnection({ state: "stale", message: errorMessage(error) });
    } finally {
      if (refreshController.current === controller) refreshController.current = null;
    }
  }, [active, sessionId, selectedExecutionId, requestedExecutionId, loadDebuggerData]);

  useEffect(() => {
    if (!active || !sessionId) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      await refresh();
      if (!cancelled) timer = setTimeout(poll, 5000);
    };
    void poll();
    const onUpdate = (event: WindowEventMap["op:execution-update"]) => {
      if (event.detail?.execution?.session_id !== sessionId) return;
      // Fetch one canonical snapshot/history pair instead of mixing event and poll versions.
      void refresh();
    };
    window.addEventListener("op:execution-update", onUpdate);
    return () => {
      cancelled = true;
      clearTimeout(timer);
      refreshToken.current++;
      refreshController.current?.abort();
      refreshController.current = null;
      window.removeEventListener("op:execution-update", onUpdate);
    };
  }, [active, sessionId, refresh]);

  const executions = useMemo(() => Object.values(snapshots).sort((a, b) => b.updated_at - a.updated_at), [snapshots]);
  const selectedKey = [selectedExecutionId, requestedExecutionId].find((id) => id && snapshots[id]) || executions[0]?.execution_id || null;
  const selectExecution = useCallback((executionId: string) => {
    if (!snapshots[executionId]) return;
    setSelectedExecutionId(executionId);
    setEvents([]);
    setConnection({ state: "reconnecting", cursor: cursors[executionId] || null });
  }, [snapshots, cursors]);

  const command = useCallback(async (commandValue: ExecutionCommand): Promise<CommandResult> => {
    try {
      const result = await postExecutionCommand(commandValue);
      const resultExecution = result.execution;
      if (resultExecution?.execution_id) {
        setSnapshots((current) => ({ ...current, [resultExecution.execution_id]: resultExecution }));
        void loadDebuggerData(resultExecution.execution_id).catch((error) => {
          setConnection({ state: "stale", message: errorMessage(error) });
        });
      }
      return result;
    } catch (error) {
      setConnection({ state: "conflict", message: errorMessage(error) });
      throw error;
    }
  }, [loadDebuggerData]);

  const respondWait = useCallback(async (input: Parameters<ExecutionDebuggerController["respondWait"]>[0]) => {
    await postExecutionWait({ ...input, generation: input.claim_generation, expected_version: snapshots[input.execution_id]?.status_version ?? 0 });
    await refresh();
  }, [refresh, snapshots]);

  const createDraft = useCallback(async (input: Parameters<ExecutionDebuggerController["createDraft"]>[0]) => {
    const draft = await createRevisionDraft({
      execution_id: input.execution_id,
      source_checkpoint_id: input.source_checkpoint_id,
      changes: input.changes || [],
      frontier_mapping: input.frontier_mapping || [],
    });
    setDebuggerData((current) => ({
      ...current,
      [input.execution_id]: {
        ...(current[input.execution_id] || { checkpoints: [], waits: [], drafts: [] }),
        drafts: [...(current[input.execution_id]?.drafts || []).filter((item) => item.draft_id !== draft.draft_id), draft],
      },
    }));
    return draft;
  }, []);

  const updateDraft = useCallback(async (draft: RevisionDraft, changes: RevisionChange[]) => {
    const updated = await postRevisionDraftCommand({
      execution_id: draft.source_execution_id,
      draft_id: draft.draft_id,
      action: "revision.draft.replace",
      expected_draft_version: draft.draft_version,
      payload: {
        changes,
        frontier_mapping: draft.frontier_mapping || [],
      },
    });
    setDebuggerData((current) => ({
      ...current,
      [draft.source_execution_id]: {
        ...(current[draft.source_execution_id] || { checkpoints: [], waits: [], drafts: [] }),
        drafts: (current[draft.source_execution_id]?.drafts || []).map((item) => item.draft_id === updated.draft_id ? updated : item),
      },
    }));
  }, []);

  const draftAction = useCallback(async (draft: RevisionDraft, action: "validate" | "approve" | "publish" | "fork") => {
    if (action === "fork") {
      if (!draft.manifest?.manifest_id || !draft.manifest.proof_hash) throw new ExecutionApiError(409, "manifest_required", "A published revision manifest is required before fork.");
      const snapshot = snapshots[draft.source_execution_id];
      if (!snapshot) throw new ExecutionApiError(404, "execution_not_loaded", "The source execution snapshot is not loaded.");
      const forkCommand = buildExecutionCommand(snapshot, "fork", newCommandId(), {
        manifest_id: draft.manifest.manifest_id,
        checkpoint_id: snapshot.checkpoint_head_id,
        proof_hash: draft.manifest.proof_hash,
      });
      await command(forkCommand);
      return;
    }
    const validationId = draft.validation?.validation_id;
    const approvalId = draft.approval?.approval_id;
    if (action === "approve" && !validationId) {
      throw new ExecutionApiError(409, "validation_required", "A validation report is required before approval.");
    }
    if (action === "publish" && (!validationId || !approvalId)) {
      throw new ExecutionApiError(409, "approval_required", "A validation report and approval are required before publication.");
    }
    const updated = await postRevisionDraftCommand({
      execution_id: draft.source_execution_id,
      draft_id: draft.draft_id,
      action: `revision.${action}` as "revision.validate" | "revision.approve" | "revision.publish",
      expected_draft_version: draft.draft_version,
      payload: action === "validate"
        ? {}
        : action === "approve"
          ? { validation_id: validationId }
          : { validation_id: validationId, approval_id: approvalId },
    });
    setDebuggerData((current) => ({
      ...current,
      [draft.source_execution_id]: {
        ...(current[draft.source_execution_id] || { checkpoints: [], waits: [], drafts: [] }),
        drafts: (current[draft.source_execution_id]?.drafts || []).map((item) => item.draft_id === updated.draft_id ? updated : item),
      },
    }));
  }, [command, snapshots]);
  const selectedData = selectedKey && debuggerData[selectedKey]
    ? selectDebuggerInspection(debuggerData[selectedKey], selectedKey)
    : undefined;
  return {
    executions,
    events,
    fetchedAt,
    selectedExecutionId: selectedKey,
    checkpoints: (selectedData?.checkpoints || []) as import("@/components/right-sidebar/debugger-panel").CheckpointInspector[],
    waits: selectedData?.waits || [],
    drafts: selectedData?.drafts || [],
    connection,
    selectExecution,
    refresh: () => { refresh(); },
    command,
    respondWait,
    createDraft,
    updateDraft,
    draftAction,
  };
}
