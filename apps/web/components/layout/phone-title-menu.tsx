"use client";

/**
 * The phone navigation bar's title (phone only; app/styles/base/phone.css
 * hides `.phone-only` above 760px). On a conversation it opens the
 * conversation's menu: its files, activity and resources, which open the
 * right drawer at that view, the context graph (the chat pane's floating
 * controls are hidden on a phone), then the chat pane's own actions
 * (components/chat/pane/session-actions-menu.tsx). Elsewhere it is the
 * page's title.
 */

import { ChevronDown } from "lucide-react";

import { SessionActionsMenu } from "@/components/chat/pane/session-actions-menu";
import { labelOf } from "@/components/center-tabs/strip/tab-label";
import { DropdownMenuItem } from "@/components/ui/dropdown-menu";
import { MENU_ITEM_STATES, itemCls } from "@/components/ui/menu/menu-styles";
import { MenuRow, type MenuGlyph } from "@/components/ui/menu/menu-parts";
import { useTranslation } from "@/lib/i18n";
import { useSessionStore } from "@/lib/session-store";
import { useCenterTabs } from "@/lib/tabs/center-tabs-store";
import { cn } from "@/lib/utils";

/** The right panel's views, as RightSidebar names them. */
const PANELS: { view: string; glyph: MenuGlyph; en: string; zh: string }[] = [
  { view: "files", glyph: "folder-open", en: "Files", zh: "文件" },
  { view: "running", glyph: "history", en: "Activity", zh: "活动" },
  { view: "resources", glyph: "box", en: "Resources", zh: "资源" },
];

function openPanel(view: string) {
  const store = useSessionStore.getState();
  store.setRightDockView(view);
  store.setRightDockOpen(true);
}

export function PhoneTitleMenu() {
  const { t, text } = useTranslation();
  const tab = useCenterTabs((s) => s.tabs.find((x) => x.id === s.activeId));
  const setTabDagView = useCenterTabs((s) => s.setTabDagView);
  if (!tab) return <div className="phone-only phone-nav-title" />;
  const label = labelOf(tab, t, text);
  if (tab.kind !== "session") {
    return <div className="phone-only phone-nav-title"><span className="phone-nav-title-text">{label}</span></div>;
  }
  return (
    <div className="phone-only phone-nav-title">
      <SessionActionsMenu
        align="center"
        compact
        leading={<>
          {PANELS.map((panel) => (
            <DropdownMenuItem
              key={panel.view}
              className={cn(itemCls(false), MENU_ITEM_STATES)}
              onSelect={() => openPanel(panel.view)}
            >
              <MenuRow glyph={panel.glyph} label={text(panel.en, panel.zh)} />
            </DropdownMenuItem>
          ))}
          <DropdownMenuItem
            className={cn(itemCls(false), MENU_ITEM_STATES)}
            onSelect={() => setTabDagView(tab.id, !tab.dagView)}
          >
            {tab.dagView
              ? <MenuRow glyph="chat-round-line" label={text("Conversation", "对话")} />
              : <MenuRow glyph="git-graph" label={text("Context graph", "上下文图")} />}
          </DropdownMenuItem>
        </>}
      >
        <button type="button" className="phone-nav-title-button" aria-label={text(`${label}, conversation menu`, `${label}，对话菜单`)}>
          <span className="phone-nav-title-text">{label}</span>
          <ChevronDown size={15} aria-hidden="true" />
        </button>
      </SessionActionsMenu>
    </div>
  );
}
