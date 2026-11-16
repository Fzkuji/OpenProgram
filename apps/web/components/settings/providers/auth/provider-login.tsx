"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ExternalLink } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Field, FieldContent, FieldDescription, FieldError, FieldGroup, FieldLabel, FieldTitle } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Spinner } from "@/components/ui/spinner";
import { SettingsHeading } from "@/components/settings/settings-ui";
import { useTranslation } from "@/lib/i18n";

import type { LoginMethod, Provider } from "../types";

/** A provider's ways to connect as a choice list (radio cards): the first is
 *  the recommended one and says so, each has its one-line description, an
 *  import names the file it reads. The order and words are the login-method
 *  registry's (openprogram/models/auth/login/login_method_registry.py). */
export function MethodChoices({
  methods, value, onValueChange, disabled = false,
}: {
  methods: LoginMethod[];
  value: string;
  onValueChange: (id: string) => void;
  disabled?: boolean;
}) {
  const { text } = useTranslation();
  return (
    <RadioGroup value={value} onValueChange={onValueChange} disabled={disabled}
      aria-label={text("Ways to connect", "接入方式")} className="gap-2">
      {methods.map((m, i) => (
        <FieldLabel key={m.id} htmlFor={`method-${m.id}`} data-method={m.id}
          data-recommended={i === 0 && methods.length > 1 ? "" : undefined}
          // The picked way reads by its border; the radio's own focus ring
          // marks the keyboard, so the card does not ring as well.
          className="has-data-checked:border-primary dark:has-data-checked:border-primary has-data-checked:has-[>[data-slot=field]]:has-[:focus-visible]:ring-0">
          <Field orientation="horizontal">
            <RadioGroupItem value={m.id} id={`method-${m.id}`} aria-label={text(m.label, m.label_zh)} />
            <FieldContent>
              <FieldTitle>
                {text(m.label, m.label_zh)}
                {i === 0 && methods.length > 1 ? <Badge variant="secondary">{text("Recommended", "推荐")}</Badge> : null}
              </FieldTitle>
              <FieldDescription className="text-text-secondary">{text(m.description, m.description_zh)}</FieldDescription>
              {m.path ? <FieldDescription className="font-mono text-fs-2xs">{m.path}</FieldDescription> : null}
            </FieldContent>
          </Field>
        </FieldLabel>
      ))}
    </RadioGroup>
  );
}

/** Generic native-login panel for any provider with a non-key login method
 *  (OAuth / device-code / import-from-CLI / a pasted token). Drives the
 *  worker endpoints /api/providers/{id}/login/{start,poll,submit,cancel}:
 *  start kicks off the flow, then we poll for events (open a URL, show a
 *  device code, progress, or a prompt we must answer) until done. Signing
 *  in again with an account already in the list renews its row.
 *
 *  With `method` the flow starts at once (the add dialog chose it); without
 *  it the panel offers the provider's ways as MethodChoices with Continue.
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
  method: startWith,
  bare = false,
}: {
  provider: Provider;
  onChanged?: () => void;
  /** Optional display label for the new credential. */
  accountLabel?: string;
  /** Start this way at once instead of offering the choice. */
  method?: string;
  /** No "Sign in" heading: the dialog around it has its own title. */
  bare?: boolean;
}) {
  const { text } = useTranslation();
  const [starting, setStarting] = useState("");
  const methods = provider.login_methods ?? [];
  const [chosen, setChosen] = useState(methods[0]?.id ?? "");

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
    setStarting("");
    setValue("");
    setMsg(message);
    submittedRef.current = null;
  }, [stop]);

  async function start(method: string) {
    const attempt = ++attemptRef.current;
    methodRef.current = method;
    setStarting(method);
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
        setStarting("");
        return;
      }
      sid = d.session;
      setStarting("");
      setSession(sid);
      sessionRef.current = sid;
    } catch {
      if (attempt !== attemptRef.current) return;
      setFailed(true);
      setMsg(text("Could not start the login.", "启动登录失败。"));
      setBusy(false);
      setStarting("");
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

  // The dialog chose the way: start it once, when the panel appears.
  const started = useRef(false);
  useEffect(() => {
    if (!startWith || started.current) return;
    started.current = true;
    void start(startWith);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once per mount
  }, [startWith]);

  const body = (
    <FieldGroup className="gap-3">
      {!session && !startWith ? (
        <>
          <MethodChoices methods={methods} value={chosen} onValueChange={setChosen} disabled={busy} />
          <Button size="sm" className="self-end" disabled={!chosen || busy} onClick={() => void start(chosen)}>
            {starting ? <Spinner data-icon="inline-start" /> : null}{text("Continue", "继续")}
          </Button>
        </>
      ) : session || busy ? (
        <Field>
          {starting ? (
            <FieldDescription className="flex items-center gap-2"><Spinner />{text("Starting the sign-in…", "正在开始登录…")}</FieldDescription>
          ) : null}
          {lines.map((l, i) => <FieldDescription key={i}>{l}</FieldDescription>)}
          {authUrl ? (
            <Button asChild variant="link" size="sm" className="self-start px-0">
              <a href={authUrl} target="_blank" rel="noopener noreferrer">
                {text("Open sign-in page", "打开登录页面")}<ExternalLink data-icon="inline-end" aria-hidden="true" />
              </a>
            </Button>
          ) : null}
          {prompt ? (
            <Field orientation="horizontal">
              <Input
                className="flex-1 font-mono"
                type={prompt.secret ? "password" : "text"}
                aria-label={prompt.message}
                placeholder={prompt.message}
                value={value}
                onChange={(e) => setValue(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter" && value.trim()) void submit(); }}
              />
              <Button size="sm" onClick={submit} disabled={!value.trim()}>
                {text("Submit", "提交")}
              </Button>
            </Field>
          ) : null}
          {session ? (
            <Button variant="ghost" size="sm" className="self-start" onClick={cancel}>
              {text("Cancel", "取消")}
            </Button>
          ) : null}
        </Field>
      ) : null}
      {failed && methodRef.current && !busy ? (
        <Button variant="elevated" size="sm" className="self-start" onClick={() => start(methodRef.current)}>
          {text("Try again", "重试")}
        </Button>
      ) : null}
      {msg ? (
        failed ? <FieldError className="text-fs-sm [overflow-wrap:anywhere]">{msg}</FieldError>
          : <FieldDescription role="status" className="[overflow-wrap:anywhere]">{msg}</FieldDescription>
      ) : null}
    </FieldGroup>
  );

  if (bare) return body;
  return (
    <div className="flex flex-col gap-3">
      <SettingsHeading title={text("Sign in", "登录")} />
      {body}
    </div>
  );
}
