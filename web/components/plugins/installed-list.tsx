"use client";

import { useState } from "react";
import styles from "./plugins.module.css";
import { usePluginsStore, type PluginRow } from "@/lib/plugins-store";
import { PluginTrustWarning } from "./plugin-trust-warning";
import { PluginOptionsDialog } from "./plugin-options-dialog";
import { ValidatePluginDialog } from "./validate-plugin";
import { PluginDetailDialog } from "./plugin-detail";

function TrustBadge({ level }: { level: string }) {
  if (level === "verified") return <span className={styles.badgeVerified}>verified</span>;
  if (level === "community") return <span className={styles.badgeCommunity}>community</span>;
  return <span className={styles.badgeUntrusted}>untrusted</span>;
}

export function InstalledList() {
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
    return <div className={styles.empty}>暂无已安装插件。可从 Marketplace 安装或本地 pin 一个目录。</div>;
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
              {p.deprecated && <span className={styles.badgeUntrusted}>deprecated</span>}
              {p.error && <span className={styles.badgeUntrusted}>error</span>}
            </div>
            {p.description && <div className={styles.rowDesc}>{p.description}</div>}
          </div>
          <button
            className={p.enabled ? styles.toggleOn : styles.toggle}
            onClick={() => tryToggle(p)}
            disabled={busy === p.name}
            title={p.enabled ? "Disable" : "Enable"}
          >
            <span className={p.enabled ? styles.toggleKnobOn : styles.toggleKnob} />
          </button>
          <button className={styles.btn} onClick={() => setOptsDialog(p)}>Options</button>
          <button className={styles.btn} onClick={() => setValidateDialog(p)}>Validate</button>
          <button
            className={styles.btn}
            onClick={async () => {
              setBusy(p.name);
              try { await reload(p.name); } finally { setBusy(""); }
            }}
            disabled={busy === p.name}
          >Reload</button>
          <button className={styles.btn} onClick={() => setDetailDialog(p)}>Detail</button>
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
          >Uninstall</button>
        </div>
      ))}

      {trustDialog && (
        <PluginTrustWarning
          name={trustDialog.name}
          currentLevel={trustDialog.trust}
          onDone={() => setTrustDialog(null)}
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
