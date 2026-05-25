"use client";

import { useEffect, useState } from "react";
import styles from "./plugins.module.css";
import { usePluginsStore } from "@/lib/plugins-store";
import { InstalledList } from "./installed-list";
import { MarketplaceBrowser } from "./marketplace-browser";
import { PluginErrors } from "./plugin-errors";

export function PluginsPage() {
  const { tab, setTab, refresh, plugins, errors } = usePluginsStore();
  const [installOpen, setInstallOpen] = useState(false);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const errCount = Object.keys(errors).length + plugins.filter((p) => p.error).length;

  return (
    <div className="main" style={{ minWidth: 0, overflow: "hidden" }}>
    <div className={styles.view}>
      <div className={styles.topbar}>
        <div className={styles.title}>Plugins</div>
        <div className={styles.tabs}>
          <button
            className={tab === "installed" ? styles.tabActive : styles.tab}
            onClick={() => setTab("installed")}
          >Installed ({plugins.length})</button>
          <button
            className={tab === "marketplace" ? styles.tabActive : styles.tab}
            onClick={() => setTab("marketplace")}
          >Marketplace</button>
          <button
            className={tab === "errors" ? styles.tabActive : styles.tab}
            onClick={() => setTab("errors")}
          >Errors ({errCount})</button>
        </div>
        <div className={styles.spacer} />
        <button className={styles.btn} onClick={() => refresh()}>Refresh</button>
        <button className={styles.btnPrimary} onClick={() => setInstallOpen(true)}>+ Install</button>
      </div>
      <div className={styles.body}>
        {tab === "installed" && <InstalledList />}
        {tab === "marketplace" && <MarketplaceBrowser />}
        {tab === "errors" && <PluginErrors />}
      </div>
      {installOpen && <ManualInstallDialog onClose={() => setInstallOpen(false)} />}
    </div>
    </div>
  );
}

function ManualInstallDialog({ onClose }: { onClose: () => void }) {
  const install = usePluginsStore((s) => s.install);
  const [source, setSource] = useState("pip");
  const [spec, setSpec] = useState("");
  const [ref, setRef] = useState("");
  const [busy, setBusy] = useState(false);
  const [log, setLog] = useState("");

  const submit = async () => {
    if (!spec.trim()) return;
    setBusy(true);
    try {
      const r = await install(source, spec.trim(), ref.trim() || undefined);
      setLog(r.log);
      if (r.success) {
        // 留窗显示成功，并允许关闭
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={styles.dialogBackdrop} onClick={onClose}>
      <div className={styles.dialog} onClick={(e) => e.stopPropagation()}>
        <div className={styles.dialogTitle}>手动安装插件</div>
        <div className={styles.dialogBody}>
          <div style={{ display: "flex", gap: 8, marginBottom: 8 }}>
            <select className={styles.select} value={source} onChange={(e) => setSource(e.target.value)}>
              <option value="pip">pip</option>
              <option value="npm">npm</option>
              <option value="git">git</option>
              <option value="path">path (本地目录)</option>
            </select>
            <input
              className={styles.input}
              placeholder={
                source === "path"
                  ? "/absolute/path/to/plugin"
                  : source === "git"
                  ? "https://github.com/user/repo.git"
                  : "package-name"
              }
              value={spec}
              onChange={(e) => setSpec(e.target.value)}
            />
          </div>
          {source === "git" && (
            <input
              className={styles.input}
              placeholder="ref (可选，branch/tag/sha)"
              value={ref}
              onChange={(e) => setRef(e.target.value)}
            />
          )}
          {log && (
            <pre style={{
              marginTop: 12,
              padding: 10,
              background: "var(--bg-secondary)",
              border: "1px solid var(--border)",
              borderRadius: 6,
              fontSize: 12,
              maxHeight: 240,
              overflow: "auto",
            }}>{log}</pre>
          )}
        </div>
        <div className={styles.dialogActions}>
          <button className={styles.btn} onClick={onClose} disabled={busy}>关闭</button>
          <button className={styles.btnPrimary} onClick={submit} disabled={busy || !spec.trim()}>
            {busy ? "安装中…" : "安装"}
          </button>
        </div>
      </div>
    </div>
  );
}
