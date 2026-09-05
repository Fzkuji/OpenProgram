"use client";

import { useEffect, useMemo, useState, type ReactNode } from "react";
import {
  availableExecutionActions,
  buildExecutionCommand,
  newCommandId,
  type CommandResult,
  type CursorHealth,
  type DurableWait,
  type EventCursor,
  type ExecutionCommand,
  type ExecutionCommandAction,
  type ExecutionSnapshot,
  type RevisionDraft,
} from "@/lib/execution-debugger";
import { buildWaitAnswer } from "@/lib/execution-wait";
import type { PersistedExecutionEvent } from "@/lib/net/execution-client";
import styles from "./debugger-panel.module.css";

export type DebuggerConnection = {
  state: "connected" | "reconnecting" | "stale" | "gap" | "conflict";
  cursor?: EventCursor | null;
  expected_sequence?: number | null;
  received_sequence?: number | null;
  message?: string | null;
};

export type CheckpointInspector = {
  checkpoint_id: string;
  execution_id: string;
  revision_id: string;
  parent_checkpoint_id?: string | null;
  status_version: number;
  safe_point?: Record<string, unknown> | null;
  frontier?: Array<{ step_id: string; status: string; contract_hash?: string }>;
  pending_inputs?: string[];
  effect_receipts?: Array<{ effect_id: string; status: string; kind?: string }>;
};

export type DebuggerPanelProps = {
  executions: ExecutionSnapshot[];
  sessionId?: string | null;
  events?: PersistedExecutionEvent[];
  fetchedAt?: number | null;
  selectedExecutionId?: string | null;
  connection: DebuggerConnection;
  checkpoints?: CheckpointInspector[];
  waits?: DurableWait[];
  drafts?: RevisionDraft[];
  onSelectExecution?: (executionId: string) => void;
  onCommand?: (command: ExecutionCommand) => Promise<CommandResult | void> | CommandResult | void;
  onRespondWait?: (input: {
    wait_id: string;
    execution_id: string;
    claim_generation: number;
    outcome: "answer" | "decline";
    value?: unknown;
  }) => Promise<void> | void;
  onCreateDraft?: (input: {
    execution_id: string;
    source_checkpoint_id: string;
  }) => Promise<void> | void;
  onUpdateDraft?: (draft: RevisionDraft, changes: RevisionDraft["changes"]) => Promise<void> | void;
  onDraftAction?: (
    draft: RevisionDraft,
    action: "validate" | "approve" | "publish" | "fork",
  ) => Promise<void> | void;
  onRefresh?: () => void;
};

const ACTION_LABELS: Record<ExecutionCommandAction, string> = {
  pause: "Pause",
  continue: "Continue",
  step: "Step",
  steer: "Steer",
  fork: "Fork",
  retry: "Retry",
  cancel: "Cancel",
};

const STATUS_LABELS: Record<string, string> = {
  queued: "Queued",
  running: "Running",
  pausing: "Pausing",
  paused: "Paused",
  cancelling: "Cancelling",
  reconciliation_required: "Needs reconciliation",
  completed: "Completed",
  failed: "Failed",
  cancelled: "Cancelled",
  interrupted: "Interrupted",
};

function shortId(value: string | null | undefined): string {
  if (!value) return "—";
  return value.length > 16 ? `${value.slice(0, 8)}…${value.slice(-6)}` : value;
}

function statusClass(status: string): string {
  if (status === "running" || status === "pausing") return styles.active;
  if (status === "paused") return styles.paused;
  if (status === "failed" || status === "reconciliation_required") return styles.danger;
  if (["completed", "cancelled", "interrupted"].includes(status)) return styles.terminal;
  return styles.queued;
}

function connectionCopy(connection: DebuggerConnection): { label: string; detail: string; className: string } {
  if (connection.state === "connected") return { label: "Synced", detail: "Last fetched snapshot", className: styles.connectionGood };
  if (connection.state === "reconnecting") return { label: "Reconnecting", detail: "Snapshot will be refreshed before replay", className: styles.connectionWarn };
  if (connection.state === "gap") return { label: "Event gap", detail: connection.message || "Refresh required before applying more events", className: styles.connectionDanger };
  if (connection.state === "stale") return { label: "Stale", detail: connection.message || "This view is behind the canonical snapshot", className: styles.connectionWarn };
  return { label: "Conflict", detail: connection.message || "The server rejected an optimistic version", className: styles.connectionDanger };
}

function formatUnknown(value: unknown): string {
  if (value == null || value === "") return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function eventSummary(event: PersistedExecutionEvent): string {
  const payload = event.payload || {};
  const values: string[] = [];
  for (const key of ["record", "attempt", "command"]) {
    const value = payload[key];
    if (!value || typeof value !== "object") continue;
    const row = value as Record<string, unknown>;
    for (const field of ["action", "status", "outcome", "reason_code"]) {
      if (typeof row[field] === "string" && !values.includes(row[field] as string)) values.push(row[field] as string);
    }
  }
  return values.join(" · ");
}

function ResourceSummary({ resource }: { resource: Record<string, unknown> | null }) {
  if (!resource) return <div className={styles.empty}>No resource snapshot published.</div>;
  const queueWait = resource.queue_wait as Record<string, unknown> | null | undefined;
  const usage = resource.usage as Record<string, unknown> | null | undefined;
  return (
    <div className={styles.resourceGrid}>
      <div><span>State</span><strong>{formatUnknown(resource.resource_state)}</strong></div>
      <div><span>Lease</span><strong>{formatUnknown(resource.resource_lease_generation)}</strong></div>
      <div><span>Queue</span><strong>{queueWait ? formatUnknown(queueWait.position ?? queueWait.state) : "Not waiting"}</strong></div>
      <div><span>Usage</span><strong>{usage ? formatUnknown(usage) : "—"}</strong></div>
    </div>
  );
}

function ExecutionTree({
  executions,
  selectedId,
  onSelect,
}: {
  executions: ExecutionSnapshot[];
  selectedId: string | null;
  onSelect?: (id: string) => void;
}) {
  const children = useMemo(() => {
    const grouped = new Map<string | null, ExecutionSnapshot[]>();
    for (const execution of executions) {
      const key = execution.parent_execution_id && executions.some((item) => item.execution_id === execution.parent_execution_id)
        ? execution.parent_execution_id
        : null;
      grouped.set(key, [...(grouped.get(key) || []), execution]);
    }
    return grouped;
  }, [executions]);

  function renderBranch(parentId: string | null, depth = 0): ReactNode {
    return (children.get(parentId) || []).map((execution) => (
      <div key={execution.execution_id} className={styles.treeBranch}>
        <button
          type="button"
          className={`${styles.executionItem} ${execution.execution_id === selectedId ? styles.selected : ""}`}
          style={{ paddingLeft: `${12 + depth * 16}px` }}
          onClick={() => onSelect?.(execution.execution_id)}
          aria-current={execution.execution_id === selectedId ? "true" : undefined}
        >
          <span className={`${styles.statusDot} ${statusClass(execution.status)}`} aria-hidden="true" />
          <span className={styles.executionText}>
            <span className={styles.executionName}>{shortId(execution.execution_id)}</span>
            <span className={styles.executionMeta}>{STATUS_LABELS[execution.status] || execution.status} · rev {shortId(execution.revision_id)}</span>
          </span>
          <span className={styles.version}>v{execution.status_version}</span>
        </button>
        {renderBranch(execution.execution_id, depth + 1)}
      </div>
    ));
  }

  return <div className={styles.executionTree}>{renderBranch(null)}</div>;
}

function ActionButton({
  action,
  snapshot,
  pending,
  payload = {},
  ready = true,
  onCommand,
}: {
  action: ExecutionCommandAction;
  snapshot: ExecutionSnapshot;
  pending: boolean;
  payload?: Record<string, unknown>;
  ready?: boolean;
  onCommand?: (command: ExecutionCommand) => Promise<CommandResult | void> | CommandResult | void;
}) {
  const available = availableExecutionActions(snapshot).includes(action);
  const disabled = !available || !ready || !onCommand || pending;
  async function submit() {
    if (disabled) return;
    const command = buildExecutionCommand(snapshot, action, newCommandId(), payload);
    await onCommand?.(command);
  }
  return (
    <button
      type="button"
      className={`${styles.actionButton} ${action === "cancel" ? styles.cancelButton : ""}`}
      onClick={() => void submit()}
      disabled={disabled}
      title={!onCommand ? "Control service is not connected" : !ready ? "A published reference is required" : undefined}
    >
      {pending ? "Submitting…" : ACTION_LABELS[action]}
    </button>
  );
}

function CommandNotice({ result }: { result: CommandResult | null }) {
  if (!result) return null;
  return (
    <div className={`${styles.commandNotice} ${result.status === "rejected" ? styles.noticeDanger : ""}`} role="status">
      <span>{result.status}</span>
      <span>{result.rejection_code || `command ${shortId(result.command_id)}`}</span>
    </div>
  );
}

export function DebuggerPanel({
  executions,
  sessionId,
  events = [],
  fetchedAt,
  selectedExecutionId,
  connection,
  checkpoints = [],
  waits = [],
  drafts = [],
  onSelectExecution,
  onCommand,
  onRespondWait,
  onCreateDraft,
  onUpdateDraft,
  onDraftAction,
  onRefresh,
}: DebuggerPanelProps) {
  const [localSelectedId, setLocalSelectedId] = useState<string | null>(selectedExecutionId || executions[0]?.execution_id || null);
  const [commandResults, setCommandResults] = useState<Record<string, CommandResult>>({});
  const [pendingActions, setPendingActions] = useState<Set<string>>(new Set());
  const [waitValue, setWaitValue] = useState("");
  const [approvalScope, setApprovalScope] = useState("");
  const [waitError, setWaitError] = useState<string | null>(null);
  const [steerValue, setSteerValue] = useState("");
  const [draftText, setDraftText] = useState("");
  const [draftError, setDraftError] = useState<string | null>(null);
  const selectedId = selectedExecutionId !== undefined
    ? selectedExecutionId
    : localSelectedId ?? executions[0]?.execution_id ?? null;
  const snapshot = selectedId
    ? executions.find((execution) => execution.execution_id === selectedId) ?? null
    : null;
  const selectedCheckpoint = snapshot?.checkpoint_head_id
    ? checkpoints.find((checkpoint) => checkpoint.checkpoint_id === snapshot.checkpoint_head_id)
    : undefined;
  const selectedWaits = snapshot ? waits.filter((wait) => wait.execution_id === snapshot.execution_id && ["open", "claimed"].includes(wait.status)) : [];
  const selectedDraft = snapshot ? drafts.find((draft) => draft.source_execution_id === snapshot.execution_id) : undefined;
  const connectionInfo = connectionCopy(connection);

  useEffect(() => {
    setWaitValue("");
    setApprovalScope("");
  }, [selectedWaits.map((wait) => wait.wait_id).join(",")]);

  function selectExecution(id: string) {
    setLocalSelectedId(id);
    onSelectExecution?.(id);
  }

  async function submitAction(command: ExecutionCommand): Promise<CommandResult | void> {
    setPendingActions((current) => new Set(current).add(command.action));
    try {
      const result = await onCommand?.(command);
      if (result) setCommandResults((current) => ({ ...current, [command.action]: result }));
      return result;
    } catch (error) {
      setCommandResults((current) => ({
        ...current,
        [command.action]: {
          command_id: command.command_id,
          status: "rejected",
          rejection_code: error instanceof Error ? error.message : "command_failed",
        },
      }));
      return undefined;
    } finally {
      setPendingActions((current) => {
        const next = new Set(current);
        next.delete(command.action);
        return next;
      });
    }
  }

  async function respondWait(wait: DurableWait, outcome: "answer" | "decline") {
    if (!onRespondWait) return;
    setWaitError(null);
    try {
      let answer: unknown = waitValue.trim();
      if (outcome === "answer") {
        if (wait.kind === "form" || wait.kind === "ask_many" || wait.request?.multi) {
          try {
            answer = JSON.parse(waitValue);
          } catch {
            throw new Error("Enter a valid JSON object or array.");
          }
        }
        answer = buildWaitAnswer(wait, answer, approvalScope);
      }
      await onRespondWait({
        wait_id: wait.wait_id,
        execution_id: wait.execution_id,
        claim_generation: wait.claim_generation,
        outcome,
        value: outcome === "answer" ? answer : undefined,
      });
      setWaitValue("");
      setApprovalScope("");
    } catch (error) {
      setWaitError(error instanceof Error ? error.message : "Wait response failed.");
    }
  }

  if (!snapshot) {
    return (
      <section className={styles.panel} aria-label="Debugger">
        <header className={styles.header}><div><p className={styles.kicker}>Runtime control</p><h2>Debugger</h2></div><span className={styles.connectionBadge}>{connectionInfo.label}</span></header>
        <div className={styles.emptyState}><strong>{connection.state === "stale" ? "Could not load executions" : connection.state === "reconnecting" && sessionId ? "Loading conversation executions…" : "No executions in this conversation"}</strong><span>{connection.message || (sessionId ? "Execution history appears here when this conversation runs." : "Open a conversation to inspect its execution history.")}</span>{onRefresh && sessionId && <button type="button" className={styles.textButton} onClick={onRefresh}>Refresh</button>}</div>
      </section>
    );
  }

  const actionPayloads: Partial<Record<ExecutionCommandAction, Record<string, unknown>>> = {
    steer: steerValue.trim() ? { message: steerValue.trim() } : undefined,
    retry: snapshot.checkpoint_head_id ? { checkpoint_id: snapshot.checkpoint_head_id } : undefined,
    fork: selectedDraft?.status === "published" && selectedDraft.manifest?.manifest_id && selectedDraft.manifest.proof_hash
      ? {
        manifest_id: selectedDraft.manifest.manifest_id,
        checkpoint_id: snapshot.checkpoint_head_id,
        proof_hash: selectedDraft.manifest.proof_hash,
      }
      : undefined,
  };

  const health: CursorHealth = connection.state === "connected"
    ? "healthy"
    : connection.state === "conflict" ? "stale" : connection.state;

  return (
    <section className={styles.panel} aria-label="Debugger">
      <header className={styles.header}>
        <div><p className={styles.kicker}>Runtime control</p><h2>Debugger</h2></div>
        <div className={`${styles.connectionBadge} ${connectionInfo.className}`} title={connectionInfo.detail}>
          <span className={styles.connectionDot} aria-hidden="true" />{connectionInfo.label}
        </div>
      </header>
      <div className={styles.connectionLine} data-health={health}>
        <span>{connectionInfo.detail}{fetchedAt ? ` · ${new Date(fetchedAt).toLocaleTimeString()}` : ""}</span>
        <span>cursor {connection.cursor?.next_sequence ?? "—"}</span>
        {onRefresh && <button type="button" className={styles.textButton} onClick={onRefresh}>Refresh snapshot</button>}
      </div>

      <div className={styles.layout}>
        <aside className={styles.executionRail} aria-label="Executions">
          <div className={styles.sectionTitle}><span>Executions</span><span>{executions.length}</span></div>
          {executions.length ? <ExecutionTree executions={executions} selectedId={snapshot.execution_id} onSelect={selectExecution} /> : <div className={styles.empty}>No executions available.</div>}
        </aside>

        <div className={styles.content}>
          <section className={styles.hero}>
            <div className={styles.heroTop}>
              <div>
                <div className={styles.overline}>Execution</div>
                <h3>{shortId(snapshot.execution_id)}</h3>
                <p className={styles.muted}>Run {shortId(snapshot.run_id)} · revision {shortId(snapshot.revision_id)}</p>
                <p className={styles.muted}>Last execution update: {new Date(snapshot.updated_at * 1000).toLocaleString()}</p>
              </div>
              <div className={`${styles.statusBadge} ${statusClass(snapshot.status)}`}><span className={styles.statusDot} />{STATUS_LABELS[snapshot.status] || snapshot.status}</div>
            </div>
            <div className={styles.identityGrid}>
              <div><span>Version</span><strong>{snapshot.status_version}</strong></div>
              <div><span>Attempt</span><strong>{shortId(snapshot.current_attempt_id)}</strong></div>
              <div><span>Safe point</span><strong>{formatUnknown(snapshot.safe_point?.kind)}</strong></div>
              <div><span>Checkpoint</span><strong>{shortId(snapshot.checkpoint_head_id)}</strong></div>
            </div>
            {snapshot.reason_code && <div className={styles.reason}>{snapshot.reason_code === "effect_reconciliation" ? "A tool action has an unconfirmed result. Execution is blocked pending reconciliation; this is not ongoing generation." : snapshot.reason_code}</div>}
            {availableExecutionActions(snapshot).includes("steer") && <label className={styles.steerInput}>Steer input ref<input value={steerValue} onChange={(event) => setSteerValue(event.target.value)} placeholder="Durable input reference" /></label>}
            <div className={styles.actions}>
              {(["pause", "continue", "step", "steer", "fork", "retry", "cancel"] as ExecutionCommandAction[]).map((action) => (
                <ActionButton key={action} action={action} snapshot={snapshot} pending={pendingActions.has(`execution.${action}`)} payload={actionPayloads[action]} ready={(action !== "steer" || Boolean(steerValue.trim())) && (action !== "fork" || Boolean(actionPayloads.fork))} onCommand={onCommand ? submitAction : undefined} />
              ))}
            </div>
            <div className={styles.commandStack}>
              {Object.values(commandResults).map((result) => <CommandNotice key={result.command_id} result={result} />)}
            </div>
          </section>

          <section className={styles.card}>
            <div className={styles.cardHeader}><h4>Execution history</h4><span>Latest {events.length} events</span></div>
            {events.length ? <ol className={styles.eventList}>{events.slice(-50).reverse().map((event) => (
              <li key={event.sequence}><span>#{event.sequence}</span><span>{event.kind.replaceAll(".", " · ")}<small className={styles.eventDetail}>{eventSummary(event)}</small></span><span>v{event.execution_version}</span></li>
            ))}</ol> : <div className={styles.empty}>No execution events recorded.</div>}
            {events.length > 50 && <div className={styles.empty}>Showing the latest 50 returned events.</div>}
          </section>

          {(!snapshot.resource || !selectedCheckpoint || !selectedWaits.length || !selectedDraft) && <p className={styles.muted}>Not recorded for this execution: {[
            !snapshot.resource && "resource snapshot", !selectedCheckpoint && "checkpoint",
            !selectedWaits.length && "open questions or approvals", !selectedDraft && "revision draft",
          ].filter(Boolean).join(", ")}.</p>}
          {(snapshot.resource || Object.keys(snapshot.effect_summary).length > 0) && <div className={styles.twoColumn}>
            {snapshot.resource && <section className={styles.card}>
              <div className={styles.cardHeader}><h4>Resource wait</h4><span>{snapshot.resource?.resource_state ? String(snapshot.resource.resource_state) : "unavailable"}</span></div>
              <ResourceSummary resource={snapshot.resource} />
            </section>}
            {Object.keys(snapshot.effect_summary).length > 0 && <section className={styles.card}>
              <div className={styles.cardHeader}><h4>Effects</h4><span>{formatUnknown(snapshot.effect_summary.unresolved)} unresolved</span></div>
              <dl className={styles.definitionList}>
                {Object.entries(snapshot.effect_summary).map(([key, value]) => <div key={key}><dt>{key.replaceAll("_", " ")}</dt><dd>{formatUnknown(value)}</dd></div>)}
              </dl>
            </section>}
          </div>}

          {selectedCheckpoint && <section className={styles.card}>
            <div className={styles.cardHeader}><h4>Checkpoint inspector</h4><span>{selectedCheckpoint ? "Published" : "Not selected"}</span></div>
            {selectedCheckpoint ? (
              <div className={styles.inspectorGrid}>
                <div><span>ID</span><code>{selectedCheckpoint.checkpoint_id}</code></div>
                <div><span>Revision</span><code>{selectedCheckpoint.revision_id}</code></div>
                <div><span>Status version</span><code>{selectedCheckpoint.status_version}</code></div>
                <div><span>Parent</span><code>{shortId(selectedCheckpoint.parent_checkpoint_id)}</code></div>
                <div className={styles.inspectorWide}><span>Frontier</span><div className={styles.frontier}>{(selectedCheckpoint.frontier || []).map((item) => <span key={item.step_id} className={styles.frontierItem}>{item.step_id}<small>{item.status}</small></span>)}</div></div>
                <div className={styles.inspectorWide}><span>Effect receipts</span><div className={styles.receipts}>{(selectedCheckpoint.effect_receipts || []).map((item) => <span key={item.effect_id}>{item.effect_id} · {item.status}</span>)}</div></div>
              </div>
            ) : <div className={styles.empty}>Only published checkpoint snapshots can be inspected.</div>}
          </section>}

          {selectedWaits.length > 0 && <section className={styles.card}>
            <div className={styles.cardHeader}><h4>Question and approval waits</h4><span>{selectedWaits.length} open</span></div>
            {selectedWaits.length ? selectedWaits.map((wait) => (
              <div className={styles.waitRow} key={wait.wait_id}>
                <div><strong>{wait.kind}</strong><span>{shortId(wait.wait_id)} · generation {wait.claim_generation}</span><code>{wait.request_ref}</code></div>
                <div className={styles.waitControls}>
                  {wait.kind === "approval" ? (
                    <select aria-label="Approval scope" value={approvalScope} onChange={(event) => setApprovalScope(event.target.value)} disabled={!onRespondWait}>
                      <option value="">Choose approval scope</option>
                      {(wait.policy_snapshot?.allowed_scopes || []).map((scope) => <option key={scope} value={scope}>{scope}</option>)}
                    </select>
                  ) : wait.kind === "form" ? (
                    <textarea aria-label="Form answer" value={waitValue} onChange={(event) => setWaitValue(event.target.value)} placeholder={JSON.stringify(Object.fromEntries(Object.entries(wait.request?.schema || {}).map(([name, field]) => [name, field.default ?? ""])), null, 2)} disabled={!onRespondWait} />
                  ) : wait.kind === "ask_many" || wait.request?.multi ? (
                    <textarea aria-label={`${wait.kind} answer`} value={waitValue} onChange={(event) => setWaitValue(event.target.value)} placeholder={'["answer 1", ["answer 2"]]'} disabled={!onRespondWait} />
                  ) : wait.request?.options?.length ? (
                    <select aria-label={`${wait.kind} answer`} value={waitValue} onChange={(event) => setWaitValue(event.target.value)} disabled={!onRespondWait}>
                      <option value="">Choose an answer</option>
                      {wait.request.options.map((option) => <option key={option} value={option}>{option}</option>)}
                    </select>
                  ) : (
                    <input aria-label={`${wait.kind} answer`} value={waitValue} onChange={(event) => setWaitValue(event.target.value)} placeholder="Answer" disabled={!onRespondWait} />
                  )}
                  <button type="button" onClick={() => void respondWait(wait, "answer")} disabled={!onRespondWait || (wait.kind === "approval" && !approvalScope)}>Answer</button><button type="button" onClick={() => void respondWait(wait, "decline")} disabled={!onRespondWait}>Decline</button>
                </div>
              </div>
            )) : <div className={styles.empty}>No unresolved execution-owned waits.</div>}
            {waitError && <div className={styles.formError} role="alert">{waitError}</div>}
          </section>}

          {(selectedDraft || snapshot.checkpoint_head_id) && <section className={styles.card}>
            <div className={styles.cardHeader}><h4>Revision draft</h4><span>{selectedDraft ? selectedDraft.status : "No draft"}</span></div>
            {selectedDraft ? (
              <div className={styles.revisionEditor}>
                <div className={styles.editorMeta}><span>Draft {shortId(selectedDraft.draft_id)}</span><span>source checkpoint {shortId(selectedDraft.source_checkpoint_id)}</span><span>base {shortId(selectedDraft.base_revision_id)}</span></div>
                <label className={styles.editorLabel}>Supported changes <textarea value={draftText || JSON.stringify(selectedDraft.changes, null, 2)} onChange={(event) => setDraftText(event.target.value)} spellCheck={false} /></label>
                <div className={styles.revisionActions}>
                  {selectedDraft.status === "draft" && <button type="button" onClick={() => { try { const changes = JSON.parse(draftText) as RevisionDraft["changes"]; setDraftError(null); void Promise.resolve(onUpdateDraft?.(selectedDraft, changes)).catch((error) => setDraftError(error instanceof Error ? error.message : "Draft update failed.")); } catch { setDraftText("Invalid JSON change list"); } }} disabled={!onUpdateDraft}>Save draft</button>}
                  {(["validate", "approve", "publish", "fork"] as const).map((action) => <button key={action} type="button" onClick={() => { setDraftError(null); void Promise.resolve(onDraftAction?.(selectedDraft, action)).catch((error) => setDraftError(error instanceof Error ? error.message : "Revision action failed.")); }} disabled={!onDraftAction || (action === "validate" ? selectedDraft.status !== "draft" : action === "approve" ? selectedDraft.status !== "validated" : action === "publish" ? selectedDraft.status !== "approved" : selectedDraft.status !== "published")}>{action[0].toUpperCase() + action.slice(1)}</button>)}
                </div>
                {draftError && <div className={styles.formError} role="alert">{draftError}</div>}
                {selectedDraft.validation && <div className={styles.validation}><span>Report {shortId(selectedDraft.validation.report_ref)}</span><span>Reusable {selectedDraft.validation.reusable_steps.length}</span><span>Affected {selectedDraft.validation.affected_steps.length}</span>{selectedDraft.validation.error_code && <strong>{selectedDraft.validation.error_code}</strong>}</div>}
              </div>
            ) : (
              <div className={styles.empty}>
                <span>Create a draft through the revision service to edit future logic. This view never mutates the source execution.</span>
                {onCreateDraft && snapshot.checkpoint_head_id && (
                  <button type="button" className={styles.actionButton} onClick={() => { setDraftError(null); void Promise.resolve(onCreateDraft({ execution_id: snapshot.execution_id, source_checkpoint_id: snapshot.checkpoint_head_id! })).catch((error) => setDraftError(error instanceof Error ? error.message : "Draft creation failed.")); }}>
                    Create draft
                  </button>
                )}
                {draftError && <div className={styles.formError} role="alert">{draftError}</div>}
              </div>
            )}
          </section>}
        </div>
      </div>
    </section>
  );
}
