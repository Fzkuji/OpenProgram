"use client";

import { useEffect, useState } from "react";
import styles from "../plugins.module.css";
import { usePluginsStore } from "@/lib/plugins-store";

interface Check {
  name: string;
  ok: boolean;
  detail: string;
}

interface Props {
  name: string;
  onClose: () => void;
}

export function ValidatePluginDialog({ name, onClose }: Props) {
  const validate = usePluginsStore((s) => s.validate);
  const [checks, setChecks] = useState<Check[] | null>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    validate(name)
      .then((r) => setChecks(r.checks))
      .catch((e) => setErr(String(e)));
  }, [name, validate]);

  return (
    <div className={styles.dialogBackdrop} onClick={onClose}>
      <div className={styles.dialog} onClick={(e) => e.stopPropagation()}>
        <div className={styles.dialogTitle}>Validate {name}</div>
        <div className={styles.dialogBody}>
          {err && <div style={{ color: "#ef4444" }}>{err}</div>}
          {!checks && !err && <div className={styles.empty}>正在校验…</div>}
          {checks?.map((c) => (
            <div key={c.name} className={styles.checkRow}>
              <span className={c.ok ? styles.checkOk : styles.checkFail}>
                {c.ok ? "✓" : "✗"}
              </span>
              <span style={{ fontWeight: 600 }}>{c.name}</span>
              <span style={{ color: "var(--text-dim)" }}>{c.detail}</span>
            </div>
          ))}
        </div>
        <div className={styles.dialogActions}>
          <button className={styles.btn} onClick={onClose}>关闭</button>
        </div>
      </div>
    </div>
  );
}
