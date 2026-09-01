"use client";

/**
 * FileTree — the right sidebar's resident content: a lazy directory
 * tree over the active tab's project. Clicking a file opens (or
 * focuses) its center file tab.
 *
 * Lazily loads one directory listing per expand via the worker's
 * ``project_file_tree`` action (root "" on mount). Filter search is served
 * by the worker; highlight mode preserves the real tree hierarchy.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  File,
  FileCode,
  FileImage,
  FileJson,
  FilePlus,
  FileText,
  Folder,
  FolderOpen,
  FolderPlus,
  RotateCw,
} from "lucide-react";

import { useTranslation } from "@/lib/i18n";
import {
  clearFileDraftsForPath,
  filesWsRequest,
  hasDirtyDraftsForPath,
  noteFileMtime,
  runAfterServerFileOperation,
  runServerRenameWithDrafts,
  type Project,
} from "@/lib/state/files-shared";
import { useCenterTabs } from "@/lib/state/center-tabs-store";
import {
  idempotencyKeyFor,
  MutationRegistryCapacityError,
  wsMutationRequest,
  wsRequest,
} from "@/lib/net/ws-request";
import { navigate } from "@/lib/navigate";
import { useSessionStore } from "@/lib/session-store";
import {
  Popover,
  PopoverAnchor,
  PopoverContent,
} from "@/components/ui/popover";
import { ConfirmDialog } from "@/components/sidebar/sessions-list/confirm-dialog";
import { TreeContextMenu, treeClipboard } from "./tree-context-menu";
import {
  ExplorerHeader,
  ExplorerMatchText,
  copyText,
  type ExplorerSearchMode,
} from "./explorer-header";
import {
  EXPLORER_BASE_PAD,
  EXPLORER_INDENT,
  matchingIndexes,
  visibleSearchPaths,
} from "./explorer-search";
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
  snapshot_id?: string | null;
  next_cursor?: string | null;
  error_code?: string | null;
  error?: string;
}

interface SearchResult extends TreeEntry {
  path: string;
  project_id?: string;
  project_name?: string;
}

interface DirectoryPage {
  snapshotId: string | null;
  nextCursor: string | null;
}

interface SearchResultPayload {
  project_id: string;
  path: string;
  results?: SearchResult[];
  snapshot_id?: string | null;
  next_cursor?: string | null;
  error_code?: string | null;
  error?: string;
}

/** Dirs rendered dimmed (still expandable — just visually de-emphasised). */
const DIM_DIRS = new Set([".git", "node_modules", ".venv", "__pycache__"]);
const MAX_SEARCH_RESULTS = 500;

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
  const sessionId = useSessionStore.getState().currentSessionId ?? "";
  const data = await wsRequest<{ projects: Project[]; session_id?: string | null }>(
    "list_projects",
    { session_id: sessionId },
    "projects_list",
    (d) => (d.session_id ?? null) === (sessionId || null),
  );
  const p = data?.projects?.find((x) => x.id === projectId);
  if (!p?.path) return null;
  projectPathCache.set(projectId, p.path);
  return p.path;
}

/* The 14px glyph is centered in a 16px slot. Advancing 27px makes the
   child's visible glyph edge meet its parent label's start exactly. */
const INDENT = EXPLORER_INDENT;
const TREE_BASE_PAD = EXPLORER_BASE_PAD;
const TREE_LABEL_OFFSET = 44;

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
        <Icon size={15} className={styles.treeIcon} style={color ? { color } : undefined} />
      );
    }
  }
  return <File size={15} className={styles.treeIcon} />;
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
    navigate("/chat");
  };
  const [dirs, setDirs] = useState<Record<string, DirState>>({});
  const [directoryPages, setDirectoryPages] = useState<Record<string, DirectoryPage>>({});
  const [loadingMore, setLoadingMore] = useState<Set<string>>(new Set());
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [filter, setFilter] = useState("");
  const [searchResults, setSearchResults] = useState<SearchResult[]>([]);
  const [searchLoading, setSearchLoading] = useState(false);
  const [searchPage, setSearchPage] = useState(1);
  const [searchHasMore, setSearchHasMore] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchMode, setSearchMode] = useState<ExplorerSearchMode>("filter");
  const [fuzzySearch, setFuzzySearch] = useState(true);
  const [searchMatchIndex, setSearchMatchIndex] = useState(0);
  const [projectRoot, setProjectRoot] = useState<string | null>(null);
  // File-management state: selected row (targets the header's New
  // File/Folder), inline create/rename editors, context menu, delete
  // confirm. Selection is UI-only — clicking still opens files.
  const [selected, setSelected] = useState<{ path: string; type: "file" | "dir" } | null>(null);
  const [creating, setCreating] = useState<{ dir: string; kind: "file" | "dir" } | null>(null);
  const [renaming, setRenaming] = useState<string | null>(null);
  const [menu, setMenu] = useState<{ x: number; y: number; path: string; type: "file" | "dir" } | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const directoryPagesRef = useRef<Record<string, DirectoryPage>>({});
  const queryControllers = useRef(new Set<AbortController>());
  const queryGeneration = useRef(0);
  const searchGeneration = useRef(0);
  const searchCursor = useRef<string | null>(null);
  const searchSnapshot = useRef<string | null>(null);
  const searchQuery = useRef("");
  const searchModeRef = useRef<"fuzzy" | "contains">("contains");
  const searchRows = useRef(new Map<string, SearchResult>());
  const searchLoadingRef = useRef(false);
  const searchLoadingGeneration = useRef<number | null>(null);
  const revealTarget = useRef<string | null>(null);
  const revealScrollTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const revealFlashTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    directoryPagesRef.current = directoryPages;
  }, [directoryPages]);

  function fileQuery<T>(
    action: string,
    payload: Record<string, unknown>,
    responseType: string,
    canRun: () => boolean,
    outerSignal?: AbortSignal,
  ): Promise<T | null> {
    if (!canRun()) return Promise.resolve(null);
    const controller = new AbortController();
    const onAbort = () => controller.abort();
    outerSignal?.addEventListener("abort", onAbort, { once: true });
    queryControllers.current.add(controller);
    return filesWsRequest<T>(action, payload, responseType, {
      signal: controller.signal,
    }).finally(() => {
      queryControllers.current.delete(controller);
      outerSignal?.removeEventListener("abort", onAbort);
    });
  }

  const load = useCallback(
    async (path: string, cursor?: string | null, retry = true): Promise<TreeResult | null> => {
      const generation = queryGeneration.current;
      if (cursor) {
        setLoadingMore((previous) => new Set(previous).add(path));
      } else {
        setDirs((d) => ({ ...d, [path]: "loading" }));
      }
      const page = directoryPagesRef.current[path];
      const data = await fileQuery<TreeResult>(
        "project_file_tree",
        {
          project_id: projectId,
          path,
          ...(cursor ? { cursor, snapshot_id: page?.snapshotId } : {}),
        },
        "project_file_tree_result",
        () => generation === queryGeneration.current,
      );
      if (cursor) {
        setLoadingMore((previous) => {
          const next = new Set(previous);
          next.delete(path);
          return next;
        });
      }
      if (generation !== queryGeneration.current) return null;
      if (data?.error_code === "STALE_SNAPSHOT" && cursor && retry) {
        setDirs((d) => ({ ...d, [path]: "loading" }));
        const nextPages = { ...directoryPagesRef.current };
        delete nextPages[path];
        directoryPagesRef.current = nextPages;
        setDirectoryPages((pages) => {
          const next = { ...pages };
          delete next[path];
          return next;
        });
        return load(path, null, false);
      }
      if (!data || data.project_id !== projectId || data.error || data.error_code
        || data.path !== path || !data.entries) {
        setDirs((d) => ({ ...d, [path]: cursor && Array.isArray(d[path]) ? d[path] : "error" }));
        return data;
      }
      for (const e of data.entries) {
        if (e.type === "file") noteFileMtime(projectId, joinPath(path, e.name), e.mtime);
      }
      setDirs((d) => {
        const existing = cursor && Array.isArray(d[path]) ? d[path] : [];
        const entries = [...existing, ...data.entries!].filter(
          (entry, index, all) => all.findIndex((candidate) => candidate.name === entry.name) === index,
        );
        return { ...d, [path]: entries };
      });
      const nextPage = {
        snapshotId: data.snapshot_id ?? null,
        nextCursor: data.next_cursor ?? null,
      };
      directoryPagesRef.current = { ...directoryPagesRef.current, [path]: nextPage };
      setDirectoryPages((pages) => ({ ...pages, [path]: nextPage }));
      return data;
    },
    [projectId],
  );

  // (Re)load the root whenever the project changes.
  useEffect(() => {
    for (const controller of queryControllers.current) controller.abort();
    queryGeneration.current += 1;
    searchGeneration.current += 1;
    setDirs({});
    setDirectoryPages({});
    directoryPagesRef.current = {};
    setLoadingMore(new Set());
    setExpanded(new Set());
    // 组件跨项目复用（右栏不带 key 渲染）：上一个项目的选中行、
    // 内联新建/重命名、筛选词都指向旧根目录下的相对路径，留着会
    // 误指到新项目里同名路径上，切根时一并清掉。
    setSelected(null);
    setCreating(null);
    setRenaming(null);
    setMenu(null);
    setFilter("");
    setSearchResults([]);
    setSearchPage(1);
    setSearchHasMore(false);
    setSearchLoading(false);
    setSearchError(null);
    setSearchOpen(false);
    revealTarget.current = null;
    if (revealScrollTimer.current) clearTimeout(revealScrollTimer.current);
    if (revealFlashTimer.current) clearTimeout(revealFlashTimer.current);
    revealScrollTimer.current = null;
    revealFlashTimer.current = null;
    void load("");
    return () => {
      for (const controller of queryControllers.current) controller.abort();
      queryGeneration.current += 1;
    };
  }, [load]);

  useEffect(() => {
    let cancelled = false;
    const resolveRoot = () => {
      void projectAbsPath(projectId).then((path) => {
        if (!cancelled) setProjectRoot(path);
      });
    };
    setProjectRoot(null);
    resolveRoot();
    const onProjectChanged = () => {
      projectPathCache.delete(projectId);
      resolveRoot();
    };
    window.addEventListener("project-changed", onProjectChanged);
    return () => {
      cancelled = true;
      window.removeEventListener("project-changed", onProjectChanged);
    };
  }, [projectId]);

  function toggleDir(path: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
    if (dirs[path] === undefined) load(path);
  }

  function loadMore(path: string) {
    const page = directoryPagesRef.current[path];
    if (!page?.nextCursor || loadingMore.has(path)) return;
    void load(path, page.nextCursor);
  }

  function refetchRoot() {
    for (const controller of queryControllers.current) controller.abort();
    queryGeneration.current += 1;
    searchGeneration.current += 1;
    setSearchResults([]);
    setSearchPage(1);
    setSearchHasMore(false);
    setSearchError(null);
    setSearchLoading(false);
    setDirs({});
    setDirectoryPages({});
    directoryPagesRef.current = {};
    setLoadingMore(new Set());
    setExpanded(new Set());
    revealTarget.current = null;
    if (revealScrollTimer.current) clearTimeout(revealScrollTimer.current);
    if (revealFlashTimer.current) clearTimeout(revealFlashTimer.current);
    revealScrollTimer.current = null;
    revealFlashTimer.current = null;
    void load("");
  }

  useEffect(() => {
    const invalidate = (event: Event) => {
      const detail = (event as CustomEvent<{ project_id?: string }>).detail;
      if (detail?.project_id !== projectId) return;
      for (const controller of queryControllers.current) controller.abort();
      queryGeneration.current += 1;
      searchGeneration.current += 1;
      setSearchResults([]);
      setSearchPage(1);
      setSearchHasMore(false);
      setSearchError(null);
      setSearchLoading(false);
      refetchRoot();
    };
    window.addEventListener("project-files-changed", invalidate);
    return () => window.removeEventListener("project-files-changed", invalidate);
  }, [projectId]);

  /* ---- file management ops ---------------------------------------- */

  /** Run one worker file op; on success re-list the affected dirs and
   *  broadcast ``project-files-changed`` (reveal mutates nothing, so it
   *  passes no dirs and skips the event). */
  async function fileOp(
    op: "create" | "rename" | "copy" | "delete" | "reveal",
    payload: Record<string, unknown>,
    refreshDirs: string[],
  ): Promise<boolean> {
    const generation = queryGeneration.current;
    const operationPayload = { project_id: projectId, ...payload };
    let operationKey: string;
    try {
      operationKey = idempotencyKeyFor(`project_file_${op}`, operationPayload);
    } catch (error) {
      if (error instanceof MutationRegistryCapacityError) {
        window.alert(text("Too many file operations are still pending.", "仍有太多文件操作未完成。"));
      }
      return false;
    }
    const operationController = new AbortController();
    queryControllers.current.add(operationController);
    const data = await wsMutationRequest<{
      project_id?: string;
      path?: string;
      ok?: boolean;
      status?: string;
      error_code?: string;
      error?: string;
    }>(
      operationKey,
      (signal) => fileQuery<{ project_id?: string; path?: string; ok?: boolean; status?: string; error_code?: string; error?: string }>(
        `project_file_${op}`,
        { ...operationPayload, idempotency_key: operationKey },
        `project_file_${op}_result`,
        () => generation === queryGeneration.current,
        signal,
      ),
      { signal: operationController.signal },
    );
    queryControllers.current.delete(operationController);
    if (!data || data.project_id !== projectId || data.path !== payload.path
      || data.error || data.status === "conflict" || data.status === "recovery_required"
      || data.status === "error" || data.status === "in_progress") {
      if (data?.error || data?.error_code) window.alert(data.error ?? data.error_code);
      return false;
    }
    // The project-files-changed listener performs one generation-safe refresh;
    // avoid starting directory requests that the event would immediately abort.
    void refreshDirs;
    if (op !== "reveal") {
      window.dispatchEvent(new CustomEvent("project-files-changed", {
        detail: { project_id: projectId },
      }));
    }
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

  async function locateTreePath(path: string, type: "file" | "dir"): Promise<boolean> {
    const parts = path.split("/");
    let directory = "";
    for (let index = 0; index < parts.length - 1; index += 1) {
      const child = parts[index];
      const page = await load(directory);
      let loaded = page;
      while (
        loaded?.next_cursor &&
        !loaded.entries?.some((entry) => entry.name === child && entry.type === "dir")
      ) {
        loaded = await load(directory, loaded.next_cursor);
      }
      if (!loaded?.entries?.some((entry) => entry.name === child && entry.type === "dir")) {
        return false;
      }
      setExpanded((previous) => new Set(previous).add(joinPath(directory, child)));
      directory = joinPath(directory, child);
    }
    const target = parts[parts.length - 1];
    let loaded = await load(directory);
    while (
      loaded?.next_cursor &&
      !loaded.entries?.some((entry) => entry.name === target && entry.type === type)
    ) {
      loaded = await load(directory, loaded.next_cursor);
    }
    return Boolean(loaded?.entries?.some((entry) => entry.name === target && entry.type === type));
  }

  async function revealSearchResult(path: string, type: "file" | "dir") {
    setFilter("");
    setSearchOpen(false);
    if (!(await locateTreePath(path, type))) return;
    setSelected({ path, type });
    revealTarget.current = path;
    if (type === "file") openFile(path);
  }

  /** "Reveal in file tree" from elsewhere in the app (the per-turn file
   *  edit card): expand every ancestor, select the row, then — once the
   *  async dir loads have actually rendered it — scroll it into view and
   *  flash it, so a deeply nested file is findable without hunting.
   *  Wired as a window CustomEvent so the caller needs no ref into this
   *  tree. ponytail: reuses expandChain + the existing `selected` state. */
  useEffect(() => {
    const onReveal = (ev: Event) => {
      const d = (ev as CustomEvent<{ projectId?: string; path?: string }>).detail;
      if (!d?.path || (d.projectId && d.projectId !== projectId)) return;
      setFilter(""); // rows only render in tree mode
      setSearchOpen(false);
      expandChain(parentOf(d.path));
      setSelected({ path: d.path, type: "file" });
      revealTarget.current = d.path;
    };
    window.addEventListener("project-file-reveal-in-tree", onReveal);
    return () => window.removeEventListener("project-file-reveal-in-tree", onReveal);
  });
  // After every render: if a reveal is pending and its row now exists
  // (ancestor dirs finished their async loads), scroll + flash once.
  useEffect(() => {
    const path = revealTarget.current;
    if (!path) return;
    const row = rootRef.current?.querySelector<HTMLElement>(
      `[data-tree-path="${CSS.escape(path)}"]`,
    );
    if (!row) return; // ancestors still loading — retry on next render
    revealTarget.current = null;
    row.scrollIntoView({ block: "center" });
    row.classList.add(styles.treeRowFlash);
    if (revealFlashTimer.current) clearTimeout(revealFlashTimer.current);
    revealFlashTimer.current = setTimeout(() => {
      row.classList.remove(styles.treeRowFlash);
      revealFlashTimer.current = null;
    }, 1200);
  });

  /** Directory a create targets: selected dir → itself, selected file
   *  → its parent, nothing selected → project root. */
  function startCreate(kind: "file" | "dir", dir?: string) {
    const target =
      dir ?? (selected ? (selected.type === "dir" ? selected.path : parentOf(selected.path)) : "");
    setFilter(""); // the editable row only renders in tree mode
    setSearchOpen(false);
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
    const result = await runServerRenameWithDrafts(
      projectId,
      oldPath,
      newPath,
      () => fileOp("rename", { path: oldPath, new_path: newPath }, [dir]),
    );
    if (result.ok) retargetOpenTabs(oldPath, newPath);
    else if (result.message) window.alert(result.message);
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
      const result = await runServerRenameWithDrafts(
        projectId,
        clip.path,
        dest,
        () => fileOp(
          "rename",
          { path: clip.path, new_path: dest },
          [parentOf(clip.path), targetDir],
        ),
      );
      if (result.ok) {
        treeClipboard.current = null;
        retargetOpenTabs(clip.path, dest);
      } else if (result.message) window.alert(result.message);
    } else {
      await fileOp("copy", { path: clip.path, new_path: dest }, [targetDir]);
    }
  }

  async function doDelete(path: string) {
    const hasDraft = await hasDirtyDraftsForPath(projectId, path);
    if (hasDraft && !window.confirm(text("Discard unsaved changes before deleting?", "删除前丢弃未保存的修改？"))) return;
    await runAfterServerFileOperation(
      () => fileOp("delete", { path }, [parentOf(path)]),
      async () => {
        if (hasDraft) {
          const cleared = await clearFileDraftsForPath(projectId, path);
          if (!cleared.ok) {
            window.alert(cleared.message ?? text("Unable to discard the local draft; tabs remain open.", "无法丢弃本地草稿；文件标签仍保持打开。"));
            return false;
          }
        }
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
        return true;
      },
    );
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

  const searchableEntries = useMemo(() => {
    const entries = new Map<string, TreeEntry>();
    for (const [dir, state] of Object.entries(dirs)) {
      if (!Array.isArray(state)) continue;
      for (const e of state) {
        const full = joinPath(dir, e.name);
        entries.set(full, e);
      }
    }
    return [...entries].map(([path, entry]) => ({ path, entry }));
  }, [dirs]);
  const fetchSearchPage = useCallback(async (generation: number, reset = false) => {
    const query = searchQuery.current;
    if (!query || generation !== searchGeneration.current || searchLoadingRef.current) return;
    searchLoadingRef.current = true;
    searchLoadingGeneration.current = generation;
    setSearchLoading(true);
    try {
      if (reset) {
        searchCursor.current = null;
        searchSnapshot.current = null;
        searchRows.current.clear();
        setSearchResults([]);
      }
      let cursor = searchCursor.current;
      let snapshotId = searchSnapshot.current;
      let retriedStale = false;
      while (generation === searchGeneration.current) {
        const data: SearchResultPayload | null = await fileQuery<SearchResultPayload>(
          "project_file_search",
          {
            project_id: projectId,
            path: "",
            query,
            mode: searchModeRef.current,
            type: "all",
            page_size: 100,
            ...(cursor ? { cursor, snapshot_id: snapshotId } : {}),
          },
          "project_file_search_result",
          () => generation === searchGeneration.current,
        );
        if (generation !== searchGeneration.current) return;
        if (data?.error_code === "STALE_SNAPSHOT" && cursor && !retriedStale) {
          cursor = null;
          snapshotId = null;
          searchCursor.current = null;
          searchSnapshot.current = null;
          searchRows.current.clear();
          setSearchResults([]);
          retriedStale = true;
          continue;
        }
        if (!data || data.project_id !== projectId || data.error_code || !data.results) {
          setSearchError(data?.error_code ?? "IO_ERROR");
          setSearchResults([]);
          break;
        }
        for (const result of data.results) {
          if (searchRows.current.size >= MAX_SEARCH_RESULTS) break;
          searchRows.current.set(result.path, result);
        }
        searchCursor.current = data.next_cursor ?? null;
        searchSnapshot.current = data.snapshot_id ?? null;
        setSearchResults([...searchRows.current.values()]);
        setSearchHasMore(Boolean(searchCursor.current)
          && searchRows.current.size < MAX_SEARCH_RESULTS);
        break;
      }
    } finally {
      if (searchLoadingGeneration.current === generation) {
        searchLoadingRef.current = false;
        searchLoadingGeneration.current = null;
        if (generation === searchGeneration.current) setSearchLoading(false);
      }
    }
  }, [projectId]);

  useEffect(() => {
    const query = filter.trim();
    const generation = ++searchGeneration.current;
    searchQuery.current = query;
    searchModeRef.current = fuzzySearch ? "fuzzy" : "contains";
    setSearchResults([]);
    setSearchHasMore(false);
    setSearchError(null);
    searchCursor.current = null;
    searchSnapshot.current = null;
    searchRows.current.clear();
    searchLoadingRef.current = false;
    setSearchLoading(false);
    if (!query) {
      setSearchLoading(false);
      return;
    }
    const timer = setTimeout(() => void fetchSearchPage(generation, true), 200);
    return () => clearTimeout(timer);
  }, [fetchSearchPage, filter, fuzzySearch, projectId]);

  const searchMatches = useMemo(() => {
    if (filter.trim()) return searchResults.map((entry) => ({ path: entry.path, entry }));
    return searchableEntries
      .filter(({ entry }) => matchingIndexes(entry.name, filter, fuzzySearch))
      .sort((left, right) => left.path.localeCompare(right.path));
  }, [filter, fuzzySearch, searchableEntries, searchResults]);
  const visiblePaths = useMemo(
    () => visibleSearchPaths(searchableEntries.map(({ path }) => path), filter, fuzzySearch),
    [filter, fuzzySearch, searchableEntries],
  );
  const currentSearchPath = searchMatches.length
    ? searchMatches[searchMatchIndex % searchMatches.length].path
    : null;
  const currentSearchType = searchMatches.length
    ? searchMatches[searchMatchIndex % searchMatches.length].entry.type
    : null;

  useEffect(() => {
    setSearchMatchIndex(0);
  }, [filter, fuzzySearch, searchMode]);

  useEffect(() => {
    if (!currentSearchPath) return;
    const generation = queryGeneration.current;
    if (filter.trim() && searchMode === "highlight") {
      void locateTreePath(
        currentSearchPath,
        currentSearchType ?? "file",
      );
    }
    const parents: string[] = [];
    let parent = parentOf(currentSearchPath);
    while (parent) {
      parents.push(parent);
      parent = parentOf(parent);
    }
    if (parents.length) {
      setExpanded((previous) => new Set([...previous, ...parents]));
    }
    const timer = setTimeout(() => {
      if (generation !== queryGeneration.current) return;
      rootRef.current?.querySelector<HTMLElement>(
        `[data-tree-path="${CSS.escape(currentSearchPath)}"]`,
      )?.scrollIntoView({ block: "nearest" });
    });
    revealScrollTimer.current = timer;
    return () => {
      clearTimeout(timer);
      if (revealScrollTimer.current === timer) revealScrollTimer.current = null;
    };
  }, [currentSearchPath, currentSearchType, filter, searchMode]);

  function moveSearchResult(delta: number) {
    if (!searchMatches.length) return;
    setSearchMatchIndex((index) => (index + delta + searchMatches.length) % searchMatches.length);
  }

  function renderSearchError(): React.ReactNode {
    if (!searchError) return null;
    const message =
      searchError === "LIMIT_EXCEEDED"
        ? text("Search results exceed the limit", "搜索结果超过限制")
        : searchError === "PERMISSION"
          ? text("Search permission denied", "搜索权限不足")
          : searchError === "IO_ERROR"
            ? text("Search failed due to an I/O error", "搜索发生 I/O 错误")
            : searchError === "INVALID_REQUEST"
              ? text("Invalid search request", "搜索请求无效")
              : text("Search failed", "搜索失败");
    return <div className={styles.treeHint}>{message}</div>;
  }

  function renderSearchResults(): React.ReactNode {
    return (
      <div role="list" aria-label={text("Project search results", "项目搜索结果")}>
        {searchMatches.map(({ path, entry }) => (
          <div key={path} role="listitem">
            <button
              type="button"
              className={styles.treeRow}
              data-tree-path={path}
              title={path}
              onClick={() => void revealSearchResult(path, entry.type)}
            >
              {entry.type === "dir" ? (
                <Folder size={15} className={styles.treeIconFolder} />
              ) : (
                <FileGlyph name={entry.name} />
              )}
              <ExplorerMatchText
                className={styles.treeName}
                value={entry.name}
                query={filter}
                fuzzy={fuzzySearch}
                current={currentSearchPath === path}
              />
              <span className={styles.treeHint}>{path}</span>
            </button>
          </div>
        ))}
        {searchHasMore ? (
          <button
            type="button"
            className={styles.treeRow}
            aria-label={text("Load more search results", "加载更多搜索结果")}
            disabled={searchLoading}
            onClick={() => {
              setSearchPage((page) => Math.min(page + 1, 5));
              void fetchSearchPage(searchGeneration.current);
            }}
          >
            {searchLoading ? text("Loading…", "加载中…") : text("Load more", "加载更多")}
          </button>
        ) : null}
      </div>
    );
  }

  function renderDir(dir: string, depth: number): React.ReactNode {
    const state = dirs[dir];
    const hintPad = TREE_LABEL_OFFSET + depth * INDENT;
    // Editable "new entry" row, rendered at the top of the target dir.
    const createRow =
      creating && creating.dir === dir ? (
        <div className={styles.treeNode}>
          <div
            className={styles.treeRow}
            style={{ paddingLeft: TREE_BASE_PAD + depth * INDENT }}
          >
            {creating.kind === "dir" ? (
              <Folder size={14} className={styles.treeIconFolder} />
            ) : (
              <File size={14} className={styles.treeIcon} />
            )}
            <InlineNameInput
              initial=""
              onCommit={commitCreate}
              onCancel={() => setCreating(null)}
            />
          </div>
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
      return null;
    }
    const rows = state.map((e) => {
      const full = joinPath(dir, e.name);
      if (filter.trim() && searchMode === "filter" && !visiblePaths.has(full)) return null;
      const selectedCls = selected?.path === full ? styles.treeRowSelected : "";
      const current = currentSearchPath === full;
      if (e.type === "dir") {
        const isOpen = expanded.has(full);
        const hasVisibleChild = [...visiblePaths].some((path) => path.startsWith(`${full}/`));
        const displayOpen = isOpen || (searchMode === "filter" && Boolean(filter.trim()) && hasVisibleChild);
        return (
          <div key={full} className={styles.treeNode}>
            <div
              data-tree-path={full}
              className={`${styles.treeRow} ${DIM_DIRS.has(e.name) ? styles.treeRowDim : ""} ${selectedCls}`}
              style={{ paddingLeft: TREE_BASE_PAD + depth * INDENT }}
              onClick={() => {
                setSelected({ path: full, type: "dir" });
                toggleDir(full);
              }}
              onContextMenu={(ev) => onRowContextMenu(ev, full, "dir")}
              title={full}
            >
              {displayOpen ? (
                <FolderOpen size={15} className={styles.treeIconFolder} />
              ) : (
                <Folder size={15} className={styles.treeIconFolder} />
              )}
              {renaming === full ? (
                <InlineNameInput
                  initial={e.name}
                  onCommit={(v) => commitRename(full, v)}
                  onCancel={() => setRenaming(null)}
                />
              ) : (
                <ExplorerMatchText className={styles.treeName} value={e.name} query={filter} fuzzy={fuzzySearch} current={current} />
              )}
            </div>
            {displayOpen ? (
              // The child container inherits the parent folder's icon
              // center as its tree rail. Each direct child draws its
              // own vertical segment and a short connector to its icon.
              <div
                className={styles.treeKids}
                style={{ "--guide-x": `${TREE_BASE_PAD + 8 + depth * INDENT}px` } as React.CSSProperties}
              >
                {renderDir(full, depth + 1)}
              </div>
            ) : null}
          </div>
        );
      }
      return (
        <div key={full} className={styles.treeNode}>
          <div
            data-tree-path={full}
            className={`${styles.treeRow} ${full === activePath ? styles.treeRowActive : ""} ${selectedCls}`}
            style={{ paddingLeft: TREE_BASE_PAD + depth * INDENT }}
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
              <ExplorerMatchText className={styles.treeName} value={e.name} query={filter} fuzzy={fuzzySearch} current={current} />
            )}
          </div>
        </div>
      );
    });
    return (
      <>
        {createRow}
        {rows}
        {directoryPages[dir]?.nextCursor ? (
          <button
            type="button"
            className={styles.treeRow}
            style={{ paddingLeft: TREE_BASE_PAD + depth * INDENT }}
            aria-label={text("Load more entries", "加载更多条目")}
            disabled={loadingMore.has(dir)}
            onClick={() => loadMore(dir)}
          >
            {loadingMore.has(dir)
              ? text("Loading…", "加载中…")
              : text("Load more", "加载更多")}
          </button>
        ) : null}
      </>
    );
  }

  return (
    <div className={styles.treeCol} ref={rootRef}>
      <ExplorerHeader
        leading={headerExtra}
        rootName={projectRoot ? baseOf(projectRoot) : text("Resolving project…", "正在读取项目…")}
        rootPath={projectRoot}
        searchOpen={searchOpen}
        onSearchOpenChange={setSearchOpen}
        query={filter}
        onQueryChange={setFilter}
        mode={searchMode}
        onModeChange={setSearchMode}
        fuzzy={fuzzySearch}
        onFuzzyChange={setFuzzySearch}
        resultCount={searchMatches.length}
        resultIndex={searchMatches.length ? searchMatchIndex % searchMatches.length : 0}
        onMoveResult={moveSearchResult}
        actions={
          <>
            <button
              type="button"
              className={styles.iconBtn}
              onClick={() => startCreate("file")}
              title={text("New File", "新建文件")}
            >
              <FilePlus />
            </button>
            <button
              type="button"
              className={styles.iconBtn}
              onClick={() => startCreate("dir")}
              title={text("New Folder", "新建文件夹")}
            >
              <FolderPlus />
            </button>
            <button
              type="button"
              className={styles.iconBtn}
              onClick={refetchRoot}
              title={text("Refresh", "刷新")}
            >
              <RotateCw />
            </button>
          </>
        }
      />
      <div className={styles.treeBody}>
        {filter.trim() && searchMode === "filter" ? (
          searchLoading && searchMatches.length === 0 ? (
            <div className={styles.treeHint}>{text("Searching…", "搜索中…")}</div>
          ) : searchError ? (
            renderSearchError()
          ) : searchMatches.length === 0 ? (
            <div className={styles.treeHint}>{text("No matches", "无匹配")}</div>
          ) : renderSearchResults()
        ) : (
          <>
            {filter.trim() ? renderSearchError() : null}
            {renderDir("", 0)}
          </>
        )}
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
                setSearchOpen(false);
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
