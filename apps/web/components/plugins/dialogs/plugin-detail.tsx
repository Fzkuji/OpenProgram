"use client";

import styles from "../plugins.module.css";
import type { PluginRow } from "@/lib/state/plugins-store";
import { useTranslation } from "@/lib/i18n";
import { useModalA11y } from "@/lib/use-modal-a11y";
import { Button } from "@/components/ui/button";

interface Props {
  plugin: PluginRow;
  busy?: boolean;
  onClose: () => void;
  onOptions?: () => void;
  onValidate?: () => void;
  onReload?: () => void;
  onUninstall?: () => void;
}

export function PluginDetailDialog({
  plugin,
  busy,
  onClose,
  onOptions,
  onValidate,
  onReload,
  onUninstall,
}: Props) {
  const { text } = useTranslation();
  const modal = useModalA11y(onClose, plugin.name);
  const contrib = Object.keys(plugin.entrypoints || {});

  return (
    <div className={styles.dialogBackdrop} onClick={onClose}>
      <div
        {...modal}
        className={styles.dialog}
        onClick={(e) => e.stopPropagation()}
      >
        <div className={styles.dialogTitle}>{plugin.name}</div>
        <div className={styles.dialogBody}>
          <div className={styles.rowMeta}>
            v{plugin.version}
            {plugin.source ? ` · ${plugin.source}` : ""}
            {plugin.deprecated ? text(" · deprecated", " · 已废弃") : ""}
          </div>
          {plugin.description && <p style={{ marginTop: 8 }}>{plugin.description}</p>}
          {contrib.length > 0 && (
            <p style={{ marginTop: 8 }}>
              {text("Provides", "提供")} {contrib.join(", ")}
            </p>
          )}
          {plugin.error && (
            <pre className={styles.errorBox} style={{ marginTop: 12 }}>{plugin.error}</pre>
          )}
        </div>
        <div className={styles.dialogActions}>
          {onOptions && (
            <Button size="sm" variant="outline" onClick={onOptions}>{text("Options", "选项")}</Button>
          )}
          {onValidate && (
            <Button size="sm" variant="outline" onClick={onValidate}>{text("Validate", "校验")}</Button>
          )}
          {onReload && (
            <Button size="sm" variant="outline" disabled={busy} onClick={() => { void onReload(); }}>
              {text("Reload", "重新加载")}
            </Button>
          )}
          {onUninstall && (
            <Button size="sm" variant="destructive" disabled={busy} onClick={() => { void onUninstall(); }}>
              {text("Uninstall", "卸载")}
            </Button>
          )}
          <Button size="sm" variant="outline" onClick={onClose}>{text("Close", "关闭")}</Button>
        </div>
      </div>
    </div>
  );
}
