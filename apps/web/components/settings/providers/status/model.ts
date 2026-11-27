/**
 * What the provider cards, rows and detail page show, worked out from the
 * status payload (`GET /api/providers/status`, lib/settings/provider-status.generated.ts).
 * Pure functions only, so every state the page can draw is tested without a
 * DOM (tests/settings/providers/provider-status-model.test.mjs).
 *
 * The rules follow the approved design (docs/reference/design/models/provider-management.html):
 *  - quota rows count what is LEFT, as Codex does; a bar turns warning at
 *    80% used and danger at 95% used or when the limit is spent;
 *  - a snapshot older than the payload's `stale_after_s` is drawn dimmed;
 *  - a provider that reports nothing says so ("No quota data") and never
 *    draws an empty bar.
 */
import type {
  FixAction,
  HealthState,
  HealthWire,
  KeyHealth,
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

/** An amount in its unit: "$18.42", "-¥0.26", "1,200 credits". */
export function fmtAmount(amount: number | null | undefined, unit = "usd"): string {
  if (amount == null || !Number.isFinite(amount)) return "—";
  const symbol = CURRENCY[unit.toLowerCase()];
  if (symbol) {
    const digits = Math.abs(amount) >= 1000 ? 0 : 2;
    return (amount < 0 ? "-" : "") + symbol + Math.abs(amount).toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
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

/** The window names the server writes (``quota.window_label``), in the reader's language. */
const WINDOW_LABELS: Record<string, [string, string]> = {
  "5h": ["5h", "5 小时"],
  Daily: ["Daily", "每天"],
  Weekly: ["Weekly", "每周"],
  Monthly: ["Monthly", "每月"],
  Annual: ["Annual", "每年"],
};

/** One bar row of a quota block: "5h [bar] 62% left · 14:30",
 *  "Req/min [bar] 46 / 50", "Key limit [bar] $3.20 of $20". */
export function quotaRow(limit: QuotaLimitWire, now: number, text: Text): QuotaRow {
  const key = baseKey(limit);
  const left = leftFraction(limit);
  const tone = barTone(limit);
  const reset = fmtReset(limit.resets_at, now);
  const rate = RATE_LABELS[key];
  const named = rate ?? WINDOW_LABELS[limit.label];
  const label = named ? text(named[0], named[1]) : limit.label;
  if (limit.unit !== "percent" && limit.remaining == null && limit.limit == null && limit.used == null && limit.used_fraction == null) {
    // A limit the provider reports without a number, such as unlimited
    // credits.
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

/** Plan ids a provider writes as one word. */
const PLAN_NAMES: Record<string, string> = { prolite: "Pro Lite" };

/** A plan as its provider writes it: "plus" → "Plus", "pro_lite" and "prolite" → "Pro Lite". */
export function planLabel(plan: string | null | undefined): string {
  const id = (plan ?? "").trim();
  return PLAN_NAMES[id.toLowerCase()] ?? id.split(/[\s_-]+/).filter(Boolean)
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
      value: fmtCount(spend.total_tokens), suffix: text("tok", "token"), byCost: false,
      tip: spend.list_price_usd > 0
        ? covered + text(` · ${fmtUsd(spend.list_price_usd)} at API prices`, ` · 按 API 价约 ${fmtUsd(spend.list_price_usd)}`)
        : covered,
    };
  }
  const cost = spend.cost_usd ?? 0;
  if (cost <= 0 && spend.total_tokens > 0) {
    // No price known for these calls: tokens say more than "$0.00".
    return { value: fmtCount(spend.total_tokens), suffix: text("tok", "token"), byCost: false, tip: unpriced };
  }
  return { value: fmtUsd(cost), suffix: text("30d", "30 天"), byCost: true, tip: unpriced };
}

export function sparkValues(series: SpendDay[], byCost: boolean): number[] {
  return series.map((d) => (byCost ? d.cost_usd : d.tokens));
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

// ── companies ──────────────────────────────────────────────────────────────

/** Health from best to worst: a company's chip shows its best way's. */
const HEALTH_ORDER: HealthState[] = [
  "ready", "unknown", "rate_limited", "signed_out", "no_credit", "invalid_key", "not_configured", "not_running",
];

/** The best of several healths (the first of equals). */
export function bestHealth(healths: HealthWire[]): HealthWire {
  const rank = (h: HealthWire) => {
    const i = HEALTH_ORDER.indexOf(h.state);
    return i < 0 ? HEALTH_ORDER.length : i;
  };
  return healths.reduce((best, h) => (rank(h) < rank(best) ? h : best));
}

/** One card of the list: a company connected in more than one way, or one provider. */
export type CompanyGroup = { family: string; label: string; ways: ProviderStatus[] };

/** The list as cards. The payload lists each company's ways together, in
 *  the company table's order (models/auth/login/login_method_registry.py);
 *  a company connected one way only is a group of one, drawn as today. */
export function companyGroups(providers: ProviderStatus[]): CompanyGroup[] {
  const groups = new Map<string, CompanyGroup>();
  for (const p of providers) {
    const family = p.family || p.id;
    const group = groups.get(family);
    if (group) group.ways.push(p);
    else groups.set(family, { family, label: p.family_label || p.label, ways: [p] });
  }
  return [...groups.values()];
}

/** A way's title on its company's card ("Go · monthly subscription"). */
export function wayTitle(p: Pick<ProviderStatus, "label" | "way_label">, text: Text): string {
  return p.way_label ? text(p.way_label.title, p.way_label.title_zh) : p.label;
}

/** A way's name in a row under its company ("Zen", "Go"). */
export function wayShort(p: Pick<ProviderStatus, "label" | "way_label">, text: Text): string {
  return p.way_label ? text(p.way_label.short, p.way_label.short_zh) : p.label;
}


// ── card rows ──────────────────────────────────────────────────────────────

/** One limit of a credential as a ring: what is left, its short name and
 *  when it resets (or the cap it counts against). `left` null draws the
 *  dashed ring of a quota that could not be read. */
export type Ring = { key: string; left: number | null; tone: Tone; label: string; under: string; tip: string };

/** The last column of a row: an amount (credits, a balance, 30 days of
 *  spend) while the credential works, else why it does not and when that
 *  changes or was found. `fix` is what a click on a danger cell does. */
export type RowEnd = { top: string; bottom: string; tone: "normal" | "warning" | "danger" | "muted"; fix: FixAction | null };

/** One credential of a card: who it is, its rings, and its last column. */
export type CardRow = {
  key: string;
  p: ProviderStatus;
  credential: string | null;
  name: string;
  sub: string;
  /** The one a new conversation would use, when the provider has several. */
  inUse: boolean;
  rings: Ring[];
  end: RowEnd;
  /** Its numbers are older than 15 minutes; `updated` says when they were read. */
  stale: boolean;
  updated: string;
  off: boolean;
  /** Needs a person or waits: drawn among the first when a card folds. */
  troubled: boolean;
  /** `sub` lists the ways a company's key works on, and may wrap. */
  listsWays?: boolean;
};

/** Rings a row holds at most; more go to the provider's page. */
export const MAX_RINGS = 3;
/** Rows a card shows before the rest fold into "N more". */
export const MAX_ROWS = 4;

const RING_LABELS: Record<string, [string, string]> = {
  "5h": ["5h", "5 小时"], Daily: ["Day", "天"], Weekly: ["Week", "周"], Monthly: ["Month", "月"], Annual: ["Year", "年"],
};
const RATE_RING: Record<string, [string, string]> = {
  rpm: ["Req/m", "请求/分"], tpm: ["Tok/m", "Token/分"], tpm_in: ["In/m", "输入/分"], tpm_out: ["Out/m", "输出/分"],
};
const NO_END: RowEnd = { top: "", bottom: "", tone: "normal", fix: null };

/** A limit with a share left, or one reported without a number (unlimited). */
function ringOf(limit: QuotaLimitWire, now: number, text: Text): Ring | null {
  const key = baseKey(limit);
  const unlimited = limit.unit !== "percent" && limit.remaining == null && limit.limit == null && limit.used == null && limit.used_fraction == null;
  const left = leftFraction(limit);
  if (!unlimited && (left == null || (key !== "key_limit" && BALANCE_KEYS.has(key)))) return null;
  const named = RATE_RING[key] ?? RING_LABELS[limit.label];
  const label = named ? text(named[0], named[1]) : key === "key_limit" ? text("Limit", "上限") : limit.label;
  const amount = (n: number | null) => (limit.unit === "percent" ? "" : BALANCE_KEYS.has(key) || CURRENCY[limit.unit.toLowerCase()] ? fmtAmount(n, limit.unit).replace(/\.00$/, "") : fmtCount(n));
  const cap = limit.limit != null && limit.unit !== "percent" ? text(`of ${amount(limit.limit)}`, `共 ${amount(limit.limit)}`) : "";
  const under = unlimited ? text("Unlimited", "不限") : fmtReset(limit.resets_at, now) || cap;
  const share = unlimited ? 1 : (left as number);
  const tip = [limit.label, unlimited ? text("Unlimited", "不限") : text(`${fmtPercent(share)} left`, `剩余 ${fmtPercent(share)}`), cap, limit.resets_at ? text(`resets ${fmtReset(limit.resets_at, now)}`, `${fmtReset(limit.resets_at, now)} 重置`) : ""].filter(Boolean).join(" · ");
  return { key: limit.limit_key, left: share, tone: unlimited ? "normal" : barTone(limit), label, under, tip };
}

/** Up to three rings, shortest window first; with more, the tightest three. */
export function ringsOf(quota: ProviderStatus["quota"] | undefined, now: number, text: Text): Ring[] {
  const limits = quota?.limits ?? [];
  const rings = limits.map((limit) => ({ limit, ring: ringOf(limit, now, text) })).filter((x): x is { limit: QuotaLimitWire; ring: Ring } => x.ring != null);
  const span = (l: QuotaLimitWire) => (l.window_minutes ?? (RATE_RING[baseKey(l)] ? 1 : Number.MAX_SAFE_INTEGER));
  const kept = rings.length > MAX_RINGS ? [...rings].sort((a, b) => (a.ring.left ?? 1) - (b.ring.left ?? 1)).slice(0, MAX_RINGS) : rings;
  const out = [...kept].sort((a, b) => span(a.limit) - span(b.limit)).map((x) => x.ring);
  if (!out.length && quota?.read_error) {
    out.push({ key: "unread", left: null, tone: "normal", label: text("Unread", "未读到"), under: quota.read_at ? fmtAge(quota.read_at, now, text) : "", tip: quota.read_error });
  }
  return out;
}

/** "18 m", "3 h", "2 d": how long ago, short enough for a row's second line. */
export function fmtAge(ts: number, now: number, text: Text): string {
  const m = Math.max(0, Math.floor((now - ts) / 60));
  if (m < 1) return text("now", "刚刚");
  if (m < 60) return text(`${m} m ago`, `${m} 分钟前`);
  const h = Math.floor(m / 60);
  if (h < 48) return text(`${h} h ago`, `${h} 小时前`);
  return text(`${Math.floor(h / 24)} d ago`, `${Math.floor(h / 24)} 天前`);
}

/** The amount a working credential holds: credits, a balance, else what it spent in 30 days. */
function amountOf(quota: ProviderStatus["quota"] | undefined, spend: number | null, billing: ProviderStatus["billing"], text: Text): RowEnd {
  const limits = quota?.limits ?? [];
  const credits = limits.find((l) => baseKey(l) === "credits" && l.limit == null && l.remaining != null);
  if (credits) return { top: fmtCount(credits.remaining), bottom: text("Credits", "Credits"), tone: "normal", fix: null };
  const balance = limits.find((l) => baseKey(l) === "balance" && l.limit == null && l.remaining != null);
  if (balance) return { top: fmtAmount(balance.remaining, balance.unit), bottom: text("Balance", "余额"), tone: (balance.remaining ?? 0) <= 0 ? "danger" : "normal", fix: null };
  if (billing === "api_key" && spend != null && spend > 0) return { top: fmtUsd(spend), bottom: text("30 days", "30 天"), tone: "normal", fix: null };
  return NO_END;
}

/** The last column of a credential's row (design: one column for amounts and states). */
export function rowEnd(p: Pick<ProviderStatus, "billing" | "fix">, k: Pick<KeyHealth, "enabled" | "health" | "quota" | "spend_usd" | "cooldown_until" | "status_at">, now: number, text: Text): RowEnd {
  if (!k.enabled) return { top: text("Off", "已停用"), bottom: "", tone: "muted", fix: null };
  const found = (k.status_at ?? k.quota?.read_at) ? fmtAge((k.status_at ?? k.quota?.read_at) as number, now, text) : "";
  const h = k.health;
  switch (h.state) {
    case "rate_limited": {
      const cooling = !!k.cooldown_until && k.cooldown_until > now;
      return { top: cooling ? text("Cooling", "冷却中") : text("Limited", "已用完"), bottom: h.until ? fmtReset(h.until, now) : "", tone: "warning", fix: null };
    }
    case "no_credit": {
      const amount = amountOf(k.quota, null, p.billing, text);
      return amount.top ? { ...amount, tone: "danger", fix: "top_up" } : { top: text("No credit", "余额不足"), bottom: found, tone: "danger", fix: "top_up" };
    }
    case "invalid_key": return { top: text("Invalid", "密钥无效"), bottom: found, tone: "danger", fix: "replace_key" };
    case "signed_out": return { top: text("Signed out", "已退出"), bottom: found, tone: "danger", fix: "sign_in" };
    default: return amountOf(k.quota, k.spend_usd, p.billing, text);
  }
}

/** What a credential is called on its row: the account's email, the name
 *  given to the key, its last four, the variable it is read from. */
export function credentialName(k: Pick<KeyHealth, "email" | "label" | "key_tail" | "env_var" | "account">): string {
  return k.email || k.label || (k.key_tail ? `····${k.key_tail}` : k.env_var ? `$${k.env_var}` : k.account);
}

/** Every row of a card: one per credential, in the payload's order. A
 *  sign-in is one way's. A company's key is listed by every way that takes
 *  a key and is one row all the same (`onePerKey`). A way with no
 *  credential (a local server, one not set up) is one row of its own. */
export function cardRows(group: CompanyGroup, now: number, text: Text): CardRow[] {
  const several = group.ways.length > 1;
  const rows: WayRow[] = [];
  for (const p of group.ways) {
    const way = several ? wayShort(p, text) : "";
    const kind = p.billing === "subscription" ? "" : p.billing === "local" ? text("Local", "本地") : text("API key", "API 密钥");
    if (!p.keys.length) {
      const local = p.billing === "local";
      const end: RowEnd = p.health.state === "not_running"
        ? { top: text("Not running", "未运行"), bottom: "", tone: "danger", fix: "start_server" }
        : NO_END;
      rows.push({
        key: p.id, p, credential: null,
        name: local ? (way || text("Local server", "本地服务")) : text("No key yet", "还没有密钥"),
        sub: local ? text("Local", "本地") : [way, kind].filter(Boolean).join(" · "),
        inUse: false, rings: ringsOf(p.quota, now, text), end, stale: false, updated: "", off: false,
        troubled: end.tone === "danger", way, used: false, refused: false,
      });
      continue;
    }
    // A way already named "API" needs no "API key" after it.
    const keyKind = /^API\b/.test(way) ? "" : kind;
    for (const k of p.keys) {
      const plan = p.billing === "subscription" ? planLabel(k.plan) || (several ? "" : text("Subscription", "订阅")) : keyKind;
      const end = rowEnd(p, k, now, text);
      const stale = !!k.quota?.stale && !!k.quota?.fetched_at;
      rows.push({
        key: `${p.id}:${k.credential_id}`, p, credential: k.credential_id,
        name: credentialName(k),
        sub: [way, plan].filter(Boolean).join(" · "),
        inUse: k.active && p.keys.filter((x) => x.enabled).length > 1,
        rings: ringsOf(k.quota, now, text), end, stale,
        updated: stale ? text(`Updated ${fmtAgo(k.quota.fetched_at, now, text)}`, `更新于 ${fmtAgo(k.quota.fetched_at, now, text)}`) : "",
        off: !k.enabled,
        troubled: end.tone === "danger" || end.tone === "warning",
        way, used: k.use_count > 0, refused: k.enabled && k.health.state === "invalid_key",
      });
    }
  }
  return onePerKey(rows, group.ways.find((p) => p.default_model)?.id, text);
}

/** A credential on one way, before a company's key is drawn as one row. */
type WayRow = CardRow & { way: string; used: boolean; refused: boolean };

/** A company's key is listed by every way that takes a key. It is one row
 *  that shows everything its ways report about it:
 *  - under its name, every way it answered on: used there, its quota read
 *    there, or a limit or an amount reported there;
 *  - the rings of those ways, the ones with a number first, each hint
 *    naming its way;
 *  - the last column of a way in trouble, else of one with an amount.
 *  A way that refuses the key is not named and is no trouble: the key is
 *  not that way's by nature (a China plan's key on the international
 *  address). A key every way refuses is an invalid key. The dot marks the
 *  key a new conversation uses: the one in use on the way of the card's
 *  model. */
function onePerKey(rows: WayRow[], current: string | undefined, text: Text): CardRow[] {
  const byKey = new Map<string, WayRow[]>();
  for (const r of rows) if (r.credential) byKey.set(r.credential, [...(byKey.get(r.credential) ?? []), r]);
  const counted = (x: WayRow) => x.rings.some((g) => g.left != null);
  const answered = (x: WayRow) => !x.refused && (x.used || counted(x) || !!x.end.top);
  const out: CardRow[] = [];
  const placed = new Set<string>();
  for (const r of rows) {
    const same = r.credential ? (byKey.get(r.credential) as WayRow[]) : [r];
    if (same.length < 2) {
      const { way: _w, used: _u, refused: _r, ...row } = r;
      out.push(row);
      continue;
    }
    if (placed.has(r.credential as string)) continue;
    placed.add(r.credential as string);
    const taken = same.filter((x) => !x.refused);
    if (!taken.length) {
      const { way: _w, used: _u, refused: _r, ...row } = same[0];
      out.push({ ...row, key: `${row.p.family}:${row.credential}`, sub: text("API key", "API 密钥"), inUse: false });
      continue;
    }
    const lead = taken.find((x) => x.troubled) ?? taken.find(counted) ?? taken.find((x) => !!x.end.top)
      ?? taken.find((x) => x.p.id === current) ?? taken.find((x) => x.used) ?? taken[0];
    const order = [lead, ...taken.filter((x) => x !== lead)];
    const named = (x: WayRow) => x.way;
    const rings: Ring[] = [];
    const seen = new Set<string>();
    for (const real of [true, false]) {
      for (const w of order) {
        for (const g of w.rings) {
          if ((g.left != null) !== real || rings.length >= MAX_RINGS) continue;
          const id = `${g.key}:${g.left}:${g.under}`;
          if (seen.has(id)) continue;
          seen.add(id);
          rings.push({ ...g, key: `${w.p.id}:${g.key}`, tip: [named(w), g.tip].filter(Boolean).join(" · ") });
        }
      }
      if (rings.some((g) => g.left != null)) break;
    }
    const endFrom = lead.end.top ? lead : taken.find((x) => !!x.end.top) ?? lead;
    const works = taken.filter(answered).map((x) => x.way).filter(Boolean);
    const { way: _w, used: _u, refused: _r, ...row } = endFrom;
    out.push({
      ...row,
      key: `${lead.p.family}:${lead.credential}`,
      p: lead.p,
      sub: works.length ? works.join(" · ") : text("Not used yet", "还没用过"),
      listsWays: works.length > 1,
      inUse: same.some((x) => x.p.id === current && x.inUse),
      rings,
      troubled: taken.some((x) => x.troubled),
      stale: lead.stale, updated: lead.updated,
    });
  }
  return out;
}

/** The rows a folded card shows: the ones in use and in trouble first,
 *  then the rest in order, up to `MAX_ROWS`, kept in list order. */
export function shownRows(rows: CardRow[]): { shown: CardRow[]; hidden: CardRow[] } {
  if (rows.length <= MAX_ROWS) return { shown: rows, hidden: [] };
  const rank = (r: CardRow) => (r.inUse ? 0 : r.troubled ? 1 : 2);
  const picked = new Set(rows.map((r, i) => ({ r, i })).sort((a, b) => rank(a.r) - rank(b.r) || a.i - b.i).slice(0, MAX_ROWS - 1).map((x) => x.r));
  return { shown: rows.filter((r) => picked.has(r)), hidden: rows.filter((r) => !picked.has(r)) };
}

/** "1 limited · 1 invalid": what the folded rows hold, troubles only. */
export function foldedSummary(rows: CardRow[]): string {
  const counts = new Map<string, number>();
  for (const r of rows) if (r.troubled) counts.set(r.end.top.toLowerCase(), (counts.get(r.end.top.toLowerCase()) ?? 0) + 1);
  return [...counts].map(([label, n]) => `${n} ${label}`).join(" · ");
}

// ── masonry ────────────────────────────────────────────────────────────────

/** Where each card goes: in list order, into the column that is shortest
 *  so far (the leftmost of equals), at that column's height. Each top is
 *  at or below the one before, so the list order is also the reading order. */
export function placeMasonry(heights: number[], columns: number, gap: number): { column: number; top: number }[] {
  const bottoms = Array.from({ length: Math.max(1, columns) }, () => 0);
  return heights.map((height) => {
    const column = bottoms.indexOf(Math.min(...bottoms));
    const top = bottoms[column];
    bottoms[column] = top + Math.ceil(height) + gap;
    return { column, top };
  });
}

/** A card's height before it is measured, close enough that the first
 *  paint already puts each card in the column it will keep. */
export function estimateCardHeight(group: CompanyGroup): number {
  // A company's key is one row, however many ways list it.
  const keys = new Set(group.ways.flatMap((p) => p.keys.map((k) => k.credential_id)));
  const rows = keys.size + group.ways.filter((p) => !p.keys.length).length;
  const shown = Math.min(rows, MAX_ROWS);
  // Rows are 60 px tiles 8 px apart, 12 px from the card's sides and bottom.
  return 16 + 32 + 8 + shown * 60 + Math.max(0, shown - 1) * 8 + 12;
}
