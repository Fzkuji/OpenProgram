"use client";

/**
 * /projects — 项目管理页。列出所有已注册项目（名/路径/会话数/状态），
 * 支持新建（选文件夹）、移除。项目是"一个工作目录 + 它下面的会话 +
 * 项目级配置（含权限规则）"的载体。数据走全局 WS：list_projects →
 * projects_list。
 */
import { useCallback, useEffect, useMemo, useState } from "react";

import styles from "./projects-page.module.css";
import { useTranslation } from "@/lib/i18n";
import { FoldersIcon } from "@/components/animated-icons";
import { wsRequest } from "@/lib/net/ws-request";

interface Project {
  id: string;
  name: string;
  path: string;
  is_default: boolean;
  session_count: number;
  status: string;
}

export function ProjectsPage() {
  const { t, text } = useTranslation();
  const [projects, setProjects] = useState<Project[]>([]);
  const [query, setQuery] = useState("");
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    // 全局 WS 可能还没连上，重试几次。
    for (let i = 0; i < 10; i++) {
      const data = await wsRequest<{ projects: Project[] }>(
        "list_projects", {}, "projects_list",
      );
      if (data) { setProjects(data.projects || []); return; }
      await new Promise((r) => setTimeout(r, 300));
    }
  }, []);

  useEffect(() => {
    function onChanged() { refresh(); }
    window.addEventListener("project-changed", onChanged);
    refresh();
    return () => window.removeEventListener("project-changed", onChanged);
  }, [refresh]);

  const addProject = useCallback(async () => {
    setError(null);
    try {
      const r = await fetch("/api/pick-folder");
      const j = await r.json();
      const path = j?.path;
      if (!path) return; // 用户取消
      await wsRequest("create_project", { path }, "project_created");
      refresh();
    } catch (e) {
      setError(String(e));
    }
  }, [refresh]);

  const removeProject = useCallback(async (id: string) => {
    await wsRequest("remove_project", { project_id: id }, "project_removed");
    refresh();
  }, [refresh]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    const list = q
      ? projects.filter(
          (p) => p.name.toLowerCase().includes(q) || p.path.toLowerCase().includes(q),
        )
      : projects;
    // 默认项目排最前，其余按名字。
    return [...list].sort((a, b) => {
      if (a.is_default !== b.is_default) return a.is_default ? -1 : 1;
      return a.name.localeCompare(b.name);
    });
  }, [projects, query]);

  return (
    <div className="main" style={{ minWidth: 0, overflow: "hidden" }}>
      <div className={styles.view}>
        <div className={styles.topbar}>
          <span className={styles.title}>{t("nav.projects")}</span>
          <div className={styles.toolbar}>
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={text("Search projects…", "搜索项目…")}
              className={styles.search}
            />
            <button type="button" onClick={addProject} className={styles.addBtn}>
              {text("Open folder…", "打开文件夹…")}
            </button>
          </div>
        </div>

        {error && <div className={styles.errorBar}>{error}</div>}

        <div className={styles.content}>
          {filtered.length === 0 ? (
            <div className={styles.empty}>
              {text("No projects yet.", "还没有项目。")}
            </div>
          ) : (
            <ul className={styles.list}>
              {filtered.map((p) => (
                <li key={p.id} className={styles.card}>
                  <span className={styles.cardIcon}><FoldersIcon size={22} /></span>
                  <div className={styles.cardMain}>
                    <div className={styles.cardName}>
                      {p.name}
                      {p.is_default && (
                        <span className={styles.badge}>{text("Default", "默认")}</span>
                      )}
                    </div>
                    <div className={styles.cardPath}>{p.path}</div>
                  </div>
                  <div className={styles.cardMeta}>
                    {p.session_count} {text("chats", "会话")}
                  </div>
                  {!p.is_default && (
                    <button
                      type="button"
                      onClick={() => removeProject(p.id)}
                      className={styles.removeBtn}
                      aria-label={text("Remove", "移除")}
                      title={text("Remove from list (files kept)", "从列表移除（不删文件）")}
                    >×</button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}
