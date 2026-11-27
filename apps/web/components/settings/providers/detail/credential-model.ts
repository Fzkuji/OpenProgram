/**
 * What a provider's credentials look like on screen
 * (docs/reference/design/providers/provider-management.html, "Credentials"):
 * a row's status badge and its one-line detail, the count a provider card
 * shows, the strategy names, and the requests that change the list. Pure
 * helpers and plain fetches, shared by the Credentials section, the add
 * dialog, the provider card and the model menu's key submenu.
 */
import type { KeyHealth, QuotaLimitWire } from "@/lib/settings/provider-status.generated";

import { fmtClock, fmtReset, fmtUsd } from "../status/model";
import type { CredentialList, CredentialRow, CredentialStrategy } from "../types";

type Text = (en: string, zh: string) => string;

export type BadgeTone = "success" | "warning" | "destructive" | "secondary";

export const credentialsUrl = (provider: string) => `/api/providers/${encodeURIComponent(provider)}/credentials`;
export const credentialUrl = (provider: string, id: string) => `${credentialsUrl(provider)}/${encodeURIComponent(id)}`;

/** The login-method registry's id for pasting an API key: a provider whose
 *  `auth.methods` has it takes keys; its other methods are sign-ins. */
export const KEY_METHOD = "api_key";

/** The event a tab receives when a provider's list changes (lib/net/ws/use-ws.ts). */
export const CREDENTIALS_CHANGED = "op:provider-credentials-changed";

export async function readCredentials(provider: string): Promise<CredentialList> {
  const r = await fetch(credentialsUrl(provider));
  if (!r.ok) throw new Error(String(r.status));
  return (await r.json()) as CredentialList;
}

/** A signed-in account (OAuth, device code, an imported login) rather than a pasted key. */
export function isSignIn(row: Pick<CredentialRow, "kind">): boolean {
  return row.kind !== "api_key" && row.kind !== "credential_process";
}

/** "····a1b2". */
export function maskedTail(tail: string): string {
  return tail ? `····${tail}` : "";
}

/** What names a credential, as a row of the list or of a card carries it. */
export interface Nameable {
  label?: string;
  kind: string;
  email?: string;
  identity?: { email?: string };
  env_var?: string | null;
  signin_name?: string;
}

/** The name a person sees for a credential, in their language: the name
 *  they gave it, else the account's email, else one from its kind
 *  ("API key", "Environment key", "Qwen sign-in"). The tail and the
 *  variable are not part of it: the row says them on its second line. */
export function credentialName(c: Nameable, text: Text): string {
  if (c.label) return c.label;
  const email = c.email || c.identity?.email;
  if (email) return email;
  if (c.env_var) return text("Environment key", "环境变量密钥");
  if (!isSignIn(c)) return text("API key", "API 密钥");
  const signin = c.signin_name || "";
  return signin ? text(`${signin} sign-in`, `${signin} 登录`) : text("Sign-in", "登录");
}

export function strategyLabel(strategy: CredentialStrategy | string, text: Text): string {
  switch (strategy) {
    case "evenly": return text("Evenly", "平均");
    case "least_used": return text("Least used", "最少使用");
    default: return text("In order", "按顺序");
  }
}

/** The status badge at a row's end: Ready, Back at 14:32, Out of quota,
 *  Invalid key, Sign in again, or Not in use for one set aside. A hold whose
 *  time has passed reads Ready: the row can be used again. */
export function credentialBadge(row: CredentialRow, now: number, text: Text): { tone: BadgeTone; label: string } {
  if (!row.enabled) return { tone: "secondary", label: text("Not in use", "暂不使用") };
  if (usableNow(row, now)) return { tone: "success", label: text("Ready", "可用") };
  switch (row.status) {
    case "cooling":
      return {
        tone: "warning",
        label: row.cooldown_until && row.cooldown_until > now
          ? text(`Back at ${fmtClock(row.cooldown_until)}`, `${fmtClock(row.cooldown_until)} 恢复`)
          : text("Rate limited", "限流中"),
      };
    case "quota": {
      // A window that resets says when; a spent balance or credit has no
      // reset to name (its hold is only how long the key rests).
      const windowed = (row.limits ?? []).some((l) => (l.window_minutes ?? 0) > 0);
      const back = windowed && row.cooldown_until && row.cooldown_until > now ? fmtReset(row.cooldown_until, now) : "";
      return { tone: "destructive", label: back ? text(`Out of quota · ${back}`, `额度用完 · ${back}`) : text("Out of quota", "额度用完") };
    }
    case "invalid_key": return { tone: "destructive", label: text("Invalid key", "密钥无效") };
    case "needs_login": return { tone: "warning", label: text("Sign in again", "需要重新登录") };
    case "not_set_up": return { tone: "secondary", label: text("Provider not set up", "服务商未设置") };
    case "no_file": return { tone: "warning", label: text("Sign-in file not found", "找不到登录文件") };
    default: return { tone: "success", label: text("Ready", "可用") };
  }
}

/** The short word the model menu's key submenu shows after a credential
 *  (always muted: a row that cannot be used is already greyed out). */
export function credentialWord(row: CredentialRow, now: number, text: Text): string {
  if (!row.enabled) return text("not in use", "暂不使用");
  if (usableNow(row, now)) return row.key_tail ? maskedTail(row.key_tail) : row.identity.plan ?? "";
  switch (row.status) {
    case "cooling":
      return row.cooldown_until && row.cooldown_until > now
        ? text(`back ${fmtClock(row.cooldown_until)}`, `${fmtClock(row.cooldown_until)} 恢复`)
        : text("rate limited", "限流中");
    case "quota": return text("out of quota", "额度用完");
    case "invalid_key": return text("invalid", "无效");
    case "needs_login": return text("sign in again", "需重新登录");
    case "not_set_up": return text("not set up", "未设置");
    case "no_file": return text("file not found", "找不到文件");
    default: return row.key_tail ? maskedTail(row.key_tail) : row.identity.plan ?? "";
  }
}

/** When the list next changes by itself (a cooldown or quota hold runs
 *  out), in seconds; null when nothing is waiting. */
export function nextExpiry(rows: CredentialRow[], now: number): number | null {
  const ends = rows
    .filter((r) => r.enabled && (r.status === "cooling" || r.status === "quota") && !!r.cooldown_until && r.cooldown_until > now)
    .map((r) => r.cooldown_until as number);
  return ends.length ? Math.min(...ends) : null;
}

/** Whether a request could use the credential now. */
export function usableNow(row: CredentialRow, now: number): boolean {
  if (!row.enabled) return false;
  if (row.status === "ready") return true;
  return (row.status === "cooling" || row.status === "quota") && !!row.cooldown_until && row.cooldown_until <= now;
}

/** Why a credential stopped, from its last error ("Rate limited (429) at
 *  14:31", "Rejected by OpenAI (401)"); empty when it works. */
export function failureLine(row: CredentialRow, provider: string, text: Text, now: number = Date.now() / 1000): string {
  if (row.status === "not_set_up") {
    return text(`${provider} has no address here; add the provider again to use this key`, `${provider} 在这里没有接口地址；重新添加这个服务商后才能用这个密钥`);
  }
  if (row.status === "no_file") {
    return text("The file this sign-in reads is not there; sign in with that tool again", "这个登录读取的文件不在了；在原来的工具里重新登录");
  }
  if (row.status === "ready" || !row.last_error || usableNow(row, now)) return "";
  const code = row.last_error.code ?? "";
  const status = /\b(\d{3})\b/.exec(`${row.last_error.message ?? ""} ${code}`)?.[1];
  const at = row.last_error.at ? fmtClock(row.last_error.at > 1e12 ? row.last_error.at / 1000 : row.last_error.at) : "";
  switch (row.status) {
    case "cooling":
      return text(`Rate limited${status ? ` (${status})` : ""}${at ? ` at ${at}` : ""}`, `被限流${status ? `（${status}）` : ""}${at ? `，${at}` : ""}`);
    case "quota":
      return text(`Out of quota${at ? ` since ${at}` : ""}`, `额度用完${at ? `，${at} 起` : ""}`);
    case "invalid_key":
      return text(`Rejected by ${provider}${status ? ` (${status})` : ""}`, `被 ${provider} 拒绝${status ? `（${status}）` : ""}`);
    case "needs_login":
      return text("The sign-in expired", "登录已失效");
    default:
      return row.last_error.message ?? "";
  }
}

/** The quota windows a row draws as bars (5 hours, week), at most two. */
export function rowMeters(limits: QuotaLimitWire[] | undefined): QuotaLimitWire[] {
  return (limits ?? []).filter((l) => l.left_fraction != null && (l.unit === "percent" || (l.window_minutes ?? 0) > 1)).slice(0, 2);
}

/** "$3.12 today", "412 requests today"; empty before the first call. */
export function todayLine(row: CredentialRow, subscription: boolean, text: Text): string[] {
  const today = row.today;
  if (!today || today.calls === 0) return [];
  const calls = text(`${today.calls} ${today.calls === 1 ? "request" : "requests"}`, `${today.calls} 次请求`);
  if (subscription || today.cost_usd <= 0) return [text(`${calls} today`, `今天 ${calls}`)];
  return [text(`${fmtUsd(today.cost_usd)} today`, `今天 ${fmtUsd(today.cost_usd)}`), calls];
}

/** Whether a provider's credentials are "keys" (every one a pasted key, or
 *  the provider takes nothing else) rather than signed-in "accounts". */
export function countsKeys(keys: Pick<KeyHealth, "kind">[], takesKeys: boolean): boolean {
  return keys.length ? keys.every((k) => !isSignIn(k)) : takesKeys;
}

/** A provider card's count: "3 keys · 1 cooling · 1 invalid", "2 accounts",
 *  "1 key · ····a1b2", "1 key · $ANTHROPIC_API_KEY", "2 keys · 1 not in use",
 *  "0 keys". Every credential the provider's page lists is counted, the
 *  ones not in use included; the tones colour the failing parts. */
export function credentialCount(keys: KeyHealth[], takesKeys: boolean, now: number, text: Text): { label: string; tone?: "warning" | "danger" }[] {
  const n = keys.length;
  const noun = countsKeys(keys, takesKeys)
    ? text(n === 1 ? "1 key" : `${n} keys`, `${n} 个密钥`)
    : text(n === 1 ? "1 account" : `${n} accounts`, `${n} 个账号`);
  const parts: { label: string; tone?: "warning" | "danger" }[] = [{ label: noun }];
  const inUse = keys.filter((k) => k.enabled !== false);
  const count = (test: (k: KeyHealth) => boolean) => inUse.filter(test).length;
  const cooling = count((k) => k.status === "cooling" && !!k.cooldown_until && k.cooldown_until > now);
  const quota = count((k) => k.status === "quota" && (!k.cooldown_until || k.cooldown_until > now));
  const invalid = count((k) => k.status === "invalid_key");
  const signedOut = count((k) => k.status === "needs_login");
  if (cooling) parts.push({ label: text(`${cooling} cooling`, `${cooling} 个冷却中`), tone: "warning" });
  if (quota) parts.push({ label: text(`${quota} out of quota`, `${quota} 个额度用完`), tone: "danger" });
  if (invalid) parts.push({ label: text(`${invalid} invalid`, `${invalid} 个无效`), tone: "danger" });
  if (signedOut) parts.push({ label: text(`${signedOut} signed out`, `${signedOut} 个需重新登录`), tone: "warning" });
  const off = n - inUse.length;
  if (off) parts.push({ label: text(`${off} not in use`, `${off} 个暂不使用`) });
  if (n === 1 && parts.length === 1) {
    // One credential: its name, else what tells it apart (its last four,
    // the variable it is read from), else its kind's name.
    const only = keys[0];
    const label = only.label || only.email
      || (only.key_tail ? maskedTail(only.key_tail) : only.env_var ? `$${only.env_var}` : credentialName(only, text));
    parts.push({ label });
  }
  return parts;
}

/** The credential a card's fix opens: the first that needs a new key or a sign-in. */
export function failingCredential(keys: KeyHealth[], action: "sign_in" | "replace_key" | "top_up"): string | null {
  const want = action === "sign_in" ? "needs_login" : action === "replace_key" ? "invalid_key" : "quota";
  return keys.find((k) => k.status === want)?.credential_id ?? null;
}
