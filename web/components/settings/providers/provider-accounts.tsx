"use client";

import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useTranslation } from "@/lib/i18n";

import { ProviderLogin } from "./provider-login";
import styles from "../settings-page.module.css";
import type { Provider } from "./types";

/** Manage the multiple accounts a provider can run on: list, add, activate /
 *  deactivate (which one the framework uses — or none), rename, remove. One
 *  component for EVERY provider — it hits /api/providers/{id}/accounts/* and
 *  branches only on the backend-declared `add_mode`:
 *
 *    - "code_paste"  claude-code's interactive OAuth (POST .../add returns a
 *                    login URL we open; the user pastes the code to .../add/code).
 *    - "login"       every other provider — the add step is the shared
 *                    <ProviderLogin> flow targeting the new account's profile,
 *                    so OAuth / device-code / import-from-CLI all work here too.
 *
 *  Accounts are profiles: each is an independent credential set. The active one
 *  is what requests use when no profile is pinned. For claude-code this stays
 *  decoupled from the terminal `claude auth login` you chat on. */

interface Account {
  name: string;
  email?: string;
  kind?: string;
  status?: string;
  count?: number;
}
interface LoginMethod {
  id: string;
  label: string;
}
interface AccountsState {
  installed: boolean;
  ready: boolean;
  active: string | null;
  accounts: Account[];
  add_mode?: "code_paste" | "login";
  login_methods?: LoginMethod[];
}
interface Pending {
  session: string;
  name: string;
  url: string;
}

const JSON_HEADERS = { "Content-Type": "application/json" };

export function ProviderAccounts({ provider }: { provider: Provider }) {
  const { text } = useTranslation();
  const pid = provider.id;
  const base = `/api/providers/${encodeURIComponent(pid)}/accounts`;

  const [state, setState] = useState<AccountsState | null>(null);
  const [newName, setNewName] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [pending, setPending] = useState<Pending | null>(null); // code_paste only
  const [code, setCode] = useState("");
  const [renaming, setRenaming] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");

  const load = useCallback(async () => {
    try {
      const r = await fetch(base);
      setState((await r.json()) as AccountsState);
    } catch {
      /* ignore */
    }
  }, [base]);

  useEffect(() => {
    load();
  }, [load]);

  async function setActive(name: string) {
    await fetch(`${base}/use`, {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({ name }),
    });
    load();
  }

  async function remove(name: string) {
    await fetch(`${base}/remove`, {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({ name }),
    });
    load();
  }

  async function doRename(old: string) {
    const nv = renameValue.trim();
    if (!nv || nv === old) {
      setRenaming(null);
      return;
    }
    const r = await fetch(`${base}/rename`, {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({ old, new: nv }),
    });
    const d = await r.json();
    if (d.ok) {
      setRenaming(null);
      setRenameValue("");
      load();
    } else {
      setMsg(d.error || text("Rename failed.", "改名失败。"));
    }
  }

  // ---- code_paste add (claude-code) -------------------------------------
  async function startAdd() {
    const name = newName.trim();
    setBusy(true);
    setMsg("");
    try {
      const r = await fetch(`${base}/add`, {
        method: "POST",
        headers: JSON_HEADERS,
        body: JSON.stringify({ name }),
      });
      const d = await r.json();
      if (d.error) {
        setMsg(d.error);
      } else {
        if (d.url) window.open(d.url, "_blank", "noopener");
        setPending({ session: d.session, name: d.name, url: d.url });
        setMsg(text(
          "A login page opened — sign in with the account you want to add, then copy the code it shows and paste it below.",
          "已打开登录页 — 用你要添加的账号登录，然后把页面给出的 code 复制粘贴到下面。",
        ));
      }
    } catch {
      setMsg(text("Could not start the login.", "启动登录失败。"));
    }
    setBusy(false);
  }

  async function submitCode() {
    if (!pending) return;
    setBusy(true);
    try {
      const r = await fetch(`${base}/add/code`, {
        method: "POST",
        headers: JSON_HEADERS,
        body: JSON.stringify({ session: pending.session, code }),
      });
      const d = await r.json();
      if (d.ok) {
        setMsg(text("Account added.", "账号已添加。"));
        setPending(null);
        setCode("");
        setNewName("");
        load();
      } else {
        setMsg(d.error || text("That code didn't work — try again.", "code 无效，请重试。"));
        if (typeof d.error === "string" && d.error.includes("no pending")) {
          setPending(null);
          setCode("");
        }
      }
    } catch {
      setMsg(text("Could not finish the login.", "完成登录失败。"));
    }
    setBusy(false);
  }

  function cancelAdd() {
    setPending(null);
    setCode("");
    setMsg("");
  }

  if (!state) return null;
  const notReady = !state.installed || !state.ready;
  const accounts = state.accounts || [];
  const codePaste = state.add_mode === "code_paste";

  return (
    <div className={styles.detailSection}>
      <div className={styles.detailSectionTitle}>
        <span>{text(`${provider.label} accounts`, `${provider.label} 账号`)}</span>
        <span className={styles.modelCountSummary}>
          {state.active
            ? text(`active: ${state.active}`, `激活：${state.active}`)
            : text("none active", "未激活")}
        </span>
      </div>

      {notReady && codePaste && (
        <div style={{ fontSize: "0.8rem", opacity: 0.7, marginBottom: "0.5rem" }}>
          {text(
            "The backend sets itself up the first time you add an account — just click Add account.",
            "第一次添加账号时后端会自动装好 — 直接点“添加账号”即可。",
          )}
        </div>
      )}

      {accounts.map((a) => (
        <div
          key={a.name}
          className={styles.detailRow}
          style={{ alignItems: "center", flexWrap: "wrap", gap: "0.4rem" }}
        >
          {renaming === a.name ? (
            <>
              <Input
                className="flex-1 font-mono"
                value={renameValue}
                onChange={(e) => setRenameValue(e.target.value)}
              />
              <Button size="sm" onClick={() => doRename(a.name)}>{text("Save", "保存")}</Button>
              <Button size="sm" onClick={() => setRenaming(null)}>{text("Cancel", "取消")}</Button>
            </>
          ) : (
            <>
              <span style={{ flex: 1, fontFamily: "monospace", minWidth: "8rem" }}>
                {a.name === state.active ? "→ " : "  "}
                {a.name}
                {a.email && a.email !== a.name ? (
                  <span style={{ opacity: 0.55 }}>{"  " + a.email}</span>
                ) : null}
                {a.count && a.count > 1 ? (
                  <span style={{ opacity: 0.45 }}>{`  (${a.count} keys)`}</span>
                ) : null}
              </span>
              {a.name === state.active ? (
                <Button size="sm" onClick={() => setActive("")}>
                  {text("Deactivate", "取消激活")}
                </Button>
              ) : (
                <Button size="sm" onClick={() => setActive(a.name)}>
                  {text("Activate", "激活")}
                </Button>
              )}
              <Button
                size="sm"
                onClick={() => {
                  setRenaming(a.name);
                  setRenameValue(a.name);
                }}
              >
                {text("Rename", "改名")}
              </Button>
              <Button size="sm" onClick={() => remove(a.name)}>
                {text("Remove", "删除")}
              </Button>
            </>
          )}
        </div>
      ))}

      {accounts.length === 0 && !notReady && !pending && (
        <div style={{ fontSize: "0.8rem", opacity: 0.6, marginBottom: "0.4rem" }}>
          {text("No accounts yet.", "还没有账号。")}
        </div>
      )}

      {/* Add account: code-paste (claude-code) vs the shared login flow. */}
      {codePaste ? (
        !pending ? (
          <div className={styles.detailRow}>
            <Input
              className="flex-1 font-mono"
              placeholder={text("optional label — leave blank to auto-name", "可选标签 — 留空自动命名")}
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              disabled={busy}
            />
            <Button size="sm" onClick={startAdd} disabled={busy}>
              {busy ? text("Opening…", "打开中…") : text("Add account", "添加账号")}
            </Button>
          </div>
        ) : (
          <div className={styles.detailRow} style={{ flexWrap: "wrap", gap: "0.4rem" }}>
            <Input
              className="flex-1 font-mono"
              placeholder={text("paste the code from the login page", "粘贴登录页给出的 code")}
              value={code}
              onChange={(e) => setCode(e.target.value)}
              disabled={busy}
            />
            <Button size="sm" onClick={submitCode} disabled={busy || !code.trim()}>
              {busy ? text("Finishing…", "完成中…") : text("Finish", "完成")}
            </Button>
            <Button size="sm" onClick={cancelAdd} disabled={busy}>
              {text("Cancel", "取消")}
            </Button>
            {pending.url && (
              <a
                href={pending.url}
                target="_blank"
                rel="noopener noreferrer"
                style={{ fontSize: "0.75rem", opacity: 0.7, width: "100%" }}
              >
                {text("Login page didn't open? Click here.", "登录页没打开？点这里。")}
              </a>
            )}
          </div>
        )
      ) : (
        <div>
          <div className={styles.detailRow}>
            <Input
              className="flex-1 font-mono"
              placeholder={text(
                "account name (e.g. work) — blank fills the default account",
                "账号名（如 work）— 留空填入默认账号",
              )}
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
            />
          </div>
          <ProviderLogin
            provider={provider}
            profileId={newName.trim() || "default"}
            bare
            onChanged={() => {
              setNewName("");
              load();
            }}
          />
        </div>
      )}

      {msg && (
        <div style={{ fontSize: "0.75rem", opacity: 0.75, marginTop: "0.3rem" }}>{msg}</div>
      )}
      <div style={{ fontSize: "0.72rem", opacity: 0.55, marginTop: "0.4rem", lineHeight: 1.5 }}>
        {codePaste
          ? text(
              "Add an account (browser login; the name is optional, it's auto-set to the account email). Activate the one the framework runs on — or deactivate to leave none active. Rename or remove any. Independent of the terminal Claude Code login you chat on.",
              "添加账号（浏览器登录；名字可选，默认用账号 email）。激活框架要用的那个 —— 或取消激活让它没有激活账号。可改名、删除任意账号。与你聊天用的终端 Claude Code 登录无关。",
            )
          : text(
              "Each account is a separate sign-in. Name it, then sign in to add it. Activate the one this provider runs on — or deactivate to fall back to the default account. Rename or remove any.",
              "每个账号是一次独立登录。先起名，再登录即可添加。激活这个 Provider 要用的那个 —— 或取消激活回退到默认账号。可改名、删除任意账号。",
            )}
      </div>
    </div>
  );
}
