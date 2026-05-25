"use client";

import { useState } from "react";

import styles from "./channels.module.css";
import { AddAccountDialog } from "./add-account-dialog";
import { PLATFORMS, PLATFORM_LABEL } from "./types";
import type { ChannelAccount } from "./types";

interface Props {
  accounts: ChannelAccount[];
  onChange: () => void | Promise<void>;
}

export function AccountsList({ accounts, onChange }: Props) {
  const [addOpen, setAddOpen] = useState(false);
  const [busyKey, setBusyKey] = useState<string | null>(null);

  const handleDelete = async (channel: string, account_id: string) => {
    if (!confirm(`确定删除 ${channel}:${account_id} 这个机器人? 跟它相关的派发规则也会一起删除。`)) {
      return;
    }
    const key = `${channel}:${account_id}`;
    setBusyKey(key);
    try {
      const r = await fetch(
        `/api/channels/accounts/${encodeURIComponent(channel)}/${encodeURIComponent(account_id)}`,
        { method: "DELETE" },
      );
      if (!r.ok) {
        const d = await r.json().catch(() => null);
        alert(d?.detail || "Delete failed");
      } else {
        await onChange();
      }
    } finally {
      setBusyKey(null);
    }
  };

  return (
    <>
      <div className={styles.sectionHeader}>
        <div>
          <div className={styles.sectionTitle}>第 1 步: 机器人账号</div>
          <div className={styles.sectionSub}>
            把你的 bot token (Telegram / Discord / Slack) 加进来。一个平台
            可以接多个 bot, 用不同的"账号名"区分。
          </div>
        </div>
        <button
          className={styles.primaryBtn}
          onClick={() => setAddOpen(true)}
          type="button"
        >
          + 添加机器人
        </button>
      </div>

      {accounts.length === 0 ? (
        <div className={styles.emptyHint}>
          还没有机器人。点击"+ 添加机器人"粘贴 bot token, 或者运行{" "}
          <code>openprogram channels accounts login wechat</code>{" "}
          扫码登录微信。
        </div>
      ) : (
        <table className={styles.rowTable}>
          <thead>
            <tr>
              <th>平台</th>
              <th>账号名</th>
              <th>状态</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {PLATFORMS.flatMap((platform) =>
              accounts
                .filter((a) => a.channel === platform)
                .map((a) => {
                  const key = `${a.channel}:${a.account_id}`;
                  const busy = busyKey === key;
                  return (
                    <tr key={key}>
                      <td>{PLATFORM_LABEL[a.channel] || a.channel}</td>
                      <td><code>{a.account_id}</code></td>
                      <td>
                        {a.configured ? (
                          <span className={styles.badgeOk}>已配置</span>
                        ) : (
                          <span className={styles.badgeWarn}>缺 token</span>
                        )}
                        {!a.enabled && (
                          <span className={styles.badgeMuted}> 已停用</span>
                        )}
                      </td>
                      <td>
                        <button
                          className={styles.dangerBtn}
                          onClick={() => handleDelete(a.channel, a.account_id)}
                          disabled={busy}
                          type="button"
                        >
                          {busy ? "删除中…" : "删除"}
                        </button>
                      </td>
                    </tr>
                  );
                }),
            )}
          </tbody>
        </table>
      )}

      {addOpen && (
        <AddAccountDialog
          onClose={() => setAddOpen(false)}
          onAdded={async () => {
            setAddOpen(false);
            await onChange();
          }}
        />
      )}
    </>
  );
}
