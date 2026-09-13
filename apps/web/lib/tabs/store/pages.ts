import { normalizeCenterTabLayout } from "@/lib/tabs/center-tab-groups";
import { builtinTabId, fileTabId, nextBrowserHomeId, nextNtpId } from "@/lib/tabs/center-tab-ids";
import { replaceGroupTabId } from "@/lib/tabs/center-tabs-persistence";
import { openReviewTabLayout } from "@/lib/tabs/review-tab-layout";
import type { StoreApi } from "zustand";
import { topLevelTabs } from "../../browser/web-page-management";
import { mapTabPages } from "../navigation/page-history";
import { sessionHistory } from "../navigation/session-history";
import { commitCenterTabsState, focusOrCreate, independentTab } from "./core";
import type { CenterTab, CenterTabsState } from "./types";

export function pagesActions(set: StoreApi<CenterTabsState>["setState"], get: StoreApi<CenterTabsState>["getState"], closedSessionAckTombstones: Set<string>): Pick<CenterTabsState, "openFileTab" | "openBuiltinTab" | "openApplicationTab" | "openReviewTab" | "setTabDirty" | "setTabDagView" | "retargetFileTab" | "openNewTabPage" | "closeTab"> {
  return {
    openFileTab: (projectId, path, options) =>
      set((s) => {
        const id = fileTabId(projectId, path);
        const opened = focusOrCreate(
          s,
          id,
          () => ({
            id,
            kind: "file",
            title: path.split("/").pop() || path,
            projectId,
            path,
            ...options,
          }),
          s.tabs.filter(tab => tab.id === s.activeId && (tab.kind === "file" || (tab.kind === "builtin" && tab.page === "files"))).map(tab => tab.id),
        );
        // Reopening the same path must not keep a stale diff/jump
        // context: overwrite it (undefined options clear it) so the
        // tab always reflects the LATEST open.
        const tabs = opened.tabs ?? s.tabs;
        return {
          ...opened,
          tabs: tabs.map((t) =>
            t.id === opened.activeId
              ? {
                ...t,
                diffSessionId: options?.diffSessionId,
                diffMsgId: options?.diffMsgId,
                scrollToLine: options?.scrollToLine,
                highlightLines: options?.highlightLines,
              }
              : t,
          ),
        };
      }),

    openBuiltinTab: (page) =>
      set((s) => {
        const id = page === "browser" ? nextBrowserHomeId() : builtinTabId(page);
        return focusOrCreate(
          s,
          id,
          () => ({ id, kind: "builtin", title: "", page }),
          [],
        );
      }),

    openApplicationTab: (appId, instanceId, title) => set((s) => focusOrCreate(
      s, `app:${instanceId}`,
      () => ({ id: `app:${instanceId}`, kind: "application", title, applicationId: appId, applicationInstanceId: instanceId }), [],
    )),

    openReviewTab: (sessionId, assistantMsgId, scope = "turn", path) =>
      set((s) => {
        const next = openReviewTabLayout(
          s.tabs, s.groups, sessionId, assistantMsgId, scope, path, s.activeId,
        );
        return commitCenterTabsState(s, {
          tabs: next.tabs, groups: next.groups, activeId: next.id,
        });
      }),

    setTabDirty: (id, dirty) =>
      set((s) => {
        const tab = s.tabs.find((t) => t.id === id);
        if (!tab || !!tab.dirty === dirty) return {};
        const tabs = s.tabs.map((t) => (t.id === id ? { ...t, dirty } : t));
        return commitCenterTabsState(s, { tabs });
      }),

    setTabDagView: (id, dagView) =>
      set((s) => {
        const tab = s.tabs.find((t) => t.id === id);
        if (!tab || !!tab.dagView === dagView) return {};
        const tabs = s.tabs.map((t) => (t.id === id ? { ...t, dagView } : t));
        return commitCenterTabsState(s, { tabs });
      }),

    retargetFileTab: (oldId, newProjectId, newPath) =>
      set((s) => {
        const tab = s.tabs.find((t) => t.id === oldId && t.kind === "file");
        if (!tab) return {};
        if (tab.projectId === newProjectId && tab.path === newPath) return {};
        const newId = independentTab({ ...tab, id: fileTabId(newProjectId, newPath) }, s.tabs, oldId).id;
        const next = mapTabPages(tab, page => {
          const snapshot = page.fileNavigationSnapshot;
          const matchingFile = page.kind === "file" && page.projectId === tab.projectId && page.path === tab.path;
          const matchingView = snapshot?.projectId === tab.projectId && snapshot?.path === tab.path;
          if (!matchingFile && !matchingView) return page;
          return {
            ...page,
            ...(matchingFile ? { id: newId, projectId: newProjectId, path: newPath, title: newPath.split("/").pop() || newPath } : {}),
            ...(matchingView ? {
              fileNavigationSnapshot: {
                ...snapshot!, projectId: newProjectId, path: newPath,
                scroll: snapshot!.scroll?.path === tab.path ? { offset: snapshot!.scroll!.offset, path: newPath } : snapshot!.scroll
              }
            } : {}),
          };
        });
        return commitCenterTabsState(s, {
          tabs: s.tabs.map(item => item.id === oldId ? next : item),
          activeId: s.activeId === oldId ? newId : s.activeId,
          groups: replaceGroupTabId(s.groups, oldId, newId),
          splitWebTabId: s.splitWebTabId === oldId ? newId : s.splitWebTabId,
        });
      }),

    openNewTabPage: () =>
      set((s) => {
        const tab: CenterTab = { id: nextNtpId(), kind: "ntp", title: "" };
        return commitCenterTabsState(s, {
          tabs: [...s.tabs, tab],
          activeId: tab.id,
        });
      }),

    closeTab: (id) =>
      set((s) => {
        const idx = s.tabs.findIndex((t) => t.id === id);
        if (idx < 0) return {};
        const closingTab = s.tabs[idx];
        for (const page of [closingTab, ...(closingTab.pageHistory?.entries ?? [])]) {
          if (page.kind !== "session") continue;
          for (const entry of sessionHistory(page).entries)
            if (entry.sessionId) closedSessionAckTombstones.add(entry.sessionId);
        }
        const tabs = s.tabs.filter((t) => t.id !== id);
        const groups = normalizeCenterTabLayout({
          tabIds: tabs.map((tab) => tab.id), groups: s.groups,
        }).groups;
        const visibleTabs = topLevelTabs(tabs, groups);
        let activeId = s.activeId;
        if (!visibleTabs.some((tab) => tab.id === activeId)) {
          const visibleIndex = topLevelTabs(s.tabs, s.groups).findIndex((tab) => tab.id === id);
          activeId = (visibleTabs[Math.max(0, visibleIndex)] ?? visibleTabs.at(-1))?.id ?? null;
        }
        const splitWebTabId = s.splitWebTabId === id ? null : s.splitWebTabId;
        return commitCenterTabsState(s, { tabs, groups, activeId, splitWebTabId });
      })
  };
}
