"use client";

/**
 * Channels settings — Chat-platform 账号 + 入站路由 (bindings) 管理.
 *
 * 两块内容堆在同一个 section:
 *
 *   1. Accounts: 已配置的 (platform, account_id) 列表 + status badge +
 *      add (token paste) + delete. WeChat 现在仍要走 CLI 扫码 (前端
 *      QR 渲染未做, 指引用户 `openprogram channels accounts login wechat`).
 *
 *   2. Bindings: (channel, account_id, peer?) → agent 路由表 + add/remove.
 *
 * 走 REST HTTP (/api/channels/...) 而非 WS — 这些都是低频操作, HTTP
 * request-response 比订阅 envelope 简洁.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { SettingsPage } from "../settings-ui";
import styles from "./channels.module.css";
import {
  cachedFetch, readCachedSettings, refreshSettings, revalidateSettings, setCachedSettings, useSettingsData,
} from "@/lib/prefs/settings-cache";
import { useTranslation } from "@/lib/i18n";
import { useChanged } from "@/lib/net/ws/changes";
import {
  CHANNEL_ACCESS_URL, CHANNEL_ACCOUNTS_URL, CHANNEL_BINDINGS_URL,
} from "@/lib/settings/settings-urls";
import { AccountsList } from "./accounts-list";
import { AccessList } from "./access-list";
import { GroupUsersList } from "./group-users-list";
import { BindingsList } from "./bindings-list";
import type {
  ChannelAccessAccount, ChannelAccount, ChannelBinding,
  ChannelHealthStatus, StatusMap,
} from "./types";

const NO_ACCOUNTS: ChannelAccount[] = [];

const statusUrl = (a: ChannelAccount) =>
  `/api/channels/${encodeURIComponent(a.channel)}/${encodeURIComponent(a.account_id)}/status`;

const RETRY_FIRST_MS = 1_000;
const RETRY_MAX_MS = 30_000;

const pause = (ms: number) => new Promise<void>((resolve) => { setTimeout(resolve, ms); });

/** Read an account's status again after a read failed, quietly: 1 s, 2 s,
 *  4 s … up to 30 s, until it arrives (true) or the page closes. A 4xx is the
 *  server's answer (the account is gone, the request is wrong), not a load
 *  problem: it is not asked again and the cell stays empty (false). */
export async function readStatusQuietly(
  url: string,
  stopped: () => boolean,
  wait: (ms: number) => Promise<void> = pause,
): Promise<boolean> {
  for (let attempt = 0; ; attempt += 1) {
    await wait(Math.min(RETRY_FIRST_MS * 2 ** attempt, RETRY_MAX_MS));
    if (stopped()) return false;
    let response: Response | null = null;
    try {
      response = await fetch(url);
      if (response.ok) {
        setCachedSettings(url, await response.json());
        return true;
      }
    } catch (error) {
      console.warn(`Channel status ${url} could not be read; trying again quietly`, error);
      continue;
    }
    if (response.status >= 400 && response.status < 500) {
      console.warn(`Channel status ${url} refused (${response.status}); not asked again`);
      return false;
    }
    console.warn(`Channel status ${url} → ${response.status}; trying again quietly`);
  }
}

export function ChannelsSection() {
  const { t, text } = useTranslation();
  // Kept across visits and read as Settings opens: the lists show at once
  // and refresh behind.
  const accountsData = useSettingsData<{ accounts?: ChannelAccount[] }>(CHANNEL_ACCOUNTS_URL);
  const bindingsData = useSettingsData<{ bindings?: ChannelBinding[] }>(CHANNEL_BINDINGS_URL);
  const accessData = useSettingsData<{ accounts?: ChannelAccessAccount[] }>(CHANNEL_ACCESS_URL);
  const accounts = accountsData?.accounts ?? NO_ACCOUNTS;
  const ready = !!accountsData && !!bindingsData && !!accessData;

  // Each account's status is kept too; `statusRead` redraws after a read.
  const [statusRead, setStatusRead] = useState(0);
  const statuses = useMemo<StatusMap>(() => {
    void statusRead;
    const map: StatusMap = {};
    for (const a of accounts) {
      const s = readCachedSettings<ChannelHealthStatus>(statusUrl(a));
      if (s) map[`${a.channel}:${a.account_id}`] = s;
    }
    return map;
  }, [accounts, statusRead]);

  // A status read that fails is tried again quietly while the page is open.
  const open = useRef(true);
  useEffect(() => {
    open.current = true;
    return () => { open.current = false; };
  }, []);
  const retrying = useRef(new Set<string>());
  const retryStatus = useCallback((url: string) => {
    if (retrying.current.has(url)) return;
    retrying.current.add(url);
    void readStatusQuietly(url, () => !open.current).then((arrived) => {
      retrying.current.delete(url);
      if (arrived && open.current) setStatusRead((n) => n + 1);
    });
  }, []);

  // Status 独立刷新: Worker 报告状态变化时只更新 status, 不打扰
  // accounts/bindings 表 (静态信息只在用户操作后才需要刷新).
  const refreshStatuses = useCallback(async (accountsList: ChannelAccount[], fresh = true) => {
    await Promise.all(accountsList.map(async (a) => {
      const url = statusUrl(a);
      const arrived = fresh
        ? await refreshSettings(url)
        : await cachedFetch(url).then(() => true, () => false);
      if (!arrived) retryStatus(url);
    }));
    setStatusRead((n) => n + 1);
  }, [retryStatus]);

  // Pairing codes arrive from outside (a sender messages the bot), so access
  // is read again on every visit; the kept list shows meanwhile.
  useEffect(() => { void revalidateSettings(CHANNEL_ACCESS_URL); }, []);
  useEffect(() => { void refreshStatuses(accounts, false); }, [accounts, refreshStatuses]);

  // After a change (add/delete account, add/remove binding, approve a sender).
  const reload = useCallback(async () => {
    await Promise.all([
      refreshSettings(CHANNEL_ACCOUNTS_URL),
      refreshSettings(CHANNEL_BINDINGS_URL),
      refreshSettings(CHANNEL_ACCESS_URL),
    ]);
    const next = readCachedSettings<{ accounts?: ChannelAccount[] }>(CHANNEL_ACCOUNTS_URL)?.accounts ?? [];
    await refreshStatuses(next);
  }, [refreshStatuses]);

  // Worker 说某个账号的状态变了 (adapter 起来 / 挂了 / 重连 / 账号改了)
  // 才刷新 status dot, 不再定时轮询. 用最新的 accounts 列表跑, 没 accounts 时跳过.
  const accountsRef = useRef(accounts);
  accountsRef.current = accounts;
  useChanged("channels", () => {
    if (accountsRef.current.length === 0) return;
    return refreshStatuses(accountsRef.current);
  });
  // "reconnecting" 到点自己失效, 没有事件: 到点再读一次.
  useEffect(() => {
    const waits = Object.values(statuses)
      .map((s) => s.recheck_in)
      .filter((n): n is number => typeof n === "number");
    if (waits.length === 0 || accounts.length === 0) return;
    const handle = setTimeout(() => {
      void refreshStatuses(accounts);
    }, (Math.min(...waits) + 0.5) * 1000);
    return () => clearTimeout(handle);
  }, [statuses, accounts, refreshStatuses]);

  return (
    <SettingsPage
      title={t("settings.tab.channels")}
      description={text(
        "Let your agents send and receive messages on Telegram / Discord / Slack / WeChat. Three steps: add a bot account, approve senders, then route them to an agent.",
        "让 Agent 在 Telegram / Discord / Slack / WeChat 上收发消息。共三步：添加 bot 账号、批准发送者，再把他们路由到 Agent。",
      )}
      bodyClassName={styles.channelsPage}
    >
        {ready ? (
          <>
            <div className="flex flex-col gap-[12px]">
              <AccountsList
                accounts={accounts}
                statuses={statuses}
                onChange={reload}
              />
            </div>
            <div className="flex flex-col gap-[12px]">
              <AccessList access={accessData.accounts ?? []} onChange={reload} />
            </div>
            <div className="flex flex-col gap-[12px]">
              <GroupUsersList access={accessData.accounts ?? []} onChange={reload} />
            </div>
            <div className="flex flex-col gap-[12px]">
              <BindingsList
                bindings={bindingsData.bindings ?? []}
                accounts={accounts}
                onChange={reload}
              />
            </div>
          </>
        ) : null}
    </SettingsPage>
  );
}
