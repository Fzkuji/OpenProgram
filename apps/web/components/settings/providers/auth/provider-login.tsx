"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useTranslation } from "@/lib/i18n";

import styles from "../provider-pane.module.css";
import type { Provider } from "../types";

/** Generic native-login panel for any provider with a non-key login method
 *  (OAuth / device-code / import-from-CLI). Drives the unified worker endpoints
 *  /api/providers/{id}/login/{start,poll,submit,cancel}: start kicks off the
 *  flow, then we poll for events (open a URL, show a device code, progress, or
 *  a prompt we must answer) until done, then refresh status. Same flow the CLI
 *  runs, and the same flow <AccountManager> embeds (with accountLabel) as its
 *  "add account" step for login providers.
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
  accountLabel,
  bare = false,
  leadingInput,
}: {
  provider: Provider;
  onChanged?: () => void;
  /** Optional display label. The worker independently allocates the stable
   *  account id, so two sign-ins cannot overwrite one another. */
  accountLabel?: string;
  /** Drop the bordered "Sign in" section wrapper — used when embedded as the
   *  add-account step inside <AccountManager>, which supplies its own frame. */
  bare?: boolean;
  /** Rendered to the LEFT of the sign-in button(s) on the same row (e.g. a name
   *  input), so login add lines up with the api-key / code-paste add rows
   *  (button on the right). */
  leadingInput?: React.ReactNode;
}) {
  const { text } = useTranslation();
  const methods = provider.login_methods ?? [];

  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [lines, setLines] = useState<string[]>([]);
  const [session, setSession] = useState<string | null>(null);
  const [prompt, setPrompt] = useState<Prompt | null>(null);
  const [value, setValue] = useState("");

  const [authUrl, setAuthUrl] = useState("");
  const [failed, setFailed] = useState(false);
  const attemptRef = useRef(0);
  const sessionRef = useRef<string | null>(null);
  const methodRef = useRef("");
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
  useEffect(() => () => {
    ++attemptRef.current;
    stop();
    if (sessionRef.current) void fetch(`/api/providers/${provider.id}/login/cancel`, {
      method: "POST", headers: JSON_HEADERS,
      body: JSON.stringify({ session: sessionRef.current }),
    }).catch(() => {});
  }, [stop, provider.id]);

  const clearLocal = useCallback((message: string) => {
    stop();
    setSession(null);
    sessionRef.current = null;
    setAuthUrl("");
    setPrompt(null);
    setBusy(false);
    setValue("");
    setMsg(message);
    submittedRef.current = null;
  }, [stop]);

  async function start(method: string) {
    const attempt = ++attemptRef.current;
    methodRef.current = method;
    stop();
    setAuthUrl("");
    setFailed(false);
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
        body: JSON.stringify(accountLabel ? { method, label: accountLabel } : { method }),
      });
      const d = await r.json();
      if (attempt !== attemptRef.current) {
        if (d.session) void fetch(`/api/providers/${provider.id}/login/cancel`, { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ session: d.session }) });
        return;
      }
      if (!r.ok || d.error || !d.session) {
        setFailed(true);
        setMsg(d.error || text("Could not start the login.", "启动登录失败。"));
        setBusy(false);
        return;
      }
      sid = d.session;
      setSession(sid);
      sessionRef.current = sid;
    } catch {
      if (attempt !== attemptRef.current) return;
      setFailed(true);
      setMsg(text("Could not start the login.", "启动登录失败。"));
      setBusy(false);
      return;
    }

    // Self-rescheduling poll: schedule the next tick only after this one
    // resolves, so exactly one request is ever in flight.
    let failures = 0;
    const tick = async () => {
      if (attempt !== attemptRef.current || finishedRef.current) return;
      try {
        const r = await fetch(
          `/api/providers/${provider.id}/login/poll?session=${sid}&cursor=${cursorRef.current}`,
          { signal: AbortSignal.timeout(15000) },
        );
        if (attempt !== attemptRef.current || finishedRef.current) return;
        if (r.status === 404) {
          // Already finished? a late 404 is harmless — just stop. Only a 404
          // BEFORE completion means the session really vanished.
          if (finishedRef.current) { stop(); return; }
          clearLocal(text("Login session expired — try again.", "登录会话已过期，请重试。"));
          return;
        }
        if (!r.ok) throw new Error("Login status unavailable");
        const d = await r.json();
        if (attempt !== attemptRef.current || finishedRef.current) return;
        failures = 0;
        setMsg("");
        cursorRef.current = Math.max(cursorRef.current, d.cursor ?? 0);
        for (const ev of d.events ?? []) {
          if (ev.type === "open_url") {
            window.open(ev.url, "_blank", "noopener");
            setAuthUrl(ev.url);
            setLines([text("Continue in your browser, then return here.", "请在浏览器完成授权，然后返回这里。")]);
          } else if (ev.type === "progress") {
            const progress = String(ev.message ?? "");
            setLines([progress === "Completing sign-in…" ? text(progress, "正在完成登录…") : progress]);
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
          sessionRef.current = null;
          setAuthUrl("");
          setPrompt(null);
          setValue("");
          setFailed(!d.ok);
          setBusy(false);
          const errors: Record<string, string> = {
            forbidden: text("OpenAI refused token exchange (403). Check the app's network/proxy and account access. The status alone does not identify the cause.", "OpenAI 拒绝兑换登录凭据（403）。请检查 App 使用的网络、代理及账号访问权限；仅凭 403 无法确定具体原因。"),
            invalid_grant: text("Authorization expired or was already used. Start a new sign-in.", "授权码已过期或已使用，请重新登录。"),
            access_denied: text("OpenAI denied authorization. Check account or workspace access.", "OpenAI 拒绝授权，请检查账号或工作区访问权限。"),
            unsupported_country_region_territory: text("OpenAI does not support sign-in from this region.", "OpenAI 不支持从当前地区登录。"),
            rate_limited: text("Too many attempts. Wait before retrying.", "登录请求过多，请稍后重试。"),
            unavailable: text("OpenAI authentication is temporarily unavailable.", "OpenAI 登录服务暂时不可用，请稍后重试。"),
          };
          setMsg(d.ok ? text("Signed in.", "登录成功。") : (errors[d.error_code] || d.error || text("Login failed.", "登录失败。")));
          if (d.ok) onChanged?.();
          return;
        }
        timerRef.current = setTimeout(tick, 1000);
      } catch {
        if (attempt !== attemptRef.current || finishedRef.current) return;
        failures += 1;
        setMsg(text(`Connection interrupted. Retrying (${failures})…`, `连接中断，正在重试（${failures}）…`));
        if (failures >= 10) {
          cancel();
          setFailed(true);
          setMsg(text("Could not reconnect. Start a new sign-in.", "无法恢复连接，请重新登录。"));
          return;
        }
        timerRef.current = setTimeout(tick, Math.min(5000, failures * 1000));
      }
    };
    timerRef.current = setTimeout(tick, 600);
  }

  async function submit() {
    if (!session || !prompt) return;
    const v = value;
    const originalPrompt = prompt;
    const attempt = attemptRef.current;
    submittedRef.current = prompt.message;
    setValue("");
    setPrompt(null);
    try {
      const response = await fetch(`/api/providers/${provider.id}/login/submit`, {
        method: "POST",
        headers: JSON_HEADERS,
        body: JSON.stringify({ session, value: v }),
      });
      if (!response.ok) throw new Error("Submit failed");
    } catch {
      if (attempt !== attemptRef.current) return;
      submittedRef.current = null;
      setPrompt(originalPrompt);
      setValue(v);
      setMsg(text("Could not submit.", "提交失败。"));
    }
  }

  function cancel() {
    ++attemptRef.current;
    const sid = sessionRef.current;
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

  const body = (
    <>
      {!session ? (
        <div className={`${styles.detailRow} flex gap-[10px] items-center`} style={{ flexWrap: "wrap", gap: "0.4rem" }}>
          {leadingInput}
          <div style={{ display: "flex", gap: "0.4rem", marginLeft: "auto", flexWrap: "wrap" }}>
            {methods.map((m) => (
              <Button key={m.id} onClick={() => start(m.id)} disabled={busy}>
                {busy ? text("Opening…", "打开中…") : m.label}
              </Button>
            ))}
          </div>
        </div>
      ) : (
        <div>
          {lines.map((l, i) => (
            <div key={i} style={{ fontSize: "var(--fs-2xs)", opacity: 0.75 }}>{l}</div>
          ))}
          {authUrl && <a href={authUrl} target="_blank" rel="noopener noreferrer" style={{ display:"inline-block", marginTop:8 }}>{text("Open sign-in page", "打开登录页面")}</a>}
          {prompt && (
            <div className={`${styles.detailRow} flex gap-[10px] items-center`} style={{ gap: "0.4rem", marginTop: "0.3rem" }}>
              <Input
                className="flex-1 font-mono"
                type={prompt.secret ? "password" : "text"}
                placeholder={prompt.message}
                value={value}
                onChange={(e) => setValue(e.target.value)}
              />
              <Button onClick={submit} disabled={!value.trim()}>
                {text("Submit", "提交")}
              </Button>
            </div>
          )}
          <Button onClick={cancel} style={{ marginTop: "0.4rem" }}>
            {text("Cancel", "取消")}
          </Button>
        </div>
      )}

      {failed && methodRef.current && !busy && <Button onClick={() => start(methodRef.current)} style={{ marginTop:8 }}>{text("Try signing in again", "重新登录")}</Button>}
      {msg && (
        <div role={failed ? "alert" : "status"} style={{ fontSize: "var(--fs-2xs)", marginTop: "0.5rem", overflowWrap:"anywhere" }}>{msg}</div>
      )}
    </>
  );

  if (bare) return body;
  return (
    <div className="flex flex-col gap-[10px]">
      <div className={styles.detailSectionTitle}>
        <span>{text("Sign in", "登录")}</span>
      </div>
      {body}
    </div>
  );
}
