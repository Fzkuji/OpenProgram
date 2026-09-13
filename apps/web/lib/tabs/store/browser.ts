import { hostnameOf, nextBrowserHomeId, nextPopupWebTabId, webTabId } from "@/lib/tabs/center-tab-ids";
import { replaceGroupTabId } from "@/lib/tabs/center-tabs-persistence";
import type { StoreApi } from "zustand";
import { recordTabPage } from "../navigation/page-history";
import { commitCenterTabsState, focusOrCreate } from "./core";
import type { CenterTab, CenterTabsState } from "./types";

export function browserActions(set: StoreApi<CenterTabsState>["setState"], get: StoreApi<CenterTabsState>["getState"]): Pick<CenterTabsState, "openWebTab" | "openPopupWebTab" | "markAgentWebTab" | "setWebTabPinned" | "ensureWebTab" | "ensureExclusiveWebTab" | "updateWebTab" | "replaceWebTabWithNewTabPage"> {
  return {
    openWebTab: (url, agentRequest = false) =>
      set((s) => {
        const id = webTabId(url);
        return focusOrCreate(s, id,
          () => ({ id, kind: "web", title: hostnameOf(url), url }), [],
          tab => ({
            ...tab, url, title: tab.url !== url ? hostnameOf(url) : tab.title,
            webPinned: !agentRequest && tab.agentOpened ? true : tab.webPinned
          }));
      }),

    openPopupWebTab: (url, openerTabId) => {
      const id = nextPopupWebTabId(url);
      set((s) => {
        const opener = s.tabs.find((tab) => tab.id === openerTabId);
        return commitCenterTabsState(s, {
          tabs: [
            ...s.tabs,
            {
              id, kind: "web", title: hostnameOf(url), url, openerTabId,
              agentOpened: opener?.agentOpened,
              agentSessionId: opener?.agentSessionId,
              agentBranchId: opener?.agentBranchId,
              agentExecutionId: opener?.agentExecutionId,
            },
          ],
          activeId: opener?.agentOpened ? s.activeId : id,
        });
      });
      return id;
    },

    markAgentWebTab: (id, sessionId, attribution) => set((s) => commitCenterTabsState(s, {
      tabs: s.tabs.map((tab) => tab.id === id && tab.kind === "web"
        ? {
          ...tab, agentOpened: true, agentSessionId: sessionId || undefined,
          agentBranchId: attribution?.branchId || tab.agentBranchId,
          agentExecutionId: attribution?.executionId || tab.agentExecutionId
        }
        : tab),
    })),

    setWebTabPinned: (id, pinned) => set((s) => commitCenterTabsState(s, {
      tabs: s.tabs.map((tab) => tab.id === id && tab.kind === "web"
        ? { ...tab, webPinned: pinned } : tab),
    })),

    ensureWebTab: (url) => {
      const id = webTabId(url);
      set((s) => {
        const existing = s.tabs.find((tab) => tab.id === id);
        if (!existing) {
          return commitCenterTabsState(s, {
            tabs: [...s.tabs, { id, kind: "web", title: hostnameOf(url), url }],
          });
        }
        if (existing.url === url) return {};
        return commitCenterTabsState(s, {
          tabs: s.tabs.map((tab) =>
            tab.id === id ? { ...tab, url, title: hostnameOf(url) } : tab,
          ),
        });
      });
      return id;
    },

    ensureExclusiveWebTab: (url) => {
      const id = nextPopupWebTabId(url);
      set((s) => commitCenterTabsState(s, {
        tabs: [...s.tabs, { id, kind: "web", title: hostnameOf(url), url }],
      }));
      return id;
    },

    updateWebTab: (id, patch) =>
      set((s) => {
        const tab = s.tabs.find((t) => t.id === id && t.kind === "web");
        if (!tab) return {};
        const url = patch.url ?? tab.url;
        // Navigating to a new site resets a stale title to the new
        // hostname unless the caller supplies one.
        const title =
          patch.title ??
          (patch.url && patch.url !== tab.url ? hostnameOf(patch.url) : tab.title);
        // Navigating to a new site drops the old site's icon unless the
        // caller supplies one (main.js clears it on did-navigate too).
        const faviconUrl =
          patch.faviconUrl ??
          (patch.url && patch.url !== tab.url ? undefined : tab.faviconUrl);
        const urlNativeAt = patch.urlNativeAt ?? (
          patch.url && patch.url !== tab.url ? undefined : tab.urlNativeAt
        );
        if (
          url === tab.url
          && title === tab.title
          && faviconUrl === tab.faviconUrl
          && urlNativeAt === tab.urlNativeAt
        ) {
          return {};
        }
        const tabs = s.tabs.map((t) =>
          t.id === id ? { ...t, url, title, faviconUrl, urlNativeAt } : t
        );
        return commitCenterTabsState(s, { tabs });
      }),

    replaceWebTabWithNewTabPage: (id) =>
      set((s) => {
        const index = s.tabs.findIndex((tab) => tab.id === id && tab.kind === "web");
        if (index < 0) return {};
        const homeTab: CenterTab = {
          id: nextBrowserHomeId(),
          kind: "builtin",
          title: "",
          page: "browser",
        };
        const tabs = s.tabs.map((tab, tabIndex) =>
          tabIndex === index ? recordTabPage(tab, homeTab) : tab
        );
        return commitCenterTabsState(s, {
          tabs,
          activeId: s.activeId === id ? homeTab.id : s.activeId,
          groups: replaceGroupTabId(s.groups, id, homeTab.id),
          splitWebTabId: s.splitWebTabId === id ? null : s.splitWebTabId,
        });
      })
  };
}
