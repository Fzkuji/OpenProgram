"use client";

/**
 * The bell beside the list's filter button, and the ⋯ menu Activity's
 * header carries. The bell switches the sidebar list in place between the
 * project (or date) list and Activity; ⌘⌥U does the same
 * (lib/tabs/navigation/tab-shortcuts.ts). A small amber dot on the bell
 * says a conversation waits for the user.
 */

import { useRef, useState } from "react";
import * as DM from "@radix-ui/react-dropdown-menu";
import { MoreHorizontal } from "lucide-react";

import { BellIcon, type AnimatedNavIconHandle } from "@/components/animated-icons";
import { MenuRow } from "@/components/ui/menu/menu-parts";
import { MENU_ITEM_STATES, MENU_PANEL, itemCls } from "@/components/ui/menu/menu-styles";
import { useTranslation } from "@/lib/i18n";
import { platformKeyCaps } from "@/lib/shortcut-keys";
import { useSessionStore } from "@/lib/session-store";
import { toggleActivityView, useRecentsView } from "@/lib/prefs/recents-view";
import { anyNeedsInput } from "./activity";
import type { LegacyConv } from "./helpers";

// The Recents filter button's box, so the two sit as a pair.
const BUTTON =
  "relative flex size-(--ui-button-h-xs) items-center justify-center rounded-sm" +
  " transition-colors hover:bg-bg-hover outline-none";

const surface =
  MENU_PANEL +
  " z-(--z-popover) text-text-primary" +
  " data-[state=open]:animate-in data-[state=closed]:animate-out" +
  " data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0" +
  " data-[state=closed]:zoom-out-95 data-[state=open]:zoom-in-95";

export function ActivityBell() {
  const { text } = useTranslation();
  const on = useRecentsView().activity;
  const waiting = useSessionStore((s) => anyNeedsInput(s.conversations as Record<string, LegacyConv>));
  const label = text("Activity", "动态");
  const iconRef = useRef<AnimatedNavIconHandle>(null);
  return (
    <button
      type="button"
      aria-label={waiting ? text("Activity: a conversation needs input", "动态：有对话在等待输入") : label}
      aria-pressed={on}
      data-tip={label}
      data-tip-keys={platformKeyCaps("⌘ ⌥ U")}
      onClick={(event) => {
        event.stopPropagation();
        toggleActivityView();
      }}
      onMouseEnter={() => iconRef.current?.startAnimation?.()}
      onMouseLeave={() => iconRef.current?.stopAnimation?.()}
      className={BUTTON + (on ? " bg-bg-hover text-text-secondary" : " text-text-muted")}
    >
      <BellIcon ref={iconRef} size={20} aria-hidden="true" />
      {waiting ? (
        <span
          aria-hidden="true"
          className="absolute right-[3px] top-[3px] size-[6px] rounded-full bg-[color:var(--status-needs-input)]"
        />
      ) : null}
    </button>
  );
}

/** Activity's ⋯: "Mark all as read". */
export function ActivityMenu({ unread, onMarkAllRead }: { unread: number; onMarkAllRead: () => void }) {
  const { text } = useTranslation();
  const [open, setOpen] = useState(false);
  return (
    <DM.Root open={open} onOpenChange={setOpen} modal={false}>
      <DM.Trigger asChild>
        <button
          type="button"
          aria-label={text("Activity options", "动态选项")}
          onClick={(event) => event.stopPropagation()}
          className={BUTTON + (open ? " bg-bg-hover text-text-secondary" : " text-text-muted")}
        >
          <MoreHorizontal size={16} aria-hidden="true" />
        </button>
      </DM.Trigger>
      <DM.Portal>
        <DM.Content align="end" sideOffset={6} className={surface + " min-w-[176px]"} onClick={(event) => event.stopPropagation()}>
          <DM.Item className={itemCls(false) + " " + MENU_ITEM_STATES} disabled={!unread} onSelect={onMarkAllRead}>
            <MenuRow glyph="check-circle" label={text("Mark all as read", "全部标为已读")} />
          </DM.Item>
        </DM.Content>
      </DM.Portal>
    </DM.Root>
  );
}
