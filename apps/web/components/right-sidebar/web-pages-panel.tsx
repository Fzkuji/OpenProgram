"use client";

import { useState } from "react";
import { Globe, Pin, PinOff, Search, X } from "lucide-react";
import { useRouter } from "next/navigation";
import { useCenterTabs } from "@/lib/state/center-tabs-store";
import { groupWebPages } from "@/lib/state/web-page-management";
import { useSessionStore } from "@/lib/session-store";
import { useTranslation } from "@/lib/i18n";
import styles from "./web-pages-panel.module.css";

export function WebPagesPanel() {
  const { text } = useTranslation();
  const router = useRouter();
  const tabs = useCenterTabs(s => s.tabs);
  const activeId = useCenterTabs(s => s.activeId);
  const conversations = useSessionStore(s => s.conversations);
  const currentSessionId = useSessionStore(s => s.currentSessionId);
  const [query, setQuery] = useState("");
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const needle = query.trim().toLocaleLowerCase();
  const groups = groupWebPages(tabs).map(group => {
    const title = group.sessionId
      ? conversations[group.sessionId]?.title
        || tabs.find(tab => tab.kind === "session" && tab.sessionId === group.sessionId)?.title
        || group.sessionId
      : group.agent ? text("Agent pages · session unknown", "Agent 网页 · 会话未知")
        : text("Manually opened", "手动打开");
    const matches = group.tabs.filter(tab => `${title} ${tab.title} ${tab.url ?? ""}`.toLocaleLowerCase().includes(needle));
    return { ...group, title, matches };
  }).filter(group => group.matches.length > 0)
    .sort((a, b) => Number(b.sessionId === currentSessionId) - Number(a.sessionId === currentSessionId));

  return <section className={styles.panel} aria-label={text("Open webpages", "已打开的网页")}>
    <label className={styles.search}>
      <Search size={15} aria-hidden="true" />
      <input value={query} onChange={event => setQuery(event.target.value)}
        placeholder={text("Search pages or conversations", "搜索网页或会话")}
        aria-label={text("Search pages or conversations", "搜索网页或会话")} />
    </label>
    <div className={styles.list}>
      {groups.length === 0 && <p className={styles.empty}>{needle
        ? text("No matching pages", "没有匹配的网页")
        : text("Pages opened by agents appear here, grouped by conversation.", "Agent 打开的网页会在此按会话分组。")}</p>}
      {groups.map(group => <details key={group.key} open={!!needle || !collapsed[group.key]}
        onToggle={event => {
          if (needle) return;
          const closed = !event.currentTarget.open;
          setCollapsed(value => value[group.key] === closed ? value : { ...value, [group.key]: closed });
        }}>
        <summary className={styles.group} title={group.title}>
          <span>{group.title}</span><small>{group.matches.length}</small>
        </summary>
        {group.matches.map(tab => <div key={tab.id} className={styles.row} data-active={tab.id === activeId}>
          <button type="button" className={styles.page} aria-pressed={tab.id === activeId}
            title={tab.url} onClick={() => {
              useCenterTabs.getState().setActive(tab.id);
              if (window.location.pathname !== "/chat" && !window.location.pathname.startsWith("/s/")) router.push("/chat");
            }}>
            <Globe size={16} aria-hidden="true" />
            <span><strong>{tab.title || tab.url}</strong><small>{tab.url}</small></span>
          </button>
          {tab.agentOpened && <button type="button" className={styles.action}
            title={tab.webPinned ? text("Remove from top tabs", "取消顶部固定") : text("Pin to top tabs", "固定到顶部")}
            aria-label={tab.webPinned ? text("Remove from top tabs", "取消顶部固定") : text("Pin to top tabs", "固定到顶部")}
            aria-pressed={!!tab.webPinned} onClick={() => useCenterTabs.getState().setWebTabPinned(tab.id, !tab.webPinned)}>
            {tab.webPinned ? <PinOff size={14} /> : <Pin size={14} />}
          </button>}
          <button type="button" className={styles.action} title={text("Close page", "关闭网页")}
            aria-label={`${text("Close page", "关闭网页")}: ${tab.title || tab.url}`}
            onClick={() => useCenterTabs.getState().closeTab(tab.id)}><X size={14} /></button>
        </div>)}
      </details>)}
    </div>
  </section>;
}
