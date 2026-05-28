"use client";

/**
 * Sessions list (the "Recents" panel in the sidebar).
 *
 * Reads conversations from `window.conversations` via `useWindowGlobals`
 * — the legacy `init.js` is what populates that global from the
 * sessions_list / history_list WS events, so we piggy-back on it
 * instead of duplicating the WS handling here. Once the WSProvider
 * (which writes to useSessionStore) is wired into the layout, this
 * hook should switch to a store subscription.
 */

import { useEffect, useRef, useState } from "react";
import { useRouter, usePathname } from "next/navigation";
import { useWindowGlobals, useCurrentSessionId } from "./use-window-globals";
import { useSessionStore } from "@/lib/session-store";
import { useTranslation } from "@/lib/i18n";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import styles from "./sidebar.module.css";

interface SessionWindow {
  ws?: WebSocket;
  conversations?: Record<string, unknown>;
  currentSessionId?: string | null;
  newSession?: () => void;
}

function wsSend(payload: unknown): void {
  const w = window as unknown as SessionWindow;
  if (w.ws && w.ws.readyState === WebSocket.OPEN) {
    w.ws.send(JSON.stringify(payload));
  }
}

/** Modal confirm — shadcn <Dialog>. */
function ConfirmDialog({
  title,
  message,
  onConfirm,
  onCancel,
}: {
  title: string;
  message: string;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation();

  return (
    <Dialog
      open
      onOpenChange={(o) => {
        if (!o) onCancel();
      }}
    >
      <DialogContent
        className="max-w-[400px] border-0"
        onOpenAutoFocus={(e) => e.preventDefault()}
      >
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{message}</DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button
            variant="secondary"
            onClick={onCancel}
            className="rounded-full bg-[var(--bg-selected)] text-[var(--text-bright)] transition-[filter] hover:bg-[var(--bg-selected)] hover:brightness-125"
          >
            {t("sidebar.cancel")}
          </Button>
          <Button
            variant="destructive"
            onClick={onConfirm}
            className="rounded-full hover:bg-[#c9413a]"
          >
            {t("sidebar.delete")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

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

function labelFor(c: LegacyConv, untitled: string): string {
  const prefix = channelPrefix(c.channel, c.account_id);
  let real = displayTitle(c);
  if (!real && c.preview) {
    const pv = String(c.preview).trim();
    real = pv.length > 30 ? pv.slice(0, 30) + "…" : pv;
  }
  if (prefix && real) return prefix + ": " + real;
  if (prefix) return prefix;
  if (real) return real;
  return c.title || untitled;
}

export function SessionsList() {
  const router = useRouter();
  const pathname = usePathname();
  const { t, locale } = useTranslation();
  const { conversations } = useWindowGlobals();
  const currentId = useCurrentSessionId();
  // Per-session running map drives the breathing colored indicator
  // on each conversation row — the visual "this session is still
  // processing" cue so the user can fan out work across sessions
  // and see at a glance which ones are working.
  const runningTasks = useSessionStore((s) => s.runningTasks);

  // 没有 created_at 的会话视为"刚刚创建" (now), 让新建会话立刻
  // 出现在顶部, 而不是因为 fallback=0 沉到最底.
  const nowTs = Date.now() / 1000;
  const list = Object.values(conversations)
    .sort((a, b) => (b.created_at || nowTs) - (a.created_at || nowTs));

  function switchTo(id: string) {
    if (id === currentId && pathname === "/s/" + id) return;
    router.push("/s/" + id);
  }

  const [confirm, setConfirm] = useState<{
    title: string;
    message: string;
    run: () => void;
  } | null>(null);

  function del(id: string, e: React.MouseEvent) {
    e.stopPropagation();
    const conv = conversations[id] as { title?: string } | undefined;
    const title = conv?.title || t("sidebar.untitled");
    setConfirm({
      title: t("sidebar.delete_chat"),
      message: locale === "zh"
        ? `确定要删除「${title}」吗？`
        : `Are you sure you want to delete "${title}"?`,
      run: () => {
        const w = window as unknown as SessionWindow;
        wsSend({ action: "delete_session", session_id: id });
        if (w.conversations) delete w.conversations[id];
        if (w.currentSessionId === id) w.newSession?.();
      },
    });
  }

  function clearAll() {
    const count = Object.keys(conversations).length;
    if (!count) return;
    setConfirm({
      title: t("sidebar.delete_all_chats"),
      message: locale === "zh"
        ? `确定要删除全部 ${count} 个会话吗？${t("sidebar.delete_all_irreversible")}`
        : `Are you sure you want to delete all ${count} conversations? ${t("sidebar.delete_all_irreversible")}`,
      run: () => {
        const w = window as unknown as SessionWindow;
        wsSend({ action: "clear_sessions" });
        if (w.conversations) {
          for (const k of Object.keys(w.conversations)) delete w.conversations[k];
        }
        w.newSession?.();
      },
    });
  }

  if (list.length === 0) {
    return <div className={styles.empty}>{t("sidebar.no_conversations")}</div>;
  }

  return (
    <>
      {list.map((c) => {
        const active = c.id === currentId;
        return (
          <ConvItem
            key={c.id}
            label={labelFor(c, t("sidebar.untitled"))}
            active={active}
            running={!!runningTasks[c.id]}
            onClick={() => switchTo(c.id)}
            onDelete={(e) => del(c.id, e)}
          />
        );
      })}
      <div className={styles.clearAll} onClick={clearAll}>
        {t("sidebar.clear_all")}
      </div>
      {confirm ? (
        <ConfirmDialog
          title={confirm.title}
          message={confirm.message}
          onCancel={() => setConfirm(null)}
          onConfirm={() => {
            confirm.run();
            setConfirm(null);
          }}
        />
      ) : null}
    </>
  );
}

/* Single row in the conversation list. Mirrors the legacy
   `.conv-item / .conv-title / .conv-del` triplet from 03-settings.css:
   32px-tall row with a title that fades on the right on hover so the
   absolutely-positioned delete button doesn't visually collide. */
function ConvItem({
  label,
  active,
  running,
  onClick,
  onDelete,
}: {
  label: string;
  active: boolean;
  running: boolean;
  onClick: () => void;
  onDelete: (e: React.MouseEvent) => void;
}) {
  const { t } = useTranslation();
  // Pixel values are explicit (not `h-8`, `px-2`, etc.) because this
  // project's `html { font-size: 14px }` makes Tailwind's rem-based
  // scale 0.875× off — see the same note in FavoritesList.
  const base =
    "group relative flex h-[32px] shrink-0 cursor-pointer items-center" +
    " gap-[12px] overflow-hidden rounded-[6px] px-[8px] py-[6px]" +
    " text-fs-base leading-[20px] whitespace-nowrap" +
    " transition-colors duration-150 ease-out hover:bg-bg-hover";
  const colorCls = active
    ? "bg-bg-hover text-text-bright"
    : "text-text-primary";
  // The legacy `.conv-item:hover .conv-title` rule swaps the
  // text-overflow from ellipsis (rest) to clip + a fade-out gradient
  // mask so the delete button has visual headroom. Express the same
  // via group-hover arbitrary utilities — Tailwind has no built-in
  // for `mask-image` gradients.
  const maskOnHover =
    "group-hover:[text-overflow:clip]" +
    " group-hover:[-webkit-mask-image:linear-gradient(to_right,#000_78%,transparent_95%)]" +
    " group-hover:[mask-image:linear-gradient(to_right,#000_78%,transparent_95%)]" +
    " group-focus-within:[text-overflow:clip]" +
    " group-focus-within:[-webkit-mask-image:linear-gradient(to_right,#000_78%,transparent_95%)]" +
    " group-focus-within:[mask-image:linear-gradient(to_right,#000_78%,transparent_95%)]";
  // 两阶段动画状态:
  //   running=true       → .convRunning (彩色无缝循环 + 呼吸)
  //   刚 running→false   → .convFinishing (wipe 1.1s 从右往左擦)
  //   wipe 结束          → 普通样子
  // 用 ref 记上一帧 running, useEffect 检测 true→false 边沿触发.
  const prevRunning = useRef(running);
  const [finishing, setFinishing] = useState(false);
  useEffect(() => {
    if (prevRunning.current && !running) {
      setFinishing(true);
      const t = setTimeout(() => setFinishing(false), 1200);
      prevRunning.current = running;
      return () => clearTimeout(t);
    }
    prevRunning.current = running;
  }, [running]);

  // convRunning / convFinishing are declared as :global in
  // sidebar.module.css so the @keyframes name references stay unmangled
  // (Turbopack's CSS-module pass mangles both keyframe declarations and
  // animation: references, which broke the running indicator). Reference
  // by plain string literal instead of styles.* — see the comment block
  // in sidebar.module.css for the full story.
  const stateCls = running
    ? "convRunning"
    : finishing
      ? "convFinishing"
      : "";
  return (
    <div
      className={`${base} ${colorCls} ${stateCls}`}
      onClick={onClick}
      title={running ? `${label} (${t("sidebar.running")})` : label}
    >
      <span
        className={`flex-1 overflow-hidden truncate text-fs-base leading-[20px] ${maskOnHover}`}
      >
        {label}
      </span>
      <span
        // Fade the delete button in/out on the same 300ms curve as
        // the row's background. `display: none → flex` (the legacy
        // approach) is instant, so the X used to pop in before the
        // hover background had time to fade in. Using
        // `opacity + pointer-events` keeps it visible only on
        // hover (no pointer events when transparent) and lets
        // `transition-opacity` smooth the appearance.
        className="absolute right-[6px] top-1/2 flex size-[20px] -translate-y-1/2
          items-center justify-center rounded-[4px] text-[12px]
          leading-none text-text-muted
          opacity-0 pointer-events-none
          transition-opacity duration-150 ease-out
          group-hover:opacity-100 group-hover:pointer-events-auto
          hover:!bg-accent-red hover:!text-white"
        onClick={onDelete}
        title={t("sidebar.delete")}
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
}
