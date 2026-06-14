"use client";

import { useState } from "react";
import styles from "../plugins.module.css";
import { usePluginsStore } from "@/lib/state/plugins-store";
import { useTranslation } from "@/lib/i18n";

interface Props {
  name: string;
  currentLevel: string;
  onDone: () => void;
  onCancel: () => void;
}

export function PluginTrustWarning({ name, currentLevel, onDone, onCancel }: Props) {
  const { t, text } = useTranslation();
  const setTrust = usePluginsStore((s) => s.setTrust);
  const [level, setLevel] = useState<string>(
    currentLevel === "untrusted" ? "community" : currentLevel,
  );
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    setBusy(true);
    try {
      await setTrust(name, level);
      onDone();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={styles.dialogBackdrop} onClick={onCancel}>
      <div className={styles.dialog} onClick={(e) => e.stopPropagation()}>
        <div className={styles.dialogTitle}>{text(`Raise trust level for ${name}`, `提升 ${name} 的 trust 等级`)}</div>
        <div className={styles.dialogBody}>
          <p>
            {text("Current level: ", "当前等级 ")}
            <strong>{currentLevel}</strong>
            {text(
              ". You must upgrade to community or verified before first enabling it. Verified loads in-process; community will use a subprocess sandbox in the future (currently still in-process).",
              "。首次启用前必须升级到 community 或 verified。verified 会以 in-process 方式加载，community 未来将走 subprocess 沙箱（当前仍 in-process）。",
            )}
          </p>
          <div style={{ marginTop: 12 }}>
            <label>
              <input
                type="radio"
                checked={level === "community"}
                onChange={() => setLevel("community")}
              />
              {" "}community
            </label>
            <label style={{ marginLeft: 16 }}>
              <input
                type="radio"
                checked={level === "verified"}
                onChange={() => setLevel("verified")}
              />
              {" "}verified
            </label>
          </div>
        </div>
        <div className={styles.dialogActions}>
          <button className={styles.btn} onClick={onCancel} disabled={busy}>{t("sidebar.cancel")}</button>
          <button className={styles.btnPrimary} onClick={submit} disabled={busy}>
            {busy ? text("Saving...", "保存中...") : text("Save", "保存")}
          </button>
        </div>
      </div>
    </div>
  );
}
