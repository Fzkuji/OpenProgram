"use client";

import { useState } from "react";

import styles from "./channels.module.css";
import { useTranslation } from "@/lib/i18n";
import { PLATFORM_LABEL } from "./types";
import type { ChannelAccessAccount } from "./types";

interface Props {
  access: ChannelAccessAccount[];
  onChange: () => void | Promise<void>;
}

export function AccessList({ access, onChange }: Props) {
  const { text } = useTranslation();
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const approve = async (account: ChannelAccessAccount, code: string) => {
    const key = `approve:${account.channel}:${account.account_id}:${code}`;
    setBusyKey(key);
    setError(null);
    try {
      const response = await fetch("/api/channels/access/approve", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          channel: account.channel,
          account_id: account.account_id,
          code,
        }),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        setError(body?.detail || text("Approval failed", "批准失败"));
        return;
      }
      await onChange();
    } catch {
      setError(text("Approval failed", "批准失败"));
    } finally {
      setBusyKey(null);
    }
  };

  const revoke = async (account: ChannelAccessAccount, userId: string) => {
    const key = `revoke:${account.channel}:${account.account_id}:${userId}`;
    setBusyKey(key);
    setError(null);
    try {
      const path = [account.channel, account.account_id, userId]
        .map(encodeURIComponent)
        .join("/");
      const response = await fetch(`/api/channels/access/${path}`, {
        method: "DELETE",
      });
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        setError(body?.detail || text("Revoke failed", "撤销失败"));
        return;
      }
      await onChange();
    } catch {
      setError(text("Revoke failed", "撤销失败"));
    } finally {
      setBusyKey(null);
    }
  };

  const pendingCount = access.reduce((count, row) => count + row.pending.length, 0);
  const pairedCount = access.reduce((count, row) => count + row.paired.length, 0);

  return (
    <>
      <div className={styles.sectionHeader}>
        <div>
          <div className={styles.sectionTitle}>
            {text("Step 2 - Paired senders", "步骤 2 - 已配对发送者")}
          </div>
          <div className={styles.sectionSub}>
            {text(
              "Unknown senders receive an eight-character code and cannot reach the agent. Approve codes only here or in the local terminal.",
              "陌生发送者只会收到八位配对码，消息不会进入 Agent。配对码只能在此处或本机终端批准。",
            )}
          </div>
        </div>
        <button className={styles.secondaryBtn} onClick={() => onChange()} type="button">
          {text("Refresh", "刷新")}
        </button>
      </div>

      {error && <div className={styles.formError} role="alert">{error}</div>}

      {pendingCount === 0 && pairedCount === 0 ? (
        <div className={styles.emptyHint}>
          {text(
            "No paired or pending senders. A sender's first message creates a code for one hour.",
            "当前没有已配对或待批准的发送者。发送者首次发消息后会生成一小时有效的配对码。",
          )}
        </div>
      ) : (
        <table className={styles.rowTable}>
          <thead>
            <tr>
              <th>{text("Bot account", "Bot 账号")}</th>
              <th>{text("Sender", "发送者")}</th>
              <th>{text("State", "状态")}</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {access.flatMap((account) => [
              ...account.pending.map((sender) => {
                const key = `approve:${account.channel}:${account.account_id}:${sender.code}`;
                return (
                  <tr key={key}>
                    <td>{PLATFORM_LABEL[account.channel] || account.channel} · <code>{account.account_id}</code></td>
                    <td>{sender.display || sender.user_id} · <code>{sender.user_id}</code></td>
                    <td><span className={styles.badgeWarn}>{sender.code}</span></td>
                    <td>
                      <button
                        className={styles.primaryBtn}
                        disabled={busyKey !== null}
                        onClick={() => approve(account, sender.code)}
                        aria-label={text(`Approve ${sender.user_id}`, `批准 ${sender.user_id}`)}
                        type="button"
                      >
                        {busyKey === key ? text("Approving...", "批准中...") : text("Approve", "批准")}
                      </button>
                    </td>
                  </tr>
                );
              }),
              ...account.paired.map((sender) => {
                const key = `revoke:${account.channel}:${account.account_id}:${sender.user_id}`;
                return (
                  <tr key={key}>
                    <td>{PLATFORM_LABEL[account.channel] || account.channel} · <code>{account.account_id}</code></td>
                    <td>{sender.display || sender.user_id} · <code>{sender.user_id}</code></td>
                    <td><span className={styles.badgeOk}>{text("paired", "已配对")}</span></td>
                    <td>
                      <button
                        className={styles.dangerBtn}
                        disabled={busyKey !== null}
                        onClick={() => revoke(account, sender.user_id)}
                        aria-label={text(`Revoke ${sender.user_id}`, `撤销 ${sender.user_id}`)}
                        type="button"
                      >
                        {busyKey === key ? text("Revoking...", "撤销中...") : text("Revoke", "撤销")}
                      </button>
                    </td>
                  </tr>
                );
              }),
            ])}
          </tbody>
        </table>
      )}
    </>
  );
}
