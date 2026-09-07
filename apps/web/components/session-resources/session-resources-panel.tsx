"use client";

import { useState } from "react";
import { Box, Globe, Monitor, Pin, PinOff, Search, Server, X } from "lucide-react";
import { useRouter } from "next/navigation";
import { useWebTabPip } from "@/lib/state/web-tab-pip-store";
import { useCenterTabs } from "@/lib/state/center-tabs-store";
import { resourceSessionId, sessionResourceRows, type SessionResource } from "@/lib/state/session-resources";
import { useSessionResources } from "@/lib/use-session-resources";
import { useSessionStore } from "@/lib/session-store";
import { useTranslation } from "@/lib/i18n";
import styles from "./session-resources.module.css";

export function SessionResourcesPanel() {
  const sessionId = useCenterTabs(s => resourceSessionId(s.tabs.find(tab => tab.id === s.activeId)));
  return <SessionResourceList key={sessionId || "no-session"} sessionId={sessionId} />;
}

function SessionResourceList({ sessionId }: { sessionId: string | null }) {
  const { text } = useTranslation();
  const router = useRouter();
  const tabs = useCenterTabs(s => s.tabs);
  const activeId = useCenterTabs(s => s.activeId);
  const conversations = useSessionStore(s => s.conversations);
  const backend = useSessionResources(sessionId);
  const [query, setQuery] = useState("");
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const [selected, setSelected] = useState<SessionResource | null>(null);
  const needle = query.trim().toLocaleLowerCase();
  const rows = sessionResourceRows(tabs, backend.rows, sessionId);
  const names: Record<string, string> = {
    web: text("Webpage", "网页"), docker: text("Container", "容器"), vm: "VM",
    remote: text("Remote environment", "远程环境"), desktop: text("Desktop", "桌面"),
  };
  const groups = new Map<string, { title: string; rows: SessionResource[] }>();
  for (const row of rows) {
    const key = row.sessionId || "unassigned";
    const title = row.sessionId ? conversations[row.sessionId]?.title
      || tabs.find(tab => tab.kind === "session" && tab.sessionId === row.sessionId)?.title || row.sessionId
      : text("Unassigned resources", "未归属会话的资源");
    if (!`${title} ${row.title} ${row.target} ${names[row.kind] || row.kind}`.toLocaleLowerCase().includes(needle)) continue;
    if (!groups.has(key)) groups.set(key, { title, rows: [] });
    groups.get(key)!.rows.push(row);
  }
  const ordered = [...groups];
  const icons = { web: Globe, docker: Box, vm: Monitor, remote: Server, desktop: Monitor };
  const statusName = (status: string) => ({
    open: text("Open", "已打开"), in_use: text("In use", "使用中"), attached: text("Attached", "已关联"),
    running: text("Running", "运行中"), starting: text("Starting", "启动中"), stopping: text("Stopping", "停止中"), unknown: text("Unknown", "状态未知"), released: text("Released", "已释放"),
  }[status] || status);

  return <section className={styles.panel} aria-label={text("Session resources", "会话资源")}>
    <label className={styles.search}><Search size={15} aria-hidden="true" />
      <input value={query} onChange={event => setQuery(event.target.value)}
        placeholder={text("Search session resources", "搜索当前会话资源")}
        aria-label={text("Search session resources", "搜索当前会话资源")} />
    </label>
    {backend.unavailable && <p role="status" className={styles.notice}>{text("Some resource statuses could not be refreshed.", "部分资源状态未能刷新。")}</p>}
    <div className={styles.list}>
      {ordered.length === 0 && <p className={styles.empty}>{needle ? text("No matching resources", "没有匹配的资源")
        : !backend.loaded ? text("Loading resources…", "正在加载资源…")
          : sessionId ? text("This session has no resources in use.", "当前会话没有正在使用的资源。") : text("Select a session to view its resources.", "选择会话以查看其资源。")}</p>}
      {ordered.map(([key, group]) => <details key={key} open={!!needle || !collapsed[key]} onToggle={event => {
        if (needle) return;
        const closed = !event.currentTarget.open;
        setCollapsed(value => value[key] === closed ? value : { ...value, [key]: closed });
      }}>
        <summary className={styles.group} title={group.title}><span>{group.title}</span><small>{group.rows.length}</small></summary>
        {group.rows.map(row => {
          const Icon = icons[row.kind as keyof typeof icons] || Box;
          const tab = row.source === "web" ? tabs.find(tab => tab.id === row.sourceId) : undefined;
          return <div key={row.id} className={styles.row} data-resource-kind={row.kind}
            data-active={selected?.id === row.id || row.sourceId === activeId}>
            <button type="button" className={styles.page} title={row.target} onClick={() => {
              if (row.source === "web") {
                const pip = useWebTabPip.getState();
                if (pip.tabId === row.sourceId || pip.backgroundTabId === row.sourceId) pip.end();
                const center = useCenterTabs.getState();
                center.setWebTabPinned(row.sourceId, true);
                center.ungroupTab(row.sourceId);
                center.setActive(row.sourceId);
                if (window.location.pathname !== "/chat" && !window.location.pathname.startsWith("/s/")) router.push("/chat");
              } else setSelected(row);
            }}><Icon size={17} aria-hidden="true" /><span><strong>{row.title}</strong>
              <small>{names[row.kind] || row.kind} · {statusName(row.status)}{row.target ? ` · ${row.target}` : ""}</small></span></button>
            {tab?.agentOpened && <button type="button" className={styles.action}
              aria-label={tab.webPinned ? text("Remove from top tabs", "取消顶部固定") : text("Pin to top tabs", "固定到顶部")}
              title={tab.webPinned ? text("Remove from top tabs", "取消顶部固定") : text("Pin to top tabs", "固定到顶部")}
              aria-pressed={!!tab.webPinned} onClick={() => useCenterTabs.getState().setWebTabPinned(tab.id, !tab.webPinned)}>
              {tab.webPinned ? <PinOff size={14} /> : <Pin size={14} />}</button>}
            {tab && <button type="button" className={styles.action} aria-label={`${text("Close page", "关闭网页")}: ${row.title}`}
              title={text("Close page", "关闭网页")} onClick={() => useCenterTabs.getState().closeTab(tab.id)}><X size={14} /></button>}
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
