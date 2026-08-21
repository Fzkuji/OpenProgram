"use client";

import { useMemo, useState } from "react";
import { usePluginsStore, type PluginRow } from "@/lib/state/plugins-store";
import { useTranslation } from "@/lib/i18n";
import { Switch } from "@/components/ui/switch";
import { SearchInput } from "@/components/ui/search-input";
import { ManageRow, managePageStyles as shared } from "@/components/ui/manage-page";
import { BlocksIcon } from "@/components/animated-icons";
import { PluginTrustWarning } from "../dialogs/plugin-trust-warning";
import { PluginOptionsDialog } from "../dialogs/plugin-options-dialog";
import { ValidatePluginDialog } from "../dialogs/validate-plugin";
import { PluginDetailDialog } from "../dialogs/plugin-detail";

function TrustBadge({ level }: { level: string }) {
  const { text } = useTranslation();
  if (level === "verified")
    return <span className={`${shared.badge} ${shared.badgeGreen}`}>{text("verified", "已验证")}</span>;
  if (level === "community")
    return <span className={`${shared.badge} ${shared.badgeYellow}`}>{text("community", "社区")}</span>;
  return <span className={`${shared.badge} ${shared.badgeRed}`}>{text("untrusted", "未信任")}</span>;
}

export function InstalledList({ externalFilter }: { externalFilter?: string } = {}) {
  const { text } = useTranslation();
  const { plugins, toggle, uninstall, reload } = usePluginsStore();
  const [trustDialog, setTrustDialog] = useState<PluginRow | null>(null);
  const [optsDialog, setOptsDialog] = useState<PluginRow | null>(null);
  const [validateDialog, setValidateDialog] = useState<PluginRow | null>(null);
  const [detailDialog, setDetailDialog] = useState<PluginRow | null>(null);
  const [busy, setBusy] = useState<string>("");
  const [filter, setFilter] = useState("");
  const [notice, setNotice] = useState("");
  const filterValue = externalFilter !== undefined ? externalFilter : filter;

  const shown = useMemo(() => {
    const q = filterValue.trim().toLowerCase();
    if (!q) return plugins;
    return plugins.filter(
      (p) =>
        p.name.toLowerCase().includes(q) ||
        (p.description || "").toLowerCase().includes(q) ||
        (p.source || "").toLowerCase().includes(q),
    );
  }, [plugins, filterValue]);

  const tryToggle = async (p: PluginRow) => {
    if (!p.enabled && p.trust === "untrusted") {
      setTrustDialog(p);
      return;
    }
    setBusy(p.name);
    try {
      const r = (await toggle(p.name, !p.enabled)) as { error?: string; code?: string };
      if (r && "error" in r && r.error) {
        if (r.code === "trust") setTrustDialog(p);
        else setNotice(r.error);
      }
    } finally {
      setBusy("");
    }
  };

  if (plugins.length === 0) {
    return (
      <div className={shared.empty}>
        {text("No plugins yet. Install from the marketplace, or add a local folder.", "还没有插件。可以从市场安装，或添加一个本地文件夹。")}
      </div>
    );
  }

  return (
    <div>
      {externalFilter === undefined && (
      <div className="mb-3">
        <SearchInput
          value={filter}
          onChange={setFilter}
          placeholder={text("Search plugins...", "搜索插件...")}
        />
      </div>
      )}
      {notice && <div className={shared.empty} style={{ padding: "8px 0" }}>{notice}</div>}
      <div className="space-y-1">
      {shown.map((p) => (
        <ManageRow
          key={p.name}
          icon={<BlocksIcon size={16} />}
          name={p.name}
          description={p.description}
          onClick={() => setDetailDialog(p)}
          title={text("Open plugin details", "查看插件详情")}
          meta={
            <>
              <span className={shared.rowCount}>v{p.version}</span>
              <TrustBadge level={p.trust} />
              {p.error && (
                <span className={`${shared.badge} ${shared.badgeRed}`}>{text("error", "错误")}</span>
              )}
            </>
          }
          actions={
            <Switch
              checked={p.enabled}
              disabled={busy === p.name}
              onCheckedChange={() => { void tryToggle(p); }}
              aria-label={p.enabled ? text("Disable", "禁用") : text("Enable", "启用")}
            />
          }
        />
      ))}
      {shown.length === 0 && (
        <div className={shared.empty}>{text("No matches.", "没有匹配结果。")}</div>
      )}
      </div>

      {trustDialog && (
        <PluginTrustWarning
          name={trustDialog.name}
          currentLevel={trustDialog.trust}
          onDone={async () => {
            const target = trustDialog;
            setTrustDialog(null);
            setBusy(target.name);
            try {
              const r = (await toggle(target.name, true)) as { error?: string; code?: string };
              if (r && "error" in r && r.error) setNotice(r.error);
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
        <PluginDetailDialog
          plugin={detailDialog}
          busy={busy === detailDialog.name}
          onClose={() => setDetailDialog(null)}
          onOptions={() => { const target = detailDialog; setDetailDialog(null); setOptsDialog(target); }}
          onValidate={() => { const target = detailDialog; setDetailDialog(null); setValidateDialog(target); }}
          onReload={async () => {
            setBusy(detailDialog.name);
            try { await reload(detailDialog.name); } finally { setBusy(""); }
          }}
          onUninstall={async () => {
            if (!window.confirm(text("Uninstall this plugin?", "卸载这个插件？"))) return;
            const target = detailDialog;
            setDetailDialog(null);
            setBusy(target.name);
            try {
              const r = await uninstall(target.name);
              if (!r.success) setNotice(r.log);
            } finally {
              setBusy("");
            }
          }}
        />
      )}
    </div>
  );
}
