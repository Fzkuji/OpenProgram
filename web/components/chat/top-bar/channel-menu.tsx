"use client";

/**
 * Channel menu — the content of the topbar `<StatusBadge />` popover.
 *
 * Lists the enabled channel accounts (WeChat / Discord / Telegram /
 * Slack) grouped by platform, plus a "Local" row for no binding.
 * Picking one binds the current conversation to that channel
 * (`set_conversation_channel` over WS) — or, for a brand-new chat with
 * no session yet, stashes the choice on `window._pendingChannelChoice`.
 *
 * Positioning / click-outside / portal are handled by the shadcn
 * <Popover> in `index.tsx`; this component just renders the rows.
 */
import { useEffect, useState } from "react";
import { Check, ChevronRight } from "lucide-react";

import { useSessionStore } from "@/lib/session-store";
import { mirrorUpsertConv } from "@/lib/runtime-bridge/conv-store-mirror";
import { useTranslation } from "@/lib/i18n";
import { Badge } from "@/components/ui/badge";
import { SettingsIcon } from "@/components/animated-icons";
import {
  CHECK_SLOT,
  CHECK_SLOT_PAD,
  GROUP_LABEL,
  MENU_PANEL,
  MENU_SEPARATOR,
  itemCls,
} from "./menu-styles";

interface ChannelAccount {
  channel: string;
  account_id: string;
  name?: string;
  enabled?: boolean;
}

interface ChannelWindow {
  fetchChannelAccounts?: () => Promise<ChannelAccount[]>;
  _currentChannelChoice?: () => { channel: string | null; account_id: string | null };
  _channelIcon?: (plat: string) => string;
  refreshChannelBadge?: () => void;
  conversations?: Record<string, { channel?: string | null; account_id?: string | null }>;
  _pendingChannelChoice?: { channel: string | null; account_id: string | null } | null;
  ws?: WebSocket;
}

const BRAND: Record<string, string> = {
  wechat: "WeChat",
  discord: "Discord",
  telegram: "Telegram",
  slack: "Slack",
};

function brandFor(plat: string): string {
  return BRAND[plat.toLowerCase()] || plat;
}

export function ChannelMenu({ onClose }: { onClose: () => void }) {
  const { text } = useTranslation();
  const sessionId = useSessionStore((s) => s.currentSessionId);
  const [rows, setRows] = useState<ChannelAccount[] | null>(null);

  useEffect(() => {
    const f = (window as unknown as ChannelWindow).fetchChannelAccounts;
    if (f) {
      f().then(
        (r) => setRows(r || []),
        () => setRows([]),
      );
    } else {
      setRows([]);
    }
  }, []);

  const w = window as unknown as ChannelWindow;
  const cur = w._currentChannelChoice?.() ?? {
    channel: null,
    account_id: null,
  };

  function pick(ch: string, acct: string) {
    onClose();
    if (sessionId) {
      if (w.ws && w.ws.readyState === WebSocket.OPEN) {
        w.ws.send(
          JSON.stringify({
            action: "set_conversation_channel",
            session_id: sessionId,
            channel: ch,
            account_id: acct,
          }),
        );
      }
      const conv = w.conversations?.[sessionId];
      if (conv) {
        conv.channel = ch || null;
        conv.account_id = ch && acct ? acct : null;
        // Mirror the optimistic channel binding into the store so the
        // sidebar row's channel prefix updates instantly too.
        mirrorUpsertConv({ ...conv, id: sessionId });
      }
    } else {
      w._pendingChannelChoice = {
        channel: ch || null,
        account_id: ch ? acct || null : null,
      };
    }
    w.refreshChannelBadge?.();
  }

  // Group accounts by platform, preserving first-seen order.
  const enabled = (rows ?? []).filter((r) => r.enabled);
  const groups: { plat: string; accounts: ChannelAccount[] }[] = [];
  for (const r of enabled) {
    let g = groups.find((x) => x.plat === r.channel);
    if (!g) {
      g = { plat: r.channel, accounts: [] };
      groups.push(g);
    }
    g.accounts.push(r);
  }

  return (
    <div className={`${MENU_PANEL} min-w-[300px] max-w-[480px]`}>
      {/* Grammar-A header naming the dimension. Platform names below
          stay as sub-section labels. */}
      <div className={GROUP_LABEL}>{text("Channel", "渠道")}</div>

      {/* 选中不铺底色（hover 是唯一底色），选中态只靠右侧勾。 */}
      <div className={itemCls(false)} onClick={() => pick("", "")}>
        <span className="flex-1 truncate">{text("Local", "本地")}</span>
        {!cur.channel ? (
          <Check size={14} className={CHECK_SLOT} />
        ) : (
          <span className={CHECK_SLOT_PAD} />
        )}
      </div>

      {groups.map((g) => (
        <div key={g.plat}>
          <div className={GROUP_LABEL}>
            <span
              className="provider-icon"
              style={{ width: 14, height: 14 }}
              dangerouslySetInnerHTML={{
                __html: w._channelIcon?.(g.plat) ?? "",
              }}
            />
            <span>{brandFor(g.plat)}</span>
          </div>
          {g.accounts.map((r) => {
            const active =
              r.channel === cur.channel && r.account_id === cur.account_id;
            const meta = r.name && r.name !== r.account_id ? r.name : "";
            return (
              <div
                key={r.channel + ":" + r.account_id}
                className={itemCls(false)}
                onClick={() => pick(r.channel, r.account_id)}
              >
                <span className="flex-1 truncate">{r.account_id}</span>
                {/* 右侧弱化元数据（账号别名）排在勾之前。 */}
                {meta ? (
                  <Badge
                    variant="secondary"
                    className="h-[18px] shrink-0 rounded-[4px] px-[5px] py-0 text-[12px] font-normal text-[var(--text-secondary)]"
                  >
                    {meta}
                  </Badge>
                ) : null}
                {active ? (
                  <Check size={14} className={CHECK_SLOT} />
                ) : (
                  <span className={CHECK_SLOT_PAD} />
                )}
              </div>
            );
          })}
        </div>
      ))}

      {/* Grammar-B action row：设置图标 + 文案 + ChevronRight，常驻
          菜单底部，hover 才上强调色。 */}
      <div className={MENU_SEPARATOR} />
      <a
        href="/settings"
        onClick={onClose}
        className={`${itemCls(false)} no-underline`}
      >
        <SettingsIcon size={16} className="shrink-0" aria-hidden="true" />
        <span className="flex-1 truncate">
          {text("Add a channel in Settings", "在设置中添加渠道")}
        </span>
        <ChevronRight size={14} className="shrink-0 text-text-muted" />
      </a>
    </div>
  );
}
