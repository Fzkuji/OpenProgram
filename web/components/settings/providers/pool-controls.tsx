"use client";

import { ChevronDown, ChevronRight } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useTranslation } from "@/lib/i18n";

import styles from "../settings-page.module.css";

/** Extra API keys for ONE account (profile) that rotate automatically: a
 *  rate-limited key cools down and the next takes over. Rendered *nested* inside
 *  the API-key section (not as a competing top-level section), collapsed by
 *  default until there's something to show. Hits
 *  /api/providers/{id}/accounts/{name}/{keys,strategy,retry}.
 *
 *  These keys live in the credential pool and, when present, are what requests
 *  rotate across. The single key field above is the one-key case; add keys here
 *  for rate-limit headroom. */

interface Key {
  credential_id: string;
  status: string;
  masked: string;
  cooling: boolean;
  use_count: number;
  last_error?: string | null;
}
interface KeysState {
  keys: Key[];
  strategy: string;
  strategies: string[];
}

const JSON_HEADERS = { "Content-Type": "application/json" };

export function PoolControls({
  providerId,
  profile = "default",
}: {
  providerId: string;
  profile?: string;
}) {
  const { text } = useTranslation();
  const base = `/api/providers/${encodeURIComponent(providerId)}/accounts/${encodeURIComponent(profile)}`;

  const [state, setState] = useState<KeysState | null>(null);
  const [open, setOpen] = useState(false);
  const [newKey, setNewKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  const load = useCallback(async () => {
    try {
      const r = await fetch(`${base}/keys`);
      const d = (await r.json()) as KeysState;
      setState(d);
      // Auto-open when there are already extra keys to show.
      if ((d.keys?.length ?? 0) > 0) setOpen(true);
    } catch {
      /* ignore */
    }
  }, [base]);

  useEffect(() => {
    load();
  }, [load]);

  async function setStrategy(strategy: string) {
    await fetch(`${base}/strategy`, { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ strategy }) });
    load();
  }

  async function retry() {
    const r = await fetch(`${base}/retry`, { method: "POST" });
    const d = await r.json();
    setMsg(d.ok ? text(`Cleared ${d.cleared} cooldown(s).`, `已清除 ${d.cleared} 个冷却。`) : (d.error || ""));
    load();
  }

  async function addKey() {
    const key = newKey.trim();
    if (!key) return;
    setBusy(true);
    setMsg("");
    try {
      const r = await fetch(`${base}/keys`, { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ api_key: key }) });
      const d = await r.json();
      if (d.ok) {
        setNewKey("");
        load();
      } else {
        setMsg(d.error || text("Could not add the key.", "添加失败。"));
      }
    } catch {
      setMsg(text("Could not add the key.", "添加失败。"));
    }
    setBusy(false);
  }

  async function removeKey(id: string) {
    await fetch(`${base}/keys/${encodeURIComponent(id)}`, { method: "DELETE" });
    load();
  }

  if (!state) return null;
  const keys = state.keys || [];
  const count = keys.length;

  return (
    <div style={{ marginTop: 4, borderTop: "1px solid var(--border, #2a2a2a)", paddingTop: 10 }}>
      <button
        onClick={() => setOpen((v) => !v)}
        style={{
          display: "flex", alignItems: "center", gap: 6, background: "none", border: "none",
          padding: 0, cursor: "pointer", color: "var(--text-muted, #999)", fontSize: "0.82rem",
        }}
      >
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <span>{text("Extra keys for rotation", "用于轮询的备用密钥")}</span>
        {count > 0 && (
          <span style={{ fontSize: "0.72rem", opacity: 0.7 }}>{text(`· ${count}`, `· ${count}`)}</span>
        )}
      </button>

      {open && (
        <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 8 }}>
          <div style={{ fontSize: "0.75rem", opacity: 0.6, lineHeight: 1.5 }}>
            {text(
              "Add more API keys to rotate across them — a rate-limited key cools down and the next takes over automatically. With one key this does nothing.",
              "添加多个 API key 即可在它们之间自动轮询 —— 某个 key 被限流会自动冷却并切到下一个；只有一个 key 时不起作用。",
            )}
          </div>

          {count > 1 && (
            <div className={styles.detailRow} style={{ gap: 10 }}>
              <span style={{ fontSize: "0.78rem", opacity: 0.7 }}>{text("Strategy", "策略")}</span>
              <select
                value={state.strategy}
                onChange={(e) => setStrategy(e.target.value)}
                style={{
                  background: "var(--input-bg, #1a1a1a)", color: "var(--text, #ddd)",
                  border: "1px solid var(--border, #333)", borderRadius: 6, padding: "3px 8px", fontSize: "0.78rem",
                }}
              >
                {(state.strategies || []).map((s) => (
                  <option key={s} value={s}>{s}</option>
                ))}
              </select>
              <Button size="sm" onClick={retry}>{text("Retry now", "立即重试")}</Button>
            </div>
          )}

          {keys.map((k) => (
            <div key={k.credential_id} className={styles.detailRow} style={{ gap: 10 }}>
              <span style={{ flex: 1, fontFamily: "monospace", fontSize: "0.8rem" }}>{k.masked || k.credential_id}</span>
              <span
                title={k.last_error || k.status}
                style={{
                  fontSize: "0.7rem", padding: "1px 7px", borderRadius: 8, whiteSpace: "nowrap",
                  background: k.cooling ? "rgba(220,140,40,0.18)" : k.status === "valid" ? "rgba(60,180,90,0.16)" : "rgba(220,70,70,0.16)",
                  color: k.cooling ? "#e0a040" : k.status === "valid" ? "#56c06a" : "#e06a6a",
                }}
              >
                {k.cooling ? text("cooling", "冷却中") : k.status}
              </span>
              <Button size="sm" onClick={() => removeKey(k.credential_id)}>{text("Remove", "删除")}</Button>
            </div>
          ))}

          <div className={styles.detailRow} style={{ gap: 10 }}>
            <Input
              className="flex-1 font-mono"
              type="password"
              placeholder={text("add another API key for rotation", "再加一个用于轮询的 API key")}
              value={newKey}
              onChange={(e) => setNewKey(e.target.value)}
              disabled={busy}
            />
            <Button size="sm" onClick={addKey} disabled={busy || !newKey.trim()}>
              {busy ? text("Adding…", "添加中…") : text("Add key", "添加密钥")}
            </Button>
          </div>

          {msg && <div style={{ fontSize: "0.75rem", opacity: 0.75 }}>{msg}</div>}
        </div>
      )}
    </div>
  );
}
