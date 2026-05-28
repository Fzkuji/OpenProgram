"use client";

import { useState } from "react";
import styles from "../plugins.module.css";
import { usePluginsStore } from "@/lib/plugins-store";
import { useTranslation } from "@/lib/i18n";

interface Props {
  onClose: () => void;
}

export function AddMarketplaceDialog({ onClose }: Props) {
  const { t, text } = useTranslation();
  const addMarketplace = usePluginsStore((s) => s.addMarketplace);
  const [url, setUrl] = useState("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const submit = async () => {
    setErr("");
    if (!url.trim()) {
      setErr(text("URL is required", "URL 不能为空"));
      return;
    }
    setBusy(true);
    try {
      await addMarketplace(url.trim(), name.trim());
      onClose();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={styles.dialogBackdrop} onClick={onClose}>
      <div className={styles.dialog} onClick={(e) => e.stopPropagation()}>
        <div className={styles.dialogTitle}>{text("Add Marketplace", "添加 Marketplace")}</div>
        <div className={styles.dialogBody}>
          <div style={{ marginBottom: 8 }}>
            <div style={{ fontSize: 12, marginBottom: 4, color: "var(--text-dim)" }}>Index URL</div>
            <input
              className={styles.input}
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://example.com/plugins.json"
            />
          </div>
          <div style={{ marginBottom: 8 }}>
            <div style={{ fontSize: 12, marginBottom: 4, color: "var(--text-dim)" }}>{text("Name (optional)", "名称（可选）")}</div>
            <input
              className={styles.input}
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </div>
          {err && <div style={{ color: "#ef4444", fontSize: 12 }}>{err}</div>}
        </div>
        <div className={styles.dialogActions}>
          <button className={styles.btn} onClick={onClose} disabled={busy}>{t("sidebar.cancel")}</button>
          <button className={styles.btnPrimary} onClick={submit} disabled={busy}>
            {busy ? text("Adding...", "添加中...") : text("Add", "添加")}
          </button>
        </div>
      </div>
    </div>
  );
}
