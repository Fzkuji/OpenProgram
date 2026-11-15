import type { StoreApi } from "zustand";
import type { CenterTabsState } from "../types";
import { commitCenterTabsState } from "../core";
import { findCenterTabGroup, placeDockedTabs, ungroupCenterTab } from "../../center-tab-groups";
import { dockLayout, leaves, rowLayout, sanitizeLayout } from "../../canvas/canvas-layout";

export function canvasActions(set: StoreApi<CenterTabsState>["setState"]): Pick<CenterTabsState, "setCanvas" | "previewCanvas" | "dockCanvas"> {
  return {
    setCanvas: (targetId, canvas) => set(s => {
      const group = findCenterTabGroup(s.groups, targetId);
      canvas = sanitizeLayout(canvas, new Set(s.tabs.map(t => t.id)));
      const memberIds = leaves(canvas.root).flatMap(p => p.content ? [p.content] : []);
      let tabs = s.tabs;
      if (!memberIds.length) {
        const existing = s.tabs.find(t => t.canvasAnchor && t.id === targetId);
        const anchor = existing ?? { id: `canvas-empty:${crypto.randomUUID()}`, kind: "ntp" as const, title: "Layout", canvasAnchor: true };
        if (!existing) tabs = [...tabs, anchor];
        memberIds.push(anchor.id);
      }
      const focusedId = leaves(canvas.root).find(p => p.id === canvas.focusedPaneId)?.content ?? (memberIds.includes(targetId) ? targetId : memberIds[0]);
      const next = { id: group?.id ?? `g:${crypto.randomUUID()}`, memberIds, visibleIds: memberIds, focusedId, canvas };
      let groups = s.groups.filter(g => g.id !== group?.id);
      for (const id of memberIds) groups = ungroupCenterTab({ tabIds: tabs.map(t => t.id), groups }, id).groups;
      groups.push(next);
      tabs = tabs.filter(t => !t.canvasAnchor || groups.some(g => g.memberIds.includes(t.id)));
      return commitCenterTabsState(s, { tabs, groups, activeId: focusedId ?? s.activeId, splitWebTabId: null });
    }),
    // A divider drag repaints every frame. Change only the in-memory tree;
    // no normalization, persistence, focus or route change until setCanvas
    // commits the final sizes on release.
    previewCanvas: (targetId, root) => set(s => {
      const group = findCenterTabGroup(s.groups, targetId);
      if (!group?.canvas) return {};
      return { groups: s.groups.map(g => g === group ? { ...g, canvas: { ...group.canvas!, root } } : g) };
    }),
    dockCanvas: (source, targetId, paneId, side) => set(s => {
      const sourceIds = typeof source === "string" ? [source] : source;
      const sourceId = sourceIds[0];
      if (!sourceId || sourceIds.some(id => !s.tabs.some(t => t.id === id)) || !s.tabs.some(t => t.id === targetId)) return {};
      const targetGroup = findCenterTabGroup(s.groups, targetId);
      const sourceGroup = findCenterTabGroup(s.groups, sourceId);
      if (sourceIds.length > 1 && sourceGroup?.id === targetGroup?.id) return {};
      const canvas = dockLayout(targetGroup?.canvas ?? rowLayout([targetId]), sourceIds, paneId, side, sourceGroup?.canvas);
      if (!canvas) return {};
      let detached = { tabIds: s.tabs.map(t => t.id), groups: s.groups };
      for (const id of sourceIds) detached = ungroupCenterTab(detached, id);
      const memberIds = leaves(canvas.root).flatMap(p => p.content ? [p.content] : []);
      const group = { id: targetGroup?.id ?? `g:${crypto.randomUUID()}`, memberIds, visibleIds: memberIds, focusedId: sourceId, canvas };
      const groups = [...detached.groups.filter(g => g.id !== group.id), group];
      const ordered = placeDockedTabs(s.tabs, sourceIds, targetGroup?.memberIds[0] ?? targetId);
      const tabs = ordered.filter(t => !t.canvasAnchor || groups.some(g => g.memberIds.includes(t.id)));
      return commitCenterTabsState(s, { tabs, groups, activeId: sourceId, splitWebTabId: null });
    }),
  };
}
