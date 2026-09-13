import { sessionTabId } from "@/lib/tabs/center-tab-ids";
import { draftTab, replaceGroupTabId } from "@/lib/tabs/center-tabs-persistence";
import type { StoreApi } from "zustand";
import { mapTabPages, recordTabPage } from "../navigation/page-history";
import { sessionHistory, withSessionHistory } from "../navigation/session-history";
import { commitCenterTabsState } from "./core";
import type { CenterTab, CenterTabsState } from "./types";

export function sessionsActions(set: StoreApi<CenterTabsState>["setState"], get: StoreApi<CenterTabsState>["getState"], closedSessionAckTombstones: Set<string>): Pick<CenterTabsState, "openSessionTab" | "openDraftSessionTab" | "claimDraftSessionTab" | "markSessionReady" | "renameSessionTab"> {
  return {
    openSessionTab: (sessionId, title) =>
      set((s) => {
        closedSessionAckTombstones.delete(sessionId);
        const active = s.tabs.find(tab => tab.id === s.activeId);
        const existing = s.tabs.find(tab => tab.kind === "session" && tab.sessionId === sessionId);
        if (existing && existing.id !== active?.id) return commitCenterTabsState(s, { activeId: existing.id });
        if (active?.kind === "session") {
          if (active.sessionId === sessionId) {
            if (active.title === title) return {};
            const history = sessionHistory(active);
            const entries = history.entries.map((entry, index) => index === history.index
              ? { ...entry, title } : entry);
            return commitCenterTabsState(s, {
              tabs: s.tabs.map(tab => tab.id === active.id
                ? { ...tab, title, sessionHistory: { ...history, entries } } : tab)
            });
          }
          if (active.sessionId) closedSessionAckTombstones.add(active.sessionId);
          const history = sessionHistory(active);
          const known = s.tabs.flatMap(tab => tab.kind === "session"
            ? sessionHistory(tab).entries : []).find(entry => entry.sessionId === sessionId);
          const { navigationRoute: _route, ...current } = active;
          const next = recordTabPage(active, withSessionHistory(current, {
            entries: [...history.entries.slice(0, history.index + 1),
            { sessionId, title, draft: !!known?.draft }],
            index: history.index + 1,
          }));
          return commitCenterTabsState(s, { tabs: s.tabs.map(tab => tab.id === active.id ? next : tab) });
        }
        let id = sessionTabId(sessionId);
        if (s.tabs.some(tab => tab.id === id)) id += `:${crypto.randomUUID()}`;
        const tab: CenterTab = { id, kind: "session", title, sessionId };
        const replace = active?.kind === "ntp";
        return commitCenterTabsState(s, {
          tabs: replace ? s.tabs.map(item => item.id === active.id ? recordTabPage(item, tab) : item) : [...s.tabs, tab],
          activeId: id,
          groups: replace ? replaceGroupTabId(s.groups, active.id, id) : s.groups,
        });
      }),

    openDraftSessionTab: () => {
      const tab = draftTab();
      set((s) => {
        const activeIdx = s.tabs.findIndex((t) => t.id === s.activeId);
        const tabs =
          activeIdx >= 0 && s.tabs[activeIdx].kind === "ntp"
            ? s.tabs.map((item, i) => (i === activeIdx ? recordTabPage(item, tab) : item))
            : [...s.tabs, tab];
        const replacedId = activeIdx >= 0 && s.tabs[activeIdx].kind === "ntp"
          ? s.tabs[activeIdx].id
          : null;
        return commitCenterTabsState(s, {
          tabs,
          activeId: tab.id,
          groups: replacedId
            ? replaceGroupTabId(s.groups, replacedId, tab.id)
            : s.groups,
        });
      });
      return tab.sessionId!;
    },

    claimDraftSessionTab: () => {
      const tab = draftTab();
      set((s) => {
        const activeIdx = s.tabs.findIndex((t) => t.id === s.activeId);
        if (activeIdx < 0) return {};
        const replacedId = s.tabs[activeIdx].id;
        const tabs = s.tabs.map((item, i) => (i === activeIdx ? recordTabPage(item, tab) : item));
        return commitCenterTabsState(s, {
          tabs,
          activeId: tab.id,
          groups: replaceGroupTabId(s.groups, replacedId, tab.id),
        });
      });
      return tab.sessionId!;
    },

    markSessionReady: (sessionId) =>
      set((s) => {
        const tabs = s.tabs.map(original => mapTabPages(original, tab => {
          if (tab.kind !== "session") return tab;
          const history = sessionHistory(tab);
          if (!history.entries.some(entry => entry.sessionId === sessionId && entry.draft)) return tab;
          const entries = history.entries.map(entry => entry.sessionId === sessionId
            ? { ...entry, draft: false } : entry);
          return {
            ...tab, draft: tab.sessionId === sessionId ? false : tab.draft,
            sessionHistory: { ...history, entries }
          };
        }));
        return commitCenterTabsState(s, { tabs });
      }),

    renameSessionTab: (sessionId, title) =>
      set((s) => {
        const tabs = s.tabs.map(original => mapTabPages(original, tab => {
          if (tab.kind !== "session") return tab;
          const history = sessionHistory(tab);
          if (!history.entries.some(entry => entry.sessionId === sessionId && entry.title !== title)) return tab;
          return {
            ...tab, title: tab.sessionId === sessionId ? title : tab.title,
            sessionHistory: {
              ...history, entries: history.entries.map(entry =>
                entry.sessionId === sessionId ? { ...entry, title } : entry)
            }
          };
        }));
        if (tabs.every((tab, index) => tab === s.tabs[index])) return {};
        return commitCenterTabsState(s, { tabs });
      })
  };
}
