"use client";

/**
 * Sessions list (the "Recents" panel in the sidebar).
 *
 * Reads conversations from `window.conversations` via `useWindowGlobals`
 * (populated by the runtime-bridge from the `sessions_list` WS event)
 * and layers Claude.ai-style management on top:
 *   - per-row right-click / ⋯ context menu (rename, pin, move to group,
 *     copy link, archive, delete) — see `conv-menu.tsx`
 *   - Recents-header filter (status / group-by / sort) — see
 *     `recents-filter.tsx`, read here via `useRecentsView`
 *
 * Flags (pinned / archived / group) and renames are persisted server-
 * side (meta.json) through WS actions; we optimistically patch
 * `window.conversations` + bump a local tick for instant feedback,
 * since `useWindowGlobals`'s poll doesn't notice in-entry mutations.
 */

import { useEffect, useRef, useState } from "react";
import { useRouter, usePathname } from "next/navigation";
import { useWindowGlobals, useCurrentSessionId } from "./use-window-globals";
import { useSessionStore } from "@/lib/session-store";
import { useTranslation } from "@/lib/i18n";
import { useRecentsView } from "@/lib/recents-view";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Popover,
  PopoverAnchor,
  PopoverContent,
} from "@/components/ui/popover";
import {
  ChevronDownIcon,
  type AnimatedNavIconHandle,
} from "@/components/animated-icons";
import { Button } from "@/components/ui/button";
import { ConvMenu } from "./conv-menu";
import { RecentsFilter } from "./recents-filter";
import styles from "./sidebar.module.css";

interface SessionWindow {
  ws?: WebSocket;
  conversations?: Record<string, LegacyConv>;
  currentSessionId?: string | null;
  newSession?: () => void;
  renderSessions?: () => void;
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
  pinned?: boolean;
  archived?: boolean;
  group?: string;
  /** Project path the conversation lives under. Backend-fed (the cwd
   *  where an OpenProgram project was activated); absent until then. */
  project?: string;
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
  const runningTasks = useSessionStore((s) => s.runningTasks);
  const view = useRecentsView();

  // Bumped after every optimistic mutation of `window.conversations`
  // so the list re-renders immediately (the useWindowGlobals poll
  // ignores in-entry field changes).
  const [, setTick] = useState(0);
  const bump = () => setTick((n) => n + 1);

  // Collapsed group names (only relevant when grouping is on).
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(new Set());
  // Transient "Link copied" toast.
  const [toast, setToast] = useState<string | null>(null);
  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  function showToast(msg: string) {
    setToast(msg);
    if (toastTimer.current) clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast(null), 1500);
  }
  useEffect(() => () => { if (toastTimer.current) clearTimeout(toastTimer.current); }, []);

  function switchTo(id: string) {
    if (id === currentId && pathname === "/s/" + id) return;
    router.push("/s/" + id);
  }

  const [confirm, setConfirm] = useState<{
    title: string;
    message: string;
    run: () => void;
  } | null>(null);

  /* ---- action senders (optimistic patch + WS) ------------------- */

  function patchConv(id: string, fields: Partial<LegacyConv>) {
    const w = window as unknown as SessionWindow;
    const conv = w.conversations?.[id];
    if (conv) Object.assign(conv, fields);
    bump();
  }

  function renameSession(id: string, title: string) {
    const clean = title.trim();
    if (!clean) return;
    patchConv(id, { title: clean });
    wsSend({ action: "rename_session", session_id: id, title: clean });
  }
  function setFlags(id: string, fields: { pinned?: boolean; archived?: boolean; group?: string }) {
    patchConv(id, fields);
    wsSend({ action: "update_session_flags", session_id: id, ...fields });
  }
  function copyLink(id: string) {
    const url = `${location.origin}/s/${id}`;
    navigator.clipboard?.writeText(url).then(
      () => showToast(t("sidebar.link_copied")),
      () => showToast(url),
    );
  }
  function del(id: string) {
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
        bump();
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
        bump();
      },
    });
  }

  /* ---- filter / sort / group ------------------------------------ */

  // NOTE: deliberately NOT memoised on `conversations`. The legacy
  // code mutates `window.conversations` IN PLACE (same object ref), so
  // a `useMemo([conversations])` would return a stale cached result
  // after the WS populate / a flag change — the list would render
  // empty even though the map has entries. The list is small, so
  // recomputing every render is free and always correct. `useTick`
  // (bumped on every optimistic action) + the useWindowGlobals content
  // signature both guarantee a render when the data actually changes.
  const nowTs = Date.now() / 1000;
  const convArr = Object.values(conversations) as LegacyConv[];

  const allGroups = (() => {
    const s = new Set<string>();
    for (const c of convArr) if (c.group) s.add(c.group);
    return Array.from(s).sort((a, b) => a.localeCompare(b));
  })();

  const visible = (() => {
    let arr = convArr;
    if (view.status === "active") arr = arr.filter((c) => !c.archived);
    else if (view.status === "archived") arr = arr.filter((c) => !!c.archived);
    // Last-activity window (uses created_at; swap to updated_at when the
    // backend tracks it). "all" = no window.
    if (view.lastActivity !== "all") {
      const days = view.lastActivity === "1d" ? 1 : view.lastActivity === "7d" ? 7 : 30;
      const cutoff = nowTs - days * 86400;
      arr = arr.filter((c) => (c.created_at || 0) >= cutoff);
    }
    // NOTE: project / environment filters are UI-only for now — the
    // menu writes the pref but there's no per-conversation project /
    // environment field yet. Wire the filter here once the backend
    // supplies those fields.
    const cmp = (a: LegacyConv, b: LegacyConv) => {
      if (!!a.pinned !== !!b.pinned) return a.pinned ? -1 : 1;
      if (view.sort === "title") {
        return labelFor(a, "").localeCompare(labelFor(b, ""));
      }
      return (b.created_at || nowTs) - (a.created_at || nowTs);
    };
    return [...arr].sort(cmp);
  })();

  function toggleGroupCollapse(name: string) {
    setCollapsedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  }

  if (Object.keys(conversations).length === 0) {
    return <div className={styles.empty}>{t("sidebar.no_conversations")}</div>;
  }

  const renderRow = (c: LegacyConv) => (
    <ConvItem
      key={c.id}
      conv={c}
      label={labelFor(c, t("sidebar.untitled"))}
      active={c.id === currentId}
      running={!!runningTasks[c.id]}
      groups={allGroups}
      onClick={() => switchTo(c.id)}
      onRename={(title) => renameSession(c.id, title)}
      onTogglePin={() => setFlags(c.id, { pinned: !c.pinned })}
      onToggleArchive={() => setFlags(c.id, { archived: !c.archived })}
      onMoveToGroup={(g) => setFlags(c.id, { group: g })}
      onCopyLink={() => copyLink(c.id)}
      onDelete={() => del(c.id)}
    />
  );

  // Build the sectioned list. Each section is { key, label, items };
  // an empty label renders a flat (header-less) run of rows. Mirrors
  // Claude's rich Recents logic — date buckets by default, Working /
  // Completed when grouping by state, project paths when grouping by
  // project.
  const isWorking = (id: string) => !!runningTasks[id];
  const sections = buildSections(visible, {
    groupBy: view.groupBy,
    sort: view.sort,
    nowTs,
    isWorking,
    labels: {
      pinned: t("sidebar.pinned"),
      today: t("sidebar.today"),
      yesterday: t("sidebar.yesterday"),
      older: t("sidebar.older"),
      working: t("sidebar.working"),
      completed: t("sidebar.completed"),
      ungrouped: t("sidebar.ungrouped"),
    },
    dateLocale: locale === "zh" ? "zh-CN" : undefined,
  });

  // The filter button rides on the FIRST section header's right (Claude
  // layout — no separate "Recents" bar). When the list is flat (title
  // sort → one header-less section) or empty, a thin right-aligned row
  // carries it instead so it's always reachable.
  // Every labelled section is collapsible — date buckets (Today /
  // Yesterday / …) just as much as State / Project — each with the
  // small right-side ⌄ toggle. (A flat title-sort run has no header,
  // so nothing to collapse there.)
  const collapsible = true;
  const firstHasHeader = sections.length > 0 && sections[0].label !== "";
  const body = (
    <>
      {!firstHasHeader ? (
        <div className="flex h-[24px] items-center justify-end px-[8px]">
          <RecentsFilter />
        </div>
      ) : null}
      {sections.map((sec, i) =>
        sec.label === "" ? (
          // Flat run (title sort, no grouping) — no header.
          <div key={sec.key}>{sec.items.map(renderRow)}</div>
        ) : (
          // group/sec → hovering anywhere in the section reveals its
          // collapse chevron (hidden otherwise).
          <div key={sec.key} className="group/sec">
            <GroupHeader
              name={sec.label}
              collapsible={collapsible}
              collapsed={collapsedGroups.has(sec.key)}
              onToggle={() => toggleGroupCollapse(sec.key)}
              actions={i === 0 ? <RecentsFilter /> : undefined}
            />
            {(!collapsible || !collapsedGroups.has(sec.key)) &&
              sec.items.map(renderRow)}
          </div>
        ),
      )}
    </>
  );

  return (
    <>
      {body}
      {visible.length === 0 ? (
        <div className={styles.empty}>{t("sidebar.no_conversations")}</div>
      ) : null}
      <div className={styles.clearAll} onClick={clearAll}>
        {t("sidebar.clear_all")}
      </div>
      {toast ? (
        <div
          className="pointer-events-none fixed bottom-[80px] left-1/2 z-[200] -translate-x-1/2
            rounded-full bg-[var(--bg-tertiary)] px-3 py-1.5 text-[12px]
            text-[var(--text-bright)] shadow-[var(--shadow-popover)] border border-[var(--border)]"
        >
          {toast}
        </div>
      ) : null}
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

/* ---- sectioning --------------------------------------------------
 * Turns the filtered+sorted conversation list into labelled sections,
 * mirroring Claude's Recents:
 *   - groupBy "state"   → Working (a task running) / Completed
 *   - groupBy "project" → one section per project path (+ Ungrouped)
 *   - groupBy "none"    → date buckets when sorted by recency
 *                         (Pinned / Today / Yesterday / <date> / Older),
 *                         or a single flat header-less run when sorted
 *                         by title
 * `items` keep the incoming order (already pinned-first + sorted), so
 * date buckets come out most-recent-first via insertion order.
 */
interface Section {
  key: string;
  label: string; // "" → flat, no header
  items: LegacyConv[];
}
interface SectionOpts {
  groupBy: "none" | "state" | "project";
  sort: "recency" | "title";
  nowTs: number;
  isWorking: (id: string) => boolean;
  labels: {
    pinned: string;
    today: string;
    yesterday: string;
    older: string;
    working: string;
    completed: string;
    ungrouped: string;
  };
  dateLocale?: string;
}

function _dateBucket(
  ts: number,
  nowTs: number,
  labels: SectionOpts["labels"],
  dateLocale?: string,
): { key: string; label: string } {
  const d = new Date((ts || nowTs) * 1000);
  const now = new Date(nowTs * 1000);
  const day = new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const diff = Math.round((today - day) / 86_400_000);
  if (diff <= 0) return { key: "b0_today", label: labels.today };
  if (diff === 1) return { key: "b1_yesterday", label: labels.yesterday };
  if (diff <= 30) {
    const sameYear = d.getFullYear() === now.getFullYear();
    return {
      key: "b2_" + day,
      label: d.toLocaleDateString(dateLocale, {
        month: "short",
        day: "numeric",
        ...(sameYear ? {} : { year: "numeric" }),
      }),
    };
  }
  return { key: "b9_older", label: labels.older };
}

function buildSections(visible: LegacyConv[], o: SectionOpts): Section[] {
  if (o.groupBy === "state") {
    const working = visible.filter((c) => o.isWorking(c.id));
    const done = visible.filter((c) => !o.isWorking(c.id));
    return [
      { key: "st_working", label: o.labels.working, items: working },
      { key: "st_completed", label: o.labels.completed, items: done },
    ].filter((s) => s.items.length > 0);
  }

  if (o.groupBy === "project") {
    const byProject = new Map<string, LegacyConv[]>();
    const ungrouped: LegacyConv[] = [];
    for (const c of visible) {
      if (c.project) {
        (byProject.get(c.project) ?? byProject.set(c.project, []).get(c.project)!).push(c);
      } else ungrouped.push(c);
    }
    const names = Array.from(byProject.keys()).sort((a, b) => a.localeCompare(b));
    const out: Section[] = names.map((n) => ({
      key: "pj_" + n,
      label: n,
      items: byProject.get(n)!,
    }));
    if (ungrouped.length) {
      out.push({ key: "pj__ungrouped", label: o.labels.ungrouped, items: ungrouped });
    }
    return out;
  }

  // groupBy "none"
  if (o.sort === "title") {
    // Flat alphabetical run, no headers.
    return [{ key: "flat", label: "", items: visible }];
  }

  // recency → Pinned (if any) + date buckets, insertion order = recency.
  const pinned = visible.filter((c) => c.pinned);
  const rest = visible.filter((c) => !c.pinned);
  const buckets = new Map<string, Section>();
  for (const c of rest) {
    const b = _dateBucket(c.created_at || 0, o.nowTs, o.labels, o.dateLocale);
    if (!buckets.has(b.key)) buckets.set(b.key, { key: b.key, label: b.label, items: [] });
    buckets.get(b.key)!.items.push(c);
  }
  const out: Section[] = [];
  if (pinned.length) out.push({ key: "pinned", label: o.labels.pinned, items: pinned });
  out.push(...buckets.values());
  return out;
}

/* ---- group section header -------------------------------------- */

/** Section label (Today / Working / a project …) in the SAME plain,
 *  normal-weight muted style as the old "Recents" label — no weird
 *  uppercase / letter-spacing. Every section is collapsible: a small
 *  ⌄ toggle sits to the right of the label (rotates to › when folded)
 *  and the header folds on click — date buckets included. The first
 *  section also hosts the filter button, far-right. */
function GroupHeader({
  name,
  collapsible,
  collapsed,
  onToggle,
  actions,
}: {
  name: string;
  collapsible: boolean;
  collapsed: boolean;
  onToggle: () => void;
  actions?: React.ReactNode;
}) {
  // Drive the chevron's bounce from the header hover (controlled mode).
  const chevronRef = useRef<AnimatedNavIconHandle>(null);
  return (
    <div
      onClick={collapsible ? onToggle : undefined}
      onMouseEnter={() => chevronRef.current?.startAnimation()}
      onMouseLeave={() => chevronRef.current?.stopAnimation()}
      className={
        "flex select-none items-center gap-1 px-[8px] pt-[10px] pb-[2px]" +
        (collapsible ? " cursor-pointer" : "")
      }
    >
      {/* Label — 12px / 400 / text-secondary @ 80%, matching Claude's
          section labels. Brightens to white together with the chevron
          when the section is hovered. */}
      <span
        className="truncate text-[12px] font-normal text-[var(--text-secondary)]
          opacity-80 transition-colors group-hover/sec:text-[var(--text-bright)]
          group-hover/sec:opacity-100"
      >
        {name}
      </span>
      {collapsible ? (
        // Animated chevron from the shared icon set (pqoqubbw / framer-
        // motion). Hidden until the section is hovered, bounces on
        // hover, brightens to white with the label, bottom-aligned to
        // the text line, rotates to › when collapsed.
        <ChevronDownIcon
          ref={chevronRef}
          size={12}
          className="self-end shrink-0 text-[var(--text-secondary)] opacity-0
            transition-[opacity,color] duration-150
            group-hover/sec:opacity-100 group-hover/sec:text-[var(--text-bright)]"
          style={{ transform: collapsed ? "rotate(-90deg)" : "rotate(0deg)" }}
        />
      ) : null}
      {actions ? (
        <span
          className="ml-auto flex items-center"
          onClick={(e) => e.stopPropagation()}
        >
          {actions}
        </span>
      ) : null}
    </div>
  );
}

/* ---- leading status marker ------------------------------------- */

/** The dot to the left of a conversation title, mirroring Claude:
 *   - pinned  → an amber pin
 *   - working → three pulsing dots (a task is running)
 *   - idle    → a hollow ring
 *  Fixed 14px slot so titles stay aligned regardless of state. */
function StatusMarker({
  pinned,
  working,
  pinnedLabel,
  workingLabel,
}: {
  pinned: boolean;
  working: boolean;
  pinnedLabel: string;
  workingLabel: string;
}) {
  if (pinned) {
    return (
      <svg
        className="shrink-0 text-[var(--accent-orange)]"
        width="12" height="12" viewBox="0 0 16 16" fill="currentColor"
        aria-label={pinnedLabel}
      >
        <path d="M9.5 1.5a1 1 0 0 0-1.7.7l.1 3.2-2.4 2.4a1 1 0 0 0-.3.7v.5l2.6-.0 0 4 .8 1.3.8-1.3 0-4 2.6.0v-.5a1 1 0 0 0-.3-.7L9.5 5.4l.1-3.2a1 1 0 0 0-.1-.7z" />
      </svg>
    );
  }
  if (working) {
    return (
      <span
        className="flex w-[14px] shrink-0 items-center justify-center gap-[2px]"
        aria-label={workingLabel}
        title={workingLabel}
      >
        {[0, 1, 2].map((i) => (
          <span
            key={i}
            className="h-[3px] w-[3px] rounded-full bg-[var(--text-secondary)] animate-pulse"
            style={{ animationDelay: `${i * 0.15}s` }}
          />
        ))}
      </span>
    );
  }
  return (
    <span
      className="block size-[7px] shrink-0 rounded-full border border-[var(--text-muted)]"
      aria-hidden="true"
    />
  );
}

/* ---- single conversation row ----------------------------------- */

function ConvItem({
  conv,
  label,
  active,
  running,
  groups,
  onClick,
  onRename,
  onTogglePin,
  onToggleArchive,
  onMoveToGroup,
  onCopyLink,
  onDelete,
}: {
  conv: LegacyConv;
  label: string;
  active: boolean;
  running: boolean;
  groups: string[];
  onClick: () => void;
  onRename: (title: string) => void;
  onTogglePin: () => void;
  onToggleArchive: () => void;
  onMoveToGroup: (group: string) => void;
  onCopyLink: () => void;
  onDelete: () => void;
}) {
  const { t } = useTranslation();
  const [menuOpen, setMenuOpen] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [draft, setDraft] = useState(label);
  const inputRef = useRef<HTMLInputElement>(null);

  const base =
    "group relative flex h-[var(--ui-list-h)] shrink-0 cursor-pointer items-center" +
    " gap-[8px] overflow-hidden rounded-[var(--ui-list-radius)] px-[8px] py-[6px]" +
    " text-fs-base leading-[20px] whitespace-nowrap" +
    " transition-colors duration-150 ease-out hover:bg-bg-hover";
  const colorCls = active ? "bg-bg-hover text-text-bright" : "text-text-primary";
  const maskOnHover =
    "group-hover:[text-overflow:clip]" +
    " group-hover:[-webkit-mask-image:linear-gradient(to_right,#000_70%,transparent_92%)]" +
    " group-hover:[mask-image:linear-gradient(to_right,#000_70%,transparent_92%)]";

  // running → finishing edge animation (unchanged from before).
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
  const stateCls = running ? "convRunning" : finishing ? "convFinishing" : "";

  function startRename() {
    setDraft(conv.title || label);
    setRenaming(true);
    requestAnimationFrame(() => {
      inputRef.current?.focus();
      inputRef.current?.select();
    });
  }
  function commitRename() {
    setRenaming(false);
    const v = draft.trim();
    if (v && v !== (conv.title || "")) onRename(v);
  }

  function newGroup() {
    const name = window.prompt(t("sidebar.new_group_prompt"));
    if (name && name.trim()) onMoveToGroup(name.trim());
  }

  return (
    <Popover open={menuOpen} onOpenChange={setMenuOpen}>
      <div
        className={`${base} ${colorCls} ${stateCls}`}
        onClick={renaming ? undefined : onClick}
        onContextMenu={(e) => {
          e.preventDefault();
          setMenuOpen(true);
        }}
        title={running ? `${label} (${t("sidebar.running")})` : label}
      >
        {/* Leading status marker (Claude-style): pinned → pin,
            else a running task → animated dots, else an idle ring. */}
        <StatusMarker
          pinned={!!conv.pinned}
          working={running}
          pinnedLabel={t("sidebar.pinned")}
          workingLabel={t("sidebar.running")}
        />

        {renaming ? (
          <input
            ref={inputRef}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onClick={(e) => e.stopPropagation()}
            onBlur={commitRename}
            onKeyDown={(e) => {
              if (e.key === "Enter") { e.preventDefault(); commitRename(); }
              else if (e.key === "Escape") { e.preventDefault(); setRenaming(false); }
            }}
            className="flex-1 min-w-0 rounded-[4px] border border-[var(--accent-orange)]
              bg-[var(--bg-input)] px-[6px] py-[2px] text-fs-base leading-[18px]
              text-text-bright outline-none"
          />
        ) : (
          <span className={`flex-1 overflow-hidden truncate text-fs-base leading-[20px] ${maskOnHover}`}>
            {label}
          </span>
        )}

        {/* ⋯ button — hover-visible; anchors the menu. */}
        <PopoverAnchor asChild>
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              setMenuOpen((v) => !v);
            }}
            aria-label={t("sidebar.filter")}
            className="absolute right-[4px] top-1/2 flex size-[22px] -translate-y-1/2
              items-center justify-center rounded-[5px] text-text-muted
              opacity-0 pointer-events-none transition-opacity duration-150 ease-out
              group-hover:opacity-100 group-hover:pointer-events-auto
              data-[state=open]:opacity-100 data-[state=open]:pointer-events-auto
              hover:bg-[var(--bg-selected)] hover:text-text-bright"
            data-state={menuOpen ? "open" : "closed"}
          >
            {/* Vertical ⋮ (matches Claude's row menu trigger). */}
            <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
              <circle cx="8" cy="3" r="1.4" />
              <circle cx="8" cy="8" r="1.4" />
              <circle cx="8" cy="13" r="1.4" />
            </svg>
          </button>
        </PopoverAnchor>
      </div>

      <PopoverContent
        align="start"
        side="bottom"
        sideOffset={4}
        className="w-auto border border-[var(--border)] bg-[var(--bg-tertiary)] p-0
          text-[var(--text-primary)] shadow-[var(--shadow-popover)]"
        onClick={(e) => e.stopPropagation()}
      >
        <ConvMenu
          conv={conv}
          groups={groups}
          onRename={startRename}
          onTogglePin={onTogglePin}
          onToggleArchive={onToggleArchive}
          onMoveToGroup={onMoveToGroup}
          onNewGroup={newGroup}
          onCopyLink={onCopyLink}
          onDelete={onDelete}
          onClose={() => setMenuOpen(false)}
        />
      </PopoverContent>
    </Popover>
  );
}
