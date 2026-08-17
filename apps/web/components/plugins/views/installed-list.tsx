"use client";

import { useMemo, useState } from "react";
import { usePluginsStore, type PluginRow } from "@/lib/state/plugins-store";
import { useTranslation } from "@/lib/i18n";
import { Switch } from "@/components/ui/switch";
import { SearchInput } from "@/components/ui/search-input";
import { Button } from "@/components/ui/button";
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

export function InstalledList() {
  const { text } = useTranslation();
  const { plugins, toggle, uninstall, reload } = usePluginsStore();
  const [trustDialog, setTrustDialog] = useState<PluginRow | null>(null);
  const [optsDialog, setOptsDialog] = useState<PluginRow | null>(null);
  const [validateDialog, setValidateDialog] = useState<PluginRow | null>(null);
  const [detailDialog, setDetailDialog] = useState<PluginRow | null>(null);
  const [busy, setBusy] = useState<string>("");
  const [filter, setFilter] = useState("");

  const shown = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return plugins;
    return plugins.filter(
      (p) =>
        p.name.toLowerCase().includes(q) ||
        (p.description || "").toLowerCase().includes(q) ||
        (p.source || "").toLowerCase().includes(q),
    );
  }, [plugins, filter]);

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
    return <div className={shared.empty}>{text("No installed plugins. Install from Marketplace or pin a local directory.", "暂无已安装插件。可从 Marketplace 安装或本地 pin 一个目录。")}</div>;
  }

  return (
    <div>
      <div className="mb-3">
        <SearchInput
          value={filter}
          onChange={setFilter}
          placeholder={text("Search plugins...", "搜索插件...")}
        />
      </div>
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
              <span className={shared.badge}>{p.source}</span>
              {p.deprecated && (
                <span className={`${shared.badge} ${shared.badgeRed}`}>{text("deprecated", "已废弃")}</span>
              )}
              {p.error && (
                <span className={`${shared.badge} ${shared.badgeRed}`}>{text("error", "错误")}</span>
              )}
            </>
          }
          actions={
            <>
              <Button size="sm" variant="outline" onClick={() => setOptsDialog(p)}>
                {text("Options", "选项")}
              </Button>
              <Button size="sm" variant="outline" onClick={() => setValidateDialog(p)}>
                {text("Validate", "校验")}
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={busy === p.name}
                onClick={async () => {
                  setBusy(p.name);
                  try { await reload(p.name); } finally { setBusy(""); }
                }}
              >{text("Reload", "重新加载")}</Button>
              <Button
                size="sm"
                variant="destructive"
                disabled={busy === p.name}
                onClick={async () => {
                  if (!confirm(text(`Uninstall ${p.name}?`, `卸载 ${p.name}？`))) return;
                  setBusy(p.name);
                  try {
                    const r = await uninstall(p.name);
                    if (!r.success) alert(r.log);
                  } finally {
                    setBusy("");
                  }
                }}
              >{text("Uninstall", "卸载")}</Button>
              <Switch
                checked={p.enabled}
                disabled={busy === p.name}
                onCheckedChange={() => void tryToggle(p)}
                aria-label={p.enabled ? text("Disable", "禁用") : text("Enable", "启用")}
              />
            </>
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
