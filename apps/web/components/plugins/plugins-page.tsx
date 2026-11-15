"use client";

import { useEffect, useState } from "react";
import styles from "./plugins.module.css";
import { ManagePageHeader, ManageSubnav, managePageStyles as shared, ManageLoadingState } from "@/components/ui/manage-page";
import { usePluginsStore } from "@/lib/abilities/plugins-store";
import { useTranslation } from "@/lib/i18n";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { PlusIcon } from "@/components/animated-icons";
import { InstalledList } from "./views/installed-list";
import { SimpleSelect } from "@/components/ui/select";
import { MarketplaceBrowser } from "./views/marketplace-browser";
import { PluginErrors } from "./views/plugin-errors";
import { Input } from "@/components/ui/input";

export function PluginsPage({
  embedded,
  query,
  installOpen: installOpenProp,
  onInstallClose,
}: {
  embedded?: boolean;
  query?: string;
  installOpen?: boolean;
  onInstallClose?: () => void;
} = {}) {
  const { t, text } = useTranslation();
  const { tab, setTab, refresh, plugins, errors, loadError, loading } = usePluginsStore();
  const [localInstallOpen, setLocalInstallOpen] = useState(false);
  const installOpen = installOpenProp ?? localInstallOpen;
  const closeInstall = onInstallClose ?? (() => setLocalInstallOpen(false));

  useEffect(() => {
    refresh();
  }, [refresh]);

  const issueNames = new Set([
    ...Object.keys(errors),
    ...plugins.filter((plugin) => plugin.error).map((plugin) => plugin.name),
  ]);
  const errCount = issueNames.size;
  const enabledCount = plugins.filter((p) => p.enabled && p.loaded && !p.error).length;

  const tabs = [
    { id: "installed", label: text("Installed", "已安装"), count: plugins.length },
    { id: "marketplace", label: text("Discover", "发现") },
    { id: "errors", label: text("Issues", "问题"), count: errCount },
  ];

  const body = (
    <>
      {tab === "installed" && (loading && plugins.length === 0
        ? <ManageLoadingState>{text("Loading plugins…", "正在加载插件…")}</ManageLoadingState>
        : <InstalledList externalFilter={query} />)}
      {tab === "marketplace" && <MarketplaceBrowser externalFilter={query} />}
      {tab === "errors" && <PluginErrors filter={query} />}
    </>
  );

  const content = (
    <>
      <ManageSubnav
        tabs={tabs}
        activeTab={tab}
        onTabChange={(id) => setTab(id as typeof tab)}
        summary={text(
          `${enabledCount} available · ${errCount} ${errCount === 1 ? "issue" : "issues"}`,
          `可用 ${enabledCount} 个 · ${errCount} 个问题`,
        )}
        action={{
          label: text("Add plugin", "添加插件"),
          onClick: () => setLocalInstallOpen(true),
          icon: PlusIcon,
          primary: true,
        }}
        ariaLabel={text("Plugin sections", "插件分区")}
        panelId="plugins-panel"
      />
      {loadError && <div className={shared.errorBar} role="alert">{loadError}</div>}
      <div
        id="plugins-panel"
        role="tabpanel"
        aria-labelledby={`plugins-panel-tab-${tab}`}
        className={shared.body}
      >{body}</div>
      {installOpen && <ManualInstallDialog onClose={closeInstall} />}
    </>
  );

  if (embedded) return content;

  return (
    <div className="main" style={{ minWidth: 0, overflow: "hidden" }}>
    <div className={shared.view}>
      <ManagePageHeader
        title={t("nav.plugins")}
        actions={[
          { label: t("sidebar.refresh"), onClick: () => { void refresh(); } },
        ]}
      />
      {content}
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
    } catch (e) {
      setLog(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open onOpenChange={(open) => { if (!open) onClose(); }}>
      <DialogContent className={styles.dialog} aria-describedby={undefined}>
        <DialogHeader><DialogTitle>{text("Manual plugin install", "手动安装插件")}</DialogTitle></DialogHeader>
        <div className={styles.dialogBody}>
          <div style={{ display: "flex", gap: 8, marginBottom: 8 }}>
            <SimpleSelect
              aria-label={text("Source", "来源")}
              value={source}
              onValueChange={setSource}
              options={[
                { value: "pip", label: "pip" },
                { value: "npm", label: "npm" },
                { value: "git", label: "git" },
                { value: "path", label: text("path (local directory)", "path（本地目录）") },
              ]}
            />
            <Input
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
            <Input
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
              borderRadius: "var(--radius-sm)",
              fontSize: "var(--fs-xs)",
              maxHeight: 240,
              overflow: "auto",
            }}>{log}</pre>
          )}
        </div>
        <DialogFooter>
          <Button variant="elevated" onClick={onClose} disabled={busy}>{text("Close", "关闭")}</Button>
          <Button onClick={submit} disabled={busy || !spec.trim()}>
            {busy ? text("Installing...", "安装中...") : text("Install", "安装")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
