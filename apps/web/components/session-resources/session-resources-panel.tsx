"use client";

import { useEffect, useState } from "react";
import { Box, File, Globe, Monitor, Pin, PinOff, Search, Server, Terminal, X } from "lucide-react";
import { useRouter } from "next/navigation";
import { useCenterTabs } from "@/lib/state/center-tabs-store";
import { resourceSessionIds, sessionResourceRows, type SessionResource } from "@/lib/state/session-resources";
import { useSessionResources } from "@/lib/use-session-resources";
import { useSessionStore } from "@/lib/session-store";
import { getProcess } from "@/lib/net/process-client";
import { useTranslation } from "@/lib/i18n";
import styles from "./session-resources.module.css";

export function SessionResourcesPanel() {
  const { text } = useTranslation();
  const router = useRouter();
  const tabs = useCenterTabs(s => s.tabs);
  const activeId = useCenterTabs(s => s.activeId);
  const conversations = useSessionStore(s => s.conversations);
  const currentSessionId = useSessionStore(s => s.currentSessionId);
  const backend = useSessionResources(JSON.stringify(resourceSessionIds(tabs, currentSessionId)));
  const [query, setQuery] = useState("");
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const [selected, setSelected] = useState<SessionResource | null>(null);
  const [output, setOutput] = useState<string | null>(null);
  useEffect(() => {
    setOutput(null);
    if (selected?.source !== "process") return;
    const controller = new AbortController();
    void getProcess(selected.sourceId, controller.signal, (selected.scopeSessionId || selected.sessionId)).then(
      result => { if (!controller.signal.aborted) setOutput(result.output); },
      () => { if (!controller.signal.aborted) setOutput(text("Output unavailable", "无法读取输出")); },
    );
    return () => controller.abort();
  }, [selected?.id, text]);
  const needle = query.trim().toLocaleLowerCase();
  const rows = sessionResourceRows(tabs, backend.rows);
  const names: Record<string, string> = {
    web: text("Webpage", "网页"), file: text("File", "文件"), terminal: text("Terminal", "终端"), docker: "Docker", vm: "VM",
    ssh: "SSH", desktop: text("Desktop", "桌面"), process: text("Process", "进程"),
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
  const ordered = [...groups].sort(([a], [b]) => Number(b === currentSessionId) - Number(a === currentSessionId));
  const icons = { web: Globe, file: File, docker: Box, vm: Monitor, ssh: Server, desktop: Monitor, process: Terminal, terminal: Terminal };
  const statusName = (status: string) => ({
    open: text("Open", "已打开"), in_use: text("In use", "使用中"), attached: text("Attached", "已关联"),
    running: text("Running", "运行中"), starting: text("Starting", "启动中"), stopping: text("Stopping", "停止中"), unknown: text("Unknown", "状态未知"), released: text("Released", "已释放"),
  }[status] || status);

  return <section className={styles.panel} aria-label={text("Session resources", "会话资源")}>
    <label className={styles.search}><Search size={15} aria-hidden="true" />
      <input value={query} onChange={event => setQuery(event.target.value)}
        placeholder={text("Search resources or sessions", "搜索资源或会话")}
        aria-label={text("Search resources or sessions", "搜索资源或会话")} />
    </label>
    {backend.unavailable && <p role="status" className={styles.notice}>{text("Some resource statuses could not be refreshed.", "部分资源状态未能刷新。")}</p>}
    <div className={styles.list}>
      {ordered.length === 0 && <p className={styles.empty}>{needle ? text("No matching resources", "没有匹配的资源")
        : !backend.loaded ? text("Loading resources…", "正在加载资源…")
          : text("Resources used by this window's sessions appear here.", "当前窗口会话使用的资源会显示在这里。")}</p>}
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
              if (row.source === "web" || row.source === "file" || row.source === "terminal") {
                useCenterTabs.getState().setActive(row.sourceId);
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
      {selected.source === "process" && <pre>{output ?? text("Loading output…", "正在读取输出…")}</pre>}
    </div>}
  </section>;
}
