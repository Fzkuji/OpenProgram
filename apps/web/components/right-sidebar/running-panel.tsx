"use client";

import { useState, type ReactNode } from "react";
import { useTranslation } from "@/lib/i18n";
import { useExecutionDebugger } from "@/lib/use-execution-debugger";
import { useManagedProcesses } from "@/lib/use-managed-processes";
import { processIsActive, stopProcess, type ManagedProcess } from "@/lib/net/process-client";
import type { ExecutionSnapshot } from "@/lib/execution-debugger";
import { Button } from "@/components/ui/button";
import { DebuggerPanel } from "./debugger-panel";
import { SidebarNotice } from "./sidebar-notice";
import { executionTitle, statusLabel, updatedTime } from "./debugger-presentation";
import styles from "./running-panel.module.css";

const terminal = new Set(["completed", "failed", "cancelled", "interrupted"]);

/** One conversation-owned list; inspecting a row reuses the existing controls. */
export function RunningPanel({ active, sessionId }: { active: boolean; sessionId: string | null }) {
  const { text } = useTranslation();
  const state = useExecutionDebugger(active, sessionId);
  const [selection, setSelection] = useState<"agent" | string | null>(null);
  const [activeOnly, setActiveOnly] = useState(false);
  const [stopPending, setStopPending] = useState(false);
  const [stopError, setStopError] = useState<string | null>(null);
  const processes = useManagedProcesses(active, sessionId, selection && selection !== "agent" ? selection : null);
  const processStatus = (item: ManagedProcess) => text(...({
    starting: ["Starting", "正在启动"], running: ["Running", "正在运行"], stopping: ["Stopping", "正在停止"],
    completed: ["Completed", "已完成"], exited: ["Exited", "已退出"], failed: ["Failed", "运行失败"],
    stopped: ["Stopped", "已停止"], interrupted: ["Interrupted", "执行中断"], unknown: ["Status needs confirmation", "状态待确认"], lost: ["Status needs confirmation", "状态待确认"],
  }[item.status] as [string, string] || [item.status, item.status]));
  const stale = processes.stale || state.connection.state !== "connected";
  const refresh = () => { state.refresh(); processes.refresh(); };
  const byExecution = new Map<string, ManagedProcess[]>();
  const unassigned: ManagedProcess[] = [];
  const ids = new Set(state.executions.map(item => item.execution_id));
  for (const item of processes.items) {
    if (item.execution_id && ids.has(item.execution_id)) {
      byExecution.set(item.execution_id, [...(byExecution.get(item.execution_id) || []), item]);
    } else unassigned.push(item);
  }
  const children = new Map<string | null, ExecutionSnapshot[]>();
  for (const item of state.executions) {
    const parentId = item.view_parent_execution_id ?? item.parent_execution_id;
    const parent = parentId && ids.has(parentId) ? parentId : null;
    children.set(parent, [...(children.get(parent) || []), item]);
  }
  function hasActive(item: ExecutionSnapshot, seen = new Set<string>()): boolean {
    if (seen.has(item.execution_id)) return false;
    seen.add(item.execution_id);
    return !terminal.has(item.status) || (byExecution.get(item.execution_id) || []).some(processIsActive)
      || (children.get(item.execution_id) || []).some(child => hasActive(child, seen));
  }
  function programRow(item: ManagedProcess): ReactNode {
    if (activeOnly && !processIsActive(item)) return null;
    return <button type="button" key={`process:${item.id}`} className={styles.row} onClick={() => { setSelection(item.id); setStopError(null); }}>
      <span className={styles.symbol} aria-hidden="true">›_</span>
      <span className={styles.rowText}><span className={styles.name}>{item.command}</span>
        <span className={styles.meta}>{processStatus(item)}{item.exit_code != null ? ` · ${text("Exit", "退出码")} ${item.exit_code}` : ""}</span>
      </span>
    </button>;
  }
  const rendered = new Set<string>();
  function agentRows(parent: string | null, depth = 0): ReactNode {
    return (children.get(parent) || []).map(item => {
      if (rendered.has(item.execution_id) || (activeOnly && !hasActive(item))) return null;
      rendered.add(item.execution_id);
      const owned = byExecution.get(item.execution_id) || [];
      return <div className={styles.branch} key={item.execution_id}>
        <button type="button" className={styles.row} onClick={() => { state.selectExecution(item.execution_id); setSelection("agent"); }}>
          <span className={`${styles.dot} ${!terminal.has(item.status) ? styles.active : ""}`} aria-hidden="true" />
          <span className={styles.rowText}>
            <span className={styles.name}>{executionTitle(item, state.executions.length - state.executions.indexOf(item), text)}</span>
            <span className={styles.meta}>{statusLabel(item.status, text)}{owned.length ? ` · ${owned.filter(processIsActive).length}/${owned.length} ${text("programs running", "程序运行中")}` : ""}</span>
          </span>
        </button>
        {(owned.length > 0 || children.has(item.execution_id)) && <div className={depth < 6 ? styles.children : undefined}>
          {owned.map(programRow)}{agentRows(item.execution_id, depth + 1)}
        </div>}
      </div>;
    });
  }
  if (!sessionId) return <SidebarNotice>{text("Open a conversation to view its Agents and programs.", "打开一个会话，查看其中的 Agent 和程序。")}</SidebarNotice>;
  const back = <Button variant="ghost" onClick={() => setSelection(null)}>{text("← All activity", "← 全部运行记录")}</Button>;
  if (selection === "agent") return <div className={styles.panel}>
    <div className={styles.toolbar}>{back}</div>
    <DebuggerPanel key={state.selectedExecutionId || "empty"} {...state} detailOnly
      onSelectExecution={state.selectExecution} onCommand={state.command} onRespondWait={state.respondWait}
      onCreateDraft={async input => { await state.createDraft(input); }} onUpdateDraft={state.updateDraft}
      onDraftAction={state.draftAction} onRefresh={refresh} />
  </div>;
  if (selection) {
    const item = processes.detail?.process.id === selection ? processes.detail.process : null;
    return <div className={styles.panel}>
      <div className={styles.toolbar}>{back}<Button variant="ghost" onClick={processes.refresh}>{text("Refresh", "刷新")}</Button></div>
      {processes.stale && <SidebarNotice>{text("Could not refresh this program. Showing the last saved result.", "无法刷新此程序，当前显示上次读取的记录。")}</SidebarNotice>}
      {!item ? <SidebarNotice>{processes.stale ? text("Program details unavailable.", "暂时无法读取程序详情。") : text("Loading program…", "正在读取程序…")}</SidebarNotice> : <div className={styles.scroll}>
        <h3 className={styles.title}>{item.command}</h3><p className={styles.meta}>{processStatus(item)}</p>{item.status === "unknown" && <p className={styles.notice}>{text("The process supervisor is unavailable. This program is not confirmed to have exited; its record is retained.", "程序监督进程不可用，尚不能确认程序已退出，记录仍然保留。")}</p>}
        <dl className={styles.facts}>
          <dt>{text("Working directory", "工作目录")}</dt><dd>{item.cwd || "—"}</dd>
          <dt>{text("Started", "开始时间")}</dt><dd>{updatedTime(item.started_at)}</dd>
          {item.ended_at != null && <><dt>{text("Ended", "结束时间")}</dt><dd>{updatedTime(item.ended_at)}</dd></>}
          {item.exit_code != null && <><dt>{text("Exit code", "退出码")}</dt><dd>{item.exit_code}</dd></>}
          <dt>{text("Environment", "运行环境")}</dt><dd>{item.backend_id}</dd>
        </dl>
        {processIsActive(item) && <Button variant="destructive" disabled={stopPending || processes.stale || item.can_stop === false} onClick={async () => {
          setStopPending(true); setStopError(null);
          try { await stopProcess(item.id, sessionId); processes.refresh(); }
          catch { setStopError(text("Could not stop the program. Refresh its status and try again.", "未能停止程序，请刷新状态后重试。")); }
          finally { setStopPending(false); }
        }}>{stopPending ? text("Stopping…", "正在停止…") : text("Stop program", "停止程序")}</Button>}
        {stopError && <p role="alert" className={styles.error}>{stopError}</p>}
        <h4 className={styles.outputHeading}>{text("Output", "输出")}</h4>
        {item.truncated && <p className={styles.meta}>{text("Earlier output was truncated; the process record is retained.", "较早的输出已截断，程序记录仍然保留。")}</p>}
        <pre className={styles.output}>{processes.detail?.output || text("No output recorded yet.", "尚无输出记录。")}</pre>
      </div>}
    </div>;
  }
  const visibleAgents = state.executions.filter(item => !activeOnly || hasActive(item));
  const visibleOther = unassigned.filter(item => !activeOnly || processIsActive(item));
  return <section className={styles.panel} aria-label={text("Conversation activity", "会话运行记录")}>
    <div className={styles.toolbar}>
      <Button variant="ghost" aria-pressed={!activeOnly} onClick={() => setActiveOnly(false)}>{text("All", "全部")}</Button>
      <Button variant="ghost" aria-pressed={activeOnly} onClick={() => setActiveOnly(true)}>{text("Active", "运行中")}</Button>
      <Button variant="ghost" onClick={refresh}>{text("Refresh", "刷新")}</Button>
    </div>
    <p className={styles.summary}>{text("This conversation", "当前会话")} · {state.executions.length} {text("executions", "执行")} · {processes.items.length} {text("programs", "程序")}</p>
    {stale && (state.fetchedAt || processes.loaded || processes.stale) && <p role="status" className={styles.notice}>{text("Some statuses could not be refreshed. Showing the last saved records.", "部分状态暂时无法刷新，当前显示上次读取的记录。")}</p>}
    <div className={styles.scroll}>
      {!state.fetchedAt && !processes.loaded && !processes.stale ? <SidebarNotice>{text("Loading…", "加载中…")}</SidebarNotice> : null}
      {agentRows(null)}
      {visibleOther.length > 0 && <><h3 className={styles.outputHeading}>{text("Other programs in this conversation", "此会话的其他程序")}</h3>{visibleOther.map(programRow)}</>}
      {state.fetchedAt && processes.loaded && visibleAgents.length === 0 && visibleOther.length === 0 && <SidebarNotice>{activeOnly ? text("Nothing is active. Completed records remain under All.", "当前没有运行项，已结束的记录保留在“全部”中。") : text("Agents and their programs will appear here when this conversation runs.", "此会话开始执行后，Agent 及其程序会显示在这里。")}</SidebarNotice>}
    </div>
  </section>;
}
