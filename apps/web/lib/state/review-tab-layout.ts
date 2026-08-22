import {
  findCenterTabGroup,
  focusCenterTabGroupMember,
  groupCenterTabs,
  type CenterTabGroup,
} from "./center-tab-groups.ts";
import { reviewTabId, sessionTabId } from "./center-tab-ids.ts";
import type { CenterTab } from "./center-tabs-store.ts";

type ReviewScope = "turn" | "branch" | "workspace";

export function openReviewTabLayout(
  tabsBefore: readonly CenterTab[],
  groupsBefore: readonly CenterTabGroup[],
  sessionId: string,
  assistantMsgId?: string,
  scope: ReviewScope = "turn",
  path?: string,
): { id: string; tabs: CenterTab[]; groups: CenterTabGroup[] } {
  const id = reviewTabId(sessionId, assistantMsgId);
  const context = {
    reviewSessionId: sessionId,
    reviewMsgId: assistantMsgId,
    reviewScope: scope,
    reviewPath: path,
  };
  let tabs = tabsBefore.some((tab) => tab.id === id)
    ? tabsBefore.map((tab) => tab.id === id ? { ...tab, ...context } : tab)
    : [...tabsBefore, {
        id, kind: "builtin" as const, title: "", page: "review" as const,
        ...context,
      }];
  let groups = [...groupsBefore];
  const chatId = sessionTabId(sessionId);
  const reviewGroup = findCenterTabGroup(groups, id);
  const chatGroup = findCenterTabGroup(groups, chatId);
  if (!reviewGroup && !chatGroup && tabs.some((tab) => tab.id === chatId)) {
    const grouped = groupCenterTabs({
      tabIds: tabs.map((tab) => tab.id),
      groups,
    }, id, chatId, 1, `g:review:${sessionId}:${assistantMsgId || "branch"}`);
    if (grouped.accepted) {
      const byId = new Map(tabs.map((tab) => [tab.id, tab]));
      tabs = grouped.layout.tabIds.flatMap((tabId) => {
        const tab = byId.get(tabId);
        return tab ? [tab] : [];
      });
      groups = grouped.layout.groups;
    }
  } else if (reviewGroup) {
    const focused = focusCenterTabGroupMember({
      tabIds: tabs.map((tab) => tab.id),
      groups,
    }, reviewGroup.id, id);
    groups = focused.groups;
  }
  return { id, tabs, groups };
}
