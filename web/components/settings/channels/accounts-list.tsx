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
    if (!confirm(`Delete ${channel}:${account_id}? This also removes its bindings.`)) {
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
        <span className={styles.sectionTitle}>Accounts</span>
        <button
          className={styles.primaryBtn}
          onClick={() => setAddOpen(true)}
          type="button"
        >
          + Add account
        </button>
      </div>

      {accounts.length === 0 ? (
        <div className={styles.emptyHint}>
          No channel accounts yet. Click "+ Add account" to register a bot
          token, or run <code>openprogram channels accounts login wechat</code>
          {" "}for WeChat QR login.
        </div>
      ) : (
        <table className={styles.rowTable}>
          <thead>
            <tr>
              <th>Platform</th>
              <th>Account</th>
              <th>Status</th>
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
                          <span className={styles.badgeOk}>configured</span>
                        ) : (
                          <span className={styles.badgeWarn}>missing token</span>
                        )}
                        {!a.enabled && (
                          <span className={styles.badgeMuted}> disabled</span>
                        )}
                      </td>
                      <td>
                        <button
                          className={styles.dangerBtn}
                          onClick={() => handleDelete(a.channel, a.account_id)}
                          disabled={busy}
                          type="button"
                        >
                          {busy ? "Deleting…" : "Delete"}
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
