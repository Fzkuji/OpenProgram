"use client";

import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
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
} from "@/lib/execution/execution-debugger";
import { buildWaitAnswer } from "@/lib/execution/execution-wait";
import type { PersistedExecutionEvent, UnresolvedEffect } from "@/lib/net/execution-client";
import { SidebarNotice } from "./sidebar-notice";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { SectionHeader } from "../sidebar/section-header";
import { useTranslation } from "@/lib/i18n";
import { ExecutionStrip } from "../chat/messages/execution-strip";
import { executionTitle, executionRequest, executionGuidance, executionNeedsAttention, executionStatusLabel, activityRows, shortTime, updatedTime } from "./debugger-presentation";
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
  detailOnly?: boolean;
  executions: ExecutionSnapshot[];
  sessionId?: string | null;
  events?: PersistedExecutionEvent[];
  unresolvedEffects?: UnresolvedEffect[];
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
    preparation?: { instructions: string; rationale?: string };
  }) => Promise<void> | void;
  onUpdateDraft?: (draft: RevisionDraft, changes: RevisionDraft["changes"] | { instructions: string; rationale?: string }) => Promise<void> | void;
  onDraftAction?: (
    draft: RevisionDraft,
    action: "validate" | "approve" | "publish" | "fork",
  ) => Promise<void> | void;
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

function connectionCopy(connection: DebuggerConnection): { label: string; detail: string } {
  if (connection.state === "connected") return { label: "Synced", detail: "Last fetched snapshot" };
  if (connection.state === "reconnecting") return { label: "Reconnecting", detail: "Snapshot will be refreshed before replay" };
  if (connection.state === "gap") return { label: "Event gap", detail: connection.message || "Automatically reloading before applying more events" };
  if (connection.state === "stale") return { label: "Stale", detail: connection.message || "This view is behind the canonical snapshot" };
  return { label: "Conflict", detail: connection.message || "The server rejected an optimistic version" };
}

function formatUnknown(value: unknown): string {
  if (value == null || value === "") return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function eventSummary(event: PersistedExecutionEvent): string {
  const payload = event.payload || {};
  const values: string[] = [];
  if (event.kind === "function.incompatible" && typeof payload.reason === "string") values.push(payload.reason);
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
  const { text } = useTranslation();
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
            <span className={styles.executionName}>{executionTitle(execution, executions.length - executions.indexOf(execution), text)}</span>
            <span className={styles.executionMeta}>{executionStatusLabel(execution, text)}</span>
          </span>
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
    <Button variant="ghost"
      type="button"
      className={action === "cancel" ? styles.cancelButton : undefined}
      onClick={() => void submit()}
      disabled={disabled}
      title={!onCommand ? "Waiting for the latest execution status" : !ready ? action === "steer" ? "Enter the next instruction" : "Create and publish an instruction branch first" : undefined}
    >
      {pending ? "Submitting…" : ACTION_LABELS[action]}
    </Button>
  );
}

function CommandNotice({ result }: { result: CommandResult | null }) {
  const { text } = useTranslation();
  if (!result) return null;
  const errors: Record<string, [string, string]> = {
    continuation_contract_mismatch: ["The saved runtime differs from the current tools or settings. Restore the matching runtime before resuming.", "保存时的运行环境与当前工具或设置不一致。恢复对应环境后才能继续。"],
    version_conflict: ["The task changed before this request arrived. Its status updates automatically; try again once it is available.", "提交请求前任务状态已改变。状态将自动更新，恢复后可重试。"],
    stale_version: ["The task changed before this request arrived. Its status updates automatically; try again once it is available.", "提交请求前任务状态已改变。状态将自动更新，恢复后可重试。"],
    permission_denied: ["Your current permissions do not allow this action.", "当前权限不允许此操作。"],
    checkpoint_not_found: ["The saved point is unavailable. Wait for the task status to update before choosing another action.", "保存点不可用。请等待任务状态自动更新后选择其他操作。"],
  };
  const message = result.status === "rejected"
    ? text(...(errors[result.rejection_code || ""] || ["The request was rejected. Task status updates automatically; Technical details contains the recorded reason.", "请求被拒绝。任务状态会自动更新；技术详情中保留了具体原因。"] as [string, string]))
    : text(...({ accepted: ["Request accepted; waiting for a safe boundary.", "请求已接受，等待安全执行边界。"], applying: ["Applying request…", "正在应用请求…"], applied: ["Request applied.", "请求已应用。"] }[result.status] as [string, string]));
  return (
    <div className={`${styles.commandNotice} ${result.status === "rejected" ? styles.noticeDanger : ""}`} role="status">
      <span>{message}</span>
    </div>
  );
}

export function DebuggerPanel({
  executions,
  sessionId,
  events = [],
  unresolvedEffects = [],
  fetchedAt,
  detailOnly = false,
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
}: DebuggerPanelProps) {
  const { text } = useTranslation();
  const [localSelectedId, setLocalSelectedId] = useState<string | null>(selectedExecutionId || executions[0]?.execution_id || null);
  const [codePolicies, setCodePolicies] = useState<Record<string, string>>({});
  const [commandResults, setCommandResults] = useState<Record<string, CommandResult>>({});
  const [pendingActions, setPendingActions] = useState<Set<string>>(new Set());
  const [waitValues, setWaitValues] = useState<Record<string, string>>({});
  const [approvalScopes, setApprovalScopes] = useState<Record<string, string>>({});
  const [pendingWaits, setPendingWaits] = useState<Set<string>>(new Set());
  const waitKey = (wait: DurableWait) => `${wait.wait_id}:${wait.claim_generation}`;
  const [waitError, setWaitError] = useState<string | null>(null);
  const [steerValue, setSteerValue] = useState("");
  const [editor, setEditor] = useState<"steer" | "branch" | null>(null);
  const editorEpoch = useRef(0);
  const steerRevision = useRef(0);
  function openEditor(next: "steer" | "branch" | null) {
    editorEpoch.current += 1;
    setEditor(next);
  }
  const [draftText, setDraftText] = useState<string | null>(null);
  const [draftPending, setDraftPending] = useState(false);
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
  // Both the server list and locally created drafts are oldest-first.
  // Reopening the inspector must keep the newest revision editable.
  const selectedDraft = snapshot ? drafts.slice().reverse().find((draft) => draft.source_execution_id === snapshot.execution_id) : undefined;
  const connectionInfo = connectionCopy(connection);
  useEffect(() => { setDraftText(null); setDraftError(null); }, [selectedDraft?.draft_id]);



  useEffect(() => {
    setCommandResults((current) => {
      let next = current;
      for (const event of events) {
        const value = event.payload?.command;
        if (!value || typeof value !== "object") continue;
        const command = value as Record<string, unknown>;
        const id = command.command_id;
        const status = command.status;
        if (typeof id !== "string" || !["accepted", "applying", "applied", "rejected"].includes(String(status))) continue;
        const entry = Object.entries(next).find(([, result]) => result.command_id === id);
        if (!entry || entry[1].status === status) continue;
        next = { ...next, [entry[0]]: { ...entry[1], status: status as CommandResult["status"], rejection_code: typeof command.rejection_code === "string" ? command.rejection_code : null } };
      }
      return next;
    });
  }, [events]);

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
    if (pendingWaits.has(waitKey(wait))) return;
    setWaitError(null);
    setPendingWaits((current) => new Set(current).add(waitKey(wait)));
    try {
      const waitValue = waitValues[waitKey(wait)] || "";
      const approvalScope = approvalScopes[waitKey(wait)] || "";
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
      setWaitValues((current) => ({ ...current, [waitKey(wait)]: "" }));
      setApprovalScopes((current) => ({ ...current, [waitKey(wait)]: "" }));
    } catch (error) {
      setWaitError(error instanceof Error ? error.message : "Wait response failed.");
    } finally {
      setPendingWaits((current) => { const next = new Set(current); next.delete(waitKey(wait)); return next; });
    }
  }

  if (!snapshot) {
    return (
      <section className={styles.panel} aria-label="Execution details">
        <SidebarNotice>
          <div>{connection.state === "stale" ? "Could not load executions. Retrying automatically." : connection.state === "reconnecting" && sessionId ? "Loading executions…" : "No executions in this conversation."}</div>
          {connection.message && <div>{connection.message}</div>}
          {!sessionId && <div>Run a message to see its execution here.</div>}
        </SidebarNotice>
      </section>
    );
  }

  const actionPayloads: Partial<Record<ExecutionCommandAction, Record<string, unknown>>> = {
    steer: steerValue.trim() ? { message: steerValue.trim() } : undefined,
    retry: snapshot.checkpoint_head_id ? { checkpoint_id: snapshot.checkpoint_head_id } : undefined,
    fork: selectedDraft?.status === "published" && selectedDraft.manifest?.manifest_id && selectedDraft.manifest.proof_hash && selectedDraft.manifest.compatible_checkpoint_id
      ? {
        manifest_id: selectedDraft.manifest.manifest_id,
        checkpoint_id: selectedDraft.manifest.compatible_checkpoint_id,
        proof_hash: selectedDraft.manifest.proof_hash,
      }
      : undefined,
  };

  const health: CursorHealth = connection.state === "connected"
    ? "healthy"
    : connection.state === "conflict" ? "stale" : connection.state;

  return (
    <section className={styles.panel} aria-label="Execution details">
      {connection.state !== "connected" && <div className={styles.connectionLine} data-health={health}>
        <span title={connectionInfo.detail}>{connectionInfo.label}{fetchedAt ? ` · ${shortTime(fetchedAt)}` : ""}</span>
      </div>}

      <div className={`${styles.layout} ${(detailOnly || executions.length === 1) ? styles.singleExecution : ""}`}>
        {!detailOnly && executions.length > 1 && <aside className={styles.executionRail} aria-label="Executions">
          <SectionHeader name={`Executions · ${executions.length}`} collapsible={false} collapsed={false} onToggle={() => {}} className="px-3 py-2" />
          {executions.length ? <ExecutionTree executions={executions} selectedId={snapshot.execution_id} onSelect={selectExecution} /> : <div className={styles.empty}>No executions available.</div>}
        </aside>}

        <div className={styles.content}>
          <section className={styles.hero}>
            <div className={styles.heroTop}>
              <div>
                <h3>{executionTitle(snapshot, executions.length - executions.indexOf(snapshot), text)}</h3>
                <p className={styles.muted}>{text("Updated", "更新于")} {updatedTime(snapshot.updated_at)}</p>
              </div>
              <div className={`${styles.statusBadge} ${statusClass(snapshot.status)}`}><span className={styles.statusDot} />{executionStatusLabel(snapshot, text)}</div>
            </div>
            {snapshot.status === "reconciliation_required" && executionNeedsAttention(snapshot) ? <div className={styles.reason}>
              {unresolvedEffects.some((effect) => effect.kind === "provider.before")
                ? text("The model request has no confirmed response. This run is waiting for its result to be resolved.", "模型请求尚无已确认的响应，本次执行正在等待结果核对。")
                : unresolvedEffects.some((effect) => effect.kind === "tool.before")
                  ? text("A tool result is unconfirmed. Verify the external action before starting it again.", "工具结果尚未确认。再次执行前需要核对外部操作的实际结果。")
                  : text("An external action has an unconfirmed outcome. This run needs attention before it can continue.", "外部操作结果尚未确认，需要处理后才能继续执行。")}
              {unresolvedEffects.filter((effect) => effect.tool_name).map((effect) => <div key={effect.effect_id}>{effect.tool_name}</div>)}
            </div> : executionGuidance(snapshot, text) && <p className={styles.muted}>{executionGuidance(snapshot, text)}</p>}

            {snapshot.status === "paused" && <label>
              {text("Function code after restart", "重启后函数代码")}
              <select aria-label={text("Function code after restart", "重启后函数代码")} value={codePolicies[snapshot.execution_id] || ""} onChange={(event) => setCodePolicies((previous) => ({ ...previous, [snapshot.execution_id]: event.target.value }))}>
                <option value="">{text("Keep task policy", "沿用任务设置")}</option>
                <option value="keep_original">{text("Continue original code", "继续原代码")}</option>
                <option value="use_latest">{text("Use new code with saved results", "保留已有结果，使用新代码")}</option>
              </select>
            </label>}
            <div className={styles.actions}>
              {(["pause", "continue", "step", "retry", "cancel"] as ExecutionCommandAction[]).filter((action) => availableExecutionActions(snapshot).includes(action)).map((action) => (
                <ActionButton key={action} action={action} snapshot={snapshot} pending={pendingActions.has(`execution.${action}`)} payload={action === "continue" && codePolicies[snapshot.execution_id] ? { code_change_policy: codePolicies[snapshot.execution_id] } : actionPayloads[action]} onCommand={onCommand && connection.state === "connected" ? submitAction : undefined} />
              ))}
              {availableExecutionActions(snapshot).includes("steer") && <Button variant="ghost" onClick={() => openEditor("steer")}>{text("Add instruction", "补充指令")}</Button>}
              {(selectedDraft || (snapshot.capabilities.fork && snapshot.checkpoint_head_id)) && <Button variant="ghost" onClick={() => openEditor("branch")}>{text("Create branch", "创建分支")}</Button>}
            </div>
            <div className={styles.commandStack}>
              {Object.values(commandResults).map((result) => <CommandNotice key={result.command_id} result={result} />)}
            </div>
          </section>

          {selectedWaits.length > 0 && <section className={styles.card}>
            <div className={styles.cardHeader}><h4>Question and approval waits</h4><span>{selectedWaits.length} open</span></div>
            {selectedWaits.length ? selectedWaits.map((wait) => (
              <div className={styles.waitRow} key={wait.wait_id}>
                <div><strong>{wait.kind === "approval" ? text("Approval needed", "需要授权") : text("Answer needed", "需要回答")}</strong><p>{wait.request?.prompt || text("This execution is waiting for your response.", "此执行正在等待你的回复。")}</p></div>
                <div className={styles.waitControls}>
                  {wait.kind === "approval" ? (
                    <select aria-label="Approval scope" value={approvalScopes[waitKey(wait)] || ""} onChange={(event) => setApprovalScopes((current) => ({ ...current, [waitKey(wait)]: event.target.value }))} disabled={!onRespondWait || pendingWaits.has(waitKey(wait))}>
                      <option value="">Choose approval scope</option>
                      {(wait.policy_snapshot?.allowed_scopes ?? ["once"]).map((scope) => <option key={scope} value={scope}>{scope}</option>)}
                    </select>
                  ) : wait.kind === "form" ? (
                    <Textarea aria-label="Form answer" value={waitValues[waitKey(wait)] || ""} onChange={(event) => setWaitValues((current) => ({ ...current, [waitKey(wait)]: event.target.value }))} placeholder={JSON.stringify(Object.fromEntries(Object.entries(wait.request?.schema || {}).map(([name, field]) => [name, field.default ?? ""])), null, 2)} disabled={!onRespondWait || pendingWaits.has(waitKey(wait))} />
                  ) : wait.kind === "ask_many" || wait.request?.multi ? (
                    <Textarea aria-label={`${wait.kind} answer`} value={waitValues[waitKey(wait)] || ""} onChange={(event) => setWaitValues((current) => ({ ...current, [waitKey(wait)]: event.target.value }))} placeholder={'["answer 1", ["answer 2"]]'} disabled={!onRespondWait || pendingWaits.has(waitKey(wait))} />
                  ) : wait.request?.options?.length ? (
                    <select aria-label={`${wait.kind} answer`} value={waitValues[waitKey(wait)] || ""} onChange={(event) => setWaitValues((current) => ({ ...current, [waitKey(wait)]: event.target.value }))} disabled={!onRespondWait || pendingWaits.has(waitKey(wait))}>
                      <option value="">Choose an answer</option>
                      {wait.request.options.map((option) => <option key={option} value={option}>{option}</option>)}
                    </select>
                  ) : (
                    <Input aria-label={`${wait.kind} answer`} value={waitValues[waitKey(wait)] || ""} onChange={(event) => setWaitValues((current) => ({ ...current, [waitKey(wait)]: event.target.value }))} placeholder="Answer" disabled={!onRespondWait || pendingWaits.has(waitKey(wait))} />
                  )}
                  <Button variant="ghost" type="button" onClick={() => void respondWait(wait, "answer")} disabled={!onRespondWait || pendingWaits.has(waitKey(wait)) || (wait.kind === "approval" && !approvalScopes[waitKey(wait)])}>Answer</Button><Button variant="ghost" type="button" onClick={() => void respondWait(wait, "decline")} disabled={!onRespondWait || pendingWaits.has(waitKey(wait))}>Decline</Button>
                </div>
              </div>
            )) : <div className={styles.empty}>No unresolved execution-owned waits.</div>}
            {waitError && <div className={styles.formError} role="alert">{waitError}</div>}
          </section>}


          {executionRequest(snapshot) && executionRequest(snapshot) !== executionTitle(snapshot, executions.length - executions.indexOf(snapshot), text) && <ExecutionStrip label={text("Request excerpt", "请求摘要")}><p className={styles.requestText}>{executionRequest(snapshot)}</p></ExecutionStrip>}
          {activityRows(events, text).length > 0 && <section className={styles.card}>
            <div className={styles.cardHeader}><h4>{text("Progress", "执行进展")}</h4></div>
            <ol className={styles.eventList}>{activityRows(events, text).slice(0, 8).map((event) => (
              <li key={event.sequence}><span>{event.title}</span>{event.time && <time>{shortTime(event.time * 1000)}</time>}</li>
            ))}</ol>
          </section>}
          <ExecutionStrip label={text("Technical details", "技术详情")}>
            {Object.values(commandResults).map((result) => <p key={result.command_id}>{result.command_id} · {result.status}{result.rejection_code ? ` · ${result.rejection_code}` : ""}</p>)}
            <dl className={styles.definitionList}>
              <div><dt>Execution ID</dt><dd>{snapshot.execution_id}</dd></div>
              <div><dt>Run ID</dt><dd>{snapshot.run_id}</dd></div>
              <div><dt>Revision</dt><dd>{snapshot.revision_id}</dd></div>
              <div><dt>State version</dt><dd>{snapshot.status_version}</dd></div>
              {connection.cursor && <div><dt>Event cursor</dt><dd>{connection.cursor.next_sequence}</dd></div>}
              {snapshot.reason_code && <div><dt>Reason code</dt><dd>{snapshot.reason_code}</dd></div>}
            </dl>
            <ol className={styles.eventList}>{events.slice(-50).reverse().map((event) => (
              <li key={event.sequence}><span>#{event.sequence}</span><span>{event.kind}<small className={styles.eventDetail}>{eventSummary(event)}</small></span>{event.execution_version != null && <span>v{event.execution_version}</span>}</li>
            ))}</ol>
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

          </ExecutionStrip>

          <Dialog open={editor === "steer"} onOpenChange={open => openEditor(open ? "steer" : null)}>
            <DialogContent>
              <DialogHeader><DialogTitle>{text("Add instruction", "补充指令")}</DialogTitle>
                <DialogDescription>{text("Applied at the next safe execution boundary. Work already completed is retained.", "在下一个安全执行边界应用，已经完成的操作保持不变。")}</DialogDescription></DialogHeader>
              <label className={styles.editorLabel}>{text("Instruction", "指令")}<Textarea maxLength={4096} value={steerValue} onChange={event => { steerRevision.current += 1; setSteerValue(event.target.value); }} placeholder={text("Describe the change", "描述需要调整的内容")} /></label>
              <CommandNotice result={commandResults["execution.steer"] || null} />
              <DialogFooter><ActionButton action="steer" snapshot={snapshot} pending={pendingActions.has("execution.steer")} payload={actionPayloads.steer} ready={Boolean(steerValue.trim())}
                onCommand={onCommand && connection.state === "connected" ? async command => {
                  const submittedEditor = editorEpoch.current;
                  const submittedRevision = steerRevision.current;
                  const result = await submitAction(command);
                  if (result && result.status !== "rejected" && steerRevision.current === submittedRevision) {
                    setSteerValue("");
                    if (editorEpoch.current === submittedEditor) openEditor(null);
                  }
                  return result;
                } : undefined} /></DialogFooter>
            </DialogContent>
          </Dialog>
          <Dialog open={editor === "branch"} onOpenChange={open => openEditor(open ? "branch" : null)}>
            <DialogContent className={styles.editorDialog}>
              <DialogHeader><DialogTitle>{text("Create branch", "创建分支")}</DialogTitle>
                <DialogDescription>{text("Continue from this saved point with new instructions. The original execution stays unchanged.", "从此保存点按新指令继续，原执行保持不变。")}</DialogDescription></DialogHeader>
            {(!selectedDraft || selectedDraft.editor) ? <label className={styles.editorLabel}>
              {text("Instructions for the new branch", "新分支的指令")}
              <Textarea maxLength={4096} value={draftText ?? selectedDraft?.editor?.instructions ?? ""} onChange={(event) => setDraftText(event.target.value)} disabled={draftPending} readOnly={Boolean(selectedDraft && ["published", "discarded"].includes(selectedDraft.status))} />
            </label> : <p className={styles.muted}>{text("This revision was prepared by another client.", "此修订由其他客户端准备。")}</p>}
            {selectedDraft && <p className={styles.muted}>{({ draft: "Draft", validated: "Validated", approved: "Approved", published: "Ready to create branch", discarded: "Discarded", rejected: "Needs changes" })[selectedDraft.status]}</p>}
            <DialogFooter className={styles.revisionActions}>
              {selectedDraft?.editor && ["published", "discarded"].includes(selectedDraft.status) && snapshot.checkpoint_head_id && <Button variant="ghost" disabled={draftPending || !onCreateDraft} onClick={async () => {
                setDraftError(null); setDraftPending(true);
                try { await onCreateDraft?.({ execution_id: snapshot.execution_id, source_checkpoint_id: snapshot.checkpoint_head_id!, preparation: { instructions: selectedDraft.editor!.instructions } }); }
                catch (error) { setDraftError(error instanceof Error ? error.message : "Could not prepare new instructions."); }
                finally { setDraftPending(false); }
              }}>{text("Edit as new draft", "编辑为新草稿")}</Button>}
              {(!selectedDraft || (!["published", "discarded"].includes(selectedDraft.status) && selectedDraft.editor)) && <Button variant={selectedDraft ? "ghost" : "default"} disabled={draftPending || (selectedDraft ? !onUpdateDraft : !onCreateDraft) || !(draftText ?? selectedDraft?.editor?.instructions ?? "").trim() || Boolean(selectedDraft && (draftText === null || draftText === selectedDraft.editor?.instructions))} onClick={async () => {
                setDraftError(null); setDraftPending(true);
                try {
                  const preparation = { instructions: (draftText ?? selectedDraft?.editor?.instructions ?? "").trim() };
                  if (selectedDraft) await onUpdateDraft?.(selectedDraft, preparation);
                  else await onCreateDraft?.({ execution_id: snapshot.execution_id, source_checkpoint_id: snapshot.checkpoint_head_id!, preparation });
                  setDraftText(null);
                } catch (error) { setDraftError(error instanceof Error ? error.message : "Could not save instructions."); }
                finally { setDraftPending(false); }
              }}>{selectedDraft ? text("Save instructions", "保存指令") : text("Prepare branch", "准备分支")}</Button>}
              {selectedDraft && (["validate", "approve", "publish", "fork"] as const).filter((action) => ({ validate: "draft", approve: "validated", publish: "approved", fork: "published" })[action] === selectedDraft.status).map((action) => <Button variant="default" key={action} disabled={draftPending || !onDraftAction || (draftText !== null && draftText !== selectedDraft.editor?.instructions)} onClick={async () => {
                setDraftError(null); setDraftPending(true);
                try { await onDraftAction?.(selectedDraft, action); }
                catch (error) { setDraftError(error instanceof Error ? error.message : "Could not apply revision action."); }
                finally { setDraftPending(false); }
              }}>{({ validate: "Check compatibility", approve: "Approve revision", publish: "Publish revision", fork: "Create branch" })[action]}</Button>)}
            </DialogFooter>
            {draftError && <div className={styles.formError} role="alert">{draftError}</div>}
            </DialogContent>
          </Dialog>
        </div>
      </div>
    </section>
  );
}
