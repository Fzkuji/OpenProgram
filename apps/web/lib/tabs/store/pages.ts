import { filePreviewLayout, filePreviewId, type FilePreviewTarget, type PagePreviewTarget } from "../panes/file-preview-layout";
import { findCenterTabGroup, normalizeCenterTabLayout } from "@/lib/tabs/center-tab-groups";
import { documentControllers } from "@/lib/files/document/document-controller-registry";
import { builtinTabId, fileTabId, nextBrowserHomeId, nextNtpId } from "@/lib/tabs/center-tab-ids";
import { replaceGroupTabId } from "@/lib/tabs/center-tabs-persistence";
import { openReviewTabLayout } from "@/lib/tabs/panes/review-tab-layout";
import type { StoreApi } from "zustand";
import { webTabSessionIds } from "../../browser/web-tab/web-page-management";
import { mapTabPages } from "../navigation/page-history";
import { sessionHistory } from "../navigation/session-history";
import { commitCenterTabsState, focusAfterClose, focusOrCreate, independentTab } from "./core";
import { requestFileSection } from "../panes/file-section-requests";
import type { CenterTab, CenterTabsState } from "./types";
import { closedTabSessionIds, rememberClosedTab } from "./closed-tabs";

export function pagesActions(set: StoreApi<CenterTabsState>["setState"], get: StoreApi<CenterTabsState>["getState"], closedSessionAckTombstones: Set<string>): Pick<CenterTabsState, "openFilePreview" | "openPagePreview" | "openFileTab" | "openBuiltinTab" | "openApplicationTab" | "openReviewTab" | "setTabDirty" | "setTabDagView" | "retargetFileTab" | "openNewTabPage" | "closeTab" | "reopenClosedTab"> {
  // Shared by file and page previews: both open beside their chat.
  const openPreview = (sessionId: string, target: FilePreviewTarget | PagePreviewTarget, automatic: boolean, section?: string) => {
    let opened = false;
    set(state => {
      if (automatic) {
        const chat = state.tabs.find(tab => tab.kind === "session" && tab.sessionId === sessionId);
        const group = chat && findCenterTabGroup(state.groups, chat.id);
        if (group?.memberIds.some(id => {
          const tab = state.tabs.find(item => item.id === id);
          if (tab?.kind !== "file" || !tab.path) return false;
          const key = tab.readOnly ? `attachment:${tab.fileSessionId}:${tab.path}` : `project:${tab.projectId}:${tab.path}`;
          return documentControllers.get(key)?.canReplacePreview() === false;
        })) return {};
      }
      const next = filePreviewLayout(state, sessionId, target, automatic);
      if (!next) return {};
      opened = true;
      const committed = commitCenterTabsState(state, next);
      if ("url" in target) return committed;
      const destination = committed.tabs.find(tab => tab.id === filePreviewId(sessionId, target))!;
      const sequence = (state.fileSectionSequence ?? 0) + 1;
      return { ...committed, fileSectionSequence: sequence,
        fileSectionRequests: requestFileSection(state.fileSectionRequests, destination, section, sequence) };
    });
    return opened;
  };
  return {
    openFilePreview: (sessionId, target, automatic = false) => {
      const { section, ...fileTarget } = target;
      return openPreview(sessionId, fileTarget, automatic, section);
    },
    openPagePreview: (sessionId, target, automatic = false) => openPreview(sessionId, target, automatic),
    openFileTab: (projectId, path, options) =>
      set((s) => {
        const { section, ...fileOptions } = options ?? {};
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
            ...fileOptions,
          }),
          s.tabs.filter(tab => tab.id === s.activeId && (tab.kind === "file" || (tab.kind === "builtin" && tab.page === "files"))).map(tab => tab.id),
        );
        // Reopening the same path must not keep a stale diff/jump
        // context: overwrite it (undefined options clear it) so the
        // tab always reflects the LATEST open.
        const tabs = opened.tabs ?? s.tabs;
        const destination = tabs.find(tab => tab.id === opened.activeId)!;
        const sequence = (s.fileSectionSequence ?? 0) + 1;
        return {
          ...opened,
          fileSectionSequence: sequence,
          fileSectionRequests: requestFileSection(s.fileSectionRequests, destination, section, sequence),
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

    openApplicationTab: (appId, instanceId, title, sessionId) => set((s) => focusOrCreate(
      s, `app:${instanceId}`,
      () => ({ id: `app:${instanceId}`, kind: "application", title, applicationId: appId, applicationInstanceId: instanceId, applicationSessionId: sessionId || undefined }), [],
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

    closeTab: (id, options) =>
      set((s) => {
        const idx = s.tabs.findIndex((t) => t.id === id);
        if (idx < 0) return {};
        const closingTab = s.tabs[idx];
        for (const page of [closingTab, ...(closingTab.pageHistory?.entries ?? [])]) {
          if (page.kind !== "session") continue;
          for (const entry of sessionHistory(page).entries)
            if (entry.sessionId) closedSessionAckTombstones.add(entry.sessionId);
        }
        const retain = options?.retainSessionResources && webTabSessionIds(closingTab).length > 0;
        const tabs = retain
          ? s.tabs.map(tab => tab.id === id ? { ...tab, webPinned: false } : tab)
          : s.tabs.filter(tab => tab.id !== id);
        const groups = normalizeCenterTabLayout({
          tabIds: tabs.filter(tab => tab.id !== id).map(tab => tab.id), groups: s.groups,
        }).groups;
        // Closing one pane of a split leaves focus on the pane beside it,
        // wherever the closed tab happened to sit in the tab list.
        const activeId = focusAfterClose(s, id, tabs, groups);
        const splitWebTabId = s.splitWebTabId === id ? null : s.splitWebTabId;
        // A tab kept for its session's resources is hidden, not closed.
        const closedTabs = retain ? s.closedTabs : rememberClosedTab(s.closedTabs, closingTab, idx);
        return { ...commitCenterTabsState(s, { tabs, groups, activeId, splitWebTabId }), closedTabs };
      }),

    reopenClosedTab: (sessionGone) => {
      let reopened: string | null = null;
      set((s) => {
        const closedTabs = [...s.closedTabs];
        for (let entry = closedTabs.pop(); entry; entry = closedTabs.pop()) {
          const { tab, index } = entry;
          if (tab.kind === "session" && tab.sessionId && sessionGone?.(tab.sessionId)) continue;
          for (const sessionId of closedTabSessionIds(tab)) closedSessionAckTombstones.delete(sessionId);
          // Already open again (the sidebar reopened that session): focus it.
          const existing = s.tabs.find(item => item.id === tab.id
            || (tab.kind === "session" && item.kind === "session" && item.sessionId === tab.sessionId));
          if (existing) {
            reopened = existing.id;
            return { ...commitCenterTabsState(s, { activeId: existing.id }), closedTabs };
          }
          const next = independentTab(tab, s.tabs);
          const at = Math.min(Math.max(0, index), s.tabs.length);
          reopened = next.id;
          return {
            ...commitCenterTabsState(s, { tabs: [...s.tabs.slice(0, at), next, ...s.tabs.slice(at)], activeId: next.id }),
            closedTabs,
          };
        }
        return closedTabs.length === s.closedTabs.length ? {} : { closedTabs };
      });
      return reopened;
    }
  };
}
