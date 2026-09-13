import type { CenterTabGroup } from "@/lib/tabs/center-tab-groups";
import { MAX_CENTER_TAB_GROUP_MEMBERS, findCenterTabGroup } from "@/lib/tabs/center-tab-groups";
import type { CenterTabsPersistedPayload } from "@/lib/tabs/center-tabs-persistence";
import { normalizeCenterTabsPayload, orderTabs, persistCenterTabsPayload, persistedState } from "@/lib/tabs/center-tabs-persistence";
import type { DesktopTransferPayload, TabDropPlacement } from "@/lib/tabs/tab-transfer-journal";
import type { StoreApi } from "zustand";
import { pageHistory } from "../navigation/page-history";
import { fileHistoryFor } from "../navigation/selectors";
import { sessionHistory } from "../navigation/session-history";
import type { CenterTab, CenterTabsState } from "./types";

export function bindTransfers(useCenterTabs: StoreApi<CenterTabsState>, closedSessionAckTombstones: Set<string>) {
  function restoreState(payload: CenterTabsPersistedPayload) {
    const active = payload.tabs.find(tab => tab.id === payload.activeId);
    return {
      ...persistedState(payload), navigationRoute: active?.navigationRoute,
      fileNavigationHistory: fileHistoryFor(active), fileNavigationRestore: active?.fileNavigationSnapshot ?? null
    };
  }

  function sessionAckIsActive(sessionId: string): boolean {
    const state = useCenterTabs.getState();
    const active = state.tabs.find(tab => tab.id === state.activeId);
    if (active?.kind === "session" && active.sessionId === sessionId) return true;
    if (closedSessionAckTombstones.has(sessionId)) return false;
    const hasTab = state.tabs.some(tab => [tab, ...(tab.pageHistory?.entries ?? [])].some(page => page.kind === "session"
      && sessionHistory(page).entries.some(entry => entry.sessionId === sessionId)));
    return !hasTab && !sessionId.startsWith("local_");
  }

  function snapshotCenterTabsPayload(): CenterTabsPersistedPayload {
    const state = useCenterTabs.getState();
    return normalizeCenterTabsPayload({
      tabs: state.tabs,
      activeId: state.activeId,
      groups: state.groups,
      splitWebTabId: state.splitWebTabId,
      splitRatio: state.splitRatio,
    });
  }

  function sameIds(left: readonly string[], right: readonly string[]): boolean {
    return left.length === right.length
      && left.every((id, index) => id === right[index]);
  }

  function sourceGroup(
    payload: DesktopTransferPayload,
  ): CenterTabGroup | null {
    if (payload.source.kind === "tab") return null;
    const transferred = new Set(payload.tabs.map((tab) => tab.id));
    const sourceMemberIds = payload.source.memberIds
      ?? payload.tabs.map((tab) => tab.id);
    const memberIds = payload.source.kind === "segment"
      ? sourceMemberIds.filter((id) => transferred.has(id))
      : sourceMemberIds;
    if (memberIds.length < 2) return null;
    const visibleIds = (payload.source.visibleIds ?? memberIds.slice(0, 2))
      .filter((id) => transferred.has(id));
    return {
      id: payload.source.groupId ?? `g:transfer:${memberIds.join(":")}`,
      memberIds,
      visibleIds,
      focusedId: payload.source.focusedId ?? visibleIds[0] ?? memberIds[0],
    };
  }

  function transferredActiveId(payload: DesktopTransferPayload): string | null {
    const activeChat = payload.chats.find((chat) => chat.wasActive)?.chatKey;
    return payload.tabs.find((tab) => tab.sessionId === activeChat)?.id
      ?? payload.source.focusedId
      ?? null;
  }

  /**
   * A lone unmodified placeholder — an ntp "New tab" page or an empty draft
   * "New chat" session (draft:true, still title "", never navigated). Same
   * consumable predicate the store uses when navigating an active ntp/draft
   * replaces it in place rather than adding a tab.
   */
  function isConsumablePlaceholder(tab: CenterTab): boolean {
    return tab.kind === "ntp" || (tab.kind === "session" && tab.draft === true);
  }

  function transferredPayloadAfter(
    before: CenterTabsPersistedPayload,
    payload: DesktopTransferPayload,
    placement: TabDropPlacement,
  ): CenterTabsPersistedPayload | null {
    // A tab delivered into a fresh window (its sole tab an empty placeholder)
    // must consume that placeholder, not sit beside it. Consume ONLY on a plain
    // strip-end append: the incoming tabs replace the placeholder outright and
    // the result is well-formed (delivered tabs inserted from an empty base, one
    // becomes active, no dangling group/target left behind). A positioned drop
    // (before/after/merge) targets the placeholder itself, so it must survive —
    // consuming it would leave the placement referencing a tab that no longer
    // exists, which is exactly the "delivered tab disappears" report.
    if (
      placement.kind === "strip-end" &&
      placement.consumePlaceholder === true &&
      before.tabs.length === 1 &&
      isConsumablePlaceholder(before.tabs[0])
    ) {
      before = { ...before, tabs: [], groups: [], activeId: null };
    }
    const transferIds = payload.tabs.map((tab) => tab.id);
    const allTabs = [...before.tabs, ...payload.tabs];
    const targetId = placement.kind === "strip-end" ? null : placement.targetTabId;
    const targetGroup = targetId ? findCenterTabGroup(before.groups, targetId) : undefined;
    let tabIds = before.tabs.map((tab) => tab.id);
    let groups = [...before.groups];

    if (placement.kind === "merge") {
      const targetMembers = targetGroup?.memberIds ?? [placement.targetTabId];
      const at = Math.max(0, Math.min(
        placement.memberIndex ?? targetMembers.indexOf(placement.targetTabId) + 1,
        targetMembers.length,
      ));
      const memberIds = [...targetMembers];
      memberIds.splice(at, 0, ...transferIds);
      const transferGroup = sourceGroup(payload);
      const visibleIds = transferGroup
        ? [
          ...transferGroup.visibleIds,
          ...(targetGroup?.visibleIds ?? [placement.targetTabId]),
        ]
          .filter((id, index, ids) => ids.indexOf(id) === index)
          .slice(0, 2)
        : [
          ...(targetGroup?.visibleIds ?? [placement.targetTabId]),
          ...transferIds,
        ]
          .filter((id, index, ids) => ids.indexOf(id) === index)
          .slice(0, 2);
      const group: CenterTabGroup = {
        id: placement.groupId
          ?? targetGroup?.id
          ?? transferGroup?.id
          ?? `g:transfer:${memberIds.join(":")}`,
        memberIds,
        visibleIds,
        focusedId: transferGroup?.focusedId ?? targetGroup?.focusedId ?? placement.targetTabId,
      };
      groups = targetGroup
        ? groups.map((candidate) => candidate.id === targetGroup.id ? group : candidate)
        : [...groups, group];
      const memberSet = new Set(targetMembers);
      const remaining = tabIds.filter((id) => !memberSet.has(id));
      const targetAt = Math.min(...targetMembers.map((id) => tabIds.indexOf(id)));
      tabIds = [
        ...remaining.slice(0, targetAt),
        ...memberIds,
        ...remaining.slice(targetAt),
      ];
    } else {
      let at = tabIds.length;
      if (targetId) {
        const targetMembers = targetGroup?.memberIds ?? [targetId];
        const positions = targetMembers.map((id) => tabIds.indexOf(id));
        at = placement.kind === "before"
          ? Math.min(...positions)
          : Math.max(...positions) + 1;
      }
      tabIds.splice(at, 0, ...transferIds);
      const group = sourceGroup(payload);
      if (group) groups.push(group);
    }

    return normalizeCenterTabsPayload({
      ...before,
      tabs: orderTabs(allTabs, tabIds),
      groups,
      activeId: transferredActiveId(payload) ?? before.activeId,
    });
  }

  function validateTransferredTabs(
    payload: DesktopTransferPayload,
    placement: TabDropPlacement,
  ):
    | { ok: true; after: CenterTabsPersistedPayload }
    | { ok: false; reason: "duplicate" | "group-full" | "invalid"; duplicateId?: string } {
    const before = snapshotCenterTabsPayload();
    const ids = payload.tabs.map((tab) => tab.id);
    if (ids.length === 0 || new Set(ids).size !== ids.length) {
      return { ok: false, reason: "invalid" };
    }
    const sessionIds = payload.tabs.filter(tab => tab.kind === "session" && tab.sessionId).map(tab => tab.sessionId);
    if (new Set(sessionIds).size !== sessionIds.length) return { ok: false, reason: "invalid" };
    const duplicateId = before.tabs.find(tab => payload.tabs.some(incoming => incoming.id === tab.id
      || (incoming.kind === "session" && tab.kind === "session" && incoming.sessionId && incoming.sessionId === tab.sessionId)))?.id;
    if (duplicateId) return { ok: false, reason: "duplicate", duplicateId };
    if (payload.source.kind === "tab" && ids.length !== 1) {
      return { ok: false, reason: "invalid" };
    }
    if (payload.source.kind === "segment" && ids.length !== 1) {
      return { ok: false, reason: "invalid" };
    }
    if (payload.source.kind !== "tab") {
      const memberIds = payload.source.memberIds;
      if (!memberIds) return { ok: false, reason: "invalid" };
      const visibleIds = payload.source.visibleIds ?? memberIds?.slice(0, 2) ?? [];
      const focusedId = payload.source.focusedId ?? visibleIds[0];
      if (payload.source.kind === "group" && ids.length > MAX_CENTER_TAB_GROUP_MEMBERS) {
        return { ok: false, reason: "group-full" };
      }
      const segmentAt = payload.source.memberIndex;
      const validMembers = payload.source.kind === "segment"
        ? segmentAt !== undefined
        && Number.isInteger(segmentAt)
        && segmentAt >= 0
        && sameIds(memberIds.slice(segmentAt, segmentAt + ids.length), ids)
        : sameIds(memberIds, ids);
      if (
        !validMembers
        || (payload.source.kind === "group" && memberIds.length < 2)
        || visibleIds.some((id) => !memberIds.includes(id))
        || visibleIds.length > 2
        || (focusedId !== undefined && !visibleIds.includes(focusedId))
        || (payload.source.kind !== "segment"
          && payload.source.memberIndex !== undefined
          && (!Number.isInteger(payload.source.memberIndex)
            || payload.source.memberIndex < 0))
      ) return { ok: false, reason: "invalid" };
      if (
        payload.source.groupId
        && (payload.source.kind === "group" || ids.length > 1)
        && before.groups.some((group) => group.id === payload.source.groupId)
      ) return { ok: false, reason: "invalid" };
    }
    if (placement.kind !== "strip-end") {
      const target = before.tabs.find((tab) => tab.id === placement.targetTabId);
      if (!target) return { ok: false, reason: "invalid" };
    }
    if (placement.kind === "merge") {
      const targetGroup = findCenterTabGroup(before.groups, placement.targetTabId);
      if ((targetGroup?.memberIds.length ?? 1) + ids.length > MAX_CENTER_TAB_GROUP_MEMBERS) {
        return { ok: false, reason: "group-full" };
      }
    }
    const after = transferredPayloadAfter(before, payload, placement);
    return after ? { ok: true, after } : { ok: false, reason: "invalid" };
  }

  function insertTransferredTabs(
    payload: DesktopTransferPayload,
    placement: TabDropPlacement,
    _options: { persist: false },
  ): {
    ok: boolean;
    before: CenterTabsPersistedPayload;
    after: CenterTabsPersistedPayload;
  } {
    void _options;
    const before = snapshotCenterTabsPayload();
    const validated = validateTransferredTabs(payload, placement);
    if (!validated.ok) return { ok: false, before, after: before };
    useCenterTabs.setState(restoreState(validated.after));
    return { ok: true, before, after: validated.after };
  }

  function removeTransferredTabs(
    ids: string[],
    _options: { persist: false; expectedTabs?: readonly CenterTab[] },
  ): {
    ok: boolean;
    empty: boolean;
    before: CenterTabsPersistedPayload;
    after: CenterTabsPersistedPayload;
  } {
    void _options;
    const before = snapshotCenterTabsPayload();
    const removed = new Set(ids);
    const valid = ids.length > 0
      && removed.size === ids.length
      && ids.every((id) => before.tabs.some((tab) => tab.id === id))
      && (_options.expectedTabs ?? []).every(expected => {
        const current = before.tabs.find(tab => tab.id === expected.id);
        if (!current) return false;
        const navigation = (tab: CenterTab) => {
          const history = pageHistory(tab);
          return {
            index: history.index, entries: history.entries.map(entry => ({
              id: entry.id, kind: entry.kind, sessionId: entry.sessionId, title: entry.title,
              draft: !!entry.draft, navigationRoute: entry.navigationRoute, page: entry.page,
              path: entry.path, projectId: entry.projectId, url: entry.url,
              fileNavigationSnapshot: entry.fileNavigationSnapshot,
            }))
          };
        };
        return JSON.stringify(navigation(current)) === JSON.stringify(navigation(expected));
      });
    if (!valid) return { ok: false, empty: before.tabs.length === 0, before, after: before };

    const activeIndex = before.tabs.findIndex((tab) => tab.id === before.activeId);
    const tabs = before.tabs.filter((tab) => !removed.has(tab.id));
    const activeId = before.activeId && !removed.has(before.activeId)
      ? before.activeId
      : tabs[activeIndex]?.id ?? tabs[activeIndex - 1]?.id ?? null;
    const after = normalizeCenterTabsPayload({
      ...before,
      tabs,
      activeId,
      groups: before.groups.flatMap((group) => {
        const memberIds = group.memberIds.filter((id) => !removed.has(id));
        if (memberIds.length < 2) return [];
        const visibleIds = group.visibleIds.filter((id) => !removed.has(id));
        return [{
          ...group,
          memberIds,
          visibleIds,
          focusedId: visibleIds.includes(group.focusedId)
            ? group.focusedId
            : visibleIds[0] ?? memberIds[0],
        }];
      }),
      splitWebTabId: before.splitWebTabId && removed.has(before.splitWebTabId)
        ? null
        : before.splitWebTabId,
    });
    useCenterTabs.setState(restoreState(after));
    return { ok: true, empty: tabs.length === 0, before, after };
  }

  function replaceCenterTabsPayload(
    payload: CenterTabsPersistedPayload,
    options: { persist: boolean },
  ): boolean {
    const normalized = normalizeCenterTabsPayload(payload);
    useCenterTabs.setState(restoreState(normalized));
    return options.persist ? persistCenterTabsPayload(normalized) : true;
  }

  function persistCurrentCenterTabsPayload(): boolean {
    return persistCenterTabsPayload(snapshotCenterTabsPayload());
  }

  return { sessionAckIsActive, snapshotCenterTabsPayload, validateTransferredTabs, insertTransferredTabs, removeTransferredTabs, replaceCenterTabsPayload, persistCurrentCenterTabsPayload };
}
