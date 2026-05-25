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

import styles from "./channels.module.css";
import { AccountsList } from "./accounts-list";
import { BindingsList } from "./bindings-list";
import type { ChannelAccount, ChannelBinding } from "./types";

export function ChannelsSection() {
  const [accounts, setAccounts] = useState<ChannelAccount[]>([]);
  const [bindings, setBindings] = useState<ChannelBinding[]>([]);
  const [loading, setLoading] = useState(true);

  const reload = useCallback(async () => {
    try {
      const [accountsResp, bindingsResp] = await Promise.all([
        fetch("/api/channels/accounts"),
        fetch("/api/channels/bindings"),
      ]);
      const accountsData = await accountsResp.json();
      const bindingsData = await bindingsResp.json();
      setAccounts(accountsData.accounts || []);
      setBindings(bindingsData.bindings || []);
    } catch (e) {
      console.error("channels reload failed:", e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  if (loading) {
    return (
      <div className={styles.detail}>
        <div className={styles.detailHeader}>
          <span className={styles.detailTitle}>正在加载…</span>
        </div>
      </div>
    );
  }

  return (
    <div className={styles.detail}>
      <div className={styles.detailHeader}>
        <span className={styles.detailTitle}>聊天平台接入</span>
        <span className={styles.detailMeta}>
          让 OpenProgram 上的 agent 在 Telegram / Discord / Slack / 微信里
          收发消息。两步: 1) 添加机器人账号 (粘贴 bot token 或微信扫码) →
          2) 设规则决定哪个平台 / 哪个用户的消息派给哪个 agent 来回复。
        </span>
      </div>

      <div className={styles.detailSection}>
        <AccountsList accounts={accounts} onChange={reload} />
      </div>

      <div className={styles.detailSection}>
        <BindingsList
          bindings={bindings}
          accounts={accounts}
          onChange={reload}
        />
      </div>
    </div>
  );
}
