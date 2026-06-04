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
 *  Backed by /api/providers/claude-code/accounts{,/add,/remove,/use}. */

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

export function ClaudeAccounts() {
  const { text } = useTranslation();
  const [state, setState] = useState<AccountsState | null>(null);
  const [newName, setNewName] = useState("");
  const [adding, setAdding] = useState(false);
  const [msg, setMsg] = useState("");

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

  async function add() {
    const name = newName.trim();
    if (!name) return;
    setAdding(true);
    setMsg(text(
      "A browser window is opening — sign in with the account to add…",
      "正在打开浏览器 — 用你要添加的账号登录…",
    ));
    await fetch("/api/providers/claude-code/accounts/add", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    // Poll until the account shows up (login finished) or we give up.
    for (let i = 0; i < 60; i++) {
      await new Promise((r) => setTimeout(r, 2000));
      try {
        const rr = await fetch("/api/providers/claude-code/accounts");
        const s = (await rr.json()) as AccountsState;
        if ((s.accounts || []).some((a) => a.name === name)) {
          setState(s);
          setNewName("");
          setMsg(text("Added.", "已添加。"));
          setAdding(false);
          return;
        }
      } catch {
        /* keep polling */
      }
    }
    setMsg(text(
      "Timed out waiting for the login. If you finished it, the account "
        + "will appear on the next refresh.",
      "等待登录超时。如果你已经登录完成，刷新后账号就会出现。",
    ));
    setAdding(false);
    load();
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
            {a.name === state.active ? "→ " : "  "}
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

      {accounts.length === 0 && !notReady && (
        <div style={{ fontSize: "0.8rem", opacity: 0.6, marginBottom: "0.4rem" }}>
          {text("No accounts yet.", "还没有账号。")}
        </div>
      )}

      <div className={styles.detailRow}>
        <Input
          className="flex-1 font-mono"
          placeholder={text("new account name, e.g. experiment", "新账号名，如 experiment")}
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
          disabled={adding || notReady}
        />
        <Button size="sm" onClick={add} disabled={adding || notReady || !newName.trim()}>
          {adding ? text("Adding…", "添加中…") : text("Add account", "添加账号")}
        </Button>
      </div>

      {msg && (
        <div style={{ fontSize: "0.75rem", opacity: 0.7, marginTop: "0.3rem" }}>{msg}</div>
      )}
      <div style={{ fontSize: "0.72rem", opacity: 0.55, marginTop: "0.4rem", lineHeight: 1.5 }}>
        {text(
          "Add a Claude account (opens a browser login), activate the one OpenProgram should run on, or remove one. Independent of the terminal Claude Code login you chat on.",
          "添加 Claude 账号（弹浏览器登录）、激活 OpenProgram 要用的那个、或删除。与你聊天用的终端 Claude Code 登录无关。",
        )}
      </div>
    </div>
  );
}
