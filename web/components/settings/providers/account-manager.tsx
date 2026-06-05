"use client";

import { Check, Eye, EyeOff, Pencil, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { useTranslation } from "@/lib/i18n";

import { ProviderLogin } from "./provider-login";
import styles from "../settings-page.module.css";
import type { Provider } from "./types";

/** ONE management panel for every provider's accounts. An *account* is a profile
 *  holding one credential — a KEY for api-key providers (identity = the key), a
 *  SIGN-IN for login providers (identity = email), a Claude subscription for
 *  claude-code. Uniform everywhere: rename (hover ✎), Use (switch the active
 *  one), validate, remove, and a rotation toggle. For api-key accounts the key
 *  can be revealed, edited and updated in place. Only the ADD step branches
 *  (paste a key / sign in / paste a code). Backed by /api/providers/{id}/
 *  accounts/* (the one profile-based surface). See docs/design/
 *  unified-account-management.md (P-E). */

interface Account {
  id: string;
  name: string;
  identity: string;     // masked key, or email
  email?: string;
  kind?: string;
  status?: string;
  is_active: boolean;
  can_reveal?: boolean;
  cooling?: boolean;
}
interface State {
  accounts: Account[];
  active: string;
  rotation: boolean;
  strategy: string;
  strategies: string[];
  add_mode: "api_key" | "login" | "code_paste";
}

const JSON_HEADERS = { "Content-Type": "application/json" };
const NON_ASCII = /[^\x20-\x7e]/;

function badgeStyle(status: string, cooling?: boolean): React.CSSProperties {
  const ok = status === "valid";
  return {
    fontSize: "0.7rem", padding: "1px 7px", borderRadius: 8, whiteSpace: "nowrap",
    background: cooling ? "rgba(220,140,40,0.18)" : ok ? "rgba(60,180,90,0.16)" : "rgba(160,160,160,0.16)",
    color: cooling ? "#e0a040" : ok ? "#56c06a" : "#bbb",
  };
}

/** One account row — name (hover ✎ rename), the key (reveal/edit/update for
 *  api-key) or email, status, validate, Use/active, remove. */
function AccountRow({
  provider, account, onChanged, refresh,
}: {
  provider: string;
  account: Account;
  onChanged?: () => void;
  refresh: () => void;
}) {
  const { text } = useTranslation();
  const base = `/api/providers/${encodeURIComponent(provider)}/accounts`;

  const [renaming, setRenaming] = useState(false);
  const [renameVal, setRenameVal] = useState(account.name);
  // key field state: masked → revealed → editing (mirrors the classic widget)
  const [keyMode, setKeyMode] = useState<"masked" | "revealed" | "editing">("masked");
  const [keyVal, setKeyVal] = useState(account.identity);
  const [vres, setVres] = useState<{ status: string; detail?: string } | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (keyMode === "masked") setKeyVal(account.identity);
  }, [account.identity, keyMode]);

  async function doRename() {
    const nv = renameVal.trim();
    setRenaming(false);
    if (!nv || nv === account.id) return;
    await fetch(`${base}/rename`, { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ id: account.id, name: nv }) });
    refresh();
  }
  async function reveal() {
    if (keyMode === "revealed") { setKeyMode("masked"); setKeyVal(account.identity); return; }
    if (keyMode === "editing") return;
    const d = await fetch(`${base}/${encodeURIComponent(account.id)}/reveal`).then((r) => r.json());
    if (d.ok) { setKeyVal(d.value); setKeyMode("revealed"); }
  }
  function onKeyInput(v: string) {
    // First edit while masked/revealed clears to a fresh entry (no leaking the
    // old key into the new one); subsequent keystrokes type normally.
    if (keyMode !== "editing") { setKeyMode("editing"); setKeyVal(""); return; }
    setKeyVal(v);
  }
  async function update() {
    const v = keyVal.trim();
    if (!v || NON_ASCII.test(v)) return;
    setBusy(true);
    const d = await fetch(`${base}/${encodeURIComponent(account.id)}/update`, {
      method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ api_key: v, validate: true }),
    }).then((r) => r.json());
    setBusy(false);
    if (d.ok) { setKeyMode("masked"); setVres(d.validation || null); refresh(); onChanged?.(); }
    else setVres({ status: "invalid_credential", detail: d.error });
  }
  function cancelEdit() { setKeyMode("masked"); setKeyVal(account.identity); }
  async function validate() {
    setBusy(true); setVres({ status: "checking" });
    const d = await fetch(`${base}/${encodeURIComponent(account.id)}/validate`, { method: "POST" }).then((r) => r.json());
    setBusy(false);
    setVres(d.ok ? { status: d.status, detail: d.detail } : { status: "unknown", detail: d.error });
  }
  async function use() {
    await fetch(`${base}/use`, { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ id: account.id }) });
    refresh();
  }
  async function remove() {
    await fetch(`${base}/remove`, { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ id: account.id }) });
    refresh(); onChanged?.();
  }

  const editing = keyMode === "editing";
  const status = vres?.status || account.status || "";

  return (
    <div className={styles.detailRow} style={{ flexWrap: "wrap" }}>
      {/* name + hover ✎ */}
      {renaming ? (
        <Input className="font-mono" style={{ width: "8rem" }} autoFocus value={renameVal}
          onChange={(e) => setRenameVal(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") doRename(); if (e.key === "Escape") setRenaming(false); }}
          onBlur={doRename} />
      ) : (
        <span style={{ display: "inline-flex", alignItems: "center", gap: 4, width: "8rem", minWidth: "8rem" }}>
          <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontSize: "0.85rem" }}>{account.name}</span>
          <button className={`${styles.iconBtn} ${styles.hoverShow}`} title={text("Rename", "重命名")}
            onClick={() => { setRenameVal(account.name); setRenaming(true); }} style={{ height: 18, width: 18, flexShrink: 0 }}>
            <Pencil size={11} />
          </button>
        </span>
      )}

      {/* identity: an editable key (api-key) or the email (login) */}
      {account.can_reveal ? (
        <>
          <Input className="flex-1 font-mono" style={{ minWidth: "10rem" }}
            type={keyMode === "masked" ? "text" : "text"}
            value={keyVal}
            placeholder={text("paste a new key to replace", "粘贴新 key 替换")}
            onChange={(e) => onKeyInput(e.target.value)} disabled={busy} />
          <button className={styles.iconBtn} title={text("Show/hide", "显示/隐藏")} onClick={reveal} style={{ height: 26, width: 26 }}>
            {keyMode === "revealed" ? <EyeOff size={15} /> : <Eye size={15} />}
          </button>
          {editing ? (
            <>
              <Button size="sm" onClick={update} disabled={busy || !keyVal.trim()}>{text("Update", "更新")}</Button>
              <button className={styles.iconBtn} title={text("Cancel", "取消")} onClick={cancelEdit} style={{ height: 26, width: 26 }}><X size={14} /></button>
            </>
          ) : (
            <button className={styles.iconBtn} title={text("Validate this key", "验证这个 key")} onClick={validate} disabled={busy} style={{ height: 26, width: 26 }}>
              <Check size={15} />
            </button>
          )}
        </>
      ) : (
        <span style={{ flex: 1, fontFamily: "monospace", fontSize: "0.8rem", opacity: 0.85, minWidth: "8rem" }}>{account.identity}</span>
      )}

      {status && status !== "checking" && (
        <span title={vres?.detail || status} style={badgeStyle(status, account.cooling)}>
          {account.cooling ? text("cooling", "冷却中") : status}
        </span>
      )}
      {status === "checking" && <span style={{ fontSize: "0.7rem", opacity: 0.6 }}>{text("checking…", "验证中…")}</span>}

      {account.is_active ? (
        <span style={{ fontSize: "0.72rem", color: "var(--text-primary)", padding: "0 4px" }}>{text("in use", "使用中")}</span>
      ) : (
        <Button size="sm" onClick={use}>{text("Use", "使用")}</Button>
      )}
      <Button size="sm" onClick={remove}>{text("Remove", "删除")}</Button>
    </div>
  );
}

export function AccountManager({ provider, onChanged }: { provider: Provider; onChanged?: () => void }) {
  const { text } = useTranslation();
  const pid = provider.id;
  const base = `/api/providers/${encodeURIComponent(pid)}/accounts`;

  const [state, setState] = useState<State | null>(null);
  const [newKey, setNewKey] = useState("");
  const [newName, setNewName] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  // code_paste add (claude-code)
  const [pending, setPending] = useState<{ session: string; url?: string } | null>(null);
  const [code, setCode] = useState("");

  const load = useCallback(async () => {
    try {
      let d = (await fetch(base).then((r) => r.json())) as State;
      // migrate a key set the old way (env var / config) into the list
      if ((d.accounts?.length ?? 0) === 0 && d.add_mode === "api_key" && provider.api_key_env) {
        try {
          const cfg = await fetch(`/api/config/key/${encodeURIComponent(provider.api_key_env)}?reveal=1`).then((r) => r.json());
          if (cfg.has_value && cfg.value && !NON_ASCII.test(cfg.value)) {
            await fetch(`${base}/keys`, { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ api_key: cfg.value, name: "default" }) });
            d = (await fetch(base).then((r) => r.json())) as State;
          }
        } catch { /* ignore */ }
      }
      setState(d);
    } catch { /* ignore */ }
  }, [base, provider.api_key_env]);

  useEffect(() => { load(); }, [load]);

  async function addKey() {
    const key = newKey.trim();
    if (!key) return;
    setBusy(true); setMsg(text("Validating…", "验证中…"));
    const d = await fetch(`${base}/keys`, { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ name: newName.trim(), api_key: key, validate: true }) }).then((r) => r.json());
    setBusy(false);
    if (d.ok) { setNewKey(""); setNewName(""); setMsg(""); await load(); onChanged?.(); }
    else setMsg(d.error || text("Could not add the key.", "添加失败。"));
  }
  async function toggleRotation(enabled: boolean) {
    await fetch(`${base}/rotation`, { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ enabled, strategy: state?.strategy }) });
    await load();
  }
  async function setStrategy(strategy: string) {
    await fetch(`${base}/rotation`, { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ enabled: true, strategy }) });
    await load();
  }
  async function validateAll() {
    setMsg(text("Validating all…", "正在验证全部…"));
    await fetch(`${base}/validate-all`, { method: "POST" });
    setMsg("");
    await load();
  }
  // code_paste add
  async function startCodeAdd() {
    setBusy(true); setMsg("");
    const d = await fetch(`${base}/add`, { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ name: newName.trim() }) }).then((r) => r.json());
    setBusy(false);
    if (d.error) setMsg(d.error);
    else { if (d.url) window.open(d.url, "_blank", "noopener"); setPending({ session: d.session, url: d.url }); setMsg(text("A login page opened — sign in, then paste the code below.", "已打开登录页 — 登录后把 code 粘到下面。")); }
  }
  async function submitCode() {
    if (!pending) return;
    setBusy(true);
    const d = await fetch(`${base}/add/code`, { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ session: pending.session, code }) }).then((r) => r.json());
    setBusy(false);
    if (d.ok) { setMsg(text("Account added.", "账号已添加。")); setPending(null); setCode(""); setNewName(""); await load(); }
    else { setMsg(d.error || text("That code didn't work.", "code 无效。")); if (typeof d.error === "string" && d.error.includes("no pending")) { setPending(null); setCode(""); } }
  }

  if (!state) return null;
  const accounts = state.accounts || [];
  const multi = accounts.length > 1;
  const anyKeys = accounts.some((a) => a.can_reveal);

  return (
    <div className={styles.detailSection}>
      <div className={styles.detailSectionTitle}>
        <span>{state.add_mode === "api_key" ? text("API keys", "API 密钥") : text(`${provider.label} accounts`, `${provider.label} 账号`)}</span>
        <span className={styles.modelCountSummary}>
          {accounts.length === 0 ? text("Not set", "未设置")
            : state.active ? text(`active: ${state.active}`, `当前：${state.active}`) : text(`${accounts.length}`, `${accounts.length}`)}
        </span>
      </div>

      {/* rotation toggle (>1 account) */}
      {multi && state.add_mode !== "code_paste" && (
        <div className={styles.detailRow} style={{ alignItems: "center" }}>
          <Switch checked={state.rotation} onCheckedChange={toggleRotation} />
          <span style={{ fontSize: "0.82rem", flex: 1 }}>{text("Rotate across accounts automatically", "在多个账号之间自动轮询")}</span>
          {state.rotation && (
            <select value={state.strategy} onChange={(e) => setStrategy(e.target.value)}
              style={{ background: "var(--input-bg, #1a1a1a)", color: "var(--text, #ddd)", border: "1px solid var(--border, #333)", borderRadius: 6, padding: "3px 8px", fontSize: "0.78rem" }}>
              <option value="fill_first">{text("in order (failover)", "按顺序（容错）")}</option>
              <option value="round_robin">{text("spread evenly", "均匀轮询")}</option>
              <option value="random">{text("random", "随机")}</option>
              <option value="least_used">{text("least used", "最少使用")}</option>
            </select>
          )}
        </div>
      )}

      {accounts.map((a) => (
        <AccountRow key={a.id} provider={pid} account={a} onChanged={onChanged} refresh={load} />
      ))}

      {/* add */}
      {state.add_mode === "api_key" && (
        <div className={styles.detailRow}>
          {accounts.length > 0 && (
            <Input className="font-mono" style={{ width: "8rem" }} placeholder={text("name (optional)", "名称（可选）")} value={newName} onChange={(e) => setNewName(e.target.value)} disabled={busy} />
          )}
          <Input className="flex-1 font-mono" type="password"
            placeholder={accounts.length === 0 ? text("paste your API key", "粘贴你的 API key") : text("add another account (paste a key)", "添加账号（粘贴一个 key）")}
            value={newKey} onChange={(e) => setNewKey(e.target.value)} disabled={busy} />
          <Button size="sm" onClick={addKey} disabled={busy || !newKey.trim()}>{busy ? text("Adding…", "添加中…") : text("Add", "添加")}</Button>
        </div>
      )}
      {state.add_mode === "login" && (
        <ProviderLogin provider={provider} profileId={newName.trim() || undefined} bare onChanged={() => { setNewName(""); load(); }} />
      )}
      {state.add_mode === "code_paste" && (
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

      <div className={styles.detailRow} style={{ marginTop: 2 }}>
        <span style={{ flex: 1, fontSize: "0.72rem", opacity: 0.55, lineHeight: 1.5 }}>
          {state.add_mode === "api_key"
            ? (multi
                ? text("Each key is an account — name it, Use to switch, ✎ to rename, the eye to reveal/edit. Turn on rotation to fail over on rate limits.", "每个 key 是一个账号 —— 起名、点“使用”切换、✎ 改名、眼睛查看/编辑。打开轮询可在限流时自动切换。")
                : text("Add more keys as accounts to switch between them or rotate on rate limits. The eye reveals or edits the key.", "添加多个 key 作为账号即可切换或限流时轮询。眼睛可查看/编辑 key。"))
            : text("Each account is a separate sign-in. Use to switch which the framework runs on.", "每个账号是一次独立登录。点“使用”切换框架跑哪个。")}
        </span>
        {anyKeys && multi && (
          <Button size="sm" onClick={validateAll}>{text("Validate all", "验证全部")}</Button>
        )}
      </div>

      {msg && <div style={{ fontSize: "0.75rem", opacity: 0.75, marginTop: "0.2rem" }}>{msg}</div>}
    </div>
  );
}
