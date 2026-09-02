"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  buildExecutionCommand,
  newCommandId,
  reduceExecutionEvent,
  type CommandResult,
  type EventCursor,
  type ExecutionCommand,
  type ExecutionEvent,
  type ExecutionSnapshot,
  type RevisionChange,
  type RevisionDraft,
  selectDebuggerInspection,
} from "@/lib/execution-debugger";
import {
  ExecutionApiError,
  createRevisionDraft,
  getExecutionDebuggerState,
  getExecutionEvents,
  getExecutionSnapshot,
  getRunningExecutions,
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

export function useExecutionDebugger(active: boolean, requestedExecutionId?: string | null): ExecutionDebuggerController {
  const [snapshots, setSnapshots] = useState<Record<string, ExecutionSnapshot>>({});
  const [cursors, setCursors] = useState<Record<string, EventCursor>>({});
  const [debuggerData, setDebuggerData] = useState<Record<string, {
    checkpoints: import("@/components/right-sidebar/debugger-panel").CheckpointInspector[];
    waits: import("@/lib/execution-debugger").DurableWait[];
    drafts: RevisionDraft[];
  }>>({});
  const [selectedExecutionId, setSelectedExecutionId] = useState<string | null>(requestedExecutionId || null);
  const [connection, setConnection] = useState<DebuggerConnection>({ state: "reconnecting" });
  const refreshToken = useRef(0);

  const loadDebuggerData = useCallback(async (executionId: string, signal?: AbortSignal) => {
    const state = await getExecutionDebuggerState(executionId, signal);
    setDebuggerData((current) => ({
      ...current,
      [executionId]: {
        checkpoints: (state.checkpoints || []).map((checkpoint) => ({
          ...checkpoint,
          status_version: checkpoint.status_version ?? checkpoint.source_execution_version ?? 0,
          safe_point: checkpoint.safe_point || null,
          frontier: checkpoint.frontier || [],
          effect_receipts: checkpoint.effect_receipts || [],
        })),
        waits: state.waits || [],
        drafts: (state.drafts || []).map(parseRevisionState),
      },
    }));
    return state;
  }, []);

  const recover = useCallback(async (executionId: string, afterSequence: number) => {
    setConnection((current) => ({ ...current, state: "reconnecting", message: "Refreshing canonical execution state…" }));
    try {
      const replay = await getExecutionEvents(executionId, afterSequence);
      if (!replay.snapshot?.execution_id) throw new Error("The execution recovery response is incomplete.");
      setSnapshots((current) => ({ ...current, [executionId]: replay.snapshot as ExecutionSnapshot }));
      if (replay.event_cursor) setCursors((current) => ({ ...current, [executionId]: replay.event_cursor as EventCursor }));
      await loadDebuggerData(executionId);
      setConnection({ state: "connected", cursor: replay.event_cursor || null });
    } catch (error) {
      setConnection({ state: "stale", message: errorMessage(error) });
    }
  }, [loadDebuggerData]);

  const refresh = useCallback(() => {
    const token = ++refreshToken.current;
    const controller = new AbortController();
    void (async () => {
      setConnection({ state: "reconnecting", message: "Loading canonical execution state…" });
      try {
        const list = await getRunningExecutions(controller.signal);
        if (token !== refreshToken.current) return;
        const next: Record<string, ExecutionSnapshot> = {};
        const nextCursors: Record<string, EventCursor> = {};
        for (const item of list.items || []) {
          if (item.kind !== "execution" || !item.snapshot?.execution_id) continue;
          next[item.snapshot.execution_id] = item.snapshot;
          if (item.event_cursor) nextCursors[item.snapshot.execution_id] = item.event_cursor;
        }
        setSnapshots((current) => ({ ...current, ...next }));
        setCursors((current) => ({ ...current, ...nextCursors }));
        const inspectionId = selectedExecutionId || Object.keys(next)[0];
        if (inspectionId) {
          void loadDebuggerData(inspectionId, controller.signal).catch((error) => {
            if (!(error instanceof DOMException && error.name === "AbortError")) {
              setConnection({ state: "stale", message: errorMessage(error) });
            }
          });
        }
        setConnection({ state: "connected", cursor: selectedExecutionId ? nextCursors[selectedExecutionId] || null : null });
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") return;
        if (token === refreshToken.current) setConnection({ state: "stale", message: errorMessage(error) });
      }
    })();
    return controller;
  }, [loadDebuggerData, selectedExecutionId]);

  useEffect(() => {
    if (!active) return;
    const controller = refresh();
    const timer = window.setInterval(() => { refresh(); }, 5000);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, [active, refresh]);

  useEffect(() => {
    if (!requestedExecutionId) return;
    setSelectedExecutionId(requestedExecutionId);
    const controller = new AbortController();
    void (async () => {
      try {
        if (!snapshots[requestedExecutionId]) {
          const snapshot = await getExecutionSnapshot(requestedExecutionId, controller.signal);
          setSnapshots((current) => ({ ...current, [requestedExecutionId]: snapshot }));
          setConnection((current) => ({ ...current, state: "connected" }));
        }
        await loadDebuggerData(requestedExecutionId, controller.signal);
      } catch (error) {
        if (!(error instanceof DOMException && error.name === "AbortError")) {
          setConnection({ state: "stale", message: errorMessage(error) });
        }
      }
    })();
    return () => controller.abort();
  }, [loadDebuggerData, requestedExecutionId, snapshots]);

  useEffect(() => {
    if (!active) return;
    const onUpdate = (event: WindowEventMap["op:execution-update"]) => {
      const detail = event.detail || {};
      const execution = detail.execution;
      if (!execution?.execution_id) return;
      const executionId = execution.execution_id;
      const previous = snapshots[executionId];
      if (!previous || !Number.isSafeInteger(execution.event_sequence)) {
        setSnapshots((current) => ({ ...current, [executionId]: execution }));
        if (detail.event_cursor) setCursors((current) => ({ ...current, [executionId]: detail.event_cursor as EventCursor }));
        void loadDebuggerData(executionId).catch((error) => {
          setConnection({ state: "stale", message: errorMessage(error) });
        });
        setConnection({ state: "connected", cursor: detail.event_cursor || null });
        return;
      }
      const eventValue: ExecutionEvent = {
        sequence: execution.event_sequence,
        status_version: execution.status_version,
        execution,
      };
      const reduced = reduceExecutionEvent(previous, eventValue);
      if (reduced.kind === "gap") {
        setConnection({ state: "gap", expected_sequence: reduced.expected, received_sequence: reduced.received });
        void recover(executionId, previous.event_sequence);
        return;
      }
      if (reduced.kind === "stale") {
        setConnection({ state: "stale", message: "Received an older execution snapshot." });
        return;
      }
      setSnapshots((current) => ({ ...current, [executionId]: reduced.snapshot }));
      if (detail.event_cursor) setCursors((current) => ({ ...current, [executionId]: detail.event_cursor as EventCursor }));
      void loadDebuggerData(executionId).catch((error) => {
        setConnection({ state: "stale", message: errorMessage(error) });
      });
      setConnection({ state: "connected", cursor: detail.event_cursor || null });
    };
    window.addEventListener("op:execution-update", onUpdate);
    return () => window.removeEventListener("op:execution-update", onUpdate);
  }, [active, loadDebuggerData, recover, snapshots]);

  const executions = useMemo(() => Object.values(snapshots).sort((a, b) => b.updated_at - a.updated_at), [snapshots]);
  const selected = selectedExecutionId ? snapshots[selectedExecutionId] : undefined;

  const selectExecution = useCallback((executionId: string) => {
    setSelectedExecutionId(executionId);
    const cursor = cursors[executionId];
    setConnection((current) => ({ ...current, cursor: cursor || null }));
    const controller = new AbortController();
    void (async () => {
      try {
        if (!snapshots[executionId]) {
          const snapshot = await getExecutionSnapshot(executionId, controller.signal);
          setSnapshots((current) => ({ ...current, [executionId]: snapshot }));
        }
        await loadDebuggerData(executionId, controller.signal);
      } catch (error) {
        if (!(error instanceof DOMException && error.name === "AbortError")) {
          setConnection({ state: "stale", message: errorMessage(error) });
        }
      }
    })();
  }, [cursors, loadDebuggerData, snapshots]);

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
    await recover(input.execution_id, snapshots[input.execution_id]?.event_sequence ?? 0);
  }, [recover, snapshots]);

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
  const selectedKey = selected?.execution_id || selectedExecutionId || executions[0]?.execution_id || null;
  const selectedData = selectedKey && debuggerData[selectedKey]
    ? selectDebuggerInspection(debuggerData[selectedKey], selectedKey)
    : undefined;
  return {
    executions,
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
