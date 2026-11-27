"use client";

/**
 * The phone's first two pages. Projects (/chats): one row per project with
 * its chat count, latest chat and how many wait on the user, so the first
 * page stays short however many chats there are. A project
 * (/chats?project=<id>): its chats, newest first, a chat waiting on the
 * user first and saying so. Searching on Projects finds chats in every
 * project. A project's bottom bar starts a new chat in it. Projects come
 * from the same registry as the desktop sidebar's project groups
 * (lib/projects/).
 */

import { useCallback, useEffect, useMemo, useRef, useState, type RefObject } from "react";
import { Mic, Pin, Settings, SlidersHorizontal } from "lucide-react";
import {
  Actions,
  ActionsButton,
  ActionsGroup,
  Block,
  BlockTitle,
  Link,
  List,
  ListItem,
  Navbar,
  Page,
  Preloader,
  Searchbar,
  SwipeoutActions,
  SwipeoutButton,
  Toolbar,
} from "framework7-react";

import { useSessionActions } from "@/components/chat/pane/session-actions-menu";

import { AgentMark } from "@/components/chat/top-bar/chips/model-mark";
import { activityState, type ActivityState } from "@/components/sidebar/sessions/activity";
import { displayTitle, wsSend, type LegacyConv } from "@/components/sidebar/sessions/helpers";
import { useTranslation } from "@/lib/i18n";
import { LOCAL_ID, projectMachineId } from "@/lib/machines/store";
import { haptic, mobileBridge } from "@/lib/mobile/bridge";
import { armNativePress, nativeMenus } from "@/lib/mobile/native-menu";
import { navigate } from "@/lib/nav/navigate";
import { listSessionProjects } from "@/lib/projects/list-projects";
import { projectGroups, type ProjectGroupSource } from "@/lib/projects/project-groups";
import { useSessionStore } from "@/lib/session-store";
import { openNewChat, prepareNewChat } from "@/lib/tabs/navigation/open-session";
import { SessionScopeProvider } from "@/lib/session-store/session-scope";
import { PhoneNewChatSettings } from "@/components/chat/composer/controls/phone-composer-bar";

import { PhoneComputerSwitcher, useLinkedComputers, useShownMachine } from "./phone-computer-switcher";
import { openSessionWhenReady, pushUrl } from "./phone-nav";
import { askDelete, askRename } from "./phone-dialogs";
import { PhonePortal } from "./phone-portal";
import { PhoneProjectMenu } from "./phone-project-menu";
import { backTo } from "./phone-router";
import { pinnedProjectIds, useRecentsView } from "@/lib/prefs/recents-view";
import { cachedProjects, cacheProjects } from "@/lib/mobile/session-list-cache";
import { preloadSession, sessionHeld } from "@/lib/tabs/navigation/preload-session";

type Row = { conv: LegacyConv; state: ActivityState | null };
/** `registered`: `id` is a registry project's id, not a name from the
 *  chats before the registry answered. */
type Group = { id: string; name: string; rows: Row[]; waiting: number; latest: number; registered: boolean; machine: string; pinned: boolean };

const time = (conv: LegacyConv) => conv.updated_at || conv.created_at || 0;

/** Today: the time; this week: the weekday; else the date. */
function when(ts: number | undefined, locale: string): string {
  if (!ts) return "";
  const date = new Date(ts < 1e12 ? ts * 1000 : ts);
  const now = new Date();
  const days = Math.floor((new Date(now.toDateString()).getTime() - new Date(date.toDateString()).getTime()) / 86_400_000);
  const lang = locale === "zh" ? "zh-CN" : undefined;
  if (days <= 0) return date.toLocaleTimeString(lang, { hour: "numeric", minute: "2-digit" });
  if (days < 7) return date.toLocaleDateString(lang, { weekday: "short" });
  return date.toLocaleDateString(lang, { month: "numeric", day: "numeric" });
}

/** The project registry as last read. It outlives a page: going back to
 *  Projects remounts it, and starting from nothing regrouped the chats
 *  under their folder names until the registry answered again, so every
 *  return looked like a reload. The app also keeps it across starts. */
let knownProjects: ProjectGroupSource[] | null = null;

/** The project registry, read again when chats come or go, a binding
 *  changes (`project-changed`) or the connection opens. */
function useProjects(): ProjectGroupSource[] {
  const [projects, setProjects] = useState<ProjectGroupSource[]>(() => {
    knownProjects ??= cachedProjects<ProjectGroupSource>();
    return knownProjects ?? [];
  });
  const ids = useSessionStore((s) => Object.keys(s.conversations).sort().join(","));
  const open = useSessionStore((s) => s.wsStatus === "open");
  const read = useCallback(() => {
    void listSessionProjects<{ projects: ProjectGroupSource[]; session_id?: string | null }>("")
      .then((data) => {
        const next = data?.projects;
        if (!next) return;
        const same = (list: ProjectGroupSource[] | null) => !!list && JSON.stringify(list) === JSON.stringify(next);
        if (!same(knownProjects)) { knownProjects = next; cacheProjects(next); }
        // The same registry again changes nothing on screen.
        setProjects((shown) => same(shown) ? shown : next);
      });
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
  const view = useRecentsView();
  return useMemo(() => {
    // Pinned as in the sidebar; the default project, where a chat with no
    // project goes, is pinned until unpinned there.
    const pinned = new Set(pinnedProjectIds(view, projects.find((project) => project.is_default)?.id));
    const convs = (Object.values(conversations) as LegacyConv[]).filter((conv) => !conv.archived);
    const rank = (row: Row) => row.state === "needs_input" ? 0 : row.conv.pinned ? 1 : 2;
    const toGroup = (id: string, name: string, items: LegacyConv[], registered: boolean, machine = LOCAL_ID): Group => {
      const rows = items.map((conv) => ({ conv, state: activityState(conv, !!runningTasks[conv.id]) }));
      rows.sort((a, b) => rank(a) - rank(b) || time(b.conv) - time(a.conv));
      return {
        id,
        name,
        rows,
        waiting: rows.filter((row) => row.state === "needs_input").length,
        latest: Math.max(0, ...items.map(time)),
        registered,
        machine,
        pinned: registered && pinned.has(id),
      };
    };
    let groups: Group[];
    if (projects.length) {
      const byId = new Map(projects.map((project) => [project.id, project]));
      groups = projectGroups(projects, convs, [], { sort: "recency" })
        .map((g) => toGroup(g.key, g.name, g.items, true, projectMachineId(byId.get(g.key))));
    } else {
      // Before the registry answers, the chats' own project names.
      const byName = new Map<string, LegacyConv[]>();
      for (const conv of convs) byName.set(conv.project || "", [...(byName.get(conv.project || "") ?? []), conv]);
      groups = [...byName.entries()].map(([name, items]) => toGroup(name, name, items, false));
    }
    return groups.sort((a, b) => Number(b.waiting > 0) - Number(a.waiting > 0) || Number(b.pinned) - Number(a.pinned) || b.latest - a.latest);
  }, [conversations, runningTasks, projects, view]);
}

function stateWord(state: ActivityState | null, text: (en: string, zh: string) => string): string | null {
  if (state === "needs_input") return text("Waiting for you", "等你确认");
  if (state === "working") return text("Running", "运行中");
  if (state === "failed") return text("Failed", "失败");
  if (state === "unread") return text("Done", "已完成");
  return null;
}

/** Archive or pin a chat from its row, as the sidebar's menu does: the
 *  store at once, then the server. */
function setChatFlags(id: string, flags: { archived?: boolean; pinned?: boolean }) {
  const store = useSessionStore.getState();
  const prev = store.conversations[id];
  if (prev) store.upsertConversation({ ...prev, ...flags, id });
  wsSend({ action: "update_session_flags", session_id: id, ...flags });
}

const HOLD_MS = 450;

/** A long press on a row opens its chat's menu (Paseo, 450ms): iOS's own
 *  in the phone app, Framework7's action sheet in a browser. A swipe on
 *  the row is Framework7's swipeout. Returns whether the click that ends
 *  the press is the press's own. */
function useLongPress(row: RefObject<{ el: HTMLElement | null } | null>, conv: LegacyConv, onMenu: (id: string, run?: RowAction) => void) {
  const { t } = useTranslation();
  const swallow = useRef(false);
  const flags = useRef(conv);
  flags.current = conv;
  useEffect(() => {
    const el = row.current?.el;
    if (!el) return;
    let timer = 0;
    let x0 = 0;
    let y0 = 0;
    const cancel = () => { window.clearTimeout(timer); timer = 0; };
    const onStart = (event: TouchEvent) => {
      if (event.touches.length !== 1) return;
      x0 = event.touches[0].clientX;
      y0 = event.touches[0].clientY;
      swallow.current = false;
      cancel();
      // Read the chat while the finger is down: most are here by the tap.
      if (!sessionHeld(conv.id)) preloadSession(conv.id);
      if (nativeMenus()) {
        const run = (action: RowAction) => () => onMenu(conv.id, action);
        armNativePress(el, [
          { title: "", inline: true, children: [
            { title: flags.current.pinned ? t("sidebar.unpin") : t("sidebar.pin"), icon: flags.current.pinned ? "pin.slash" : "pin", run: run("p") },
            { title: t("sidebar.rename"), icon: "pencil", run: run("r") },
            { title: t("sidebar.copy_link"), icon: "link", run: run("c") },
            { title: flags.current.archived ? t("sidebar.unarchive") : t("sidebar.archive"), icon: "archivebox", run: run("a") },
          ] },
          { title: t("sidebar.delete"), icon: "trash", destructive: true, run: run("d") },
        ]);
        return;
      }
      timer = window.setTimeout(() => {
        timer = 0;
        swallow.current = true;
        haptic("medium");
        onMenu(conv.id);
      }, HOLD_MS);
    };
    const onMove = (event: TouchEvent) => {
      if (Math.hypot(event.touches[0].clientX - x0, event.touches[0].clientY - y0) <= 8) return;
      cancel();
      if (nativeMenus()) armNativePress(null);
    };
    const onEnd = () => {
      cancel();
      if (nativeMenus()) armNativePress(null);
    };
    el.addEventListener("touchstart", onStart, { passive: true });
    el.addEventListener("touchmove", onMove, { passive: true });
    el.addEventListener("touchend", onEnd);
    el.addEventListener("touchcancel", onEnd);
    return () => {
      cancel();
      el.removeEventListener("touchstart", onStart);
      el.removeEventListener("touchmove", onMove);
      el.removeEventListener("touchend", onEnd);
      el.removeEventListener("touchcancel", onEnd);
    };
  }, [row, conv.id, onMenu, t]);
  return swallow;
}

/** A chat's actions (Pin, Rename, Copy link, Archive, Delete). */
type RowAction = "p" | "r" | "c" | "a" | "d";

/** A chat's row: Framework7's media-list row, its title and time, its
 *  state and the latest message under it. A swipe right shows Pin, a
 *  swipe left Archive, as Mail and Telegram do. */
function ChatRow({ row, onMenu }: { row: Row; onMenu: (id: string, run?: RowAction) => void }) {
  const { t, text, locale } = useTranslation();
  const { conv, state } = row;
  // Framework7 hands its components' elements as `el`.
  const li = useRef<{ el: HTMLElement | null } | null>(null);
  const swallow = useLongPress(li, conv, onMenu);
  const word = stateWord(state, text);
  const preview = conv.preview?.replace(/\[image\]\s*(?=\S)/g, `${text("Image", "图片")} · `).replace(/\[image\]/g, text("Image", "图片"));
  return (
    <ListItem
      ref={li as never}
      link="#"
      swipeout
      noChevron
      title={displayTitle(conv)}
      after={state === "working" || state === "needs_input" ? undefined : when(time(conv), locale)}
      badge={state === "needs_input" ? text("Waiting", "等你") : undefined}
      badgeColor="orange"
      text={[word, preview].filter(Boolean).join(" · ")}
      onClick={() => {
        if (swallow.current) { swallow.current = false; return; }
        openSessionWhenReady(conv.id);
      }}
    >
      {conv.pinned ? <span slot="before-title"><Pin size={12} aria-hidden="true" /> </span> : null}
      {conv.harness ? <span slot="after-start" data-not-f7><AgentMark harness={conv.harness} size={13} fit="contain" /></span> : null}
      {state === "working" ? <span slot="after"><Preloader size={14} /></span> : null}
      <SwipeoutActions left>
        <SwipeoutButton color="orange" close onClick={() => { haptic("success"); setChatFlags(conv.id, { pinned: !conv.pinned }); }}>
          {conv.pinned ? t("sidebar.unpin") : t("sidebar.pin")}
        </SwipeoutButton>
      </SwipeoutActions>
      <SwipeoutActions right>
        <SwipeoutButton color="blue" close onClick={() => { haptic("success"); setChatFlags(conv.id, { archived: true }); }}>
          {text("Archive", "归档")}
        </SwipeoutButton>
      </SwipeoutActions>
    </ListItem>
  );
}

// Framework7's List puts its rows in its <ul> by their component's name.
ChatRow.displayName = "f7-list-item-chat";

/** A chat's menu from its row's long press: the same actions as the
 *  details page, for this chat (chat/pane/session-actions-menu.tsx), as
 *  Framework7's action sheet. */
function ChatRowMenu({ id, run }: { id: string; run?: RowAction }) {
  const { t, text } = useTranslation();
  const [open, setOpen] = useState(!run);
  const here = typeof window === "undefined" ? "" : window.location.pathname + window.location.search;
  const { conv, title, actions, rename, remove } = useSessionActions(() => {
    // Deleting it must not leave the list for another chat.
    setTimeout(() => { if (window.location.pathname + window.location.search !== here) window.history.replaceState(null, "", here); }, 0);
  }, id);
  const runs: Record<RowAction, () => void> = {
    ...actions,
    r: () => askRename(text, title, rename),
    d: () => askDelete(text, title, remove),
  };
  // Picked from iOS's menu: run it here.
  const ran = useRef(false);
  useEffect(() => {
    if (!run || ran.current) return;
    ran.current = true;
    runs[run]();
  });
  const button = (label: string, action: RowAction, color?: string) => (
    <ActionsButton color={color} onClick={runs[action]}>{label}</ActionsButton>
  );
  return (
    <PhonePortal>
      <Actions opened={open} onActionsClosed={() => setOpen(false)}>
        <ActionsGroup>
          {button(conv?.pinned ? t("sidebar.unpin") : t("sidebar.pin"), "p")}
          {button(t("sidebar.rename"), "r")}
          {button(t("sidebar.copy_link"), "c")}
          {button(conv?.archived ? t("sidebar.unarchive") : t("sidebar.archive"), "a")}
          {button(t("sidebar.delete"), "d", "red")}
        </ActionsGroup>
        <ActionsGroup>
          <ActionsButton strong>{text("Cancel", "取消")}</ActionsButton>
        </ActionsGroup>
      </Actions>
    </PhonePortal>
  );
}

function ChatRows({ rows, sections }: { rows: Row[]; sections?: boolean }) {
  const { text } = useTranslation();
  const [menu, setMenu] = useState<{ id: string; n: number; run?: RowAction } | null>(null);
  const onMenu = useCallback((id: string, run?: RowAction) => setMenu((prev) => ({ id, run, n: (prev?.n ?? 0) + 1 })), []);
  // A project's chats in two groups when some are live: those running or
  // waiting for the person, then the rest.
  const live = sections ? rows.filter((row) => row.state === "needs_input" || row.state === "working") : [];
  const rest = live.length ? rows.filter((row) => !live.includes(row)) : rows;
  const list = (items: Row[]) => (
    <List strong inset dividersIos mediaList>
      {items.map((row) => <ChatRow key={row.conv.id} row={row} onMenu={onMenu} />)}
    </List>
  );
  return (
    <>
      {live.length ? <><BlockTitle>{text("In progress", "进行中")}</BlockTitle>{list(live)}</> : null}
      {live.length && rest.length ? <BlockTitle>{text("Earlier", "更早")}</BlockTitle> : null}
      {rest.length ? list(rest) : null}
      {menu ? <ChatRowMenu key={menu.n} id={menu.id} run={menu.run} /> : null}
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

/** Framework7's searchbar, in the page under the bar. */
function SearchField({ onChange }: { onChange: (value: string) => void }) {
  const { text } = useTranslation();
  return (
    <Searchbar
      customSearch
      disableButton={false}
      placeholder={text("Search chats", "搜索对话")}
      onSearchbarSearch={(_bar: unknown, query: string) => onChange(query)}
      onSearchbarClear={() => onChange("")}
    />
  );
}

/** The bottom bar: the new chat's settings, the new chat, and voice, as
 *  Framework7's toolbar. */
function NewChatDock({ projectId, name }: { projectId?: string; name?: string; slot?: string }) {
  const { text } = useTranslation();
  // The draft the settings button set up: the next new chat opens it.
  const [prepared, setPrepared] = useState<string | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  // In a project: a fresh draft bound to it until its first message, as
  // the sidebar's ＋ on a project does (sessions-list.tsx).
  function bindProject(draftId: string) {
    const bind = (id: string) => {
      useSessionStore.getState().setPendingProject(draftId, id);
      window.dispatchEvent(new Event("project-changed"));
    };
    if (projectId) bind(projectId);
    else if (name) {
      // The registry had not answered when the page drew (it groups by
      // name until then): the project is found by its name now.
      void listSessionProjects<{ projects: ProjectGroupSource[]; session_id?: string | null }>("").then((data) => {
        const found = data?.projects?.find((project) => project.name === name);
        if (found) bind(found.id);
      });
    }
  }
  function newChat() {
    const draftId = openNewChat({ fresh: !!(projectId || name), draft: prepared });
    if (draftId !== prepared) bindProject(draftId);
    setPrepared(null);
    pushUrl("/chat");
  }
  // The settings open here, over the list, for the chat the bar starts.
  function openSettings() {
    const draftId = prepareNewChat(prepared);
    if (draftId !== prepared) {
      bindProject(draftId);
      setPrepared(draftId);
    }
    setSettingsOpen(true);
  }
  // Voice: the new chat's own mic, pressed once its page is up.
  function newChatByVoice() {
    newChat();
    const started = performance.now();
    const press = () => {
      const button = document.querySelector<HTMLButtonElement>(".phone-conversation [data-composer-mic]");
      if (button) button.click();
      else if (performance.now() - started < 2000) window.requestAnimationFrame(press);
    };
    window.requestAnimationFrame(press);
  }
  return (
    <Toolbar bottom>
      {/* Framework7 draws its toolbar's left, pane and right on glass. */}
      <div className="left">
        <Link iconOnly aria-label={text("New chat settings", "新对话设置")} onClick={openSettings}>
          <SlidersHorizontal size={22} aria-hidden="true" />
        </Link>
      </div>
      <div className="toolbar-pane phone-new-chat-pane">
        <Link className="phone-new-chat" onClick={newChat}>
          <span>{name ? text(`New chat in ${name}`, `在 ${name} 里开新对话`) : text("New chat", "新对话")}</span>
        </Link>
      </div>
      <div className="right">
        <Link iconOnly aria-label={text("New chat by voice", "语音开新对话")} onClick={newChatByVoice}>
          <Mic size={22} aria-hidden="true" />
        </Link>
      </div>
      <SessionScopeProvider sid={prepared ?? "__new__"}>
        <PhoneNewChatSettings open={settingsOpen} setOpen={setSettingsOpen} />
      </SessionScopeProvider>
    </Toolbar>
  );
}

export function PhoneHome({ projectId }: { projectId?: string | null }) {
  return projectId ? <PhoneProject projectId={projectId} /> : <PhoneProjects />;
}

function PhoneProjects() {
  const { text, locale } = useTranslation();
  const all = useGroups();
  const socket = useSessionStore((s) => s.wsStatus);
  const [query, setQuery] = useState("");
  const computer = mobileBridge()?.computer;
  // One computer at a time: this one (with its SSH and Docker machines),
  // or a computer it is linked to (phone-computer-switcher.tsx).
  const shown = useShownMachine();
  const linked = useLinkedComputers();
  const groups = useMemo(() => all.filter((g) => shown === LOCAL_ID
    ? !linked.some((machine) => machine.id === g.machine)
    : g.machine === shown), [all, shown, linked]);
  const q = query.trim().toLowerCase();
  const found = useMemo(() => q
    ? groups.flatMap((g) => g.rows).filter((row) => displayTitle(row.conv).toLowerCase().includes(q))
      .sort((a, b) => time(b.conv) - time(a.conv))
    : [], [groups, q]);

  const projectRow = (group: (typeof groups)[number]) => {
    const name = group.name || text("Other", "其他");
    return (
      <ListItem
        key={group.id}
        link="#"
        onClick={() => pushUrl(`/chats?project=${encodeURIComponent(group.id)}`)}
        title={name}
        after={group.waiting ? undefined : when(group.latest, locale)}
        badge={group.waiting || undefined}
        badgeColor="red"
        footer={`${text(`${group.rows.length} ${group.rows.length === 1 ? "chat" : "chats"}`, `${group.rows.length} 个对话`)}${group.rows[0] ? ` · ${displayTitle(group.rows[0].conv)}` : ""}`}
      >
        <span slot="media" className="phone-project-icon" style={{ background: projectTone(name) }} aria-hidden="true">
          {[...name.trim()][0]?.toUpperCase() ?? "?"}
        </span>
      </ListItem>
    );
  };
  const pinned = groups.filter((group) => group.pinned);
  const rest = groups.filter((group) => !group.pinned);

  // Grouped as Settings and Reminders are: a pinned card, then the rest.
  return (
    <Page className="phone-home">
      {/* The title sits centred in the bar, between the computer and
          settings; a new chat starts inside a project. */}
      <Navbar title={text("Projects", "项目")}>
        {/* Framework7 places a bar's sides by their slot. */}
          <PhoneComputerSwitcher
            slot="left"
            online={socket === "open"}
            status={computer
              ? `${computer.name} · ${socket === "open"
                ? computer.via === "lan" ? text("Same Wi-Fi", "同一 Wi-Fi") : text("Anywhere", "远程")
                : text("Connecting…", "正在连接…")}`
              : socket === "open" ? text("Connected", "已连接") : text("Connecting…", "正在连接…")}
          />
          <Link slot="right" iconOnly aria-label={text("Settings", "设置")} onClick={() => navigate("/settings")}>
            <Settings size={22} aria-hidden="true" />
          </Link>
      </Navbar>
      <SearchField onChange={setQuery} />
      {q ? (
        found.length ? <ChatRows rows={found} /> : <Block>{text("No matching chats", "没有匹配的对话")}</Block>
      ) : (
        <>
          {pinned.length ? (
            <>
              <BlockTitle>{text("Pinned", "置顶")}</BlockTitle>
              <List strong inset dividersIos>{pinned.map(projectRow)}</List>
            </>
          ) : null}
          {rest.length ? (
            <>
              {pinned.length ? <BlockTitle>{text("Projects", "项目")}</BlockTitle> : null}
              <List strong inset dividersIos>{rest.map(projectRow)}</List>
            </>
          ) : null}
        </>
      )}
      {groups.length === 0 && socket === "open" && !q ? (
        <Block>{text("No chats yet", "还没有对话")}</Block>
      ) : null}
    </Page>
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
  const project = useProjects().find((source) => group?.registered && source.id === group.id);

  return (
    <Page className="phone-home phone-project">
      <Navbar title={name} {...backTo(text("Projects", "项目"))}>
        {project ? <PhoneProjectMenu slot="right" project={project} /> : null}
      </Navbar>
      <NewChatDock slot="fixed" projectId={group?.registered ? group.id : undefined} name={group?.name} />
      <SearchField onChange={setQuery} />
      {rows.length ? <ChatRows rows={rows} sections={!q} /> : group ? (
        <Block>{q ? text("No matching chats", "没有匹配的对话") : text("No chats yet", "还没有对话")}</Block>
      ) : null}
    </Page>
  );
}
