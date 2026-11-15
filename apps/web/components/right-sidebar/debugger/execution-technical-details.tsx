"use client";

/** What an execution's Technical details hold: why it stands where it does,
 *  the policy for code on resume, ids, the last raw events, the checkpoint,
 *  the resource wait and the effects. */
import type { CommandResult, ExecutionSnapshot } from "@/lib/execution/execution-debugger";
import type { PersistedExecutionEvent, UnresolvedEffect } from "@/lib/net/execution/execution-client";
import { SimpleSelect } from "@/components/ui/select";
import { useTranslation } from "@/lib/i18n";
import type { CheckpointInspector, DebuggerConnection } from "./debugger-panel";
import { executionGuidance, executionNeedsAttention } from "./debugger-presentation";
import styles from "./debugger-panel.module.css";

function shortId(value: string | null | undefined): string {
  if (!value) return "—";
  return value.length > 16 ? `${value.slice(0, 8)}…${value.slice(-6)}` : value;
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

/** The guidance sentence, or the unconfirmed effects that hold the run. */
export function ExecutionStandingNote({ snapshot, unresolvedEffects }: { snapshot: ExecutionSnapshot; unresolvedEffects: UnresolvedEffect[] }) {
  const { text } = useTranslation();
  if (snapshot.status === "reconciliation_required" && executionNeedsAttention(snapshot)) {
    return <div className={styles.reason}>
      {unresolvedEffects.some((effect) => effect.kind === "provider.before")
        ? text("The model request has no confirmed response. This run is waiting for its result to be resolved.", "模型请求尚无已确认的响应，本次执行正在等待结果核对。")
        : unresolvedEffects.some((effect) => effect.kind === "tool.before")
          ? text("A tool result is unconfirmed. Verify the external action before starting it again.", "工具结果尚未确认。再次执行前需要核对外部操作的实际结果。")
          : text("An external action has an unconfirmed outcome. This run needs attention before it can continue.", "外部操作结果尚未确认，需要处理后才能继续执行。")}
      {unresolvedEffects.filter((effect) => effect.tool_name).map((effect) => <div key={effect.effect_id}>{effect.tool_name}</div>)}
    </div>;
  }
  const guidance = executionGuidance(snapshot, text);
  return guidance ? <p className={styles.muted}>{guidance}</p> : null;
}

export function ExecutionCodePolicySelect({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  const { text } = useTranslation();
  return <label>
    {text("Function code after restart", "重启后函数代码")}
    <SimpleSelect aria-label={text("Function code after restart", "重启后函数代码")} value={value} onValueChange={onChange} options={[
      { value: "", label: text("Keep task policy", "沿用任务设置") },
      { value: "keep_original", label: text("Continue original code", "继续原代码") },
      { value: "use_latest", label: text("Use new code with saved results", "保留已有结果，使用新代码") },
    ]} />
  </label>;
}

export function ExecutionTechnicalDetails({ snapshot, events, connection, commandResults, checkpoint, identity = true, eventListClassName }: {
  snapshot: ExecutionSnapshot;
  events: PersistedExecutionEvent[];
  connection: Pick<DebuggerConnection, "cursor">;
  commandResults: Record<string, CommandResult>;
  checkpoint: CheckpointInspector | undefined;
  /** False when the page lists the ids itself. */
  identity?: boolean;
  eventListClassName?: string;
}) {
  return <>
    {Object.values(commandResults).map((result) => <p key={result.command_id}>{result.command_id} · {result.status}{result.rejection_code ? ` · ${result.rejection_code}` : ""}</p>)}
    {identity && <dl className={`${styles.definitionList} grid gap-[6px] m-[0]`}>
      <div><dt>Execution ID</dt><dd>{snapshot.execution_id}</dd></div>
      <div><dt>Run ID</dt><dd>{snapshot.run_id}</dd></div>
      <div><dt>Revision</dt><dd>{snapshot.revision_id}</dd></div>
      <div><dt>State version</dt><dd>{snapshot.status_version}</dd></div>
      {connection.cursor && <div><dt>Event cursor</dt><dd>{connection.cursor.next_sequence}</dd></div>}
      {snapshot.reason_code && <div><dt>Reason code</dt><dd>{snapshot.reason_code}</dd></div>}
    </dl>}
    <ol className={`${styles.eventList}${eventListClassName ? ` ${eventListClassName}` : ""}`}>{events.slice(-50).reverse().map((event) => (
      <li key={event.sequence}><span>#{event.sequence}</span><span>{event.kind}<small className={styles.eventDetail}>{eventSummary(event)}</small></span>{event.execution_version != null && <span>v{event.execution_version}</span>}</li>
    ))}</ol>
    {checkpoint && <section className={styles.card}>
      <div className={`${styles.cardHeader} flex flex-wrap items-baseline justify-between gap-[8px] mb-[8px]`}><h4>Checkpoint inspector</h4><span>Published</span></div>
      <div className={styles.inspectorGrid}>
        <div><span>ID</span><code>{checkpoint.checkpoint_id}</code></div>
        <div><span>Revision</span><code>{checkpoint.revision_id}</code></div>
        <div><span>Status version</span><code>{checkpoint.status_version}</code></div>
        <div><span>Parent</span><code>{shortId(checkpoint.parent_checkpoint_id)}</code></div>
        <div className={styles.inspectorWide}><span>Frontier</span><div className={styles.frontier}>{(checkpoint.frontier || []).map((item) => <span key={item.step_id} className={styles.frontierItem}>{item.step_id}<small>{item.status}</small></span>)}</div></div>
        <div className={styles.inspectorWide}><span>Effect receipts</span><div className={styles.receipts}>{(checkpoint.effect_receipts || []).map((item) => <span key={item.effect_id}>{item.effect_id} · {item.status}</span>)}</div></div>
      </div>
    </section>}

    {(snapshot.resource || Object.keys(snapshot.effect_summary).length > 0) && <div className={`${styles.twoColumn} grid grid-cols-[repeat(2,_minmax(0,_1fr))] gap-[16px]`}>
      {snapshot.resource && <section className={styles.card}>
        <div className={`${styles.cardHeader} flex flex-wrap items-baseline justify-between gap-[8px] mb-[8px]`}><h4>Resource wait</h4><span>{snapshot.resource?.resource_state ? String(snapshot.resource.resource_state) : "unavailable"}</span></div>
        <ResourceSummary resource={snapshot.resource} />
      </section>}
      {Object.keys(snapshot.effect_summary).length > 0 && <section className={styles.card}>
        <div className={`${styles.cardHeader} flex flex-wrap items-baseline justify-between gap-[8px] mb-[8px]`}><h4>Effects</h4><span>{formatUnknown(snapshot.effect_summary.unresolved)} unresolved</span></div>
        <dl className={`${styles.definitionList} grid gap-[6px] m-[0]`}>
          {Object.entries(snapshot.effect_summary).map(([key, value]) => <div key={key}><dt>{key.replaceAll("_", " ")}</dt><dd>{formatUnknown(value)}</dd></div>)}
        </dl>
      </section>}
    </div>}
  </>;
}
