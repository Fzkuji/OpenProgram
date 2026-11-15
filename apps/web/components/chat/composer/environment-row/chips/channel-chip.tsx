"use client";

import { useEffect, useState } from "react";

import { SolarIcon } from "@/components/solar-icons";
import { ChannelLogo } from "@/components/channels/channel-logo";
import { HoverTip, TipBody } from "@/components/ui/tooltip";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { MENU_PANEL } from "@/components/ui/menu/menu-styles";
import { buttonVariants } from "@/components/ui/button";
import { useSessionStore } from "@/lib/session-store";
import { closeAllPopovers } from "@/lib/runtime-bridge/ui";
import { useTranslation } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { channelChipText } from "@/lib/channels/channel-brands";
import { useCurrentChannel } from "../../../top-bar/menus/channel-choices";
import { ChannelMenu } from "../../../top-bar/menus/channel-menu";

/** Channel chip — which chat channel account this conversation's
 *  messages are mirrored to, beside the environment chip. Always shown:
 *  unbound reads "No channel" beside the chat glyph; bound shows the
 *  app's logo and the account id (the logo alone when the row squeezes
 *  the chip to its icon). The channel poll's health is a dot on the
 *  logo's corner (yellow degraded, red unreachable). The dropdown binds,
 *  switches or disconnects. */
export function ChannelChip() {
  const { text } = useTranslation();
  const { channel, account_id } = useCurrentChannel();
  const health = useSessionStore((s) => s.channelHealth);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const close = () => setOpen(false);
    window.addEventListener("topbar-close-menus", close);
    return () => window.removeEventListener("topbar-close-menus", close);
  }, []);

  function onOpenChange(next: boolean) {
    if (next) {
      window.dispatchEvent(new Event("topbar-close-menus"));
      closeAllPopovers();
    }
    setOpen(next);
  }

  const chip = channelChipText(channel, account_id);
  const label = chip.label ?? text("No channel", "未接渠道");
  const tipName = chip.tip ?? label;
  const dot =
    !channel ? undefined
    : health === "err" ? "var(--accent-red)" : health === "warn" ? "var(--accent-yellow)" : undefined;
  const healthText =
    !channel ? ""
    : health === "err"
      ? text("The channel is unreachable.", "渠道连接不上。")
      : health === "warn"
        ? text("The channel is degraded.", "渠道状态异常。")
        : "";

  return (
    <DropdownMenu open={open} onOpenChange={onOpenChange} modal={false}>
      <HoverTip
        label={
          <TipBody
            title={text(`Channel: ${tipName}`, `渠道：${tipName}`)}
            detail={[
              channel
                ? text("Messages are mirrored to this account. Click to change or disconnect.",
                       "消息会同步到这个账号。点击切换或断开。")
                : text("Not mirrored to any chat app. Click to connect WeChat, Telegram, Discord or Slack.",
                       "没有同步到聊天软件。点击绑定微信、Telegram、Discord 或 Slack。"),
              healthText,
            ].filter(Boolean).join(" ")}
          />
        }
      >
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            className={cn(buttonVariants({ variant: "elevated", size: "sm" }), "status-badge")}
            data-channel-chip=""
            aria-label={text(`Channel: ${tipName}`, `渠道：${tipName}`)}
          >
            {channel ? (
              <span className="relative flex shrink-0">
                <ChannelLogo channel={channel} size={16} fit="fill" />
                {dot ? (
                  // Health on the logo's corner, ringed in the chip's own
                  // fill so it reads as a badge on the mark.
                  <span
                    aria-hidden="true"
                    data-channel-health={health}
                    className="absolute -right-[2px] -bottom-[2px] size-[6px] rounded-full ring-[1.5px] ring-bg-input"
                    style={{ backgroundColor: dot }}
                  />
                ) : null}
              </span>
            ) : (
              <SolarIcon name="chat-round-dots" size={16} fit="fill" aria-hidden="true" />
            )}
            <span className="badge-short">{label}</span>
          </button>
        </DropdownMenuTrigger>
      </HoverTip>
      <DropdownMenuContent
        side="top"
        align="start"
        /* Same gap as the model chip's menu: 10px band gap − 1px ring. */
        sideOffset={9}
        onCloseAutoFocus={(e) => e.preventDefault()}
        className={cn(MENU_PANEL, "text-text-primary")}
      >
        <ChannelMenu onClose={() => setOpen(false)} />
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
