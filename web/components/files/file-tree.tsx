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
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ChevronRight,
  File,
  FileCode,
  FileImage,
  FileJson,
  FilePlus,
  FileText,
  Folder,
  FolderPlus,
  RotateCw,
} from "lucide-react";

import { useTranslation } from "@/lib/i18n";
import {
  filesWsRequest,
  latestFileMtime,
  type Project,
} from "@/lib/state/files-shared";
import { useCenterTabs } from "@/lib/state/center-tabs-store";
import { wsRequest } from "@/lib/net/ws-request";
import { useSessionStore } from "@/lib/session-store";
import {
  Popover,
  PopoverAnchor,
  PopoverContent,
} from "@/components/ui/popover";
import { ConfirmDialog } from "@/components/sidebar/sessions-list/confirm-dialog";
import { TreeContextMenu, treeClipboard } from "./tree-context-menu";
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

function parentOf(path: string): string {
  const i = path.lastIndexOf("/");
  return i > 0 ? path.slice(0, i) : "";
}

function baseOf(path: string): string {
  return path.split("/").pop() || path;
}

/** Absolute project root per project id — fetched once via
 *  ``list_projects`` (the tree itself only knows the project id). */
const projectPathCache = new Map<string, string>();

async function projectAbsPath(projectId: string): Promise<string | null> {
  const hit = projectPathCache.get(projectId);
  if (hit) return hit;
  const data = await wsRequest<{ projects: Project[] }>(
    "list_projects",
    { session_id: useSessionStore.getState().currentSessionId ?? "" },
    "projects_list",
  );
  const p = data?.projects?.find((x) => x.id === projectId);
  if (!p?.path) return null;
  projectPathCache.set(projectId, p.path);
  return p.path;
}

/** navigator.clipboard with the textarea+execCommand fallback used
 *  elsewhere in the repo (see settings/providers/setup-hint.tsx).
 *  The write is awaited so a rejected promise (permissions, focus
 *  loss) also falls back instead of silently copying nothing. */
async function copyText(value: string): Promise<void> {
  try {
    if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(value);
      return;
    }
  } catch {
    /* fall through to the textarea path */
  }
  const ta = document.createElement("textarea");
  ta.value = value;
  ta.style.position = "fixed";
  ta.style.opacity = "0";
  document.body.appendChild(ta);
  ta.select();
  document.execCommand("copy");
  document.body.removeChild(ta);
}

/* VS Code-ish geometry: 12px per depth level; folder rows lead with a
   14px chevron + 4px gap, so file rows (no chevron) indent 18px extra
   to align their icon with the folder glyph. */
const INDENT = 12;
const ROW_PAD = 4;
const FILE_PAD = ROW_PAD + 18;

/** Extension bucket → icon + colour (existing accent tokens only). */
const ICON_BUCKETS: [Set<string>, typeof File, string | undefined][] = [
  [
    new Set(["ts", "tsx", "js", "jsx", "mjs", "cjs", "py", "rs", "go", "c", "cpp", "h", "hpp", "java", "sh"]),
    FileCode,
    "var(--accent-cyan)",
  ],
  [new Set(["json", "yaml", "yml", "toml", "csv"]), FileJson, "var(--accent-yellow)"],
  [new Set(["md", "markdown", "txt", "rst", "log"]), FileText, undefined],
  [new Set(["png", "jpg", "jpeg", "gif", "svg", "webp", "ico"]), FileImage, "var(--accent-purple)"],
  [new Set(["pdf"]), FileText, "var(--accent-red)"],
];

function FileGlyph({ name }: { name: string }) {
  const dot = name.lastIndexOf(".");
  const ext = dot > 0 ? name.slice(dot + 1).toLowerCase() : "";
  for (const [exts, Icon, color] of ICON_BUCKETS) {
    if (exts.has(ext)) {
      return (
        <Icon size={14} className={styles.treeIcon} style={color ? { color } : undefined} />
      );
    }
  }
  return <File size={14} className={styles.treeIcon} />;
}

/** Editable row label for inline create / rename (VS Code style):
 *  Enter commits, Escape or blur cancels. Name validation (non-empty,
 *  no "/") happens here — an invalid Enter just keeps the input open. */
function InlineNameInput({
  initial,
  onCommit,
  onCancel,
}: {
  initial: string;
  onCommit: (name: string) => void;
  onCancel: () => void;
}) {
  const [value, setValue] = useState(initial);
  const ref = useRef<HTMLInputElement>(null);
  // Guards the Enter-then-blur double fire: committing unmounts the
  // input, which fires a native blur that must not also cancel.
  const done = useRef(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.focus();
    const dot = initial.lastIndexOf(".");
    el.setSelectionRange(0, dot > 0 ? dot : initial.length);
  }, [initial]);

  const finish = (fn: () => void) => {
    if (done.current) return;
    done.current = true;
    fn();
  };

  return (
    <input
      ref={ref}
      className={styles.treeInput}
      value={value}
      spellCheck={false}
      onChange={(e) => setValue(e.target.value)}
      onClick={(e) => e.stopPropagation()}
      onBlur={() => finish(onCancel)}
      onKeyDown={(e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          const v = value.trim();
          if (!v) return finish(onCancel);
          if (v.includes("/")) return;
          finish(() => onCommit(v));
        } else if (e.key === "Escape") {
          e.preventDefault();
          finish(onCancel);
        }
      }}
    />
  );
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
  const openFile = (path: string) => {
    openFileTab(projectId, path);
    (window as Window & { __navigate?: (route: string) => void })
      .__navigate?.("/chat");
  };
  const [dirs, setDirs] = useState<Record<string, DirState>>({});
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [filter, setFilter] = useState("");
  // File-management state: selected row (targets the header's New
  // File/Folder), inline create/rename editors, context menu, delete
  // confirm. Selection is UI-only — clicking still opens files.
  const [selected, setSelected] = useState<{ path: string; type: "file" | "dir" } | null>(null);
  const [creating, setCreating] = useState<{ dir: string; kind: "file" | "dir" } | null>(null);
  const [renaming, setRenaming] = useState<string | null>(null);
  const [menu, setMenu] = useState<{ x: number; y: number; path: string; type: "file" | "dir" } | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);

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
    // 组件跨项目复用（右栏不带 key 渲染）：上一个项目的选中行、
    // 内联新建/重命名、筛选词都指向旧根目录下的相对路径，留着会
    // 误指到新项目里同名路径上，切根时一并清掉。
    setSelected(null);
    setCreating(null);
    setRenaming(null);
    setMenu(null);
    setFilter("");
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

  /* ---- file management ops ---------------------------------------- */

  /** Run one worker file op; on success re-list the affected dirs and
   *  broadcast ``project-files-changed`` (reveal mutates nothing, so it
   *  passes no dirs and skips the event). */
  async function fileOp(
    op: "create" | "rename" | "copy" | "delete" | "reveal",
    payload: Record<string, unknown>,
    refreshDirs: string[],
  ): Promise<boolean> {
    const data = await filesWsRequest<{ ok?: boolean; error?: string }>(
      `project_file_${op}`,
      { project_id: projectId, ...payload },
      `project_file_${op}_result`,
    );
    if (!data || data.error) {
      if (data?.error) window.alert(data.error);
      return false;
    }
    for (const d of new Set(refreshDirs)) load(d);
    if (op !== "reveal") window.dispatchEvent(new Event("project-files-changed"));
    return true;
  }

  /** Expand + lazily load every dir along the "/"-chain ending at
   *  `dir` ("" = root, always rendered). Called before showing an
   *  inline editor: when the action starts from a filter-mode row,
   *  the target's ancestors may never have been expanded, and a
   *  collapsed ancestor would leave the editor invisible. */
  function expandChain(dir: string) {
    if (!dir) return;
    const chain: string[] = [];
    let acc = "";
    for (const seg of dir.split("/")) {
      acc = acc ? `${acc}/${seg}` : seg;
      chain.push(acc);
    }
    setExpanded((prev) => {
      const next = new Set(prev);
      for (const d of chain) next.add(d);
      return next;
    });
    for (const d of chain) if (dirs[d] === undefined) load(d);
  }

  /** Directory a create targets: selected dir → itself, selected file
   *  → its parent, nothing selected → project root. */
  function startCreate(kind: "file" | "dir", dir?: string) {
    const target =
      dir ?? (selected ? (selected.type === "dir" ? selected.path : parentOf(selected.path)) : "");
    setFilter(""); // the editable row only renders in tree mode
    expandChain(target);
    setCreating({ dir: target, kind });
  }

  async function commitCreate(name: string) {
    if (!creating) return;
    const { dir, kind } = creating;
    setCreating(null);
    const full = joinPath(dir, name);
    const ok = await fileOp("create", { path: full, kind }, [dir]);
    if (ok && kind === "file") openFile(full);
  }

  /** After a rename/move, any open center file tab at the old path —
   *  or under it when a directory moved — follows to the new path. */
  function retargetOpenTabs(oldPath: string, newPath: string) {
    const s = useCenterTabs.getState();
    for (const t of [...s.tabs]) {
      if (t.kind !== "file" || t.projectId !== projectId || !t.path) continue;
      if (t.path === oldPath) {
        s.retargetFileTab(t.id, projectId, newPath);
      } else if (t.path.startsWith(oldPath + "/")) {
        s.retargetFileTab(t.id, projectId, newPath + t.path.slice(oldPath.length));
      }
    }
  }

  async function commitRename(oldPath: string, name: string) {
    setRenaming(null);
    if (name === baseOf(oldPath)) return;
    const dir = parentOf(oldPath);
    const newPath = joinPath(dir, name);
    const ok = await fileOp("rename", { path: oldPath, new_path: newPath }, [dir]);
    if (ok) retargetOpenTabs(oldPath, newPath);
  }

  async function copyPathTo(rel: string, absolute: boolean) {
    if (!absolute) return copyText(rel);
    const root = await projectAbsPath(projectId);
    if (root) copyText(`${root}/${rel}`);
  }

  /** Paste destination: the right-clicked dir itself, or the parent
   *  dir of a right-clicked file — always `<target>/<basename(src)>`. */
  async function pasteInto(targetDir: string) {
    const clip = treeClipboard.current;
    if (!clip) return;
    const dest = joinPath(targetDir, baseOf(clip.path));
    if (dest === clip.path) return;
    if (clip.op === "cut") {
      const ok = await fileOp(
        "rename",
        { path: clip.path, new_path: dest },
        [parentOf(clip.path), targetDir],
      );
      if (ok) {
        treeClipboard.current = null;
        retargetOpenTabs(clip.path, dest);
      }
    } else {
      await fileOp("copy", { path: clip.path, new_path: dest }, [targetDir]);
    }
  }

  async function doDelete(path: string) {
    const ok = await fileOp("delete", { path }, [parentOf(path)]);
    if (!ok) return;
    // Close any center tab now pointing at a deleted file (the path
    // itself, or anything under a deleted dir).
    const s = useCenterTabs.getState();
    for (const t of [...s.tabs]) {
      if (
        t.kind === "file" &&
        t.projectId === projectId &&
        t.path &&
        (t.path === path || t.path.startsWith(path + "/"))
      ) {
        s.closeTab(t.id);
      }
    }
  }

  function onRowContextMenu(
    e: React.MouseEvent,
    path: string,
    type: "file" | "dir",
  ) {
    e.preventDefault();
    e.stopPropagation();
    setSelected({ path, type });
    setMenu({ x: e.clientX, y: e.clientY, path, type });
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
    const hintPad = FILE_PAD + depth * INDENT;
    // Editable "new entry" row, rendered at the top of the target dir.
    const createRow =
      creating && creating.dir === dir ? (
        <div
          className={styles.treeRow}
          style={{
            paddingLeft:
              (creating.kind === "dir" ? ROW_PAD : FILE_PAD) + depth * INDENT,
          }}
        >
          {creating.kind === "dir" ? (
            <>
              <ChevronRight size={14} className={styles.chevron} />
              <Folder size={14} className={styles.treeIcon} />
            </>
          ) : (
            <File size={14} className={styles.treeIcon} />
          )}
          <InlineNameInput
            initial=""
            onCommit={commitCreate}
            onCancel={() => setCreating(null)}
          />
        </div>
      ) : null;
    if (state === "loading" || state === undefined) {
      return (
        <div className={styles.treeHint} style={{ paddingLeft: hintPad }}>
          {text("Loading…", "加载中…")}
        </div>
      );
    }
    if (state === "error") {
      return (
        <div className={styles.treeHint} style={{ paddingLeft: hintPad }}>
          {text("Failed to load", "加载失败")}
        </div>
      );
    }
    if (state.length === 0 && !createRow) {
      return (
        <div className={styles.treeHint} style={{ paddingLeft: hintPad }}>
          {text("Empty", "空目录")}
        </div>
      );
    }
    const rows = state.map((e) => {
      const full = joinPath(dir, e.name);
      const selectedCls = selected?.path === full ? styles.treeRowSelected : "";
      if (e.type === "dir") {
        const isOpen = expanded.has(full);
        return (
          <div key={full}>
            <div
              className={`${styles.treeRow} ${DIM_DIRS.has(e.name) ? styles.treeRowDim : ""} ${selectedCls}`}
              style={{ paddingLeft: ROW_PAD + depth * INDENT }}
              onClick={() => {
                setSelected({ path: full, type: "dir" });
                toggleDir(full);
              }}
              onContextMenu={(ev) => onRowContextMenu(ev, full, "dir")}
              title={full}
            >
              <ChevronRight
                size={14}
                className={`${styles.chevron} ${isOpen ? styles.chevronOpen : ""}`}
              />
              <Folder size={14} className={styles.treeIcon} />
              {renaming === full ? (
                <InlineNameInput
                  initial={e.name}
                  onCommit={(v) => commitRename(full, v)}
                  onCancel={() => setRenaming(null)}
                />
              ) : (
                <span className={styles.treeName}>{e.name}</span>
              )}
            </div>
            {isOpen ? (
              // Indent guide under this folder's chevron center; the
              // kids container is full-width so row hover still spans
              // the whole panel (guide is an ::before in the CSS).
              <div
                className={styles.treeKids}
                style={{ "--guide-x": `${ROW_PAD + 7 + depth * INDENT}px` } as React.CSSProperties}
              >
                {renderDir(full, depth + 1)}
              </div>
            ) : null}
          </div>
        );
      }
      return (
        <div
          key={full}
          className={`${styles.treeRow} ${full === activePath ? styles.treeRowActive : ""} ${selectedCls}`}
          style={{ paddingLeft: FILE_PAD + depth * INDENT }}
          onClick={() => {
            setSelected({ path: full, type: "file" });
            openFile(full);
          }}
          onContextMenu={(ev) => onRowContextMenu(ev, full, "file")}
          title={full}
        >
          <FileGlyph name={e.name} />
          {renaming === full ? (
            <InlineNameInput
              initial={e.name}
              onCommit={(v) => commitRename(full, v)}
              onCancel={() => setRenaming(null)}
            />
          ) : (
            <span className={styles.treeName}>{e.name}</span>
          )}
        </div>
      );
    });
    return (
      <>
        {createRow}
        {rows}
      </>
    );
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
          onClick={() => startCreate("file")}
          title={text("New File", "新建文件")}
        >
          <FilePlus size={13} />
        </button>
        <button
          type="button"
          className={styles.iconBtn}
          onClick={() => startCreate("dir")}
          title={text("New Folder", "新建文件夹")}
        >
          <FolderPlus size={13} />
        </button>
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
                    onContextMenu={(ev) => onRowContextMenu(ev, path, "dir")}
                    title={path}
                  >
                    <ChevronRight size={14} className={styles.chevron} />
                    <Folder size={14} className={styles.treeIcon} />
                    <span className={styles.treePath}>{path}</span>
                  </div>
                ) : (
                  <div
                    key={path}
                    className={`${styles.treeRow} ${path === activePath ? styles.treeRowActive : ""}`}
                    onClick={() => openFile(path)}
                    onContextMenu={(ev) => onRowContextMenu(ev, path, "file")}
                    title={path}
                  >
                    <FileGlyph name={path} />
                    <span className={styles.treePath}>{path}</span>
                  </div>
                ),
              )
          : renderDir("", 0)}
      </div>

      {/* Right-click context menu — same Popover/MENU_PANEL pattern as
          the Recents ConvMenu, anchored to the pointer via a fixed
          zero-size span. */}
      {menu ? (
        <Popover
          open
          onOpenChange={(o) => {
            if (!o) setMenu(null);
          }}
        >
          <PopoverAnchor asChild>
            <span
              style={{ position: "fixed", left: menu.x, top: menu.y, width: 0, height: 0 }}
            />
          </PopoverAnchor>
          <PopoverContent
            align="start"
            side="bottom"
            sideOffset={2}
            className="w-auto border-0 bg-transparent p-0 text-[var(--text-primary)] shadow-none"
          >
            <TreeContextMenu
              canPaste={!!treeClipboard.current}
              onReveal={() => fileOp("reveal", { path: menu.path }, [])}
              onNewFile={() =>
                startCreate("file", menu.type === "dir" ? menu.path : parentOf(menu.path))
              }
              onNewFolder={() =>
                startCreate("dir", menu.type === "dir" ? menu.path : parentOf(menu.path))
              }
              onCopyPath={() => copyPathTo(menu.path, true)}
              onCopyRelativePath={() => copyPathTo(menu.path, false)}
              onCut={() => {
                treeClipboard.current = { op: "cut", path: menu.path };
              }}
              onCopy={() => {
                treeClipboard.current = { op: "copy", path: menu.path };
              }}
              onPaste={() =>
                pasteInto(menu.type === "dir" ? menu.path : parentOf(menu.path))
              }
              onRename={() => {
                setFilter(""); // inline editor only renders in tree mode
                expandChain(parentOf(menu.path)); // row must be visible
                setRenaming(menu.path);
              }}
              onDelete={() => setConfirmDelete(menu.path)}
              onClose={() => setMenu(null)}
            />
          </PopoverContent>
        </Popover>
      ) : null}

      {confirmDelete ? (
        <ConfirmDialog
          title={text(
            `Delete "${baseOf(confirmDelete)}"?`,
            `删除“${baseOf(confirmDelete)}”？`,
          )}
          message={text(
            "It will be permanently removed from disk.",
            "将从磁盘中永久删除。",
          )}
          onConfirm={() => {
            const path = confirmDelete;
            setConfirmDelete(null);
            doDelete(path);
          }}
          onCancel={() => setConfirmDelete(null)}
        />
      ) : null}
    </div>
  );
}
