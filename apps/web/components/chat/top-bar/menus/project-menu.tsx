"use client";

/**
 * Project menu — content of the topbar `<ProjectBadge />` popover.
 *
 * Claude-Code-style project picker whose contents depend on whether the
 * conversation's main directory is still open to choice:
 *
 *   * **Draft session** (no session_id yet) — the full picker: choose
 *     among registered projects, listed under the machine each is on
 *     (this computer first; a project on a machine that is not connected
 *     is greyed), or open a new folder: "Open folder on…" picks the
 *     machine first, then the folder on it. The choice rides on the first
 *     chat frame; the project's machine is where the conversation runs.
 *   * **Active session** (has turns) — the main directory is frozen, so
 *     the picker is gone. The menu shows the bound project read-only.
 *     The one action left is the repair below.
 *   * **Missing directory** — the bound folder is gone from disk. A
 *     warning line plus "Locate folder…", which relocates the PROJECT to
 *     a new path (the binding itself never moves).
 *
 * Backend requests use one-shot ``wsRequest`` request/response pairs
 * (``list_projects`` → ``projects_list``, etc.). A draft-only project
 * choice is stored in the session store until chat_ack creates the session.
 * Positioning / click-outside come from the shadcn <Popover> in index.tsx.
 */
import { useEffect, useRef, useState, useCallback } from "react";
import { ChevronRight } from "lucide-react";
import { SolarIcon } from "@/components/solar-icons";
import { MachineIcon, useMachineName } from "@/components/machines/machine-icon";
import { MachineChoiceRows, useOpenFolderOn, type FolderOnMachine } from "@/components/machines/open-folder-on";

import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { HoverTip, TipBody } from "@/components/ui/tooltip";
import { useSessionStore } from "@/lib/session-store";
import { closeAllPopovers } from "@/lib/runtime-bridge/ui";
import { useTranslation } from "@/lib/i18n";
import { wsRequest } from "@/lib/net/ws/ws-request";
import { listSessionProjects } from "@/lib/projects/list-projects";
import { duplicateNamePrefixes } from "@/lib/projects/duplicate-names";
import { canConfirmInPlace, confirmProjectFolder, locationMessage, locationProblem, relocateProject, type RelocateReply } from "@/lib/projects/project-location";
import { useBoundChat } from "../bound-chat";
import { CHECK_SLOT, CHECK_SLOT_PAD, GROUP_LABEL, MENU_NOTE, MENU_NOTE_ERROR, MENU_PANEL, MENU_SEPARATOR, itemCls } from "@/components/ui/menu/menu-styles";
import { cn } from "@/lib/utils";
import { MenuCheck } from "@/components/ui/menu/menu-check";
import { GitChip } from "../chips/git-chip";
import { FOLDER_PILL } from "../chips/folder-pill";
import { onReconnected } from "@/lib/net/ws/changes";
import {
  LOCAL_ID,
  ensureMachines,
  findMachine,
  machineKeepsFiles,
  machineOnline,
  projectMachineId,
  useMachines,
  type MachineInfo,
} from "@/lib/machines/store";

/** Fired whenever the conversation's project changes so the topbar chip
 * re-fetches its label without a store round-trip. */
function notifyProjectChanged() {
  window.dispatchEvent(new Event("project-changed"));
}

interface Project {
  id: string;
  name: string;
  path: string;
  is_default: boolean;
  /** The machine the folder is on; its conversations run there. */
  machine_id?: string;
  custom_name?: boolean;
  hidden?: boolean;
  /** Backend-computed: the folder no longer exists on disk. */
  path_missing?: boolean;
  path_replaced?: boolean;
  location_state?: string;
  location_revision?: number;
  migration_error?: string;
  session_count: number;
  unarchived_session_count?: number;
  source_folders?: string[];
}

export function ProjectMenu({
  onClose,
  pickFolder,
  openOn,
}: {
  onClose: () => void;
  pickFolder(start?: string): Promise<string | null>;
  /** The folder for a new project, chosen on the machine picked first. */
  openOn(machine: MachineInfo): Promise<FolderOnMachine | null>;
}) {
  const { text } = useTranslation();
  const machines = useMachines((s) => s.items);
  const kinds = useMachines((s) => s.kinds);
  const machineName = useMachineName();
  const [machineMenuOpen, setMachineMenuOpen] = useState(false);
  const machineMenuTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  // The main shell's chat, or the bound one (split view, an application's
  // conversation). A bound draft has no session yet: it shows its own pending
  // project, never the main shell's chat.
  const { sessionId, chatKey: activeChatKey } = useBoundChat();
  const pendingProjectId = useSessionStore((s) =>
    activeChatKey ? s.pendingProjectsByChat[activeChatKey] ?? null : null,
  );
  const setPendingProject = useSessionStore((s) => s.setPendingProject);
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [currentId, setCurrentId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    const data = await listSessionProjects<{
      projects: Project[];
      current_project_id: string | null;
      session_id: string | null;
    }>(sessionId);
    // An unreadable registry answers `projects: null`; keep what is shown.
    if (data && Array.isArray(data.projects)) {
      setProjects(data.projects);
      setCurrentId(data.current_project_id ?? null);
    } else if (!data) {
      // No answer: an empty first listing, but a re-list never wipes
      // what the open menu already shows.
      setProjects((shown) => shown ?? []);
    }
  }, [sessionId]);

  // The open menu follows the registry: a project created, renamed,
  // relocated or removed anywhere (another tab, the CLI, an agent)
  // arrives as `project-changed` and re-lists in place. Rows are keyed
  // by project id inside the same scrolling list, so its scroll position
  // stays where it was.
  useEffect(() => {
    void ensureMachines();
  }, []);

  useEffect(() => () => clearTimeout(machineMenuTimer.current), []);

  useEffect(() => {
    void refresh();
    const onChanged = () => void refresh();
    window.addEventListener("project-changed", onChanged);
    return () => window.removeEventListener("project-changed", onChanged);
  }, [refresh]);

  useEffect(() => {
    const onDraftError = (event: Event) => {
      const detail = (event as CustomEvent<{ message?: string }>).detail;
      if (detail?.message) setErr(detail.message);
    };
    window.addEventListener("project-draft-error", onDraftError);
    return () => window.removeEventListener("project-draft-error", onDraftError);
  }, []);

  // Locate the missing folder: relocate the PROJECT to the path the
  // user picks. Distinct from switchTo — the session's binding does not
  // move, only the directory the project points at, which is why this
  // stays legal after the main directory has frozen.
  async function locateFolder(projectId: string) {
    const current = projects?.find((project) => project.id === projectId);
    if (!current) return;
    try {
      const path = await pickFolder(current.path);
      if (path) await repair(projectId, () => relocateProject(current, path, sessionId));
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      setErr(text(`Couldn't choose a folder: ${detail}`, `无法选择文件夹：${detail}`));
    }
  }

  async function confirmFolder(projectId: string) {
    const current = projects?.find((project) => project.id === projectId);
    if (current) await repair(projectId, () => confirmProjectFolder(current, sessionId));
  }

  async function repair(projectId: string, run: () => Promise<RelocateReply>) {
    setBusy(true);
    setErr(null);
    try {
      const reply = await run();
      if (!reply.ok) {
        setErr(reply.error ?? text("Relocation failed.", "移动失败。"));
      } else if (reply.project_id && reply.project_id !== projectId) {
        // The folder was already another project: the stale entry folded
        // into it, so pick that project instead.
        await refresh();
        setBusy(false);
        await switchTo(reply.project_id);
        return;
      } else {
        await refresh();
        notifyProjectChanged();
        onClose();
      }
    } catch (error) {
      setErr(error instanceof Error ? error.message : String(error));
    }
    setBusy(false);
  }

  async function switchTo(projectId: string) {
    if (!sessionId) {
      // Each unsent tab has its own provisional key. chat_ack consumes
      // this exact entry and binds the newly-created backend session.
      if (activeChatKey) setPendingProject(activeChatKey, projectId);
      setCurrentId(projectId);
      notifyProjectChanged();
      onClose();
      return;
    }
    setBusy(true);
    await wsRequest(
      "set_session_project",
      { session_id: sessionId, project_id: projectId },
      "session_project_set",
    );
    setBusy(false);
    setCurrentId(projectId);
    notifyProjectChanged();
    onClose();
  }

  // "Open folder on…": the machine first, then the folder on it. This
  // computer uses the worker's native chooser when available and a
  // validated server-path dialog on headless hosts; a machine that keeps
  // its own files is browsed through its shell.
  async function openFolder(machine: MachineInfo) {
    setMachineMenuOpen(false);
    setBusy(true);
    setErr(null);
    try {
      const chosen = await openOn(machine);
      if (chosen) {
        const created = await wsRequest<{
          ok: boolean;
          project?: Project | null;
          error?: string | null;
        }>(
          "create_project",
          { path: chosen.path, machine_id: chosen.machineId, session_id: sessionId ?? "" },
          "project_created",
          undefined,
          // A folder on another machine is checked there over its shell.
          30_000,
        );
        if (created?.ok && created.project?.id) {
          if (activeChatKey) {
            setPendingProject(activeChatKey, created.project.id);
          }
          setCurrentId(created.project.id);
          await refresh();
          notifyProjectChanged();
          onClose();
        } else {
          setErr(
            created?.error ?? text("Couldn't add this project.", "无法添加此项目。"),
          );
        }
      }
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      setErr(text(`Couldn't choose a folder: ${detail}`, `无法选择文件夹：${detail}`));
    }
    setBusy(false);
  }

  const list = projects ?? [];
  // With no session / pending choice yet, the effective project is the
  // default — so the menu's checkmark agrees with the topbar chip.
  const activeId =
    (!sessionId ? pendingProjectId : currentId) ??
    list.find((p) => p.is_default)?.id ??
    null;
  // Same-named projects lead with the parent folder that tells them apart.
  const parents = duplicateNamePrefixes(list.filter((p) => !p.hidden || p.id === activeId));
  // The main directory freezes when the draft becomes a real session,
  // i.e. exactly when a session_id exists. Past that the picker is gone;
  // the backend rejects a rebind anyway (FROZEN_ERROR).
  const frozen = sessionId !== null;
  const activeProject = list.find((p) => p.id === activeId) ?? null;
  const problem = locationProblem(activeProject);
  const missing = problem !== null;

  const errorLine = err ? (
    <div className={cn(MENU_NOTE, MENU_NOTE_ERROR)}>
      {err}
    </div>
  ) : null;

  if (frozen) {
    return (
      <div className={MENU_PANEL}>
        <div className={GROUP_LABEL}>
          {text("Main working directory", "主工作目录")}
        </div>
        <div className={cn(MENU_NOTE, "text-text-secondary")}>
          <div className="truncate">{activeProject ? activeProject.name : "—"}</div>
          {activeProject?.path ? (
            <div className="truncate text-text-muted">
              {activeProject.path}
            </div>
          ) : null}
        </div>
        {missing ? (
          <>
            <div className="project-menu-missing">
              <SolarIcon motionPreset="none" name="danger-triangle" size={14} aria-hidden="true" />
              <span>{activeProject && problem ? locationMessage(problem, activeProject, text) : null}</span>
            </div>
            {canConfirmInPlace(problem) ? (
              <div
                className={itemCls(false)}
                onClick={() => !busy && activeProject && confirmFolder(activeProject.id)}
              >
                <SolarIcon name="check-circle" size={16} fit="fill" className="opacity-70" />
                <span className="flex-1">{text("Use the folder at this location", "就是这个位置的文件夹")}</span>
              </div>
            ) : null}
            <div
              className={itemCls(false)}
              onClick={() => !busy && activeProject && locateFolder(activeProject.id)}
            >
              <SolarIcon name="folder-open" size={16} fit="fill" className="opacity-70" />
              <span className="flex-1">{text("Locate folder…", "定位文件夹…")}</span>
            </div>
          </>
        ) : (
          <div className={MENU_NOTE}>
            {text(
              "Fixed for this conversation. Extra folders can still be added below.",
              "本会话已固定。仍可在下方添加额外目录。",
            )}
          </div>
        )}
        {errorLine}
      </div>
    );
  }

  // Projects under the machine each is on: this computer first, then the
  // machines in their configured order, then any machine no longer there.
  const visible = list.filter((p) => !p.hidden || p.id === activeId);
  const groupIds: string[] = [LOCAL_ID];
  for (const machine of machines) if (!groupIds.includes(machine.id)) groupIds.push(machine.id);
  for (const p of visible) if (!groupIds.includes(projectMachineId(p))) groupIds.push(projectMachineId(p));
  const groups = groupIds
    .map((id) => ({ id, machine: findMachine(machines, id), projects: visible.filter((p) => projectMachineId(p) === id) }))
    .filter((group) => group.projects.length > 0);

  function scheduleMachineMenu(next: boolean) {
    clearTimeout(machineMenuTimer.current);
    machineMenuTimer.current = setTimeout(() => setMachineMenuOpen(next), next ? 80 : 200);
  }

  return (
    // Only the project list scrolls; the machine headers scroll with their
    // projects and "Open folder on…" stays in place however many there are.
    <div className={cn(MENU_PANEL, "overflow-hidden")}>
      <div className="project-menu-list -mx-1 flex min-h-0 flex-1 flex-col overflow-y-auto px-1">
      {groups.map((group) => {
        const offline = group.id !== LOCAL_ID && !machineOnline(group.machine);
        return (
          <div key={group.id} className="flex flex-col" data-project-machine={group.id}>
            <div className={GROUP_LABEL}>
              {group.id === LOCAL_ID ? null : <MachineIcon machine={group.machine} kinds={kinds} fill />}
              <span className="truncate">{machineName(group.machine, group.id)}</span>
              {offline ? <span className="font-normal">{text("offline", "离线")}</span> : null}
            </div>
            {/* 目录已不存在的项目仍然列出（静默消失会让用户以为项目丢了），
                点击它走定位修复而不是选中——选一个死路径当主目录没有意义。 */}
            {group.projects.map((p) => projectRow(p, offline))}
          </div>
        );
      })}
      </div>

      <div className={MENU_SEPARATOR} />

      <Popover open={machineMenuOpen} onOpenChange={setMachineMenuOpen}>
        <PopoverTrigger asChild>
          <div
            className={cn(itemCls(false), "shrink-0")}
            data-open-folder-on=""
            onMouseEnter={() => scheduleMachineMenu(true)}
            onMouseLeave={() => scheduleMachineMenu(false)}
          >
            <SolarIcon name="folder-with-files" size={16} fit="fill" className="opacity-70" />
            <span className="flex-1">{text("Open folder on…", "在…上打开文件夹")}</span>
            <ChevronRight className="menu-row-chevron size-4 shrink-0 text-text-muted" aria-hidden="true" />
          </div>
        </PopoverTrigger>
        <PopoverContent
          side="right"
          align="end"
          sideOffset={8}
          className="w-auto border-0 bg-transparent p-0 shadow-none"
          onMouseEnter={() => scheduleMachineMenu(true)}
          onMouseLeave={() => scheduleMachineMenu(false)}
        >
          <div className={MENU_PANEL}>
            <MachineChoiceRows
              onPick={(machine) => { if (!busy) void openFolder(machine); }}
              onAddMachine={onClose}
            />
          </div>
        </PopoverContent>
      </Popover>

      {errorLine}
    </div>
  );

  function projectRow(p: Project, offline: boolean) {
        const parent = parents.get(p.id);
        const active = p.id === activeId;
        const unavailable = locationProblem(p) !== null;
        return (
          <div
            key={p.id}
            className={cn(itemCls(false), offline && "pointer-events-none opacity-50")}
            aria-disabled={offline || undefined}
            data-tip={
              unavailable
                ? text(
                    "Folder unavailable — click to locate it",
                    "目录不可用 — 点击重新定位",
                  )
                : p.path ||
                  (p.is_default
                    ? text(
                        "Ad-hoc chats — stored in your home folder",
                        "随手聊 — 保存在主目录",
                      )
                    : "")
            }
            onClick={() =>
              // Keep missing-directory routing explicit: p.path_missing ? locateFolder(p.id) : switchTo(p.id)
              !busy && !offline && (p.path_missing ? locateFolder(p.id) : unavailable ? locateFolder(p.id) : switchTo(p.id))
            }
          >
            <span className="min-w-0 flex-1 truncate">
              {parent && <span className="text-text-muted">{parent} / </span>}
              {p.name}
            </span>
            {unavailable ? (
              <SolarIcon
                motionPreset="none"
                name="danger-triangle"
                size={16}
                className={`${CHECK_SLOT} text-accent-orange`}
                aria-label={text("Folder unavailable", "目录不可用")}
              />
            ) : active ? (
              <MenuCheck />
            ) : (
              <span className={CHECK_SLOT_PAD} aria-hidden="true" />
            )}
          </div>
        );
  }
}

/* ---- Topbar chip ------------------------------------------------- */

/**
 * ProjectBadge — the topbar chip that shows the conversation's current
 * project and opens the <ProjectMenu> picker. Mirrors BranchBadge's
 * Popover + `topbar-close-menus` coordination so only one dropdown is
 * ever open.
 *
 * Self-fetches its label over WS (``list_projects`` → ``projects_list``)
 * on mount, when the session changes, and on the ``project-changed``
 * event the menu fires after a switch or bind.
 */
export function ProjectBadge() {
  const { text } = useTranslation();
  // The main shell's chat, or the bound one (split view, an application's
  // conversation). A bound draft has no session yet: it shows its own pending
  // project, never the main shell's chat.
  const { sessionId, chatKey: activeChatKey } = useBoundChat();
  const pendingProjectId = useSessionStore((s) =>
    activeChatKey ? s.pendingProjectsByChat[activeChatKey] ?? null : null,
  );
  const takePendingProject = useSessionStore((s) => s.takePendingProject);
  const [open, setOpen] = useState(false);
  const [label, setLabel] = useState<string>(text("Project", "项目"));
  const [missing, setMissing] = useState(false);
  // Real folder of a user project (not the home-dir default) — the git pill's subject.
  const [gitPath, setGitPath] = useState<string | null>(null);
  const [projectPath, setProjectPath] = useState<string | null>(null);
  const [onDefaultProject, setOnDefaultProject] = useState(false);
  const setPendingProject = useSessionStore((s) => s.setPendingProject);
  const { openOn, pickFolder, dialogs, dialogOpen } = useOpenFolderOn();
  const machines = useMachines((s) => s.items);
  const kinds = useMachines((s) => s.kinds);
  const [projectMachine, setProjectMachine] = useState<string>(LOCAL_ID);

  // Returns true once it has resolved a project (so the caller can stop
  // retrying). On a fresh page load the WebSocket may not be OPEN yet, so
  // `wsRequest` resolves null — we retry below until it answers.
  const refreshLabel = useCallback(async (): Promise<boolean> => {
    const data = await listSessionProjects<{
      projects: Project[];
      current_project_id: string | null;
      session_id: string | null;
    }>(sessionId);
    if (!data || !Array.isArray(data.projects)) return false;
    const projects = data.projects;
    const wantId =
      (!sessionId ? pendingProjectId : data.current_project_id) ??
      data.current_project_id ??
      null;
    let cur = projects.find((p) => p.id === wantId);
    if (!cur) {
      // Drop a stale pending choice and fall back to the default label.
      if (
        activeChatKey &&
        pendingProjectId &&
        !projects.some((p) => p.id === pendingProjectId)
      ) {
        takePendingProject(activeChatKey);
      }
      cur = projects.find((p) => p.is_default);
    }
    if (cur) {
      setLabel(cur.name);
      setMissing(cur.path_missing === true);
      setProjectMachine(projectMachineId(cur));
      setGitPath(!cur.is_default && !cur.path_missing && cur.path ? cur.path : null);
      setProjectPath(cur.path || null);
      setOnDefaultProject(cur.is_default);
      return true;
    }
    return false;
  }, [activeChatKey, pendingProjectId, sessionId, takePendingProject]);

  useEffect(() => {
    let cancelled = false;
    void refreshLabel();
    // Unanswered while the socket is down: asked again as it opens, so the
    // chip does not stay on the "Project" placeholder.
    const offOpen = onReconnected(() => { if (!cancelled) void refreshLabel(); });
    const onChanged = () => refreshLabel();
    window.addEventListener("project-changed", onChanged);
    return () => {
      cancelled = true;
      offOpen();
      window.removeEventListener("project-changed", onChanged);
    };
  }, [refreshLabel]);

  useEffect(() => {
    const close = () => setOpen(false);
    window.addEventListener("topbar-close-menus", close);
    return () => window.removeEventListener("topbar-close-menus", close);
  }, []);

  useEffect(() => {
    void ensureMachines();
  }, []);

  const projectMachineInfo = findMachine(machines, projectMachine);
  const folderHere =
    projectMachine === LOCAL_ID || (!!projectMachineInfo && !machineKeepsFiles(projectMachineInfo, kinds));

  function onOpenChange(next: boolean) {
    // The headless-host fallback and the remote folder browser are portaled
    // outside the Popover. Keep this menu mounted while that modal owns focus
    // so the pending create/relocate operation can resume after the choice.
    if (!next && dialogOpen) return;
    if (next) {
      window.dispatchEvent(new Event("topbar-close-menus"));
      closeAllPopovers();
    }
    setOpen(next);
  }

  return (
    <>
    <span
      className={
        FOLDER_PILL +
        " runtime-badge project-badge" +
        (missing ? " project-badge-missing" : "")
      }
    >
    <Popover open={open} onOpenChange={onOpenChange}>
      <HoverTip
        label={
          missing ? (
            <TipBody
              title={text(`Project folder missing: ${label}`, `项目目录缺失：${label}`)}
              detail={text("Click to locate the folder.", "点击重新定位目录。")}
            />
          ) : (
            <TipBody
              title={text(`Project: ${label}`, `项目：${label}`)}
              detail={
                <>
                  {onDefaultProject
                    ? text("Ad-hoc chats, stored in your home folder", "随手聊，保存在主目录")
                    : projectPath}
                  <br />
                  {sessionId
                    ? text("Main folder, fixed for this conversation", "主目录，本会话已固定")
                    : text("Click to choose the project folder", "点击选择项目目录")}
                </>
              }
            />
          )
        }
      >
        <PopoverTrigger asChild>
          <span id="projectBadge" role="button" tabIndex={0} className="folder-pill-seg">
          <span className="project-icon" aria-hidden="true">
            {/* Warning triangle replaces the folder icon when the bound
                directory is gone — the menu then offers the repair. */}
            {missing ? (
              <SolarIcon name="danger-triangle" size={16} fit="fill" />
            ) : (
              <SolarIcon name="folder-open" size={16} fit="fill" />
            )}
          </span>
          {/* Always show the project name — "Default" for the unbound
              project, the folder name once a real one is selected. */}
          <span className="badge-short">{label}</span>
          </span>
        </PopoverTrigger>
      </HoverTip>
      <PopoverContent
        side="top"
        align="start"
        sideOffset={10}
        className="w-auto border-0 bg-transparent p-0 shadow-none"
      >
        <ProjectMenu onClose={() => setOpen(false)} pickFolder={pickFolder} openOn={openOn} />
      </PopoverContent>
    </Popover>
    <GitChip
      // Git runs on this computer's folders: a project on a machine that
      // keeps its own files has no git pill or worktrees here.
      path={folderHere ? gitPath : null}
      order={0}
      onUseFolder={moveOntoWorktree}
      useFolderLabel={sessionId ? {
        en: "This conversation's main folder is fixed; another worktree is added as a working folder.",
        zh: "本会话的主目录已固定；其他 worktree 会作为额外工作目录加入。",
      } : undefined}
    />
    </span>
    {dialogs}
    </>
  );

  // Draft: the worktree becomes this chat's project. Started chat: the
  // main folder is frozen, so the worktree joins as a working folder.
  async function moveOntoWorktree(path: string) {
    if (sessionId) {
      window.dispatchEvent(new CustomEvent("op:workdir-add", { detail: { path } }));
      return;
    }
    const created = await wsRequest<{ ok: boolean; project?: Project | null; error?: string | null }>(
      "create_project", { path, session_id: "" }, "project_created",
    );
    if (!created?.ok || !created.project?.id) {
      throw new Error(created?.error ?? text("Couldn't open this worktree as a project.", "无法把该 worktree 作为项目打开。"));
    }
    if (activeChatKey) setPendingProject(activeChatKey, created.project.id);
    notifyProjectChanged();
  }
}
