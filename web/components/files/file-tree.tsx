"use client";

/**
 * FileTree — the right sidebar's resident content: a lazy directory
 * tree over the active tab's project. Clicking a file opens (or
 * focuses) its center file tab.
 *
 * Lazily loads one directory listing per expand via the worker's
 * ``project_file_tree`` action (root "" on mount). The filter input
 * does a client-side substring match over already-loaded nodes only —
 * it never triggers fetches; matches render as a flat path list.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { ChevronRight, RotateCw } from "lucide-react";

import { useTranslation } from "@/lib/i18n";
import { filesWsRequest, latestFileMtime } from "@/lib/state/files-shared";
import { useCenterTabs } from "@/lib/state/center-tabs-store";
import styles from "./files-panel.module.css";

export interface TreeEntry {
  name: string;
  type: "file" | "dir";
  size: number;
  mtime: number;
}

interface TreeResult {
  project_id: string;
  path: string;
  entries?: TreeEntry[];
  error?: string;
}

/** Dirs rendered dimmed (still expandable — just visually de-emphasised). */
const DIM_DIRS = new Set([".git", "node_modules", ".venv", "__pycache__"]);

function joinPath(dir: string, name: string): string {
  return dir ? `${dir}/${name}` : name;
}

type DirState = TreeEntry[] | "loading" | "error";

export function FileTree({
  projectId,
  headerExtra,
}: {
  projectId: string;
  /** Slot rendered before the filter input (the right sidebar puts
   *  its collapse toggle here so header stays a single row). */
  headerExtra?: React.ReactNode;
}) {
  const { text } = useTranslation();
  const openFileTab = useCenterTabs((s) => s.openFileTab);
  // Highlight the file whose center tab is active (primitive selector,
  // so recomputing per store change is re-render-safe).
  const activePath = useCenterTabs((s) => {
    const t = s.tabs.find((x) => x.id === s.activeId);
    return t?.kind === "file" && t.projectId === projectId
      ? (t.path ?? null)
      : null;
  });
  const openFile = (path: string) => openFileTab(projectId, path);
  const [dirs, setDirs] = useState<Record<string, DirState>>({});
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [filter, setFilter] = useState("");

  const load = useCallback(
    async (path: string) => {
      setDirs((d) => ({ ...d, [path]: "loading" }));
      const data = await filesWsRequest<TreeResult>(
        "project_file_tree",
        { project_id: projectId, path },
        "project_file_tree_result",
      );
      if (!data || data.error || data.path !== path || !data.entries) {
        setDirs((d) => ({ ...d, [path]: "error" }));
        return;
      }
      for (const e of data.entries) {
        if (e.type === "file") latestFileMtime.set(joinPath(path, e.name), e.mtime);
      }
      const entries = data.entries;
      setDirs((d) => ({ ...d, [path]: entries }));
    },
    [projectId],
  );

  // (Re)load the root whenever the project changes.
  useEffect(() => {
    setDirs({});
    setExpanded(new Set());
    load("");
  }, [load]);

  function toggleDir(path: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
    if (dirs[path] === undefined) load(path);
  }

  function refetchRoot() {
    setDirs({});
    setExpanded(new Set());
    load("");
  }

  // Filter mode: flat list of every already-loaded node whose full
  // relative path contains the query (case-insensitive).
  const filtered = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return null;
    const out: { path: string; entry: TreeEntry }[] = [];
    for (const [dir, state] of Object.entries(dirs)) {
      if (!Array.isArray(state)) continue;
      for (const e of state) {
        const full = joinPath(dir, e.name);
        if (full.toLowerCase().includes(q)) out.push({ path: full, entry: e });
      }
    }
    out.sort((a, b) => a.path.localeCompare(b.path));
    return out;
  }, [dirs, filter]);

  function renderDir(dir: string, depth: number): React.ReactNode {
    const state = dirs[dir];
    if (state === "loading" || state === undefined) {
      return (
        <div className={styles.treeHint} style={{ paddingLeft: 14 + depth * 14 }}>
          {text("Loading…", "加载中…")}
        </div>
      );
    }
    if (state === "error") {
      return (
        <div className={styles.treeHint} style={{ paddingLeft: 14 + depth * 14 }}>
          {text("Failed to load", "加载失败")}
        </div>
      );
    }
    if (state.length === 0) {
      return (
        <div className={styles.treeHint} style={{ paddingLeft: 14 + depth * 14 }}>
          {text("Empty", "空目录")}
        </div>
      );
    }
    return state.map((e) => {
      const full = joinPath(dir, e.name);
      if (e.type === "dir") {
        const isOpen = expanded.has(full);
        return (
          <div key={full}>
            <div
              className={`${styles.treeRow} ${DIM_DIRS.has(e.name) ? styles.treeRowDim : ""}`}
              style={{ paddingLeft: depth * 14 }}
              onClick={() => toggleDir(full)}
              title={full}
            >
              <ChevronRight
                size={13}
                className={`${styles.chevron} ${isOpen ? styles.chevronOpen : ""}`}
              />
              <span className={styles.treeName}>{e.name}</span>
            </div>
            {isOpen ? renderDir(full, depth + 1) : null}
          </div>
        );
      }
      return (
        <div
          key={full}
          className={`${styles.treeRow} ${full === activePath ? styles.treeRowActive : ""}`}
          style={{ paddingLeft: 13 + depth * 14 }}
          onClick={() => openFile(full)}
          title={full}
        >
          <span className={styles.treeName}>{e.name}</span>
        </div>
      );
    });
  }

  return (
    <div className={styles.treeCol}>
      <div className={styles.treeHeader}>
        {headerExtra}
        <input
          className={styles.treeFilter}
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder={text("Filter files…", "筛选文件…")}
          spellCheck={false}
        />
        <button
          type="button"
          className={styles.iconBtn}
          onClick={refetchRoot}
          title={text("Refresh", "刷新")}
        >
          <RotateCw size={13} />
        </button>
      </div>
      <div className={styles.treeBody}>
        {filtered
          ? filtered.length === 0
            ? <div className={styles.treeHint}>{text("No matches", "无匹配")}</div>
            : filtered.map(({ path, entry }) =>
                entry.type === "dir" ? (
                  <div
                    key={path}
                    className={`${styles.treeRow} ${styles.treeRowDim}`}
                    onClick={() => toggleDir(path)}
                    title={path}
                  >
                    <ChevronRight size={13} className={styles.chevron} />
                    <span className={styles.treePath}>{path}</span>
                  </div>
                ) : (
                  <div
                    key={path}
                    className={`${styles.treeRow} ${path === activePath ? styles.treeRowActive : ""}`}
                    onClick={() => openFile(path)}
                    title={path}
                  >
                    <span className={styles.treePath}>{path}</span>
                  </div>
                ),
              )
          : renderDir("", 0)}
      </div>
    </div>
  );
}
