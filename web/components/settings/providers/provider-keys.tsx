"use client";

import { ChevronDown, ChevronUp, Pencil } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { useTranslation } from "@/lib/i18n";

import styles from "../settings-page.module.css";

/** A provider's API key(s) as ONE list. Each key can be NAMED; you pick which
 *  one is active (used), or flip the rotation toggle to cycle across them
 *  automatically on rate limits. This mirrors how OAuth providers switch between
 *  accounts — multiple credentials, name them, switch, with automation optional.
 *
 *  Default is rotation OFF: the single active key is used (the simple case). Turn
 *  rotation ON and a rate-limited key cools down while the next takes over;
 *  ↑/↓ then orders priority and a strategy picker appears. Backed by
 *  /api/providers/{id}/accounts/default/{keys,keys/*,rotation,strategy}.
 *
 *  A key set the old way (env var / config) is migrated into the list on first
 *  load so nothing is lost. */

interface Key {
  credential_id: string;
  name: string;
  status: string;
  masked: string;
  is_active: boolean;
  cooling: boolean;
  use_count: number;
  last_error?: string | null;
}
interface KeysState {
  keys: Key[];
  rotation: boolean;
  active: string;
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
  envVar?: string;
  profile?: string;
  onChanged?: () => void;
}) {
  const { text } = useTranslation();
  const base = `/api/providers/${encodeURIComponent(providerId)}/accounts/${encodeURIComponent(profile)}`;

  const [state, setState] = useState<KeysState | null>(null);
  const [newKey, setNewKey] = useState("");
  const [newName, setNewName] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [editValue, setEditValue] = useState("");

  const fetchKeys = useCallback(async (): Promise<KeysState> => {
    const r = await fetch(`${base}/keys`);
    return (await r.json()) as KeysState;
  }, [base]);

  const load = useCallback(async () => {
    try {
      let d = await fetchKeys();
      if ((d.keys?.length ?? 0) === 0 && envVar) {
        // Migrate a key set the old way (env var / config) into the list.
        try {
          const cfg = await fetch(`/api/config/key/${encodeURIComponent(envVar)}?reveal=1`).then((r) => r.json());
          if (cfg.has_value && cfg.value && !NON_ASCII.test(cfg.value)) {
            await fetch(`${base}/keys`, { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ api_key: cfg.value }) });
            d = await fetchKeys();
          }
        } catch { /* ignore */ }
      }
      setState(d);
    } catch { /* ignore */ }
  }, [base, envVar, fetchKeys]);

  useEffect(() => { load(); }, [load]);

  async function addKey() {
    const key = newKey.trim();
    if (!key) return;
    setBusy(true);
    setMsg(text("Validating…", "验证中…"));
    try {
      const r = await fetch(`${base}/keys`, {
        method: "POST", headers: JSON_HEADERS,
        body: JSON.stringify({ api_key: key, name: newName.trim(), validate: true }),
      });
      const d = await r.json();
      if (d.ok) { setNewKey(""); setNewName(""); setMsg(""); await load(); onChanged?.(); }
      else setMsg(d.error || text("Could not add the key.", "添加失败。"));
    } catch { setMsg(text("Could not add the key.", "添加失败。")); }
    setBusy(false);
  }

  async function removeKey(id: string) {
    await fetch(`${base}/keys/${encodeURIComponent(id)}`, { method: "DELETE" });
    await load(); onChanged?.();
  }
  async function useKey(id: string) {
    await fetch(`${base}/keys/${encodeURIComponent(id)}/use`, { method: "POST" });
    await load();
  }
  async function saveName(id: string) {
    await fetch(`${base}/keys/${encodeURIComponent(id)}/name`, { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ name: editValue.trim() }) });
    setEditing(null); setEditValue(""); await load();
  }
  async function toggleRotation(enabled: boolean) {
    await fetch(`${base}/rotation`, { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ enabled, strategy: state?.strategy }) });
    await load();
  }
  async function setStrategy(strategy: string) {
    await fetch(`${base}/strategy`, { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ strategy }) });
    await load();
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

  if (!state) return null;
  const keys = state.keys || [];
  const multi = keys.length > 1;
  const rotating = state.rotation;

  return (
    <div className={styles.detailSection}>
      <div className={styles.detailSectionTitle}>
        <span>{text("API key", "API 密钥")}</span>
        <span className={styles.modelCountSummary}>
          {keys.length === 0
            ? text("Not set", "未设置")
            : multi
              ? (rotating ? text(`${keys.length} keys · rotating`, `${keys.length} 个 · 轮询中`) : text(`${keys.length} keys`, `${keys.length} 个`))
              : text("Configured", "已配置")}
        </span>
      </div>

      {/* Rotation toggle — only meaningful with more than one key. */}
      {multi && (
        <div className={styles.detailRow} style={{ alignItems: "center" }}>
          <Switch checked={rotating} onCheckedChange={toggleRotation} />
          <span style={{ fontSize: "0.82rem", flex: 1 }}>{text("Rotate across keys automatically", "在多个 key 之间自动轮询")}</span>
          {rotating && (
            <select
              value={state.strategy}
              onChange={(e) => setStrategy(e.target.value)}
              style={{ background: "var(--input-bg, #1a1a1a)", color: "var(--text, #ddd)", border: "1px solid var(--border, #333)", borderRadius: 6, padding: "3px 8px", fontSize: "0.78rem" }}
            >
              <option value="fill_first">{text("in order (failover)", "按顺序（容错）")}</option>
              <option value="round_robin">{text("spread evenly", "均匀轮询")}</option>
              <option value="random">{text("random", "随机")}</option>
              <option value="least_used">{text("least used", "最少使用")}</option>
            </select>
          )}
        </div>
      )}

      {keys.map((k, i) => (
        <div key={k.credential_id} className={styles.detailRow}>
          {rotating && multi && (
            <span style={{ display: "flex", flexDirection: "column" }}>
              <button className={styles.iconBtn} style={{ height: 15, opacity: i === 0 ? 0.25 : 0.8 }} disabled={i === 0} title={text("Move up", "上移")} onClick={() => move(i, -1)}><ChevronUp size={12} /></button>
              <button className={styles.iconBtn} style={{ height: 15, opacity: i === keys.length - 1 ? 0.25 : 0.8 }} disabled={i === keys.length - 1} title={text("Move down", "下移")} onClick={() => move(i, 1)}><ChevronDown size={12} /></button>
            </span>
          )}
          {/* name (click ✎ to edit) */}
          {editing === k.credential_id ? (
            <Input
              className="font-mono"
              style={{ width: "9rem" }}
              autoFocus
              value={editValue}
              placeholder={text("name", "名称")}
              onChange={(e) => setEditValue(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") saveName(k.credential_id); if (e.key === "Escape") setEditing(null); }}
              onBlur={() => saveName(k.credential_id)}
            />
          ) : (
            <button
              className={styles.iconBtn}
              style={{ width: "9rem", justifyContent: "flex-start", gap: 5, opacity: 0.85, fontSize: "0.8rem" }}
              title={text("Rename this key", "重命名")}
              onClick={() => { setEditing(k.credential_id); setEditValue(k.name); }}
            >
              <Pencil size={11} style={{ opacity: 0.5, flexShrink: 0 }} />
              <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {k.name || text("unnamed", "未命名")}
              </span>
            </button>
          )}
          <span style={{ flex: 1, fontFamily: "monospace", fontSize: "0.8rem", opacity: 0.85 }}>{k.masked || k.credential_id}</span>
          <span
            title={k.last_error || k.status}
            style={{ fontSize: "0.7rem", padding: "1px 7px", borderRadius: 8, whiteSpace: "nowrap",
              background: k.cooling ? "rgba(220,140,40,0.18)" : k.status === "valid" ? "rgba(60,180,90,0.16)" : "rgba(220,70,70,0.16)",
              color: k.cooling ? "#e0a040" : k.status === "valid" ? "#56c06a" : "#e06a6a" }}
          >
            {k.cooling ? text("cooling", "冷却中") : k.status}
          </span>
          {k.is_active ? (
            <span style={{ fontSize: "0.72rem", color: "var(--text-primary)", padding: "0 4px" }}>
              {rotating ? text("default", "默认") : text("in use", "使用中")}
            </span>
          ) : (
            <Button size="sm" onClick={() => useKey(k.credential_id)}>{text("Use", "使用")}</Button>
          )}
          <Button size="sm" onClick={() => removeKey(k.credential_id)}>{text("Remove", "删除")}</Button>
        </div>
      ))}

      <div className={styles.detailRow}>
        {keys.length > 0 && (
          <Input className="font-mono" style={{ width: "9rem" }} placeholder={text("name (optional)", "名称（可选）")} value={newName} onChange={(e) => setNewName(e.target.value)} disabled={busy} />
        )}
        <Input
          className="flex-1 font-mono"
          type="password"
          placeholder={keys.length === 0 ? text("paste your API key", "粘贴你的 API key") : text("add another key", "再加一个 key")}
          value={newKey}
          onChange={(e) => setNewKey(e.target.value)}
          disabled={busy}
        />
        <Button size="sm" onClick={addKey} disabled={busy || !newKey.trim()}>
          {busy ? text("Adding…", "添加中…") : text("Add key", "添加")}
        </Button>
      </div>

      <div style={{ fontSize: "0.72rem", opacity: 0.55, marginTop: "0.2rem", lineHeight: 1.5 }}>
        {!multi
          ? text(
              "Add more keys to switch between them or rotate automatically on rate limits.",
              "添加多个 key 后可以手动切换,或开启自动轮询(限流时切换)。",
            )
          : rotating
            ? text(
                "Keys rotate automatically — a rate-limited one cools down and the next takes over. ↑/↓ sets priority.",
                "多个 key 自动轮询 —— 某个被限流会冷却并切到下一个。↑/↓ 调整优先级。",
              )
            : text(
                "Only the key in use is called. Switch with Use, or turn on rotation to cycle automatically.",
                "只调用“使用中”的那个 key。点“使用”切换,或打开轮询自动切换。",
              )}
      </div>

      {msg && <div style={{ fontSize: "0.75rem", opacity: 0.75, marginTop: "0.2rem" }}>{msg}</div>}
    </div>
  );
}
