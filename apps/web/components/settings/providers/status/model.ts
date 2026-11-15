/**
 * What the provider cards, rows and detail page show, worked out from the
 * status payload (`GET /api/providers/status`, lib/settings/provider-status.generated.ts).
 * Pure functions only, so every state the page can draw is tested without a
 * DOM (tests/settings/provider-status-model.test.mjs).
 *
 * The rules follow the approved design (docs/reference/design/models/provider-management.html):
 *  - quota rows count what is LEFT, as Codex does; a bar turns warning at
 *    80% used and danger at 95% used or when the limit is spent;
 *  - a snapshot older than the payload's `stale_after_s` is drawn dimmed;
 *  - a provider that reports nothing says so ("No quota data") and never
 *    draws an empty bar.
 */
import type {
  HealthState,
  HealthWire,
  ProviderStatus,
  QuotaLimitWire,
  SpendDay,
  SpendWire,
} from "@/lib/settings/provider-status.generated";

export type Text = (en: string, zh: string) => string;

/** Used share at which a bar turns warning / danger (models/usage/quota.py). */
export const WARNING_AT = 0.8;
export const DANGER_AT = 0.95;

export type Tone = "normal" | "warning" | "danger";
export type ChipTone = "ok" | "warn" | "bad" | "muted";

// ── formatting ─────────────────────────────────────────────────────────────

/** 245_000_000 → "245M", 8_100_000 → "8.1M", 312_000 → "312K". */
export function fmtCount(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return "—";
  const abs = Math.abs(n);
  const short = (value: number, suffix: string) => {
    const digits = value >= 100 ? 0 : value >= 10 ? (Number.isInteger(value) ? 0 : 1) : 1;
    return value.toFixed(digits).replace(/\.0$/, "") + suffix;
  };
  if (abs >= 1e9) return short(n / 1e9, "B");
  if (abs >= 1e6) return short(n / 1e6, "M");
  if (abs >= 1e3) return short(n / 1e3, "K");
  return String(Math.round(n));
}

const CURRENCY: Record<string, string> = { usd: "$", cny: "¥", eur: "€" };

/** An amount in its unit: "$18.42", "¥0.00", "1,200 credits". */
export function fmtAmount(amount: number | null | undefined, unit = "usd"): string {
  if (amount == null || !Number.isFinite(amount)) return "—";
  const symbol = CURRENCY[unit.toLowerCase()];
  if (symbol) {
    const digits = Math.abs(amount) >= 1000 ? 0 : 2;
    return symbol + amount.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  }
  if (unit === "credits") return `${amount.toLocaleString("en-US", { maximumFractionDigits: 2 })} credits`;
  return fmtCount(amount);
}

/** "$0.00" for nothing, "<$0.01" for a fraction of a cent. */
export function fmtUsd(amount: number | null | undefined): string {
  if (amount == null || !Number.isFinite(amount)) return "—";
  if (amount > 0 && amount < 0.01) return "<$0.01";
  return fmtAmount(amount, "usd");
}

/** USD per 1M tokens as the models table prints it: 0.27, 10.00, 0.0036. */
export function fmtPrice(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return "—";
  if (n === 0) return "0";
  if (n < 0.01) return n.toFixed(4);
  return n.toFixed(2);
}

export function fmtPercent(fraction: number | null | undefined): string {
  if (fraction == null || !Number.isFinite(fraction)) return "—";
  return `${Math.round(fraction * 100)}%`;
}

/** 128_000 → "128K", 1_000_000 → "1M". */
export function fmtContext(n: number | null | undefined): string {
  if (!n) return "—";
  if (n >= 1_000_000) return `${Math.round(n / 100_000) / 10}`.replace(/\.0$/, "") + "M";
  if (n >= 1000) return Math.round(n / 1000) + "K";
  return String(n);
}

const pad = (n: number) => String(n).padStart(2, "0");
const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** When a limit resets, as short as it can be said: "14:30" today, "Mon"
 *  within a week, "Dec 1" later (Codex's reset wording). */
export function fmtReset(resetsAt: number | null | undefined, now: number): string {
  if (resetsAt == null || !Number.isFinite(resetsAt)) return "";
  const at = new Date(resetsAt * 1000);
  const today = new Date(now * 1000);
  const sameDay = at.toDateString() === today.toDateString();
  if (sameDay || resetsAt - now < 12 * 3600) return `${pad(at.getHours())}:${pad(at.getMinutes())}`;
  if (resetsAt - now < 6.5 * 86400) return WEEKDAYS[at.getDay()];
  return `${MONTHS[at.getMonth()]} ${at.getDate()}`;
}

/** A clock time for "Rate limited · 14:02". */
export function fmtClock(ts: number): string {
  const at = new Date(ts * 1000);
  return `${pad(at.getHours())}:${pad(at.getMinutes())}`;
}

/** "just now", "4 min ago", "3 h ago", "2 days ago". */
export function fmtAgo(ts: number | null | undefined, now: number, text: Text): string {
  if (ts == null || !Number.isFinite(ts)) return "";
  const s = Math.max(0, now - ts);
  if (s < 60) return text("just now", "刚刚");
  const m = Math.floor(s / 60);
  if (m < 60) return text(`${m} min ago`, `${m} 分钟前`);
  const h = Math.floor(m / 60);
  if (h < 48) return text(`${h} h ago`, `${h} 小时前`);
  const d = Math.floor(h / 24);
  return text(`${d} days ago`, `${d} 天前`);
}

// ── health ─────────────────────────────────────────────────────────────────

export function chipTone(state: HealthState): ChipTone {
  switch (state) {
    case "ready": return "ok";
    case "rate_limited":
    case "signed_out": return "warn";
    case "no_credit":
    case "invalid_key": return "bad";
    default: return "muted";
  }
}

export function healthLabel(health: Pick<HealthWire, "state" | "until">, text: Text): string {
  switch (health.state) {
    case "ready": return text("Ready", "可用");
    case "rate_limited":
      return health.until ? text(`Rate limited · ${fmtClock(health.until)}`, `限流中 · ${fmtClock(health.until)}`) : text("Rate limited", "限流中");
    case "signed_out": return text("Signed out", "已退出登录");
    case "no_credit": return text("No credit", "余额不足");
    case "invalid_key": return text("Invalid key", "密钥无效");
    case "not_running": return text("Not running", "未运行");
    case "not_configured": return text("Not set up", "未配置");
    default: return text("Unknown", "未知");
  }
}

/** A key's pool status (KeyHealth.status) read as a health state. */
export function keyState(status: string, cooldownUntil: number | null, now: number): HealthState {
  switch (status) {
    case "valid":
    case "expiring_soon":
    case "refreshing":
      return cooldownUntil && cooldownUntil > now ? "rate_limited" : "ready";
    case "rate_limited":
      return cooldownUntil && cooldownUntil > now ? "rate_limited" : "ready";
    case "needs_reauth":
    case "stale": return "signed_out";
    case "billing_blocked": return "no_credit";
    case "revoked":
    case "invalid_credential": return "invalid_key";
    default: return "unknown";
  }
}

/** States whose card dims its quota block: the numbers are not current. */
export function dimmed(state: HealthState): boolean {
  return state === "signed_out" || state === "not_running";
}

// ── quota ──────────────────────────────────────────────────────────────────

/** The limit's own key without the plan prefix ("plan:balance" → "balance"). */
export function baseKey(limit: Pick<QuotaLimitWire, "limit_key">): string {
  return limit.limit_key.split(":").pop() ?? limit.limit_key;
}

export function barTone(limit: Pick<QuotaLimitWire, "used_fraction" | "status" | "remaining">): Tone {
  if (limit.status === "exhausted" || (limit.remaining != null && limit.remaining <= 0)) return "danger";
  const used = limit.used_fraction;
  if (used == null) return "normal";
  if (used >= DANGER_AT) return "danger";
  if (used >= WARNING_AT) return "warning";
  return "normal";
}

/** The share of a limit that is left, 0..1, or null when nothing says. */
export function leftFraction(limit: QuotaLimitWire): number | null {
  if (limit.left_fraction != null) return Math.max(0, Math.min(1, limit.left_fraction));
  if (limit.used_fraction != null) return Math.max(0, Math.min(1, 1 - limit.used_fraction));
  if (limit.limit && limit.remaining != null) return Math.max(0, Math.min(1, limit.remaining / limit.limit));
  return null;
}

export type QuotaRow = {
  key: string;
  label: string;
  /** Bar fill (share left), or null for a row without a bar. */
  left: number | null;
  tone: Tone;
  /** The bold figure ("62%", "46", "$3.20") and the rest of the line. */
  value: string;
  rest: string;
  /** A second line under the row ("Resets Dec 1"). */
  under: string;
};

const BALANCE_KEYS = new Set(["balance", "key_limit", "credits"]);
const RATE_LABELS: Record<string, [string, string]> = {
  rpm: ["Req/min", "请求/分钟"],
  tpm: ["Tok/min", "Token/分钟"],
  tpm_in: ["In tok/min", "输入 Token/分钟"],
  tpm_out: ["Out tok/min", "输出 Token/分钟"],
};

/** One bar row of a quota block: "5h [bar] 62% left · 14:30",
 *  "Req/min [bar] 46 / 50", "Key limit [bar] $3.20 of $20". */
export function quotaRow(limit: QuotaLimitWire, now: number, text: Text): QuotaRow {
  const key = baseKey(limit);
  const left = leftFraction(limit);
  const tone = barTone(limit);
  const reset = fmtReset(limit.resets_at, now);
  const rate = RATE_LABELS[key];
  const label = rate ? text(rate[0], rate[1]) : limit.label;
  if (limit.unit !== "percent" && limit.remaining == null && limit.limit == null && limit.used == null && limit.used_fraction == null) {
    // A limit the provider reports without a number: Codex's unlimited
    // credits, Copilot's unlimited premium requests.
    return {
      key: limit.limit_key, label, left: null, tone: "normal",
      value: text("Unlimited", "不限"), rest: "",
      under: "",
    };
  }
  if (limit.unit === "percent" || ((limit.window_minutes ?? 0) > 1 && !BALANCE_KEYS.has(key) && !rate && limit.unit !== "requests")) {
    return {
      key: limit.limit_key, label, left, tone,
      value: fmtPercent(left),
      rest: text(" left", " 剩余") + (reset ? ` · ${reset}` : ""),
      under: "",
    };
  }
  if (rate || limit.unit === "requests" || limit.unit === "tokens") {
    const value = limit.remaining != null ? fmtCount(limit.remaining) : fmtPercent(left);
    return {
      key: limit.limit_key, label, left, tone, value,
      rest: limit.limit != null ? ` / ${fmtCount(limit.limit)}` : "",
      under: "",
    };
  }
  // Money: key limit, credits, balance.
  const value = fmtAmount(limit.remaining ?? (limit.limit != null && limit.used != null ? limit.limit - limit.used : null), limit.unit);
  return {
    key: limit.limit_key, label, left: limit.limit ? left : null, tone, value,
    rest: limit.limit != null ? text(` of ${fmtAmount(limit.limit, limit.unit).replace(/\.00$/, "")}`, ` / ${fmtAmount(limit.limit, limit.unit).replace(/\.00$/, "")}`) : "",
    under: reset ? text(`Resets ${reset}`, `${reset} 重置`) : "",
  };
}

/** The four quota forms of the design, chosen by what the provider reports. */
export type QuotaForm =
  | { form: "windowed"; rows: QuotaRow[] }
  | { form: "balance"; amount: string; unit: string; tone: Tone; runway: string; rows: QuotaRow[] }
  | { form: "rate_limit"; rows: QuotaRow[] }
  | { form: "none" };

/** The 30-day pace of a prepaid balance: "≈ 11 weeks at this pace", hidden
 *  with fewer than 3 days of spend or when spend and balance differ in unit. */
export function runway(amount: number, unit: string, spend: Pick<SpendWire, "series">, text: Text): string {
  if (unit.toLowerCase() !== "usd" || amount <= 0) return "";
  const days = spend.series.filter((d) => d.cost_usd > 0);
  if (days.length < 3) return "";
  const first = spend.series.findIndex((d) => d.cost_usd > 0);
  const span = Math.max(spend.series.length - first, days.length);
  const perDay = days.reduce((sum, d) => sum + d.cost_usd, 0) / span;
  if (perDay <= 0) return "";
  const daysLeft = amount / perDay;
  if (daysLeft < 14) {
    const n = Math.max(1, Math.round(daysLeft));
    return text(`≈ ${n} ${n === 1 ? "day" : "days"} at this pace`, `按当前速度约可用 ${n} 天`);
  }
  const weeks = Math.round(daysLeft / 7);
  if (weeks > 104) return text("≈ 2+ years at this pace", "按当前速度可用 2 年以上");
  return text(`≈ ${weeks} weeks at this pace`, `按当前速度约可用 ${weeks} 周`);
}

export function quotaForm(p: Pick<ProviderStatus, "quota" | "spend">, now: number, text: Text): QuotaForm {
  const quota = p.quota;
  const limits = quota?.limits ?? [];
  if (!quota || quota.kind === "none" || limits.length === 0) return { form: "none" };
  if (quota.kind === "windowed") {
    return { form: "windowed", rows: limits.filter((l) => l.unit === "percent" || (l.window_minutes ?? 0) > 1).map((l) => quotaRow(l, now, text)) };
  }
  if (quota.kind === "rate_limit") {
    return { form: "rate_limit", rows: limits.filter((l) => RATE_LABELS[baseKey(l)]).map((l) => quotaRow(l, now, text)) };
  }
  // Balance kind: a plain balance is a big figure; a key limit or credits
  // with a cap is a bar row ("$3.20 of $20").
  const balance = limits.find((l) => baseKey(l) === "balance" && l.limit == null);
  const capped = limits.filter((l) => BALANCE_KEYS.has(baseKey(l)) && l !== balance);
  const rows = capped.map((l) => quotaRow(l, now, text));
  if (balance) {
    const amount = balance.remaining ?? 0;
    return {
      form: "balance",
      amount: fmtAmount(amount, balance.unit),
      unit: balance.unit,
      tone: barTone(balance),
      runway: runway(amount, balance.unit, p.spend, text),
      rows,
    };
  }
  return { form: "windowed", rows };
}

export type CompactQuota = {
  form: QuotaForm["form"];
  /** The bar of the row that runs out first, or null. */
  left: number | null;
  tone: Tone;
  /** Bold figure + the rest, joined with " · ". */
  parts: { b: string; t: string }[];
};

const TONE_RANK: Record<Tone, number> = { normal: 0, warning: 1, danger: 2 };

/** The one-line quota of a compact row (design §2, B):
 *  "62% 5h · 14:30 · 81% wk", "$18.42 balance · ≈ 11 wk", "46/50 rpm · no balance data". */
export function compactQuota(p: Pick<ProviderStatus, "quota" | "spend">, now: number, text: Text): CompactQuota {
  const q = quotaForm(p, now, text);
  if (q.form === "none") return { form: "none", left: null, tone: "normal", parts: [] };
  const rows = q.rows;
  const worst = rows.reduce<QuotaRow | null>((a, r) => (!a || (r.left ?? 1) < (a.left ?? 1) ? r : a), null);
  const tone = rows.reduce<Tone>((t, r) => (TONE_RANK[r.tone] > TONE_RANK[t] ? r.tone : t), q.form === "balance" ? q.tone : "normal");
  if (q.form === "balance") {
    const parts = [{ b: q.amount, t: text(" balance", " 余额") }];
    if (q.runway) parts.push({ b: "", t: q.runway.replace(/ at this pace$/, "").replace(/ weeks?$/, " wk") });
    for (const r of rows) parts.push({ b: r.value, t: r.rest });
    return { form: q.form, left: worst?.left ?? null, tone, parts };
  }
  if (q.form === "rate_limit") {
    const first = rows[0];
    return {
      form: q.form, left: first?.left ?? null, tone,
      parts: [
        ...(first ? [{ b: first.value, t: `${first.rest.replace(/^ \/ /, "/")} ${first.label.toLowerCase().replace("req/min", "rpm")}` }] : []),
        { b: "", t: text("no balance data", "没有余额数据") },
      ],
    };
  }
  const parts = rows.map((r, i) => {
    if (r.left != null && r.under === "" && /^ (left|剩余)/.test(r.rest)) {
      const reset = i === 0 ? r.rest.split(" · ")[1] : "";
      return { b: r.value, t: ` ${r.label}${reset ? ` · ${reset}` : ""}` };
    }
    // Money: "$3.20 of $20 · Dec 1".
    const reset = r.under.replace(/^Resets /, "").replace(/ 重置$/, "");
    return { b: r.value, t: `${r.rest}${reset ? ` · ${reset}` : ""}` };
  });
  return { form: q.form, left: worst?.left ?? null, tone, parts };
}

/** The limit that runs out first, for the detail page's fourth figure. */
export function bindingLimit(p: Pick<ProviderStatus, "quota">): QuotaLimitWire | null {
  const limits = (p.quota?.limits ?? []).filter((l) => leftFraction(l) != null || baseKey(l) === "balance");
  if (!limits.length) return null;
  const balance = limits.find((l) => baseKey(l) === "balance" && l.limit == null);
  const ranked = limits.filter((l) => leftFraction(l) != null);
  if (!ranked.length) return balance ?? null;
  return ranked.reduce((a, b) => ((leftFraction(a) ?? 1) <= (leftFraction(b) ?? 1) ? a : b));
}

// ── spend ──────────────────────────────────────────────────────────────────

export type SpendView = {
  /** "$6.30" with suffix "30d", "245M" with "tok", or "Included". */
  value: string;
  suffix: string;
  /** Whether the sparkline draws cost (true) or tokens. */
  byCost: boolean;
  /** Tooltip: API list price of included calls, unpriced call count. */
  tip: string;
};

/** A plan as its provider writes it: "plus" → "Plus", "pro_lite" → "Pro Lite". */
export function planLabel(plan: string | null | undefined): string {
  return (plan ?? "").trim().split(/[\s_-]+/).filter(Boolean)
    .map((word) => word[0].toUpperCase() + word.slice(1)).join(" ");
}

export function spendView(spend: SpendWire, text: Text, plan?: string | null): SpendView {
  const unpriced = spend.unpriced_calls > 0
    ? text(`${spend.unpriced_calls} calls unpriced`, `${spend.unpriced_calls} 次调用无价格`)
    : "";
  if (spend.included) {
    const name = planLabel(plan);
    const covered = name
      ? text(`Included in the ${name} plan`, `${name} 套餐内`)
      : text("Included in the plan", "套餐内");
    return {
      value: fmtCount(spend.total_tokens), suffix: text("tok", "tok"), byCost: false,
      tip: spend.list_price_usd > 0
        ? covered + text(` · ${fmtUsd(spend.list_price_usd)} at API prices`, ` · 按 API 价约 ${fmtUsd(spend.list_price_usd)}`)
        : covered,
    };
  }
  const cost = spend.cost_usd ?? 0;
  if (cost <= 0 && spend.total_tokens > 0) {
    // No price known for these calls: tokens say more than "$0.00".
    return { value: fmtCount(spend.total_tokens), suffix: text("tok", "tok"), byCost: false, tip: unpriced };
  }
  return { value: fmtUsd(cost), suffix: text("30d", "30 天"), byCost: true, tip: unpriced };
}

export function sparkValues(series: SpendDay[], byCost: boolean): number[] {
  return series.map((d) => (byCost ? d.cost_usd : d.tokens));
}

/** The billing tag next to the name: a subscription's plan name when the
 *  sign-in or its replies name one, else the billing kind. */
export function billingLabel(billing: ProviderStatus["billing"], text: Text, plan?: string | null): string {
  if (billing === "subscription") return planLabel(plan) || text("Subscription", "订阅");
  if (billing === "local") return text("Local", "本地");
  return text("API key", "API key");
}

/** When one provider's status was last read: its quota, or a Refresh of its login and health. */
export function providerUpdatedAt(p: Pick<ProviderStatus, "quota" | "checked_at">): number | null {
  const times = [p.quota?.fetched_at ?? null, p.checked_at ?? null].filter((t): t is number => t != null);
  return times.length ? Math.max(...times) : null;
}

/** The newest time any provider was read, else when the payload was built. */
export function updatedAt(providers: ProviderStatus[], generatedAt: number): number {
  const times = providers.map(providerUpdatedAt).filter((t): t is number => t != null);
  return times.length ? Math.max(...times) : generatedAt;
}
