"use client";

import { useState } from "react";

import styles from "./channels.module.css";
import { AddAccountDialog } from "./add-account-dialog";
import { PLATFORMS, PLATFORM_LABEL } from "./types";
import type { ChannelAccount, StatusMap } from "./types";

interface Props {
  accounts: ChannelAccount[];
  statuses: StatusMap;
  onChange: () => void | Promise<void>;
}

export function AccountsList({ accounts, statuses, onChange }: Props) {
  const [addOpen, setAddOpen] = useState(false);
  const [busyKey, setBusyKey] = useState<string | null>(null);

  const handleDelete = async (channel: string, account_id: string) => {
    if (!confirm(`Delete ${channel}:${account_id}? Any rules using this bot will also be removed.`)) {
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
          <div className={styles.sectionTitle}>Step 1 — Bot accounts</div>
          <div className={styles.sectionSub}>
            Add your bot tokens (Telegram / Discord / Slack). One platform
            can hold multiple bots — give each one a short name to tell
            them apart.
          </div>
        </div>
        <button
          className={styles.primaryBtn}
          onClick={() => setAddOpen(true)}
          type="button"
        >
          + Add bot
        </button>
      </div>

      {accounts.length === 0 ? (
        <div className={styles.emptyHint}>
          No bots yet. Click "+ Add bot" to paste a token, or run{" "}
          <code>openprogram channels accounts login wechat</code>{" "}
          to scan a WeChat QR.
        </div>
      ) : (
        <table className={styles.rowTable}>
          <thead>
            <tr>
              <th>Platform</th>
              <th>Account name</th>
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
                  const status = statuses[key];
                  // Health dot:
                  //   green  — adapter thread alive + heartbeat fresh
                  //   red    — heartbeat stale / adapter crashed
                  //   gray   — unknown (worker hasn't started this
                  //            adapter, or account is disabled)
                  const dotClass =
                    status?.state === "alive" ? styles.dotGreen
                    : status?.state === "stale" ? styles.dotRed
                    : styles.dotGray;
                  const dotTitle =
                    status?.state === "alive" ? "Adapter running"
                    : status?.state === "stale" ? "Adapter heartbeat stale — likely crashed"
                    : "Adapter not started";
                  return (
                    <tr key={key}>
                      <td>{PLATFORM_LABEL[a.channel] || a.channel}</td>
                      <td><code>{a.account_id}</code></td>
                      <td>
                        <span
                          className={`${styles.statusDot} ${dotClass}`}
                          title={dotTitle}
                        />
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
