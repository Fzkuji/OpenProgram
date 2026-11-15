"use client";

import { useEffect, useState } from "react";
import { Box, ExternalLink, Globe, Monitor, PictureInPicture2, Server, TerminalSquare, X } from "lucide-react";
import { SectionHeader } from "@/components/sidebar/sessions/section-header";
import { useWebTabPip } from "@/lib/browser/web-tab/web-tab-pip-store";
import { useCenterTabs } from "@/lib/tabs/center-tabs-store";
import {
  existingResourceTabId,
  getPreviewPreference,
  groupSessionResources,
  hideResourcePreview,
  optimisticallyCloseBrowserResource,
  previewTabId,
  requestCloseBrowserResource,
  resourceDisplayTitle,
  resourceIsOperating,
  resourceSessionId,
  previewResource,
  sessionResourceRows,
  terminalResourceRows,
  backgroundTerminalRows,
  useBrowserResourceStore,
  type SessionResource,
} from "@/lib/chat/resources/session-resources";
import {
  pendingCloseRequest,
} from "@/lib/browser/control/browser-control";
import { revealExistingWebTab } from "@/lib/browser/web-tab/web-page-management";
import { desktopBridge, retryRestoreWebTab } from "@/lib/desktop/desktop-bridge";
import { useSessionResources } from "@/lib/chat/resources/use-session-resources";
import { useTranslation } from "@/lib/i18n";
import { useTerminalResources } from "@/lib/desktop/terminal-resources";
import { BackgroundTerminalView, TerminalResourceView } from "./terminal-resources";
import { useManagedProcesses } from "@/lib/execution/use-managed-processes";
import { stopProcess } from "@/lib/net/execution/process-client";
import { ApplicationResourceView } from "./application-resource";
import { useResourceSelection } from "@/lib/framework/resource-selection";
import { pruneWebResourceThumbnails, refreshWebResourceThumbnail, useWebResourceThumbnails, watchWebResourceThumbnails } from "@/lib/browser/web-tab/web-resource-thumbnails";
import styles from "./session-resources.module.css";
import { Button } from "@/components/ui/button";

function retryRecoverablePage(row: SessionResource, tabs: { id: string }[]): void {
  if (row.status !== "unknown" && row.status !== "restore_failed") return;
  if (row.kind !== "web" && row.source !== "browser") return;
  const tabId = existingResourceTabId(row, tabs);
  if (!tabId) return;
  const bridge = desktopBridge();
  if (!bridge) return;
  void retryRestoreWebTab(bridge, tabId).catch(() => undefined);
}

/** `active` is false while the panel is mounted but not shown (the right
 *  panel collapsed, or the window hidden): the list keeps what it shows,
 *  and nothing in it polls, streams or takes pictures until it is shown
 *  again. */
export function SessionResourcesPanel({ sessionId: scope, active = true }: { sessionId?: string | null; active?: boolean } = {}) {
  const sessionId = useCenterTabs(s => scope !== undefined ? scope
    : resourceSessionId(s.tabs.find(tab => tab.id === s.activeId)));
  return <SessionResourceList key={sessionId || "no-session"} sessionId={sessionId} active={active} />;
}

function ownerTabIdFor(sessionId: string | null): string | null {
  if (!sessionId) return null;
  const tab = useCenterTabs.getState().tabs.find(item => item.kind === "session" && item.sessionId === sessionId);
  return tab?.id || null;
}

function bindPreview(sessionId: string, row: SessionResource | undefined, hidden: boolean) {
  const ownerTabId = ownerTabIdFor(sessionId);
  const tabId = previewTabId(row);
  if (hidden || !tabId || !ownerTabId) {
    if (hidden) useWebTabPip.getState().hide();
    return;
  }
  useWebTabPip.getState().show(tabId, ownerTabId);
}

function previewInConversation(sessionId: string, row: SessionResource) {
  const ownerTabId = ownerTabIdFor(sessionId);
  const tabs = useCenterTabs.getState();
  const active = tabs.tabs.find(item => item.id === tabs.activeId);
  if (ownerTabId && active?.kind === "web") tabs.setActive(ownerTabId);
  bindPreview(sessionId, row, false);
}

const DEFAULT_GROUPS = ["web", "application", "terminal"];

function SessionResourceList({ sessionId, active }: { sessionId: string | null; active: boolean }) {
  const { text } = useTranslation();
  const tabs = useCenterTabs(s => s.tabs);
  const backend = useSessionResources(sessionId);
  useBrowserResourceStore(s => s.ingestClock);
  const terminals = useTerminalResources(s => s.rows);
  const terminalError = useTerminalResources(s => s.error);
  const pendingClose = pendingCloseRequest();
  const collapseKey = `openprogram.resource-groups:${sessionId || "no-session"}`;
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>(() => {
    try {
      const value = JSON.parse(localStorage.getItem(collapseKey) || "{}");
      return value && typeof value === "object" && !Array.isArray(value) ? value : {};
    } catch { return {}; }
  });
  useEffect(() => {
    try { localStorage.setItem(collapseKey, JSON.stringify(collapsed)); } catch { /* Storage may be unavailable. */ }
  }, [collapseKey, collapsed]);
  const [selected, setSelected] = useState<SessionResource | null>(null);
  const [, render] = useState(0);
  const viewingProcess = selected?.source === "process" ? selected.sourceId : null;
  const programs = useManagedProcesses(active && !!sessionId, sessionId, viewingProcess);
  const rows = sessionResourceRows(tabs, [...backend.rows, ...terminalResourceRows(Object.values(terminals), sessionId),
    ...backgroundTerminalRows(programs.items, sessionId, viewingProcess)], sessionId);
  const selectView = (row: SessionResource) => {
    if (!sessionId) return;
    hideResourcePreview(sessionId, backend.currentBranchId);
    useWebTabPip.getState().hide();
    setSelected(row);
  };
  const requested = useResourceSelection(s => s.id);
  const requestRevision = useResourceSelection(s => s.revision);
  useEffect(() => {
    if (!requested) { setSelected(null); return; }
    const row = rows.find(item => item.id === requested);
    if (row?.source === "browser" || row?.source === "web") {
      setSelected(null);
      if (sessionId) previewInConversation(sessionId, row);
    } else if (row) selectView(row);
    // An explicit command selects the current descriptor once, never a replacement.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [requested, requestRevision]);
  const names: Record<string, string> = {
    application: text("Application", "应用"), terminal: text("Terminal", "终端"), web: text("Webpage", "网页"), docker: text("Container", "容器"), vm: text("VM", "虚拟机"),
    remote: text("Remote environment", "远程环境"), desktop: text("Desktop", "桌面"),
  };
  const viewedBranch = backend.currentBranchId;
  const pref = sessionId ? getPreviewPreference(sessionId, viewedBranch) : null;
  // A group, header included, is listed only when it has something in it.
  const loaded = backend.loaded;
  const groups = groupSessionResources(rows, viewedBranch, sessionId ? DEFAULT_GROUPS : []).filter(group => group.rows.length > 0).map(group => ({
    ...group, title: names[group.key] || text("Other resources", "其他资源"),
  }));
  const icons = { terminal: TerminalSquare, web: Globe, docker: Box, vm: Monitor, remote: Server, desktop: Monitor };
  const thumbnails = useWebResourceThumbnails(s => s.shots);
  // A web row shows its page as it was after the Agent's last operation:
  // a settled operation takes a new picture, one still in flight keeps the
  // previous picture (or takes a first one).
  const thumbnailWork = groups.flatMap(group => group.rows).flatMap(row => {
    const tabId = previewTabId(row);
    if (!tabId || !tabs.some(item => item.id === tabId)) return [];
    // A page still being restored has nothing to show yet; its status
    // change is part of the key, so the picture is taken once it opens.
    if (["restoring", "unknown", "restore_failed"].includes(row.status)) return [];
    const operation = row.lastOperation;
    const operationId = operation && operation.phase !== "dispatched" ? operation.id : thumbnails[tabId]?.operationId ?? null;
    return [[tabId, operationId, row.status] as const];
  });
  const thumbnailKey = thumbnailWork.map(([tabId, operationId, status]) => `${tabId}:${operationId}:${status}`).join("|");
  useEffect(() => {
    if (!active) return;
    const take = () => { for (const [tabId, operationId] of thumbnailWork) refreshWebResourceThumbnail(tabId, operationId); };
    // A page that finishes loading is pictured at once (the watch); an
    // operation is pictured after a short paint delay. A page that is
    // already loaded but has no picture yet is caught by the slow retry.
    const stop = watchWebResourceThumbnails(new Map(thumbnailWork.map(([tabId, operationId]) => [tabId, operationId])));
    const timer = window.setTimeout(take, 150);
    const retry = window.setInterval(take, 4000);
    return () => { stop(); window.clearTimeout(timer); window.clearInterval(retry); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [thumbnailKey, active]);
  // Showing a page (its preview or its tab) means it is loaded: picture it now.
  const pipTabId = useWebTabPip(s => s.tabId);
  const activeTabId = useCenterTabs(s => s.activeId);
  useEffect(() => {
    if (!active) return;
    const shown = thumbnailWork.filter(([tabId]) => tabId === pipTabId || tabId === activeTabId);
    if (!shown.length) return;
    const timer = window.setTimeout(() => {
      for (const [tabId, operationId] of shown) refreshWebResourceThumbnail(tabId, operationId);
    }, 120);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pipTabId, activeTabId, active]);
  useEffect(() => { pruneWebResourceThumbnails(new Set(tabs.map(item => item.id))); }, [tabs]);
  const shotTime = (at: number) => new Date(at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const statusName = (row: SessionResource) => {
    const pageRow = row.kind === "web" || row.source === "browser" || row.source === "web";
    if (pageRow && row.status === "restoring") return text("Restoring page…", "正在恢复网页…");
    if (pageRow && row.status === "restore_failed") return text("Could not restore page", "网页恢复失败");
    return ({
      open: text("Open", "已打开"), idle: text("Idle", "空闲"), in_use: text("In use", "使用中"),
      attached: text("Attached", "已关联"), running: text("Running", "运行中"),
      starting: text("Starting", "启动中"), stopping: text("Stopping", "停止中"),
      exited: text("Exited", "已退出"), closed: text("Closed", "已关闭"), released: text("Released", "已释放"),
      stopped: text("Stopped", "已停止"), failed: text("Failed", "失败"),
      unknown: text("Reconnect", "需要重新连接"),
    } as Record<string, string>)[row.status] || row.status;
  };

  return <section className={styles.panel} aria-label={text("Session resources", "会话资源")}>
    {backend.unavailable && <p role="status" className={styles.notice}>{text("Some resource statuses could not be refreshed.", "部分资源状态未能刷新。")}</p>}
    {pendingClose && <p role="status" className={styles.notice}>
      {pendingClose.error
        ? pendingClose.error === "Stop unconfirmed"
          ? text("Could not pause Agent. The page is still open.", "暂停 Agent 失败，页面仍保持打开。")
          : text("Could not confirm the page status. Check the connection and try again.", "无法确认页面状态，请检查连接后重试。")
        : text("Waiting for Agent to pause before closing the page…", "正在暂停 Agent，完成后关闭页面…")}
    </p>}
    <div className={`${styles.list} flex-1 overflow-y-auto min-h-0`}>
      {!sessionId && <p className={styles.empty}>{text("Select a session to view its resources.", "选择会话以查看其资源。")}</p>}
      {sessionId && loaded && groups.length === 0 && <p className={styles.empty}>{text("No pages, applications or terminals in this conversation.", "这个会话还没有网页、应用或终端。")}</p>}
      {groups.map(group => {
        const open = loaded && collapsed[group.key] !== true;
        const toggleGroup = () => {
          if (!loaded) return;
          setCollapsed(value => {
            const closed = open;
            return value[group.key] === closed ? value : { ...value, [group.key]: closed };
          });
        };
        return <div key={group.key} className={`group/sec ${styles.groupBlock} min-w-0`}
          data-resource-group={group.key}>
        <SectionHeader
          name={group.title}
          className={styles.sectionHeader}
          collapsible
          collapsed={!open}
          onToggle={toggleGroup}
          actions={loaded ? <span className="flex items-center gap-[8px]">
            <small className={styles.groupCount}>{group.rows.length}</small>
          </span> : undefined}
        />
        {open && group.rows.map(row => {
          const Icon = icons[row.kind as keyof typeof icons] || Box;
          const tab = previewTabId(row) ? tabs.find(item => item.id === previewTabId(row)) : undefined;
          const operating = resourceIsOperating(row);
          const title = resourceDisplayTitle(row, tab);
          const thumbnail = tab ? thumbnails[tab.id] : undefined;
          const subtitle = [
            row.source === "process" ? text("Background terminal", "后台终端") : names[row.kind] || row.kind,
            operating ? text("Operating", "操作中") : statusName(row),
            row.agentName === "main" ? text("Main agent", "主 Agent") : row.agentName,
            thumbnail && text(`Snapshot ${shotTime(thumbnail.at)}`, `快照 ${shotTime(thumbnail.at)}`),
          ].filter(Boolean).join(" · ");
          return <div key={row.id} className={styles.row} data-resource-kind={row.kind}
            data-active={pref?.targetId === row.id && !pref.hidden}>
            <button type="button" className={styles.page} title={row.target}
              aria-pressed={pref?.targetId === row.id && !pref.hidden}
              onClick={() => {
                if (!sessionId) return;
                if (row.kind === "web" || row.source === "browser") {
                  setSelected(null);
                  previewResource(sessionId, viewedBranch, row.id);
                  bindPreview(sessionId, row, false);
                  retryRecoverablePage(row, tabs);
                  render(value => value + 1);
                } else selectView(row);
              }}>{thumbnail
                ? <img className={styles.thumbnail} src={thumbnail.url} alt="" aria-hidden="true" />
                : tab
                  // A web row without a picture yet keeps the picture's place,
                  // so every row's title starts at the same x.
                  ? <span className={styles.thumbnailSlot} aria-hidden="true"><Icon size={16} /></span>
                  : <Icon size={16} aria-hidden="true" />}<span><strong>{title}</strong>
              <small>{subtitle}</small></span></button>
            {operating && <span className={styles.dot} aria-hidden="true" />}
            {tab && <Button type="button" variant="ghost" size="icon-xs"
              aria-label={`${text("Preview in conversation", "在会话中预览")}: ${title}`}
              title={text("Preview in conversation", "在会话中预览")} onClick={() => {
                if (!sessionId) return;
                setSelected(null);
                previewResource(sessionId, viewedBranch, row.id);
                previewInConversation(sessionId, row);
                retryRecoverablePage(row, tabs);
                render(value => value + 1);
              }}><PictureInPicture2 size={14} aria-hidden="true" /></Button>}
            {tab && <Button type="button" variant="ghost" size="icon-xs"
              aria-label={`${text("Open in tab", "在标签中打开")}: ${title}`}
              title={text("Open in tab", "在标签中打开")} onClick={() => {
                const tabId = existingResourceTabId(row, tabs);
                if (tabId) revealExistingWebTab(tabId, useCenterTabs.getState());
                retryRecoverablePage(row, tabs);
                if (!tabId) render(value => value + 1);
              }}><ExternalLink size={14} aria-hidden="true" /></Button>}
            {(tab || row.resourceId) && <Button type="button" variant="ghost" size="icon-xs" aria-label={`${text("Close webpage", "关闭网页")}: ${title}`}
              title={text("Close webpage", "关闭网页")} onClick={() => {
                const resourceId = row.resourceId || row.sourceId;
                optimisticallyCloseBrowserResource(resourceId, row.generation || 0);
                if (tab) useCenterTabs.getState().closeTab(tab.id);
                const conversationSessionId = row.conversationSessionId || sessionId;
                if (conversationSessionId && row.resourceId) {
                  void requestCloseBrowserResource({ conversationSessionId, resourceId: row.resourceId })
                    .then(() => render(value => value + 1));
                }
                if (pref?.targetId === row.id) {
                  hideResourcePreview(sessionId!, viewedBranch);
                  useWebTabPip.getState().hide();
                }
                render(value => value + 1);
              }}><X size={14} /></Button>}
          </div>;
        })}
      </div>;
      })}

    </div>
    {terminalError && <p role="status" className={styles.notice}>{text("Terminal status is unavailable.", "暂时无法获取终端状态。")}</p>}
    {selected?.source === "terminal" && sessionId && terminals[selected.sourceId]?.generation === selected.terminalGeneration
      && terminals[selected.sourceId]?.session_ids.includes(sessionId) &&
      <TerminalResourceView key={`${selected.sourceId}:${terminals[selected.sourceId].generation}`}
        resource={terminals[selected.sourceId]} sessionId={sessionId} active={active} onHide={() => setSelected(null)} />}
    {selected?.source === "application" && selected.applicationInstanceId
      && rows.some(row => row.id === selected.id && row.applicationDigest === selected.applicationDigest)
      && <ApplicationResourceView resource={selected} onHide={() => setSelected(null)} />}
    {selected?.source === "process" && programs.detail?.process.id === selected.sourceId &&
      <BackgroundTerminalView key={selected.sourceId} process={programs.detail.process} output={programs.detail.output}
        onHide={() => setSelected(null)}
        onStop={() => { void stopProcess(selected.sourceId, sessionId).finally(() => { void programs.refresh(); }); }} />}
    {selected && !["terminal", "application", "process"].includes(selected.source) && <div className={styles.detail}>
      <Button type="button" variant="ghost" size="icon-xs" className="float-right" aria-label={text("Close resource details", "关闭资源详情")} onClick={() => setSelected(null)}><X size={14} /></Button>
      <strong>{selected.title}</strong><p>{names[selected.kind] || selected.kind} · {statusName(rows.find(row => row.id === selected.id) || selected)}</p>
      <p>{selected.target}</p>
    </div>}
  </section>;
}
