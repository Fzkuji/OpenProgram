"use client";

/**
 * Sessions list (the "Recents" panel in the sidebar).
 *
 * Reads conversations from `window.conversations` via `useLegacyGlobals`
 * — the legacy `init.js` is what populates that global from the
 * sessions_list / history_list WS events, so we piggy-back on it
 * instead of duplicating the WS handling here. Once the WSProvider
 * (which writes to useSessionStore) is wired into the layout, this
 * hook should switch to a store subscription.
 */

import { useRouter, usePathname } from "next/navigation";
import { useLegacyGlobals, useCurrentSessionId } from "./use-legacy-globals";
import styles from "./sidebar.module.css";

interface LegacyConv {
  id: string;
  title?: string;
  created_at?: number;
  channel?: string | null;
  account_id?: string | null;
  preview?: string | null;
  has_session?: boolean;
}

const CHANNEL_BRAND: Record<string, string> = {
  wechat: "WeChat",
  discord: "Discord",
  telegram: "Telegram",
  slack: "Slack",
};

function channelBrand(ch?: string | null): string {
  if (!ch) return "";
  return CHANNEL_BRAND[String(ch).toLowerCase()] || ch;
}

function channelPrefix(ch?: string | null, acct?: string | null): string {
  if (!ch) return "";
  const brand = channelBrand(ch);
  return acct ? `${brand} (${acct})` : brand;
}

function isPlaceholderTitle(t: string): boolean {
  if (!t) return true;
  if (t === "New conversation" || t === "Untitled") return true;
  return /^(wechat|discord|telegram|slack)\s*[:：]\s*\S{8,}/i.test(t);
}

function displayTitle(c: LegacyConv): string {
  const t = (c.title || "").trim();
  if (isPlaceholderTitle(t)) return "";
  return t.length > 30 ? t.slice(0, 30) + "…" : t;
}

function labelFor(c: LegacyConv): string {
  const prefix = channelPrefix(c.channel, c.account_id);
  let real = displayTitle(c);
  if (!real && c.preview) {
    const pv = String(c.preview).trim();
    real = pv.length > 30 ? pv.slice(0, 30) + "…" : pv;
  }
  if (prefix && real) return prefix + ": " + real;
  if (prefix) return prefix;
  if (real) return real;
  return c.title || "Untitled";
}

export function SessionsList() {
  const router = useRouter();
  const pathname = usePathname();
  const { conversations } = useLegacyGlobals();
  const currentId = useCurrentSessionId();

  const list = Object.values(conversations).sort(
    (a, b) => (b.created_at || 0) - (a.created_at || 0)
  );

  function switchTo(id: string) {
    if (id === currentId && pathname === "/s/" + id) return;
    router.push("/s/" + id);
  }

  function del(id: string, e: React.MouseEvent) {
    e.stopPropagation();
    // Delegate to the legacy helper — it already handles the confirm
    // dialog, the WS `delete_session` action, the in-memory cleanup
    // and the redirect to /chat when the active conv is deleted.
    const w = window as unknown as { deleteSession?: (id: string) => void };
    if (typeof w.deleteSession === "function") w.deleteSession(id);
  }

  function clearAll() {
    const w = window as unknown as { clearAllSessions?: () => void };
    if (typeof w.clearAllSessions === "function") w.clearAllSessions();
  }

  if (list.length === 0) {
    return <div className={styles.empty}>No conversations yet</div>;
  }

  return (
    <>
      {list.map((c) => {
        const active = c.id === currentId;
        const label = labelFor(c);
        return (
          <div
            key={c.id}
            className={"conv-item" + (active ? " active" : "")}
            onClick={() => switchTo(c.id)}
            title={label}
          >
            <span className="conv-title">{label}</span>
            <span
              className="conv-del"
              onClick={(e) => del(c.id, e)}
              title="Delete"
            >
              <svg
                width="10"
                height="10"
                viewBox="0 0 10 10"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
              >
                <line x1="2" y1="2" x2="8" y2="8" />
                <line x1="8" y1="2" x2="2" y2="8" />
              </svg>
            </span>
          </div>
        );
      })}
      <div className={styles.clearAll} onClick={clearAll}>
        Clear all
      </div>
    </>
  );
}
