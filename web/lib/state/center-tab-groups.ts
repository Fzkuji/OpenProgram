export const MAX_CENTER_TAB_GROUP_MEMBERS = 3;
export const MAX_CENTER_TAB_GROUP_VISIBLE = 2;

export interface CenterTabGroup {
  id: string;
  memberIds: string[];
  visibleIds: string[];
  focusedId: string;
}

export interface CenterTabLayout {
  tabIds: string[];
  groups: CenterTabGroup[];
}

export type CenterTabStripEntry =
  | { id: `tab:${string}`; kind: "tab"; tabId: string }
  | { id: `group:${string}`; kind: "group"; group: CenterTabGroup };

const unique = (ids: readonly string[]) => Array.from(new Set(ids));

function moveBlockBefore(
  tabIds: readonly string[],
  block: readonly string[],
  beforeId: string | null,
): string[] {
  const blockSet = new Set(block);
  const remaining = tabIds.filter((id) => !blockSet.has(id));
  const index = beforeId === null ? remaining.length : remaining.indexOf(beforeId);
  const at = index < 0 ? remaining.length : index;
  return [...remaining.slice(0, at), ...block, ...remaining.slice(at)];
}

export function normalizeCenterTabLayout(layout: CenterTabLayout): CenterTabLayout {
  let tabIds = unique(layout.tabIds);
  const alive = new Set(tabIds);
  const claimed = new Set<string>();
  const claimedGroupIds = new Set<string>();
  const groups: CenterTabGroup[] = [];
  for (const candidate of layout.groups) {
    if (claimedGroupIds.has(candidate.id)) continue;
    const memberIds = unique(candidate.memberIds).filter(
      (id) => alive.has(id) && !claimed.has(id),
    ).slice(0, MAX_CENTER_TAB_GROUP_MEMBERS);
    if (memberIds.length < 2) continue;
    claimedGroupIds.add(candidate.id);
    const memberSet = new Set(memberIds);
    const first = Math.min(...memberIds.map((id) => tabIds.indexOf(id)));
    const beforeId = tabIds.slice(first).find((id) => !memberSet.has(id)) ?? null;
    tabIds = moveBlockBefore(tabIds, memberIds, beforeId);
    memberIds.forEach((id) => claimed.add(id));
    let visibleIds = unique(candidate.visibleIds)
      .filter((id) => memberIds.includes(id))
      .slice(0, MAX_CENTER_TAB_GROUP_VISIBLE);
    if (visibleIds.length === 0) {
      visibleIds = memberIds.slice(0, MAX_CENTER_TAB_GROUP_VISIBLE);
    }
    const focusedId = visibleIds.includes(candidate.focusedId)
      ? candidate.focusedId
      : visibleIds[0];
    groups.push({ ...candidate, memberIds, visibleIds, focusedId });
  }
  return { tabIds, groups };
}

export function findCenterTabGroup(
  groups: readonly CenterTabGroup[],
  tabId: string,
): CenterTabGroup | undefined {
  return groups.find((group) => group.memberIds.includes(tabId));
}

export function focusCenterTabGroupMember(
  layout: CenterTabLayout,
  groupId: string,
  memberId: string,
): CenterTabLayout {
  return normalizeCenterTabLayout({
    ...layout,
    groups: layout.groups.map((group) => {
      if (group.id !== groupId || !group.memberIds.includes(memberId)) return group;
      if (group.visibleIds.includes(memberId)) {
        return { ...group, focusedId: memberId };
      }
      const replaceAt = Math.max(0, group.visibleIds.indexOf(group.focusedId));
      const visibleIds = [...group.visibleIds];
      visibleIds[replaceAt] = memberId;
      return { ...group, visibleIds, focusedId: memberId };
    }),
  });
}

export function ungroupCenterTab(
  layout: CenterTabLayout,
  tabId: string,
  beforeId: string | null = null,
): CenterTabLayout {
  const source = findCenterTabGroup(layout.groups, tabId);
  if (!source) {
    return beforeId === null ? layout : moveCenterTab(layout, tabId, beforeId);
  }
  const groups = layout.groups.flatMap((group) => {
    if (group.id !== source.id) return [group];
    const memberIds = group.memberIds.filter((id) => id !== tabId);
    if (memberIds.length < 2) return [];
    const visibleIds = group.visibleIds.filter((id) => id !== tabId);
    return [{
      ...group,
      memberIds,
      visibleIds,
      focusedId: visibleIds.includes(group.focusedId)
        ? group.focusedId
        : visibleIds[0] ?? memberIds[0],
    }];
  });
  const tabIds = beforeId === null
    ? layout.tabIds
    : moveBlockBefore(layout.tabIds, [tabId], beforeId);
  return normalizeCenterTabLayout({ tabIds, groups });
}

export function moveCenterTab(
  layout: CenterTabLayout,
  tabId: string,
  beforeId: string | null,
): CenterTabLayout {
  if (beforeId === tabId
      || !layout.tabIds.includes(tabId)
      || (beforeId !== null && !layout.tabIds.includes(beforeId))) {
    return layout;
  }
  const ungrouped = ungroupCenterTab(layout, tabId);
  return normalizeCenterTabLayout({
    ...ungrouped,
    tabIds: moveBlockBefore(ungrouped.tabIds, [tabId], beforeId),
  });
}

export function moveCenterTabGroup(
  layout: CenterTabLayout,
  groupId: string,
  beforeId: string | null,
): CenterTabLayout {
  const group = layout.groups.find((candidate) => candidate.id === groupId);
  if (!group || (beforeId !== null
      && (!layout.tabIds.includes(beforeId) || group.memberIds.includes(beforeId)))) {
    return layout;
  }
  return normalizeCenterTabLayout({
    ...layout,
    tabIds: moveBlockBefore(layout.tabIds, group.memberIds, beforeId),
  });
}

export function groupCenterTabs(
  layout: CenterTabLayout,
  sourceId: string,
  targetId: string,
  memberIndex: number,
  newGroupId: string,
): { layout: CenterTabLayout; accepted: boolean } {
  if (sourceId === targetId
      || !layout.tabIds.includes(sourceId)
      || !layout.tabIds.includes(targetId)) {
    return { layout, accepted: false };
  }
  const targetBefore = findCenterTabGroup(layout.groups, targetId);
  if (targetBefore && !targetBefore.memberIds.includes(sourceId)
      && targetBefore.memberIds.length >= MAX_CENTER_TAB_GROUP_MEMBERS) {
    return { layout, accepted: false };
  }
  if (targetBefore?.memberIds.includes(sourceId)) {
    const memberIds = targetBefore.memberIds.filter((id) => id !== sourceId);
    const at = Math.max(0, Math.min(memberIndex, memberIds.length));
    memberIds.splice(at, 0, sourceId);
    const memberSet = new Set(memberIds);
    const targetAt = Math.min(...memberIds.map((id) => layout.tabIds.indexOf(id)));
    const beforeId = layout.tabIds.slice(Math.max(0, targetAt))
      .find((id) => !memberSet.has(id)) ?? null;
    return {
      accepted: true,
      layout: normalizeCenterTabLayout({
        tabIds: moveBlockBefore(layout.tabIds, memberIds, beforeId),
        groups: layout.groups.map((group) => group.id === targetBefore.id
          ? { ...group, memberIds }
          : group),
      }),
    };
  }
  const detached = ungroupCenterTab(layout, sourceId);
  const targetGroup = findCenterTabGroup(detached.groups, targetId);
  const targetMembers = targetGroup?.memberIds ?? [targetId];
  const at = Math.max(0, Math.min(memberIndex, targetMembers.length));
  const memberIds = [...targetMembers];
  memberIds.splice(at, 0, sourceId);
  if (memberIds.length > MAX_CENTER_TAB_GROUP_MEMBERS) {
    return { layout, accepted: false };
  }
  const group: CenterTabGroup = targetGroup
    ? { ...targetGroup, memberIds }
    : {
        id: targetBefore?.id ?? newGroupId,
        memberIds,
        visibleIds: memberIds.slice(0, MAX_CENTER_TAB_GROUP_VISIBLE),
        focusedId: targetId,
      };
  const groups = targetGroup
    ? detached.groups.map((candidate) => candidate.id === group.id ? group : candidate)
    : [...detached.groups, group];
  const memberSet = new Set(memberIds);
  const targetAt = Math.min(...targetMembers.map((id) => detached.tabIds.indexOf(id)));
  const beforeId = detached.tabIds.slice(Math.max(0, targetAt))
    .find((id) => !memberSet.has(id)) ?? null;
  return {
    accepted: true,
    layout: normalizeCenterTabLayout({
      tabIds: moveBlockBefore(detached.tabIds, memberIds, beforeId),
      groups,
    }),
  };
}

export function centerTabStripEntries(layout: CenterTabLayout): CenterTabStripEntry[] {
  const firstToGroup = new Map(layout.groups.map((group) => [group.memberIds[0], group]));
  const grouped = new Set(layout.groups.flatMap((group) => group.memberIds));
  return layout.tabIds.flatMap<CenterTabStripEntry>((tabId) => {
    const group = firstToGroup.get(tabId);
    if (group) return [{ id: `group:${group.id}` as const, kind: "group" as const, group }];
    if (grouped.has(tabId)) return [];
    return [{ id: `tab:${tabId}` as const, kind: "tab" as const, tabId }];
  });
}
