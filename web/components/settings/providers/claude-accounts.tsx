"use client";

import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useTranslation } from "@/lib/i18n";

import styles from "../settings-page.module.css";

/** claude-code only: manage the Claude accounts the provider can run on.
 *  Add (browser login), activate (which one OpenProgram uses), and remove
 *  accounts — all independent of the terminal `claude auth login` you chat
 *  on. The underlying proxy is never named here; users see "Claude account".
 *
 *  Add is a two-step OAuth: POST .../add returns a login URL (we open it);
 *  the page hands back a code the user pastes, which we POST to .../add/code.
 *  Backed by /api/providers/claude-code/accounts{,/add,/add/code,/remove,/use}. */

interface Account {
  name: string;
  email?: string;
}
interface AccountsState {
  installed: boolean;
  ready: boolean;
  active: string | null;
  accounts: Account[];
}
interface Pending {
  session: string;
  name: string;
  url: string;
}

export function ClaudeAccounts() {
  const { text } = useTranslation();
  const [state, setState] = useState<AccountsState | null>(null);
  const [newName, setNewName] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [pending, setPending] = useState<Pending | null>(null);
  const [code, setCode] = useState("");

  const load = useCallback(async () => {
    try {
      const r = await fetch("/api/providers/claude-code/accounts");
      setState((await r.json()) as AccountsState);
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function activate(name: string) {
    await fetch("/api/providers/claude-code/accounts/use", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    load();
  }

  async function remove(name: string) {
    await fetch("/api/providers/claude-code/accounts/remove", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    load();
  }

  async function startAdd() {
    const name = newName.trim();
    if (!name) return;
    setBusy(true);
    setMsg("");
    try {
      const r = await fetch("/api/providers/claude-code/accounts/add", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
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
      const r = await fetch("/api/providers/claude-code/accounts/add/code", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
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

  return (
    <div className={styles.detailSection}>
      <div className={styles.detailSectionTitle}>
        <span>{text("Claude accounts", "Claude 账号")}</span>
        <span className={styles.modelCountSummary}>
          {state.active
            ? text(`active: ${state.active}`, `激活：${state.active}`)
            : text("none active", "未激活")}
        </span>
      </div>

      {notReady && (
        <div style={{ fontSize: "0.8rem", opacity: 0.7, marginBottom: "0.5rem" }}>
          {text(
            "Backend not ready — follow the setup steps above first.",
            "后端未就绪 — 先按上方的安装步骤装好。",
          )}
        </div>
      )}

      {accounts.map((a) => (
        <div key={a.name} className={styles.detailRow} style={{ alignItems: "center" }}>
          <span style={{ flex: 1, fontFamily: "monospace" }}>
            {a.name === state.active ? "→ " : "  "}
            {a.name}
            {a.email ? <span style={{ opacity: 0.55 }}>{"  " + a.email}</span> : null}
          </span>
          {a.name !== state.active && (
            <Button size="sm" onClick={() => activate(a.name)}>
              {text("Activate", "激活")}
            </Button>
          )}
          <Button size="sm" onClick={() => remove(a.name)}>
            {text("Remove", "删除")}
          </Button>
        </div>
      ))}

      {accounts.length === 0 && !notReady && !pending && (
        <div style={{ fontSize: "0.8rem", opacity: 0.6, marginBottom: "0.4rem" }}>
          {text("No accounts yet.", "还没有账号。")}
        </div>
      )}

      {/* Step 1: name + start. Step 2 (pending): paste the code. */}
      {!pending ? (
        <div className={styles.detailRow}>
          <Input
            className="flex-1 font-mono"
            placeholder={text("new account name, e.g. experiment", "新账号名，如 experiment")}
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            disabled={busy || notReady}
          />
          <Button size="sm" onClick={startAdd} disabled={busy || notReady || !newName.trim()}>
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
      )}

      {msg && (
        <div style={{ fontSize: "0.75rem", opacity: 0.75, marginTop: "0.3rem" }}>{msg}</div>
      )}
      <div style={{ fontSize: "0.72rem", opacity: 0.55, marginTop: "0.4rem", lineHeight: 1.5 }}>
        {text(
          "Add a Claude account (a browser login — sign in, paste the code it gives you), activate the one OpenProgram should run on, or remove one. Independent of the terminal Claude Code login you chat on.",
          "添加 Claude 账号（浏览器登录 — 登录后把页面给的 code 粘回来）、激活 OpenProgram 要用的那个、或删除。与你聊天用的终端 Claude Code 登录无关。",
        )}
      </div>
    </div>
  );
}
