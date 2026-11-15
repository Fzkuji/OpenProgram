"use client";

/**
 * Channel binding for one composer — which chat channel (WeChat /
 * Telegram / Discord / Slack account) a conversation's messages are
 * mirrored to. Shared by the channel chip's menu and the composer "+"
 * menu's Channel submenu.
 *
 * A channel is not where the conversation runs; that is its
 * environment. Binding or unbinding a channel never moves its tools.
 *
 * The answer follows the composer's own pane (`useBoundChat`), so a
 * split view's background pane reads and changes its own conversation.
 */
import { useEffect, useState } from "react";
import { create } from "zustand";

import { useBoundChat } from "../bound-chat";
import { mirrorUpsertConv } from "@/lib/runtime-bridge/session/conv-store-mirror";
import {
  draftChannelChoiceFor,
  draftChannelChoiceHost,
  setDraftChannelChoice,
} from "@/lib/runtime-bridge/actions/draft-channel-choice";
import {
  fetchChannelAccounts,
  refreshChannelBadge,
} from "@/lib/runtime-bridge/session/conversations";
import { getSocket, runtimeState } from "@/lib/runtime-bridge/state";
import { useSessionStore } from "@/lib/session-store";

export interface ChannelAccount {
  channel: string;
  account_id: string;
  name?: string;
  enabled?: boolean;
}

export interface ChannelGroup {
  plat: string;
  accounts: ChannelAccount[];
}

// The brand names, colours and logos live in lib/channels/channel-brands.ts.
export { channelBrand } from "@/lib/channels/channel-brands";

/** Draft picks live outside the session store; bumping this re-renders
 *  every chip and menu reading them. */
const useDraftPickTick = create<{ tick: number; bump(): void }>((set) => ({
  tick: 0,
  bump: () => set((s) => ({ tick: s.tick + 1 })),
}));

export interface ChannelChoice {
  channel: string | null;
  account_id: string | null;
}

/** The current binding for this pane's conversation, reactive. */
export function useCurrentChannel(): ChannelChoice {
  const { sessionId, chatKey } = useBoundChat();
  const channel = useSessionStore((s) =>
    sessionId ? s.conversations[sessionId]?.channel ?? null : null);
  const account = useSessionStore((s) =>
    sessionId ? s.conversations[sessionId]?.account_id ?? null : null);
  useDraftPickTick((s) => s.tick);
  if (sessionId) return { channel: channel || null, account_id: account || null };
  const draft = draftChannelChoiceFor(draftChannelChoiceHost, chatKey)
    ?? (chatKey ? null : draftChannelChoiceHost._pendingChannelChoice ?? null);
  return { channel: draft?.channel ?? null, account_id: draft?.account_id ?? null };
}

/** Enabled accounts grouped by platform, plus the pick action. */
export function useChannelChoices() {
  const { sessionId, chatKey } = useBoundChat();
  const current = useCurrentChannel();
  const [rows, setRows] = useState<ChannelAccount[] | null>(null);

  useEffect(() => {
    fetchChannelAccounts().then(
      (r) => setRows(r || []),
      () => setRows([]),
    );
  }, []);

  const groups: ChannelGroup[] = [];
  for (const r of (rows ?? []).filter((x) => x.enabled)) {
    let g = groups.find((x) => x.plat === r.channel);
    if (!g) {
      g = { plat: r.channel, accounts: [] };
      groups.push(g);
    }
    g.accounts.push(r);
  }

  /** Bind to (channel, account), or unbind with ("", ""). */
  function pick(ch: string, acct: string) {
    if (sessionId) {
      const sock = getSocket();
      if (sock && sock.readyState === WebSocket.OPEN) {
        sock.send(JSON.stringify({
          action: "set_conversation_channel",
          session_id: sessionId,
          channel: ch,
          account_id: acct,
        }));
      }
      const conv = runtimeState.conversations[sessionId] as
        | { channel?: string | null; account_id?: string | null }
        | undefined;
      if (conv) {
        conv.channel = ch || null;
        conv.account_id = ch && acct ? acct : null;
        // Optimistic: the sidebar row's logo and the chip update now.
        mirrorUpsertConv({ ...conv, id: sessionId });
      }
    } else {
      setDraftChannelChoice(draftChannelChoiceHost, chatKey, {
        channel: ch || null,
        account_id: ch ? acct || null : null,
      });
      useDraftPickTick.getState().bump();
    }
    refreshChannelBadge();
  }

  return { loaded: rows !== null, groups, current, pick };
}
