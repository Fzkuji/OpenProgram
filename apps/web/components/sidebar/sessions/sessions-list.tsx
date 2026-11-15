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
 * (and the `runtimeState.conversations` heavy map the top-bar still
 * reads) for instant feedback before the server's echo lands.
 */

import { copyText } from "@/lib/clipboard";
import { memo, useCallback, useEffect, useRef, useState } from "react";
import { useRouter, usePathname } from "next/navigation";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { useCurrentSessionId } from "../use-window-globals";
import { useSessionStore } from "@/lib/session-store";
import type { ConvSummary } from "@/lib/session-store";
import { useCenterTabs } from "@/lib/tabs/center-tabs-store";
import { useTranslation } from "@/lib/i18n";
import { useRecentsView, setRecentsView } from "@/lib/prefs/recents-view";
import { autoRenameSession } from "@/lib/chat/session-auto-rename";
import { wsRequest } from "@/lib/net/ws/ws-request";
import { projectGroups, moveProject, filterProjectItems } from "@/lib/projects/project-groups";
import { EMPTY_SELECTION, pickChat, type ChatSelection } from "@/lib/sidebar/chat-selection";
import { RecentsFilter } from "./recents-filter";
import { SectionHeader } from "./section-header";
import styles from "../sidebar.module.css";
import { ProjectMenu, ProjectSectionHeading } from "../projects/project-menu";
import type { EditableProject } from "../projects/project-editor";
import { useProjectDrag } from "./use-project-drag";
import { useChatDrag } from "./use-chat-drag";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { pushPath } from "@/lib/nav/shallow-nav";
import { runtimeState } from "@/lib/runtime-bridge/state";
import { newSession } from "@/lib/runtime-bridge/session/conversations";
import { deleteSessionAndRefocus } from "@/lib/tabs/navigation/open-session";
import { type LegacyConv, wsSend, labelFor } from "./helpers";
import { recentsConversationsEqual, runningIdSetEqual } from "./recents-select";
import { buildSections } from "./sections";
import { ProjectGroupHeader } from "./project-group-header";
import { ConvItem } from "./conv-item";

interface SidebarProject extends EditableProject {
  id: string;
  name: string;
  path: string;
  is_default: boolean;
  session_count: number;
  session_ids?: string[];
  custom_name?: boolean;
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

/** The last project list the server sent and the last collapsed projects
 *  read or written. The list unmounts with the collapsed sidebar and mounts
 *  again when it opens; starting from these, a reply or a read that brings
 *  nothing new changes nothing, so the list does not render again (and
 *  restyle every row) while the sidebar is still sliding open. The first
 *  mount starts empty, as the server render does. */
let lastProjects: SidebarProject[] = [];
let lastCollapsedProjects = new Set<string>();
const sameProjects = (a: SidebarProject[], b: SidebarProject[]) =>
  a.length === b.length && JSON.stringify(a) === JSON.stringify(b);
const sameSet = (a: Set<string>, b: Set<string>) =>
  a.size === b.size && [...a].every((item) => b.has(item));

export const SessionsList = memo(function SessionsList({ onNewChat }: { onNewChat: (options?: { fresh?: boolean }) => string }) {
  const router = useRouter();
  const pathname = usePathname();
  const { t, text, locale } = useTranslation();
  // Recents only cares about list fields + which ids are running. Other
  // summary writes (workspace_alignment, running-task payloads) must not
  // rebuild the table.
  const conversations = useSessionStore((s) => s.conversations, recentsConversationsEqual);
  const upsertConversation = useSessionStore((s) => s.upsertConversation);
  const clearConversations = useSessionStore((s) => s.clearConversations);
  const currentId = useCurrentSessionId();
  const runningTasks = useSessionStore((s) => s.runningTasks, runningIdSetEqual);
  const view = useRecentsView();

  /* ---- project mode (Group-by → Project): registry-backed tree ---- */

  const projectMode = view.groupBy === "project";
  const [orderNotice, setOrderNotice] = useState("");
  const [selection, setSelection] = useState<ChatSelection>(EMPTY_SELECTION);
  // Rows in the order this render lays them out, for Shift-click runs.
  const rowOrder: string[] = [];
  const lastRowOrder = useRef<string[]>([]);
  useEffect(() => { lastRowOrder.current = rowOrder; });
  useEffect(() => {
    if (!selection.ids.length) return;
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") setSelection(EMPTY_SELECTION); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selection.ids.length]);
  const { draggingProject, projectOffset, headerProps } = useProjectDrag(projectMode, reorderProject);
  const { dropProject, dropGrow, rowProps, headerOffset: chatHeaderOffset } = useChatDrag(projectMode, view.sort === "recency",
    (sessionId, projectId, prevId, nextId) => void dropChat(sessionId, projectId, prevId, nextId));
  // In project lists a chat dragged to a place sorts by that place's time
  // until it has activity of its own again (sort_at / sort_since).
  const placedAt = (c: LegacyConv) => projectMode && c.sort_at && (c.updated_at || 0) <= (c.sort_since || 0)
    ? c.sort_at : (c.updated_at || c.created_at || 0);
  const reducedMotion = useReducedMotion();

  const [projects, setProjects] = useState<SidebarProject[]>(() => lastProjects);
  const refreshProjects = useCallback(async (): Promise<boolean> => {
    const data = await wsRequest<{ projects: SidebarProject[] }>(
      "list_projects",
      { session_id: "" },
      "projects_list",
    );
    if (data?.projects) {
      const next = data.projects;
      // Even an updater that returns the same list renders the list once.
      if (!sameProjects(lastProjects, next)) {
        lastProjects = next;
        setProjects(next);
      }
      return true;
    }
    return false;
  }, []);

  // The registry supplies both project grouping and stable-ID filtering. It
  // re-runs when the session SET changes (create / delete — also what a
  // WS reconnect's list_sessions replay produces), since the registry's
  // reverse index may have gained/lost bindings; `project-changed`
  // (topbar picker / group ＋) refetches too. On a fresh page the
  // WebSocket may not be OPEN yet, so retry (~6s) until it answers —
  // same pattern as the topbar ProjectBadge.
  const convIdsKey = Object.keys(conversations).sort().join(",");
  useEffect(() => {
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
    () => lastCollapsedProjects,
  );
  useEffect(() => {
    const stored = readCollapsedProjects();
    if (sameSet(lastCollapsedProjects, stored)) return;
    lastCollapsedProjects = stored;
    setCollapsedProjects(stored);
  }, []);
  function writeCollapsedProjects(next: Set<string>) {
    lastCollapsedProjects = next;
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
  // this project against that chat key until its chat_ack arrives. Never a
  // reused draft: that one may already belong to another project.
  function newSessionInProject(projectId: string) {
    const draftId = onNewChat({ fresh: true });
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

  function switchTo(id: string, title: string, newTab = false) {
    // Navigate the active session tab before the route guard. From a web
    // or file tab this creates a session tab even if the route is unchanged.
    // A chat whose turn is still running keeps its tab: the clicked chat
    // opens beside it instead of replacing what the user is watching.
    const tabs = useCenterTabs.getState();
    const active = tabs.tabs.find((tab) => tab.id === tabs.activeId);
    const activeRunning = active?.kind === "session" && !!active.sessionId
      && !active.navigationRoute && !!useSessionStore.getState().runningTasks[active.sessionId];
    tabs.openSessionTab(id, title, { newTab: newTab || activeRunning });
    if (id === currentId && pathname === "/s/" + id) return;
    pushPath("/s/" + id);
  }

  const [confirm, setConfirm] = useState<{
    title: string;
    message: string;
    run: () => void;
  } | null>(null);

  /* ---- action senders (optimistic patch + WS) ------------------- */

  // Optimistically patch BOTH the store (drives this sidebar) and the
  // runtimeState.conversations heavy map (still read by the top-bar
  // title / status-source badge machinery) so a rename / pin shows
  // instantly everywhere before the server's session_updated echo lands.
  function patchConv(id: string, fields: Partial<ConvSummary>) {
    const conv = runtimeState.conversations[id];
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
    const url = `${location.origin}/s/${encodeURIComponent(id)}`;
    void copyText(url).then(success => showToast(
      success ? t("sidebar.link_copied") : `${t("sidebar.link_copy_failed")} ${url}`,
    ));
  }
  // The endpoint answers with Content-Disposition: attachment and rides
  // the same session cookie as every other call, so pointing the browser
  // at it downloads the file — no fetch/blob dance needed.
  function exportSession(id: string, format: "md" | "html") {
    const a = document.createElement("a");
    a.href = `/api/sessions/${encodeURIComponent(id)}/export?format=${format}`;
    a.download = `${id}.${format}`;
    document.body.appendChild(a);
    a.click();
    a.remove();
  }
  function del(id: string) {
    const conv = conversations[id] as { title?: string } | undefined;
    const title = conv?.title || t("sidebar.untitled");
    setConfirm({
      title: t("sidebar.delete_chat"),
      message: locale === "zh"
        ? `确定要删除「${title}」吗？`
        : `Are you sure you want to delete "${title}"?`,
      run: () => deleteSessionAndRefocus(id),
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
        wsSend({ action: "clear_sessions" });
        for (const k of Object.keys(runtimeState.conversations)) {
          delete runtimeState.conversations[k];
        }
        clearConversations();
        newSession();
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
    if (projectMode || view.status === "active") arr = arr.filter((c) => !c.archived);
    else if (view.status === "archived") arr = arr.filter((c) => !!c.archived);
    // Last-activity window — updated_at（后端随消息追加维护），老行
    // 无 updated_at 时退回 created_at。"all" = no window.
    if (view.lastActivity !== "all") {
      const days = view.lastActivity === "1d" ? 1 : view.lastActivity === "7d" ? 7 : 30;
      const cutoff = nowTs - days * 86400;
      arr = arr.filter((c) => (c.updated_at || c.created_at || 0) >= cutoff);
    }
    // Match registry membership by ID so renaming a project preserves the filter.
    if (view.project && view.project !== "all") {
      arr = filterProjectItems(projects, arr, view.project);
    }
    const cmp = (a: LegacyConv, b: LegacyConv) => {
      if (!!a.pinned !== !!b.pinned) return a.pinned ? -1 : 1;
      if (view.sort === "title") {
        return labelFor(a, "").localeCompare(labelFor(b, "")) * (view.sortDirection === "asc" ? 1 : -1);
      }
      // "created" 按创建时间；"recency" 按最后活跃（updated_at，随消息
      // 追加更新），老行缺 updated_at 时退回 created_at。缺失时间戳一律
      // 按 0（最旧）处理，不能退回 nowTs——否则 null 时间戳的老行会压过
      // 刚建的会话。
      if (view.sort === "created") {
        return ((b.created_at || 0) - (a.created_at || 0)) * (view.sortDirection === "asc" ? -1 : 1);
      }
      return (placedAt(b) - placedAt(a)) * (view.sortDirection === "asc" ? -1 : 1);
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
  // "archived" narrows.) Empty and archived-only projects are always hidden.
  const filtering =
    (!projectMode && view.status === "archived") ||
    view.lastActivity !== "all" ||
    (view.project !== "" && view.project !== "all");

  // Recomputed per render like `visible` — the list is small and the
  // inputs (visible, projects) change together anyway. Sessions with no
  // explicit project claim belong to the DEFAULT project (the backend's
  // project_for_session falls back to it) — there is no separate
  // "Ungrouped" bucket in this mode. Manual project order does not alter
  // membership or the session order from `visible`.
  const projectSection = (id: string) => view.pinnedProjects.includes(id) ? "__pinned__" : (view.projectSectionNames.includes(view.projectSections[id]) ? view.projectSections[id] : "");
  const sectionOrder = ["__pinned__", ...view.projectSectionNames, ""];
  const groupedProjects = projectMode ? projectGroups(projects, visible, view.projectOrder, { sort: view.projectSort, pinned: view.pinnedProjects, activityItems: convArr })
    .sort((a,b) => sectionOrder.indexOf(projectSection(a.key)) - sectionOrder.indexOf(projectSection(b.key))) : [];
  function reorderProject(source: string, target: string, side: "before" | "after") {
    // Include hidden/empty projects so filtering cannot discard their position.
    const displayed = projectGroups(projects, convArr, view.projectOrder, { sort: view.projectSort, pinned: view.pinnedProjects, includeEmpty: true, includeHidden: true }).map(g => g.key);
    const fallback = [...projects].sort((a, b) => Number(b.is_default) - Number(a.is_default) || a.name.localeCompare(b.name));
    const ids = new Set(projects.map((p) => p.id));
    const order = [...new Set([...displayed, ...view.projectOrder.filter((id) => ids.has(id)), ...fallback.map((p) => p.id)])];
    if (source === target || !ids.has(source) || !ids.has(target)) return;
    try {
      setRecentsView({ projectSort: "manual", projectOrder: moveProject(order, source, target, side),
        pinnedProjects: view.pinnedProjects.includes(target) ? [...new Set([...view.pinnedProjects,source])] : view.pinnedProjects.filter(id=>id!==source),
        projectSections: view.pinnedProjects.includes(target) ? view.projectSections : {...view.projectSections,[source]:view.projectSections[target]||""},
      });
      setOrderNotice(text("Project order saved", "项目顺序已保存"));
    } catch {
      setOrderNotice(text("Order changed, but could not be saved on this device", "顺序已更改，但无法保存在此设备上"));
    }
  }

  // A chat moved into another project: the server moves its repository and
  // records the move; the groups update here at once so the row does not
  // wait for the round trip, and the registry answer settles them.
  async function moveChat(sessionId: string, projectId: string) {
    const target = projects.find((p) => p.id === projectId);
    if (!target || (target.session_ids || []).includes(sessionId)) return false;
    setProjects((items) => items.map((p) => ({
      ...p,
      session_ids: p.id === projectId
        ? [...(p.session_ids || []), sessionId]
        : (p.session_ids || []).filter((id) => id !== sessionId),
    })));
    const reply = await wsRequest<{ ok: boolean; error?: string | null }>(
      "move_session_to_project", { session_id: sessionId, project_id: projectId }, "session_moved");
    if (reply?.ok) {
      setOrderNotice(text(`Moved to ${target.name}`, `已移到“${target.name}”`));
      window.dispatchEvent(new Event("project-changed"));
      return true;
    }
    void refreshProjects();
    showToast(/running/.test(reply?.error || "")
      ? text("This chat is running; wait for it to finish before moving it.", "这个聊天正在运行，等它结束再移动。")
      : text("Could not move the chat.", "没能移动这个聊天。"));
    return false;
  }
  async function moveChats(ids: string[], projectId: string) {
    const target = projects.find((p) => p.id === projectId);
    let moved = 0;
    for (const id of ids) if (await moveChat(id, projectId)) moved += 1;
    if (target && moved > 1) setOrderNotice(text(`Moved ${moved} chats to ${target.name}`, `已把 ${moved} 个聊天移到“${target.name}”`));
  }
  // A dropped chat: into the other project first, then at the place it
  // was let go.
  async function dropChat(sessionId: string, projectId: string, prevId: string | null, nextId: string | null) {
    if (projectId !== projectOf(sessionId) && !(await moveChat(sessionId, projectId))) return;
    placeChat(sessionId, prevId, nextId);
  }
  // The chat takes a time between its new neighbours' (now, at the top),
  // so the list stays in time order: later activity elsewhere passes it,
  // and its own next activity ends the placement. Pinned rows come first
  // regardless, so a neighbour of the other kind is no neighbour.
  function placeChat(id: string, prevId: string | null, nextId: string | null) {
    const me = conversations[id] as LegacyConv | undefined;
    if (!me) return;
    const same = (other: LegacyConv | undefined) => other && !!other.pinned === !!me.pinned ? other : undefined;
    const prev = same(prevId ? conversations[prevId] as LegacyConv : undefined);
    const next = same(nextId ? conversations[nextId] as LegacyConv : undefined);
    const now = Date.now() / 1000;
    const sortAt = prev && next ? (placedAt(prev) + placedAt(next)) / 2
      : prev ? placedAt(prev) - 1 : next ? Math.max(now, placedAt(next) + 1) : now;
    patchConv(id, { sort_at: sortAt, sort_since: me.updated_at || 0 });
    wsSend({ action: "update_session_flags", session_id: id, sort_at: sortAt });
  }
  /** A project header's drag props, moved aside for a carried chat. */
  const shiftedBy = (props: React.HTMLAttributes<HTMLDivElement>, y: number) =>
    y ? { ...props, style: { ...props.style, transform: `translateY(${y}px)` } } : props;
  // The project a chat sits in, by the registry; unclaimed chats are the
  // home project's.
  const projectOf = (sessionId: string) =>
    projects.find((p) => (p.session_ids || []).includes(sessionId))?.id
      ?? projects.find((p) => p.is_default)?.id ?? "";

  const renderRow = (c: LegacyConv) => {
    const label = labelFor(c, t("sidebar.untitled"));
    const home = projectOf(c.id);
    rowOrder.push(c.id);
    const inSelection = selection.ids.includes(c.id);
    const bulkIds = inSelection && selection.ids.length > 1 ? selection.ids : null;
    const visibleProjects = projects.filter((p) => !p.hidden).map((p) => ({ id: p.id, name: p.name }));
    return (
      <ConvItem
        key={c.id}
        conv={c}
        label={label}
        active={c.id === currentId}
        selected={inSelection}
        bulk={bulkIds ? {
          count: bulkIds.length,
          projects: projects.length > 1 ? visibleProjects : undefined,
          onMoveToProject: (projectId) => { setSelection(EMPTY_SELECTION); void moveChats(bulkIds, projectId); },
          onMoveToGroup: (group) => { for (const id of bulkIds) setFlags(id, { group }); setSelection(EMPTY_SELECTION); },
          onArchive: () => { for (const id of bulkIds) setFlags(id, { archived: true }); setSelection(EMPTY_SELECTION); },
          onClear: () => setSelection(EMPTY_SELECTION),
        } : undefined}
        running={!!runningTasks[c.id]}
        groups={allGroups}
        projects={projects.length > 1 ? projects.filter((p) => p.id !== home && !p.hidden).map((p) => ({ id: p.id, name: p.name })) : undefined}
        onMoveToProject={(projectId) => void moveChat(c.id, projectId)}
        dragProps={projectMode ? rowProps(c.id, home) : undefined}
        onClick={(event) => {
          if (event && (event.metaKey || event.ctrlKey || event.shiftKey)) {
            setSelection(pickChat(lastRowOrder.current, selection, currentId, c.id, event.shiftKey ? "range" : "toggle"));
            return;
          }
          if (selection.ids.length) setSelection(EMPTY_SELECTION);
          switchTo(c.id, label);
        }}
        onOpenInNewTab={() => switchTo(c.id, label, true)}
        onRename={(title) => renameSession(c.id, title)}
        onAutoRename={() => void autoRenameSession(c.id)}
        onTogglePin={() => setFlags(c.id, { pinned: !c.pinned })}
        onToggleArchive={() => setFlags(c.id, { archived: !c.archived })}
        onMoveToGroup={(g) => setFlags(c.id, { group: g })}
        onCopyLink={() => copyLink(c.id)}
        onExport={(format) => exportSession(c.id, format)}
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
        sortDirection: view.sortDirection,
        nowTs,
        locale,
        isWorking,
        labels: {
          pinned: t("sidebar.pinned"),
          recents: t("sidebar.recents"),
          today: t("sidebar.today"),
          past7: t("sidebar.activity_7d"),
          working: t("sidebar.working"),
          completed: t("sidebar.completed"),
        },
      });

  // The filter button rides on the FIRST section header's right (Claude
  // layout — no separate "Recents" bar). When the list is flat (title
  // sort → one header-less section) or empty, a thin right-aligned row
  // carries it instead so it's always reachable.
  // Every labelled section is collapsible — date buckets (Today /
  // Past 7 days / …) just as much as State / Project — each with the
  // small right-side ⌄ toggle. (A flat title-sort run has no header,
  // so nothing to collapse there.)
  const collapsible = true;
  const isEmpty = visible.length === 0;
  const firstHasHeader = sections.length > 0 && sections[0].label !== "";
  // Pinned, custom sections, and Projects are peer lists with independent folds.
  const PROJECTS_SECTION_KEY = "__projects__";
  const projectSectionKey = (section: string) => section ? `project-section:${section}` : PROJECTS_SECTION_KEY;
  const visibleProjectGroups = groupedProjects.filter(group => !collapsedGroups.has(projectSectionKey(projectSection(group.key))));
  const projectLists = sectionOrder.map(section => ({ section, groups: groupedProjects.filter(group => projectSection(group.key) === section) }))
    .filter(({ section, groups }) => section !== "__pinned__" || groups.length > 0);
  const body = projectMode ? (
    <>
      {projectLists.map(({ section, groups }) => {
        const key = projectSectionKey(section);
        const folded = collapsedGroups.has(key);
        const name = section === "__pinned__" ? text("Pinned", "置顶") : section || text("Projects", "项目");
        return (
          // Rows sit 1px apart; the header keeps its own 6px to the first
          // row (section-header.tsx), so no 1px is added after it.
          <section key={key} aria-label={name} className="group/sec flex flex-col [&>*+*+*]:mt-px">
            <ProjectSectionHeading
              section={section}
              collapsed={folded}
              onToggle={() => toggleGroupCollapse(key)}
              actions={section === "" ? <RecentsFilter projects={projects} /> : undefined}
            />
            {!folded && (projects.length === 0 && section === ""
              ? visible.map(renderRow)
              : groups.map(g => {
                const expanded = filtering ? true : !collapsedProjects.has(g.key);
                const project = projects.find(project => project.id === g.key)!;
                return (
                  <motion.div layout="position" data-project-id={g.key}
                    key={g.key}
                    className={`${styles.projectGroup} flex flex-col gap-px`}
                    animate={{ y: projectOffset(g.key), scale: draggingProject?.id === g.key ? 1.02 : 1 }}
                    transition={reducedMotion || draggingProject?.id === g.key ? { duration: 0 } : { type: "spring", stiffness: 600, damping: 40 }}
                    style={{ zIndex: draggingProject?.id === g.key ? 5 : undefined,
                      // The drop frame (::after) reaches past the box to take in the gap the rows opened.
                      ...(dropProject === g.key ? { "--drop-grow-top": `${dropGrow.top}px`, "--drop-grow-bottom": `${dropGrow.bottom}px` } as React.CSSProperties : {}) }}
                    data-dragging={draggingProject?.id === g.key || undefined}
                    data-drop-target={dropProject === g.key || undefined}
                  >
                    <ProjectMenu project={project}
                      onOpen={()=>{router.push(`/projects?project=${encodeURIComponent(g.key)}`);}}
                      onNewSession={()=>newSessionInProject(g.key)}
                      onSaved={updated=>setProjects(items=>items.map(item=>item.id===updated.id?{...item,...updated}:item))}>
                    {menuTrigger=><ProjectGroupHeader
                      menuTrigger={menuTrigger}
                      icon={project.icon}
                      dragProps={shiftedBy(headerProps(g.key), chatHeaderOffset(g.key))}
                      onMove={(direction) => {
                        const target = visibleProjectGroups[visibleProjectGroups.findIndex(group => group.key === g.key) + direction];
                        if (target) {
                        reorderProject(g.key, target.key, direction < 0 ? "before" : "after");
                        // Crossing section parents remounts the header; retain keyboard focus.
                        requestAnimationFrame(() => document.querySelector<HTMLElement>(`[data-project-id="${CSS.escape(g.key)}"] [aria-keyshortcuts]`)?.focus());
                      }
                      }}
                      reorderHint={text("Drag to reorder; Alt+Up/Down to move", "拖动排序；Alt+上下方向键移动")}
                      name={g.name}
                      path={g.path}
                      collapsed={!expanded}
                      onToggle={() => toggleProjectCollapse(g.key)}
                      onNewSession={() => newSessionInProject(g.key)}
                      newSessionTitle={text(
                        "New session in this project",
                        "在此项目新建会话",
                      )}
                    />}
                    </ProjectMenu>
                    <AnimatePresence initial={false}>
                      {expanded && g.items.length > 0 ? (
                        // Level-2 block: dense rows joined by the 1px vertical
                        // guide (see .convMarker in the module CSS).
                        // Expands / collapses by height; clipped only while
                        // moving, since the guide pokes 4px above the block.
                        <motion.div
                          key="kids"
                          className={`${styles.projectKids} flex flex-col gap-px`}
                          initial={{ height: 0, opacity: 0, overflow: "hidden" }}
                          animate={{ height: "auto", opacity: 1, transitionEnd: { overflow: "visible" } }}
                          exit={{ height: 0, opacity: 0, overflow: "hidden" }}
                          transition={reducedMotion
                            ? { duration: 0 }
                            : { height: { duration: 0.24, ease: [0.2, 0.7, 0.2, 1] }, opacity: { duration: 0.16 } }}
                        >
                          {g.items.map(renderRow)}
                        </motion.div>
                      ) : null}
                    </AnimatePresence>
                  </motion.div>
                );
              }))}
            {!folded && section === "" && filtering && projects.length > 0 && groupedProjects.length === 0 ? (
              <div className="px-[16px] py-[10px] text-fs-xs text-text-muted">
                {text("No matches", "没有匹配的会话")}
              </div>
            ) : null}
          </section>
        );
      })}
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
          actions={<RecentsFilter projects={projects} />}
        />
      ) : !firstHasHeader ? (
        <div className="flex h-[24px] items-center justify-end px-[8px]">
          <RecentsFilter projects={projects} />
        </div>
      ) : null}
      {sections.map((sec, i) =>
        sec.label === "" ? (
          // Flat run (title sort, no grouping) — no header.
          <div key={sec.key} className="flex flex-col gap-px">{sec.items.map(renderRow)}</div>
        ) : (
          // group/sec → hovering anywhere in the section reveals its
          // collapse chevron (hidden otherwise). Rows sit 1px apart; the
          // header keeps its own 6px to the first row.
          <div key={sec.key} className="group/sec flex flex-col [&>*+*+*]:mt-px">
            <SectionHeader
              name={sec.label}
              collapsible={collapsible}
              collapsed={collapsedGroups.has(sec.key)}
              onToggle={() => toggleGroupCollapse(sec.key)}
              actions={i === 0 ? <RecentsFilter projects={projects} /> : undefined}
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

      <span className="sr-only" role="status" aria-live="polite">{orderNotice}</span>
      {/* Hide Clear all when every project list is folded. */}
      {!isEmpty && !(projectMode && projectLists.every(({ section }) => collapsedGroups.has(projectSectionKey(section)))) ? (
        <div className={styles.clearAll} onClick={clearAll}>
          {t("sidebar.clear_all")}
        </div>
      ) : null}
      {toast ? (
        <div
          className="pointer-events-none fixed bottom-[80px] left-1/2 z-[200] -translate-x-1/2
            rounded-full bg-bg-tertiary px-3 py-1.5 text-fs-xs
            text-text-bright shadow-[var(--shadow-popover)] border border-border"
        >
          {toast}
        </div>
      ) : null}
      {confirm ? (
        <ConfirmDialog
          title={confirm.title}
          description={confirm.message}
          confirmLabel={t("sidebar.delete")}
          destructive
          onCancel={() => setConfirm(null)}
          onConfirm={() => {
            confirm.run();
            setConfirm(null);
          }}
        />
      ) : null}
    </>
  );
});
