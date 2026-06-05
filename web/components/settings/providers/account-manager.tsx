"use client";

import { Pencil } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { useTranslation } from "@/lib/i18n";

import { ProviderLogin } from "./provider-login";
import styles from "../settings-page.module.css";
import type { Provider } from "./types";

/** ONE management panel for every provider's credentials. An *account* is a
 *  named, switchable credential: a KEY for api-key providers (identity = masked
 *  key), a SIGN-IN for login providers (identity = email), a Claude subscription
 *  for claude-code. Uniform everywhere: rename, Use (switch the active one),
 *  remove, and — where the backend supports it — a rotation toggle. Only the ADD
 *  step branches (paste a key / sign in / paste a code), and the identity label.
 *
 *  Two data sources behind one UI: api-key providers use the pool's keys
 *  (…/accounts/default/keys*); login / claude-code use profiles (…/accounts*).
 *  See docs/design/unified-account-management.md (P-D). */

interface Item {
  id: string;          // credential_id (keys) or profile name (accounts)
  name: string;        // user label (editable)
  identity: string;    // masked key, or email
  isActive: boolean;
  status: string;
  cooling: boolean;
}

const JSON_HEADERS = { "Content-Type": "application/json" };
const NON_ASCII = /[^\x20-\x7e]/;

export function AccountManager({
  provider,
  onChanged,
}: {
  provider: Provider;
  onChanged?: () => void;
}) {
  const { text } = useTranslation();
  const pid = provider.id;
  // api-key providers manage KEYS; claude-code + login-only manage ACCOUNTS.
  const kind: "keys" | "accounts" =
    provider.api_key_env && pid !== "claude-code" ? "keys" : "accounts";
  const base = `/api/providers/${encodeURIComponent(pid)}`;

  const [items, setItems] = useState<Item[] | null>(null);
  const [rotation, setRotation] = useState(false);
  const [strategy, setStrategy] = useState("fill_first");
  const [strategies, setStrategies] = useState<string[]>([]);
  const [addMode, setAddMode] = useState<"api_key" | "login" | "code_paste">("api_key");

  const [newKey, setNewKey] = useState("");
  const [newName, setNewName] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [editValue, setEditValue] = useState("");
  // code_paste add (claude-code)
  const [pending, setPending] = useState<{ session: string; url?: string } | null>(null);
  const [code, setCode] = useState("");

  const load = useCallback(async () => {
    try {
      if (kind === "keys") {
        let d = await fetch(`${base}/accounts/default/keys`).then((r) => r.json());
        // migrate a key set the old way (env var / config) into the list
        if ((d.keys?.length ?? 0) === 0 && provider.api_key_env) {
          try {
            const cfg = await fetch(`/api/config/key/${encodeURIComponent(provider.api_key_env)}?reveal=1`).then((r) => r.json());
            if (cfg.has_value && cfg.value && !NON_ASCII.test(cfg.value)) {
              await fetch(`${base}/accounts/default/keys`, { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ api_key: cfg.value }) });
              d = await fetch(`${base}/accounts/default/keys`).then((r) => r.json());
            }
          } catch { /* ignore */ }
        }
        setItems((d.keys || []).map((k: any): Item => ({
          id: k.credential_id, name: k.name || "", identity: k.masked || "",
          isActive: k.is_active, status: k.status, cooling: k.cooling,
        })));
        setRotation(!!d.rotation);
        setStrategy(d.strategy || "fill_first");
        setStrategies(d.strategies || []);
        setAddMode("api_key");
      } else {
        const d = await fetch(`${base}/accounts`).then((r) => r.json());
        setItems((d.accounts || []).map((a: any): Item => ({
          id: a.name, name: a.name, identity: a.email && a.email !== a.name ? a.email : "",
          isActive: a.name === d.active, status: a.status || "", cooling: false,
        })));
        setRotation(false);
        setAddMode(d.add_mode === "code_paste" ? "code_paste" : "login");
      }
    } catch { /* ignore */ }
  }, [base, kind, provider.api_key_env]);

  useEffect(() => { load(); }, [load]);

  // ---- uniform ops (dispatch by kind) -----------------------------------
  async function use(it: Item) {
    if (kind === "keys") await fetch(`${base}/accounts/default/keys/${encodeURIComponent(it.id)}/use`, { method: "POST" });
    else await fetch(`${base}/accounts/use`, { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ name: it.id }) });
    await load();
  }
  async function remove(it: Item) {
    if (kind === "keys") await fetch(`${base}/accounts/default/keys/${encodeURIComponent(it.id)}`, { method: "DELETE" });
    else await fetch(`${base}/accounts/remove`, { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ name: it.id }) });
    await load(); onChanged?.();
  }
  async function saveName(it: Item) {
    const nv = editValue.trim();
    setEditing(null); setEditValue("");
    if (kind === "keys") {
      await fetch(`${base}/accounts/default/keys/${encodeURIComponent(it.id)}/name`, { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ name: nv }) });
    } else if (nv && nv !== it.id) {
      const d = await fetch(`${base}/accounts/rename`, { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ old: it.id, new: nv }) }).then((r) => r.json());
      if (!d.ok) setMsg(d.error || text("Rename failed.", "改名失败。"));
    }
    await load();
  }
  async function toggleRotation(enabled: boolean) {
    await fetch(`${base}/accounts/default/rotation`, { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ enabled, strategy }) });
    await load();
  }
  async function changeStrategy(s: string) {
    await fetch(`${base}/accounts/default/strategy`, { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ strategy: s }) });
    await load();
  }

  // ---- add (branches) ----------------------------------------------------
  async function addKey() {
    const key = newKey.trim();
    if (!key) return;
    setBusy(true); setMsg(text("Validating…", "验证中…"));
    try {
      const d = await fetch(`${base}/accounts/default/keys`, { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ api_key: key, name: newName.trim(), validate: true }) }).then((r) => r.json());
      if (d.ok) { setNewKey(""); setNewName(""); setMsg(""); await load(); onChanged?.(); }
      else setMsg(d.error || text("Could not add the key.", "添加失败。"));
    } catch { setMsg(text("Could not add the key.", "添加失败。")); }
    setBusy(false);
  }
  async function startCodeAdd() {
    setBusy(true); setMsg("");
    try {
      const d = await fetch(`${base}/accounts/add`, { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ name: newName.trim() }) }).then((r) => r.json());
      if (d.error) setMsg(d.error);
      else {
        if (d.url) window.open(d.url, "_blank", "noopener");
        setPending({ session: d.session, url: d.url });
        setMsg(text("A login page opened — sign in, then paste the code it shows below.", "已打开登录页 — 登录后把页面给出的 code 粘到下面。"));
      }
    } catch { setMsg(text("Could not start the login.", "启动登录失败。")); }
    setBusy(false);
  }
  async function submitCode() {
    if (!pending) return;
    setBusy(true);
    try {
      const d = await fetch(`${base}/accounts/add/code`, { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ session: pending.session, code }) }).then((r) => r.json());
      if (d.ok) { setMsg(text("Account added.", "账号已添加。")); setPending(null); setCode(""); setNewName(""); await load(); }
      else { setMsg(d.error || text("That code didn't work — try again.", "code 无效，请重试。")); if (typeof d.error === "string" && d.error.includes("no pending")) { setPending(null); setCode(""); } }
    } catch { setMsg(text("Could not finish the login.", "完成登录失败。")); }
    setBusy(false);
  }

  if (!items) return null;
  const multi = items.length > 1;

  const title = kind === "keys"
    ? text("API key", "API 密钥")
    : text(`${provider.label} accounts`, `${provider.label} 账号`);
  const summary = items.length === 0
    ? text("Not set", "未设置")
    : kind === "keys"
      ? (multi ? (rotation ? text(`${items.length} keys · rotating`, `${items.length} 个 · 轮询中`) : text(`${items.length} keys`, `${items.length} 个`)) : text("Configured", "已配置"))
      : (items.find((i) => i.isActive) ? text(`active: ${items.find((i) => i.isActive)!.id}`, `激活：${items.find((i) => i.isActive)!.id}`) : text("none active", "未激活"));

  return (
    <div className={styles.detailSection}>
      <div className={styles.detailSectionTitle}>
        <span>{title}</span>
        <span className={styles.modelCountSummary}>{summary}</span>
      </div>

      {/* rotation toggle — only where supported (api-key pool) and with >1 key */}
      {kind === "keys" && multi && (
        <div className={styles.detailRow} style={{ alignItems: "center" }}>
          <Switch checked={rotation} onCheckedChange={toggleRotation} />
          <span style={{ fontSize: "0.82rem", flex: 1 }}>{text("Rotate across keys automatically", "在多个 key 之间自动轮询")}</span>
          {rotation && (
            <select value={strategy} onChange={(e) => changeStrategy(e.target.value)}
              style={{ background: "var(--input-bg, #1a1a1a)", color: "var(--text, #ddd)", border: "1px solid var(--border, #333)", borderRadius: 6, padding: "3px 8px", fontSize: "0.78rem" }}>
              <option value="fill_first">{text("in order (failover)", "按顺序（容错）")}</option>
              <option value="round_robin">{text("spread evenly", "均匀轮询")}</option>
              <option value="random">{text("random", "随机")}</option>
              <option value="least_used">{text("least used", "最少使用")}</option>
            </select>
          )}
        </div>
      )}

      {items.map((it) => (
        <div key={it.id} className={styles.detailRow}>
          {editing === it.id ? (
            <Input className="font-mono" style={{ width: "9rem" }} autoFocus value={editValue}
              placeholder={text("name", "名称")} onChange={(e) => setEditValue(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") saveName(it); if (e.key === "Escape") setEditing(null); }}
              onBlur={() => saveName(it)} />
          ) : (
            <button className={styles.iconBtn} style={{ width: "9rem", justifyContent: "flex-start", gap: 5, opacity: 0.85, fontSize: "0.8rem" }}
              title={text("Rename", "重命名")} onClick={() => { setEditing(it.id); setEditValue(it.name || (kind === "accounts" ? it.id : "")); }}>
              <Pencil size={11} style={{ opacity: 0.5, flexShrink: 0 }} />
              <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {it.name || (kind === "accounts" ? it.id : text("unnamed", "未命名"))}
              </span>
            </button>
          )}
          <span style={{ flex: 1, fontFamily: "monospace", fontSize: "0.8rem", opacity: 0.85, overflow: "hidden", textOverflow: "ellipsis" }}>{it.identity}</span>
          {it.status && (
            <span title={it.status} style={{ fontSize: "0.7rem", padding: "1px 7px", borderRadius: 8, whiteSpace: "nowrap",
              background: it.cooling ? "rgba(220,140,40,0.18)" : it.status === "valid" ? "rgba(60,180,90,0.16)" : "rgba(120,120,120,0.16)",
              color: it.cooling ? "#e0a040" : it.status === "valid" ? "#56c06a" : "#aaa" }}>
              {it.cooling ? text("cooling", "冷却中") : it.status}
            </span>
          )}
          {it.isActive ? (
            <span style={{ fontSize: "0.72rem", color: "var(--text-primary)", padding: "0 4px" }}>
              {kind === "keys" && rotation ? text("default", "默认") : text("in use", "使用中")}
            </span>
          ) : (
            <Button size="sm" onClick={() => use(it)}>{text("Use", "使用")}</Button>
          )}
          <Button size="sm" onClick={() => remove(it)}>{text("Remove", "删除")}</Button>
        </div>
      ))}

      {/* add — branches on mode */}
      {addMode === "api_key" && (
        <div className={styles.detailRow}>
          {items.length > 0 && (
            <Input className="font-mono" style={{ width: "9rem" }} placeholder={text("name (optional)", "名称（可选）")} value={newName} onChange={(e) => setNewName(e.target.value)} disabled={busy} />
          )}
          <Input className="flex-1 font-mono" type="password"
            placeholder={items.length === 0 ? text("paste your API key", "粘贴你的 API key") : text("add another key", "再加一个 key")}
            value={newKey} onChange={(e) => setNewKey(e.target.value)} disabled={busy} />
          <Button size="sm" onClick={addKey} disabled={busy || !newKey.trim()}>{busy ? text("Adding…", "添加中…") : text("Add key", "添加")}</Button>
        </div>
      )}
      {addMode === "login" && (
        <ProviderLogin provider={provider} profileId={newName.trim() || "default"} bare onChanged={() => { setNewName(""); load(); }} />
      )}
      {addMode === "code_paste" && (
        !pending ? (
          <div className={styles.detailRow}>
            <Input className="flex-1 font-mono" placeholder={text("optional label — leave blank to auto-name", "可选标签 — 留空自动命名")} value={newName} onChange={(e) => setNewName(e.target.value)} disabled={busy} />
            <Button size="sm" onClick={startCodeAdd} disabled={busy}>{busy ? text("Opening…", "打开中…") : text("Add account", "添加账号")}</Button>
          </div>
        ) : (
          <div className={styles.detailRow} style={{ flexWrap: "wrap" }}>
            <Input className="flex-1 font-mono" placeholder={text("paste the code from the login page", "粘贴登录页给出的 code")} value={code} onChange={(e) => setCode(e.target.value)} disabled={busy} />
            <Button size="sm" onClick={submitCode} disabled={busy || !code.trim()}>{busy ? text("Finishing…", "完成中…") : text("Finish", "完成")}</Button>
            <Button size="sm" onClick={() => { setPending(null); setCode(""); setMsg(""); }} disabled={busy}>{text("Cancel", "取消")}</Button>
          </div>
        )
      )}

      <div style={{ fontSize: "0.72rem", opacity: 0.55, marginTop: "0.2rem", lineHeight: 1.5 }}>
        {kind === "keys"
          ? (!multi
              ? text("Add more keys to switch between them or rotate automatically on rate limits.", "添加多个 key 后可以手动切换,或开启自动轮询(限流时切换)。")
              : rotation
                ? text("Keys rotate automatically — a rate-limited one cools down and the next takes over.", "多个 key 自动轮询 —— 某个被限流会冷却并切到下一个。")
                : text("Only the key in use is called. Switch with Use, or turn on rotation.", "只调用“使用中”的那个 key。点“使用”切换,或打开轮询。"))
          : text("Each account is a separate sign-in. Add one, then Use to switch which the framework runs on.", "每个账号是一次独立登录。添加后用“使用”切换框架跑哪个。")}
      </div>

      {msg && <div style={{ fontSize: "0.75rem", opacity: 0.75, marginTop: "0.2rem" }}>{msg}</div>}
    </div>
  );
}
