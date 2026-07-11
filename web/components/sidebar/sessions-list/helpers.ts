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

export function displayTitle(c: LegacyConv): string {
  const raw = (c.title || "").trim();
  if (isPlaceholderTitle(raw)) return "";
  // The title often is the first user message verbatim, including the
  // composer's "[attached: …]" / inlined <file> markers. Strip them so
  // the recents row reads as prose (or the filename when only attached),
  // not raw attachment text — before truncating to 30 chars.
  const parsed = parseUserAttachments(raw);
  const t = parsed.text.trim() || parsed.attachments[0]?.filename || raw;
  return t.length > 30 ? t.slice(0, 30) + "…" : t;
}

export function labelFor(c: LegacyConv, untitled: string): string {
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
    real = pv.length > 30 ? pv.slice(0, 30) + "…" : pv;
  }
  if (brand && real) return `[${brand}] ${real}`;
  if (brand) return `[${brand}]`;
  if (real) return real;
  return c.title || untitled;
}

