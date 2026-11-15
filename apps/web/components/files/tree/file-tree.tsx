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
import { revealLabel } from "@/lib/files/reveal-file";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  FilePlus,
  Info,
  FolderPlus,
  RotateCw,
} from "lucide-react";

import { useTranslation } from "@/lib/i18n";
import { useEffectEvent } from "@/lib/hooks/use-effect-event";
import {
  fileResponseMatchesOwner,
  noteFileMtime,
} from "@/lib/files/files-shared";
import { useCenterTabs } from "@/lib/tabs/center-tabs-store";
import {
  reconcileWsMutation,
  wsRequest,
} from "@/lib/net/ws/ws-request";
import { navigate } from "@/lib/nav/navigate";
import { ContextMenu, ContextMenuTrigger } from "@/components/ui/context-menu";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { TreeContextMenu, treeClipboard } from "../explorer/tree-context-menu";
import { MoveToDialog } from "../explorer/move-to-dialog";
import { rememberMoveTarget } from "../explorer/move-targets";
import {
  ExplorerHeader,
  type ExplorerSearchMode,
} from "../explorer/explorer-header";
import {
  matchingIndexes,
} from "../explorer/explorer-search";
import { baseOf, joinPath, parentOf, projectAbsPath, projectPathCache } from "./file-tree-query";
import { runFileOperation, type FileOperation } from "./file-tree-operation";
import { createFileTreeActions } from "./file-tree-actions";
import { FileBreadcrumb, FileDetails, FileSortMenu, useFileSort, invalidateFolderSizes } from "../file-management";
import { HoverTip } from "@/components/ui/tooltip";
import { Button } from "@/components/ui/button";
import styles from "../files-panel.module.css";
import { PierreFileTree, PierreSearchTree, type PierreTreeEntry, type PierreTreeHandle } from "../explorer/pierre-file-tree";
import { collectDroppedFiles, uploadDroppedFiles } from "./file-tree-drop";
import { ProjectLocationNotice } from "../project-location-notice";
import { showToast } from "@/lib/format-utils/toast";

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
const MAX_SEARCH_RESULTS = 500;


/** Absolute project root per project id — fetched once via
 *  ``list_projects`` (the tree itself only knows the project id). */

/* The 14px glyph is centered in a 16px slot. Advancing 27px makes the
   child's visible glyph edge meet its parent label's start exactly. */

/** Extension bucket → icon + colour (existing accent tokens only). */
type DirState = TreeEntry[] | "loading" | "error";

export function FileTree({
  projectId,
  headerExtra,
  central = false,
}: {
  projectId: string;
  central?: boolean;
  /** Slot rendered before the filter input (the right sidebar puts
   *  its collapse toggle here so header stays a single row). */
  headerExtra?: React.ReactNode;
}) {
  const { text } = useTranslation();
  const openFileTab = useCenterTabs((s) => s.openFileTab);
  const recordFileNavigation = useCenterTabs((s) => s.recordFileNavigation);
  const fileNavigationEntry = useCenterTabs(s => s.fileNavigationRestore);
  const initialNavigation = useRef(useCenterTabs.getState().fileNavigationHistory);
  const appliedNavigation = useRef<typeof fileNavigationEntry>(null);
  const restoreGeneration = useRef(0);
  // Highlight the file whose center tab is active (primitive selector,
  // so recomputing per store change is re-render-safe).
  const activePath = useCenterTabs((s) => {
    const t = s.tabs.find((x) => x.id === s.activeId);
    return t?.kind === "file" && t.projectId === projectId
      ? (t.path ?? null)
      : null;
  });
  const recordNavigation = (path: string, type: "file" | "dir", expandedPaths = expanded) => {
    if (!recordFileNavigation) return;
    restoreGeneration.current += 1;
    const scroll = pierreRef.current?.getScrollState() ?? null;
    useCenterTabs.getState().updateFileNavigationView?.({ expanded: [...expandedPaths].sort(), scroll });
    if (type === "file") openFileTab(projectId, path);
    recordFileNavigation({
      projectId,
      path,
      selectedType: type,
      expanded: [...expandedPaths].sort(),
      scroll,
    });
  };
  const openFile = (path: string) => {
    recordNavigation(path, "file");
    // Preserve the conversation route: changing it reactivates its session tab.
    const pathname = typeof window === "undefined" ? "" : window.location?.pathname ?? "";
    if (pathname !== "/chat" && !pathname.startsWith("/s/")) navigate("/chat");
  };
  const [sort, setSort] = useFileSort(projectId);
  const sortRef = useRef(sort);
  sortRef.current = sort;
  const [detailsPath, setDetailsPath] = useState<string | null>(null);
  const [dirs, setDirs] = useState<Record<string, DirState>>({});
  const [directoryPages, setDirectoryPages] = useState<Record<string, DirectoryPage>>({});
  const [refreshErrors, setRefreshErrors] = useState<Set<string>>(new Set());
  const [loadingMore, setLoadingMore] = useState<Set<string>>(new Set());
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const treeStateRef = useRef({ dirs, expanded });
  treeStateRef.current = { dirs, expanded };
  const [filter, setFilter] = useState("");
  const [searchRefresh, setSearchRefresh] = useState(0);
  const [searchResults, setSearchResults] = useState<SearchResult[]>([]);
  const [searchLoading, setSearchLoading] = useState(false);
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
  // Every selected row (Cmd/Ctrl- and Shift-click); `selected` is the last.
  const [selection, setSelection] = useState<{ path: string; type: "file" | "dir" }[]>([]);
  // The root listing was refused because the project's folder is unavailable.
  const [locationUnavailable, setLocationUnavailable] = useState(false);
  const [creating, setCreating] = useState<{ dir: string; kind: "file" | "dir" } | null>(null);
  const [menuTarget, setMenuTarget] = useState<{ path: string; type: "file" | "dir" } | null>(null);
  // Set by a row's right-click on its way up to the tree's ContextMenu
  // trigger, which opens only for a row (see treeMenu).
  const rowMenuHit = useRef(false);
  const [confirmDelete, setConfirmDelete] = useState<string[] | null>(null);
  const [moveTargets, setMoveTargets] = useState<string[] | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const pierreRef = useRef<PierreTreeHandle>(null);
  const [pendingScrollRestore, setPendingScrollRestore] = useState<{ path: string; offset: number } | null>(null);
  const [detailsInline, setDetailsInline] = useState(false);
  useEffect(() => {
    if (!central) return;
    const observer = new ResizeObserver(([entry]) => setDetailsInline(entry.contentRect.width >= 700));
    if (rootRef.current) observer.observe(rootRef.current);
    return () => observer.disconnect();
  }, [central]);
  const directoryPagesRef = useRef<Record<string, DirectoryPage>>({});
  const queryControllers = useRef(new Set<AbortController>());
  const mutationControllers = useRef(new Set<AbortController>());
  const mutationRequestControllers = useRef(new Set<AbortController>());
  const mutationKeys = useRef(new Set<string>());
  const queryGeneration = useRef(0);
  const mutationLifecycleGeneration = useRef(0);
  const searchGeneration = useRef(0);
  const searchCursor = useRef<string | null>(null);
  const searchSnapshot = useRef<string | null>(null);
  const searchQuery = useRef("");
  const searchModeRef = useRef<"fuzzy" | "contains">("contains");
  const searchRows = useRef(new Map<string, SearchResult>());
  const searchLoadingRef = useRef(false);
  const searchLoadingGeneration = useRef<number | null>(null);
  const searchControllers = useRef(new Set<AbortController>());
  const revealTarget = useRef<string | null>(null);
  const revealScrollTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    const state = useCenterTabs.getState();
    const active = state.tabs.find(tab => tab.id === state.activeId);
    if (active?.kind !== "builtin" || active.page !== "files") return;
    const history = state.fileNavigationHistory;
    const hasProjectHistory = history?.entries.some(entry => entry.projectId === projectId);
    if (!hasProjectHistory) recordNavigation("", "dir", new Set());
  // Seed only a new project; remounting Files must preserve the history cursor.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);


  function abortSearchQueries(): void {
    for (const controller of searchControllers.current) controller.abort();
    searchControllers.current.clear();
  }

  function abortMutationRequests(): void {
    for (const controller of mutationControllers.current) controller.abort();
    for (const key of mutationKeys.current) reconcileWsMutation(key);
    mutationControllers.current.clear();
  }

  useEffect(() => {
    directoryPagesRef.current = directoryPages;
  }, [directoryPages]);

  function fileQuery<T>(
    action: string,
    payload: Record<string, unknown>,
    responseType: string,
    canRun: () => boolean,
    outerSignal?: AbortSignal,
    controllerSet: Set<AbortController> = queryControllers.current,
  ): Promise<T | null> {
    if (!canRun()) return Promise.resolve(null);
    const controller = new AbortController();
    const onAbort = () => controller.abort();
    outerSignal?.addEventListener("abort", onAbort, { once: true });
    controllerSet.add(controller);
    return wsRequest<T>(
      action,
      payload,
      responseType,
      (data) => fileResponseMatchesOwner(data as unknown as Record<string, unknown>, payload),
      4000,
      { signal: controller.signal },
    ).finally(() => {
      controllerSet.delete(controller);
      outerSignal?.removeEventListener("abort", onAbort);
    });
  }

  const load = useCallback(
    async (path: string, cursor?: string | null, retry = true, refreshedEntries?: TreeEntry[]): Promise<TreeResult | null> => {
      const generation = queryGeneration.current;
      if (cursor) {
        setLoadingMore((previous) => new Set(previous).add(path));
      } else if (!refreshedEntries) {
        setDirs((d) => ({ ...d, [path]: "loading" }));
      }
      const page = directoryPagesRef.current[path];
      const data = await fileQuery<TreeResult>(
        "project_file_tree",
        {
          project_id: projectId,
          path,
          sort: sortRef.current,
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
        if (refreshedEntries) refreshedEntries.length = 0;
        // Keep the loaded range mounted while a new snapshot replaces it.
        // Emptying this directory clamps the virtual tree's scroll position.
        const previous = treeStateRef.current.dirs[path];
        const retainedCount = Array.isArray(previous) ? previous.length : 0;
        const nextPages = { ...directoryPagesRef.current };
        delete nextPages[path];
        directoryPagesRef.current = nextPages;
        setDirectoryPages((pages) => {
          const next = { ...pages };
          delete next[path];
          return next;
        });
        if (refreshedEntries) return load(path, null, false, refreshedEntries);
        const replacement: TreeEntry[] = [];
        let result = await load(path, null, false, replacement);
        while (generation === queryGeneration.current && result?.next_cursor
          && !result.error && !result.error_code && replacement.length <= retainedCount) {
          result = await load(path, result.next_cursor, false, replacement);
        }
        if (generation !== queryGeneration.current) return null;
        if (result?.entries && !result.error && !result.error_code) {
          setDirs(previous => ({ ...previous, [path]: replacement.filter((entry, index) => replacement.findIndex(other => other.name === entry.name) === index) }));
          setDirectoryPages(previous => ({ ...previous, [path]: {
            snapshotId: result.snapshot_id ?? null, nextCursor: result.next_cursor ?? null,
          } }));
        }
        return result;
      }
      if (path === "" && !cursor) setLocationUnavailable(Boolean(data?.error?.startsWith("project location unavailable")));
      if (!data || data.project_id !== projectId || data.error || data.error_code
        || data.path !== path || !data.entries) {
        setDirs((d) => ({ ...d, [path]: (cursor || refreshedEntries) && Array.isArray(d[path]) ? d[path] : "error" }));
        return data;
      }
      for (const e of data.entries) {
        if (e.type === "file") noteFileMtime(projectId, joinPath(path, e.name), e.mtime);
      }
      if (refreshedEntries) refreshedEntries.push(...data.entries);
      else setDirs((d) => {
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
      if (!refreshedEntries) setDirectoryPages((pages) => ({ ...pages, [path]: nextPage }));
      return data;
    },
    [projectId],
  );

  // (Re)load the root whenever the project changes.
  useEffect(() => {
    abortSearchQueries();
    for (const controller of queryControllers.current) controller.abort();
    queryGeneration.current += 1;
    searchGeneration.current += 1;
    setDirs({});
    setDirectoryPages({});
    directoryPagesRef.current = {};
    setLoadingMore(new Set());
    setRefreshErrors(new Set());
    setExpanded(new Set());
    // 组件跨项目复用（右栏不带 key 渲染）：上一个项目的选中行、
    // 内联新建/重命名、筛选词都指向旧根目录下的相对路径，留着会
    // 误指到新项目里同名路径上，切根时一并清掉。
    setSelected(null);
    setSelection([]);
    setLocationUnavailable(false);
    setDetailsPath(null);
    setCreating(null);
    setMenuTarget(null);
    setFilter("");
    setSearchResults([]);
    setSearchHasMore(false);
    setSearchLoading(false);
    setSearchError(null);
    setSearchOpen(false);
    revealTarget.current = null;
    if (revealScrollTimer.current) clearTimeout(revealScrollTimer.current);
    revealScrollTimer.current = null;
    void load("");
    const controllers = queryControllers.current;
    return () => {
      for (const controller of controllers) controller.abort();
      abortMutationRequests();
      queryGeneration.current += 1;
      mutationLifecycleGeneration.current += 1;
    };
  }, [load]);

  const locate = useEffectEvent((...args: Parameters<typeof locateTreePath>) => locateTreePath(...args));
  const refetchTreeRoot = useEffectEvent(() => refetchRoot());
  useEffect(() => {
    if (!Array.isArray(dirs[""])) return;
    const initial = initialNavigation.current;
    const target = fileNavigationEntry ?? (!appliedNavigation.current ? initial?.entries[initial.index] : undefined);
    if (!target || target.projectId !== projectId || appliedNavigation.current === target) return;
    appliedNavigation.current = target;
    const epoch = ++restoreGeneration.current;
    setExpanded(new Set(target.expanded));
    setSelected({ path: target.path, type: target.selectedType });
    setPendingScrollRestore(null);
    void (async () => {
      const current = () => appliedNavigation.current === target && restoreGeneration.current === epoch;
      for (const path of target.expanded) {
        if (!current()) return;
        if (treeStateRef.current.dirs[path] === undefined) await load(path);
      }
      if (target.scroll && current()) await locate(target.scroll.path, undefined, current);
      if (current()) setPendingScrollRestore(target.scroll);
    })();
  }, [dirs, fileNavigationEntry, load, projectId]);

  useEffect(() => {
    if (pendingScrollRestore && pierreRef.current?.restoreScrollState(pendingScrollRestore)) setPendingScrollRestore(null);
  }, [dirs, expanded, pendingScrollRestore]);

  const previousSort = useRef(sort);
  useEffect(() => {
    if (previousSort.current === sort) return;
    previousSort.current = sort;
    for (const controller of queryControllers.current) controller.abort();
    queryGeneration.current += 1;
    setDirs({});
    setDirectoryPages({});
    directoryPagesRef.current = {};
    setLoadingMore(new Set());
    setRefreshErrors(new Set());
    void load("");
    for (const path of expanded) void load(path);
  }, [sort, load, expanded]);

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

  const pendingPages = useRef(new Map<string, number>());
  function loadMore(path: string) {
    const page = directoryPages[path];
    const generation = queryGeneration.current;
    if (!page?.nextCursor || loadingMore.has(path) || pendingPages.current.get(path) === generation || refreshErrors.has(path)) return;
    pendingPages.current.set(path, generation);
    const failed = () => {
      if (queryGeneration.current === generation) setRefreshErrors(previous => new Set(previous).add(path));
    };
    void load(path, page.nextCursor).then(result => {
      if (!result || result.error || result.error_code) failed();
    }).catch(failed).finally(() => {
      if (pendingPages.current.get(path) === generation) pendingPages.current.delete(path);
    });
  }

  /** Re-list the loaded directories. ``keepSizes`` leaves folder sizes
   *  alone, for the quiet refresh when the window regains focus. */
  function refetchRoot({ keepSizes = false }: { keepSizes?: boolean } = {}) {
    lastRefresh.current = Date.now();
    setSearchRefresh(value => value + 1);
    if (!keepSizes) invalidateFolderSizes(projectId);
    abortSearchQueries();
    for (const controller of queryControllers.current) controller.abort();
    queryGeneration.current += 1;
    searchGeneration.current += 1;
    setSearchResults([]);
    setSearchHasMore(false);
    setSearchError(null);
    setSearchLoading(false);
    const previous = treeStateRef.current;
    const retained = new Set(["", ...previous.expanded]);
    for (const dir of previous.expanded) {
      let parent = parentOf(dir);
      while (parent) { retained.add(parent); parent = parentOf(parent); }
    }
    const paths = [...retained];
    // Keep visible rows during revalidation; collapsed caches must be reloaded
    // on their next expansion. The event listener reads the latest view via ref.
    setDirs(Object.fromEntries(paths.filter(path => previous.dirs[path] !== undefined).map(path => [path, previous.dirs[path]])));
    setDirectoryPages({});
    directoryPagesRef.current = {};
    setLoadingMore(new Set());
    setRefreshErrors(new Set());
    revealTarget.current = null;
    if (revealScrollTimer.current) clearTimeout(revealScrollTimer.current);
    revealScrollTimer.current = null;
    const generation = queryGeneration.current;
    for (const path of paths) void (async () => {
      const oldRows = previous.dirs[path];
      const count = Array.isArray(oldRows) ? oldRows.length : 0;
      const entries: TreeEntry[] = [];
      let page = await load(path, null, true, entries);
      // Retain the user's loaded page range with fresh snapshot cursors.
      while (generation === queryGeneration.current && page?.next_cursor && entries.length < count) {
        page = await load(path, page.next_cursor, true, entries);
      }
      if (generation !== queryGeneration.current) return;
      if (!page?.entries || page.error || page.error_code) {
        setRefreshErrors(previous => new Set(previous).add(path));
        return;
      }
      setDirs(previous => ({ ...previous, [path]: entries.filter((entry, index) => entries.findIndex(other => other.name === entry.name) === index) }));
      const nextPage = { snapshotId: page.snapshot_id ?? null, nextCursor: page.next_cursor ?? null };
      setDirectoryPages(previous => ({ ...previous, [path]: nextPage }));
    })();
  }

  // Files changed outside the app show up when the window is used again:
  // coming back to it re-lists what is open, at most every few seconds.
  const lastRefresh = useRef(0);
  const refetchRef = useRef(refetchRoot);
  refetchRef.current = refetchRoot;
  useEffect(() => {
    const onReturn = () => {
      if (document.visibilityState === "hidden" || Date.now() - lastRefresh.current < 3000) return;
      if (treeStateRef.current.dirs[""] === undefined) return;
      refetchRef.current({ keepSizes: true });
    };
    window.addEventListener("focus", onReturn);
    document.addEventListener("visibilitychange", onReturn);
    return () => {
      window.removeEventListener("focus", onReturn);
      document.removeEventListener("visibilitychange", onReturn);
    };
  }, []);

  useEffect(() => {
    const invalidate = (event: Event) => {
      const detail = (event as CustomEvent<{ project_id?: string }>).detail;
      if (detail?.project_id !== projectId) return;
      abortSearchQueries();
      for (const controller of queryControllers.current) controller.abort();
      queryGeneration.current += 1;
      searchGeneration.current += 1;
      setSearchResults([]);
      setSearchHasMore(false);
      setSearchError(null);
      setSearchLoading(false);
      refetchTreeRoot();
    };
    window.addEventListener("project-files-changed", invalidate);
    return () => window.removeEventListener("project-files-changed", invalidate);
  }, [projectId]);

  /* ---- file management ops ---------------------------------------- */

  /** Run one worker file op; on success re-list the affected dirs and
   *  broadcast ``project-files-changed`` (reveal mutates nothing, so it
   *  passes no dirs and skips the event). */
  const fileOp: FileOperation = (op, payload, refreshDirs) => runFileOperation({
    projectId, text, fileQuery, mutationLifecycleGeneration, mutationKeys,
    mutationControllers, mutationRequestControllers,
  }, op, payload, refreshDirs);

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

  async function locateTreePath(path: string, type?: "file" | "dir", isCurrent: () => boolean = () => true): Promise<boolean> {
    const generation = queryGeneration.current;
    const locateEntry = async (directory: string, name: string, kind?: "file" | "dir") => {
      const matches = (entry: TreeEntry) => entry.name === name && (!kind || entry.type === kind);
      const cached = treeStateRef.current.dirs[directory];
      if (Array.isArray(cached) && cached.some(matches)) return true;
      let loaded = await load(directory);
      while (generation === queryGeneration.current && isCurrent() && loaded?.next_cursor && !loaded.entries?.some(matches)) {
        loaded = await load(directory, loaded.next_cursor);
      }
      return generation === queryGeneration.current && isCurrent() && Boolean(loaded?.entries?.some(matches));
    };
    const parts = path.split("/");
    let directory = "";
    for (let index = 0; index < parts.length - 1; index += 1) {
      const child = parts[index];
      if (!(await locateEntry(directory, child, "dir"))) return false;
      setExpanded((previous) => new Set(previous).add(joinPath(directory, child)));
      directory = joinPath(directory, child);
    }
    const target = parts[parts.length - 1];
    return locateEntry(directory, target, type);
  }

  async function revealSearchResult(path: string, type: "file" | "dir") {
    setFilter("");
    setSearchOpen(false);
    if (!(await locateTreePath(path, type))) return;
    setSelected({ path, type });
    recordNavigation(path, type);
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
    if (!pierreRef.current?.reveal(path)) return;
    revealTarget.current = null;
  });

  /** Directory a create targets: selected dir → itself, selected file
   *  → its parent, nothing selected → project root. */
  const { startCreate, commitCreate, commitRename, copyPathTo, pasteInto, moveEntries, doDelete } = createFileTreeActions({
    projectId,
    text,
    selected,
    creating,
    setFilter,
    setSearchOpen,
    expandChain,
    setCreating,
    setSelected,
    setDetailsPath,
    recordNavigation,
    openFile,
    fileOp,
  });

  /** The rows an action covers: the whole selection when the row is part
   *  of a multi-row selection, otherwise the row alone. */
  function actionTargets(path: string): string[] {
    const rows = currentSelection();
    return rows.length > 1 && rows.some(item => item.path === path) ? rows.map(item => item.path) : [path];
  }

  /** The multi-row selection while it still holds the selected row; a
   *  selection made elsewhere (opening a tab, revealing a file) replaces it. */
  function currentSelection(): { path: string; type: "file" | "dir" }[] {
    if (!selected) return [];
    return selection.some(item => item.path === selected.path) ? selection : [selected];
  }

  async function deleteEntries(paths: string[]) {
    // A folder's own deletion covers whatever else is selected inside it.
    const roots = paths.filter(path => !paths.some(other => other !== path && path.startsWith(other + "/")));
    for (const path of roots) await doDelete(path);
    setSelection([]);
  }

  async function dropFromDesktop(transfer: DataTransfer, dir: string) {
    const files = await collectDroppedFiles(transfer);
    if (!files.length) return;
    if (files.length > 1) showToast(text(`Adding ${files.length} files…`, `正在添加 ${files.length} 个文件…`));
    const outcome = await uploadDroppedFiles(projectId, dir, files);
    window.dispatchEvent(new CustomEvent("project-files-changed", { detail: { project_id: projectId } }));
    if (dir) expandChain(dir);
    const where = dir || text("the project root", "项目根目录");
    if (outcome.added) showToast(text(`Added ${outcome.added} file${outcome.added === 1 ? "" : "s"} to ${where}`, `已添加 ${outcome.added} 个文件到 ${where}`));
    if (outcome.existing.length) showToast(text(
      `${outcome.existing.length} already existed and were left unchanged`,
      `${outcome.existing.length} 个同名文件已存在，未覆盖`,
    ), { tone: "warn", duration: 6000 });
    if (outcome.failed.length) showToast(text(
      `${outcome.failed.length} could not be added`,
      `${outcome.failed.length} 个文件添加失败`,
    ), { tone: "error", duration: 6000 });
  }

  /** "Move to…": the chosen folder is remembered for next time and
   *  opened so the moved entries show where they landed. */
  async function moveInto(paths: string[], targetDir: string, create = false) {
    if (create && (await fileOp("create", { path: targetDir, kind: "dir" }, [parentOf(targetDir)])).status !== "ready") return;
    rememberMoveTarget(projectId, targetDir);
    const moved = await moveEntries(paths, targetDir);
    expandChain(targetDir);
    if (!moved) refetchRoot({ keepSizes: true });
  }

  async function moveSelection(paths: string[], targetDir: string) {
    // Pierre already shows the move; a refused one is undone by re-listing.
    if (!(await moveEntries(paths, targetDir))) refetchRoot({ keepSizes: true });
  }

  /** Finder-style keys on the tree: copy, cut, paste, move to, delete, rename. */
  function onTreeKeyDown(event: React.KeyboardEvent) {
    // The innermost target: keys typed in Pierre's name editor come out of
    // its shadow root retargeted to the tree host.
    const target = event.nativeEvent.composedPath()[0];
    if (target instanceof Element && target.closest("input, textarea, [contenteditable='true']")) return;
    const mod = event.metaKey || event.ctrlKey;
    const primary = selected;
    const paths = currentSelection().map(item => item.path);
    const key = event.key.toLowerCase();
    if (mod && (key === "c" || key === "x") && paths.length) {
      treeClipboard.current = { op: key === "x" ? "cut" : "copy", projectId, paths };
    } else if (mod && key === "v" && treeClipboard.current?.projectId === projectId) {
      void pasteInto(primary ? (primary.type === "dir" ? primary.path : parentOf(primary.path)) : "");
    } else if (((mod && key === "backspace") || key === "delete") && paths.length) {
      setConfirmDelete(paths);
    } else if (mod && event.shiftKey && key === "m" && paths.length) {
      setMoveTargets(paths);
    } else if (key === "f2" && primary && paths.length <= 1) {
      fileContextActions(primary.path, primary.type).rename();
    } else {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
  }

  function fileContextActions(path: string, type: "file" | "dir") {
    const targetDir = type === "dir" ? path : parentOf(path);
    return {
      info: () => setDetailsPath(path),
      reveal: () => void fileOp("reveal", { path }, []),
      newFile: () => startCreate("file", targetDir),
      newFolder: () => startCreate("dir", targetDir),
      copyPath: () => void copyPathTo(path, true),
      copyRelativePath: () => void copyPathTo(path, false),
      cut: () => { treeClipboard.current = { op: "cut", projectId, paths: actionTargets(path) }; },
      copy: () => { treeClipboard.current = { op: "copy", projectId, paths: actionTargets(path) }; },
      paste: () => void pasteInto(targetDir),
      moveTo: () => setMoveTargets(actionTargets(path)),
      rename: () => {
        setFilter("");
        setSearchOpen(false);
        expandChain(parentOf(path));
        pierreRef.current?.startRename(path);
      },
      delete: () => setConfirmDelete(actionTargets(path)),
    };
  }

  function onRowContextMenu(
    e: React.MouseEvent<HTMLElement>,
    path: string,
    type: "file" | "dir",
  ) {
    setSelected({ path, type });
    if (!selection.some(item => item.path === path)) setSelection([{ path, type }]);
    setMenuTarget({ path, type });
    rowMenuHit.current = true;
  }

  const webMenuActions = menuTarget
    ? fileContextActions(menuTarget.path, menuTarget.type)
    : null;

  /** The tree's right-click menu (shadcn ContextMenu at the pointer): it
   *  opens over a row only — elsewhere in the tree nothing opens. Keyed by
   *  project, so switching roots closes it. */
  function treeMenu(tree: React.ReactNode): React.ReactNode {
    return (
      // Non-modal: a modal menu's focus trap pulls focus back from the name
      // editor that Rename / New File open while it is still closing.
      <ContextMenu key={projectId} modal={false} onOpenChange={open => { if (!open) setMenuTarget(null); }}>
        <ContextMenuTrigger asChild onContextMenu={event => {
          if (!rowMenuHit.current) event.preventDefault();
          rowMenuHit.current = false;
        }}>
          <div style={{ flex: 1, minHeight: 0 }}>{tree}</div>
        </ContextMenuTrigger>
        {webMenuActions ? (
          <TreeContextMenu
            canPaste={treeClipboard.current?.projectId === projectId}
            count={menuTarget ? actionTargets(menuTarget.path).length : 1}
            revealLabel={revealLabel(text)}
            onInfo={webMenuActions.info}
            onReveal={webMenuActions.reveal}
            onNewFile={webMenuActions.newFile}
            onNewFolder={webMenuActions.newFolder}
            onCopyPath={webMenuActions.copyPath}
            onCopyRelativePath={webMenuActions.copyRelativePath}
            onCut={webMenuActions.cut}
            onCopy={webMenuActions.copy}
            onPaste={webMenuActions.paste}
            onMoveTo={webMenuActions.moveTo}
            onRename={webMenuActions.rename}
            onDelete={webMenuActions.delete}
          />
        ) : null}
      </ContextMenu>
    );
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
          undefined,
          searchControllers.current,
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
    for (const controller of searchControllers.current) controller.abort();
    searchControllers.current.clear();
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
    const controllers = searchControllers.current;
    return () => {
      clearTimeout(timer);
      for (const controller of controllers) controller.abort();
      controllers.clear();
      searchGeneration.current += 1;
    };
  }, [fetchSearchPage, filter, fuzzySearch, projectId, searchRefresh]);

  const searchMatches = useMemo(() => {
    if (filter.trim()) return searchResults.map((entry) => ({ path: entry.path, entry }));
    return searchableEntries
      .filter(({ entry }) => matchingIndexes(entry.name, filter, fuzzySearch))
      .sort((left, right) => left.path.localeCompare(right.path));
  }, [filter, fuzzySearch, searchableEntries, searchResults]);
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
      void locate(
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
      pierreRef.current?.reveal(currentSearchPath);
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

  const pierreSearchEntries = useMemo(() => searchMatches.map(({ path, entry }) => ({ path, type: entry.type, size: entry.size, mtime: entry.mtime })), [searchMatches]);
  function renderSearchResults(): React.ReactNode {
    return (
      <div role="list" aria-label={text("Project search results", "项目搜索结果")} style={{ height: "100%", display: "flex", flexDirection: "column" }}><div className={styles.treeHint}>{text("Matching files and their parent folders", "匹配文件及其父目录")}</div>
        {treeMenu(<PierreSearchTree key={projectId} ref={pierreRef} projectId={projectId} matches={pierreSearchEntries} currentPath={currentSearchPath} query={!fuzzySearch ? filter : undefined}
          onSelect={(path, type) => setSelected({ path, type })} onOpen={path => void revealSearchResult(path, "file")} onActivate={(path, type) => void revealSearchResult(path, type)} onContextMenu={onRowContextMenu} />)}
        {searchHasMore ? (
          <button
            type="button"
            className={styles.treeRow}
            aria-label={text("Load more search results", "加载更多搜索结果")}
            disabled={searchLoading}
            onClick={() => {
              void fetchSearchPage(searchGeneration.current);
            }}
          >
            {searchLoading ? text("Loading…", "加载中…") : text("Load more", "加载更多")}
          </button>
        ) : null}
      </div>
    );
  }

  const pierreEntries = useMemo(() => {
    const result: PierreTreeEntry[] = [];
    const visit = (dir: string) => {
      const entries = dirs[dir];
      if (!Array.isArray(entries)) return;
      for (const entry of entries) {
        const path = joinPath(dir, entry.name);
        result.push({ path, type: entry.type, size: entry.size, mtime: entry.mtime });
        if (entry.type === "dir") visit(path);
      }
    };
    visit("");
    return result;
  }, [dirs]);
  const visibleDirectories = ["", ...expanded].filter(dir => {
    let parent = parentOf(dir);
    while (parent) { if (!expanded.has(parent)) return false; parent = parentOf(parent); }
    return true;
  });
  const primaryPath = (filter.trim() ? currentSearchPath : null) ?? selected?.path ?? activePath ?? null;
  const selectedPaths = useMemo(
    () => primaryPath && selection.some(item => item.path === primaryPath) ? selection.map(item => item.path) : primaryPath ? [primaryPath] : [],
    [primaryPath, selection],
  );
  function renderTree() {
    if (locationUnavailable) {
      return <ProjectLocationNotice projectId={projectId} onRepaired={() => { setLocationUnavailable(false); setDirs({}); void load(""); }} />;
    }
    return <div style={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }} onKeyDown={onTreeKeyDown}>
      {dirs[""] === "loading" || dirs[""] === undefined ? <div className={styles.treeHint}>{text("Loading…", "加载中…")}</div> : null}
      {treeMenu(
        <PierreFileTree key={projectId} ref={pierreRef} projectId={projectId} sort={sort} onSortChange={setSort} entries={pierreEntries} query={searchMode === "highlight" && !fuzzySearch ? filter : undefined} matches={filter.trim() ? new Set(searchMatches.map(match => match.path)) : undefined} expanded={expanded} selected={primaryPath}
          onRowsRendered={paths => {
            if (pendingScrollRestore && pierreRef.current?.restoreScrollState(pendingScrollRestore)) setPendingScrollRestore(null);
            for (const dir of visibleDirectories) {
              const entries = dirs[dir];
              if (Array.isArray(entries) && entries.length && paths.has(joinPath(dir, entries[entries.length - 1].name))) loadMore(dir);
            }
          }}
          onExpandedChange={next => { setExpanded(next); useCenterTabs.getState().updateFileNavigationView?.({ expanded: [...next].sort(), scroll: pierreRef.current?.getScrollState() ?? null }); for (const path of next) if (dirs[path] === undefined) void load(path); }}
          selectedPaths={selectedPaths} movable onSelectionChange={setSelection}
          onMove={(paths, dir) => void moveSelection(paths, dir)}
          onExternalDrop={(transfer, dir) => void dropFromDesktop(transfer, dir)}
          onSelect={(path, type) => { setSelected({ path, type }); if (type === "dir") recordNavigation(path, type); }} onOpen={openFile} onContextMenu={onRowContextMenu}
          // The new entry's row appears once its folder's listing is in.
          draft={creating && (creating.dir === "" || Array.isArray(dirs[creating.dir])) ? creating : null}
          onDraftCommit={name => void commitCreate(name)} onDraftCancel={() => setCreating(null)}
          onRenameCommit={(path, name) => void commitRename(path, name)} />
      )}
      {visibleDirectories.map(dir => <div key={dir}>
        {dir && dirs[dir] === "loading" ? <div className={styles.treeHint}>{dir} · {text("Loading…", "加载中…")}</div> : null}
        {dirs[dir] === "error" || refreshErrors.has(dir) ? <button type="button" className={styles.treeRow} onClick={() => refetchRoot()}>{dir || "/"} · {text("Refresh failed — retry", "刷新失败，重试")}</button> : null}
      </div>)}
    </div>;
  }

  return (
    <div className={`${styles.treeCol} ${detailsInline && detailsPath !== null ? styles.treeWithDetails : ""}`} ref={rootRef}>
      <ExplorerHeader
        leading={headerExtra}
        pathNavigation={<FileBreadcrumb absolutePath={projectRoot ? `${projectRoot.replace(/\/$/, "")}/${selected?.path ?? activePath ?? ""}`.replace(/\/$/, "") || "/" : undefined} root={projectRoot ? baseOf(projectRoot) : text("Project", "项目")} path={selected?.path ?? activePath ?? ""} onLocate={path => {
          setFilter("");
          if (!path) { setSelected({ path: "", type: "dir" }); recordNavigation("", "dir", expanded); pierreRef.current?.scrollToTop(); return; }
          const type = path === selected?.path ? selected.type : path === activePath ? "file" : "dir";
          void locateTreePath(path, type).then(found => { if (found) { setSelected({ path, type }); recordNavigation(path, type); revealTarget.current = path; } });
        }} />}
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
            <HoverTip label={text("New File", "新建文件")}><Button
              type="button"
              variant="ghost"
              size="icon-sm"
              onClick={() => startCreate("file")}
              aria-label={text("New File", "新建文件")}
            >
              <FilePlus />
            </Button></HoverTip>
            <HoverTip label={text("New Folder", "新建文件夹")}><Button
              type="button"
              variant="ghost"
              size="icon-sm"
              onClick={() => startCreate("dir")}
              aria-label={text("New Folder", "新建文件夹")}
            >
              <FolderPlus />
            </Button></HoverTip>
            <HoverTip label={text("Refresh", "刷新")}><Button
              type="button"
              variant="ghost"
              size="icon-sm"
              onClick={() => refetchRoot()}
              aria-label={text("Refresh", "刷新")}
            >
              <RotateCw />
            </Button></HoverTip>
            <FileSortMenu value={sort} onChange={setSort} />
            <HoverTip label={text("Get Info", "查看详细信息")}><Button type="button" variant="ghost" size="icon-sm" onClick={() => setDetailsPath(selected?.path ?? activePath ?? "")} aria-label={text("Get Info", "查看详细信息")}><Info /></Button></HoverTip>
          </>
        }
      />
      <div className="flex-1 min-h-0 overflow-auto p-0" style={{ overflow: filter.trim() && searchMode === "filter" ? "auto" : "hidden" }}>
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
            {renderTree()}
          </>
        )}
      </div>

      {detailsPath !== null ? <FileDetails key={`${projectId}:${detailsPath}`} projectId={projectId} path={detailsPath} inline={detailsInline} onClose={() => setDetailsPath(null)} /> : null}

      <MoveToDialog
        open={moveTargets !== null}
        projectId={projectId}
        rootName={projectRoot ? baseOf(projectRoot) : text("Project", "项目")}
        paths={moveTargets ?? []}
        onOpenChange={open => { if (!open) setMoveTargets(null); }}
        onMove={dir => { if (moveTargets) void moveInto(moveTargets, dir); }}
        onCreateAndMove={dir => { if (moveTargets) void moveInto(moveTargets, dir, true); }}
      />

      {confirmDelete ? (
        <ConfirmDialog
          title={confirmDelete.length === 1
            ? text(`Delete "${baseOf(confirmDelete[0])}"?`, `删除“${baseOf(confirmDelete[0])}”？`)
            : text(`Delete ${confirmDelete.length} items?`, `删除这 ${confirmDelete.length} 项？`)}
          confirmLabel={text("Delete", "删除")}
          destructive
          description={text(
            "Deleted items are moved to recoverable storage. Use openprogram trash list and openprogram trash restore to recover them.",
            "删除的内容会移入可恢复存储。可通过 openprogram trash list 和 openprogram trash restore 恢复。",
          )}
          onConfirm={() => {
            const paths = confirmDelete;
            setConfirmDelete(null);
            void deleteEntries(paths);
          }}
          onCancel={() => setConfirmDelete(null)}
        />
      ) : null}
    </div>
  );
}
