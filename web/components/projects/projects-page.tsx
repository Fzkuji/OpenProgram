"use client";

/**
 * /projects — 项目管理。左右分栏（照 Functions 页）：左栏项目列表，
 * 右栏选中项目的内容（Settings 权限规则+项目默认设置 / Chats 会话列表 /
 * Info 元数据）。同页切换，不跳路由。
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";

import styles from "./projects-page.module.css";
import { useTranslation } from "@/lib/i18n";
import { FoldersIcon, FolderPlusIcon } from "@/components/animated-icons";
import { wsRequest } from "@/lib/net/ws-request";
import { PermissionsSection } from "./permissions-section";
import { ProjectConfigSection } from "./project-config-section";

interface Project {
  id: string;
  name: string;
  path: string;
  is_default: boolean;
  session_count: number;
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

export function ProjectsPage() {
  const { text } = useTranslation();
  const router = useRouter();
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("settings");
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [error, setError] = useState<string | null>(null);

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

  const selected = useMemo(
    () => projects.find((p) => p.id === selectedId) || null,
    [projects, selectedId],
  );

  const loadSessions = useCallback(async (pid: string) => {
    const d = await wsRequest<{ sessions: SessionSummary[] }>(
      "list_project_sessions", { project_id: pid }, "project_sessions",
    );
    setSessions(d?.sessions || []);
  }, []);

  useEffect(() => {
    if (tab === "sessions" && selectedId) loadSessions(selectedId);
  }, [tab, selectedId, loadSessions]);

  const addProject = useCallback(async () => {
    setError(null);
    try {
      const r = await fetch("/api/pick-folder");
      const j = await r.json();
      if (!j?.path) return;
      const d = await wsRequest<{ ok: boolean; project?: Project; error?: string }>(
        "create_project", { path: j.path }, "project_created",
      );
      if (d?.error) setError(d.error);
      if (d?.project) setSelectedId(d.project.id);
      refresh();
    } catch (e) {
      setError(String(e));
    }
  }, [refresh]);

  const removeProject = useCallback(async (id: string) => {
    await wsRequest("remove_project", { project_id: id }, "project_removed");
    setSelectedId((cur) => (cur === id ? null : cur));
    refresh();
  }, [refresh]);

  const relTime = (ts?: number) => {
    if (!ts) return "";
    const d = new Date(ts * 1000);
    return d.toLocaleDateString() + " " +
      d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  };

  return (
    <div className="main">
      <div className={styles.view}>
        <div className={styles.topbar}>
          <span className={styles.title}>{text("Projects", "项目")}</span>
        </div>

        {error && <div className={styles.errorBar}>{error}</div>}

        <div className={styles.body}>
          {/* 左栏：项目列表 */}
          <div className={styles.nav}>
            {projects.map((p) => (
              <div
                key={p.id}
                className={cls(styles.navItem, p.id === selectedId && styles.active)}
                onClick={() => setSelectedId(p.id)}
              >
                <span className={styles.navIcon}><FoldersIcon size={16} /></span>
                <span className={styles.navName}>{p.name}</span>
                {p.is_default && <span className={styles.badge}>{text("Default", "默认")}</span>}
              </div>
            ))}
            <div className={styles.navSep} />
            <div
              className={cls(styles.navItem, styles.navNew)}
              onClick={addProject}
            >
              <span className={styles.navIcon}><FolderPlusIcon size={16} /></span>
              <span className={styles.navName}>{text("Open folder…", "打开文件夹…")}</span>
            </div>
          </div>

          {/* 右栏：选中项目的内容 */}
          <div className={styles.content}>
            {!selected ? (
              <div className={styles.empty}>{text("Select a project.", "选择一个项目。")}</div>
            ) : (
              <>
                <div className={styles.detailHead}>
                  <span className={styles.detailTitle}>{selected.name}</span>
                  <span className={styles.detailPath}>{selected.path}</span>
                  {!selected.is_default && (
                    <button
                      type="button"
                      onClick={() => removeProject(selected.id)}
                      className={styles.removeBtn}
                      title={text("Remove from list (files kept)", "从列表移除（不删文件）")}
                    >{text("Remove", "移除")}</button>
                  )}
                </div>

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
                      <div className={styles.empty}>{text("No chats in this project.", "该项目还没有会话。")}</div>
                    ) : (
                      <ul className={styles.sessionList}>
                        {sessions.map((s) => (
                          <li
                            key={s.id}
                            className={styles.sessionRow}
                            onClick={() => router.push("/s/" + s.id)}
                          >
                            <span className={styles.sessionTitle}>{s.title}</span>
                            <span className={styles.sessionMeta}>{relTime(s.created_at)}</span>
                          </li>
                        ))}
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
    </div>
  );
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
