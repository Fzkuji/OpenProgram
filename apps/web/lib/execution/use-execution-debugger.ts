"use client";

import { showToast } from "@/lib/format-utils/toast";

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
} from "@/lib/execution/execution-debugger";
import {
  ExecutionApiError,
  type PersistedExecutionEvent,
  type UnresolvedEffect,
  createRevisionDraft,
  getExecutionDebuggerState,
  getExecutionEvents,
  getExecutionSnapshot,
  getSessionExecutions,
  inspectionFromDebuggerState,
  postExecutionCommand,
  postExecutionWait,
  postRevisionDraftCommand,
} from "@/lib/net/execution/execution-client";
import "@/lib/net/ws/ws-events";
import { useSessionStore } from "@/lib/session-store";
import { activityReadFreshFor, activityReadIsFresh, lastActivityRead, activityReadStart, rememberActivityRead } from "./activity-reads";

export type ExecutionDebuggerController = {
  executions: ExecutionSnapshot[];
  branches?: import("./execution-debugger").ConversationActivityBranch[];
  events: PersistedExecutionEvent[];
  unresolvedEffects: UnresolvedEffect[];
  fetchedAt: number | null;
  selectedExecutionId: string | null;
  checkpoints: import("@/components/right-sidebar/debugger/debugger-panel").CheckpointInspector[];
  waits: import("@/lib/execution/execution-debugger").DurableWait[];
  drafts: RevisionDraft[];
  connection: DebuggerConnection;
  selectExecution: (executionId: string) => void;
  refresh: () => Promise<boolean>;
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
    preparation?: { instructions: string; rationale?: string };
    changes?: RevisionChange[];
    frontier_mapping?: Array<Record<string, unknown>>;
  }) => Promise<RevisionDraft>;
  updateDraft: (draft: RevisionDraft, changes: RevisionChange[] | { instructions: string; rationale?: string }) => Promise<void>;
  draftAction: (draft: RevisionDraft, action: "validate" | "approve" | "publish" | "fork") => Promise<void>;
};

type DebuggerConnection = {
  errorSessionId?: string | null;
  state: "connected" | "reconnecting" | "stale" | "gap" | "conflict";
  cursor?: EventCursor | null;
  expected_sequence?: number | null;
  received_sequence?: number | null;
  message?: string | null;
};

/** One read of a conversation's executions: what the list draws. */
type ExecutionList = {
  sessionId: string;
  snapshots: Record<string, ExecutionSnapshot>;
  branches: import("./execution-debugger").ConversationActivityBranch[];
  cursors: Record<string, EventCursor>;
  fetchedAt: number;
};

/** A periodic read follows the last one by this long, and event-driven
 *  reads follow each other at least `READ_GAP_MS` apart. */
const POLL_MS = 30000;
const READ_GAP_MS = 1000;
/** A failed read is tried again after 2, 4, 8 … seconds, up to `POLL_MS`. */
const retryDelay = (failures: number) => Math.min(POLL_MS, 1000 * 2 ** failures);

function errorMessage(error: unknown): string {
  return error instanceof ExecutionApiError || error instanceof Error
    ? error.message
    : "Execution request failed.";
}

export function useExecutionDebugger(active: boolean, sessionId: string | null, requestedExecutionId?: string | null, inspect = true): ExecutionDebuggerController {
  // The conversation's last read draws at once, from this page's earlier
  // visit when there was one; a new read replaces it in place.
  const [read, setRead] = useState<ExecutionList | null>(() => lastActivityRead<ExecutionList>("executions", sessionId));
  const list = read?.sessionId === sessionId ? read : lastActivityRead<ExecutionList>("executions", sessionId);
  const snapshots = useMemo(() => list?.snapshots ?? {}, [list]);
  const branches = useMemo(() => list?.branches ?? [], [list]);
  const cursors = useMemo(() => list?.cursors ?? {}, [list]);
  const fetchedAt = list?.fetchedAt ?? null;
  const setSnapshots = useCallback((update: (current: Record<string, ExecutionSnapshot>) => Record<string, ExecutionSnapshot>) => {
    setRead((current) => {
      const base = current?.sessionId === sessionId ? current : lastActivityRead<ExecutionList>("executions", sessionId);
      return base ? { ...base, snapshots: update(base.snapshots) } : current;
    });
  }, [sessionId]);
  const [events, setEvents] = useState<PersistedExecutionEvent[]>([]);
  const [debuggerData, setDebuggerData] = useState<Record<string, {
    checkpoints: import("@/components/right-sidebar/debugger/debugger-panel").CheckpointInspector[];
    waits: import("@/lib/execution/execution-debugger").DurableWait[];
    drafts: RevisionDraft[];
    unresolvedEffects?: UnresolvedEffect[];
  }>>({});
  const [selectedExecutionId, setSelectedExecutionId] = useState<string | null>(null);
  const [connection, setConnection] = useState<DebuggerConnection>({ state: "reconnecting" });
  const refreshToken = useRef(0);
  const mounted = useRef(true);
  const refreshController = useRef<AbortController | null>(null);
  const refreshPromise = useRef<Promise<boolean> | null>(null);
  useEffect(() => {
    mounted.current = true;
    const refreshes = refreshToken;
    return () => { mounted.current = false; refreshes.current++; refreshController.current?.abort(); };
  }, []);

  useEffect(() => {
    setEvents([]);
    setDebuggerData({}); setSelectedExecutionId(null);
    setConnection({ state: "reconnecting" });
  }, [sessionId]);

  const loadDebuggerData = useCallback(async (executionId: string, signal?: AbortSignal) => {
    const state = await getExecutionDebuggerState(executionId, signal, sessionId);
    if (!mounted.current || signal?.aborted) return state;
    setDebuggerData((current) => ({ ...current, [executionId]: inspectionFromDebuggerState(state) }));
    return state;
  }, [sessionId]);

  const refresh = useCallback((): Promise<boolean> => {
    if (!sessionId || !active) return Promise.resolve(false);
    if (refreshPromise.current) return refreshPromise.current;
    const request = (async () => {
      const token = ++refreshToken.current;
      const startedAt = activityReadStart();
      const controller = new AbortController();
      refreshController.current = controller;
      const signal = AbortSignal.any([controller.signal, AbortSignal.timeout(15000)]);
      try {
        const list = await getSessionExecutions(sessionId, signal);
        const next: Record<string, ExecutionSnapshot> = {};
        const nextCursors: Record<string, EventCursor> = {};
        for (const item of list.items || []) {
          if (!item.snapshot?.execution_id) continue;
          next[item.snapshot.execution_id] = { ...item.snapshot, started_at: item.started_at, task_label: item.task_label, scheduled_task_id: item.scheduled_task_id ?? null, view_parent_execution_id: item.parent_execution_id };
          if (item.event_cursor) nextCursors[item.snapshot.execution_id] = item.event_cursor;
        }
        const inspectionId = inspect ? ([selectedExecutionId, requestedExecutionId].find((id) => id && next[id])
          || Object.values(next).sort((a, b) => b.updated_at - a.updated_at)[0]?.execution_id) : undefined;
        let history: PersistedExecutionEvent[] = [];
        if (inspectionId) {
          const [replay] = await Promise.all([
            getExecutionEvents(inspectionId, Math.max(0, next[inspectionId].event_sequence - 50), signal, sessionId),
            loadDebuggerData(inspectionId, signal),
          ]);
          if (replay.snapshot?.execution_id !== inspectionId || replay.snapshot.session_id !== next[inspectionId].session_id) throw new Error("Execution does not belong to this conversation.");
          next[inspectionId] = { ...next[inspectionId], ...replay.snapshot };
          if (replay.event_cursor) nextCursors[inspectionId] = replay.event_cursor;
          history = replay.events || [];
        }
        if (!mounted.current || token !== refreshToken.current || signal.aborted) return false;
        const read: ExecutionList = { sessionId, snapshots: next, branches: list.branches || [], cursors: nextCursors, fetchedAt: Date.now() };
        rememberActivityRead("executions", sessionId, read, startedAt);
        setRead(read);
        setEvents(history);
        setConnection({ state: "connected", cursor: inspectionId ? nextCursors[inspectionId] || null : null });
        return true;
      } catch (error) {
        if (!mounted.current || token !== refreshToken.current || controller.signal.aborted) return false;
        setConnection({ state: "stale", message: errorMessage(error), errorSessionId: sessionId });
        return false;
      } finally {
        if (refreshController.current === controller) refreshController.current = null;
      }
    })();
    refreshPromise.current = request;
    void request.then(() => { if (refreshPromise.current === request) refreshPromise.current = null; });
    return request;
  }, [active, sessionId, selectedExecutionId, requestedExecutionId, loadDebuggerData, inspect]);

  useEffect(() => {
    if (!active || !sessionId) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    let reading = false;
    let dirty = false;
    let failures = 0;
    const visible = () => document.visibilityState !== "hidden";
    const pause = (ms: number) => new Promise<void>((resolve) => { timer = setTimeout(resolve, ms); });
    const update = async () => {
      if (cancelled || !visible()) return;
      dirty = true;
      if (reading) return;
      reading = true;
      clearTimeout(timer);
      let read = false;
      try {
        do {
          dirty = false;
          const started = Date.now();
          read = await refresh();
          failures = read ? 0 : failures + 1;
          // Moves that arrived during the read are taken in one more read,
          // a gap after this one began: a busy turn is not a read per step.
          if (dirty && !cancelled) await pause(Math.max(0, READ_GAP_MS - (Date.now() - started)));
        } while (dirty && !cancelled && visible());
      } finally {
        reading = false;
        if (!cancelled) timer = setTimeout(update, read ? POLL_MS : retryDelay(failures));
      }
    };
    // Events invalidate the authorized list, including newly created child sessions.
    // Never merge their possibly out-of-scope payloads into the current view.
    const onUpdate = () => { void update(); };
    const unsubscribe = useSessionStore.subscribe((state, previous) => {
      if (state.wsStatus === "open" && previous.wsStatus !== "open") onUpdate();
    });
    window.addEventListener("op:execution-update", onUpdate);
    window.addEventListener("op:job-status", onUpdate);
    window.addEventListener("online", onUpdate);
    window.addEventListener("focus", onUpdate);
    document.addEventListener("visibilitychange", onUpdate);
    // Coming back to a read nothing has changed since needs no new one; the
    // inspector reads events no earlier visit kept, so it always reads.
    if (!inspect && activityReadIsFresh("executions", sessionId)) {
      timer = setTimeout(update, activityReadFreshFor("executions", sessionId));
    } else void update();
    const refreshes = refreshToken;
    return () => {
      cancelled = true;
      clearTimeout(timer);
      refreshes.current++;
      refreshController.current?.abort();
      refreshController.current = null;
      refreshPromise.current = null;
      unsubscribe();
      window.removeEventListener("op:execution-update", onUpdate);
      window.removeEventListener("op:job-status", onUpdate);
      window.removeEventListener("online", onUpdate);
      window.removeEventListener("focus", onUpdate);
      document.removeEventListener("visibilitychange", onUpdate);
    };
  }, [active, sessionId, refresh, inspect]);

  const executions = useMemo(() => Object.values(snapshots).sort((a, b) => (b.started_at ?? b.updated_at) - (a.started_at ?? a.updated_at)), [snapshots]);
  const selectedKey = [selectedExecutionId, requestedExecutionId].find((id) => id && snapshots[id]) || executions[0]?.execution_id || null;
  const selectExecution = useCallback((executionId: string) => {
    if (!snapshots[executionId]) return;
    setSelectedExecutionId(executionId);
    setEvents([]);
    setConnection({ state: "reconnecting", cursor: cursors[executionId] || null });
  }, [snapshots, cursors]);

  const command = useCallback(async (commandValue: ExecutionCommand): Promise<CommandResult> => {
    if (commandValue.action === "execution.pause") showToast("Pause requested. Waiting for the current operation to finish; use Stop to interrupt it.");
    if (commandValue.action === "execution.cancel") showToast("Stop requested. Cancelling the current operation…");
    try {
      const result = await postExecutionCommand(commandValue);
      const resultExecution = result.execution;
      if (resultExecution?.execution_id) {
        setSnapshots((current) => ({ ...current, [resultExecution.execution_id]: { ...current[resultExecution.execution_id], ...resultExecution } }));
        void loadDebuggerData(resultExecution.execution_id).catch((error) => {
          setConnection({ state: "stale", message: errorMessage(error), errorSessionId: sessionId });
        });
      }
      const childId = result.result_json?.child_execution_id;
      if (childId) {
        const child = await getExecutionSnapshot(childId, undefined, sessionId);
        const scope = await getSessionExecutions(sessionId!);
        if (mounted.current && scope.items.some(item => item.snapshot?.execution_id === childId)) {
          setSnapshots((current) => ({ ...current, [childId]: child }));
          setSelectedExecutionId(childId);
          setEvents([]);
          setConnection({ state: "reconnecting" });
        }
      }
      return result;
    } catch (error) {
      setConnection({ state: "conflict", message: errorMessage(error), errorSessionId: sessionId });
      throw error;
    } finally {
      // Commands can be rejected without publishing an execution event.
      void refresh();
    }
  }, [loadDebuggerData, sessionId, refresh, setSnapshots]);

  const respondWait = useCallback(async (input: Parameters<ExecutionDebuggerController["respondWait"]>[0]) => {
    await postExecutionWait({ ...input, generation: input.claim_generation, expected_version: snapshots[input.execution_id]?.status_version ?? 0 });
    await refresh();
  }, [refresh, snapshots]);

  const createDraft = useCallback(async (input: Parameters<ExecutionDebuggerController["createDraft"]>[0]) => {
    const draft = await createRevisionDraft({
      execution_id: input.execution_id,
      source_checkpoint_id: input.source_checkpoint_id,
      preparation: input.preparation,
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

  const updateDraft = useCallback(async (draft: RevisionDraft, changes: RevisionChange[] | { instructions: string; rationale?: string }) => {
    const updated = await postRevisionDraftCommand({
      execution_id: draft.source_execution_id,
      draft_id: draft.draft_id,
      action: "revision.draft.replace",
      expected_draft_version: draft.draft_version,
      payload: Array.isArray(changes)
        ? { changes, frontier_mapping: draft.frontier_mapping || [] }
        : { preparation: changes },
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
      if (!draft.manifest?.manifest_id || !draft.manifest.proof_hash || !draft.manifest.compatible_checkpoint_id) throw new ExecutionApiError(409, "manifest_required", "A published revision manifest is required before fork.");
      const snapshot = snapshots[draft.source_execution_id];
      if (!snapshot) throw new ExecutionApiError(404, "execution_not_loaded", "The source execution snapshot is not loaded.");
      const forkCommand = buildExecutionCommand(snapshot, "fork", newCommandId(), {
        manifest_id: draft.manifest.manifest_id,
        checkpoint_id: draft.manifest.compatible_checkpoint_id,
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
    branches,
    events,
    unresolvedEffects: selectedKey ? debuggerData[selectedKey]?.unresolvedEffects || [] : [],
    fetchedAt,
    selectedExecutionId: selectedKey,
    checkpoints: (selectedData?.checkpoints || []) as import("@/components/right-sidebar/debugger/debugger-panel").CheckpointInspector[],
    waits: selectedData?.waits || [],
    drafts: selectedData?.drafts || [],
    connection,
    selectExecution,
    refresh,
    command,
    respondWait,
    createDraft,
    updateDraft,
    draftAction,
  };
}
