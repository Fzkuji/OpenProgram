"use client";

import styles from "../plugins.module.css";
import { managePageStyles as shared } from "@/components/ui/manage-page";
import { usePluginsStore } from "@/lib/state/plugins-store";
import { useTranslation } from "@/lib/i18n";

export function PluginErrors({ filter }: { filter?: string } = {}) {
  const { text } = useTranslation();
  const { errors, plugins } = usePluginsStore();
  const rows: Array<[string, string]> = [];
  for (const p of plugins) {
    if (p.error) rows.push([p.name, p.error]);
  }
  for (const [k, v] of Object.entries(errors)) {
    if (!rows.find((r) => r[0] === k)) rows.push([k, v]);
  }
  const q = (filter || "").trim().toLowerCase();
  const shown = q
    ? rows.filter(([name, msg]) => name.toLowerCase().includes(q) || msg.toLowerCase().includes(q))
    : rows;
  if (rows.length === 0) {
    return <div className={shared.empty}>{text("No errors.", "没有错误。")}</div>;
  }
  if (shown.length === 0) {
    return <div className={shared.empty}>{text("No matches.", "没有匹配结果。")}</div>;
  }
  return (
    <div>
      {shown.map(([name, msg]) => (
        <div key={name} className={styles.errorBox}>
          <strong>{name}</strong>
          {"\n"}
          {msg}
        </div>
      ))}
    </div>
  );
}
