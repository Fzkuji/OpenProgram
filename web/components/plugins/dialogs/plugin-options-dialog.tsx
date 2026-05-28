"use client";

import { useEffect, useMemo, useState } from "react";
import styles from "../plugins.module.css";
import { usePluginsStore } from "@/lib/plugins-store";
import { useTranslation } from "@/lib/i18n";

interface Props {
  name: string;
  onClose: () => void;
}

// claude-code style PluginOptionSchema — each field declares type +
// metadata; sensitive fields are masked + never pre-populated.
interface FieldSchema {
  type?: "string" | "number" | "boolean";
  title?: string;
  description?: string;
  default?: unknown;
  required?: boolean;
  sensitive?: boolean;
  enum?: string[];
}

type Schema = Record<string, FieldSchema>;

interface PluginDetail {
  manifest?: { options?: Schema | { fields?: Schema; properties?: Schema } | null };
}

function normaliseSchema(raw: unknown): Schema {
  if (!raw || typeof raw !== "object") return {};
  // Accept either {field: schema, ...} directly, or {fields: {...}} /
  // {properties: {...}} for JSON-Schema compat.
  const obj = raw as Record<string, unknown>;
  if (obj.fields && typeof obj.fields === "object") return obj.fields as Schema;
  if (obj.properties && typeof obj.properties === "object") return obj.properties as Schema;
  // Otherwise the top-level keys are the fields themselves — but only
  // if each value looks like a field schema (has a known type).
  const out: Schema = {};
  for (const [k, v] of Object.entries(obj)) {
    if (v && typeof v === "object" && !Array.isArray(v)) {
      const f = v as Record<string, unknown>;
      if (
        typeof f.type === "string" &&
        ["string", "number", "boolean"].includes(f.type)
      ) {
        out[k] = f as FieldSchema;
      }
    }
  }
  return out;
}

export function PluginOptionsDialog({ name, onClose }: Props) {
  const { t, text } = useTranslation();
  const { getOptions, setOptions } = usePluginsStore();
  const [schema, setSchema] = useState<Schema>({});
  const [values, setValues] = useState<Record<string, string | number | boolean>>({});
  const [initial, setInitial] = useState<Record<string, unknown>>({});
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [rawMode, setRawMode] = useState(false);
  const [rawText, setRawText] = useState("{}");

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        // Load both options (current values) and the plugin manifest
        // (schema for the form).
        const [opts, det] = await Promise.all([
          getOptions(name),
          fetch(`/api/plugins/${encodeURIComponent(name)}`).then((r) =>
            r.ok ? r.json() as Promise<PluginDetail> : null,
          ),
        ]);
        if (cancelled) return;
        const sch = normaliseSchema(det?.manifest?.options);
        setSchema(sch);
        setInitial(opts as Record<string, unknown>);
        setRawText(JSON.stringify(opts, null, 2));
        // Seed values from existing options + defaults, masking sensitive.
        const seeded: Record<string, string | number | boolean> = {};
        for (const [k, f] of Object.entries(sch)) {
          const existing = (opts as Record<string, unknown>)[k];
          if (f.sensitive) {
            seeded[k] = "";
            continue;
          }
          if (existing !== undefined) {
            seeded[k] = existing as string | number | boolean;
          } else if (f.default !== undefined) {
            seeded[k] = f.default as string | number | boolean;
          } else if (f.type === "boolean") {
            seeded[k] = false;
          } else {
            seeded[k] = "";
          }
        }
        setValues(seeded);
      } catch (e) {
        if (!cancelled) setErr(String(e));
      }
    })();
    return () => { cancelled = true; };
  }, [name, getOptions]);

  const hasSchema = useMemo(() => Object.keys(schema).length > 0, [schema]);

  const update = (k: string, v: string | number | boolean) => {
    setValues((s) => ({ ...s, [k]: v }));
  };

  const save = async () => {
    setErr("");
    let payload: Record<string, unknown>;
    if (rawMode || !hasSchema) {
      try {
        payload = JSON.parse(rawText);
      } catch (e) {
        setErr(text(`JSON parse failed: ${(e as Error).message}`, `JSON 解析失败：${(e as Error).message}`));
        return;
      }
    } else {
      payload = {};
      for (const [k, f] of Object.entries(schema)) {
        const v = values[k];
        // Sensitive fields: empty input means "leave existing
        // untouched". Omit so the backend doesn't wipe a stored secret.
        if (f.sensitive && (v === "" || v === undefined)) {
          if (initial[k] !== undefined) continue;
        }
        if (f.type === "number") {
          if (v === "" || v === null || v === undefined) {
            if (f.required) {
              setErr(text(`Field ${k} is required`, `字段 ${k} 为必填项`));
              return;
            }
            continue;
          }
          const n = Number(v);
          if (Number.isNaN(n)) {
            setErr(text(`Field ${k} is not a valid number`, `字段 ${k} 不是有效数字`));
            return;
          }
          payload[k] = n;
        } else if (f.type === "boolean") {
          payload[k] = Boolean(v);
        } else {
          if (f.required && (v === "" || v === undefined)) {
            setErr(text(`Field ${k} is required`, `字段 ${k} 为必填项`));
            return;
          }
          payload[k] = v ?? "";
        }
      }
    }
    setBusy(true);
    try {
      await setOptions(name, payload);
      onClose();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={styles.dialogBackdrop} onClick={onClose}>
      <div
        className={styles.dialog}
        onClick={(e) => e.stopPropagation()}
        style={{ width: "min(560px, 90vw)" }}
      >
        <div className="flex items-center justify-between gap-3">
          <div className={styles.dialogTitle}>{text(`${name} options`, `${name} 选项`)}</div>
          {hasSchema && (
            <button
              onClick={() => setRawMode((v) => !v)}
              className="text-xs text-[var(--text-secondary)] hover:text-nav-color-hover"
            >
              {rawMode ? text("Form", "表单") : "Raw JSON"}
            </button>
          )}
        </div>
        <div className={styles.dialogBody}>
          {(!hasSchema || rawMode) ? (
            <textarea
              className={styles.textarea}
              value={rawText}
              onChange={(e) => setRawText(e.target.value)}
              spellCheck={false}
            />
          ) : (
            <div className="space-y-3">
              {Object.entries(schema).map(([k, f]) => (
                <div key={k}>
                  <label className="block text-xs font-semibold text-[var(--text-bright)] mb-1">
                    {f.title || k}
                    {f.required && <span className="ml-1 text-[var(--accent-red,#ef4444)]">*</span>}
                    {f.sensitive && <span className="ml-2 text-[10px] uppercase tracking-wide text-amber-400">{text("sensitive", "敏感")}</span>}
                  </label>
                  {f.description && (
                    <div className="text-[11px] text-[var(--text-tertiary)] mb-1">{f.description}</div>
                  )}
                  {f.type === "boolean" ? (
                    <label className="flex items-center gap-2 text-sm">
                      <input
                        type="checkbox"
                        checked={Boolean(values[k])}
                        onChange={(e) => update(k, e.target.checked)}
                      />
                      <span className="text-[var(--text-secondary)]">{text("enabled", "已启用")}</span>
                    </label>
                  ) : f.enum && f.enum.length > 0 ? (
                    <select
                      value={String(values[k] ?? "")}
                      onChange={(e) => update(k, e.target.value)}
                      className="w-full rounded-md border border-[var(--border)] bg-[var(--bg-input)] px-2 py-1 text-sm"
                    >
                      {f.enum.map((opt) => (
                        <option key={opt} value={opt}>{opt}</option>
                      ))}
                    </select>
                  ) : (
                    <input
                      type={f.sensitive ? "password" : f.type === "number" ? "number" : "text"}
                      value={String(values[k] ?? "")}
                      onChange={(e) => update(k, e.target.value)}
                      placeholder={f.sensitive ? text("(unchanged)", "（不变）") : f.default !== undefined ? String(f.default) : ""}
                      className="w-full rounded-md border border-[var(--border)] bg-[var(--bg-input)] px-2 py-1 text-sm"
                    />
                  )}
                </div>
              ))}
            </div>
          )}
          {err && <div style={{ color: "#ef4444", fontSize: 12, marginTop: 10 }}>{err}</div>}
        </div>
        <div className={styles.dialogActions}>
          <button className={styles.btn} onClick={onClose} disabled={busy}>{t("sidebar.cancel")}</button>
          <button className={styles.btnPrimary} onClick={save} disabled={busy}>
            {busy ? text("Saving...", "保存中...") : text("Save", "保存")}
          </button>
        </div>
      </div>
    </div>
  );
}
