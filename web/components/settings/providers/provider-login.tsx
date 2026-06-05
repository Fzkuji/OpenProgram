"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useTranslation } from "@/lib/i18n";

import styles from "../settings-page.module.css";
import type { Provider } from "./types";

/** Generic native-login panel for any provider with a non-key login method
 *  (OAuth / device-code / import-from-CLI). Drives the unified worker endpoints
 *  /api/providers/{id}/login/{start,poll,submit,cancel}: start kicks off the
 *  flow, then we poll for events (open a URL, show a device code, progress, or
 *  a prompt we must answer) until done, then refresh status. Same flow the CLI
 *  runs; claude-code keeps its own ClaudeAccounts panel, plain api_key keeps the
 *  ApiKey field.
 *
 *  Polling is a SELF-RESCHEDULING setTimeout (never setInterval) so only one
 *  poll is ever in flight — no overlapping reads, no cursor rewind, no late
 *  404 clobbering a just-succeeded login (finishedRef guards that too). */

const JSON_HEADERS = { "Content-Type": "application/json" };

interface Prompt {
  message: string;
  secret: boolean;
}

export function ProviderLogin({
  provider,
  onChanged,
}: {
  provider: Provider;
  onChanged?: () => void;
}) {
  const { text } = useTranslation();
  const methods = provider.login_methods ?? [];

  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [lines, setLines] = useState<string[]>([]);
  const [session, setSession] = useState<string | null>(null);
  const [prompt, setPrompt] = useState<Prompt | null>(null);
  const [value, setValue] = useState("");

  const cursorRef = useRef(0);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const finishedRef = useRef(false);            // terminal state reached
  const submittedRef = useRef<string | null>(null); // prompt message just answered

  const stop = useCallback(() => {
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  // Clear the poll timer if the panel unmounts mid-flow.
  useEffect(() => () => stop(), [stop]);

  const clearLocal = useCallback((message: string) => {
    stop();
    setSession(null);
    setPrompt(null);
    setBusy(false);
    setValue("");
    setMsg(message);
    submittedRef.current = null;
  }, [stop]);

  async function start(method: string) {
    setBusy(true);
    setMsg("");
    setLines([]);
    setPrompt(null);
    setValue("");
    cursorRef.current = 0;
    finishedRef.current = false;
    submittedRef.current = null;
    let sid = "";
    try {
      const r = await fetch(`/api/providers/${provider.id}/login/start`, {
        method: "POST",
        headers: JSON_HEADERS,
        body: JSON.stringify({ method }),
      });
      const d = await r.json();
      if (d.error || !d.session) {
        setMsg(d.error || text("Could not start the login.", "启动登录失败。"));
        setBusy(false);
        return;
      }
      sid = d.session;
      setSession(sid);
    } catch {
      setMsg(text("Could not start the login.", "启动登录失败。"));
      setBusy(false);
      return;
    }

    // Self-rescheduling poll: schedule the next tick only after this one
    // resolves, so exactly one request is ever in flight.
    const tick = async () => {
      try {
        const r = await fetch(
          `/api/providers/${provider.id}/login/poll?session=${sid}&cursor=${cursorRef.current}`,
        );
        if (r.status === 404) {
          // Already finished? a late 404 is harmless — just stop. Only a 404
          // BEFORE completion means the session really vanished.
          if (finishedRef.current) { stop(); return; }
          clearLocal(text("Login session expired — try again.", "登录会话已过期，请重试。"));
          return;
        }
        const d = await r.json();
        cursorRef.current = Math.max(cursorRef.current, d.cursor ?? 0);
        for (const ev of d.events ?? []) {
          if (ev.type === "open_url") {
            window.open(ev.url, "_blank", "noopener");
            setLines((l) => [...l, text(`Opened ${ev.url}`, `已打开 ${ev.url}`)]);
          } else if (ev.type === "progress") {
            setLines((l) => [...l, String(ev.message ?? "")]);
          } else if (ev.type === "code") {
            setLines((l) => [...l, `${ev.user_code}  —  ${ev.verification_uri}`]);
          }
        }
        // Show the prompt, but suppress the one we just submitted until the
        // backend confirms it's consumed (waiting flips false) — avoids a
        // flicker where the answered input briefly reappears empty.
        if (d.waiting && d.prompt && d.prompt.message !== submittedRef.current) {
          setPrompt(d.prompt);
        } else if (!d.waiting) {
          setPrompt(null);
          submittedRef.current = null;
        }
        if (d.done) {
          finishedRef.current = true;
          stop();
          setSession(null);
          setPrompt(null);
          setBusy(false);
          setMsg(d.ok ? text("Signed in.", "登录成功。") : (d.error || text("Login failed.", "登录失败。")));
          if (d.ok) onChanged?.();
          return;
        }
        timerRef.current = setTimeout(tick, 1000);
      } catch {
        timerRef.current = setTimeout(tick, 1000); // transient — keep polling
      }
    };
    timerRef.current = setTimeout(tick, 600);
  }

  async function submit() {
    if (!session || !prompt) return;
    const v = value;
    submittedRef.current = prompt.message;
    setValue("");
    setPrompt(null);
    try {
      await fetch(`/api/providers/${provider.id}/login/submit`, {
        method: "POST",
        headers: JSON_HEADERS,
        body: JSON.stringify({ session, value: v }),
      });
    } catch {
      setMsg(text("Could not submit.", "提交失败。"));
    }
  }

  function cancel() {
    const sid = session;
    if (sid) {
      void fetch(`/api/providers/${provider.id}/login/cancel`, {
        method: "POST",
        headers: JSON_HEADERS,
        body: JSON.stringify({ session: sid }),
      }).catch(() => {});
    }
    finishedRef.current = true;
    clearLocal("");
  }

  return (
    <div className={styles.detailSection}>
      <div className={styles.detailSectionTitle}>
        <span>{text("Sign in", "登录")}</span>
      </div>

      {!session ? (
        <div className={styles.detailRow} style={{ flexWrap: "wrap", gap: "0.4rem" }}>
          {methods.map((m) => (
            <Button key={m.id} size="sm" onClick={() => start(m.id)} disabled={busy}>
              {busy ? text("Opening…", "打开中…") : m.label}
            </Button>
          ))}
        </div>
      ) : (
        <div>
          {lines.map((l, i) => (
            <div key={i} style={{ fontSize: "0.75rem", opacity: 0.75 }}>{l}</div>
          ))}
          {prompt && (
            <div className={styles.detailRow} style={{ gap: "0.4rem", marginTop: "0.3rem" }}>
              <Input
                className="flex-1 font-mono"
                type={prompt.secret ? "password" : "text"}
                placeholder={prompt.message}
                value={value}
                onChange={(e) => setValue(e.target.value)}
              />
              <Button size="sm" onClick={submit} disabled={!value.trim()}>
                {text("Submit", "提交")}
              </Button>
            </div>
          )}
          <Button size="sm" onClick={cancel} style={{ marginTop: "0.4rem" }}>
            {text("Cancel", "取消")}
          </Button>
        </div>
      )}

      {msg && (
        <div style={{ fontSize: "0.75rem", opacity: 0.75, marginTop: "0.3rem" }}>{msg}</div>
      )}
    </div>
  );
}
