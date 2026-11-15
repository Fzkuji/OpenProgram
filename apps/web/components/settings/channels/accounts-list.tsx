"use client";

import { settingsSend } from "@/lib/settings/settings-request";
import { useState } from "react";

import styles from "./channels.module.css";
import { Button } from "@/components/ui/button";
import { PlusIcon } from "@/components/animated-icons";
import { useTranslation } from "@/lib/i18n";
import { AddAccountDialog } from "./add-account-dialog";
import { PLATFORMS, PLATFORM_LABEL } from "./types";
import type { ChannelAccount, ChannelHealthStatus, StatusMap } from "./types";
import { confirmAction } from "@/components/ui/confirm-dialog";

interface Props {
  accounts: ChannelAccount[];
  statuses: StatusMap;
  onChange: () => void | Promise<void>;
}

function describeStatus(
  status: ChannelHealthStatus | undefined,
  text: (english: string, chinese: string) => string,
): { dot: string; label: string; hint?: string; needsSignIn?: boolean } {
  switch (status?.state) {
    case "alive":
      return { dot: styles.dotGreen, label: text("Running", "运行中") };
    case "off":
      return { dot: styles.dotGray, label: text("Turned off", "已关闭") };
    case "no_token":
      return {
        dot: styles.dotGray,
        label: text("Not signed in", "未登录"),
        hint: text("Run this to sign in:", "运行这条命令登录："),
        needsSignIn: true,
      };
    case "signed_out":
      return {
        dot: styles.dotRed,
        label: text("Signed out", "已退出登录"),
        hint: text(
          "Its token or login stopped working, so messages no longer arrive. Run this to sign in again:",
          "它的 token 或登录已失效，收不到消息了。运行这条命令重新登录：",
        ),
        needsSignIn: true,
      };
    case "failed":
      return {
        dot: styles.dotRed,
        label: text("Stopped by an error", "因错误停止"),
        hint: text(
          "Messages no longer arrive. Restart OpenProgram to try again.",
          "收不到消息了。重启 OpenProgram 再试一次。",
        ),
      };
    case "reconnecting":
      return {
        dot: styles.dotRed,
        label: text("Reconnecting", "正在重连"),
        hint: text(
          "It hit an error and keeps trying by itself. Messages may not arrive until it connects.",
          "它遇到错误，正在自动重试。连上之前可能收不到消息。",
        ),
      };
    case "stale":
      return {
        dot: styles.dotRed,
        label: text("Not responding", "没有响应"),
        hint: text(
          "Messages may not arrive. Restart OpenProgram to reconnect.",
          "可能收不到消息。重启 OpenProgram 重新连接。",
        ),
      };
    case "unknown":
      return {
        dot: styles.dotGray,
        label: text("Not started yet", "尚未启动"),
        hint: text(
          "It starts within a few seconds while OpenProgram runs in the background.",
          "OpenProgram 在后台运行时，几秒内就会启动。",
        ),
      };
    default:
      return { dot: styles.dotGray, label: text("Checking", "检查中") };
  }
}

export function AccountsList({ accounts, statuses, onChange }: Props) {
  const { t, text } = useTranslation();
  const [addOpen, setAddOpen] = useState(false);
  const [busyKey, setBusyKey] = useState<string | null>(null);

  const handleDelete = async (channel: string, account_id: string) => {
    if (!(await confirmAction({
      title: text(`Delete ${channel}:${account_id}?`, `删除 ${channel}:${account_id}？`),
      description: text("Any rules using this bot will also be removed.", "使用这个 bot 的规则也会被移除。"),
      confirmLabel: text("Delete", "删除"),
      destructive: true,
    }))) return;
    const key = `${channel}:${account_id}`;
    setBusyKey(key);
    try {
      const deleted = await settingsSend(
        `/api/channels/accounts/${encodeURIComponent(channel)}/${encodeURIComponent(account_id)}`,
        { method: "DELETE", failure: text("Delete failed", "删除失败") },
      );
      if (deleted !== null) await onChange();
    } finally {
      setBusyKey(null);
    }
  };

  return (
    <>
      <div className="flex items-start justify-between gap-[16px]">
        <div>
          <div className={styles.sectionTitle}>{text("Step 1 - Bot accounts", "步骤 1 - Bot 账号")}</div>
          <div className={styles.sectionSub}>
            {text(
              "Add your bot tokens (Telegram / Discord / Slack). One platform can hold multiple bots. Give each one a short name to tell them apart.",
              "添加 bot token（Telegram / Discord / Slack）。一个平台可以有多个 bot，请给每个 bot 一个短名称方便区分。",
            )}
          </div>
        </div>
        <Button
          variant="default"
          onClick={() => setAddOpen(true)}
          type="button"
        >
          <PlusIcon size={16} aria-hidden />{text("Add bot", "添加 bot")}
        </Button>
      </div>

      {accounts.length === 0 ? (
        <div className={styles.emptyHint}>
          {text("No bots yet. Click \"Add bot\" to paste a token, or run ", "还没有 bot。点击“添加 bot”粘贴 token，或运行 ")}
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
                  const signIn = `openprogram channels accounts login ${a.channel} --id ${a.account_id}`;
                  const shown = describeStatus(status, text);
                  return (
                    <tr key={key}>
                      <td>{PLATFORM_LABEL[a.channel] || a.channel}</td>
                      <td><code>{a.account_id}</code></td>
                      <td>
                        <span className={`${styles.statusDot} ${shown.dot}`} />
                        {shown.label}
                        {shown.hint && <div className={styles.statusHint}>{shown.hint}</div>}
                        {shown.needsSignIn && <div className={styles.statusHint}><code>{signIn}</code></div>}
                      </td>
                      <td>
                        <Button
                          variant="destructive" size="xs"
                          onClick={() => handleDelete(a.channel, a.account_id)}
                          disabled={busy}
                          type="button"
                        >
                          {busy ? text("Deleting...", "删除中...") : t("sidebar.delete")}
                        </Button>
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
