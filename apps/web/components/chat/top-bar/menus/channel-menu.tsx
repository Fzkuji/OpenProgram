"use client";

/**
 * Channel menu — the rows of the channel chip's dropdown.
 *
 * Laid out like the model picker: one row per chat app that has enabled
 * accounts (its logo and name, the bound account id on the right when
 * this app is the bound one), each opening its accounts in a
 * DropdownMenuSub (hover, click or →; Radix's roving focus and pointer
 * grace). The bound account carries the check. Below them "Disconnect"
 * (only while bound) and "Manage channels". Picking an account mirrors
 * the conversation's messages to it; it never changes where the
 * conversation runs (that is the environment chip beside it).
 *
 * Positioning / click-outside / portal are handled by the shadcn
 * <DropdownMenu> in the chip; this component renders its rows.
 */
import { ChevronRight } from "lucide-react";

import { useTranslation } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { SolarIcon } from "@/components/solar-icons";
import { ChannelLogo } from "@/components/channels/channel-logo";
import {
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
} from "@/components/ui/dropdown-menu";
import {
  CHECK_SLOT_PAD,
  GROUP_LABEL,
  MENU_ITEM_STATES,
  MENU_PANEL,
  MENU_SEPARATOR,
  MENU_TAG,
  itemCls,
} from "@/components/ui/menu/menu-styles";
import { MenuCheck } from "@/components/ui/menu/menu-check";
import {
  channelBrand,
  useChannelChoices,
  type ChannelChoice,
  type ChannelGroup,
} from "./channel-choices";

export function ChannelMenu({ onClose }: { onClose: () => void }) {
  const { loaded, groups, current, pick } = useChannelChoices();
  return (
    <ChannelMenuRows
      loaded={loaded}
      groups={groups}
      current={current}
      onPick={(channel, account) => {
        onClose();
        pick(channel, account);
      }}
      onManage={onClose}
    />
  );
}

/** The menu's rows for a given account list and binding. */
export function ChannelMenuRows({
  loaded,
  groups,
  current,
  onPick,
  onManage,
}: {
  loaded: boolean;
  groups: ChannelGroup[];
  current: ChannelChoice;
  /** Bind to (channel, account), or unbind with ("", ""). */
  onPick: (channel: string, account: string) => void;
  onManage: () => void;
}) {
  const { text } = useTranslation();
  const row = cn(itemCls(false), MENU_ITEM_STATES);

  return (
    <>
      {groups.map((g) => {
        const bound = current.channel === g.plat ? current.account_id : null;
        return (
          <DropdownMenuSub key={g.plat}>
            <DropdownMenuSubTrigger className={row} data-channel-app={g.plat}>
              <ChannelLogo channel={g.plat} size={16} fit="fill" />
              <span className="flex-1 truncate">{channelBrand(g.plat)}</span>
              {bound ? (
                <span className="max-w-[164px] truncate pl-6 text-fs-xs text-text-muted">{bound}</span>
              ) : null}
              <ChevronRight className="menu-row-chevron size-4 shrink-0 text-text-muted" aria-hidden="true" />
            </DropdownMenuSubTrigger>
            <DropdownMenuSubContent className={cn(MENU_PANEL, "text-text-primary")}>
              {g.accounts.map((r) => {
                const active = r.channel === current.channel && r.account_id === current.account_id;
                const meta = r.name && r.name !== r.account_id ? r.name : "";
                return (
                  <DropdownMenuItem
                    key={r.channel + ":" + r.account_id}
                    className={row}
                    onSelect={() => onPick(r.channel, r.account_id)}
                    data-channel-account={r.account_id}
                  >
                    {/* Claude 实测：别名 tag 紧贴账号名左侧成簇，右缘只留勾位。 */}
                    <span className="flex min-w-0 flex-1 items-center gap-[6px]">
                      <span className="truncate">{r.account_id}</span>
                      {meta ? <span className={MENU_TAG}>{meta}</span> : null}
                    </span>
                    {active ? <MenuCheck /> : <span className={CHECK_SLOT_PAD} />}
                  </DropdownMenuItem>
                );
              })}
            </DropdownMenuSubContent>
          </DropdownMenuSub>
        );
      })}

      {loaded && groups.length === 0 ? (
        <div className={`${GROUP_LABEL} whitespace-normal`}>
          {text("No chat app accounts yet.", "还没有可用的聊天软件账号。")}
        </div>
      ) : null}

      <DropdownMenuSeparator className={MENU_SEPARATOR} />
      {current.channel ? (
        <DropdownMenuItem className={row} onSelect={() => onPick("", "")}>
          <SolarIcon name="close-circle" size={16} fit="fill" aria-hidden="true" />
          <span className="flex-1 truncate">{text("Disconnect", "断开")}</span>
        </DropdownMenuItem>
      ) : null}
      <DropdownMenuItem asChild className={cn(row, "no-underline")}>
        <a href="/settings/channels" onClick={onManage}>
          <SolarIcon name="settings-minimalistic" size={16} fit="fill" aria-hidden="true" />
          {/* A plain link to Settings, not a submenu: no chevron. */}
          <span className="flex-1 truncate">{text("Manage channels", "管理渠道")}</span>
        </a>
      </DropdownMenuItem>
    </>
  );
}
