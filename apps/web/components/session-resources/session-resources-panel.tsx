"use client";

import { useState } from "react";
import { Box, Globe, Monitor, Search, Server, X } from "lucide-react";
import { useWebTabPip } from "@/lib/state/web-tab-pip-store";
import { useCenterTabs } from "@/lib/state/center-tabs-store";
import {
  existingResourceTabId,
  getPreviewPreference,
  groupSessionResources,
  hideResourcePreview,
  previewTabId,
  resourceIsOperating,
  resourceSessionId,
  selectResourcePreview,
  sessionResourceRows,
  showResourcePreview,
  useBrowserResourceStore,
  type SessionResource,
} from "@/lib/state/session-resources";
import {
  pendingCloseRequest,
  requestCloseBrowserPage,
  settlePendingClose,
} from "@/lib/state/browser-control";
import { useSessionResources } from "@/lib/use-session-resources";
import { useTranslation } from "@/lib/i18n";
import styles from "./session-resources.module.css";

export function SessionResourcesPanel() {
  const sessionId = useCenterTabs(s => resourceSessionId(s.tabs.find(tab => tab.id === s.activeId)));
  return <SessionResourceList key={sessionId || "no-session"} sessionId={sessionId} />;
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

function SessionResourceList({ sessionId }: { sessionId: string | null }) {
  const { text } = useTranslation();
  const tabs = useCenterTabs(s => s.tabs);
  const backend = useSessionResources(sessionId);
  useBrowserResourceStore(s => s.ingestClock);
  const pendingClose = pendingCloseRequest();
  const [query, setQuery] = useState("");
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const [selected, setSelected] = useState<SessionResource | null>(null);
  const [, render] = useState(0);
  const needle = query.trim().toLocaleLowerCase();
  const rows = sessionResourceRows(tabs, backend.rows, sessionId);
  const names: Record<string, string> = {
    web: text("Webpage", "网页"), docker: text("Container", "容器"), vm: "VM",
    remote: text("Remote environment", "远程环境"), desktop: text("Desktop", "桌面"),
  };
  const viewedBranch = backend.currentBranchId;
  const pref = sessionId ? getPreviewPreference(sessionId, viewedBranch) : null;
  const groups = groupSessionResources(rows, viewedBranch).map(group => {
    const title = group.key === "unavailable"
      ? text("Unavailable", "不可用")
      : group.key === "unassigned"
        ? text("Unassigned", "未归属")
        : group.title;
    const filtered = group.rows.filter(row => `${title} ${row.title} ${row.target} ${names[row.kind] || row.kind} ${row.agentName || ""}`.toLocaleLowerCase().includes(needle));
    return { ...group, title, rows: filtered };
  }).filter(group => group.rows.length > 0);
  const icons = { web: Globe, docker: Box, vm: Monitor, remote: Server, desktop: Monitor };
  const statusName = (status: string) => ({
    open: text("Open", "已打开"), idle: text("Idle", "空闲"), in_use: text("In use", "使用中"),
    attached: text("Attached", "已关联"), running: text("Running", "运行中"),
    starting: text("Starting", "启动中"), stopping: text("Stopping", "停止中"),
    closed: text("Closed", "已关闭"), unknown: text("Unknown", "状态未知"), released: text("Released", "已释放"),
  }[status] || status);

  return <section className={styles.panel} aria-label={text("Session resources", "会话资源")}>
    <label className={styles.search}><Search size={15} aria-hidden="true" />
      <input value={query} onChange={event => setQuery(event.target.value)}
        placeholder={text("Search resources", "搜索资源")}
        aria-label={text("Search resources", "搜索资源")} />
    </label>
    {backend.unavailable && <p role="status" className={styles.notice}>{text("Some resource statuses could not be refreshed.", "部分资源状态未能刷新。")}</p>}
    {pendingClose && <p role="status" className={styles.notice}>
      {pendingClose.error
        ? `${text("Stop unconfirmed", "停止未确认")}: ${pendingClose.error}`
        : `${text("Stopping", "正在停止")} · ${pendingClose.associationIds.length} ${text("references", "引用")}${pendingClose.executionIds.length ? ` · ${pendingClose.executionIds.length} ${text("executions", "执行")}` : ""}`}
    </p>}
    {pref?.hidden && sessionId && <button type="button" className={styles.showPreview} onClick={() => {
      const next = showResourcePreview(sessionId, viewedBranch);
      bindPreview(sessionId, rows.find(row => row.id === next.targetId), false);
      render(value => value + 1);
    }}>{text("Show preview", "显示预览")}</button>}
    <div className={styles.list}>
      {groups.length === 0 && <p className={styles.empty}>{needle ? text("No matching resources", "没有匹配的资源")
        : !backend.loaded ? text("Loading resources…", "正在加载资源…")
          : sessionId ? text("This session has no resources in use.", "当前会话没有正在使用的资源。") : text("Select a session to view its resources.", "选择会话以查看其资源。")}</p>}
      {groups.map(group => <details key={group.key} open={!!needle || (group.key === "unavailable" ? collapsed[group.key] === false : !collapsed[group.key])} onToggle={event => {
        if (needle) return;
        const closed = !event.currentTarget.open;
        setCollapsed(value => value[group.key] === closed ? value : { ...value, [group.key]: closed });
      }}>
        <summary className={styles.group} title={group.title}>
          <span>{group.title}{group.current ? ` · ${text("Current", "当前")}` : ""}</span>
          <small>{group.rows.length}</small>
        </summary>
        {group.rows.map(row => {
          const Icon = icons[row.kind as keyof typeof icons] || Box;
          const tab = previewTabId(row) ? tabs.find(item => item.id === previewTabId(row)) : undefined;
          const operating = resourceIsOperating(row);
          const subtitle = [
            names[row.kind] || row.kind,
            operating ? text("Operating", "操作中") : statusName(row.status),
            row.agentName,
          ].filter(Boolean).join(" · ");
          return <div key={row.id} className={styles.row} data-resource-kind={row.kind}
            data-active={pref?.targetId === row.id && !pref.hidden}>
            <button type="button" className={styles.page} title={row.target}
              aria-pressed={pref?.targetId === row.id && !pref.hidden}
              onClick={() => {
                if (!sessionId) return;
                if (row.kind === "web" || row.source === "browser") {
                  selectResourcePreview(sessionId, viewedBranch, row.id);
                  bindPreview(sessionId, row, false);
                  render(value => value + 1);
                } else setSelected(row);
              }}><Icon size={16} aria-hidden="true" /><span><strong>{row.title}</strong>
              <small>{subtitle}</small></span></button>
            {operating && <span className={styles.dot} aria-hidden="true" />}
            {tab && <button type="button" className={styles.action}
              aria-label={`${text("Open in tab", "在标签中打开")}: ${row.title}`}
              title={text("Open in tab", "在标签中打开")} onClick={() => {
                const tabId = existingResourceTabId(row, tabs);
                if (tabId) useCenterTabs.getState().setActive(tabId);
                else render(value => value + 1);
              }}>↗</button>}
            {tab && <button type="button" className={styles.action} aria-label={`${text("Close webpage", "关闭网页")}: ${row.title}`}
              title={text("Close webpage", "关闭网页")} onClick={() => {
                const result = requestCloseBrowserPage(row, tabs);
                if (result === "closed") {
                  useCenterTabs.getState().closeTab(tab.id);
                  if (pref?.targetId === row.id) {
                    hideResourcePreview(sessionId!, viewedBranch);
                    useWebTabPip.getState().hide();
                  }
                } else if (result === "pending") {
                  settlePendingClose(id => useCenterTabs.getState().closeTab(id));
                }
                render(value => value + 1);
              }}><X size={14} /></button>}
          </div>;
        })}
      </details>)}
    </div>
    {selected && <div className={styles.detail}>
      <button type="button" className={styles.action} aria-label={text("Close resource details", "关闭资源详情")} onClick={() => setSelected(null)}><X size={14} /></button>
      <strong>{selected.title}</strong><p>{names[selected.kind] || selected.kind} · {statusName(rows.find(row => row.id === selected.id)?.status || "released")}</p>
      <p>{selected.target}</p>
    </div>}
  </section>;
}
