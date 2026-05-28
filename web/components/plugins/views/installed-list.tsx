"use client";

import { useState } from "react";
import styles from "../plugins.module.css";
import { usePluginsStore, type PluginRow } from "@/lib/plugins-store";
import { useTranslation } from "@/lib/i18n";
import { PluginTrustWarning } from "../dialogs/plugin-trust-warning";
import { PluginOptionsDialog } from "../dialogs/plugin-options-dialog";
import { ValidatePluginDialog } from "../dialogs/validate-plugin";
import { PluginDetailDialog } from "../dialogs/plugin-detail";

function TrustBadge({ level }: { level: string }) {
  const { text } = useTranslation();
  if (level === "verified") return <span className={styles.badgeVerified}>{text("verified", "已验证")}</span>;
  if (level === "community") return <span className={styles.badgeCommunity}>{text("community", "社区")}</span>;
  return <span className={styles.badgeUntrusted}>{text("untrusted", "未信任")}</span>;
}

export function InstalledList() {
  const { text } = useTranslation();
  const { plugins, toggle, uninstall, reload } = usePluginsStore();
  const [trustDialog, setTrustDialog] = useState<PluginRow | null>(null);
  const [optsDialog, setOptsDialog] = useState<PluginRow | null>(null);
  const [validateDialog, setValidateDialog] = useState<PluginRow | null>(null);
  const [detailDialog, setDetailDialog] = useState<PluginRow | null>(null);
  const [busy, setBusy] = useState<string>("");

  const tryToggle = async (p: PluginRow) => {
    if (!p.enabled && p.trust === "untrusted") {
      setTrustDialog(p);
      return;
    }
    setBusy(p.name);
    try {
      const r = (await toggle(p.name, !p.enabled)) as { error?: string; code?: string };
      if (r && "error" in r && r.error) {
        if (r.code === "trust") {
          setTrustDialog(p);
        } else {
          alert(r.error);
        }
      }
    } finally {
      setBusy("");
    }
  };

  if (plugins.length === 0) {
    return <div className={styles.empty}>{text("No installed plugins. Install from Marketplace or pin a local directory.", "暂无已安装插件。可从 Marketplace 安装或本地 pin 一个目录。")}</div>;
  }

  return (
    <div>
      {plugins.map((p) => (
        <div key={p.name} className={styles.row}>
          <div className={styles.rowMain}>
            <div className={styles.rowName}>
              {p.name}{" "}
              <span style={{ fontSize: 12, fontWeight: 400, color: "var(--text-dim)" }}>
                v{p.version}
              </span>
            </div>
            <div className={styles.rowMeta}>
              <TrustBadge level={p.trust} />
              <span className={styles.badge}>{p.source}</span>
              {p.deprecated && <span className={styles.badgeUntrusted}>{text("deprecated", "已废弃")}</span>}
              {p.error && <span className={styles.badgeUntrusted}>{text("error", "错误")}</span>}
            </div>
            {p.description && <div className={styles.rowDesc}>{p.description}</div>}
          </div>
          <button
            className={p.enabled ? styles.toggleOn : styles.toggle}
            onClick={() => tryToggle(p)}
            disabled={busy === p.name}
            title={p.enabled ? text("Disable", "禁用") : text("Enable", "启用")}
          >
            <span className={p.enabled ? styles.toggleKnobOn : styles.toggleKnob} />
          </button>
          <button className={styles.btn} onClick={() => setOptsDialog(p)}>{text("Options", "选项")}</button>
          <button className={styles.btn} onClick={() => setValidateDialog(p)}>{text("Validate", "校验")}</button>
          <button
            className={styles.btn}
            onClick={async () => {
              setBusy(p.name);
              try { await reload(p.name); } finally { setBusy(""); }
            }}
            disabled={busy === p.name}
          >{text("Reload", "重新加载")}</button>
          <button className={styles.btn} onClick={() => setDetailDialog(p)}>{text("Detail", "详情")}</button>
          <button
            className={styles.btnDanger}
            onClick={async () => {
              if (!confirm(`卸载 ${p.name}?`)) return;
              setBusy(p.name);
              try {
                const r = await uninstall(p.name);
                if (!r.success) alert(r.log);
              } finally {
                setBusy("");
              }
            }}
            disabled={busy === p.name}
          >{text("Uninstall", "卸载")}</button>
        </div>
      ))}

      {trustDialog && (
        <PluginTrustWarning
          name={trustDialog.name}
          currentLevel={trustDialog.trust}
          onDone={async () => {
            // After the user elevates trust, finish the original
            // enable flow they started — toggling the plugin on.
            const target = trustDialog;
            setTrustDialog(null);
            setBusy(target.name);
            try {
              const r = (await toggle(target.name, true)) as { error?: string; code?: string };
              if (r && "error" in r && r.error) alert(r.error);
            } finally {
              setBusy("");
            }
          }}
          onCancel={() => setTrustDialog(null)}
        />
      )}
      {optsDialog && (
        <PluginOptionsDialog name={optsDialog.name} onClose={() => setOptsDialog(null)} />
      )}
      {validateDialog && (
        <ValidatePluginDialog name={validateDialog.name} onClose={() => setValidateDialog(null)} />
      )}
      {detailDialog && (
        <PluginDetailDialog plugin={detailDialog} onClose={() => setDetailDialog(null)} />
      )}
    </div>
  );
}
