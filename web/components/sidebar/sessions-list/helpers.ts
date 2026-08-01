"use client";

// Pure helpers + types for the sessions list. Split out of the
// 879-line sessions-list.tsx (title/label formatting, channel brand,
// placeholder detection, the LegacyConv shape, wsSend).

import { parseUserAttachments } from "@/components/chat/messages/user-attachments";

export interface SessionWindow {
  ws?: WebSocket;
  conversations?: Record<string, LegacyConv>;
  currentSessionId?: string | null;
  newSession?: () => void;
  renderSessions?: () => void;
}

export function wsSend(payload: unknown): void {
  const w = window as unknown as SessionWindow;
  if (w.ws && w.ws.readyState === WebSocket.OPEN) {
    w.ws.send(JSON.stringify(payload));
  }
}


export interface LegacyConv {
  id: string;
  title?: string;
  created_at?: number;
  /** 最后活跃时间（追加消息即更新）；recency 排序 / 日期分桶用它。 */
  updated_at?: number;
  channel?: string | null;
  account_id?: string | null;
  preview?: string | null;
  pinned?: boolean;
  archived?: boolean;
  group?: string;
  /** Project NAME this conversation belongs to. Backend-fed: a bound
   *  project's folder name, or the home-folder name as the catch-all for
   *  ad-hoc chats — so "group by project" always has a bucket (never
   *  "Ungrouped"). */
  project?: string;
  /** Lifecycle status driving the leading dot, Claude-Code-style:
   *   - "needs_input" → amber dot (the agent is waiting on the user)
   *   - "done"        → completed; pairs with `unread` for the blue dot
   *   - else          → idle (hollow ring)
   *  A live running task (see `runningTasks`) overrides this with the
   *  animated working dots. Backend-fed — absent until the server emits
   *  it, in which case rows fall back to working / idle. */
  status?: "needs_input" | "done" | "idle";
  /** A finished result the user hasn't opened yet → blue dot. Cleared
   *  when the conversation is viewed. Backend-fed. */
  unread?: boolean;
}

/** Recency bucket key for a conversation timestamp, on CALENDAR-day
 *  boundaries (not rolling 24h windows) so "Today" means "today", not
 *  "within the last 24 hours". Shared by the sidebar Recents list and
 *  the /chats page so both bucket identically. Label lookup is the
 *  caller's job — each surface words its own headers. */
/** "today" | "past7" | "m-<year>-<month0>" (current-year month beyond
 *  7 days) | "y-<year>" (previous years). */
export type BucketKey = string;

export function bucketKey(ts: number, nowTs: number): BucketKey {
  const d = new Date((ts || nowTs) * 1000);
  const now = new Date(nowTs * 1000);
  const day = new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const diff = Math.round((today - day) / 86_400_000);
  if (diff <= 0) return "today";
  if (diff <= 7) return "past7";
  if (d.getFullYear() === now.getFullYear()) return `m-${d.getFullYear()}-${d.getMonth()}`;
  return `y-${d.getFullYear()}`;
}

/** Whether a bucket is one of the dynamic ">7 days" month/year ones. */
export function bucketIsOlder(k: BucketKey): boolean {
  return k.startsWith("m-") || k.startsWith("y-");
}

/** Lexicographic sort key: ascending sort yields newest bucket first
 *  (today, 7d, then months newest-first, then years newest-first). */
export function bucketSortKey(k: BucketKey): string {
  if (k === "today") return "a0";
  if (k === "past7") return "a1";
  if (k.startsWith("m-")) {
    const [, y, m] = k.split("-");
    return `b-${9999 - Number(y)}-${String(11 - Number(m)).padStart(2, "0")}`;
  }
  if (k.startsWith("y-")) return `c-${9999 - Number(k.slice(2))}`;
  return "z";
}

/** Human label for a bucket. The fixed buckets take their i18n
 *  strings from the caller; month/year buckets format themselves. */
export function bucketLabel(
  k: BucketKey,
  locale: string,
  fixed: { today: string; past7: string },
): string {
  if (k === "today") return fixed.today;
  if (k === "past7") return fixed.past7;
  if (k.startsWith("m-")) {
    const [, y, m] = k.split("-").map(Number);
    return new Intl.DateTimeFormat(locale.startsWith("zh") ? "zh-CN" : "en-US", {
      month: "long",
    }).format(new Date(y, m, 1));
  }
  if (k.startsWith("y-")) {
    const y = k.slice(2);
    return locale.startsWith("zh") ? `${y} 年` : y;
  }
  return k;
}

/** The timestamp both surfaces bucket & recency-sort by: last activity,
 *  falling back to creation for rows the backend hasn't stamped yet. */
export function activityTs(c: { updated_at?: number; created_at?: number }): number {
  return c.updated_at || c.created_at || 0;
}

const CHANNEL_BRAND: Record<string, string> = {
  wechat: "WeChat",
  discord: "Discord",
  telegram: "Telegram",
  slack: "Slack",
};

export function channelBrand(ch?: string | null): string {
  if (!ch) return "";
  return CHANNEL_BRAND[String(ch).toLowerCase()] || ch;
}

function isPlaceholderTitle(t: string): boolean {
  if (!t) return true;
  if (t === "New conversation" || t === "Untitled") return true;
  return false;
}

/** The only fields displayTitle / labelFor actually read. Kept narrower
 *  than LegacyConv so the store's ConvSummary (whose `status` is a plain
 *  string) can be labelled too — both the sidebar and /chats call these. */
export type LabelableConv = Pick<LegacyConv, "title" | "preview" | "channel">;

export function displayTitle(c: LabelableConv): string {
  const raw = (c.title || "").trim();
  if (isPlaceholderTitle(raw)) return "";
  // The title often is the first user message verbatim, including the
  // composer's "[attached: …]" / inlined <file> markers. Strip them so
  // the recents row reads as prose (or the filename when only attached),
  // not raw attachment text — before truncating to 30 chars.
  const parsed = parseUserAttachments(raw);
  const t = parsed.text.trim() || parsed.attachments[0]?.filename || raw;
  // 不再拼 "…" 硬截断——溢出交给行内 CSS（右缘渐隐 + 悬停跑马灯）。
  // 只做超长兜底，防止整条首消息灌进 DOM。
  return t.length > 120 ? t.slice(0, 120) : t;
}

export function labelFor(c: LabelableConv, untitled: string): string {
  // Channel conversations get a bracketed brand prefix (no account, no
  // colon): "[WeChat] <title>". The title itself is the LLM-generated
  // real title (same two-phase naming as normal sessions); only the
  // display-layer brand tag is channel-specific.
  const brand = c.channel ? channelBrand(c.channel) : "";
  let real = displayTitle(c);
  if (!real && c.preview) {
    // Strip "[attached: …]" / inlined <file> markers the composer baked
    // into the message so the recents preview reads as the user's prose
    // (or the filename when they only attached), not raw attachment text.
    const parsed = parseUserAttachments(String(c.preview));
    let pv = parsed.text.trim();
    if (!pv && parsed.attachments.length > 0) pv = parsed.attachments[0].filename;
    pv = pv || String(c.preview).trim();
    real = pv.length > 120 ? pv.slice(0, 120) : pv;
  }
  if (brand && real) return `[${brand}] ${real}`;
  if (brand) return `[${brand}]`;
  if (real) return real;
  return c.title || untitled;
}

