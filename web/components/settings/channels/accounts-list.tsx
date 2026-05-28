"use client";

import { useState } from "react";

import styles from "./channels.module.css";
import { useTranslation } from "@/lib/i18n";
import { AddAccountDialog } from "./add-account-dialog";
import { PLATFORMS, PLATFORM_LABEL } from "./types";
import type { ChannelAccount, StatusMap } from "./types";

interface Props {
  accounts: ChannelAccount[];
  statuses: StatusMap;
  onChange: () => void | Promise<void>;
}

export function AccountsList({ accounts, statuses, onChange }: Props) {
  const { t, text } = useTranslation();
  const [addOpen, setAddOpen] = useState(false);
  const [busyKey, setBusyKey] = useState<string | null>(null);

  const handleDelete = async (channel: string, account_id: string) => {
    if (!confirm(text(
      `Delete ${channel}:${account_id}? Any rules using this bot will also be removed.`,
      `删除 ${channel}:${account_id}？使用这个 bot 的规则也会被移除。`,
    ))) {
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
        alert(d?.detail || text("Delete failed", "删除失败"));
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
          <div className={styles.sectionTitle}>{text("Step 1 - Bot accounts", "步骤 1 - Bot 账号")}</div>
          <div className={styles.sectionSub}>
            {text(
              "Add your bot tokens (Telegram / Discord / Slack). One platform can hold multiple bots. Give each one a short name to tell them apart.",
              "添加 bot token（Telegram / Discord / Slack）。一个平台可以有多个 bot，请给每个 bot 一个短名称方便区分。",
            )}
          </div>
        </div>
        <button
          className={styles.primaryBtn}
          onClick={() => setAddOpen(true)}
          type="button"
        >
          + {text("Add bot", "添加 bot")}
        </button>
      </div>

      {accounts.length === 0 ? (
        <div className={styles.emptyHint}>
          {text("No bots yet. Click \"+ Add bot\" to paste a token, or run ", "还没有 bot。点击“+ 添加 bot”粘贴 token，或运行 ")}
          <code>openprogram channels accounts login wechat</code>{" "}
          {text("to scan a WeChat QR.", "扫描 WeChat 二维码。")}
        </div>
      ) : (
        <table className={styles.rowTable}>
          <thead>
            <tr>
              <th>{text("Platform", "平台")}</th>
              <th>{text("Account name", "账号名称")}</th>
              <th>{text("Status", "状态")}</th>
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
                    status?.state === "alive" ? text("Adapter running", "适配器正在运行")
                    : status?.state === "stale" ? text("Adapter heartbeat stale; likely crashed", "适配器心跳过期，可能已崩溃")
                    : text("Adapter not started", "适配器未启动");
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
                          <span className={styles.badgeOk}>{text("configured", "已配置")}</span>
                        ) : (
                          <span className={styles.badgeWarn}>{text("missing token", "缺少 token")}</span>
                        )}
                        {!a.enabled && (
                          <span className={styles.badgeMuted}> {text("disabled", "已禁用")}</span>
                        )}
                      </td>
                      <td>
                        <button
                          className={styles.dangerBtn}
                          onClick={() => handleDelete(a.channel, a.account_id)}
                          disabled={busy}
                          type="button"
                        >
                          {busy ? text("Deleting...", "删除中...") : t("sidebar.delete")}
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
