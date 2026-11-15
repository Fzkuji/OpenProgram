"use client";

import { useEffect, useMemo, useState, type ReactNode } from "react";
import {
  availableExecutionActions,
  type CommandResult,
  type CursorHealth,
  type DurableWait,
  type EventCursor,
  type ExecutionCommand,
  type ExecutionCommandAction,
  type ExecutionSnapshot,
  type RevisionDraft,
} from "@/lib/execution/execution-debugger";
import type { PersistedExecutionEvent, UnresolvedEffect } from "@/lib/net/execution/execution-client";
import { SidebarNotice } from "../sidebar-notice";
import { Button } from "@/components/ui/button";
import { SectionHeader } from "../../sidebar/sessions/section-header";
import { useTranslation } from "@/lib/i18n";
import { ExecutionStrip } from "../../chat/messages/execution/execution-strip";
import { executionTitle, executionRequest, executionStatusLabel, activityRows, shortTime, updatedTime } from "./debugger-presentation";
import { ExecutionActionButton, ExecutionBranchDialog, ExecutionCommandNotice, ExecutionSteerDialog } from "./execution-dialogs";
import { ExecutionCodePolicySelect, ExecutionStandingNote, ExecutionTechnicalDetails } from "./execution-technical-details";
import { ExecutionWaitForms } from "./execution-wait-forms";
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
          <span className="min-w-0 flex-1 grid gap-[2px]">
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
  const [editor, setEditor] = useState<"steer" | "branch" | null>(null);
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

  // The pieces both layouts share: the standing's reason and controls, the
  // open waits, the progress rows, the raw details fold and the editors.
  const connectedCommand = onCommand && connection.state === "connected" ? submitAction : undefined;
  const heroBody = <><ExecutionStandingNote snapshot={snapshot} unresolvedEffects={unresolvedEffects} />
            {snapshot.status === "paused" && <ExecutionCodePolicySelect value={codePolicies[snapshot.execution_id] || ""} onChange={(value) => setCodePolicies((previous) => ({ ...previous, [snapshot.execution_id]: value }))} />}
            <div className={styles.actions}>
              {(["pause", "continue", "step", "retry", "cancel"] as ExecutionCommandAction[]).filter((action) => availableExecutionActions(snapshot).includes(action)).map((action) => (
                <ExecutionActionButton key={action} action={action} snapshot={snapshot} pending={pendingActions.has(`execution.${action}`)} payload={action === "continue" && codePolicies[snapshot.execution_id] ? { code_change_policy: codePolicies[snapshot.execution_id] } : actionPayloads[action]} onCommand={connectedCommand} />
              ))}
              {availableExecutionActions(snapshot).includes("steer") && <Button variant="ghost" onClick={() => setEditor("steer")}>{text("Add instruction", "补充指令")}</Button>}
              {(selectedDraft || (snapshot.capabilities.fork && snapshot.checkpoint_head_id)) && <Button variant="ghost" onClick={() => setEditor("branch")}>{text("Create branch", "创建分支")}</Button>}
            </div>
            <div className="grid gap-[6px]">
              {Object.values(commandResults).map((result) => <ExecutionCommandNotice key={result.command_id} result={result} />)}
            </div></>;
  const waitsCard = <ExecutionWaitForms waits={selectedWaits} onRespond={onRespondWait} />;
  const progressRows = activityRows(events, text);
  const techStrip = <ExecutionStrip label={text("Technical details", "技术详情")}>
            <ExecutionTechnicalDetails snapshot={snapshot} events={events} connection={connection} commandResults={commandResults} checkpoint={selectedCheckpoint} />
          </ExecutionStrip>;
  const dialogs = <><ExecutionSteerDialog open={editor === "steer"} onOpenChange={open => setEditor(open ? "steer" : null)} snapshot={snapshot} pending={pendingActions.has("execution.steer")} result={commandResults["execution.steer"] || null} onCommand={connectedCommand} />
          <ExecutionBranchDialog open={editor === "branch"} onOpenChange={open => setEditor(open ? "branch" : null)} snapshot={snapshot} draft={selectedDraft} onCreateDraft={onCreateDraft} onUpdateDraft={onUpdateDraft} onDraftAction={onDraftAction} /></>;

  return (
    <section className={styles.panel} aria-label="Execution details">
      {connection.state !== "connected" && <div className={styles.connectionLine} data-health={health}>
        <span data-tip={connectionInfo.detail}>{connectionInfo.label}{fetchedAt ? ` · ${shortTime(fetchedAt)}` : ""}</span>
      </div>}

      <div className={`${styles.layout} ${(detailOnly || executions.length === 1) ? styles.singleExecution : ""}`}>
        {!detailOnly && executions.length > 1 && <aside className={`${styles.executionRail} min-w-0 overflow-y-auto p-[0_8px_8px]`} aria-label="Executions">
          <SectionHeader name={`Executions · ${executions.length}`} collapsible={false} collapsed={false} onToggle={() => {}} className="px-3 py-2" />
          {executions.length ? <ExecutionTree executions={executions} selectedId={snapshot.execution_id} onSelect={selectExecution} /> : <div className={styles.empty}>No executions available.</div>}
        </aside>}

        <div className="min-w-0 overflow-y-auto p-[8px_16px_16px]">
          <section className={styles.hero}>
            <div className={`${styles.heroTop} grid grid-cols-[minmax(0,_1fr)] gap-[8px] items-start`}>
              <div>
                <h3>{executionTitle(snapshot, executions.length - executions.indexOf(snapshot), text)}</h3>
                <p className={styles.muted}>{text("Updated", "更新于")} {updatedTime(snapshot.updated_at)}</p>
              </div>
              <div className={`${styles.statusBadge} ${statusClass(snapshot.status)}`}><span className={styles.statusDot} />{executionStatusLabel(snapshot, text)}</div>
            </div>
            {heroBody}
          </section>

          {waitsCard}

          {executionRequest(snapshot) && executionRequest(snapshot) !== executionTitle(snapshot, executions.length - executions.indexOf(snapshot), text) && <ExecutionStrip label={text("Request excerpt", "请求摘要")}><p className={styles.requestText}>{executionRequest(snapshot)}</p></ExecutionStrip>}
          {progressRows.length > 0 && <section className={styles.card}>
            <div className={`${styles.cardHeader} flex flex-wrap items-baseline justify-between gap-[8px] mb-[8px]`}><h4>{text("Progress", "执行进展")}</h4></div>
            <ol className={styles.eventList}>{activityRows(events, text).slice(0, 8).map((event) => (
              <li key={event.sequence}><span>{event.title}</span>{event.time && <time>{shortTime(event.time * 1000)}</time>}</li>
            ))}</ol>
          </section>}
          {techStrip}

          {dialogs}
        </div>
      </div>
    </section>
  );
}
