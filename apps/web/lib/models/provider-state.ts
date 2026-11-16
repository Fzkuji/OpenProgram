/**
 * What the chat says about a provider, from the picker state the Worker
 * sends with the enabled models (`GET /api/models/enabled` → `providers`)
 * and with every `provider_quota` frame.
 *
 * Everything the chat shows about providers lives inside the model picker
 * (its trigger and menu) and on the reply card of a spent quota; nothing
 * here draws, it only words and classifies:
 *
 *   - `stateWord`      — the word a provider row shows when it needs
 *                        action ("No credit", "Sign in", "Rate limited · 14:02").
 *   - `quotaTone`      — the bar colour rule: warning from 80 % used, danger
 *                        from 95 % or when spent.
 *   - `triggerMark`    — whether the picker trigger carries its small red
 *                        mark: only when the selected provider needs action
 *                        or its quota is 95 % used or spent.
 *   - `fixLink`        — where a row that needs action leads.
 *   - `quotaCardText`  — the title and reset line of the spent-quota card.
 *   - `headerRows`     — the limits a provider's submenu header lists.
 *
 * Design: docs/reference/design/providers/provider-management.html#chat.
 */
import type {
  FixWire,
  HealthState,
  PickerBalance,
  PickerProvider,
  PickerQuota,
  ProviderQuotaEvent,
  ProviderStatusPayload,
  QuotaLimitWire,
  Tone,
} from "@/lib/settings/provider-status.generated";

export type Text = (en: string, zh: string) => string;
export type WordTone = Tone | "muted";

/** States whose row leads to a fix instead of the provider's models. */
export const ACTION_STATES: ReadonlySet<HealthState> = new Set([
  "signed_out", "no_credit", "invalid_key", "not_configured", "not_running",
]);

export function needsAction(p: PickerProvider | undefined | null): boolean {
  return !!p && ACTION_STATES.has(p.state);
}

const pad = (n: number) => String(n).padStart(2, "0");
const DAYS_EN = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const DAYS_ZH = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];

/** A reset or clear time as people read it: "14:30" today, "Mon 14:30"
 *  within the week, "11/20" later. `ts` and `now` are Unix seconds. */
export function clockLabel(ts: number, text: Text, now: number = Date.now() / 1000): string {
  const at = new Date(ts * 1000);
  const today = new Date(now * 1000);
  const hm = `${pad(at.getHours())}:${pad(at.getMinutes())}`;
  if (at.toDateString() === today.toDateString()) return hm;
  if (ts - now < 7 * 86400 && ts > now) {
    const day = at.getDay();
    return `${text(DAYS_EN[day], DAYS_ZH[day])} ${hm}`;
  }
  return `${at.getMonth() + 1}/${at.getDate()}`;
}

/** The word a provider row shows instead of its model count, and its tone;
 *  null when the provider needs nothing. */
export function stateWord(
  p: PickerProvider | undefined | null,
  text: Text,
  now?: number,
): { label: string; tone: WordTone } | null {
  if (!p) return null;
  switch (p.state) {
    case "not_configured":
      return { label: text("Not set up", "未配置"), tone: "muted" };
    case "signed_out":
      return { label: text("Sign in", "需登录"), tone: "warning" };
    case "no_credit":
      return { label: text("No credit", "没有余额"), tone: "danger" };
    case "invalid_key":
      return { label: text("Invalid key", "密钥无效"), tone: "danger" };
    case "not_running":
      return { label: text("Not running", "未运行"), tone: "muted" };
    case "rate_limited":
      return {
        label: p.until
          ? `${text("Rate limited", "限流中")} · ${clockLabel(p.until, text, now)}`
          : text("Rate limited", "限流中"),
        tone: "warning",
      };
    default:
      return null;
  }
}

/** Whole percent left, 0–100. */
export function percentLeft(leftFraction: number): number {
  return Math.max(0, Math.min(100, Math.round(leftFraction * 100)));
}

/** The bar colour rule from the used share: warning from 80 % used,
 *  danger from 95 % or when the limit is spent. */
export function quotaTone(leftFraction: number | null | undefined, status?: string): Tone {
  if (status === "exhausted") return "danger";
  if (leftFraction == null) return "normal";
  const used = 1 - leftFraction;
  if (used >= 0.95) return "danger";
  if (used >= 0.8) return "warning";
  return "normal";
}

/** "$18.42", "¥0.00", "1,200 credits", "38,991.82 credits" — grouped as the
 *  Providers page writes the same amount. */
export function balanceLabel(b: Pick<PickerBalance, "amount" | "unit">): string {
  const unit = (b.unit || "").toLowerCase();
  const money = (n: number) => n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  if (unit === "usd") return `$${money(b.amount)}`;
  if (unit === "cny") return `¥${money(b.amount)}`;
  const amount = b.amount.toLocaleString("en-US", { maximumFractionDigits: 2 });
  return `${amount} ${unit || ""}`.trim();
}

/** "5h · 8% left · resets 14:30" — the ring's tooltip and the trigger's detail. */
export function quotaSummary(q: PickerQuota, text: Text, now?: number): string {
  const left = percentLeft(q.left_fraction);
  const parts = [`${q.label} · ${text(`${left}% left`, `剩 ${left}%`)}`];
  if (q.resets_at) parts.push(text(`resets ${clockLabel(q.resets_at, text, now)}`, `${clockLabel(q.resets_at, text, now)} 重置`));
  return parts.join(" · ");
}

/** Where a row that needs action leads: the provider's page in Settings
 *  (its sign-in, key or setup step), or — for a spent balance with a known
 *  billing page — that page, outside the app. */
export function fixLink(p: Pick<PickerProvider, "id" | "state"> & { fix: FixWire | null }): { href: string; external: boolean } {
  if (p.state === "no_credit" && p.fix?.action === "top_up" && p.fix.url) {
    return { href: p.fix.url, external: true };
  }
  // Nothing saved for it yet: straight into Add provider at its key or sign-in step.
  if (p.state === "not_configured") return { href: addProviderPage(p.id), external: false };
  return { href: providerPage(p.id), external: false };
}

/** The Providers page with Add provider open: at the list of choices, or at
 *  one provider's key or sign-in step. */
export function addProviderPage(id?: string): string {
  return `/settings/providers?add=${id ? encodeURIComponent(id) : "1"}`;
}

export function providerPage(id: string): string {
  return `/settings/providers/${encodeURIComponent(id)}`;
}

export const PROVIDERS_PAGE = "/settings/providers";

/** The picker trigger's mark, always in the danger tone and only when the
 *  selected model's provider cannot answer: it needs action (signed out, no
 *  credit, invalid key, not set up, not running) or its quota is nearly
 *  spent (95 % used) or spent. A quota that only warns — the 80 % rule
 *  the bars and the ring follow — leaves the trigger plain. */
export function triggerMark(
  p: PickerProvider | undefined | null,
  text: Text,
  now?: number,
): { tone: Tone; detail: string } | null {
  if (!p) return null;
  if (needsAction(p)) {
    const word = stateWord(p, text, now);
    return { tone: "danger", detail: word?.label ?? "" };
  }
  if (p.quota?.tone === "danger") {
    return { tone: "danger", detail: quotaSummary(p.quota, text, now) };
  }
  return null;
}

/** What a failed reply's `errorCause` carries (`errors.failure_cause`). */
export interface FailureCause {
  cause: "quota_exhausted" | "invalid_key" | "not_available" | "unreachable" | string;
  http_status?: number | null;
  provider?: string | null;
  limit?: string | null;
  resets_at?: number | null;
  account?: string | null;
  other_limits?: { label: string; left_fraction: number | null; resets_at: number | null }[];
}

export function isQuotaCause(cause: FailureCause | null | undefined): cause is FailureCause {
  return !!cause && cause.cause === "quota_exhausted";
}

/** A spent balance (402, or the provider's state says no credit) rather
 *  than a window that resets. A spent balance may still name its limit
 *  ("Balance"), so only a reset time rules it out. */
export function isNoCredit(cause: FailureCause, picker?: PickerProvider | null): boolean {
  if (cause.resets_at) return false;
  return cause.http_status === 402 || picker?.state === "no_credit";
}

/** Title and second line of the spent-quota reply card:
 *  "OpenAI Codex 5h limit reached" / "Resets 14:30 · Week 71% left". */
export function quotaCardText(
  cause: FailureCause,
  providerLabel: string,
  text: Text,
  opts: { now?: number; picker?: PickerProvider | null } = {},
): { title: string; detail: string } {
  const { now, picker } = opts;
  const noCredit = isNoCredit(cause, picker);
  const title = noCredit
    ? text(`${providerLabel} has no credit`, `${providerLabel} 没有余额了`)
    : cause.limit
      ? text(`${providerLabel} ${cause.limit} limit reached`, `${providerLabel} ${cause.limit} 额度已用完`)
      : text(`${providerLabel} quota used up`, `${providerLabel} 额度已用完`);
  const parts: string[] = [];
  if (cause.resets_at) {
    parts.push(text(`Resets ${clockLabel(cause.resets_at, text, now)}`, `${clockLabel(cause.resets_at, text, now)} 重置`));
  }
  for (const other of (cause.other_limits ?? []).slice(0, 2)) {
    if (other.left_fraction == null) continue;
    const left = percentLeft(other.left_fraction);
    parts.push(text(`${other.label} ${left}% left`, `${other.label} 剩 ${left}%`));
  }
  if (noCredit && picker?.balance) {
    parts.push(text(`Balance ${balanceLabel(picker.balance)}`, `余额 ${balanceLabel(picker.balance)}`));
  }
  return { title, detail: parts.join(" · ") };
}

/** One limit as a submenu header row: a window or key limit (% left, its
 *  reset, a bar) or an amount without a limit (balance, credits). */
export interface QuotaRow {
  key: string;
  label: string;
  /** Bar fill, 0–1 left; null for an amount without a limit. */
  left: number | null;
  /** Whole percent left; null for an amount. */
  pct: number | null;
  /** "14:30", "Thu 02:22"; null without a reset time or for an amount. */
  reset: string | null;
  /** "$18.42", "38,991.82 credits"; null for a window. */
  value: string | null;
  tone: Tone;
  stale: boolean;
}

const MONEY_KEYS = new Set(["balance", "credits"]);

function windowRow(
  key: string, label: string, leftFraction: number, resetsAt: number | null | undefined,
  tone: Tone, stale: boolean, text: Text, now?: number,
): QuotaRow {
  return {
    key, label, left: Math.max(0, Math.min(1, leftFraction)), pct: percentLeft(leftFraction),
    reset: resetsAt ? clockLabel(resetsAt, text, now) : null, value: null, tone, stale,
  };
}

const byLeft = (a: QuotaRow, b: QuotaRow) => (a.left ?? 2) - (b.left ?? 2);

/** At most this many window or key limits in the header, worst first; the
 *  amounts (one short line each, no bar) follow them and are never cut. */
const MAX_WINDOWS = 4;

function capped(rows: QuotaRow[]): QuotaRow[] {
  const windows = rows.filter((r) => r.left != null).sort(byLeft).slice(0, MAX_WINDOWS);
  return [...windows, ...rows.filter((r) => r.left == null)];
}

function limitRows(limits: QuotaLimitWire[], text: Text, now?: number): QuotaRow[] {
  const rows: QuotaRow[] = [];
  for (const limit of limits) {
    const base = limit.limit_key.split(":").pop() ?? limit.limit_key;
    if (limit.left_fraction != null && !MONEY_KEYS.has(base)) {
      rows.push(windowRow(
        limit.limit_key, limit.label, limit.left_fraction, limit.resets_at,
        quotaTone(limit.left_fraction, limit.status), limit.stale, text, now,
      ));
    } else if (limit.remaining != null) {
      rows.push({
        key: limit.limit_key, label: limit.label, left: null, pct: null, reset: null,
        value: balanceLabel({ amount: limit.remaining, unit: limit.unit }),
        tone: limit.status === "exhausted" ? "danger" : "normal", stale: limit.stale,
      });
    }
  }
  return rows;
}

/** The rows a status read gives the submenu header: windows worst first
 *  (at most four), then amounts. */
export function quotaRows(limits: QuotaLimitWire[], text: Text, now?: number): QuotaRow[] {
  return capped(limitRows(limits, text, now));
}

/** The submenu header's rows: every limit the status read carries, and
 *  always the limit the provider row's ring is drawn from and the balance
 *  of the picker state. The picker state arrives first and the status read
 *  seconds later (it also sums 30 days of spend), so until then the header
 *  shows what the ring and balance already know, never less. */
export function headerRows(
  limits: QuotaLimitWire[],
  picker: PickerProvider | undefined | null,
  text: Text,
  now?: number,
): QuotaRow[] {
  // Dedupe against every limit of the read before the cap, so a ring limit
  // the cap drops is not brought back from the picker state.
  const rows = limitRows(limits, text, now);
  const stale = !!picker?.stale;
  const ring = picker?.quota;
  if (ring && !rows.some((r) => r.key === ring.limit_key)) {
    rows.push(windowRow(ring.limit_key, ring.label, ring.left_fraction, ring.resets_at, ring.tone, stale, text, now));
  }
  const balance = picker?.balance;
  if (balance && !rows.some((r) => r.left == null)) {
    rows.push({
      key: "picker:balance", label: balance.label || text("Balance", "余额"), left: null, pct: null, reset: null,
      value: balanceLabel(balance), tone: "normal", stale,
    });
  }
  return capped(rows);
}

/** Query keys of the picker's two reads. Both start with "models-enabled",
 *  so every existing invalidation of the enabled models refetches them. */
export const PICKER_QUERY_KEY = ["models-enabled", "picker"] as const;
export const PICKER_LIMITS_QUERY_KEY = ["models-enabled", "provider-status"] as const;

type PickerData = { models: unknown[]; providers: Record<string, PickerProvider> };

interface QueryClientLike {
  setQueryData: (key: readonly unknown[], updater: (old: unknown) => unknown) => unknown;
}

/** Apply a `provider_quota` frame to the picker's cached reads, so the menu
 *  and its trigger follow a reply's headers without a request. */
export function applyProviderQuotaFrame(client: QueryClientLike | null | undefined, frame: ProviderQuotaEvent): void {
  if (!client || !frame?.provider) return;
  if (frame.picker) {
    const picker = frame.picker;
    client.setQueryData(PICKER_QUERY_KEY, (old) => {
      const data = old as PickerData | undefined;
      if (!data) return old;
      return { ...data, providers: { ...data.providers, [frame.provider]: picker } };
    });
  }
  client.setQueryData(PICKER_LIMITS_QUERY_KEY, (old) => {
    const data = old as ProviderStatusPayload | undefined;
    if (!data) return old;
    return {
      ...data,
      providers: data.providers.map((row) => {
        if (row.id !== frame.provider) return row;
        const account = row.quota?.account ?? "";
        if ((frame.account || "") !== (account || "") && row.quota?.limits?.length) return row;
        return {
          ...row,
          quota: {
            ...(row.quota ?? { read_error: null, read_at: null }),
            kind: frame.kind, account: frame.account || null, fetched_at: frame.fetched_at,
            stale: frame.stale, limits: frame.limits,
          },
        };
      }),
    };
  });
}
