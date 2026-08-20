"use client";

import { Reorder, useDragControls } from "framer-motion";
import { GripVertical, KeyRound, Pencil, Plus, RefreshCw, Trash2, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { useTranslation } from "@/lib/i18n";
import { normalizeSecretReplacement } from "@/lib/net/secret-replacement";

import { ProviderLogin } from "./provider-login";
import styles from "../settings-page.module.css";
import type { Provider, ProviderAccountView } from "./types";

/** ONE management panel for every provider's accounts. An *account* is a profile
 *  holding one credential. EVERY provider uses the same account-row structure;
 *  only the left content differs — api-key shows a masked key with an explicit
 *  replacement action, while login/claude shows the account email. Right-hand
 *  controls (status · Validate · active · Remove) use fixed columns while the
 *  detail pane is wide and reflow into additional rows when it is narrow. The
 *  active control is a single
 *  toggle: it shows the STATE by default and the ACTION on hover, and "none
 *  active" is allowed. A drag handle (≥2 accounts) sets the rotation priority.
 *  See docs/design/unified-account-management.md / the plan file. */

interface State {
  accounts: ProviderAccountView[];
  active: string;
  pinned?: string;
  rotation: boolean;
  strategy: string;
  strategies: string[];
  add_mode: "api_key" | "login" | "code_paste";
  // claude-code only — install guidance + which login methods this host offers.
  claude_installed?: boolean;
  claude_install_cmd?: string;
  backend_installed?: boolean;
  backend_install_cmd?: string;
  browser_login?: boolean;   // interactive sign-in works here (pty available)
  token_login?: boolean;     // setup-token paste (always available)
}

const JSON_HEADERS = { "Content-Type": "application/json" };

function statusTone(status: string): string {
  if (status === "valid") return styles.valid;
  if (status === "rate_limited" || status === "valid_model_unavailable") return styles.warn;
  if (status === "billing_blocked" || status === "valid_no_balance"
      || status === "invalid_credential" || status === "needs_reauth" || status === "revoked") {
    return styles.error;
  }
  return "";
}

/** Literal status text — the key is either usable or stopped for a reason. */
function statusLabel(status: string, text: (en: string, zh: string) => string): string {
  switch (status) {
    case "valid": return text("valid", "有效");
    case "rate_limited": return text("rate limited", "限流中");
    case "billing_blocked":
    case "valid_no_balance": return text("out of credits", "欠费停用");
    case "valid_model_unavailable": return text("model unavailable", "模型不可用");
    case "needs_reauth": return text("needs re-auth", "需重新验证");
    case "invalid_credential": return text("invalid key", "密钥无效");
    case "revoked": return text("revoked", "已失效");
    case "unknown": return text("unknown", "未知");
    case "checking": return text("Checking", "验证中");
    case "missing": return text("not configured", "未配置");
    default: return status;
  }
}

function AccountUseToggle({ active, rotation, onActivate, onDeactivate }: {
  active: boolean;
  rotation: boolean;
  onActivate: () => void;
  onDeactivate: () => void;
}) {
  const { text } = useTranslation();
  const label = rotation
    ? text("Include in rotation", "加入轮询")
    : text("Use this account for requests", "使用这个账号处理请求");
  return (
    <div className={styles.acctUseRow}>
      <Switch
        checked={active}
        onCheckedChange={(checked) => checked ? onActivate() : onDeactivate()}
        aria-label={label}
      />
      <span>{label}</span>
    </div>
  );
}

function AccountRow({
  provider, account, multi, rotation, onChanged, refresh, onCommit,
}: {
  provider: string;
  account: ProviderAccountView;
  multi: boolean;
  rotation: boolean;
  onChanged?: () => void;
  refresh: () => void;
  onCommit: () => void;
}) {
  const { text } = useTranslation();
  const controls = useDragControls();
  const base = `/api/providers/${encodeURIComponent(provider)}/accounts`;

  const [renaming, setRenaming] = useState(false);
  const [renameVal, setRenameVal] = useState(account.label || account.name);
  const [editingKey, setEditingKey] = useState(false);
  const [replacement, setReplacement] = useState("");
  const [vres, setVres] = useState<{ status: string; detail?: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const validate = useCallback(async (opts?: { ping?: boolean }) => {
    setVres({ status: "checking" });
    try {
      const qs = opts?.ping ? "?ping=true" : "";
      const d = await fetch(`${base}/${encodeURIComponent(account.id)}/validate${qs}`, { method: "POST" }).then((r) => r.json());
      const status = typeof d.status === "string" && d.status ? d.status : "unknown";
      setVres({ status, detail: d.detail || d.error });
    } catch { setVres({ status: "unknown" }); }
  }, [base, account.id]);

  // On-mount: layer-1 only (do not spend tokens). Explicit click pings.
  useEffect(() => { void validate(); }, [validate]);

  async function doRename() {
    const nv = renameVal.trim();
    setRenaming(false);
    if (!nv || nv === (account.label || account.name)) return;
    // A refused rename (name taken, invalid characters) answers 4xx and leaves
    // the account named as it was — surface it instead of showing the new name.
    const r = await fetch(`${base}/rename`, { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ id: account.id, name: nv }) });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      setVres({ status: "unknown", detail: d.error });
    }
    refresh();
  }
  async function update() {
    const v = normalizeSecretReplacement(replacement, account.masked_key);
    if (v === null) return;
    setBusy(true);
    try {
      const d = await fetch(`${base}/${encodeURIComponent(account.id)}/update`, { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ api_key: v, validate: true }) }).then((r) => r.json());
      if (d.ok) {
        setEditingKey(false);
        setReplacement("");
        await refresh();
        onChanged?.();
        void validate({ ping: true });
      } else {
        setVres({ status: "invalid_credential", detail: d.error });
      }
    } catch {
      setVres({ status: "unknown" });
    } finally {
      setBusy(false);
    }
  }
  function startEdit() { setReplacement(""); setEditingKey(true); }
  function cancelEdit() { setReplacement(""); setEditingKey(false); }
  async function activate() {
    await fetch(`${base}/use`, { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ id: account.id }) });
    refresh();
  }
  async function deactivate() {
    await fetch(`${base}/use`, { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ id: "" }) });
    refresh();
  }
  // Rotation ON: independent per-account on/off (several can be on at once).
  async function setEnabled(enabled: boolean) {
    await fetch(`${base}/enabled`, { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ id: account.id, enabled }) });
    refresh();
  }
  async function remove() {
    await fetch(`${base}/remove`, { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ id: account.id }) });
    refresh(); onChanged?.();
  }

  const status = vres?.status ?? "checking";
  const statusText = status === "checking" ? text("Checking", "验证中") : statusLabel(status, text);
  const validateLabel = `${statusText}. ${text("Validate account", "验证账号")}`;

  return (
    <Reorder.Item value={account} dragListener={false} dragControls={controls}
      className={styles.acctRow} onDragEnd={onCommit}
      whileDrag={{ backgroundColor: "var(--bg-hover)", boxShadow: "var(--shadow)", zIndex: 5 }}
      transition={{ type: "spring", stiffness: 600, damping: 40 }}>
      <div className={styles.acctCardHeader}>
        {multi && (
          <span className={styles.dragHandle} onPointerDown={(e) => controls.start(e)} style={{ touchAction: "none" }}>
            <GripVertical size={14} />
          </span>
        )}
        {renaming ? (
          <Input className={styles.acctRenameInput} autoFocus value={renameVal}
            aria-label={text("Account name", "账号名称")}
            onChange={(e) => setRenameVal(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") doRename(); if (e.key === "Escape") setRenaming(false); }}
            onBlur={doRename} />
        ) : (
          <span className={styles.acctName}>
            <span className={styles.acctNameText}>{account.label || account.name}</span>
            {/* Keep rename and cancel controls on the same compact icon size. */}
            <button type="button" className={styles.iconBtn} title={text("Rename", "重命名")}
              aria-label={text("Rename account", "重命名账号")}
              onClick={() => { setRenameVal(account.label || account.name); setRenaming(true); }}>
              <Pencil size={15} />
            </button>
          </span>
        )}

        <button
          type="button"
          className={`${styles.acctStatusButton} ${statusTone(status)}`}
          onClick={() => { void validate({ ping: true }); }}
          aria-label={validateLabel}
          aria-busy={status === "checking"}
          title={vres?.detail || text("Validate account", "验证账号")}
        >
          <RefreshCw size={13} />
          <span>{statusText}</span>
        </button>

        <button type="button" className={`${styles.iconBtn} ${styles.dangerIconBtn}`}
          title={text("Remove account", "删除账号")} aria-label={text("Remove account", "删除账号")}
          onClick={remove}>
          <Trash2 size={15} />
        </button>
      </div>

      {account.kind === "api_key" && !renaming && (
        <div className={styles.acctKey}>
          {editingKey ? (
            <>
              <Input className="font-mono" type="password" autoFocus
                value={replacement} autoComplete="new-password"
                placeholder={text("Paste a new API key", "粘贴新的 API 密钥")}
                onChange={(e) => setReplacement(e.target.value)} disabled={busy} />
              <Button size="sm" onClick={update} disabled={busy || !replacement.trim()}>{text("Save", "保存")}</Button>
              <button type="button" className={styles.iconBtn} title={text("Cancel", "取消")}
                aria-label={text("Cancel replacing API key", "取消替换 API 密钥")} onClick={cancelEdit}>
                <X size={14} />
              </button>
            </>
          ) : (
            <>
              <Input className="font-mono" readOnly
                value={account.has_value ? account.masked_key : ""}
                placeholder={text("Not set", "未设置")} />
              <button type="button" className={styles.iconBtn} title={text("Replace API key", "替换 API 密钥")}
                aria-label={text("Replace API key", "替换 API 密钥")} onClick={startEdit}>
                <KeyRound size={15} />
              </button>
            </>
          )}
        </div>
      )}

      {rotation
        ? <AccountUseToggle active={account.enabled ?? true} rotation onActivate={() => setEnabled(true)} onDeactivate={() => setEnabled(false)} />
        : <AccountUseToggle active={account.is_active} rotation={false} onActivate={activate} onDeactivate={deactivate} />}
    </Reorder.Item>
  );
}

export function AccountManager({ provider, onChanged }: { provider: Provider; onChanged?: () => void }) {
  const { text } = useTranslation();
  const pid = provider.id;
  const base = `/api/providers/${encodeURIComponent(pid)}/accounts`;

  const [state, setState] = useState<State | null>(null);
  const [newKey, setNewKey] = useState("");
  const [newKeyName, setNewKeyName] = useState("");
  const [newName, setNewName] = useState("");
  const [addingKey, setAddingKey] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  // Local drag order (account ids). Driven by framer Reorder for instant,
  // FLIP-animated reordering; persisted to /reorder on drag end. orderRef
  // mirrors it so the drag-end commit reads the freshest order (state may
  // not have flushed by the time onDragEnd fires).
  const [order, setOrder] = useState<string[]>([]);
  const orderRef = useRef<string[]>([]);
  const [pending, setPending] = useState<{ session: string; url?: string } | null>(null);
  const [code, setCode] = useState("");
  // claude-code add: which login method, and the pasted setup-token.
  const [method, setMethod] = useState<"browser" | "token">("browser");
  const [token, setToken] = useState("");
  const pollStop = useRef(false);

  const load = useCallback(async () => {
    try {
      const d = (await fetch(base).then((r) => r.json())) as State;
      setState(d);
    } catch { /* ignore */ }
  }, [base]);

  useEffect(() => { load(); }, [load]);

  // Mirror the server account order into local order, but only when the SET
  // of accounts changes (add / remove). A pure reorder leaves the set equal,
  // so we keep the local order we already applied — no fight with the drag.
  useEffect(() => {
    const ids = (state?.accounts || []).map((a) => a.id);
    setOrder((prev) => {
      const sameSet = prev.length === ids.length
        && prev.every((id) => ids.includes(id)) && ids.every((id) => prev.includes(id));
      const next = sameSet ? prev : ids;
      orderRef.current = next;
      return next;
    });
  }, [state?.accounts]);

  function onReorder(next: ProviderAccountView[]) {
    const ids = next.map((a) => a.id);
    orderRef.current = ids;
    setOrder(ids);
  }
  async function commitOrder() {
    const ids = orderRef.current;
    if (ids.length < 2) return;
    await fetch(`${base}/reorder`, { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ order: ids }) });
  }

  async function addKey() {
    const key = newKey.trim();
    if (!key) return;
    setBusy(true); setMsg(text("Validating…", "验证中…"));
    // A rejected key answers 4xx with {error}; only a 2xx {ok:true} stored it.
    const r = await fetch(`${base}/keys`, { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ name: newKeyName.trim(), api_key: key, validate: true }) });
    const d = await r.json().catch(() => ({}));
    setBusy(false);
    if (r.ok && d.ok) { setNewKey(""); setNewKeyName(""); setAddingKey(false); setMsg(""); await load(); onChanged?.(); }
    else setMsg(d.error || text("Could not add the key.", "添加失败。"));
  }
  function toggleAddingKey() {
    if (addingKey) {
      setNewKey("");
      setNewKeyName("");
    }
    setAddingKey((open) => !open);
    setMsg("");
  }
  async function toggleRotation(enabled: boolean) {
    await fetch(`${base}/rotation`, { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ enabled, strategy: state?.strategy }) });
    await load();
  }
  async function setStrategy(strategy: string) {
    await fetch(`${base}/rotation`, { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ enabled: true, strategy }) });
    await load();
  }
  // POST JSON with a hard client timeout so a stuck backend can't freeze the
  // button forever (the old "Add account does nothing" symptom).
  async function postJson(url: string, body: unknown, timeoutMs: number): Promise<any> {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), timeoutMs);
    try {
      return await fetch(url, { method: "POST", headers: JSON_HEADERS, body: JSON.stringify(body), signal: ctrl.signal }).then((r) => r.json());
    } finally { clearTimeout(t); }
  }
  // Watch an in-flight browser login: the Claude CLI usually completes the
  // OAuth itself (localhost loopback — "you're all set up", no code to paste),
  // so we poll until the backend reports done, falling back to the manual
  // paste-code box if it keeps waiting.
  async function pollAdd(session: string) {
    pollStop.current = false;
    const deadline = Date.now() + 240000;
    while (!pollStop.current && Date.now() < deadline) {
      await new Promise((r) => setTimeout(r, 2500));
      if (pollStop.current) return;
      let d: any;
      try { d = await fetch(`${base}/add/poll?session=${encodeURIComponent(session)}`).then((r) => r.json()); }
      catch { continue; }
      if (!d.done) continue;
      if (d.ok) { setMsg(text("Account added.", "账号已添加。")); setPending(null); setCode(""); setNewName(""); await load(); onChanged?.(); }
      else { setMsg(d.error || text("Login didn't complete — try again.", "登录未完成 —— 请重试。")); setPending(null); setCode(""); }
      return;
    }
  }
  async function startCodeAdd() {
    setBusy(true); setMsg(text("Opening the sign-in page…", "正在打开登录页…"));
    let d: any;
    try { d = await postJson(`${base}/add`, { name: newName.trim() }, 70000); }
    catch { setBusy(false); setMsg(text("The backend didn't respond in time — try again.", "后端响应超时 —— 请重试。")); return; }
    setBusy(false);
    if (d.error === "BROWSER_LOGIN_UNAVAILABLE") { setMethod("token"); setMsg(d.detail || text("Use the Paste token method instead.", "请改用「粘贴 token」方式。")); return; }
    if (d.error) { setMsg(d.error); return; }
    if (d.url) window.open(d.url, "_blank", "noopener");
    setPending({ session: d.session, url: d.url });
    setMsg(text("Sign in in the browser. If it says you're all set, you're done. (If it shows a code, paste it below.)", "在浏览器里登录。若提示已完成即可，无需操作。（如果页面给了 code，就粘到下面。）"));
    pollAdd(d.session);  // detect loopback auto-completion
  }
  async function submitCode() {
    if (!pending) return;
    pollStop.current = true;  // manual paste takes over from the loopback poll
    setBusy(true); setMsg(text("Exchanging the code with Claude — this can take a minute or two…", "正在与 Claude 交换 code —— 可能需要一两分钟…"));
    let d: any;
    try { d = await postJson(`${base}/add/code`, { session: pending.session, code }, 250000); }
    catch { setBusy(false); setMsg(text("Login is taking too long — the code may have expired. Cancel and try again.", "登录耗时过长 —— code 可能已失效。取消后重试。")); return; }
    setBusy(false);
    if (d.ok) { setMsg(text("Account added.", "账号已添加。")); setPending(null); setCode(""); setNewName(""); await load(); onChanged?.(); }
    else { setMsg(d.error || text("That code didn't work.", "code 无效。")); if (typeof d.error === "string" && d.error.includes("no pending")) { setPending(null); setCode(""); } }
  }
  async function startTokenAdd() {
    const t = token.trim();
    if (!t) { setMsg(text("Paste the token from `claude setup-token`.", "请粘贴 `claude setup-token` 生成的 token。")); return; }
    setBusy(true); setMsg(text("Adding the account…", "正在添加账号…"));
    let d: any;
    try { d = await postJson(`${base}/add/token`, { name: newName.trim(), token: t }, 130000); }
    catch { setBusy(false); setMsg(text("The backend didn't respond in time — try again.", "后端响应超时 —— 请重试。")); return; }
    setBusy(false);
    if (d.ok) { setMsg(text("Account added.", "账号已添加。")); setToken(""); setNewName(""); await load(); onChanged?.(); }
    else setMsg(d.error || text("Could not add the account.", "添加失败。"));
  }

  if (!state) return null;
  const accounts = state.accounts || [];
  const multi = accounts.length > 1;
  // Render in local drag order; fall back to server order until the order
  // state has synced (or if it ever drifts out of sync with the account set).
  const byId = new Map(accounts.map((a) => [a.id, a] as const));
  const ordered = order.map((id) => byId.get(id)).filter(Boolean) as ProviderAccountView[];
  const items = ordered.length === accounts.length ? ordered : accounts;

  return (
    <div className={styles.detailSection}>
      <div className={styles.detailSectionTitle}>
        <span>{state.add_mode === "api_key" ? text("API keys", "API 密钥") : text(`${provider.label} accounts`, `${provider.label} 账号`)}</span>
        <span className={styles.modelCountSummary}>
          {text(`${accounts.length} ${accounts.length === 1 ? "account" : "accounts"}`, `${accounts.length} 个账号`)}
        </span>
      </div>

      {/* rotation toggle (≥2 accounts) */}
      {multi && state.add_mode !== "code_paste" && (
        <div className={styles.detailRow} style={{ alignItems: "center" }}>
          <Switch checked={state.rotation} onCheckedChange={toggleRotation} />
          <span style={{ fontSize: "0.82rem", flex: 1 }}>{text("Rotate across accounts automatically", "在多个账号之间自动轮询")}</span>
          {state.rotation && (
            <select value={state.strategy} onChange={(e) => setStrategy(e.target.value)}
              style={{ height: "var(--ui-button-h)", background: "var(--bg-input)", color: "var(--text-primary)", border: "1px solid var(--border)", borderRadius: "var(--ui-button-radius)", padding: "0 12px", fontSize: "0.875rem", cursor: "pointer" }}>
              <option value="fill_first">{text("in order (failover)", "按顺序（容错）")}</option>
              <option value="round_robin">{text("spread evenly", "均匀轮询")}</option>
              <option value="random">{text("random", "随机")}</option>
              <option value="least_used">{text("least used", "最少使用")}</option>
            </select>
          )}
        </div>
      )}

      <Reorder.Group axis="y" values={items} onReorder={onReorder}
        className={styles.accountList}>
        {items.map((a) => (
          <AccountRow key={a.id} provider={pid} account={a} multi={multi}
            rotation={state.rotation} onChanged={onChanged} refresh={load} onCommit={commitOrder} />
        ))}
      </Reorder.Group>

      {/* Keep adding visually secondary once an account exists. */}
      {state.add_mode === "api_key" && (
        <>
          {accounts.length > 0 && (
            <button type="button" className={styles.addCredentialTrigger}
              aria-expanded={addingKey} aria-controls="add-api-key-form"
              onClick={toggleAddingKey}>
              {addingKey ? <X size={15} /> : <Plus size={15} />}
              <span>{addingKey ? text("Cancel adding key", "取消添加密钥") : text("Add API key", "添加 API 密钥")}</span>
            </button>
          )}
          {(accounts.length === 0 || addingKey) && (
            <div id="add-api-key-form" className={styles.addCredentialCard}>
              <div className={styles.addCredentialTitle}>
                {accounts.length === 0 ? text("Add your API key", "添加 API 密钥") : text("New API key", "新 API 密钥")}
              </div>
              <div className={`${styles.addCredentialFields} ${accounts.length === 0 ? styles.addCredentialFieldsSingle : ""}`}>
                {accounts.length > 0 && (
                  <Input className="font-mono" placeholder={text("Account name (optional)", "账号名称（可选）")}
                    value={newKeyName} onChange={(e) => setNewKeyName(e.target.value)} disabled={busy} />
                )}
                <Input className="font-mono" type="password" placeholder={text("Paste API key", "粘贴 API 密钥")}
                  value={newKey} onChange={(e) => setNewKey(e.target.value)} disabled={busy} />
                <Button size="sm" onClick={addKey} disabled={busy || !newKey.trim()}>
                  {busy ? text("Adding…", "添加中…") : text("Add key", "添加密钥")}
                </Button>
              </div>
            </div>
          )}
        </>
      )}
      {/* Some api-key providers ALSO support a native sign-in (anthropic:
          OAuth subscription / setup-token; gemini; …). add_mode is a single
          value (api_key) so it can't carry both paths — surface the login
          button(s) here whenever the provider advertises login_methods, so
          the key box and the sign-in button coexist instead of the sign-in
          entry disappearing. */}
      {state.add_mode === "api_key" && (provider.login_methods?.length ?? 0) > 0 && (
        <ProviderLogin provider={provider} accountLabel={newName.trim() || undefined} bare
          leadingInput={<Input className="flex-1 font-mono" placeholder={text("name (optional)", "名字（可选）")} value={newName} onChange={(e) => setNewName(e.target.value)} />}
          onChanged={() => { setNewName(""); load(); onChanged?.(); }} />
      )}
      {state.add_mode === "login" && (
        <ProviderLogin provider={provider} accountLabel={newName.trim() || undefined} bare
          leadingInput={<Input className="flex-1 font-mono" placeholder={text("name (optional)", "名字（可选）")} value={newName} onChange={(e) => setNewName(e.target.value)} />}
          onChanged={() => { setNewName(""); load(); }} />
      )}
      {state.add_mode === "code_paste" && (
        (state.claude_installed === false || state.backend_installed === false) ? (
          /* Guide the user to install the one-time prerequisites first. Both
             login methods sign in via the Claude Code CLI; the backend (proxy)
             is what holds the accounts. Gating here also avoids the add button
             triggering a slow (up to 300s) auto-install that the client would
             time out on. */
          <div style={{ display: "flex", flexDirection: "column", gap: "0.4rem" }}>
            <div style={{ fontSize: "0.8rem" }}>
              {text("Claude accounts need a one-time setup. Run the command(s) below in a terminal:", "Claude 账号需要一次性安装。请在终端运行下面的命令：")}
            </div>
            {state.claude_installed === false && (
              <code style={{ fontSize: "0.75rem", padding: "0.35rem 0.5rem", background: "rgba(127,127,127,0.12)", borderRadius: 6, userSelect: "all", fontFamily: "monospace" }}>
                {state.claude_install_cmd || "npm install -g @anthropic-ai/claude-code"}
              </code>
            )}
            {state.backend_installed === false && (
              <code style={{ fontSize: "0.75rem", padding: "0.35rem 0.5rem", background: "rgba(127,127,127,0.12)", borderRadius: 6, userSelect: "all", fontFamily: "monospace" }}>
                {state.backend_install_cmd || "npm install -g @rynfar/meridian"}
              </code>
            )}
            <div className={styles.detailRow}>
              <Button size="sm" onClick={() => { setMsg(""); load(); }} disabled={busy}>{text("I've installed it — recheck", "已安装，重新检测")}</Button>
            </div>
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: "0.4rem" }}>
            {/* method picker */}
            <div className={styles.detailRow} style={{ gap: "0.35rem" }}>
              {([["browser", text("Browser sign-in", "浏览器登录")], ["token", text("Paste token", "粘贴 token")]] as const).map(([m, label]) => {
                const disabled = m === "browser" && state.browser_login === false;
                const active = method === m;
                return (
                  <button key={m} type="button" disabled={disabled || busy}
                    onClick={() => { pollStop.current = true; setMethod(m as "browser" | "token"); setMsg(""); setPending(null); setCode(""); }}
                    style={{ fontSize: "0.75rem", padding: "0.25rem 0.6rem", borderRadius: 6, cursor: disabled ? "not-allowed" : "pointer",
                      border: active ? "1px solid var(--accent, #6b8afd)" : "1px solid rgba(127,127,127,0.3)",
                      background: active ? "rgba(107,138,253,0.14)" : "transparent", opacity: disabled ? 0.4 : 1 }}>
                    {label}
                  </button>
                );
              })}
            </div>

            {method === "browser" ? (
              state.browser_login === false ? (
                <div style={{ fontSize: "0.75rem", opacity: 0.7 }}>
                  {text("Browser sign-in isn't available on this machine — use Paste token.", "此机器不支持浏览器登录 —— 请用「粘贴 token」。")}
                </div>
              ) : !pending ? (
                <div className={styles.detailRow}>
                  <Input className="flex-1 font-mono" placeholder={text("optional label — leave blank to auto-name", "可选标签 — 留空自动命名")} value={newName} onChange={(e) => setNewName(e.target.value)} disabled={busy} />
                  <Button size="sm" onClick={startCodeAdd} disabled={busy}>{busy ? text("Opening…", "打开中…") : text("Add account", "添加账号")}</Button>
                </div>
              ) : (
                <div className={styles.detailRow} style={{ flexWrap: "wrap" }}>
                  <Input className="flex-1 font-mono" placeholder={text("paste the code from the login page", "粘贴登录页给出的 code")} value={code} onChange={(e) => setCode(e.target.value)} disabled={busy} />
                  <Button size="sm" onClick={submitCode} disabled={busy || !code.trim()}>{busy ? text("Finishing…", "完成中…") : text("Finish", "完成")}</Button>
                  <Button size="sm" onClick={() => { pollStop.current = true; setPending(null); setCode(""); setMsg(""); }} disabled={busy}>{text("Cancel", "取消")}</Button>
                </div>
              )
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: "0.3rem" }}>
                <div style={{ fontSize: "0.72rem", opacity: 0.7 }}>
                  {text("Run", "运行")} <code style={{ fontFamily: "monospace", userSelect: "all" }}>claude setup-token</code> {text("in a terminal, then paste the token it prints:", "（在终端里），把它输出的 token 粘进来：")}
                </div>
                <div className={styles.detailRow} style={{ flexWrap: "wrap" }}>
                  <Input className="font-mono" style={{ width: "8rem" }} placeholder={text("label (optional)", "标签（可选）")} value={newName} onChange={(e) => setNewName(e.target.value)} disabled={busy} />
                  <Input className="flex-1 font-mono" type="password" placeholder={text("paste sk-ant-… token", "粘贴 sk-ant-… token")} value={token} onChange={(e) => setToken(e.target.value)} disabled={busy} />
                  <Button size="sm" onClick={startTokenAdd} disabled={busy || !token.trim()}>{busy ? text("Adding…", "添加中…") : text("Add account", "添加账号")}</Button>
                </div>
              </div>
            )}
          </div>
        )
      )}

      <div className={styles.accountHelp}>
        {state.add_mode === "api_key"
          ? text("Each key is a separate account. Saved values stay masked.", "每个密钥都是独立账号，保存后的值始终显示为掩码。")
          : text("Each account is a separate sign-in. Choose which account handles requests.", "每个账号都是独立登录，可选择处理请求的账号。")}
      </div>

      {msg && <div style={{ fontSize: "0.75rem", opacity: 0.75, marginTop: "0.2rem" }}>{msg}</div>}
    </div>
  );
}
