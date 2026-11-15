"use client";

import { useMemo, useState } from "react";
import { usePluginsStore, type PluginRow } from "@/lib/abilities/plugins-store";
import { useTranslation } from "@/lib/i18n";
import { Switch } from "@/components/ui/switch";
import { SearchInput } from "@/components/ui/search-input";
import { ManageEmptyState, ManageRow, managePageStyles as shared } from "@/components/ui/manage-page";
import { BlocksIcon, SearchIcon } from "@/components/animated-icons";
import { PluginTrustWarning } from "../dialogs/plugin-trust-warning";
import { PluginOptionsDialog } from "../dialogs/plugin-options-dialog";
import { ValidatePluginDialog } from "../dialogs/validate-plugin";
import { PluginDetailDialog } from "../dialogs/plugin-detail";
import { confirmAction } from "@/components/ui/confirm-dialog";

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
      <ManageEmptyState
        compact
        icon={<BlocksIcon size={20} />}
        title={text("No plugins yet", "还没有插件")}
        description={text("Install from Discover, or add a local folder with Add plugin.", "可以在“发现”中安装，或通过“添加插件”添加本地文件夹。")}
      />
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
              aria-label={p.enabled ? text(`Disable ${p.name}`, `禁用 ${p.name}`) : text(`Enable ${p.name}`, `启用 ${p.name}`)}
            />
          }
        />
      ))}
      {shown.length === 0 && (
        <ManageEmptyState compact icon={<SearchIcon size={20} />} title={text("No matching plugins", "没有匹配的插件")} description={text("Try a different search.", "换个关键词试试。")} />
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
            if (!(await confirmAction({
              title: text("Uninstall this plugin?", "卸载这个插件？"),
              confirmLabel: text("Uninstall", "卸载"),
              destructive: true,
            }))) return;
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
