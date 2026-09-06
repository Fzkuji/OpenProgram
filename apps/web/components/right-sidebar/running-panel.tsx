"use client";

import { useState, type ReactNode } from "react";
import { useTranslation } from "@/lib/i18n";
import { useExecutionDebugger } from "@/lib/use-execution-debugger";
import { useManagedProcesses } from "@/lib/use-managed-processes";
import { processIsActive, stopProcess, type ManagedProcess } from "@/lib/net/process-client";
import type { ExecutionSnapshot } from "@/lib/execution-debugger";
import { ChevronRight, Bot, Terminal } from "lucide-react";
import { SectionHeader } from "@/components/sidebar/section-header";
import { Button } from "@/components/ui/button";
import { DebuggerPanel } from "./debugger-panel";
import { ActivityRefreshButton } from "./activity-refresh-button";
import { SidebarNotice } from "./sidebar-notice";
import { executionTitle, executionRequest, statusLabel, shortTime, updatedTime } from "./debugger-presentation";
import styles from "./running-panel.module.css";


/** One conversation-owned list; inspecting a row reuses the existing controls. */
export function RunningPanel({ active, sessionId }: { active: boolean; sessionId: string | null }) {
  const { text } = useTranslation();
  const state = useExecutionDebugger(active, sessionId);
  const [selection, setSelection] = useState<"agent" | string | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const toggle = (id: string) => setExpanded(previous => {
    const next = new Set(previous);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });
  const [stopPending, setStopPending] = useState(false);
  const [stopError, setStopError] = useState<string | null>(null);
  const processes = useManagedProcesses(active, sessionId, selection && selection !== "agent" ? selection : null);
  const processStatus = (item: ManagedProcess) => text(...({
    starting: ["Starting", "正在启动"], running: ["Running", "正在运行"], stopping: ["Stopping", "正在停止"],
    completed: ["Completed", "已完成"], exited: ["Exited", "已退出"], failed: ["Failed", "运行失败"],
    stopped: ["Stopped", "已停止"], interrupted: ["Interrupted", "执行中断"], unknown: ["Status needs confirmation", "状态待确认"], lost: ["Status needs confirmation", "状态待确认"],
  }[item.status] as [string, string] || [item.status, item.status]));
  const stale = processes.stale || state.connection.state !== "connected";
  const hasRead = Boolean(state.fetchedAt) || processes.loaded;
  const refresh = async () => {
    const results = await Promise.all([state.refresh(), processes.refresh()]);
    return results.every(Boolean);
  };
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
    return ["queued", "running", "pausing", "cancelling"].includes(item.status) || (byExecution.get(item.execution_id) || []).some(processIsActive)
      || (children.get(item.execution_id) || []).some(child => hasActive(child, seen));
  }
  function needsAttention(item: ExecutionSnapshot, seen = new Set<string>()): boolean {
    if (seen.has(item.execution_id)) return false;
    seen.add(item.execution_id);
    return ["paused", "reconciliation_required"].includes(item.status)
      || (byExecution.get(item.execution_id) || []).some(p => ["unknown", "lost"].includes(p.status))
      || (children.get(item.execution_id) || []).some(child => needsAttention(child, seen));
  }
  function programName(item: ManagedProcess): string {
    const siblings = byExecution.get(item.execution_id || "") || unassigned;
    const ordinal = [...siblings].sort((a, b) => a.started_at - b.started_at || a.id.localeCompare(b.id)).findIndex(p => p.id === item.id) + 1;
    const executable = item.command.trim().match(/^(?:[^\s=]+=[^\s]+\s+)*([^\s]+)/)?.[1]?.split("/").pop() || text("Program", "程序");
    const display = item.display;
    const name = display?.kind === "snippet"
      ? `${display.name} ${text("snippet", "代码片段")}`
      : display?.name || executable || text("Program", "程序");
    return `${name} · ${ordinal}`;
  }
  function programRow(item: ManagedProcess): ReactNode {
    return <Button variant="ghost" key={`process:${item.id}`} className={styles.row} onClick={() => { setSelection(item.id); setStopError(null); }}>
      <Terminal size={16} className={styles.symbol} aria-hidden="true" />
      <span className={styles.rowText}><span className={styles.name}>{programName(item)}</span>
        <span className={styles.meta}>{processStatus(item)}</span>
      </span>
    </Button>;
  }
  function agentRow(item: ExecutionSnapshot, ancestors = new Set<string>()): ReactNode {
    if (ancestors.has(item.execution_id)) return null;
    const path = new Set(ancestors).add(item.execution_id);
    const owned = byExecution.get(item.execution_id) || [];
    const descendants = children.get(item.execution_id) || [];
    const expandable = owned.length > 0 || descendants.length > 0;
    const open = expanded.has(item.execution_id);
    const title = executionTitle(item, state.executions.length - state.executions.indexOf(item), text);
    return <div className={styles.branch} key={item.execution_id}>
      <div className={styles.agentRow}>
        <Button variant="ghost" className={styles.row} onClick={() => { state.selectExecution(item.execution_id); setSelection("agent"); }}>
          <Bot size={16} className={styles.symbol} aria-hidden="true" />
          <span className={styles.rowText}>
            <span className={styles.rowHeading}><span className={styles.name} title={executionRequest(item) || title}>{title}</span>
              {item.started_at != null && <time className={styles.started} dateTime={new Date(item.started_at * 1000).toISOString()} title={updatedTime(item.started_at)} aria-label={updatedTime(item.started_at)}>{shortTime(item.started_at * 1000)}</time>}</span>
            <span className={styles.meta}>{statusLabel(item.status, text)}{descendants.length ? ` · ${descendants.length} ${text("branches", "分支")}` : ""}{owned.length ? ` · ${owned.length} ${text("programs", "程序")}${owned.some(processIsActive) ? ` (${owned.filter(processIsActive).length} ${text("active", "活动中")})` : ""}` : ""}{!["paused", "reconciliation_required"].includes(item.status) && needsAttention(item) ? ` · ${text("Child needs attention", "子项需要处理")}` : ""}</span>
          </span>
        </Button>
        {expandable && <Button variant="ghost" size="icon" className={styles.expand} aria-expanded={open}
          aria-label={`${open ? text("Collapse", "折叠") : text("Expand", "展开")} ${title}`} onClick={() => toggle(item.execution_id)}>
          <ChevronRight size={16} style={{ transform: open ? "rotate(90deg)" : undefined }} />
        </Button>}
      </div>
      {expandable && open && <div className={ancestors.size < 4 ? styles.children : undefined}>
        {descendants.map(child => agentRow(child, path))}{owned.map(programRow)}
      </div>}
    </div>;
  }
  if (!sessionId) return <SidebarNotice>{text("Select or start a conversation to view its Agents and programs.", "选择或开始一个会话，查看其中的 Agent 和程序。")}</SidebarNotice>;
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
      <div className={styles.toolbar}>{back}<ActivityRefreshButton key={selection} onRefresh={processes.refresh} label={text("Refresh program", "刷新程序")} /></div>
      {processes.stale && item && <SidebarNotice>{text("Could not refresh this program. Showing the last saved result.", "无法刷新此程序，当前显示上次读取的记录。")}</SidebarNotice>}
      {!item ? <SidebarNotice>{processes.stale ? text("Program details unavailable.", "暂时无法读取程序详情。") : text("Loading program…", "正在读取程序…")}</SidebarNotice> : <div className={styles.scroll}>
        <h3 className={styles.title}>{programName(item)}</h3><p className={styles.meta}>{processStatus(item)}</p>{item.status === "unknown" && <p className={styles.notice}>{text("The process supervisor is unavailable. This program is not confirmed to have exited; its record is retained.", "程序监督进程不可用，尚不能确认程序已退出，记录仍然保留。")}</p>}
        {item.execution_id && ids.has(item.execution_id) && <Button variant="ghost" className={styles.row} onClick={() => { state.selectExecution(item.execution_id!); setSelection("agent"); }}>
          <Bot size={16} aria-hidden="true" /><span className={styles.rowText}><span className={styles.meta}>{text("Started by", "所属 Agent")}</span><span className={styles.name}>{executionTitle(state.executions.find(e => e.execution_id === item.execution_id)!, 1, text)}</span></span>
        </Button>}
        <h4 className={styles.outputHeading}>{text("Command", "命令")}</h4>
        <pre className={styles.output}>{item.command}</pre>
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
  const roots = children.get(null) || [];
  const attention = roots.filter(item => needsAttention(item));
  const working = roots.filter(item => !needsAttention(item) && hasActive(item));
  const history = roots.filter(item => !needsAttention(item) && !hasActive(item));
  const refreshButton = <ActivityRefreshButton className={styles.refresh} onRefresh={refresh} label={text("Refresh activity", "刷新运行记录")} />;
  const firstGroup = attention.length ? attention : working.length ? working : history;
  const section = (name: string, items: ExecutionSnapshot[], historical = false) => items.length > 0 && <div className="group/sec">
    <SectionHeader name={name} className={styles.sectionHeader} collapsible={historical} collapsed={!historyOpen} onToggle={() => setHistoryOpen(value => !value)} actions={<><span className={styles.count}>{items.length}</span>{items === firstGroup && refreshButton}</>} />
    {(!historical || historyOpen) && items.map(item => agentRow(item))}
  </div>;
  return <section className={styles.panel} aria-label={text("Conversation activity", "会话运行记录")}>
    {stale && (hasRead || processes.stale || state.connection.state === "stale") && <p role="status" className={styles.notice}>{hasRead
      ? text("Some statuses could not be refreshed. Showing the last saved records.", "部分状态暂时无法刷新，当前显示上次读取的记录。")
      : text("Could not load activity. Refresh to try again.", "无法读取运行记录，请刷新重试。")}</p>}
    <div className={styles.scroll}>
      {roots.length === 0 && <div className={styles.emptyTools}>{refreshButton}</div>}
      {!hasRead && !processes.stale && state.connection.state === "reconnecting" ? <SidebarNotice>{text("Loading…", "加载中…")}</SidebarNotice> : null}
      {section(text("Needs attention", "需要处理"), attention)}
      {section(text("In progress", "正在进行"), working)}
      {state.fetchedAt && processes.loaded && unassigned.length === 0 && attention.length === 0 && working.length === 0 && <SidebarNotice>{roots.length ? text("No tasks are running. Previous tasks are in History.", "当前没有正在进行的任务，已结束任务保留在历史记录中。") : text("Tasks and their programs will appear here when this conversation runs.", "此会话开始执行后，任务及其程序会显示在这里。")}</SidebarNotice>}
      {section(text("History", "历史记录"), history, true)}
      {unassigned.length > 0 && <div className="group/sec"><SectionHeader name={text("Programs without an Agent record", "未关联 Agent 记录的程序")} collapsible={false} collapsed={false} onToggle={() => {}} />{unassigned.map(programRow)}</div>}
    </div>
  </section>;
}
