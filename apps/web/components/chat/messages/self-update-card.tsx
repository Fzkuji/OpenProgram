"use client";

import { useEffect, useRef, useState } from "react";
import { Square } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ExecutionStrip } from "./execution-strip";
import { ActionButton, SVG } from "./message-actions";
import { showToast } from "@/lib/format-utils/toast";
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
  function editRequest(name: string, retry = false) {
    useSessionStore.getState().openFnForm({
      name,
      category: "tool",
      description: text(
        "Prepare a request in the original conversation draft. Send the draft there for tool authorization and any required approval.",
        "将请求加入原会话草稿。需在那里发送，按权限规则审批并执行。",
      ),
      params_detail: retry ? [{ name: "candidate_sha", label: text("New revision", "新版本"), placeholder: text("40-character commit SHA", "40 位提交 SHA"), type: "str", required: true, description: text("New candidate commit (40-character SHA)", "新候选提交（40 位 SHA）") }] : [],
    }, null, {
      label: text("Prepare request", "准备请求"),
      submit: (kwargs) => {
        const candidate = String(kwargs.candidate_sha ?? "").trim();
        if (retry && !/^[0-9a-f]{40}$/.test(candidate)) return {
          error: text("Enter a complete 40-character commit SHA.", "请输入完整的 40 位提交 SHA。"),
          errorParam: "candidate_sha",
        };
        const call = `${name}(update_id=${JSON.stringify(update.update_id)}${retry ? `, candidate_sha=${JSON.stringify(candidate)}` : ""})`;
        const instruction = text(
          `Please request ${call} for this self-update through the normal tool approval flow.`,
          `请通过正常工具审批流程，为本次自更新请求执行 ${call}。`,
        );
        const store = useSessionStore.getState();
        const draft = store.composerDrafts[update.session_id] ?? "";
        store.setComposerInputFor(update.session_id, draft ? `${draft}\n\n${instruction}` : instruction);
        showToast(text("Request added to the original conversation draft; not submitted.", "请求已加入原会话草稿，尚未发送。"));
      },
    });
  }
  const preInstall = ["preparing", "staging", "ready"].includes(update.phase);
  const canRetry = ["aborted", "rolled_back"].includes(update.phase);
  const canStop = update.iteration && !update.iteration.stopped;
  if (!preInstall && !canRetry && !canStop) return null;
  return <div className="message-actions-footer runtime-actions-footer"><div className="message-actions">
    {canRetry && <ActionButton icon={SVG.pencil} title={text("Edit retry request", "编辑重试请求")} onClick={() => editRequest("self_update_retry", true)} />}
    {preInstall && <ActionButton icon={<Square />} title={text("Prepare cancellation request", "准备取消请求")} onClick={() => editRequest("self_update_cancel")} />}
    {canStop && <ActionButton icon={<Square />} title={text("Prepare stop-iteration request", "准备停止迭代请求")} onClick={() => editRequest("self_update_iteration_cancel")} />}
  </div></div>;
}

export function SelfUpdateCard({ update }: { update: SelfUpdate }) {
  const { text } = useTranslation();
  const runtime = update.last_verified_runtime;
  const iteration = update.iteration;
  return <article className={`runtime-card-host ${styles.card}`} aria-label={text("Self-update", "自更新")} data-update-id={update.update_id} data-phase={update.phase}>
    <ExecutionStrip label={`${text("Self-update", "自更新")} · ${text("Attempt", "尝试")} ${update.attempt} · ${text(...PHASES[update.phase])}`} after={<UpdateRequests key={`${update.session_id}:${update.update_id}`} update={update} />}>
    <dl className={styles.facts}>
      <div><dt>{text("Target revision", "目标版本")}</dt><dd><code title={update.candidate_revision}>{update.candidate_revision.slice(0, 8)}</code></dd></div>
      <div><dt>{text("Verified runtime", "已验证运行版本")}</dt><dd>{runtime ? <code title={runtime.candidate_sha}>{runtime.candidate_sha.slice(0, 8)}</code> : text("Unknown", "未知")}</dd></div>
      <div><dt>{text("Verification", "验证结论")}</dt><dd>{update.verifier_verdict ?? text("Not available", "暂无")}</dd></div>
      <div><dt>{text("Rollback", "回退")}</dt><dd>{update.rollback_available ? text("Available", "可用") : text("Unavailable", "不可用")}</dd></div>
      {update.diagnosis && <div><dt>{text("Diagnosis", "诊断")}</dt><dd>{update.diagnosis.status}</dd></div>}
      {update.source_repair_result && <div><dt>{text("Source repair", "源码修复")}</dt><dd>{update.source_repair_result.status}</dd></div>}
      {iteration && <>
        <div><dt>{text("Remaining attempts", "剩余尝试次数")}</dt><dd>{Math.max(0, iteration.max_attempts - iteration.attempt)}</dd></div>
        <div><dt>{text("Deadline", "截止时间")}</dt><dd>{new Date(iteration.deadline * 1000).toLocaleString()}</dd></div>
        {iteration.stopped && <div><dt>{text("Iteration", "迭代")}</dt><dd>{text("Stopped", "已停止")}</dd></div>}
        {iteration.submission && <div><dt>{text("Submission", "提交状态")}</dt><dd>{iteration.submission.status}</dd></div>}
      </>}
    </dl>
    <ExecutionStrip label={text("Details and evidence", "详情与证据")}>
      <dl className={styles.facts}>
        <div><dt>{text("Full revision", "完整版本")}</dt><dd><code>{update.candidate_revision}</code></dd></div>
        <div><dt>{text("Update ID", "更新 ID")}</dt><dd><code>{update.update_id}</code></dd></div>
        <div><dt>{text("Target app", "目标应用")}</dt><dd>{update.target_app}<span className={styles.annotation}>{text("Installation target, not proof of the running version.", "安装目标，不代表实际运行版本。")}</span></dd></div>
        {runtime && <>
          <div><dt>{text("Runtime revision", "运行版本")}</dt><dd><code>{runtime.candidate_sha}</code></dd></div>
          <div><dt>{text("Verified at", "验证时间")}</dt><dd>{new Date(runtime.verified_at * 1000).toLocaleString()}<span className={styles.annotation}>{text("Historical verification, not a live connection check.", "历史验证，不代表当前仍然在线。")}</span></dd></div>
          <div><dt>{text("Worker PID", "Worker PID")}</dt><dd>{runtime.worker_pid}</dd></div>
          <div><dt>{text("Evidence source", "证据来源")}</dt><dd>{runtime.source}</dd></div>
        </>}
        {update.source_repair_result?.candidate_sha && <div><dt>{text("Repaired revision", "修正版本")}</dt><dd><code>{update.source_repair_result.candidate_sha}</code></dd></div>}
        {iteration?.submission?.child_id && <div><dt>{text("Next update ID", "后继更新 ID")}</dt><dd><code>{iteration.submission.child_id}</code></dd></div>}
        {update.changed_paths.length > 0 && <div><dt>{text("Changed files", "变更文件")}</dt><dd><ul className={styles.paths}>{update.changed_paths.map((path) => <li key={path}>{path}</li>)}</ul></dd></div>}
      </dl>
      {update.verifier ? <>
        <ul>{update.verifier.assertions.map((assertion) => <li key={assertion.id}>
          {assertion.id}: {assertion.status}
          <ul>{assertion.evidence_refs.map((ref) => <li key={ref}><code>{ref}</code></li>)}</ul>
        </li>)}</ul>
        <Evidence key={`${update.session_id}:${update.update_id}:${update.snapshot_id}`} update={update} />
      </> : <dl className={styles.facts}><div><dt>{text("Evidence", "验证证据")}</dt><dd>{text("Not recorded", "暂无记录")}</dd></div></dl>}
    </ExecutionStrip>
    </ExecutionStrip>
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
