"use client";

import { Reorder, useDragControls } from "framer-motion";
import { Eye, EyeOff, GripVertical, Pencil, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { useTranslation } from "@/lib/i18n";

import { ProviderLogin } from "./provider-login";
import styles from "../settings-page.module.css";
import type { Provider } from "./types";

/** ONE management panel for every provider's accounts. An *account* is a profile
 *  holding one credential. EVERY provider uses the exact same fixed-column row;
 *  only the left content differs — api-key shows an editable KEY, login/claude
 *  show the account EMAIL. Right-hand controls (status · Validate · active ·
 *  Remove) are fixed-width so they never reflow. The active control is a single
 *  toggle: it shows the STATE by default and the ACTION on hover, and "none
 *  active" is allowed. A drag handle (≥2 accounts) sets the rotation priority.
 *  See docs/design/unified-account-management.md / the plan file. */

interface Account {
  id: string;
  name: string;        // label (api-key) or email (login)
  identity: string;    // masked key (api-key); "" for login
  email?: string;
  kind?: string;
  status?: string;
  is_active: boolean;     // single-active pin (rotation OFF)
  enabled?: boolean;      // independent on/off for rotation (rotation ON); default true
  can_reveal?: boolean;
  cooling?: boolean;
}
interface State {
  accounts: Account[];
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
const NON_ASCII = /[^\x20-\x7e]/;

function statusClass(status: string, cooling?: boolean): string {
  if (cooling) return `${styles.statusBadge} ${styles.cooling}`;
  if (status === "valid" || status.startsWith("valid")) return `${styles.statusBadge} ${styles.valid}`;
  if (status === "invalid_credential" || status === "needs_reauth") return `${styles.statusBadge} ${styles.error}`;
  return styles.statusBadge;
}

/** Active control: shows STATE by default, ACTION on hover. Fixed width. */
function ActiveToggle({ active, onActivate, onDeactivate }: { active: boolean; onActivate: () => void; onDeactivate: () => void }) {
  const { text } = useTranslation();
  const [hover, setHover] = useState(false);
  const label = active
    ? (hover ? text("Deactivate", "取消激活") : text("Activated", "已激活"))
    : (hover ? text("Activate", "激活") : text("Deactivated", "未激活"));
  // Green is reserved for the ON state only. "Deactivate" is a turning-OFF
  // action and a negative word, so it must NOT be green — show it neutral
  // grey on hover. "Activate" inherits the button's brand colour (undefined
  // → the default orange) as a call-to-action; "Deactivated" sits muted.
  const color = active
    ? (hover ? "var(--text-secondary)" : "var(--accent-green)")
    : (hover ? undefined : "var(--text-muted)");
  return (
    <Button size="sm" className={styles.acctCellBtn}
      onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}
      onClick={active ? onDeactivate : onActivate}
      style={{ color }}>
      {label}
    </Button>
  );
}

function AccountRow({
  provider, account, multi, rotation, onChanged, refresh, onCommit,
}: {
  provider: string;
  account: Account;
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
  const [renameVal, setRenameVal] = useState(account.name);
  const [keyMode, setKeyMode] = useState<"masked" | "revealed" | "editing">("masked");
  const [keyVal, setKeyVal] = useState(account.identity);
  const [vres, setVres] = useState<{ status: string; detail?: string } | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => { if (keyMode === "masked") setKeyVal(account.identity); }, [account.identity, keyMode]);

  const validate = useCallback(async () => {
    setVres({ status: "checking" });
    try {
      const d = await fetch(`${base}/${encodeURIComponent(account.id)}/validate`, { method: "POST" }).then((r) => r.json());
      setVres(d.ok ? { status: d.status, detail: d.detail } : { status: "unknown", detail: d.error });
    } catch { setVres({ status: "unknown" }); }
  }, [base, account.id]);

  // badge is a LIVE check on mount (kind-aware), never the stored status
  useEffect(() => { void validate(); }, [validate]);

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
    if (keyMode !== "editing") { setKeyMode("editing"); setKeyVal(""); return; }
    setKeyVal(v);
  }
  async function update() {
    const v = keyVal.trim();
    if (!v || NON_ASCII.test(v)) return;
    setBusy(true);
    const d = await fetch(`${base}/${encodeURIComponent(account.id)}/update`, { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ api_key: v, validate: true }) }).then((r) => r.json());
    setBusy(false);
    if (d.ok) { setKeyMode("masked"); await refresh(); onChanged?.(); void validate(); }
    else setVres({ status: "invalid_credential", detail: d.error });
  }
  function cancelEdit() { setKeyMode("masked"); setKeyVal(account.identity); }
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

  const editing = keyMode === "editing";
  const status = vres?.status ?? "checking";

  return (
    <Reorder.Item value={account} dragListener={false} dragControls={controls}
      className={styles.acctRow} onDragEnd={onCommit}
      whileDrag={{ backgroundColor: "var(--bg-hover)", borderRadius: 8, boxShadow: "var(--shadow)", zIndex: 5 }}
      transition={{ type: "spring", stiffness: 600, damping: 40 }}>
      {/* drag handle (only with ≥2 accounts) — press it to pick the row up;
          dragListener is off so the row's buttons/inputs stay clickable */}
      <span className={styles.dragHandle}
        onPointerDown={(e) => { if (multi) controls.start(e); }}
        style={{ visibility: multi ? "visible" : "hidden", touchAction: "none" }}>
        <GripVertical size={14} />
      </span>

      {/* content: name (+✎) and, for api-key, the editable key */}
      <div className={styles.acctContent}>
        {renaming ? (
          <Input className="font-mono" style={{ width: "9rem" }} autoFocus value={renameVal}
            onChange={(e) => setRenameVal(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") doRename(); if (e.key === "Escape") setRenaming(false); }}
            onBlur={doRename} />
        ) : (
          <span className={styles.acctName}>
            <span className={styles.acctNameText}>{account.name}</span>
            {/* same icon-button as the key eye/cancel: .iconBtn 28px, 15px glyph */}
            <button className={styles.iconBtn} title={text("Rename", "重命名")}
              onClick={() => { setRenameVal(account.name); setRenaming(true); }} style={{ flexShrink: 0 }}>
              <Pencil size={15} />
            </button>
          </span>
        )}

        {account.can_reveal && !renaming && (
          <span className={styles.acctKey}>
            <Input className="flex-1 font-mono" value={keyVal}
              placeholder={text("paste a new key to replace", "粘贴新 key 替换")}
              onChange={(e) => onKeyInput(e.target.value)} disabled={busy} />
            <button className={styles.iconBtn} title={text("Show / hide", "显示 / 隐藏")} onClick={reveal}>
              {keyMode === "revealed" ? <EyeOff size={15} /> : <Eye size={15} />}
            </button>
            {editing && (
              <>
                <Button size="sm" onClick={update} disabled={busy || !keyVal.trim()}>{text("Update", "更新")}</Button>
                <button className={styles.iconBtn} title={text("Cancel", "取消")} onClick={cancelEdit}><X size={14} /></button>
              </>
            )}
          </span>
        )}
      </div>

      {/* status (live) */}
      {status === "checking"
        ? <span className={styles.statusChecking}>{text("checking…", "验证中")}</span>
        : <span className={statusClass(status, account.cooling)} title={vres?.detail || status}>{account.cooling ? text("cooling", "冷却中") : status}</span>}

      {/* Validate — text button, every row */}
      <Button size="sm" className={styles.acctCellBtn} onClick={validate}>{text("Validate", "验证")}</Button>

      {/* Rotation ON → each account is independently in / out of the rotation
          (several can be on at once; turning one off just excludes it).
          Rotation OFF → single-active pin (Activate one, or none). Same toggle
          UI, different wiring. */}
      {rotation
        ? <ActiveToggle active={account.enabled ?? true} onActivate={() => setEnabled(true)} onDeactivate={() => setEnabled(false)} />
        : <ActiveToggle active={account.is_active} onActivate={activate} onDeactivate={deactivate} />}

      {/* Remove */}
      <Button size="sm" className={styles.acctCellBtn} onClick={remove}>{text("Remove", "删除")}</Button>
    </Reorder.Item>
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
      let d = (await fetch(base).then((r) => r.json())) as State;
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

  function onReorder(next: Account[]) {
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
  const ordered = order.map((id) => byId.get(id)).filter(Boolean) as Account[];
  const items = ordered.length === accounts.length ? ordered : accounts;

  return (
    <div className={styles.detailSection}>
      <div className={styles.detailSectionTitle}>
        <span>{state.add_mode === "api_key" ? text("API keys", "API 密钥") : text(`${provider.label} accounts`, `${provider.label} 账号`)}</span>
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
        className="flex flex-col gap-[2px] m-0 p-0 list-none">
        {items.map((a) => (
          <AccountRow key={a.id} provider={pid} account={a} multi={multi}
            rotation={state.rotation} onChanged={onChanged} refresh={load} onCommit={commitOrder} />
        ))}
      </Reorder.Group>

      {/* add */}
      {state.add_mode === "api_key" && (
        <div className={styles.detailRow}>
          {accounts.length > 0 && (
            <Input className="font-mono" style={{ width: "8rem" }} placeholder={text("name (optional)", "名字（可选）")} value={newName} onChange={(e) => setNewName(e.target.value)} disabled={busy} />
          )}
          <Input className="flex-1 font-mono" type="password"
            placeholder={accounts.length === 0 ? text("paste your API key", "粘贴你的 API key") : text("add another account (paste a key)", "添加账号（粘贴一个 key）")}
            value={newKey} onChange={(e) => setNewKey(e.target.value)} disabled={busy} />
          <Button size="sm" onClick={addKey} disabled={busy || !newKey.trim()}>{busy ? text("Adding…", "添加中…") : text("Add", "添加")}</Button>
        </div>
      )}
      {state.add_mode === "login" && (
        <ProviderLogin provider={provider} profileId={newName.trim() || undefined} bare
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

      <div style={{ fontSize: "0.72rem", opacity: 0.55, marginTop: "0.2rem", lineHeight: 1.5 }}>
        {state.add_mode === "api_key"
          ? (multi
              ? (state.rotation
                  ? text("Rotation on — every account on is used in turn (drag ⠿ to set priority). Turn any account off to drop it from the rotation; the others keep going.", "已开启轮询 —— 所有「已激活」的账号轮流使用(拖 ⠿ 调优先级)。把某个账号停用即可踢出轮询,其余照常。")
                  : text("Drag ⠿ to set rotation order. Each key is an account — Activate one to use it, the eye reveals / edits it, Validate checks it.", "拖 ⠿ 调轮询顺序。每个 key 是一个账号 —— Activate 选一个使用,眼睛查看/编辑,Validate 验证。"))
              : text("Add more keys as accounts to switch between them or rotate on rate limits. The eye reveals / edits the key.", "添加多个 key 作为账号即可切换或限流时轮询。眼睛可查看/编辑 key。"))
          : text("Each account is a separate sign-in. Activate to switch which the framework runs on.", "每个账号是一次独立登录。Activate 切换框架跑哪个。")}
      </div>

      {msg && <div style={{ fontSize: "0.75rem", opacity: 0.75, marginTop: "0.2rem" }}>{msg}</div>}
    </div>
  );
}
