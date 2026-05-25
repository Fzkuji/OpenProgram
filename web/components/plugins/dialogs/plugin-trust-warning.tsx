"use client";

import { useState } from "react";
import styles from "../plugins.module.css";
import { usePluginsStore } from "@/lib/plugins-store";

interface Props {
  name: string;
  currentLevel: string;
  onDone: () => void;
  onCancel: () => void;
}

export function PluginTrustWarning({ name, currentLevel, onDone, onCancel }: Props) {
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
        <div className={styles.dialogTitle}>提升 {name} 的 trust 等级</div>
        <div className={styles.dialogBody}>
          <p>
            当前等级 <strong>{currentLevel}</strong>。首次启用前必须升级到 community 或
            verified。verified 会以 in-process 方式加载，community 未来将走 subprocess 沙箱
            (当前仍 in-process)。
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
          <button className={styles.btn} onClick={onCancel} disabled={busy}>取消</button>
          <button className={styles.btnPrimary} onClick={submit} disabled={busy}>
            {busy ? "保存中…" : "保存"}
          </button>
        </div>
      </div>
    </div>
  );
}
