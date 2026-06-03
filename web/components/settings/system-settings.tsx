"use client";

/**
 * System settings — schema-driven editor, styled to match the other
 * settings tabs (same .section/.row/.label/.value from settings-page.module
 * .css). Renders the SAME settings the TUI panel and `openprogram config`
 * edit, fetched from /api/settings (backed by openprogram.config_schema).
 * One SettingSpec server-side → one row here. See docs/design/cli-redesign.md.
 */
import { useEffect, useState, type CSSProperties } from "react";

import styles from "./settings-page.module.css";

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

const inputStyle: CSSProperties = {
  padding: "6px 10px",
  background: "var(--bg-secondary)",
  border: "1px solid var(--border)",
  borderRadius: "var(--ui-button-radius)",
  color: "var(--text-primary)",
  font: "inherit",
  width: 120,
  textAlign: "right",
};

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
    return <div style={{ padding: 24, color: "var(--text-muted)" }}>Loading…</div>;
  }

  return (
    <div style={{ padding: "8px 16px", maxWidth: 760 }}>
      {groups.map((g) => (
        <div className={styles.section} key={g}>
          <h3 className={styles.sectionTitle}>{g}</h3>
          {rows
            .filter((r) => r.group === g)
            .map((r) => {
              const st = status[r.key];
              return (
                <div className={`${styles.row} ${styles.rowTop}`} key={r.key}>
                  <div className={styles.label}>
                    <div>{r.label}</div>
                    {r.help ? (
                      <div style={{ fontSize: 13, color: "var(--text-muted)", marginTop: 2 }}>
                        {r.help}
                      </div>
                    ) : null}
                    {st ? (
                      <div
                        style={{
                          fontSize: 12,
                          marginTop: 3,
                          color: st.startsWith("✗") ? "#ef4444" : "#10b981",
                        }}
                      >
                        {st}
                      </div>
                    ) : r.apply === "next_start" ? (
                      <div style={{ fontSize: 12, marginTop: 3, color: "var(--text-muted)" }}>
                        takes effect next start
                      </div>
                    ) : null}
                  </div>
                  <div className={styles.value}>
                    <Control row={r} onSave={save} />
                  </div>
                </div>
              );
            })}
        </div>
      ))}
    </div>
  );
}

function Control({ row, onSave }: { row: Row; onSave: (k: string, v: unknown) => void }) {
  if (row.widget === "status") {
    const ok = !!row.value;
    return (
      <span style={{ fontSize: 13, color: ok ? "#10b981" : "var(--text-muted)" }}>
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
        style={{ width: 16, height: 16, accentColor: "var(--accent-primary, #d97757)" }}
      />
    );
  }
  if (row.widget === "enum") {
    return (
      <select
        value={String(row.value)}
        onChange={(e) => onSave(row.key, e.target.value)}
        style={{ ...inputStyle, width: "auto", textAlign: "left" }}
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
      style={inputStyle}
      onBlur={(e) => {
        if (e.target.value !== String(row.value)) onSave(row.key, e.target.value);
      }}
      onKeyDown={(e) => {
        if (e.key === "Enter") (e.target as HTMLInputElement).blur();
      }}
    />
  );
}
