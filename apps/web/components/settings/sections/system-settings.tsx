"use client";

/**
 * System settings — schema-driven editor on the shared Settings rows.
 * Renders the SAME settings the TUI panel and `openprogram config`
 * edit, fetched from /api/settings (backed by openprogram.state.settings).
 * One SettingSpec server-side → one row here. See docs/design/cli/redesign.md.
 */
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { SystemAccess } from "./system-access";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import { Input } from "@/components/ui/input";
import { SimpleSelect } from "@/components/ui/select";
import { useTranslation } from "@/lib/i18n";
import { settingRowsQuery, systemAccessQuery, type SettingRow } from "@/lib/settings/system-page-queries";
import { cn } from "@/lib/utils";
import { SettingsPage, SettingsRow, SettingsSection } from "../settings-ui";

// On the web, only show settings that have NO dedicated page. Providers,
// Search, Memory, and Tools already have their own surfaces (the Providers,
// Search, and Memory settings tabs), so re-listing them here would just
// duplicate them. The schema still feeds all of them on the TUI (which has
// no settings pages) and the CLI — this is purely which groups the web
// chooses to render. Ports and the model fallback chain have no other page.
const WEB_GROUPS = ["Models", "Ports"];

export function SystemSettings() {
  const { t, text } = useTranslation();
  const client = useQueryClient();
  const settings = useQuery(settingRowsQuery);
  const access = useQuery(systemAccessQuery);
  const [status, setStatus] = useState<Record<string, string>>({});
  const rows = (settings.data ?? []).filter((r) => WEB_GROUPS.includes(r.group));
  const loaded = !settings.isPending && !access.isPending;

  async function save(key: string, value: unknown) {
    let res: { applied?: string; value?: unknown; note?: string; error?: string };
    try {
      res = await fetch("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key, value }),
      }).then((r) => r.json());
    } catch (e) {
      res = { error: String(e) };
    }
    if (res.error) {
      setStatus((s) => ({ ...s, [key]: `✗ ${res.error}` }));
      return;
    }
    client.setQueryData(settingRowsQuery.queryKey, (all) =>
      all?.map((r) => (r.key === key ? { ...r, value: res.value } : r)));
    const when = res.applied === "next_start"
      ? text("Saved · takes effect on next start", "已保存 · 下次启动后生效")
      : text("Saved", "已保存");
    const saved = `✓ ${when}${res.note ? ` · ${res.note}` : ""}`;
    setStatus((s) => ({ ...s, [key]: saved }));
    // "Takes effect on next start" stays true; a plain "Saved" is only news for a moment.
    if (res.applied !== "next_start") {
      window.setTimeout(() => {
        setStatus((s) => {
          if (s[key] !== saved) return s;
          const { [key]: _done, ...rest } = s;
          return rest;
        });
      }, 3000);
    }
  }

  const groups: string[] = [];
  rows.forEach((r) => {
    if (!groups.includes(r.group)) groups.push(r.group);
  });

  return (
    <SettingsPage
      title={t("settings.tab.system")}
      description={text(
        "Settings with no dedicated page. Some take effect on the next start.",
        "没有独立页面的设置。部分项会在下次启动后生效。",
      )}
    >
      {loaded ? (
        <>
          <SystemAccess />
          {groups.map((g) => (
            <SettingsSection key={g} title={g === "Ports" ? text("Web server", "网页服务") : g}>
              {rows.filter((r) => r.group === g).map((r) => (
                <SettingsRow
                  key={r.key}
                  label={r.label}
                  description={<Description row={r} status={status[r.key]} />}
                >
                  <Control row={r} onSave={save} />
                </SettingsRow>
              ))}
            </SettingsSection>
          ))}
        </>
      ) : null}
    </SettingsPage>
  );
}

/** A setting's help, then its save result or when a change takes effect. */
function Description({ row, status }: { row: SettingRow; status?: string }) {
  const { text } = useTranslation();
  const note = status || (row.apply === "next_start" ? text("Takes effect on next start", "下次启动后生效") : "");
  const tone = status?.startsWith("✗") ? "text-danger-soft" : status ? "text-accent-green" : "";
  return (
    <>
      {row.help}
      {note ? <span className={cn("mt-1 block", tone)}>{note}</span> : null}
    </>
  );
}

function Control({ row, onSave }: { row: SettingRow; onSave: (k: string, v: unknown) => void }) {
  const { text } = useTranslation();
  if (row.widget === "status") {
    const ok = !!row.value;
    return (
      <Badge variant={ok ? "default" : "secondary"}>
        {ok ? text("Configured", "已配置") : text("Not configured", "未配置")}
      </Badge>
    );
  }
  if (row.widget === "toggle") {
    return (
      <Switch
        checked={!!row.value}
        onCheckedChange={(v) => onSave(row.key, v)}
      />
    );
  }
  if (row.widget === "enum") {
    return (
      <SimpleSelect
        label={row.label}
        value={String(row.value)}
        onValueChange={(value) => onSave(row.key, value)}
        options={(row.choices || []).map((c) => ({ value: c, label: c }))}
      />
    );
  }
  // Plain text input — a port number is typed, not nudged one at a time,
  // so no <input type="number"> spinner arrows; numbers get the numeric
  // keyboard. Text and JSON values (bind address, allowed origins) get a
  // wider, left-aligned field. The backend validates and reports inline.
  const isNumber = row.widget === "number";
  const initial = typeof row.value === "object" && row.value !== null
    ? JSON.stringify(row.value)
    : String(row.value ?? "");
  return (
    <Input
      type="text"
      inputMode={isNumber ? "numeric" : undefined}
      spellCheck={false}
      data-kind={isNumber ? "number" : "text"}
      defaultValue={initial}
      className={isNumber ? undefined : "font-mono"}
      onBlur={(e) => {
        if (e.target.value !== initial) onSave(row.key, e.target.value);
      }}
      onKeyDown={(e) => {
        if (e.key === "Enter") (e.target as HTMLInputElement).blur();
      }}
    />
  );
}
