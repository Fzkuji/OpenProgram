"use client";

/**
 * /projects — 项目管理。左右分栏（照 Programs 页）：左栏项目列表，
 * 右栏选中项目的内容（Settings 权限规则+项目默认设置 / Chats 会话列表 /
 * Info 元数据）。同页切换，不跳路由。
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { AlertTriangle, FolderSearch } from "lucide-react";

import fx from "@/components/functions/functions-page.module.css";
import { SearchInput } from "@/components/ui/search-input";
import { useFolderPicker } from "@/components/ui/folder-picker";
import styles from "./projects-page.module.css";
import { managePageStyles as shared } from "@/components/ui/manage-page";
import { useTranslation } from "@/lib/i18n";
import { FoldersIcon, FolderPlusIcon } from "@/components/animated-icons";
import { wsRequest } from "@/lib/net/ws-request";
import { formatRelativeTime } from "@/lib/format-utils/format";
import { ProjectEditor, type EditableProject } from "@/components/sidebar/project-editor";
import { Button } from "@/components/ui/button";
import { PermissionsSection } from "./permissions-section";
import { ProjectConfigSection } from "./project-config-section";
import { pushPath } from "@/lib/shallow-nav";

interface Project extends EditableProject {
  id: string;
  name: string;
  path: string;
  is_default: boolean;
  /** Backend-computed: the folder no longer exists on disk. */
  path_missing?: boolean;
  path_replaced?: boolean;
  location_state?: string;
  location_revision?: number;
  migration_error?: string;
  session_count: number;
  unarchived_session_count?: number;
  status: string;
}

interface SessionSummary {
  id: string;
  title: string;
  created_at?: number;
  preview?: string | null;
}

type Tab = "settings" | "sessions" | "info";

function cls(...xs: (string | false | undefined)[]) {
  return xs.filter(Boolean).join(" ");
}

export function ProjectsPage({
  embedded,
  query: queryProp,
}: {
  embedded?: boolean;
  query?: string;
} = {}) {
  const { text, locale } = useTranslation();
  const router = useRouter();
  const requestedProject = useSearchParams()?.get("project");
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("settings");
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [editing, setEditing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [localQuery, setLocalQuery] = useState("");
  const { pickFolder, folderPickerDialog } = useFolderPicker();
  const query = queryProp !== undefined ? queryProp : localQuery;
  const setQuery = setLocalQuery;

  const refresh = useCallback(async () => {
    for (let i = 0; i < 10; i++) {
      const data = await wsRequest<{ projects: Project[] }>(
        "list_projects", {}, "projects_list",
      );
      if (data) {
        const list = data.projects || [];
        setProjects(list);
        setSelectedId((cur) => cur ?? (list[0]?.id ?? null));
        return;
      }
      await new Promise((r) => setTimeout(r, 300));
    }
  }, []);

  useEffect(() => {
    function onChanged() { refresh(); }
    window.addEventListener("project-changed", onChanged);
    refresh();
    return () => window.removeEventListener("project-changed", onChanged);
  }, [refresh]);

  useEffect(() => { if (requestedProject) setSelectedId(requestedProject); }, [requestedProject]);

  const selected = useMemo(
    () => projects.find((p) => p.id === selectedId) || null,
    [projects, selectedId],
  );

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return projects;
    return projects.filter(
      (p) => p.name.toLowerCase().includes(q) || p.path.toLowerCase().includes(q),
    );
  }, [projects, query]);

  const loadSessions = useCallback(async (pid: string) => {
    const d = await wsRequest<{ sessions: SessionSummary[] }>(
      "list_project_sessions", { project_id: pid }, "project_sessions",
    );
    setSessions(d?.sessions || []);
  }, []);

  useEffect(() => {
    if (tab === "sessions" && selectedId) loadSessions(selectedId);
  }, [tab, selectedId, loadSessions]);

  // 定位缺失项目的新目录：改项目 path、保留 id（relocate_project）。
  const locateProject = useCallback(async (projectId: string, startPath = "") => {
    setError(null);
    try {
      const path = await pickFolder(startPath);
      if (!path) return;
      const current = projects.find((project) => project.id === projectId);
      const d = await wsRequest<{ ok: boolean; error?: string | null }>(
        "relocate_project",
        {
          project_id: projectId,
          path,
          expected_path: current?.path || startPath,
          expected_revision: current?.location_revision,
          replace_identity: true,
        },
        "project_relocated",
      );
      if (d && !d.ok) setError(d.error || text("Relocation failed.", "移动失败。"));
      refresh();
    } catch (e) {
      setError(String(e));
    }
  }, [pickFolder, refresh, text]);

  const addProject = useCallback(async () => {
    setError(null);
    try {
      const path = await pickFolder();
      if (!path) return;
      const d = await wsRequest<{ ok: boolean; project?: Project; error?: string }>(
        "create_project", { path }, "project_created",
      );
      if (d?.error) setError(d.error);
      if (d?.project) setSelectedId(d.project.id);
      refresh();
    } catch (e) {
      setError(String(e));
    }
  }, [pickFolder, refresh]);

  const view = (
      <div className={fx.view} style={embedded ? { flex: 1, minHeight: 0, height: "auto" } : undefined}>
        {folderPickerDialog}
        {editing && selected && <ProjectEditor key={selected.id} project={selected} onClose={()=>setEditing(false)} onSaved={updated=>setProjects(items=>items.map(item=>item.id===updated.id?{...item,...updated}:item))}/>}
        {(!embedded || queryProp === undefined) && (
          <div className={fx.topbar}>
            {!embedded && <span className={fx.title}>{text("Projects", "项目")}</span>}
            {queryProp === undefined && (
              <div className={fx.toolbar}>
                <SearchInput
                  className="flex-1 max-w-[320px]"
                  placeholder={text("Search projects...", "搜索项目...")}
                  value={query}
                  onChange={setQuery}
                />
              </div>
            )}
          </div>
        )}

        {error && <div className={shared.errorBar} role="alert">{error}</div>}

        <div className={fx.body}>
          {/* 左栏：项目列表（复用 Functions 的 profilesNav） */}
          <div className={fx.profilesNav}>
            {filtered.map((p) => (
              <div
                key={p.id}
                className={cls(fx.profileItem, p.id === selectedId && fx.active)}
                onClick={() => setSelectedId(p.id)}
              >
                <span className={fx.profileIcon}>{p.icon || <FoldersIcon size={16} />}</span>
                <span className={fx.profileName}>{p.name}</span>
                {p.hidden && <span className={styles.badge}>{text("Hidden", "已隐藏")}</span>}
                {p.is_default && <span className={styles.badge}>{text("Default", "默认")}</span>}
                {(p.path_missing || p.path_replaced ||
                  ["missing", "replaced", "migrating", "pending", "error"].includes(p.location_state ?? "")) ? (
                  <AlertTriangle
                    size={14}
                    strokeWidth={2}
                    className={styles.statusSlot}
                    aria-label={text("Folder missing", "目录缺失")}
                  />
                ) : (
                  <span className={styles.statusSlotPad} aria-hidden="true" />
                )}
              </div>
            ))}
            <div className={fx.profileSep} />
            <div
              className={cls(fx.profileItem, fx.profileNew)}
              onClick={addProject}
            >
              <span className={fx.profileIcon}><FolderPlusIcon size={16} /></span>
              <span className={fx.profileName}>{text("Open folder…", "打开文件夹…")}</span>
            </div>
          </div>

          {/* 右栏：选中项目的内容 */}
          <div className={fx.content}>
            {!selected ? (
              <div className={fx.empty}>{text("Select a project.", "选择一个项目。")}</div>
            ) : (
              <>
                <div className={styles.detailHead}>
                  <span className={styles.detailTitle}>{selected.name}</span>
                  <span className={styles.detailPath}>{selected.path}</span>
                  {selected.hidden&&<Button variant="outline" onClick={async()=>{try{const result=await wsRequest<{ok:boolean;error?:string}>("restore_project",{project_id:selected.id},"restore_project_result");if(!result?.ok)throw new Error(result?.error||"Could not restore project");await refresh();window.dispatchEvent(new Event("project-changed"));}catch(err){setError(String(err));}}}>{text("Restore to sidebar", "恢复到侧边栏")}</Button>}
                  <Button variant="outline" onClick={()=>setEditing(true)}>{text("Edit project", "编辑项目")}</Button>
                </div>
                {(selected.path_missing || selected.path_replaced || (selected.location_state && selected.location_state !== "available")) && (
                  <div
                    role="alert"
                    style={{
                      display: "flex", alignItems: "center", gap: 8,
                      margin: "8px 0", fontSize: 12,
                      color: "var(--accent-orange)",
                    }}
                  >
                    <AlertTriangle size={14} strokeWidth={2} aria-hidden="true" />
                    <span>
                      {selected.path_replaced || selected.location_state === "replaced"
                        ? text(
                            "The folder at this location has changed.",
                            "该位置上的文件夹已更换。",
                          )
                        : selected.location_state === "migrating"
                          ? text(
                              "Migrating conversations. Tasks start after completion.",
                              "正在迁移对话。完成前不能启动任务。",
                            )
                            : selected.location_state === "pending" &&
                                selected.migration_error !== "directory identity unavailable"
                              ? text(
                                  "This legacy conversation has not migrated. Reconnect the original drive to finish.",
                                  "这条旧对话尚未迁移。请接回原来的磁盘后再完成。",
                                )
                              : selected.location_state === "pending"
                                ? text(
                                    "This folder needs confirmation. Locate the original folder to continue.",
                                    "该目录需要确认。请定位原来的目录后继续。",
                                  )
                            : selected.location_state === "error"
                              ? text(
                                  selected.migration_error || "Conversation migration failed.",
                                  selected.migration_error || "对话迁移失败。",
                                )
                              : text(
                                  "This folder no longer exists on disk.",
                                  "该目录已不在磁盘上。",
                                )}
                    </span>
                    <button
                      type="button"
                      onClick={() => locateProject(selected.id, selected.path)}
                      style={{
                        display: "inline-flex", alignItems: "center", gap: 4,
                        cursor: "pointer", background: "none",
                        border: "1px solid var(--accent-orange)",
                        borderRadius: 6, padding: "2px 8px",
                        color: "var(--accent-orange)", fontSize: 12,
                      }}
                    >
                      <FolderSearch size={13} strokeWidth={2} aria-hidden="true" />
                      {text("Locate folder…", "定位文件夹…")}
                    </button>
                  </div>
                )}

                <div className={styles.tabs}>
                  {(["settings", "sessions", "info"] as Tab[]).map((tk) => (
                    <button
                      key={tk}
                      type="button"
                      onClick={() => setTab(tk)}
                      className={cls(styles.tab, tab === tk && styles.tabActive)}
                    >
                      {tk === "settings"
                        ? text("Settings", "设置")
                        : tk === "sessions"
                          ? `${text("Chats", "会话")} (${selected.session_count})`
                          : text("Info", "信息")}
                    </button>
                  ))}
                </div>

                {tab === "settings" && (
                  <div className={styles.tabBody}>
                    <div className={styles.sectionTitle}>{text("Default Settings", "默认设置")}</div>
                    <ProjectConfigSection projectId={selected.id} />
                    <div className={styles.sectionTitle} style={{ marginTop: 28 }}>
                      {text("Permission Rules", "权限规则")}
                    </div>
                    <PermissionsSection projectId={selected.id} />
                  </div>
                )}

                {tab === "sessions" && (
                  <div className={styles.tabBody}>
                    {sessions.length === 0 ? (
                      <div className={fx.empty}>{text("No chats in this project.", "该项目还没有会话。")}</div>
                    ) : (
                      <ul className={styles.sessionList}>
                        {sessions.map((s) => {
                          const title = s.title || s.id;
                          const initial = title.replace(/^\s+/, "").slice(0, 1).toUpperCase() || "?";
                          return (
                            <li
                              key={s.id}
                              className={styles.sessionRow}
                              onClick={() => pushPath("/s/" + s.id)}
                            >
                              <span className={styles.sessionAvatar}>{initial}</span>
                              <div className={styles.sessionBody}>
                                <div className={styles.sessionTitle}>{title}</div>
                                <div className={styles.sessionMeta}>{s.id.slice(0, 12)}</div>
                              </div>
                              <span className={styles.sessionTime}>
                                {formatRelativeTime(s.created_at ?? 0, locale)}
                              </span>
                            </li>
                          );
                        })}
                      </ul>
                    )}
                  </div>
                )}

                {tab === "info" && (
                  <div className={styles.tabBody}>
                    <Field label={text("Path", "路径")}><code>{selected.path}</code></Field>
                    <Field label={text("Type", "类型")}>
                      {selected.is_default ? text("Default (home)", "默认（家目录）") : text("Custom", "自定义")}
                    </Field>
                    <Field label={text("Status", "状态")}>{selected.status}</Field>
                    <Field label={text("Chats", "会话数")}>{selected.session_count}</Field>
                    <Field label="ID"><code>{selected.id}</code></Field>
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      </div>
  );

  if (embedded) return view;
  return <div className="main">{view}</div>;
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: 16 }}>
      <div style={{
        fontSize: 11, fontWeight: 600, textTransform: "uppercase",
        letterSpacing: "0.04em", color: "var(--text-muted)", marginBottom: 4,
      }}>{label}</div>
      <div style={{ fontSize: 14, color: "var(--text-primary)" }}>{children}</div>
    </div>
  );
}
