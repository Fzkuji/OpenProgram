"use client";

import { useState } from "react";

import styles from "./channels.module.css";
import { PLATFORMS, PLATFORM_LABEL } from "./types";

interface Props {
  onClose: () => void;
  onAdded: () => void | Promise<void>;
}

const TOKEN_HINT: Record<string, string> = {
  telegram: "在 Telegram 里找 @BotFather 发 /newbot, 它会给你一个 token。",
  discord: "Discord Developer Portal → 你的 Application → Bot → Reset Token。",
  slack: "Slack app 设置 → OAuth & Permissions → Bot User OAuth Token (xoxb- 开头)。",
};

export function AddAccountDialog({ onClose, onAdded }: Props) {
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
        "微信要扫码登录, 网页这边没做 QR 渲染。请到终端运行: openprogram channels accounts login wechat --id " +
          accountId,
      );
      return;
    }
    if (!accountId.trim() || !token.trim()) {
      setError("账号名和 bot token 都不能空");
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
          <span className={styles.modalTitle}>添加机器人</span>
          <button
            className={styles.iconBtn}
            onClick={onClose}
            type="button"
            aria-label="Close"
          >
            ×
          </button>
        </div>

        <div className={styles.modalBody}>
          <label className={styles.formRow}>
            <span className={styles.formLabel}>哪个平台</span>
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
            <span className={styles.formLabel}>取个名字 (账号名)</span>
            <input
              className={styles.formInput}
              value={accountId}
              onChange={(e) => setAccountId(e.target.value)}
              placeholder="比如 default / work / personal"
            />
            <span className={styles.formHint}>
              同一个平台可以接多个 bot, 这里随便起个名字区分 (中英文都行)。
            </span>
          </label>

          {isWeChat ? (
            <div className={styles.emptyHint}>
              微信登录要扫二维码, 网页端目前不支持 QR 渲染。请到终端跑:
              <pre className={styles.codeBlock}>
                openprogram channels accounts login wechat --id {accountId || "default"}
              </pre>
              扫码完成后会自动出现在上面的机器人列表里, 不用刷新这个页面。
            </div>
          ) : (
            <label className={styles.formRow}>
              <span className={styles.formLabel}>Bot token</span>
              <input
                className={styles.formInput}
                type="password"
                value={token}
                onChange={(e) => setToken(e.target.value)}
                placeholder="粘贴 token"
                autoComplete="off"
              />
              <span className={styles.formHint}>
                {TOKEN_HINT[channel] || ""}
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
            取消
          </button>
          {!isWeChat && (
            <button
              className={styles.primaryBtn}
              onClick={submit}
              disabled={submitting}
              type="button"
            >
              {submitting ? "添加中…" : "添加"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
