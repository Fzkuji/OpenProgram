"use client";

import styles from "../plugins.module.css";
import { ManageEmptyState } from "@/components/ui/manage-page";
import { CheckIcon, SearchIcon } from "@/components/animated-icons";
import { usePluginsStore } from "@/lib/abilities/plugins-store";
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
    return (
      <ManageEmptyState
        compact
        icon={<CheckIcon size={20} />}
        title={text("No issues", "没有问题")}
        description={text("Plugins that fail to load or validate are listed here.", "加载或校验失败的插件会显示在这里。")}
      />
    );
  }
  if (shown.length === 0) {
    return <ManageEmptyState compact icon={<SearchIcon size={20} />} title={text("No matching issues", "没有匹配的问题")} description={text("Try a different search.", "换个关键词试试。")} />;
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
