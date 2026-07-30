"use client";

import { useEffect, useState } from "react";
import styles from "./plugins.module.css";
import { ManagePageHeader, managePageStyles as shared } from "@/components/ui/manage-page";
import { usePluginsStore } from "@/lib/state/plugins-store";
import { useTranslation } from "@/lib/i18n";
import { InstalledList } from "./views/installed-list";
import { MarketplaceBrowser } from "./views/marketplace-browser";
import { PluginErrors } from "./views/plugin-errors";

export function PluginsPage() {
  const { t, text } = useTranslation();
  const { tab, setTab, refresh, plugins, errors } = usePluginsStore();
  const [installOpen, setInstallOpen] = useState(false);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const errCount = Object.keys(errors).length + plugins.filter((p) => p.error).length;

  return (
    <div className="main" style={{ minWidth: 0, overflow: "hidden" }}>
    <div className={shared.view}>
      <ManagePageHeader
        title={t("nav.plugins")}
        tabs={[
          { id: "installed", label: text("Installed", "已安装"), count: plugins.length },
          { id: "marketplace", label: "Marketplace" },
          { id: "errors", label: text("Errors", "错误"), count: errCount },
        ]}
        activeTab={tab}
        onTabChange={(id) => setTab(id as typeof tab)}
        actions={[
          { label: t("sidebar.refresh"), onClick: () => { void refresh(); } },
          { label: text("Install", "安装"), onClick: () => setInstallOpen(true), primary: true },
        ]}
      />
      <div className={shared.body}>
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
  const { text } = useTranslation();
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
        <div className={styles.dialogTitle}>{text("Manual plugin install", "手动安装插件")}</div>
        <div className={styles.dialogBody}>
          <div style={{ display: "flex", gap: 8, marginBottom: 8 }}>
            <select className={styles.select} value={source} onChange={(e) => setSource(e.target.value)}>
              <option value="pip">pip</option>
              <option value="npm">npm</option>
              <option value="git">git</option>
              <option value="path">{text("path (local directory)", "path（本地目录）")}</option>
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
              placeholder={text("ref (optional, branch/tag/sha)", "ref（可选，branch/tag/sha）")}
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
          <button className={styles.btn} onClick={onClose} disabled={busy}>{text("Close", "关闭")}</button>
          <button className={styles.btnPrimary} onClick={submit} disabled={busy || !spec.trim()}>
            {busy ? text("Installing...", "安装中...") : text("Install", "安装")}
          </button>
        </div>
      </div>
    </div>
  );
}
