"use client";

import { useState } from "react";

import styles from "./channels.module.css";
import { useTranslation } from "@/lib/i18n";
import { PLATFORMS, PLATFORM_LABEL } from "./types";

interface Props {
  onClose: () => void;
  onAdded: () => void | Promise<void>;
}

const TOKEN_HINT: Record<string, string> = {
  telegram: "Open Telegram, message @BotFather with /newbot, it will hand you a token.",
  discord: "Discord Developer Portal → your Application → Bot → Reset Token.",
  slack: "Your Slack app settings → OAuth & Permissions → Bot User OAuth Token (starts with xoxb-).",
};

const TOKEN_HINT_ZH: Record<string, string> = {
  telegram: "打开 Telegram，向 @BotFather 发送 /newbot，它会返回 token。",
  discord: "Discord Developer Portal → 你的 Application → Bot → Reset Token。",
  slack: "你的 Slack app 设置 → OAuth & Permissions → Bot User OAuth Token（以 xoxb- 开头）。",
};

export function AddAccountDialog({ onClose, onAdded }: Props) {
  const { t, text } = useTranslation();
  const [channel, setChannel] = useState<string>("telegram");
  const [accountId, setAccountId] = useState("default");
  const [token, setToken] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const isWeChat = channel === "wechat";

  const submit = async () => {
    setError(null);
    if (isWeChat) {
      setError(
        text(
          "WeChat needs QR-code login, which the web UI does not render. Run in a terminal: openprogram channels accounts login wechat --id ",
          "WeChat 需要二维码登录，当前 Web UI 不渲染二维码。请在终端运行：openprogram channels accounts login wechat --id ",
        ) + accountId,
      );
      return;
    }
    if (!accountId.trim() || !token.trim()) {
      setError(text("Account name and bot token are both required.", "账号名称和 bot token 都是必填项。"));
      return;
    }
    setSubmitting(true);
    try {
      const r = await fetch("/api/channels/accounts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          channel,
          account_id: accountId.trim(),
          token: token.trim(),
        }),
      });
      if (!r.ok) {
        const d = await r.json().catch(() => null);
        setError(d?.detail || `HTTP ${r.status}`);
        setSubmitting(false);
        return;
      }
      await onAdded();
    } catch (e) {
      setError(String(e));
      setSubmitting(false);
    }
  };

  return (
    <div className={styles.modalBackdrop} onClick={onClose}>
      <div className={styles.modal} onClick={(e) => e.stopPropagation()}>
        <div className={styles.modalHeader}>
          <span className={styles.modalTitle}>{text("Add a bot", "添加 bot")}</span>
          <button
            className={styles.iconBtn}
            onClick={onClose}
            type="button"
            aria-label={text("Close", "关闭")}
          >
            ×
          </button>
        </div>

        <div className={styles.modalBody}>
          <label className={styles.formRow}>
            <span className={styles.formLabel}>{text("Platform", "平台")}</span>
            <select
              className={styles.formInput}
              value={channel}
              onChange={(e) => setChannel(e.target.value)}
            >
              {PLATFORMS.map((p) => (
                <option key={p} value={p}>
                  {PLATFORM_LABEL[p] || p}
                </option>
              ))}
            </select>
          </label>

          <label className={styles.formRow}>
            <span className={styles.formLabel}>{text("Give it a name", "给它命名")}</span>
            <input
              className={styles.formInput}
              value={accountId}
              onChange={(e) => setAccountId(e.target.value)}
              placeholder={text("e.g. default / work / personal", "例如 default / work / personal")}
            />
            <span className={styles.formHint}>
              {text(
                "You can hold multiple bots per platform. This short name tells them apart in the list above.",
                "每个平台可以有多个 bot。这个短名称用于在上方列表中区分它们。",
              )}
            </span>
          </label>

          {isWeChat ? (
            <div className={styles.emptyHint}>
              {text("WeChat uses QR-code login, which the web UI does not render. Run this in a terminal:", "WeChat 使用二维码登录，当前 Web UI 不渲染二维码。请在终端运行：")}
              <pre className={styles.codeBlock}>
                openprogram channels accounts login wechat --id {accountId || "default"}
              </pre>
              {text("After you scan, the new bot shows up in the list above automatically. No need to refresh this page.", "扫码后，新 bot 会自动显示在上方列表，无需刷新页面。")}
            </div>
          ) : (
            <label className={styles.formRow}>
              <span className={styles.formLabel}>Bot token</span>
              <input
                className={styles.formInput}
                type="password"
                value={token}
                onChange={(e) => setToken(e.target.value)}
                placeholder={text("paste here", "粘贴到这里")}
                autoComplete="off"
              />
              <span className={styles.formHint}>
                {text(TOKEN_HINT[channel] || "", TOKEN_HINT_ZH[channel] || "")}
              </span>
            </label>
          )}

          {error && <div className={styles.formError}>{error}</div>}
        </div>

        <div className={styles.modalFooter}>
          <button
            className={styles.secondaryBtn}
            onClick={onClose}
            type="button"
          >
            {t("sidebar.cancel")}
          </button>
          {!isWeChat && (
            <button
              className={styles.primaryBtn}
              onClick={submit}
              disabled={submitting}
              type="button"
            >
              {submitting ? text("Adding...", "添加中...") : text("Add bot", "添加 bot")}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
