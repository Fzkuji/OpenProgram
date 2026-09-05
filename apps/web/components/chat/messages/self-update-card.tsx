"use client";

import { useEffect, useRef, useState } from "react";
import { ChevronRight, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useTranslation } from "@/lib/i18n";
import { jsonFetch } from "@/lib/net/fetch-client";
import type { SelfUpdate } from "@/lib/self-update";
import { groupSelfUpdates } from "@/lib/self-update";
import { useSessionStore } from "@/lib/session-store";
import { useSelfUpdates } from "./use-self-updates";
import styles from "./self-update-card.module.css";

const PHASES = {
  preparing: ["Preparing", "准备中"], staging: ["Building and testing", "构建与测试中"],
  ready: ["Ready", "准备完成"], activating: ["Installing and restarting", "安装与重启中"],
  verifying: ["Verifying", "验证中"], succeeded: ["Update committed", "更新已确认"],
  aborted: ["Aborted before installation", "安装前已终止"], rolled_back: ["Rolled back", "已回退"],
  needs_manual_recovery: ["Manual recovery required", "需要手动恢复"],
} satisfies Record<SelfUpdate["phase"], [string, string]>;

function Evidence({ update }: { update: SelfUpdate }) {
  const { text } = useTranslation();
  const request = useRef<AbortController | null>(null);
  const [body, setBody] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  useEffect(() => () => { request.current?.abort(); request.current = null; }, []);
  async function load() {
    if (request.current || !update.verifier) return;
    const controller = new AbortController();
    request.current = controller;
    setBusy(true);
    setFailed(false);
    const timeout = setTimeout(() => controller.abort(), 15000);
    try {
      const query = new URLSearchParams({ session_id: update.session_id, evidence_id: update.verifier.evidence_id });
      const value = await jsonFetch<unknown>(`/api/self-updates/${encodeURIComponent(update.update_id)}/evidence?${query}`, { signal: controller.signal, cache: "no-store" });
      if (!controller.signal.aborted) setBody(value);
    } catch {
      if (request.current === controller) setFailed(true);
    } finally {
      clearTimeout(timeout);
      if (request.current === controller) {
        request.current = null;
        setBusy(false);
      }
    }
  }
  return <>
    <Button type="button" disabled={busy} onClick={load}>
      {busy ? text("Loading evidence…", "加载证据中…") : text("Load evidence", "加载证据")}
    </Button>
    {failed && <p role="status">{text("Evidence unavailable. Retry when connected.", "证据不可用。连接恢复后可重试。")}</p>}
    {body !== null && <>
      <p className={styles.note}>{text("Historical observations; HTTP or HTML content does not prove the rendered UI.", "历史观测；HTTP 或 HTML 内容不能证明实际界面。")}</p>
      <pre>{JSON.stringify(body, null, 2)}</pre>
    </>}
  </>;
}

function UpdateRequests({ update }: { update: SelfUpdate }) {
  const { text } = useTranslation();
  const [candidate, setCandidate] = useState("");
  const [prepared, setPrepared] = useState(false);
  const prepare = (call: string) => {
    const store = useSessionStore.getState();
    const draft = store.composerDrafts[update.session_id] ?? "";
    const instruction = text(
      `Please request ${call} for this self-update through the normal tool approval flow.`,
      `请通过正常工具审批流程，为本次自更新请求执行 ${call}。`,
    );
    store.setComposerInputFor(update.session_id, draft ? `${draft}\n\n${instruction}` : instruction);
    setPrepared(true);
  };
  const preInstall = ["preparing", "staging", "ready"].includes(update.phase);
  const canRetry = ["aborted", "rolled_back"].includes(update.phase);
  const iteration = update.iteration;
  if (!preInstall && !canRetry && (!iteration || iteration.stopped)) return null;
  return <details className={styles.section}>
    <summary><ChevronRight size={14} aria-hidden="true" />{text("Update actions", "更新操作")}</summary>
    <p className={styles.note}>{text("These buttons add a request to the original conversation draft. Send it there; tool authorization and any required approval still apply. Nothing runs on click.", "这些按钮只向原会话草稿添加请求。需在原会话发送；工具鉴权及所需审批仍然适用。点击不会执行操作。")}</p>
    <div className={styles.actions}>
      {preInstall && <Button type="button" onClick={() => prepare(`self_update_cancel(update_id=${JSON.stringify(update.update_id)})`)}>{text("Prepare cancellation request", "准备取消请求")}</Button>}
      {iteration && !iteration.stopped && <Button type="button" onClick={() => prepare(`self_update_iteration_cancel(update_id=${JSON.stringify(update.update_id)})`)}>{text("Prepare stop-iteration request", "准备停止迭代请求")}</Button>}
    </div>
    {canRetry && <div className={styles.actions}>
      <label>{text("New candidate commit (40-character SHA)", "新候选提交（40 位 SHA）")}
        <Input aria-label={text("New candidate commit", "新候选提交")} value={candidate} onChange={(event) => setCandidate(event.target.value)} spellCheck={false} />
      </label>
      <Button type="button" disabled={!/^[0-9a-f]{40}$/.test(candidate)} onClick={() => prepare(`self_update_retry(update_id=${JSON.stringify(update.update_id)}, candidate_sha=${JSON.stringify(candidate)})`)}>{text("Prepare retry request", "准备重试请求")}</Button>
    </div>}
    {prepared && <p role="status">{text("Request added to the original conversation draft; not submitted.", "请求已加入原会话草稿，尚未发送。")}</p>}
  </details>;
}

export function SelfUpdateCard({ update }: { update: SelfUpdate }) {
  const { text } = useTranslation();
  const runtime = update.last_verified_runtime;
  const iteration = update.iteration;
  return <article className={styles.card} aria-label={text("Self-update", "自更新")} data-update-id={update.update_id} data-phase={update.phase}>
    <details className={styles.disclosure}>
    <summary className={styles.header}>
      <RefreshCw size={16} className={styles.icon} aria-hidden="true" />
      <span className={styles.heading}><strong>{text("Self-update", "自更新")}</strong><span className={styles.note}>{text("Attempt", "尝试")} {update.attempt}</span></span>
      <span className={styles.status}><span className={styles.dot} aria-hidden="true" />{text(...PHASES[update.phase])}</span>
      <ChevronRight size={14} className={styles.chevron} aria-hidden="true" />
    </summary>
    <div className={styles.body}>
    <dl className={styles.versions}>
      <div><dt>{text("Target revision", "目标版本")}</dt><dd><code title={update.candidate_revision}>{update.candidate_revision.slice(0, 8)}</code></dd></div>
      <div><dt>{text("Last verified runtime", "最近已验证运行版本")}</dt><dd>
        {runtime ? <><code title={runtime.candidate_sha}>{runtime.candidate_sha.slice(0, 8)}</code><div>{new Date(runtime.verified_at * 1000).toLocaleString()} · PID {runtime.worker_pid} · {runtime.source}</div></> : text("Unknown", "未知")}
      </dd></div>
    </dl>
    {runtime && <p className={styles.note}>{text("Prior verification does not confirm the worker is still online.", "先前验证不代表 worker 当前仍在线。")}</p>}
    <p>{text("Verifier verdict", "验证结论")}: {update.verifier_verdict ?? text("Not available", "暂无")}</p>
    <p>{text("Rollback available", "可回退")}: {update.rollback_available ? text("Yes", "是") : text("No", "否")}</p>
    {update.diagnosis && <p>{text("Diagnosis", "诊断")}: {update.diagnosis.status}</p>}
    {update.source_repair_result && <p>{text("Source repair", "源码修复")}: {update.source_repair_result.status}</p>}
    {update.source_repair_result?.candidate_sha && <p>{text("Repaired candidate revision", "修正候选版本")}: <code>{update.source_repair_result.candidate_sha}</code></p>}
    {iteration && <p>
      {text("Remaining attempts", "剩余尝试次数")}: {Math.max(0, iteration.max_attempts - iteration.attempt)} · {text("Deadline", "截止时间")}: {new Date(iteration.deadline * 1000).toLocaleString()}
      {iteration.stopped && <> · {text("Iteration stopped", "迭代已停止")}</>}
      {iteration.submission && <> · {text("Submission", "提交状态")}: {iteration.submission.status}</>}
    </p>}
    {iteration?.submission?.child_id && <p>{text("Next update ID", "后继更新 ID")}: {iteration.submission.child_id}</p>}
    <details className={styles.section}>
      <summary><ChevronRight size={14} aria-hidden="true" />{text("Details and evidence", "详情与证据")}</summary>
      <div className={styles.sectionBody}>
      <p>{text("Target revision", "目标版本")}: <code>{update.candidate_revision}</code></p>
      {runtime && <p>{text("Last verified runtime", "最近已验证运行版本")}: <code>{runtime.candidate_sha}</code></p>}
      <p>{text("Update ID", "更新 ID")}: {update.update_id}</p>
      <p>{text("Target app (not runtime proof)", "目标应用（不是运行证明）")}: {update.target_app}</p>
      <ul>{update.changed_paths.map((path) => <li key={path}>{path}</li>)}</ul>
      {update.verifier ? <>
        <ul>{update.verifier.assertions.map((assertion) => <li key={assertion.id}>
          {assertion.id}: {assertion.status}
          <ul>{assertion.evidence_refs.map((ref) => <li key={ref}><code>{ref}</code></li>)}</ul>
        </li>)}</ul>
        <Evidence key={`${update.session_id}:${update.update_id}:${update.snapshot_id}`} update={update} />
      </> : <p>{text("No verifier evidence is available.", "暂无验证证据。")}</p>}
      </div>
    </details>
    <UpdateRequests key={`${update.session_id}:${update.update_id}`} update={update} />
    </div>
    </details>
    {update.phase === "needs_manual_recovery" && <p className={styles.warning} role="status">{text("Automatic recovery did not complete. Inspect recovery evidence before another update.", "自动恢复未完成。再次更新前请检查恢复证据。")}</p>}
  </article>;
}

export function SelfUpdateHistory({ sessionId }: { sessionId: string | null }) {
  const { text } = useTranslation();
  const history = useSelfUpdates(sessionId);
  if (!sessionId) return null;
  if (history.loaded && !history.items.length && !history.stale) return null;
  return <section aria-label={text("Self-update history", "自更新历史")} className={styles.history}>
    {!history.loaded && !history.stale && <p>{text("Loading update history…", "加载更新历史中…")}</p>}
    {history.stale && <p role="status">
      {text("Update status unavailable. Displayed results may be stale; reconnecting automatically.", "更新状态不可用。显示的结果可能已过时；正在自动重连。")}
      {history.syncedAt !== null && <> {text("Last sync", "最近同步")}: {new Date(history.syncedAt).toLocaleString()}</>}
    </p>}
    {groupSelfUpdates(history.items).map((group) => <div key={group[0].root_id} data-update-root={group[0].root_id}>
      {group.map((item) => <SelfUpdateCard key={item.update_id} update={item} />)}
    </div>)}
    {history.cursor && <Button type="button" disabled={history.busy} onClick={history.loadMore}>{text("Load older updates", "加载更早更新")}</Button>}
  </section>;
}
