"use client";

/**
 * One group of the schema-driven settings — the SAME settings the TUI panel
 * and `openprogram config` edit, read from /api/settings (backed by
 * openprogram.state.settings), one SettingSpec server-side → one row here.
 * The web shows only the groups with no page of their own, each on the page
 * it belongs to: "Models" on Model Services → Behavior, "Ports" on General.
 */
import { useState, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import { Input } from "@/components/ui/input";
import { SimpleSelect } from "@/components/ui/select";
import { useTranslation } from "@/lib/i18n";
import { errorDetailText } from "@/lib/net/error-text";
import { settingRowsQuery, type SettingRow } from "@/lib/settings/system-page-queries";
import { cn } from "@/lib/utils";
import { SettingsRow, SettingsSection } from "../settings-ui";

/** What saving one setting answers. */
interface SaveReply {
  applied?: string;
  value?: unknown;
  note?: string;
  error?: unknown;
}

export function SchemaSettings({
  group,
  title,
  description,
}: {
  group: string;
  title?: ReactNode;
  description?: ReactNode;
}) {
  const { text } = useTranslation();
  const client = useQueryClient();
  const settings = useQuery(settingRowsQuery);
  const [status, setStatus] = useState<Record<string, string>>({});
  const rows = (settings.data ?? []).filter((r) => r.group === group);

  async function save(key: string, value: unknown) {
    let res: SaveReply;
    try {
      res = await fetch("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key, value }),
      }).then((r) => r.json() as Promise<SaveReply>);
    } catch (e) {
      res = { error: String(e) };
    }
    if (res.error) {
      const reason = errorDetailText(res.error, text("Not saved", "未保存"));
      setStatus((s) => ({ ...s, [key]: `✗ ${reason}` }));
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

  if (!rows.length) return null;
  return (
    <SettingsSection title={title} description={description}>
      {rows.map((r) => (
        <SettingsRow
          key={r.key}
          label={r.label}
          description={<Description row={r} status={status[r.key]} />}
        >
          <Control row={r} onSave={save} />
        </SettingsRow>
      ))}
    </SettingsSection>
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
