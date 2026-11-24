"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle, CheckIcon, ExternalLink, XIcon } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Field, FieldDescription, FieldError, FieldLabel } from "@/components/ui/field";
import { SecretInput } from "@/components/ui/secret-input";
import { Spinner } from "@/components/ui/spinner";
import { useTranslation } from "@/lib/i18n";
import { errorDetailText } from "@/lib/net/error-text";
import type { KeyCheck } from "@/lib/settings/provider-status.generated";

import { credentialUrl, credentialsUrl } from "../detail/credential-model";
import { fmtAmount } from "../status/model";
import type { Stored } from "./data";

/** How long typing rests before a typed key is checked. */
export const CHECK_AFTER_TYPING_MS = 600;

/** Shortest typed value checked when typing rests: a key typed by hand is
 *  not sent to the provider prefix by prefix (paste, Enter and leaving the
 *  field still check any length). */
export const MIN_TYPED_KEY_LENGTH = 20;

/** Whether a typed value looks like a whole key, so a rest in typing checks it. */
export function looksComplete(value: string): boolean {
  const v = value.trim();
  return v.length >= MIN_TYPED_KEY_LENGTH && !/\s/.test(v);
}

/** With `checkOnly`, only a key the provider refused holds Add back: a key
 *  that could not be checked (`not_configured`, `not_available`,
 *  `unreachable`) is kept by the server (key_check: a key that is not
 *  rejected is kept), so it stays savable. */
export function refusedBy(check: KeyCheck | null): boolean {
  return check?.result === "rejected";
}

/** Results that keep the key and let setup go on (key_check.ENABLING_RESULTS). */
export const USABLE = new Set(["works", "no_credit", "unverified"]);

type Outcome =
  | { kind: "stored"; stored: Stored }
  | { kind: "checked"; check: KeyCheck }
  | { kind: "rejected"; check: KeyCheck | null; message: string }
  | { kind: "failed"; message: string };

/** A link out of the app (a console, a billing page) as the kit's link Button. */
export function ExternalLinkButton({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <Button asChild variant="link" size="xs" className="h-auto px-0">
      <a href={href} target="_blank" rel="noopener noreferrer">
        {children}<ExternalLink data-icon="inline-end" aria-hidden="true" />
      </a>
    </Button>
  );
}

/** "opencode.ai/auth" for "https://opencode.ai/auth". */
export function shortUrl(url: string): string {
  try {
    const u = new URL(url);
    return `${u.host}${u.pathname === "/" ? "" : u.pathname}`;
  } catch {
    return url;
  }
}

/** What a key check answered (design §4, step 2), as one status Badge and,
 *  when there is a way on, a link or Retry beside it. */
export function CheckLine({
  check, consoleUrl, host, onRetry, retrying = false, saved = true,
}: {
  check: KeyCheck;
  consoleUrl: string | null;
  host: string;
  onRetry?: () => void;
  retrying?: boolean;
  /** False while the key is only checked, not stored yet. */
  saved?: boolean;
}) {
  const { text } = useTranslation();
  const keysUrl = (check.fix_page === "keys" ? check.fix_url : null) ?? consoleUrl;
  const billingUrl = check.fix_page === "billing" ? check.fix_url : null;
  const row = (badge: React.ReactNode, after?: React.ReactNode, alert = false) => (
    <div className="flex min-w-0 flex-wrap items-center gap-2" role={alert ? "alert" : "status"} data-result={check.result}>
      {badge}
      {after}
    </div>
  );
  switch (check.result) {
    case "works": {
      const parts = [
        check.balance ? fmtAmount(check.balance.amount, check.balance.unit) : null,
        check.model_count ? text(`${check.model_count} models`, `${check.model_count} 个模型`) : null,
      ].filter(Boolean);
      return row(<Badge variant="success"><CheckIcon aria-hidden="true" />{[text("Key works", "密钥可用"), ...parts].join(" · ")}</Badge>);
    }
    case "unverified":
      return row(<Badge variant="secondary"><CheckIcon aria-hidden="true" />{saved ? text("Key saved · not verified", "密钥已保存 · 未能验证") : text("Key not verified", "未能验证密钥")}</Badge>);
    case "not_configured":
      return row(<Badge variant="secondary"><AlertTriangle aria-hidden="true" />{text("Provider not set up here · key not checked", "这里未设置该服务商 · 未检查密钥")}</Badge>);
    case "no_credit":
      return row(
        <Badge variant="warning"><AlertTriangle aria-hidden="true" />{text("Key works · no credit", "密钥可用 · 余额不足")}</Badge>,
        billingUrl ? <ExternalLinkButton href={billingUrl}>{text("Top up", "充值")}</ExternalLinkButton> : null,
      );
    case "not_available":
      return row(
        <Badge variant="warning"><AlertTriangle aria-hidden="true" />{text(`Not available in your region or plan (${check.http_status ?? 403})`, `你所在地区或套餐不可用（${check.http_status ?? 403}）`)}</Badge>,
        null, true,
      );
    case "unreachable":
      return row(
        <Badge variant="warning">{retrying ? <Spinner /> : <AlertTriangle aria-hidden="true" />}{host ? text(`Can't reach ${host}`, `无法连接 ${host}`) : text("Can't reach the provider", "无法连接服务商")}</Badge>,
        onRetry ? <Button variant="link" size="xs" className="h-auto px-0" onClick={onRetry} disabled={retrying}>{text("Retry", "重试")}</Button> : null,
        true,
      );
    case "rejected":
    default:
      return row(
        <Badge variant="destructive"><XIcon aria-hidden="true" />{text(`Key rejected (${check.http_status ?? 401})`, `密钥被拒绝（${check.http_status ?? 401}）`)}</Badge>,
        keysUrl ? <ExternalLinkButton href={keysUrl}>{text("Get a key", "获取密钥")}</ExternalLinkButton> : null,
        true,
      );
  }
}

/** Store a pasted key at the end of the provider's list (or in an existing
 *  key's place), checked first: a rejected key is not stored. */
export async function storeKey(
  providerId: string, key: string, opts: { label?: string; replace?: string } = {},
): Promise<{ ok: true; stored: Stored } | { ok: false; status: number; check: KeyCheck | null; error: string }> {
  const url = opts.replace ? `${credentialUrl(providerId, opts.replace)}/replace` : credentialsUrl(providerId);
  const body = opts.replace ? { api_key: key } : { api_key: key, validate: true, ...(opts.label ? { label: opts.label } : {}) };
  const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const d = await r.json().catch((): StoreKeyReply => ({})) as StoreKeyReply;
  if (r.ok && d.ok) return { ok: true, stored: { name: String(d.credential?.id ?? ""), check: d.check ?? null } };
  return { ok: false, status: r.status, check: d.check ?? null, error: d.error == null ? "" : errorDetailText(d.error, "") };
}

/** What storing or replacing a key answers. */
interface StoreKeyReply {
  ok?: boolean;
  credential?: { id?: string } | null;
  check?: KeyCheck | null;
  error?: unknown;
}

/** What checking a key answers. */
interface CheckReply { check?: KeyCheck | null }

/** Check a pasted key with the provider without storing it (for `replace`,
 *  against that stored key's endpoint). The worker keeps the result for a
 *  few minutes, so storing the same key right after does not ask again. */
export async function checkKey(
  providerId: string, key: string, opts: { replace?: string } = {},
): Promise<KeyCheck | null> {
  const r = await fetch(`${credentialsUrl(providerId)}/check-key`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ api_key: key, ...(opts.replace ? { replace: opts.replace } : {}) }),
  });
  if (!r.ok) throw new Error(String(r.status));
  const d = await r.json().catch((): CheckReply => ({})) as CheckReply;
  return d.check ?? null;
}

/** Check a stored credential again; its new check, or null when the request failed. */
export async function recheck(providerId: string, id: string): Promise<KeyCheck | null> {
  const r = await fetch(`${credentialUrl(providerId, id)}/check`, { method: "POST" });
  const d = await r.json().catch((): CheckReply => ({})) as CheckReply;
  return d.check ?? null;
}

/** One masked key field, checked when pasted, on Enter, when it loses
 *  focus or when typing rests (CHECK_AFTER_TYPING_MS). By default a usable key is stored then and reports through
 *  `onStored` (Add provider's flow); with `checkOnly` it is only checked,
 *  and the caller stores it from its own button (the credential dialog,
 *  whose Cancel then leaves nothing behind). A rejected key is not stored
 *  and says so. The key itself never reaches the page again: the field
 *  only holds what was typed. */
export function KeyEntry({
  providerId, consoleUrl, host, name, replace, onStored, onValue, onRejected, onChecked, checkOnly = false, autoFocus = false,
}: {
  providerId: string;
  consoleUrl: string | null;
  host: string;
  /** Label for the stored key; blank names it after its last four. */
  name?: string;
  /** Put the key in this stored key's place instead of adding one. */
  replace?: string;
  onStored?: (stored: Stored) => void;
  /** What the field holds, as it is typed or pasted. */
  onValue?: (value: string) => void;
  /** With `checkOnly`: whether the provider refused what the field holds. */
  onRejected?: (rejected: boolean) => void;
  /** With `checkOnly`: whether a check has finished for what the field holds now. */
  onChecked?: (checked: boolean) => void;
  /** Check the key only; the caller stores it. */
  checkOnly?: boolean;
  autoFocus?: boolean;
}) {
  const { text } = useTranslation();
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const checked = useRef<string>("");
  // The last finished check: going back to that key shows its answer again
  // instead of leaving Add disabled with no line.
  const last = useRef<{ key: string; outcome: Outcome | null; refused: boolean } | null>(null);
  // The value the field holds now, so a check that ends after the key
  // changed does not count for the new one.
  const current = useRef<string>("");
  // In a ref, not the render's closure: a timer armed while a check ran
  // must see that it has ended.
  const busyRef = useRef(false);
  // A key asked for while another was being checked; checked next.
  const wanted = useRef<string>("");
  const typing = useRef<number | null>(null);
  const submitRef = useRef<(raw: string) => Promise<void>>(async () => {});
  useEffect(() => () => { if (typing.current != null) window.clearTimeout(typing.current); }, []);

  const submit = useCallback(async (raw: string) => {
    const key = raw.trim();
    if (!key || key === checked.current) return;
    if (busyRef.current) { wanted.current = key; return; }
    checked.current = key;
    busyRef.current = true;
    setBusy(true);
    setOutcome(null);
    try {
      if (checkOnly) {
        const check = await checkKey(providerId, key, { replace });
        if (current.current !== key) return;
        const refused = refusedBy(check);
        const next: Outcome | null = !check ? null : refused ? { kind: "rejected", check, message: "" } : { kind: "checked", check };
        last.current = { key, outcome: next, refused };
        setOutcome(next);
        onRejected?.(refused);
        onChecked?.(true);
        return;
      }
      const result = await storeKey(providerId, key, { label: name, replace });
      if (result.ok) {
        setOutcome({ kind: "stored", stored: result.stored });
        onStored?.(result.stored);
      } else {
        setOutcome({ kind: "rejected", check: result.check, message: result.error });
      }
    } catch {
      if (current.current !== key) return; // the field moved on: this failure is not its
      checked.current = "";
      last.current = null;
      setOutcome({ kind: "failed", message: checkOnly ? text("Couldn't check the key", "无法检查密钥") : text("Couldn't save the key", "无法保存密钥") });
      // A check that could not run does not hold the key back.
      if (checkOnly) onChecked?.(true);
    } finally {
      busyRef.current = false;
      setBusy(false);
      // The field changed while this check ran: check what it holds now.
      const next = wanted.current;
      wanted.current = "";
      if (next && next === current.current && next !== checked.current) void submitRef.current(next);
    }
  }, [checkOnly, name, onChecked, onRejected, onStored, providerId, replace, text]);
  submitRef.current = submit;

  const retry = useCallback(async () => {
    if (outcome?.kind === "checked") {
      setRetrying(true);
      checked.current = "";
      last.current = null;
      try { await submit(value); } finally { setRetrying(false); }
      return;
    }
    if (outcome?.kind !== "stored") return;
    setRetrying(true);
    try {
      const check = await recheck(providerId, outcome.stored.name);
      if (check) {
        const stored = { ...outcome.stored, check };
        setOutcome({ kind: "stored", stored });
        onStored?.(stored);
      }
    } finally {
      setRetrying(false);
    }
  }, [onStored, outcome, providerId, submit, value]);

  const kept = outcome?.kind === "stored";
  const rejected = outcome?.kind === "rejected";
  return (
    <Field data-invalid={rejected || undefined}>
      <FieldLabel htmlFor={`key-${providerId}`}>{text("API key", "API 密钥")}</FieldLabel>
      <SecretInput
        id={`key-${providerId}`}
        autoFocus={autoFocus}
        aria-invalid={rejected || undefined}
        placeholder={text("Paste an API key", "粘贴 API 密钥")}
        value={value}
        readOnly={kept}
        onChange={(e) => {
          const typed = e.target.value;
          const key = typed.trim();
          setValue(typed); onValue?.(key);
          current.current = key;
          if (typing.current != null) { window.clearTimeout(typing.current); typing.current = null; }
          if (outcome?.kind === "stored") return;
          if (checkOnly && last.current && key === last.current.key) {
            // Back to the key last checked: its answer holds again.
            checked.current = key;
            setOutcome(last.current.outcome);
            onRejected?.(last.current.refused);
            onChecked?.(true);
            return;
          }
          // Anything else is a new key: checked again when asked for.
          checked.current = "";
          setOutcome(null);
          onRejected?.(false);
          onChecked?.(false);
          if (checkOnly && looksComplete(key)) {
            // A typed key is checked once typing rests, not only on blur.
            typing.current = window.setTimeout(() => { typing.current = null; void submitRef.current(key); }, CHECK_AFTER_TYPING_MS);
          }
        }}
        onPaste={(e) => {
          const pasted = e.clipboardData?.getData("text") ?? "";
          onValue?.(pasted.trim());
          current.current = pasted.trim();
          if (pasted.trim()) setTimeout(() => { void submit(pasted); }, 0);
        }}
        onBlur={(e) => { void submit(e.currentTarget.value); }}
        onKeyDown={(e) => { if (e.key === "Enter") void submit(e.currentTarget.value); }}
      />
      {consoleUrl ? (
        <FieldDescription>
          {text("From ", "在 ")}<ExternalLinkButton href={consoleUrl}>{shortUrl(consoleUrl)}</ExternalLinkButton>{text("", " 创建")}
        </FieldDescription>
      ) : null}
      {busy ? (
        <FieldDescription role="status" className="flex items-center gap-2"><Spinner />{text("Checking the key…", "正在检查密钥…")}</FieldDescription>
      ) : outcome?.kind === "checked" ? (
        <CheckLine check={outcome.check} consoleUrl={consoleUrl} host={host} onRetry={retry} retrying={retrying} saved={false} />
      ) : outcome?.kind === "stored" && outcome.stored.check ? (
        <CheckLine check={outcome.stored.check} consoleUrl={consoleUrl} host={host} onRetry={retry} retrying={retrying} />
      ) : outcome?.kind === "stored" ? (
        <div role="status"><Badge variant="secondary"><CheckIcon aria-hidden="true" />{text("Key saved", "密钥已保存")}</Badge></div>
      ) : outcome?.kind === "rejected" ? (
        outcome.check ? <CheckLine check={outcome.check} consoleUrl={consoleUrl} host={host} />
          : <FieldError>{outcome.message || text("Key rejected", "密钥被拒绝")}</FieldError>
      ) : outcome?.kind === "failed" ? (
        <div className="flex items-center gap-2">
          <FieldError>{outcome.message}</FieldError>
          <Button variant="link" size="xs" className="h-auto px-0" onClick={() => { checked.current = ""; last.current = null; void submit(value); }}>{text("Retry", "重试")}</Button>
        </div>
      ) : null}
    </Field>
  );
}
