"use client";

import { useEffect, useState } from "react";
import styles from "./plugins.module.css";
import { usePluginsStore } from "@/lib/plugins-store";

interface Props {
  name: string;
  onClose: () => void;
}

// TODO: 升级为 schema-driven (按 manifest.options 的 JSON Schema 渲染)。
// 现阶段纯 JSON 文本编辑。
export function PluginOptionsDialog({ name, onClose }: Props) {
  const { getOptions, setOptions } = usePluginsStore();
  const [text, setText] = useState("{}");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    getOptions(name)
      .then((o) => setText(JSON.stringify(o, null, 2)))
      .catch((e) => setErr(String(e)));
  }, [name, getOptions]);

  const save = async () => {
    setErr("");
    let parsed: Record<string, unknown>;
    try {
      parsed = JSON.parse(text);
    } catch (e) {
      setErr(`JSON 解析失败: ${(e as Error).message}`);
      return;
    }
    setBusy(true);
    try {
      await setOptions(name, parsed);
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
        <div className={styles.dialogTitle}>{name} options</div>
        <div className={styles.dialogBody}>
          <textarea
            className={styles.textarea}
            value={text}
            onChange={(e) => setText(e.target.value)}
            spellCheck={false}
          />
          {err && <div style={{ color: "#ef4444", fontSize: 12, marginTop: 6 }}>{err}</div>}
        </div>
        <div className={styles.dialogActions}>
          <button className={styles.btn} onClick={onClose} disabled={busy}>取消</button>
          <button className={styles.btnPrimary} onClick={save} disabled={busy}>
            {busy ? "保存中…" : "保存"}
          </button>
        </div>
      </div>
    </div>
  );
}
