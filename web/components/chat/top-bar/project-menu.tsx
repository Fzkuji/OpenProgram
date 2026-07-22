"use client";

/**
 * Project menu — content of the topbar `<ProjectBadge />` popover.
 *
 * Claude-Code-style project picker. Lets the user:
 *   * see + switch the conversation's main project (decides where the
 *     session repo is stored: <project>/.openprogram/sessions/<id>/)
 *   * bind a new folder as a project (paste an absolute path)
 *
 * Backend requests use one-shot ``window.ws`` request/response pairs
 * (``list_projects`` → ``projects_list``, etc.). A draft-only project
 * choice is stored in the session store until chat_ack creates the session.
 * Positioning / click-outside come from the shadcn <Popover> in index.tsx.
 */
import { useEffect, useRef, useState, useCallback } from "react";
import { Check, Folder } from "lucide-react";
import {
  type AnimatedNavIconHandle,
  FolderOpenIcon,
} from "@/components/animated-icons";

import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { HoverTip } from "@/components/ui/tooltip";
import { useSessionStore } from "@/lib/session-store";
import { useTranslation } from "@/lib/i18n";
import { wsRequest } from "@/lib/net/ws-request";
import { CHECK_SLOT, CHECK_SLOT_PAD, GROUP_LABEL, MENU_PANEL, MENU_SEPARATOR, itemCls } from "./menu-styles";

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
  session_count: number;
}

export function ProjectMenu({ onClose }: { onClose: () => void }) {
  const { text } = useTranslation();
  const sessionId = useSessionStore((s) => s.currentSessionId);
  const activeChatKey = useSessionStore((s) => s.activeChatKey);
  const pendingProjectId = useSessionStore((s) =>
    s.activeChatKey ? s.pendingProjectsByChat[s.activeChatKey] ?? null : null,
  );
  const setPendingProject = useSessionStore((s) => s.setPendingProject);
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [currentId, setCurrentId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    const data = await wsRequest<{
      projects: Project[];
      current_project_id: string | null;
      session_id: string | null;
    }>(
      "list_projects",
      { session_id: sessionId ?? "" },
      "projects_list",
      // 只认后端回显 session_id 匹配的回复——其他组件（侧栏 Projects
      // 分组等）并发发的 list_projects 不带会话，其 current_project_id
      // 恒为 null，误收会把选中态画到默认项目上。
      (d) => (d.session_id ?? null) === (sessionId || null),
    );
    if (data) {
      setProjects(data.projects || []);
      setCurrentId(data.current_project_id ?? null);
    } else {
      setProjects([]);
    }
  }, [sessionId]);

  useEffect(() => {
    refresh();
  }, [refresh]);

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

  // "Open folder…" → pops the OS-native directory chooser via the
  // worker. NO manual path entry — a button that opens the system
  // dialog, period. On the rare platform with no native dialog we show
  // a one-line hint, never a text box.
  async function openFolder() {
    setBusy(true);
    setErr(null);
    try {
      const res = await fetch("/api/pick-folder", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: "{}",
      });
      const data = res.ok
        ? ((await res.json()) as { path?: string | null; unsupported?: boolean })
        : null;
      if (data?.path) {
        await wsRequest(
          "create_project",
          { path: data.path, session_id: sessionId ?? "" },
          "project_created",
        );
        await refresh();
        notifyProjectChanged();
      } else if (!data || data.unsupported) {
        setErr(
          text(
            "Couldn't open the system folder picker — restart the worker.",
            "无法打开系统文件夹选择器 — 请重启 worker。",
          ),
        );
      }
      // data.path == null (and supported) → user cancelled; no-op.
    } catch {
      setErr(text("Couldn't open the folder picker.", "无法打开文件夹选择器。"));
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

  return (
    <div className={`${MENU_PANEL} min-w-[230px] max-w-[340px]`}>
      <div className={GROUP_LABEL}>{text("Recent", "最近")}</div>

      {list.map((p) => {
        const active = p.id === activeId;
        return (
          <div
            key={p.id}
            className={itemCls(false)}
            title={
              p.path ||
              (p.is_default
                ? text(
                    "Ad-hoc chats — stored in your home folder",
                    "随手聊 — 保存在主目录",
                  )
                : "")
            }
            onClick={() => !busy && switchTo(p.id)}
          >
            <span className="min-w-0 flex-1 truncate">{p.name}</span>
            {active ? (
              <Check size={14} className={CHECK_SLOT} />
            ) : (
              <span className={CHECK_SLOT_PAD} aria-hidden="true" />
            )}
          </div>
        );
      })}

      <div className={MENU_SEPARATOR} />

      <div className={itemCls(false)} onClick={() => !busy && openFolder()}>
        <Folder size={14} strokeWidth={2} className="shrink-0 opacity-70" />
        <span className="flex-1">{text("Open folder…", "打开文件夹…")}</span>
      </div>
      {err ? (
        <div className="px-[8px] pb-[3px] pt-[1px] text-[11px] text-[var(--accent-orange)]">
          {err}
        </div>
      ) : null}
    </div>
  );
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
  const sessionId = useSessionStore((s) => s.currentSessionId);
  const activeChatKey = useSessionStore((s) => s.activeChatKey);
  const pendingProjectId = useSessionStore((s) =>
    s.activeChatKey ? s.pendingProjectsByChat[s.activeChatKey] ?? null : null,
  );
  const takePendingProject = useSessionStore((s) => s.takePendingProject);
  const [open, setOpen] = useState(false);
  const [label, setLabel] = useState<string>(text("Project", "项目"));
  const iconRef = useRef<AnimatedNavIconHandle>(null);

  // Returns true once it has resolved a project (so the caller can stop
  // retrying). On a fresh page load the WebSocket may not be OPEN yet, so
  // `wsRequest` resolves null — we retry below until it answers.
  const refreshLabel = useCallback(async (): Promise<boolean> => {
    const data = await wsRequest<{
      projects: Project[];
      current_project_id: string | null;
      session_id: string | null;
    }>(
      "list_projects",
      { session_id: sessionId ?? "" },
      "projects_list",
      // 同 ProjectMenu.refresh：并发的无会话 list_projects 回复会把
      // 徽标误置回默认项目，按回显的 session_id 只认自己那条。
      (d) => (d.session_id ?? null) === (sessionId || null),
    );
    if (!data) return false;
    const projects = data.projects || [];
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
      return true;
    }
    return false;
  }, [activeChatKey, pendingProjectId, sessionId, takePendingProject]);

  useEffect(() => {
    let cancelled = false;
    let tries = 0;
    const attempt = () => {
      if (cancelled) return;
      refreshLabel().then((ok) => {
        // Retry (~6s) until the socket is open and answers — otherwise the
        // chip would stay stuck on the "Project" placeholder.
        if (!ok && !cancelled && tries++ < 20) setTimeout(attempt, 300);
      });
    };
    attempt();
    const onChanged = () => refreshLabel();
    window.addEventListener("project-changed", onChanged);
    return () => {
      cancelled = true;
      window.removeEventListener("project-changed", onChanged);
    };
  }, [refreshLabel]);

  useEffect(() => {
    const close = () => setOpen(false);
    window.addEventListener("topbar-close-menus", close);
    return () => window.removeEventListener("topbar-close-menus", close);
  }, []);

  function onOpenChange(next: boolean) {
    if (next) {
      window.dispatchEvent(new Event("topbar-close-menus"));
      (
        window as unknown as { _closeAllPopovers?: () => void }
      )._closeAllPopovers?.();
    }
    setOpen(next);
  }

  return (
    <Popover open={open} onOpenChange={onOpenChange}>
      <HoverTip label={text("Project — working folder", "项目 — 工作目录")}>
        <PopoverTrigger asChild>
          <span
            id="projectBadge"
            className="runtime-badge project-badge"
          >
          <span className="project-icon" aria-hidden="true">
            <FolderOpenIcon ref={iconRef} size={14} />
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
        <ProjectMenu onClose={() => setOpen(false)} />
      </PopoverContent>
    </Popover>
  );
}
