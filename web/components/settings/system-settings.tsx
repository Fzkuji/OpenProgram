"use client";

/**
 * System settings — schema-driven editor. Renders the SAME settings the
 * TUI panel and `openprogram config` edit, fetched from /api/settings
 * (backed by openprogram.config_schema). One SettingSpec server-side →
 * one row here; no per-field code. See docs/design/cli-redesign.md.
 */
import { useEffect, useState } from "react";

interface Row {
  key: string;
  group: string;
  label: string;
  widget: "number" | "toggle" | "enum" | "status";
  apply: "live" | "next_start";
  help?: string;
  value?: unknown;
  choices?: string[];
  set?: boolean;
}

// On the web, only show settings that have NO dedicated page. Providers,
// Search, Memory, and Tools already have their own surfaces (the Providers
// / Search tabs, the /memory page), so re-listing them here would just
// duplicate them. The schema still feeds all of them on the TUI (which has
// no settings pages) and the CLI — this is purely which groups the web
// chooses to render. Ports is the one genuinely-homeless setting.
const WEB_GROUPS = ["Ports"];

export function SystemSettings() {
  const [rows, setRows] = useState<Row[]>([]);
  const [status, setStatus] = useState<Record<string, string>>({});
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    fetch("/api/settings")
      .then((r) => r.json())
      .then((d) => setRows((d.settings || []).filter((r: Row) => WEB_GROUPS.includes(r.group))))
      .catch(() => {})
      .finally(() => setLoaded(true));
  }, []);

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
    setRows((rs) => rs.map((r) => (r.key === key ? { ...r, value: res.value } : r)));
    const when = res.applied === "next_start" ? "takes effect next start" : "saved";
    setStatus((s) => ({ ...s, [key]: `✓ ${when}${res.note ? ` · ${res.note}` : ""}` }));
  }

  const groups: string[] = [];
  rows.forEach((r) => {
    if (!groups.includes(r.group)) groups.push(r.group);
  });

  if (!loaded) {
    return <div style={{ padding: 24, color: "var(--text-dim)" }}>Loading…</div>;
  }

  return (
    <div style={{ padding: "8px 4px", maxWidth: 640 }}>
      {groups.map((g) => (
        <div key={g} style={{ marginBottom: 24 }}>
          <h3
            style={{
              fontSize: 12,
              textTransform: "uppercase",
              letterSpacing: 0.6,
              color: "var(--text-dim)",
              margin: "0 0 6px",
            }}
          >
            {g}
          </h3>
          {rows
            .filter((r) => r.group === g)
            .map((r) => (
              <div
                key={r.key}
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  gap: 16,
                  padding: "10px 0",
                  borderBottom: "1px solid var(--border, rgba(127,127,127,0.2))",
                }}
              >
                <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                  <span style={{ fontSize: 14 }}>{r.label}</span>
                  {r.help ? (
                    <span style={{ fontSize: 12, color: "var(--text-dim)" }}>{r.help}</span>
                  ) : null}
                  {status[r.key] ? (
                    <span
                      style={{
                        fontSize: 12,
                        color: status[r.key].startsWith("✗") ? "#ef4444" : "#10b981",
                      }}
                    >
                      {status[r.key]}
                    </span>
                  ) : r.apply === "next_start" ? (
                    <span style={{ fontSize: 11, color: "var(--text-dim)" }}>
                      takes effect next start
                    </span>
                  ) : null}
                </div>
                <Control row={r} onSave={save} />
              </div>
            ))}
        </div>
      ))}
    </div>
  );
}

function Control({ row, onSave }: { row: Row; onSave: (k: string, v: unknown) => void }) {
  if (row.widget === "status") {
    const ok = !!row.value;
    return (
      <span style={{ fontSize: 13, color: ok ? "#10b981" : "var(--text-dim)" }}>
        {ok ? "✓ configured" : "✗ not configured"}
      </span>
    );
  }
  if (row.widget === "toggle") {
    return (
      <input
        type="checkbox"
        checked={!!row.value}
        onChange={(e) => onSave(row.key, e.target.checked)}
      />
    );
  }
  if (row.widget === "enum") {
    return (
      <select
        value={String(row.value)}
        onChange={(e) => onSave(row.key, e.target.value)}
        style={{ padding: "4px 8px" }}
      >
        {(row.choices || []).map((c) => (
          <option key={c} value={c}>
            {c}
          </option>
        ))}
      </select>
    );
  }
  return (
    <input
      type="number"
      defaultValue={String(row.value ?? "")}
      style={{ width: 110, padding: "4px 8px", textAlign: "right" }}
      onBlur={(e) => {
        if (e.target.value !== String(row.value)) onSave(row.key, e.target.value);
      }}
      onKeyDown={(e) => {
        if (e.key === "Enter") (e.target as HTMLInputElement).blur();
      }}
    />
  );
}
