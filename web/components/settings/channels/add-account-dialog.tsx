"use client";

import { useState } from "react";

import styles from "./channels.module.css";
import { PLATFORMS, PLATFORM_LABEL } from "./types";

interface Props {
  onClose: () => void;
  onAdded: () => void | Promise<void>;
}

const TOKEN_HINT: Record<string, string> = {
  telegram: "Get from @BotFather after /newbot.",
  discord: "Discord Developer Portal → Application → Bot → Reset Token.",
  slack: "OAuth & Permissions → Bot User OAuth Token (starts with xoxb-).",
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
        "WeChat 走 QR 扫码登录, Web 端目前不支持. 请运行: openprogram channels accounts login wechat --id " +
          accountId,
      );
      return;
    }
    if (!accountId.trim() || !token.trim()) {
      setError("account_id 和 token 都不能空");
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
          <span className={styles.modalTitle}>Add channel account</span>
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
            <span className={styles.formLabel}>Platform</span>
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
            <span className={styles.formLabel}>Account id</span>
            <input
              className={styles.formInput}
              value={accountId}
              onChange={(e) => setAccountId(e.target.value)}
              placeholder="default / work / personal"
            />
          </label>

          {isWeChat ? (
            <div className={styles.emptyHint}>
              WeChat 登录是扫码流程, Web 端目前不支持 QR 渲染. 请运行:
              <pre className={styles.codeBlock}>
                openprogram channels accounts login wechat --id {accountId || "default"}
              </pre>
              扫码后会自动出现在这个列表里.
            </div>
          ) : (
            <label className={styles.formRow}>
              <span className={styles.formLabel}>Bot token</span>
              <input
                className={styles.formInput}
                type="password"
                value={token}
                onChange={(e) => setToken(e.target.value)}
                placeholder="paste here"
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
            Cancel
          </button>
          {!isWeChat && (
            <button
              className={styles.primaryBtn}
              onClick={submit}
              disabled={submitting}
              type="button"
            >
              {submitting ? "Adding…" : "Add account"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
