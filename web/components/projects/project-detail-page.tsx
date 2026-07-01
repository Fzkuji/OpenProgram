"use client";

/**
 * 项目详情页 /projects/[projectId] — 一个项目的控制台。
 * 顶部返回 + 名字；主区三个 tab：Settings（权限规则 + 项目级默认设置）/
 * Sessions（该项目的会话，点击跳转）/ Info（元数据）。
 */
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import styles from "./project-detail-page.module.css";
import { useTranslation } from "@/lib/i18n";
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

export function ProjectDetailPage({ projectId }: { projectId: string }) {
  const { text } = useTranslation();
  const router = useRouter();
  const [project, setProject] = useState<Project | null>(null);
  const [tab, setTab] = useState<Tab>("settings");
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [notFound, setNotFound] = useState(false);

  useEffect(() => {
    let done = false;
    (async () => {
      for (let i = 0; i < 10 && !done; i++) {
        const d = await wsRequest<{ projects: Project[] }>(
          "list_projects", {}, "projects_list",
        );
        if (d?.projects) {
          const p = d.projects.find((x) => x.id === projectId);
          if (p) { setProject(p); return; }
          setNotFound(true); return;
        }
        await new Promise((r) => setTimeout(r, 300));
      }
    })();
    return () => { done = true; };
  }, [projectId]);

  const loadSessions = useCallback(async () => {
    const d = await wsRequest<{ sessions: SessionSummary[] }>(
      "list_project_sessions", { project_id: projectId }, "project_sessions",
    );
    if (d?.sessions) setSessions(d.sessions);
  }, [projectId]);

  useEffect(() => { if (tab === "sessions") loadSessions(); }, [tab, loadSessions]);

  const relTime = (ts?: number) => {
    if (!ts) return "";
    const d = new Date(ts * 1000);
    return d.toLocaleDateString() + " " + d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  };

  return (
    <div className="main" style={{ minWidth: 0, overflow: "hidden" }}>
      <div className={styles.view}>
        <div className={styles.topbar}>
          <Link href="/projects" className={styles.back}>← {text("Projects", "项目")}</Link>
          <span className={styles.title}>{project?.name || projectId}</span>
          {project?.is_default && (
            <span className={styles.badge}>{text("Default", "默认")}</span>
          )}
        </div>

        {notFound ? (
          <div className={styles.empty}>{text("Project not found.", "项目不存在。")}</div>
        ) : (
          <div className={styles.body}>
            <div className={styles.content}>
              <div className={styles.tabs}>
                {(["settings", "sessions", "info"] as Tab[]).map((tk) => (
                  <button
                    key={tk}
                    type="button"
                    onClick={() => setTab(tk)}
                    className={styles.tab + (tab === tk ? " " + styles.tabActive : "")}
                  >
                    {tk === "settings"
                      ? text("Settings", "设置")
                      : tk === "sessions"
                        ? `${text("Chats", "会话")}${project ? ` (${project.session_count})` : ""}`
                        : text("Info", "信息")}
                  </button>
                ))}
              </div>

              {tab === "settings" && (
                <div className={styles.tabBody}>
                  <div className={styles.sectionTitle}>{text("Default Settings", "默认设置")}</div>
                  <ProjectConfigSection projectId={projectId} />
                  <div className={styles.sectionTitle} style={{ marginTop: 28 }}>
                    {text("Permission Rules", "权限规则")}
                  </div>
                  <PermissionsSection projectId={projectId} />
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
                          <div className={styles.sessionTitle}>{s.title}</div>
                          <div className={styles.sessionMeta}>{relTime(s.created_at)}</div>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              )}

              {tab === "info" && project && (
                <div className={styles.tabBody}>
                  <Field label={text("Path", "路径")}><code>{project.path}</code></Field>
                  <Field label={text("Type", "类型")}>
                    {project.is_default ? text("Default (home)", "默认（家目录）") : text("Custom", "自定义")}
                  </Field>
                  <Field label={text("Status", "状态")}>{project.status}</Field>
                  <Field label={text("Chats", "会话数")}>{project.session_count}</Field>
                  <Field label="ID"><code>{project.id}</code></Field>
                </div>
              )}
            </div>
          </div>
        )}
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
