"use client";

/**
 * The phone's first two pages. Projects (/chats): one row per project with
 * its chat count, latest chat and how many wait on the user, so the first
 * page stays short however many chats there are. A project
 * (/chats?project=<id>): its chats, newest first, a chat waiting on the
 * user first and saying so. Searching on Projects finds chats in every
 * project. The box at the bottom starts a new chat, in the open project
 * when there is one. Projects come from the same registry as the desktop
 * sidebar's project groups (lib/projects/).
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ChevronRight, Pin, Search, Settings } from "lucide-react";

import { useSessionActions } from "@/components/chat/pane/session-actions-menu";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { MENU_ITEM_STATES, MENU_PANEL, itemCls } from "@/components/ui/menu/menu-styles";

import { AgentMark } from "@/components/chat/top-bar/chips/model-mark";
import { activityState, type ActivityState } from "@/components/sidebar/sessions/activity";
import { displayTitle, wsSend, type LegacyConv } from "@/components/sidebar/sessions/helpers";
import { useTranslation } from "@/lib/i18n";
import { callMobile, haptic, mobileBridge } from "@/lib/mobile/bridge";
import { navigate } from "@/lib/nav/navigate";
import { listSessionProjects } from "@/lib/projects/list-projects";
import { projectGroups, type ProjectGroupSource } from "@/lib/projects/project-groups";
import { useSessionStore } from "@/lib/session-store";
import { openNewChat, openSession } from "@/lib/tabs/navigation/open-session";
import { cn } from "@/lib/utils";

import { PhoneNavBar } from "./phone-nav-bar";
import { pushUrl } from "./phone-nav";

type Row = { conv: LegacyConv; state: ActivityState | null };
/** `registered`: `id` is a registry project's id, not a name from the
 *  chats before the registry answered. */
type Group = { id: string; name: string; rows: Row[]; waiting: number; latest: number; registered: boolean };

const time = (conv: LegacyConv) => conv.updated_at || conv.created_at || 0;

/** Today: the time; this week: the weekday; else the date. */
function when(ts: number | undefined, locale: string): string {
  if (!ts) return "";
  const date = new Date(ts < 1e12 ? ts * 1000 : ts);
  const now = new Date();
  const days = Math.floor((new Date(now.toDateString()).getTime() - new Date(date.toDateString()).getTime()) / 86_400_000);
  const lang = locale === "zh" ? "zh-CN" : undefined;
  if (days <= 0) return date.toLocaleTimeString(lang, { hour: "2-digit", minute: "2-digit" });
  if (days < 7) return date.toLocaleDateString(lang, { weekday: "short" });
  return date.toLocaleDateString(lang, { month: "numeric", day: "numeric" });
}

/** The project registry, read again when chats come or go, a binding
 *  changes (`project-changed`) or the connection opens. */
function useProjects(): ProjectGroupSource[] {
  const [projects, setProjects] = useState<ProjectGroupSource[]>([]);
  const ids = useSessionStore((s) => Object.keys(s.conversations).sort().join(","));
  const open = useSessionStore((s) => s.wsStatus === "open");
  const read = useCallback(() => {
    void listSessionProjects<{ projects: ProjectGroupSource[]; session_id?: string | null }>("")
      .then((data) => { if (data?.projects) setProjects(data.projects); });
  }, []);
  useEffect(() => { if (open) read(); }, [open, ids, read]);
  useEffect(() => {
    window.addEventListener("project-changed", read);
    return () => window.removeEventListener("project-changed", read);
  }, [read]);
  return projects;
}

/** Chats grouped by project, waiting ones first in each. */
function useGroups(): Group[] {
  const conversations = useSessionStore((s) => s.conversations);
  const runningTasks = useSessionStore((s) => s.runningTasks);
  const projects = useProjects();
  return useMemo(() => {
    const convs = (Object.values(conversations) as LegacyConv[]).filter((conv) => !conv.archived);
    const rank = (row: Row) => row.state === "needs_input" ? 0 : row.conv.pinned ? 1 : 2;
    const toGroup = (id: string, name: string, items: LegacyConv[], registered: boolean): Group => {
      const rows = items.map((conv) => ({ conv, state: activityState(conv, !!runningTasks[conv.id]) }));
      rows.sort((a, b) => rank(a) - rank(b) || time(b.conv) - time(a.conv));
      return {
        id,
        name,
        rows,
        waiting: rows.filter((row) => row.state === "needs_input").length,
        latest: Math.max(0, ...items.map(time)),
        registered,
      };
    };
    let groups: Group[];
    if (projects.length) {
      groups = projectGroups(projects, convs, [], { sort: "recency" }).map((g) => toGroup(g.key, g.name, g.items, true));
    } else {
      // Before the registry answers, the chats' own project names.
      const byName = new Map<string, LegacyConv[]>();
      for (const conv of convs) byName.set(conv.project || "", [...(byName.get(conv.project || "") ?? []), conv]);
      groups = [...byName.entries()].map(([name, items]) => toGroup(name, name, items, false));
    }
    return groups.sort((a, b) => Number(b.waiting > 0) - Number(a.waiting > 0) || b.latest - a.latest);
  }, [conversations, runningTasks, projects]);
}

function stateWord(state: ActivityState | null, text: (en: string, zh: string) => string): string | null {
  if (state === "needs_input") return text("Waiting for you", "等你确认");
  if (state === "working") return text("Running", "运行中");
  if (state === "failed") return text("Failed", "失败");
  if (state === "unread") return text("Done", "已完成");
  return null;
}

/** Archive a chat from its row, as the sidebar's menu does: the store at
 *  once, then the server. */
function archiveChat(id: string) {
  const store = useSessionStore.getState();
  const prev = store.conversations[id];
  if (prev) store.upsertConversation({ ...prev, archived: true, id });
  wsSend({ action: "update_session_flags", session_id: id, archived: true });
}

const ACTION_W = 84; // the revealed Archive button
const HOLD_MS = 450;
/** The one row whose Archive is showing; opening another closes it. */
let closeOpenRow: (() => void) | null = null;

/** A chat's row. Its state sits where the time is and keeps that slot's
 *  width, so the title does not move when it changes (Happy's corner):
 *  "Waiting" while it waits for the person, a pulsing dot while it runs,
 *  a dot for a result not seen yet. A swipe to the left shows Archive,
 *  which takes a tap (Happy: no full swipe); a long press opens the
 *  chat's menu (Paseo, 450ms). */
function ChatRow({ row, onMenu }: { row: Row; onMenu: (id: string) => void }) {
  const { text, locale } = useTranslation();
  const { conv, state } = row;
  const slide = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const shown = useRef(0);
  const swallowClick = useRef(false);

  useEffect(() => {
    const el = button.current;
    const track = slide.current;
    if (!el || !track) return;
    let x0 = 0;
    let y0 = 0;
    let base = 0;
    let axis: "x" | "y" | null = null;
    let timer = 0;
    const row = track.parentElement!;
    const reveal = (on: boolean) => { if (on) row.setAttribute("data-swiped", ""); else row.removeAttribute("data-swiped"); };
    const place = (offset: number, animate: boolean) => {
      if (offset) reveal(true);
      else if (animate) track.addEventListener("transitionend", () => { if (!shown.current) reveal(false); }, { once: true });
      else reveal(false);
      track.style.transition = animate ? "transform 260ms cubic-bezier(0.32, 0.72, 0, 1)" : "none";
      track.style.transform = offset ? `translateX(${offset}px)` : "";
      shown.current = offset;
      if (offset && closeOpenRow !== close) { closeOpenRow?.(); closeOpenRow = close; }
      if (!offset && closeOpenRow === close) closeOpenRow = null;
    };
    const close = () => place(0, true);
    const onStart = (event: TouchEvent) => {
      if (event.touches.length !== 1) return;
      x0 = event.touches[0].clientX;
      y0 = event.touches[0].clientY;
      base = shown.current;
      axis = null;
      swallowClick.current = false;
      window.clearTimeout(timer);
      timer = window.setTimeout(() => {
        timer = 0;
        swallowClick.current = true;
        haptic("medium");
        onMenu(conv.id);
      }, HOLD_MS);
    };
    const onMove = (event: TouchEvent) => {
      const dx = event.touches[0].clientX - x0;
      const dy = event.touches[0].clientY - y0;
      if (!axis && Math.hypot(dx, dy) > 8) {
        window.clearTimeout(timer);
        timer = 0;
        axis = Math.abs(dx) > Math.abs(dy) ? "x" : "y";
      }
      if (axis !== "x") return;
      event.preventDefault();
      const offset = Math.min(0, Math.max(-ACTION_W * 1.25, base + dx));
      reveal(true);
      track.style.transition = "none";
      track.style.transform = `translateX(${offset}px)`;
      shown.current = offset;
    };
    const onEnd = () => {
      window.clearTimeout(timer);
      timer = 0;
      if (axis === "x") {
        swallowClick.current = true;
        const open = shown.current < -ACTION_W / 2;
        if (open && !base) haptic("light");
        place(open ? -ACTION_W : 0, true);
      }
      axis = null;
    };
    el.addEventListener("touchstart", onStart, { passive: true });
    el.addEventListener("touchmove", onMove, { passive: false });
    el.addEventListener("touchend", onEnd);
    el.addEventListener("touchcancel", onEnd);
    return () => {
      window.clearTimeout(timer);
      if (closeOpenRow === close) closeOpenRow = null;
      el.removeEventListener("touchstart", onStart);
      el.removeEventListener("touchmove", onMove);
      el.removeEventListener("touchend", onEnd);
      el.removeEventListener("touchcancel", onEnd);
    };
  }, [conv.id, onMenu]);

  const word = stateWord(state, text);
  return (
    <div className="phone-chat-row" data-state={state ?? undefined}>
      <button
        type="button"
        className="phone-chat-row-archive"
        tabIndex={-1}
        onClick={() => { haptic("success"); archiveChat(conv.id); }}
      >
        {text("Archive", "归档")}
      </button>
      <div className="phone-chat-row-track" ref={slide}>
        <button
          ref={button}
          type="button"
          className="phone-home-row"
          onClick={() => {
            if (swallowClick.current) { swallowClick.current = false; return; }
            if (shown.current) { closeOpenRow?.(); return; }
            closeOpenRow?.();
            openSession(conv.id);
          }}
          onContextMenu={(event) => { event.preventDefault(); onMenu(conv.id); }}
        >
          <span className="phone-home-text">
            <span className="phone-home-line">
              <span className="phone-home-title">{conv.pinned ? <Pin size={12} className="phone-home-pin" aria-hidden="true" /> : null}{displayTitle(conv)}</span>
              <span className="phone-home-slot">
                {state === "needs_input" ? <span className="phone-home-badge">{text("Waiting", "等你")}</span>
                  : state ? <span className="phone-home-state" data-state={state} aria-label={word ?? undefined} />
                  : <span className="phone-home-time">{when(time(conv), locale)}</span>}
              </span>
            </span>
            <span className="phone-home-preview">
              {word ? <b data-state={state ?? undefined}>{word}</b> : null}
              {word && conv.preview ? " · " : null}
              {conv.preview}
            </span>
          </span>
        </button>
      </div>
    </div>
  );
}

/** A chat's menu from its row's long press: the same actions as the
 *  details page, for this chat (chat/pane/session-actions-menu.tsx). */
function ChatRowMenu({ id }: { id: string }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(true);
  const here = typeof window === "undefined" ? "" : window.location.pathname + window.location.search;
  const { conv, actions, dialogs } = useSessionActions(() => {
    // Deleting it must not leave the list for another chat.
    setTimeout(() => { if (window.location.pathname + window.location.search !== here) window.history.replaceState(null, "", here); }, 0);
  }, id);
  const row = (label: string, run: () => void, danger?: boolean) => (
    <DropdownMenuItem className={cn(itemCls(false), MENU_ITEM_STATES, danger && "text-[var(--accent-red)]")}
      onSelect={() => window.setTimeout(run, 0)}>{label}</DropdownMenuItem>
  );
  return (
    <>
      {/* Stays mounted after the menu closes: Rename and Delete open their
          dialogs from here. */}
      <DropdownMenu open={open} onOpenChange={setOpen}>
        <DropdownMenuTrigger asChild><span className="phone-message-menu-anchor" aria-hidden="true" /></DropdownMenuTrigger>
        <DropdownMenuContent className={MENU_PANEL} onCloseAutoFocus={(event) => event.preventDefault()}>
          {row(conv?.pinned ? t("sidebar.unpin") : t("sidebar.pin"), actions.p)}
          {row(t("sidebar.rename"), actions.r)}
          {row(t("sidebar.copy_link"), actions.c)}
          {row(conv?.archived ? t("sidebar.unarchive") : t("sidebar.archive"), actions.a)}
          {row(t("sidebar.delete"), actions.d, true)}
        </DropdownMenuContent>
      </DropdownMenu>
      {dialogs}
    </>
  );
}

function ChatRows({ rows, sections }: { rows: Row[]; sections?: boolean }) {
  const { text } = useTranslation();
  const [menu, setMenu] = useState<{ id: string; n: number } | null>(null);
  const onMenu = useCallback((id: string) => setMenu((prev) => ({ id, n: (prev?.n ?? 0) + 1 })), []);
  // A project's chats in two groups when some are live: those running or
  // waiting for the person, then the rest.
  const live = sections ? rows.filter((row) => row.state === "needs_input" || row.state === "working") : [];
  const rest = live.length ? rows.filter((row) => !live.includes(row)) : rows;
  const list = (items: Row[]) => (
    <div className="phone-app-list">
      {items.map((row) => <ChatRow key={row.conv.id} row={row} onMenu={onMenu} />)}
    </div>
  );
  return (
    <>
      {live.length ? <><h2 className="phone-app-section-title">{text("In progress", "进行中")}</h2>{list(live)}</> : null}
      {live.length && rest.length ? <h2 className="phone-app-section-title">{text("Earlier", "更早")}</h2> : null}
      {rest.length ? list(rest) : null}
      {menu ? <ChatRowMenu key={menu.n} id={menu.id} /> : null}
    </>
  );
}

/** A project's mark: its initial on a colour of its own, so projects are
 *  told apart at a glance rather than by a row of the same folder. */
const PROJECT_TONES = ["#2f6fe4", "#3d9a5b", "#8a63d2", "#c96442", "#c98a1a", "#2a9d9f", "#d4483b", "#5a6b7b"];
function projectTone(name: string): string {
  let hash = 0;
  for (const ch of name) hash = (hash * 31 + ch.codePointAt(0)!) >>> 0;
  return PROJECT_TONES[hash % PROJECT_TONES.length];
}

function SearchField({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  const { text } = useTranslation();
  return (
    <label className="phone-app-search">
      <Search size={16} aria-hidden="true" />
      <input
        type="search"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={text("Search chats", "搜索对话")}
        enterKeyHint="search"
      />
    </label>
  );
}

function NewChatDock({ projectId, name }: { projectId?: string; name?: string }) {
  const { text } = useTranslation();
  function newChat() {
    // In a project: a fresh draft bound to it until its first message, as
    // the sidebar's ＋ on a project does (sessions-list.tsx).
    const bind = (draftId: string, id: string) => {
      useSessionStore.getState().setPendingProject(draftId, id);
      window.dispatchEvent(new Event("project-changed"));
    };
    const draftId = openNewChat(projectId || name ? { fresh: true } : undefined);
    if (projectId) bind(draftId, projectId);
    else if (name) {
      // The registry had not answered when the page drew (it groups by
      // name until then): the project is found by its name now.
      void listSessionProjects<{ projects: ProjectGroupSource[]; session_id?: string | null }>("").then((data) => {
        const found = data?.projects?.find((project) => project.name === name);
        if (found) bind(draftId, found.id);
      });
    }
    pushUrl("/chat");
  }
  return (
    <div className="phone-home-dock">
      <button type="button" className="phone-home-new" onClick={newChat}>
        {name ? text(`New chat in ${name}…`, `在 ${name} 里开新对话…`) : text("Start a new chat…", "开一个新对话…")}
      </button>
    </div>
  );
}

export function PhoneHome({ projectId }: { projectId?: string | null }) {
  return projectId ? <PhoneProject projectId={projectId} /> : <PhoneProjects />;
}

function PhoneProjects() {
  const { text, locale } = useTranslation();
  const groups = useGroups();
  const socket = useSessionStore((s) => s.wsStatus);
  const [query, setQuery] = useState("");
  const computer = mobileBridge()?.computer;
  const q = query.trim().toLowerCase();
  const found = useMemo(() => q
    ? groups.flatMap((g) => g.rows).filter((row) => displayTitle(row.conv).toLowerCase().includes(q))
      .sort((a, b) => time(b.conv) - time(a.conv))
    : [], [groups, q]);

  return (
    <div className="phone-app-page phone-home">
      <header className="phone-app-nav">
        <button
          type="button"
          className="phone-home-computer"
          data-state={socket}
          onClick={() => callMobile("computers")}
          disabled={!computer}
        >
          <span className="phone-home-dot" aria-hidden="true" />
          {computer
            ? `${computer.name} · ${socket === "open"
              ? computer.via === "lan" ? text("Same Wi-Fi", "同一 Wi-Fi") : text("Anywhere", "远程")
              : text("Connecting…", "正在连接…")}`
            : socket === "open" ? text("Connected", "已连接") : text("Connecting…", "正在连接…")}
        </button>
        <div className="phone-app-nav-side phone-app-nav-trailing">
          <button type="button" className="phone-app-circle" aria-label={text("Settings", "设置")} onClick={() => navigate("/settings")}>
            <Settings size={19} strokeWidth={1.8} aria-hidden="true" />
          </button>
        </div>
      </header>
      <div className="phone-app-scroll">
        <h1 className="phone-app-large-title">{text("Projects", "项目")}</h1>
        <SearchField value={query} onChange={setQuery} />
        {q ? (
          found.length ? <ChatRows rows={found} /> : <p className="phone-app-empty">{text("No matching chats", "没有匹配的对话")}</p>
        ) : (
          <div className="phone-app-list">
            {groups.map((group) => {
              const name = group.name || text("Other", "其他");
              return (
                <button
                  key={group.id}
                  type="button"
                  className="phone-home-row phone-project-row"
                  onClick={() => pushUrl(`/chats?project=${encodeURIComponent(group.id)}`)}
                >
                  <span className="phone-project-icon" style={{ background: projectTone(name) }} aria-hidden="true">
                    {[...name.trim()][0]?.toUpperCase() ?? "?"}
                  </span>
                  <span className="phone-home-text">
                    <span className="phone-home-line">
                      <span className="phone-home-title">{name}</span>
                      {group.waiting
                        ? <span className="phone-home-badge">{text(`${group.waiting} waiting`, `${group.waiting} 个等你`)}</span>
                        : <span className="phone-home-time">{when(group.latest, locale)}</span>}
                    </span>
                    <span className="phone-home-preview">
                      {text(`${group.rows.length} ${group.rows.length === 1 ? "chat" : "chats"}`, `${group.rows.length} 个对话`)}
                      {group.rows[0] ? ` · ${displayTitle(group.rows[0].conv)}` : ""}
                    </span>
                  </span>
                  <ChevronRight size={18} className="phone-app-row-chevron phone-project-chevron" aria-hidden="true" />
                </button>
              );
            })}
          </div>
        )}
        {groups.length === 0 && socket === "open" && !q ? (
          <p className="phone-app-empty">{text("No chats yet", "还没有对话")}</p>
        ) : null}
      </div>
      <NewChatDock />
    </div>
  );
}

function PhoneProject({ projectId }: { projectId: string }) {
  const { text } = useTranslation();
  const groups = useGroups();
  const group = groups.find((g) => g.id === projectId);
  const [query, setQuery] = useState("");
  const q = query.trim().toLowerCase();
  const rows = (group?.rows ?? []).filter((row) => !q || displayTitle(row.conv).toLowerCase().includes(q));
  const name = group?.name || text("Other", "其他");

  return (
    <div className="phone-app-page phone-home phone-project">
      <PhoneNavBar back={text("Projects", "项目")} />
      <div className="phone-app-scroll">
        <h1 className="phone-app-large-title">{name}</h1>
        <SearchField value={query} onChange={setQuery} />
        {rows.length ? <ChatRows rows={rows} sections={!q} /> : group ? (
          <p className="phone-app-empty">{q ? text("No matching chats", "没有匹配的对话") : text("No chats yet", "还没有对话")}</p>
        ) : null}
      </div>
      <NewChatDock projectId={group?.registered ? group.id : undefined} name={group?.name} />
    </div>
  );
}
