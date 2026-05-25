"use client";

import styles from "../plugins.module.css";
import type { PluginRow } from "@/lib/plugins-store";

interface Props {
  plugin: PluginRow;
  onClose: () => void;
}

export function PluginDetailDialog({ plugin, onClose }: Props) {
  return (
    <div className={styles.dialogBackdrop} onClick={onClose}>
      <div className={styles.dialog} onClick={(e) => e.stopPropagation()}>
        <div className={styles.dialogTitle}>{plugin.name}</div>
        <div className={styles.dialogBody}>
          <div className={styles.rowMeta}>
            v{plugin.version} · source={plugin.source} · manifest={plugin.manifest_form}
            {plugin.compatibility && ` · compat=${plugin.compatibility}`}
            {plugin.deprecated && " · DEPRECATED"}
          </div>
          {plugin.description && <p style={{ marginTop: 8 }}>{plugin.description}</p>}
          <pre style={{
            marginTop: 12,
            padding: 10,
            background: "var(--bg-secondary)",
            border: "1px solid var(--border)",
            borderRadius: 6,
            fontSize: 12,
            overflow: "auto",
          }}>
{JSON.stringify({
  root: plugin.root,
  entrypoints: plugin.entrypoints,
  sidebar: plugin.sidebar,
  trust: plugin.trust,
  loaded: plugin.loaded,
  error: plugin.error || null,
}, null, 2)}
          </pre>
        </div>
        <div className={styles.dialogActions}>
          <button className={styles.btn} onClick={onClose}>关闭</button>
        </div>
      </div>
    </div>
  );
}
