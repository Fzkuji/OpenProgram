"use client";

/**
 * Sessions list (the "Recents" panel in the sidebar).
 *
 * Reads conversations from the React store (`store.conversations`), which
 * the runtime-bridge keeps authoritative from the `sessions_list` WS event
 * (see conv-store-mirror). Layers Claude.ai-style management on top:
 *   - per-row right-click / ⋯ context menu (rename, pin, move to group,
 *     copy link, archive, delete) — see `conv-menu.tsx`
 *   - Recents-header filter (status / group-by / sort) — see
 *     `recents-filter.tsx`, read here via `useRecentsView`
 *
 * Group-by modes: Date (default), State and Flat render classic labelled
 * sections via `buildSections`. Group-by → Project instead renders the
 * registry-backed folder tree (nav-row project headers + dense child
 * rows): sessions are joined against `list_projects` → `projects_list`
 * `session_ids` (alive-filtered server-side); unclaimed sessions belong
 * to the DEFAULT project group — the same fallback the backend's
 * project_for_session applies.
 *
 * Flags (pinned / archived / group) and renames are persisted server-
 * side (meta.json) through WS actions; we optimistically patch the store
 * (and the legacy `window.conversations` heavy map the top-bar still
 * reads) for instant feedback before the server's echo lands.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter, usePathname } from "next/navigation";
import { ChevronRight, Plus } from "lucide-react";
import { useCurrentSessionId } from "./use-window-globals";
import { useSessionStore } from "@/lib/session-store";
import type { ConvSummary } from "@/lib/session-store";
import { useCenterTabs } from "@/lib/state/center-tabs-store";
import { useTranslation } from "@/lib/i18n";
import { useRecentsView } from "@/lib/prefs/recents-view";
import { wsRequest } from "@/lib/net/ws-request";
import { projectGroups } from "@/lib/project-groups";
import {
  Popover,
  PopoverAnchor,
  PopoverContent,
} from "@/components/ui/popover";
import {
  FoldersIcon,
  type AnimatedNavIconHandle,
} from "@/components/animated-icons";
import { ConvMenu } from "./conv-menu";
import { RecentsFilter } from "./recents-filter";
import { SectionHeader } from "./section-header";
import {
  sidebarNavIconClass,
  sidebarNavItemClass,
  sidebarNavLabelClass,
} from "./nav-classes";
import styles from "./sidebar.module.css";

import { ConfirmDialog } from "./sessions-list/confirm-dialog";
import {
  type LegacyConv,
  type SessionWindow,
  wsSend,
  labelFor,
} from "./sessions-list/helpers";

/** One registry project as `projects_list` ships it (Group-by → Project
 *  mode only). `session_ids` is alive-filtered server-side (same filter
 *  as `session_count`). */
interface SidebarProject {
  id: string;
  name: string;
  path: string;
  is_default: boolean;
  session_count: number;
  session_ids?: string[];
  status?: string;
}

const COLLAPSED_PROJECTS_KEY = "sidebar_collapsed_projects";

function readCollapsedProjects(): Set<string> {
  try {
    const raw = localStorage.getItem(COLLAPSED_PROJECTS_KEY);
    return new Set(raw ? (JSON.parse(raw) as string[]) : []);
  } catch {
    return new Set();
  }
}

export function SessionsList({ onNewChat }: { onNewChat: () => string }) {
  const router = useRouter();
  const pathname = usePathname();
  const { t, text, locale } = useTranslation();
  // The sidebar's source of truth is the React store. The runtime-bridge
  // mirrors every window.conversations summary write into it (see
  // conv-store-mirror), so subscribing here re-renders the list the moment
  // a session is added / renamed / pinned / deleted — no polling.
  const conversations = useSessionStore((s) => s.conversations);
  const upsertConversation = useSessionStore((s) => s.upsertConversation);
  const removeConversation = useSessionStore((s) => s.removeConversation);
  const clearConversations = useSessionStore((s) => s.clearConversations);
  const currentId = useCurrentSessionId();
  const runningTasks = useSessionStore((s) => s.runningTasks);
  const view = useRecentsView();

  /* ---- project mode (Group-by → Project): registry-backed tree ---- */

  const projectMode = view.groupBy === "project";

  const [projects, setProjects] = useState<SidebarProject[]>([]);
  const refreshProjects = useCallback(async (): Promise<boolean> => {
    const data = await wsRequest<{ projects: SidebarProject[] }>(
      "list_projects",
      { session_id: "" },
      "projects_list",
    );
    if (data?.projects) {
      setProjects(data.projects);
      return true;
    }
    return false;
  }, []);

  // The registry fetch only runs while Group-by is set to Project. It
  // re-runs when the session SET changes (create / delete — also what a
  // WS reconnect's list_sessions replay produces), since the registry's
  // reverse index may have gained/lost bindings; `project-changed`
  // (topbar picker / group ＋) refetches too. On a fresh page the
  // WebSocket may not be OPEN yet, so retry (~6s) until it answers —
  // same pattern as the topbar ProjectBadge.
  const convIdsKey = Object.keys(conversations).sort().join(",");
  useEffect(() => {
    if (!projectMode) return;
    let cancelled = false;
    let tries = 0;
    const attempt = () => {
      if (cancelled) return;
      refreshProjects().then((ok) => {
        if (!ok && !cancelled && tries++ < 20) setTimeout(attempt, 300);
      });
    };
    attempt();
    const onChanged = () => void refreshProjects();
    window.addEventListener("project-changed", onChanged);
    return () => {
      cancelled = true;
      window.removeEventListener("project-changed", onChanged);
    };
  }, [projectMode, convIdsKey, refreshProjects]);

  // Per-project collapse state, persisted per browser.
  const [collapsedProjects, setCollapsedProjects] = useState<Set<string>>(
    new Set(),
  );
  useEffect(() => setCollapsedProjects(readCollapsedProjects()), []);
  function writeCollapsedProjects(next: Set<string>) {
    try {
      localStorage.setItem(
        COLLAPSED_PROJECTS_KEY,
        JSON.stringify(Array.from(next)),
      );
    } catch {
      /* ignore */
    }
  }
  function toggleProjectCollapse(id: string) {
    setCollapsedProjects((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      writeCollapsedProjects(next);
      return next;
    });
  }

  // Auto-expand the group that owns the ACTIVE session — once per
  // session switch (the ref guard), so the user can still collapse it
  // manually afterwards without a projects refetch re-expanding it.
  const autoExpandedFor = useRef<string | null>(null);
  useEffect(() => {
    if (!projectMode || !currentId || autoExpandedFor.current === currentId)
      return;
    const owner = projects.find((p) =>
      (p.session_ids || []).includes(currentId),
    );
    if (!owner) return; // registry not loaded yet — retry on next answer
    autoExpandedFor.current = currentId;
    setCollapsedProjects((prev) => {
      if (!prev.has(owner.id)) return prev;
      const next = new Set(prev);
      next.delete(owner.id);
      writeCollapsedProjects(next);
      return next;
    });
  }, [projectMode, currentId, projects]);

  // ＋ on a group header creates a distinct provisional chat and records
  // this project against that chat key until its chat_ack arrives.
  function newSessionInProject(projectId: string) {
    const draftId = onNewChat();
    useSessionStore.getState().setPendingProject(draftId, projectId);
    // Same event the project picker fires — the topbar chip re-reads
    // the pending choice.
    window.dispatchEvent(new Event("project-changed"));
  }

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

  function switchTo(id: string, title: string) {
    // Focus-or-recreate the session's center tab BEFORE the navigation
    // guard: when the clicked session IS the current one but its tab
    // was closed (user parked on a file tab), the early return below
    // would otherwise leave nothing re-opened. openSessionTab is
    // focus-or-create, so this is a no-op when the tab already exists.
    useCenterTabs.getState().openSessionTab(id, title);
    if (id === currentId && pathname === "/s/" + id) return;
    router.push("/s/" + id);
  }

  const [confirm, setConfirm] = useState<{
    title: string;
    message: string;
    run: () => void;
  } | null>(null);

  /* ---- action senders (optimistic patch + WS) ------------------- */

  // Optimistically patch BOTH the store (drives this sidebar) and the
  // legacy window.conversations heavy map (still read by the top-bar
  // title / status-source badge machinery) so a rename / pin shows
  // instantly everywhere before the server's session_updated echo lands.
  function patchConv(id: string, fields: Partial<ConvSummary>) {
    const w = window as unknown as SessionWindow;
    const conv = w.conversations?.[id];
    if (conv) Object.assign(conv, fields);
    const prev = conversations[id];
    if (prev) upsertConversation({ ...prev, ...fields, id });
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
        removeConversation(id);
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
        clearConversations();
        w.newSession?.();
      },
    });
  }

  /* ---- filter / sort / group ------------------------------------ */

  // `conversations` is the React store map: every write replaces the
  // object (immutable updates), so the component re-renders on any change
  // and there's no in-place-mutation hazard. The list is small, so just
  // recompute the filtered/sorted view each render.
  const nowTs = Date.now() / 1000;
  const convArr = Object.values(conversations) as LegacyConv[];

  const allGroups = (() => {
    const s = new Set<string>();
    for (const c of convArr) if (c.group) s.add(c.group);
    return Array.from(s).sort((a, b) => a.localeCompare(b));
  })();

  const visible = (() => {
    let arr = convArr;
    // All sessions are shown — no filtering of empty/placeholder rows.
    if (view.status === "active") arr = arr.filter((c) => !c.archived);
    else if (view.status === "archived") arr = arr.filter((c) => !!c.archived);
    // Last-activity window — updated_at（后端随消息追加维护），老行
    // 无 updated_at 时退回 created_at。"all" = no window.
    if (view.lastActivity !== "all") {
      const days = view.lastActivity === "1d" ? 1 : view.lastActivity === "7d" ? 7 : 30;
      const cutoff = nowTs - days * 86400;
      arr = arr.filter((c) => (c.updated_at || c.created_at || 0) >= cutoff);
    }
    // Project filter — each conv carries a project NAME (home-folder
    // name for ad-hoc chats), so "All projects" shows everything and a
    // specific pick narrows to that folder's chats. (Environment is
    // still UI-only — no per-conversation environment field yet.)
    if (view.project && view.project !== "all") {
      arr = arr.filter((c) => c.project === view.project);
    }
    const cmp = (a: LegacyConv, b: LegacyConv) => {
      if (!!a.pinned !== !!b.pinned) return a.pinned ? -1 : 1;
      if (view.sort === "title") {
        return labelFor(a, "").localeCompare(labelFor(b, ""));
      }
      // "created" 按创建时间；"recency" 按最后活跃（updated_at，随消息
      // 追加更新），老行缺 updated_at 时退回 created_at。缺失时间戳一律
      // 按 0（最旧）处理，不能退回 nowTs——否则 null 时间戳的老行会压过
      // 刚建的会话。
      if (view.sort === "created") {
        return (b.created_at || 0) - (a.created_at || 0);
      }
      return (b.updated_at || b.created_at || 0)
        - (a.updated_at || a.created_at || 0);
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

  // No empty-state early return here: an empty list is rendered as a
  // plain (non-collapsible) section header reading "No conversations
  // yet" — same font / size / indent as the Recents bucket headers, so
  // it reads as a peer of Favorite Functions / Recents rather than a
  // stray hint. (Handled below where `visible.length === 0`.)

  /* ---- project-mode grouping (registry join) ---------------------- */

  // Any narrowing filter active → matched-only view: groups auto-expand
  // around their matches. (status "all" widens, so it doesn't count;
  // "archived" narrows.) Empty project groups are always hidden.
  const filtering =
    view.status === "archived" ||
    view.lastActivity !== "all" ||
    (view.project !== "" && view.project !== "all");

  // Recomputed per render like `visible` — the list is small and the
  // inputs (visible, projects) change together anyway. Sessions with no
  // explicit project claim belong to the DEFAULT project (the backend's
  // project_for_session falls back to it) — there is no separate
  // "Ungrouped" bucket in this mode. Group order is fixed: default first,
  // then project name; session order remains the order from `visible`.
  const groupedProjects = projectMode ? projectGroups(projects, visible) : [];

  const renderRow = (c: LegacyConv) => {
    const label = labelFor(c, t("sidebar.untitled"));
    return (
      <ConvItem
        key={c.id}
        conv={c}
        label={label}
        active={c.id === currentId}
        running={!!runningTasks[c.id]}
        groups={allGroups}
        onClick={() => switchTo(c.id, label)}
        onRename={(title) => renameSession(c.id, title)}
        onTogglePin={() => setFlags(c.id, { pinned: !c.pinned })}
        onToggleArchive={() => setFlags(c.id, { archived: !c.archived })}
        onMoveToGroup={(g) => setFlags(c.id, { group: g })}
        onCopyLink={() => copyLink(c.id)}
        onDelete={() => del(c.id)}
      />
    );
  };

  // Build the sectioned list. Each section is { key, label, items };
  // an empty label renders a flat (header-less) run of rows. Mirrors
  // Claude's rich Recents logic — date buckets by default, Working /
  // Completed when grouping by state. (Group-by → Project bypasses
  // this entirely — the registry-backed tree above renders instead.)
  const isWorking = (id: string) => !!runningTasks[id];
  const sections = projectMode
    ? []
    : buildSections(visible, {
        groupBy: view.groupBy,
        sort: view.sort,
        nowTs,
        isWorking,
        labels: {
          pinned: t("sidebar.pinned"),
          recents: t("sidebar.recents"),
          today: t("sidebar.today"),
          yesterday: t("sidebar.yesterday"),
          older: t("sidebar.older"),
          working: t("sidebar.working"),
          completed: t("sidebar.completed"),
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
  const isEmpty = visible.length === 0;
  const firstHasHeader = sections.length > 0 && sections[0].label !== "";
  // Whole-tree fold for project mode — same SectionHeader affordance as
  // the "Recents" bucket header, same transient collapsedGroups set.
  const PROJECTS_SECTION_KEY = "__projects__";
  const projectsFolded = collapsedGroups.has(PROJECTS_SECTION_KEY);
  const body = projectMode ? (
    <>
      {/* Group-by → Project: a top-level collapsible "Projects" header
          (the way "Recents" heads the date view) that folds ALL groups
          at once; the filter rides its right — it must stay reachable,
          it's the way back to the other modes. */}
      <SectionHeader
        name={text("Projects", "项目")}
        collapsible
        collapsed={projectsFolded}
        onToggle={() => toggleGroupCollapse(PROJECTS_SECTION_KEY)}
        actions={<RecentsFilter />}
      />
      {projectsFolded ? null : projects.length === 0
        ? // projects_list hasn't answered yet (the registry always holds
          // at least the default project) — render a flat run instead of
          // flashing everything under a wrong group.
          visible.map(renderRow)
        : groupedProjects.map((g) => {
            const expanded = filtering ? true : !collapsedProjects.has(g.key);
            return (
              <div key={g.key} className="flex flex-col gap-px">
                <ProjectGroupHeader
                  name={g.name}
                  path={g.path}
                  collapsed={!expanded}
                  onToggle={() => toggleProjectCollapse(g.key)}
                  onNewSession={() => newSessionInProject(g.key)}
                  newSessionTitle={text(
                    "New session in this project",
                    "在此项目新建会话",
                  )}
                />
                {expanded && g.items.length > 0 ? (
                  // Level-2 block: dense 28px rows + the 1px vertical
                  // guide at x=16px (see .projectKids in the module CSS).
                  <div className={`${styles.projectKids} flex flex-col gap-px`}>
                    {g.items.map(renderRow)}
                  </div>
                ) : null}
              </div>
            );
          })}
      {filtering && projects.length > 0 && groupedProjects.length === 0 ? (
        <div className="px-[16px] py-[10px] text-[12px] text-[var(--text-muted)]">
          {text("No matches", "没有匹配的会话")}
        </div>
      ) : null}
    </>
  ) : (
    <>
      {isEmpty ? (
        // Empty list: render the "Recents" slot as a plain section header
        // that reads "No conversations yet" — identical font / size /
        // indent to the real Recents bucket headers, with the filter
        // button on its right just like a populated Recents header. Not
        // collapsible (nothing to collapse), so no chevron.
        <SectionHeader
          name={t("sidebar.no_conversations")}
          collapsible={false}
          collapsed={false}
          onToggle={() => {}}
          actions={<RecentsFilter />}
        />
      ) : !firstHasHeader ? (
        <div className="flex h-[24px] items-center justify-end px-[8px]">
          <RecentsFilter />
        </div>
      ) : null}
      {sections.map((sec, i) =>
        sec.label === "" ? (
          // Flat run (title sort, no grouping) — no header.
          <div key={sec.key} className="flex flex-col gap-px">{sec.items.map(renderRow)}</div>
        ) : (
          // group/sec → hovering anywhere in the section reveals its
          // collapse chevron (hidden otherwise).
          <div key={sec.key} className="group/sec flex flex-col gap-px">
            <SectionHeader
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
      {/* "Clear all" only when there are conversations to clear — an
          empty list shows just the "No conversations yet" header. It
          folds away with the Projects section: a folded section leaves
          no rows for it to act on visually. */}
      {!isEmpty && !(projectMode && projectsFolded) ? (
        <div className={styles.clearAll} onClick={clearAll}>
          {t("sidebar.clear_all")}
        </div>
      ) : null}
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
 *   - groupBy "project" → handled in SessionsList (registry folder tree)
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
  groupBy: "none" | "state" | "project" | "flat";
  sort: "recency" | "created" | "title";
  nowTs: number;
  isWorking: (id: string) => boolean;
  labels: {
    pinned: string;
    recents: string;
    today: string;
    yesterday: string;
    older: string;
    working: string;
    completed: string;
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

  // groupBy "project" never reaches here — SessionsList renders the
  // registry-backed folder tree for that mode before calling this.

  if (o.groupBy === "flat") {
    // "None" — one flat "Recents" run: a single header, no date sub-
    // buckets (order follows the sort). Always headed so the list is
    // never an empty / header-less strip.
    return [{ key: "flat", label: o.labels.recents, items: visible }];
  }

  // groupBy "none" → "Date": Pinned (if any) + date buckets; insertion
  // order within each bucket follows the chosen sort.
  const pinned = visible.filter((c) => c.pinned);
  const rest = visible.filter((c) => !c.pinned);
  const buckets = new Map<string, Section>();
  for (const c of rest) {
    // 日期分桶按最后活跃时间："Today" = 今天动过的会话，与 recency
    // 排序一致（否则今天聊过的老会话会挂在旧日期桶里）。
    const b = _dateBucket(
      c.updated_at || c.created_at || 0, o.nowTs, o.labels, o.dateLocale,
    );
    if (!buckets.has(b.key)) buckets.set(b.key, { key: b.key, label: b.label, items: [] });
    buckets.get(b.key)!.items.push(c);
  }
  const out: Section[] = [];
  if (pinned.length) out.push({ key: "pinned", label: o.labels.pinned, items: pinned });
  const sorted = Array.from(buckets.values()).sort((a, b) => a.key.localeCompare(b.key));
  out.push(...sorted);
  return out;
}

/* ---- project group header (Group-by → Project mode) --------------
 * The EXACT nav-row recipe the Functions/Chats links use: `ui-list-item`
 * box (32px) via sidebarNavItemClass, animated FoldersIcon in the
 * standard 16px icon slot, normal-weight label — plus this row's two
 * trailing extras: a hover-revealed ＋ (new session bound to this
 * project) and a small chevron at the row end that rotates 90° when the
 * group is open. The project's path lives in the row's title tooltip. */
function ProjectGroupHeader({
  name,
  path,
  collapsed,
  onToggle,
  onNewSession,
  newSessionTitle,
}: {
  name: string;
  path: string;
  collapsed: boolean;
  onToggle: () => void;
  onNewSession: () => void;
  newSessionTitle: string;
}) {
  const iconRef = useRef<AnimatedNavIconHandle>(null);
  return (
    <div
      className={sidebarNavItemClass + " select-none"}
      role="button"
      title={path || undefined}
      onClick={onToggle}
      onMouseEnter={() => iconRef.current?.startAnimation?.()}
      onMouseLeave={() => iconRef.current?.stopAnimation?.()}
    >
      <span className={sidebarNavIconClass}>
        <FoldersIcon ref={iconRef} size={20} />
      </span>
      <span className={sidebarNavLabelClass}>{name}</span>
      <button
        type="button"
        title={newSessionTitle}
        aria-label={newSessionTitle}
        onClick={(e) => {
          e.stopPropagation();
          onNewSession();
        }}
        className="flex size-[20px] shrink-0 items-center justify-center rounded-[5px]
          text-text-muted opacity-0 transition-opacity duration-150
          group-hover:opacity-100 hover:bg-[var(--bg-selected)] hover:text-text-bright"
      >
        <Plus size={14} strokeWidth={2} />
      </button>
      <ChevronRight
        size={12}
        aria-hidden="true"
        className="shrink-0 text-[var(--text-muted)] transition-transform duration-150"
        style={{ transform: collapsed ? "none" : "rotate(90deg)" }}
      />
    </div>
  );
}

/* ---- leading status marker ------------------------------------- */

type MarkerState = "working" | "needs_input" | "unread" | "idle";

/** The dot to the left of a conversation title, mirroring Claude Code's
 *  status markers (colours sampled from claude.ai/code):
 *   - pinned       → an amber pin (our own addition, takes priority)
 *   - working      → three pulsing gray dots (a task is running)
 *   - needs_input  → a filled amber dot (#ffd014 — awaiting the user)
 *   - unread       → a filled blue dot (#5aa6f2 — finished, not yet seen)
 *   - idle         → a hollow gray ring (done & seen / nothing pending)
 *  Fixed 14px slot so titles stay aligned regardless of state. */
function StatusMarker({
  pinned,
  state,
  labels,
}: {
  pinned: boolean;
  state: MarkerState;
  labels: { pinned: string; working: string; needsInput: string; unread: string };
}) {
  if (pinned) {
    return (
      <svg
        className="shrink-0 text-[var(--accent-orange)]"
        width="12" height="12" viewBox="0 0 16 16" fill="currentColor"
        aria-label={labels.pinned}
      >
        <path d="M9.5 1.5a1 1 0 0 0-1.7.7l.1 3.2-2.4 2.4a1 1 0 0 0-.3.7v.5l2.6-.0 0 4 .8 1.3.8-1.3 0-4 2.6.0v-.5a1 1 0 0 0-.3-.7L9.5 5.4l.1-3.2a1 1 0 0 0-.1-.7z" />
      </svg>
    );
  }
  if (state === "working") {
    return (
      <span
        className="flex w-[14px] shrink-0 items-center justify-center gap-[2px]"
        aria-label={labels.working}
        title={labels.working}
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
  if (state === "needs_input") {
    // Awaiting the user → amber filled dot.
    return (
      <span
        className="block size-[7px] shrink-0 rounded-full bg-[#ffd014]"
        aria-label={labels.needsInput}
        title={labels.needsInput}
      />
    );
  }
  if (state === "unread") {
    // Finished, not yet opened → blue filled dot.
    return (
      <span
        className="block size-[7px] shrink-0 rounded-full bg-[#5aa6f2]"
        aria-label={labels.unread}
        title={labels.unread}
      />
    );
  }
  // idle / done & seen → 圆环。圆心用侧栏背景色实心填充，盖住底下穿过的
  // 竖导线（不能整体 opacity-50：填充会半透、线又透出来），描边的半透明
  // 单独调在边框色上。
  return (
    <span
      className="block size-[7px] shrink-0 rounded-full"
      style={{
        background: "var(--bg-sidebar, var(--bg-secondary))",
        border: "1px solid color-mix(in srgb, var(--text-secondary) 50%, transparent)",
      }}
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

  // `ui-list-item` (global) carries the row box — height, corner, padding,
  // gap-[12px] (which + the 16px marker slot aligns titles to the nav rows'
  // icon-slot), colour-transition, and the hover --bg-hover tint. Only the
  // chat-row extras stay here.
  const base =
    "ui-list-item group relative shrink-0 overflow-hidden" +
    " leading-[20px] whitespace-nowrap";
  // Selected row: a background highlight marks it; the text steps down
  // from pure white to the warm off-white (--text-primary) so it isn't
  // glaringly bright.
  const colorCls = active ? "bg-bg-hover text-text-primary" : "text-text-primary";
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
        {/* Leading status marker (Claude-Code-style). Priority: pinned →
            live running task → backend status (needs_input / unread) →
            idle. status/unread are backend-fed; until the server sends
            them, rows simply show working or idle.
            Wrapped in a 16px-wide centred slot — the SAME width as the
            nav rows' icon slot (sidebarNavIconClass) — so with the row's
            12px gap the conversation title lines up at the exact same
            left indent as New chat / Functions / … above. */}
        <span className="flex w-[16px] shrink-0 items-center justify-center">
          <StatusMarker
            pinned={!!conv.pinned}
            state={
              // needs_input outranks working: a session waiting on the user
              // shows amber even while its run is technically still active.
              conv.status === "needs_input"
                ? "needs_input"
                : running
                  ? "working"
                  : conv.unread
                    ? "unread"
                    : "idle"
            }
            labels={{
              pinned: t("sidebar.pinned"),
              working: t("sidebar.running"),
              needsInput: t("sidebar.needs_input"),
              unread: t("sidebar.unread"),
            }}
          />
        </span>

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
            className="flex-1 min-w-0 rounded-[var(--ui-button-radius)] border border-[var(--accent-orange)]
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
            className="absolute right-[4px] top-1/2 flex size-[24px] -translate-y-1/2
              items-center justify-center rounded-[6px] text-text-muted
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
        className="w-auto border-0 bg-transparent p-0 text-[var(--text-primary)] shadow-none"
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
