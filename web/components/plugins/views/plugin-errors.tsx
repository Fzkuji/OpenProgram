"use client";

import styles from "./plugins.module.css";
import { usePluginsStore } from "@/lib/plugins-store";

export function PluginErrors() {
  const { errors, plugins } = usePluginsStore();
  const rows: Array<[string, string]> = [];
  for (const p of plugins) {
    if (p.error) rows.push([p.name, p.error]);
  }
  for (const [k, v] of Object.entries(errors)) {
    if (!rows.find((r) => r[0] === k)) rows.push([k, v]);
  }
  if (rows.length === 0) {
    return <div className={styles.empty}>没有错误。</div>;
  }
  return (
    <div>
      {rows.map(([name, msg]) => (
        <div key={name} className={styles.errorBox}>
          <strong>{name}</strong>
          {"\n"}
          {msg}
        </div>
      ))}
    </div>
  );
}
