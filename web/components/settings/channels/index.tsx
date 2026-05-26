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
import { useCallback, useEffect, useState } from "react";

import shellStyles from "../settings-page.module.css";
import styles from "./channels.module.css";
import { AccountsList } from "./accounts-list";
import { BindingsList } from "./bindings-list";
import type {
  ChannelAccount, ChannelBinding, ChannelHealthStatus, StatusMap,
} from "./types";

export function ChannelsSection() {
  const [accounts, setAccounts] = useState<ChannelAccount[]>([]);
  const [bindings, setBindings] = useState<ChannelBinding[]>([]);
  const [statuses, setStatuses] = useState<StatusMap>({});
  const [loading, setLoading] = useState(true);

  // Status fetch 独立成自己的 helper, 这样 30s 定时刷新只更新 status
  // 而不打扰 accounts/bindings 表 (静态信息只在用户操作后才需要刷新).
  const refreshStatuses = useCallback(
    async (accountsList: ChannelAccount[]) => {
      const statusEntries = await Promise.all(
        accountsList.map(async (a) => {
          try {
            const r = await fetch(
              `/api/channels/${encodeURIComponent(a.channel)}/${encodeURIComponent(a.account_id)}/status`,
            );
            if (!r.ok) return null;
            const s = (await r.json()) as ChannelHealthStatus;
            return [`${a.channel}:${a.account_id}`, s] as const;
          } catch {
            return null;
          }
        }),
      );
      const map: StatusMap = {};
      for (const e of statusEntries) {
        if (e) map[e[0]] = e[1];
      }
      setStatuses(map);
    },
    [],
  );

  const reload = useCallback(async () => {
    try {
      const [accountsResp, bindingsResp] = await Promise.all([
        fetch("/api/channels/accounts"),
        fetch("/api/channels/bindings"),
      ]);
      const accountsData = await accountsResp.json();
      const bindingsData = await bindingsResp.json();
      const accountsList: ChannelAccount[] = accountsData.accounts || [];
      setAccounts(accountsList);
      setBindings(bindingsData.bindings || []);
      await refreshStatuses(accountsList);
    } catch (e) {
      console.error("channels reload failed:", e);
    } finally {
      setLoading(false);
    }
  }, [refreshStatuses]);

  useEffect(() => {
    void reload();
  }, [reload]);

  // 30s 定时只刷新 status dot — 让用户不刷新页面就能看到 adapter 挂了
  // 或重新连上. 用最新的 accounts 列表跑 refresh, 没 accounts 时跳过.
  useEffect(() => {
    if (accounts.length === 0) return;
    const handle = setInterval(() => {
      void refreshStatuses(accounts);
    }, 30_000);
    return () => clearInterval(handle);
  }, [accounts, refreshStatuses]);

  return (
    <div className={shellStyles.page}>
      <div className={shellStyles.pageHeader}>
        <h2 className={shellStyles.pageTitle}>Channels</h2>
        <p className={shellStyles.pageMeta}>
          Let your agents send and receive messages on Telegram / Discord
          / Slack / WeChat. Two steps: 1) add a bot account (paste a bot
          token, or scan a QR for WeChat) → 2) add rules that decide
          which platform / sender goes to which agent.
        </p>
      </div>
      <div className={shellStyles.pageBody}>
        {loading ? (
          <div className={styles.emptyHint}>Loading…</div>
        ) : (
          <>
            <div className={styles.detailSection}>
              <AccountsList
                accounts={accounts}
                statuses={statuses}
                onChange={reload}
              />
            </div>
            <div className={styles.detailSection}>
              <BindingsList
                bindings={bindings}
                accounts={accounts}
                onChange={reload}
              />
            </div>
          </>
        )}
      </div>
    </div>
  );
}
