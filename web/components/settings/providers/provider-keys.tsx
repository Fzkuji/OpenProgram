"use client";

import { ChevronDown, ChevronUp } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useTranslation } from "@/lib/i18n";

import styles from "../settings-page.module.css";

/** The provider's API key(s), as ONE list — the credential pool. Add a key
 *  (validated first) and it joins the list; reorder with ↑ / ↓ to set priority
 *  (the top key is the default — used until it's rate-limited, then the next
 *  takes over); remove any. With one key it behaves like a single key; with
 *  several they rotate automatically on rate limits. Backed by
 *  /api/providers/{id}/accounts/default/{keys,strategy,retry,keys/reorder}.
 *
 *  This replaces the old "single API Key field + separate rotation pool" split:
 *  everything is the list. An existing key configured the old way (the env-var /
 *  config key) is migrated into the list on first load so nothing is lost. */

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
const NON_ASCII = /[^\x20-\x7e]/;

export function ProviderKeys({
  providerId,
  envVar,
  profile = "default",
  onChanged,
}: {
  providerId: string;
  /** The provider's API-key env var — used once to migrate a pre-existing key
   *  (set via the old single-key field / shell env) into the list. */
  envVar?: string;
  profile?: string;
  onChanged?: () => void;
}) {
  const { text } = useTranslation();
  const base = `/api/providers/${encodeURIComponent(providerId)}/accounts/${encodeURIComponent(profile)}`;

  const [state, setState] = useState<KeysState | null>(null);
  const [newKey, setNewKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  const fetchKeys = useCallback(async (): Promise<KeysState> => {
    const r = await fetch(`${base}/keys`);
    return (await r.json()) as KeysState;
  }, [base]);

  const load = useCallback(async () => {
    try {
      let d = await fetchKeys();
      // First load with an empty pool: pull in a key the user set the old way
      // (env var / config) so it shows in the list instead of being invisible.
      if ((d.keys?.length ?? 0) === 0 && envVar) {
        try {
          const cfg = await fetch(`/api/config/key/${encodeURIComponent(envVar)}?reveal=1`).then((r) => r.json());
          if (cfg.has_value && cfg.value && !NON_ASCII.test(cfg.value)) {
            await fetch(`${base}/keys`, { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ api_key: cfg.value }) });
            d = await fetchKeys();
          }
        } catch { /* ignore migration failure — list just starts empty */ }
      }
      setState(d);
    } catch {
      /* ignore */
    }
  }, [base, envVar, fetchKeys]);

  useEffect(() => {
    load();
  }, [load]);

  async function addKey() {
    const key = newKey.trim();
    if (!key) return;
    setBusy(true);
    setMsg(text("Validating…", "验证中…"));
    try {
      const r = await fetch(`${base}/keys`, {
        method: "POST",
        headers: JSON_HEADERS,
        body: JSON.stringify({ api_key: key, validate: true }),
      });
      const d = await r.json();
      if (d.ok) {
        setNewKey("");
        setMsg("");
        await load();
        onChanged?.();
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
    await load();
    onChanged?.();
  }

  async function move(index: number, dir: -1 | 1) {
    if (!state) return;
    const ids = state.keys.map((k) => k.credential_id);
    const j = index + dir;
    if (j < 0 || j >= ids.length) return;
    [ids[index], ids[j]] = [ids[j], ids[index]];
    await fetch(`${base}/keys/reorder`, { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ order: ids }) });
    await load();
  }

  async function setStrategy(strategy: string) {
    await fetch(`${base}/strategy`, { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ strategy }) });
    await load();
  }

  if (!state) return null;
  const keys = state.keys || [];
  const multi = keys.length > 1;

  return (
    <div className={styles.detailSection}>
      <div className={styles.detailSectionTitle}>
        <span>{text("API key", "API 密钥")}</span>
        <span className={styles.modelCountSummary}>
          {keys.length === 0
            ? text("Not set", "未设置")
            : multi
              ? text(`${keys.length} keys · rotating`, `${keys.length} 个 · 轮询中`)
              : text("Configured", "已配置")}
        </span>
      </div>

      {keys.map((k, i) => (
        <div key={k.credential_id} className={styles.detailRow}>
          <span style={{ display: "flex", flexDirection: "column" }}>
            <button
              className={styles.iconBtn}
              style={{ height: 16, opacity: i === 0 ? 0.25 : 0.8 }}
              disabled={i === 0}
              title={text("Move up", "上移")}
              onClick={() => move(i, -1)}
            >
              <ChevronUp size={13} />
            </button>
            <button
              className={styles.iconBtn}
              style={{ height: 16, opacity: i === keys.length - 1 ? 0.25 : 0.8 }}
              disabled={i === keys.length - 1}
              title={text("Move down", "下移")}
              onClick={() => move(i, 1)}
            >
              <ChevronDown size={13} />
            </button>
          </span>
          <span style={{ flex: 1, fontFamily: "monospace", fontSize: "0.85rem" }}>
            {k.masked || k.credential_id}
            {i === 0 && multi && (
              <span style={{ marginLeft: 8, fontSize: "0.7rem", opacity: 0.5 }}>{text("default", "默认")}</span>
            )}
          </span>
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

      <div className={styles.detailRow}>
        <Input
          className="flex-1 font-mono"
          type="password"
          placeholder={keys.length === 0
            ? text("paste your API key", "粘贴你的 API key")
            : text("add another key for rotation", "再加一个用于轮询的 key")}
          value={newKey}
          onChange={(e) => setNewKey(e.target.value)}
          disabled={busy}
        />
        <Button size="sm" onClick={addKey} disabled={busy || !newKey.trim()}>
          {busy ? text("Adding…", "添加中…") : text("Add key", "添加")}
        </Button>
      </div>

      {multi && (
        <div className={styles.detailRow}>
          <span style={{ fontSize: "0.78rem", opacity: 0.7 }}>{text("When several:", "多个时：")}</span>
          <select
            value={state.strategy}
            onChange={(e) => setStrategy(e.target.value)}
            style={{
              background: "var(--input-bg, #1a1a1a)", color: "var(--text, #ddd)",
              border: "1px solid var(--border, #333)", borderRadius: 6, padding: "3px 8px", fontSize: "0.78rem",
            }}
          >
            <option value="fill_first">{text("use in order (failover)", "按顺序用（容错）")}</option>
            <option value="round_robin">{text("spread evenly", "均匀轮询")}</option>
            <option value="random">{text("random", "随机")}</option>
            <option value="least_used">{text("least used", "最少使用")}</option>
          </select>
        </div>
      )}

      <div style={{ fontSize: "0.72rem", opacity: 0.55, marginTop: "0.2rem", lineHeight: 1.5 }}>
        {keys.length <= 1
          ? text(
              "Add more keys to rotate across them — a rate-limited key cools down and the next takes over automatically.",
              "添加多个 key 即可自动轮询 —— 某个 key 被限流会自动冷却并切到下一个。",
            )
          : text(
              "The top key is the default; if it's rate-limited the next takes over, then recovers. Reorder with ↑ / ↓.",
              "最上面的是默认；它被限流时自动切到下一个，恢复后再切回。用 ↑ / ↓ 调整顺序。",
            )}
      </div>

      {msg && <div style={{ fontSize: "0.75rem", opacity: 0.75, marginTop: "0.2rem" }}>{msg}</div>}
    </div>
  );
}
