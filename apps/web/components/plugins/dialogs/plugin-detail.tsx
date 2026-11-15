"use client";

import styles from "../plugins.module.css";
import type { PluginRow } from "@/lib/abilities/plugins-store";
import { useTranslation } from "@/lib/i18n";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
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
  const contrib = Object.keys(plugin.entrypoints || {});

  return (
    <Dialog open onOpenChange={(open) => { if (!open) onClose(); }}>
      <DialogContent className={styles.dialog} aria-describedby={undefined}>
        <DialogHeader><DialogTitle>{plugin.name}</DialogTitle></DialogHeader>
        <div className={styles.dialogBody}>
          <div className={styles.rowMeta}>
            v{plugin.version}
            {plugin.source ? ` · ${plugin.source}` : ""}
            {plugin.deprecated ? text(" · deprecated", " · 已废弃") : ""}
          </div>
          {plugin.description && <p style={{ marginTop: 8 }}>{plugin.description}</p>}
          {plugin.loaded && Object.keys(plugin.contributions || {}).length > 0 && (
            <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-fs-xs">
              {Object.entries(plugin.contributions || {}).map(([kind, names]) => (
                <div key={kind} className="contents">
                  <dt className="text-text-muted">{kind}</dt>
                  <dd className="break-all">{names.join(", ")}</dd>
                </div>
              ))}
            </dl>
          )}
          {contrib.length > 0 && (
            <p style={{ marginTop: 8 }}>
              {text("Provides", "提供")} {contrib.join(", ")}
            </p>
          )}
          {plugin.error && (
            <pre className={styles.errorBox} style={{ marginTop: 12 }}>{plugin.error}</pre>
          )}
        </div>
        <DialogFooter>
          {onOptions && (
            <Button variant="elevated" onClick={onOptions}>{text("Options", "选项")}</Button>
          )}
          {onValidate && (
            <Button variant="elevated" onClick={onValidate}>{text("Validate", "校验")}</Button>
          )}
          {onReload && (
            <Button variant="elevated" disabled={busy} onClick={() => { void onReload(); }}>
              {text("Reload", "重新加载")}
            </Button>
          )}
          {onUninstall && (
            <Button variant="destructive" disabled={busy} onClick={() => { void onUninstall(); }}>
              {text("Uninstall", "卸载")}
            </Button>
          )}
          <Button variant="elevated" onClick={onClose}>{text("Close", "关闭")}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
