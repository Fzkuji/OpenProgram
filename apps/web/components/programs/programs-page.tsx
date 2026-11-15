"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { useRouter } from "next/navigation";
import {
  Boxes,
  ChevronDown,
  ChevronRight,
  FileCode,
  GitBranch,
  Network,
  RefreshCw,
  Star,
  Wrench,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { ManageEmptyState, ManagePageHeader, managePageStyles } from "@/components/ui/manage-page";
import { SecondaryRail } from "@/components/ui/secondary-rail";
import {
  ExplorerHeader,
  type ExplorerSearchMode,
} from "@/components/files/explorer/explorer-header";
import { matchingIndexes } from "@/components/files/explorer/explorer-search";
import {
  PierreFileTree,
  PierreSearchTree,
  type PierreTreeEntry,
  type PierreTreeHandle,
} from "@/components/files/explorer/pierre-file-tree";
import { useTranslation } from "@/lib/i18n";
import { createProgramsCache } from "./programs-cache";
import { jsonFetch } from "@/lib/net/fetch-client";
import { runtimeState } from "@/lib/runtime-bridge/state";
import { useFunctions } from "@/lib/abilities/functions-store";
import type { FunctionsMeta } from "@/lib/types";

import {
  buildGraphLayout,
  buildCallTreeRows,
  GRAPH_NODE_HEIGHT,
  GRAPH_NODE_WIDTH,
  type LogicResponse,
  type ProgramKind,
} from "./programs-logic";
import {
  applicationNoteLabel,
  applicationPermissions,
  callLogicSummary,
  type ApplicationNote,
  type ProgramExplorerEntry,
} from "./programs-catalog";
import { ProgramDetail, type ProgramWindow } from "./program-detail";
import { resolveProgramSelection, takeProgramSelection } from "./programs-selection";
import { applicationRequest } from "@/lib/net/applications";
import { refreshApplications, useApplicationList } from "@/lib/net/application-list";
import { useApplicationLauncher } from "@/components/applications/use-application-launcher";
import { useCenterTabs } from "@/lib/tabs/center-tabs-store";

type ProgramSourceEntry = {
  name?: string | null;
  path?: string | null;
  callable_name?: string | null;
  entity_kind?: "harness";
  program_kind?: string | null;
  origin?: "builtin" | "installed";
  enabled?: boolean;
  note?: ApplicationNote;
};

import { ProgramLoadState } from "./program-load-state";
import styles from "./programs-page.module.css";
import fileStyles from "@/components/files/files-panel.module.css";

type ExplorerEntry = ProgramExplorerEntry;

type ExplorerResponse = {
  path: string;
  entries: ExplorerEntry[];
  default_selection?: string | null;
};

const ROOT_LABEL = "openprogram/programs";
const catalogCache = createProgramsCache(jsonFetch);
// Preserve the file-explorer view while navigating away and back.
let savedView: {
  directories: Record<string, ExplorerEntry[]>;
  selected: string | null;
  expanded: Set<string>;
  view: "tree" | "graph";
} = { directories: {}, selected: null, expanded: new Set(), view: "tree" };


function catalogLabel(
  entry: { name: string; path: string },
  text: (en: string, zh: string) => string,
) {
  if (entry.path === "tools") return text("Tools", "工具");
  if (entry.path === "applications") return text("Applications", "应用");
  return entry.name;
}

function parentPaths(path: string) {
  const parts = path.split("/");
  return parts.slice(0, -1).map((_, index) => parts.slice(0, index + 1).join("/"));
}

function kindLabel(
  kind: ProgramKind | null,
  text: (en: string, zh: string) => string,
  entry?: ProgramSourceEntry | null,
) {
  if (kind === "application") {
    const label = entry?.origin === "builtin"
      ? text("Built-in application", "内置应用")
      : entry?.origin === "installed"
        ? text("Installed application", "已安装应用")
        : text("Application", "应用");
    const details = [
      label,
      applicationNoteLabel(entry?.note, text),
      entry?.enabled === false ? text("Disabled", "已停用") : "",
    ].filter(Boolean);
    return details.join(" · ");
  }
  if (kind === "runtime_primitive") return text("Agentic Programming primitive", "Agentic Programming 原语");
  if (kind === "tool") return text("Tool", "工具");
  if (!kind && entry && !entry.callable_name) return text("Source module", "源码模块");
  return text("Tool", "工具");
}

function usesApplicationIcon(entry: ProgramSourceEntry) {
  return entry.program_kind === "application" || entry.entity_kind === "harness";
}

/** Explorer entries as Pierre rows, parents before children: folders are
 *  directories, source files and callables are files. */
function treeEntries(directories: Record<string, ExplorerEntry[]>): PierreTreeEntry[] {
  const rows: PierreTreeEntry[] = [];
  const visit = (path: string) => {
    for (const entry of directories[path] || []) {
      rows.push({ path: entry.path, type: entry.kind === "folder" ? "dir" : "file", size: 0 });
      if (entry.kind === "folder" && entry.path !== path) visit(entry.path);
    }
  };
  visit("");
  return rows;
}

/** A row the detail pane opens: a file, or a folder that is a Program. */
function selectable(entry: ExplorerEntry) {
  return entry.kind === "file" || Boolean(entry.program_kind || entry.callable_name);
}

export function ProgramsPage({
  embedded,
  query,
  reloadNonce,
}: {
  embedded?: boolean;
  query?: string;
  reloadNonce?: number;
} = {}) {
  const { t, text } = useTranslation();
  const router = useRouter();
  const [directories, setDirectories] = useState<Record<string, ExplorerEntry[]>>(() => savedView.directories);
  const [selected, setSelected] = useState<string | null>(() => savedView.selected);
  const [logic, setLogic] = useState<LogicResponse | null>(() => catalogCache.peek<LogicResponse>("logic", savedView.selected || "") || null);
  const [loadingTree, setLoadingTree] = useState(() => !savedView.directories[""]);
  const [loadingLogic, setLoadingLogic] = useState(false);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchMode, setSearchMode] = useState<ExplorerSearchMode>("filter");
  const [fuzzySearch, setFuzzySearch] = useState(true);
  const [searchMatchIndex, setSearchMatchIndex] = useState(0);
  const [expanded, setExpanded] = useState<Set<string>>(() => savedView.expanded);
  const [view, setView] = useState<"tree" | "graph">(() => savedView.view);
  const [callExpansion, setCallExpansion] = useState<Map<string, boolean>>(new Map());
  useEffect(() => setCallExpansion(new Map()), [selected]);
  const [reloadToken, setReloadToken] = useState(0);
  const [meta, setMeta] = useState<FunctionsMeta>(() => useFunctions.getState().meta);
  const treeRef = useRef<PierreTreeHandle>(null);
  // The row last clicked, and the Program it opened: a folder click moves
  // the tree's highlight without changing the detail, and a callable row
  // shares its logic path with its package.
  const [treeClick, setTreeClick] = useState<{ path: string; selected: string | null } | null>(null);
  // A "Show in Abilities" target, scrolled to once its row is in the tree.
  const pendingReveal = useRef<string | null>(null);
  const currentView = useRef({ directories, selected, expanded, view });
  currentView.current = { directories, selected, expanded, view };
  useEffect(() => { savedView = currentView.current; }, [directories, selected, expanded, view]);

  const loadDirectory = useCallback(async (path: string, signal?: AbortSignal) => {
    const response = await catalogCache.read<ExplorerResponse>("explorer", path, signal);
    setDirectories(current => {
      if (current[path] === response.entries) return current;
      const next = { ...current, [path]: response.entries };
      const present = new Set(response.entries.map(entry => entry.path));
      for (const removed of current[path] || []) {
        if (present.has(removed.path)) continue;
        for (const key of Object.keys(next)) {
          if (key === removed.path || key.startsWith(`${removed.path}/`)) delete next[key];
        }
      }
      return next;
    });
    return response;
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    let cancelled = false;
    let inFlight = false;
    async function revalidate() {
      if (inFlight || document.visibilityState === "hidden") return;
      inFlight = true;
      if (!currentView.current.directories[""]) setLoadingTree(true);
      try {
        const root = await loadDirectory("", controller.signal);
        if (cancelled) return;
        // "Show in Abilities" from the Applications page names an application.
        const request = takeProgramSelection();
        const requested = request
          ? resolveProgramSelection((await loadDirectory("applications", controller.signal)).entries, request)
          : null;
        if (cancelled) return;
        const initial = requested || currentView.current.selected || root.default_selection || null;
        const parents = initial ? parentPaths(initial) : [];
        const opened = [...new Set([...currentView.current.expanded, ...parents])];
        await Promise.all(opened.map(async path => {
          try { await loadDirectory(path, controller.signal); }
          catch (cause) {
            if ((cause as { status?: number }).status !== 404) throw cause;
            if (!cancelled) setDirectories(previous => {
              const next = { ...previous }; delete next[path]; return next;
            });
          }
        }));
        if (cancelled) return;
        if (requested || !currentView.current.selected) {
          setExpanded(previous => new Set([...previous, ...parents]));
          setSelected(initial);
          if (requested) pendingReveal.current = requested;
        }
        setError("");
      } catch (cause) {
        if (!cancelled && (cause as Error).name !== "AbortError") setError(text("Programs could not be refreshed. Showing the last loaded content.", "Programs 刷新失败，保留上次加载的内容。"));
      } finally {
        inFlight = false;
        if (!cancelled) setLoadingTree(false);
      }
    }
    void revalidate();
    const timer = window.setInterval(revalidate, 5000);
    window.addEventListener("focus", revalidate);
    document.addEventListener("visibilitychange", revalidate);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
      window.removeEventListener("focus", revalidate);
      document.removeEventListener("visibilitychange", revalidate);
      controller.abort();
    };
  }, [loadDirectory, reloadToken, text]);

  useEffect(() => {
    const controller = new AbortController();
    void jsonFetch<FunctionsMeta>("/api/programs/meta", { signal: controller.signal })
      .then((next) => {
        setMeta(next);
        runtimeState.programsMeta = {
          favorites: [...next.favorites],
          folders: { ...next.folders },
          icons: { ...(next.icons || {}) },
        };
        useFunctions.getState().setMeta(next);
      })
      .catch((cause) => {
        if ((cause as Error).name !== "AbortError") {
          setError(text("Favorites could not be loaded.", "无法加载收藏。"));
        }
      });
    return () => controller.abort();
  }, [text]);

  const treeEntriesByPath = useMemo(() => {
    const entries = new Map<string, ExplorerEntry>();
    for (const directoryEntries of Object.values(directories)) {
      for (const entry of directoryEntries) entries.set(entry.path, entry);
    }
    return entries;
  }, [directories]);
  const selectedEntry = selected ? treeEntriesByPath.get(selected) : undefined;
  const pierreEntries = useMemo(() => treeEntries(directories), [directories]);
  const treeSelection = treeClick && treeClick.selected === selected && treeEntriesByPath.has(treeClick.path)
    ? treeClick.path
    : selected && !treeEntriesByPath.has(selected)
      ? [...treeEntriesByPath.values()].find(entry => entry.logic_path === selected)?.path ?? null
      : selected;

  useEffect(() => {
    const controller = new AbortController();
    let cancelled = false;
    if (!selected) {
      setLogic(null);
      return () => controller.abort();
    }
    const logicPath = selectedEntry?.logic_path || selected;
    const cached = catalogCache.peek<LogicResponse>("logic", logicPath);
    setLogic(cached || null);
    setLoadingLogic(!cached);
    let inFlight = false;
    let appliedResult: LogicResponse | undefined;
    const refreshLogic = () => {
      if (inFlight || document.visibilityState === "hidden") return;
      inFlight = true;
      void catalogCache.read<LogicResponse>("logic", logicPath, controller.signal)
      .then((result) => {
        if (!cancelled && appliedResult !== result) {
          appliedResult = result;
          setError("");
          setLogic(selectedEntry ? {
            ...result,
            nodes: result.nodes.map((node) => node.id === result.root
              ? { ...node, name: selectedEntry.name, path: selectedEntry.path }
              : node),
          } : result);
        }
      })
      .catch((cause) => {
        if (!cancelled && (cause as Error).name !== "AbortError") {
          if ((cause as { status?: number }).status === 404) {
            setLogic(null);
            setSelected(null);
          }
          setError(text("Call logic could not be refreshed.", "调用逻辑刷新失败。"));
        }
      })
      .finally(() => {
        inFlight = false;
        if (!cancelled) setLoadingLogic(false);
      });
    };
    refreshLogic();
    const timer = window.setInterval(refreshLogic, 5000);
    window.addEventListener("focus", refreshLogic);
    document.addEventListener("visibilitychange", refreshLogic);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
      window.removeEventListener("focus", refreshLogic);
      document.removeEventListener("visibilitychange", refreshLogic);
      controller.abort();
    };
  }, [selected, selectedEntry, text, reloadToken]);

  const selectedNode = logic?.nodes.find((node) => node.id === logic.root) ?? null;
  const invocationName = selectedEntry?.callable_name || "";
  // An application row or a tool gets the full detail; a file inside a
  // package keeps the file header over its call logic.
  const detailEntry = selectedEntry && (
    selectedEntry.program_kind === "tool"
    || (selectedEntry.program_kind === "application" && selectedEntry.path.split("/").length === 2)
  ) ? selectedEntry : undefined;
  const launcher = useApplicationLauncher(() => router.push("/chat"));
  const { applications: applicationDefinitions } = useApplicationList();
  useEffect(() => { void refreshApplications().catch(() => undefined); }, []);
  const [changing, setChanging] = useState(false);
  const definition = detailEntry?.program_kind === "application"
    ? applicationDefinitions.find((item) => detailEntry.application_id
      ? item.id === detailEntry.application_id
      : detailEntry.origin === "builtin" && item.builtin && item.id === detailEntry.name)
    : undefined;
  const programWindow: ProgramWindow | null = definition ? {
    label: definition.scope === "project"
      ? text("Opens in a tab, uses the bound project's files", "在标签页中打开，使用绑定项目的文件")
      : text("Opens in a tab", "在标签页中打开"),
    permissions: applicationPermissions(definition.capabilities, text),
    open: () => void launcher.launch(definition),
  } : detailEntry?.origin === "builtin" && detailEntry.name === "browser" ? {
    label: text("Opens as a browser tab", "作为浏览器标签页打开"),
    permissions: "",
    open: () => { useCenterTabs.getState().openBuiltinTab("browser"); router.push("/chat"); },
  } : null;
  const isFavorite = Boolean(invocationName) && meta.favorites.includes(invocationName);
  const callTree = useMemo(() => logic ? buildCallTreeRows(logic, 256, callExpansion) : { rows: [], truncated: false }, [logic, callExpansion]);
  const graphLayout = useMemo(() => logic ? buildGraphLayout(logic) : null, [logic]);
  const searchMatches = useMemo(() => {
    if (!search.trim()) return [];
    return [...treeEntriesByPath.values()]
      .filter((entry) =>
        matchingIndexes(entry.name, search, fuzzySearch)
        || matchingIndexes(catalogLabel(entry, text), search, fuzzySearch)
      )
      .sort((left, right) => left.path.localeCompare(right.path));
  }, [fuzzySearch, search, text, treeEntriesByPath]);
  const searchEntries = useMemo(
    () => searchMatches.map((entry): PierreTreeEntry => ({ path: entry.path, type: entry.kind === "folder" ? "dir" : "file", size: 0 })),
    [searchMatches],
  );
  const currentSearchPath = searchMatches.length
    ? searchMatches[searchMatchIndex % searchMatches.length].path
    : null;

  useEffect(() => {
    setSearchMatchIndex(0);
  }, [search, fuzzySearch, searchMode]);

  useEffect(() => {
    if (!currentSearchPath) return;
    const parents = parentPaths(currentSearchPath);
    setExpanded((previous) => new Set([...previous, ...parents]));
    const timer = setTimeout(() => treeRef.current?.reveal(currentSearchPath));
    return () => clearTimeout(timer);
  }, [currentSearchPath]);

  useEffect(() => {
    const path = pendingReveal.current;
    if (!path || !pierreEntries.some(entry => entry.path === path)) return;
    const timer = setTimeout(() => { if (treeRef.current?.reveal(path)) pendingReveal.current = null; });
    return () => clearTimeout(timer);
  }, [pierreEntries]);

  useEffect(() => {
    if (query === undefined) return;
    setSearch(query);
    setSearchOpen(Boolean(query.trim()));
    if (query.trim()) setSearchMode("filter");
  }, [query]);

  useEffect(() => {
    if (!reloadNonce) return;
    void (async () => {
      await fetch("/api/programs/refresh", { method: "POST" }).catch(() => undefined);
      setReloadToken((value) => value + 1);
    })();
  }, [reloadNonce]);

  async function loadOpenedDirectory(path: string) {
    const entry = treeEntriesByPath.get(path);
    if (!entry || entry.kind !== "folder" || !entry.has_children) return;
    try {
      await loadDirectory(path);
    } catch {
      setError(text("Folder could not be loaded.", "无法加载文件夹。"));
    }
  }

  function changeExpanded(next: Set<string>) {
    setExpanded(next);
    for (const path of next) if (!directories[path]) void loadOpenedDirectory(path);
  }

  function selectRow(path: string) {
    const entry = treeEntriesByPath.get(path);
    if (!entry) return;
    const next = selectable(entry) ? entry.logic_path || entry.path : selected;
    setTreeClick({ path, selected: next });
    if (next !== selected) setSelected(next);
  }

  function moveSearchResult(delta: number) {
    if (!searchMatches.length) return;
    setSearchMatchIndex((index) => (index + delta + searchMatches.length) % searchMatches.length);
  }

  function renderTree() {
    if (loadingTree) return <div className={fileStyles.treeHint}>{text("Loading…", "加载中…")}</div>;
    // Filtering shows the matches under their folders, as Files does.
    if (search.trim() && searchMode === "filter") {
      if (!searchMatches.length) return <div className={fileStyles.treeHint}>{text("No matches", "无匹配")}</div>;
      return <PierreSearchTree ref={treeRef} columns={false} readOnly matches={searchEntries} currentPath={currentSearchPath}
        query={!fuzzySearch ? search : undefined} onSelect={selectRow} />;
    }
    return <PierreFileTree ref={treeRef} columns={false} readOnly entries={pierreEntries} expanded={expanded} selected={treeSelection}
      query={search.trim() && !fuzzySearch ? search : undefined}
      onExpandedChange={changeExpanded} onSelect={selectRow} />;
  }

  async function refreshPrograms() {
    await fetch("/api/programs/refresh", { method: "POST" }).catch(() => undefined);
    setReloadToken((value) => value + 1);
  }

  async function toggleFavorite() {
    if (!invocationName) return;
    const favorites = isFavorite
      ? meta.favorites.filter((name) => name !== invocationName)
      : [...meta.favorites, invocationName];
    const next = { ...meta, favorites };
    setMeta(next);
    runtimeState.programsMeta = {
      favorites: [...next.favorites],
      folders: { ...next.folders },
      icons: { ...(next.icons || {}) },
    };
    useFunctions.getState().setMeta(next);
    await fetch("/api/programs/meta", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(next),
    }).catch(() => undefined);
  }

  async function setEnabled(entry: ExplorerEntry, enabled: boolean) {
    const id = entry.application_id || entry.name;
    setChanging(true);
    try {
      await applicationRequest(`/api/applications/${encodeURIComponent(id)}`, "PATCH", { enabled });
      await refreshApplications({ fresh: true }).catch(() => undefined);
      await refreshPrograms();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setChanging(false);
    }
  }

  const useInChat = () => router.push(`/chat?${new URLSearchParams({ run: invocationName, cat: selectedNode?.program_kind || "" })}`);
  const loadStateLine = selectedEntry?.load_state && (selectedEntry.load_state !== "loaded" || selectedEntry.load_note) ? (
    <ProgramLoadState key={selectedEntry.path} entry={selectedEntry} />
  ) : null;

  const renderCallLogic = (logic: LogicResponse) => <>
                {logic.analysis_kind === "function_calls" ? <div className={styles.noCalls}>{text("Current source · possible function calls, not execution order · refreshes automatically. External libraries and object methods are not expanded.", "当前源码 · 展示可能的函数调用，不代表执行顺序 · 自动刷新。外部库与对象方法不展开。")}</div> : null}
                {logic.analysis_complete === false ? <div className={styles.analysisWarning} role="status">{text("Incomplete analysis", "分析不完整")}: {logic.analysis_warnings?.join(", ")}</div> : null}
                <div className={styles.logicToolbar}><div><strong>{text("Call logic", "调用逻辑")}</strong><small>{logic.analysis_kind === "function_calls" ? text("Source-derived function calls", "从当前源码识别的函数调用") : text("Static Program imports and Agentic Programming calls", "静态 Program import 与 Agentic Programming 调用")}</small></div><ToggleGroup type="single" variant="elevated" size="sm" spacing={1} value={view} onValueChange={(value: string) => { if (value) setView(value as typeof view); }} aria-label={text("Call logic view", "调用逻辑视图")}><ToggleGroupItem value="tree"><GitBranch size={14} />Call tree</ToggleGroupItem><ToggleGroupItem value="graph"><Network size={14} />Graph</ToggleGroupItem></ToggleGroup></div>
                {view === "tree" ? (
                  <div className={styles.callTree} data-testid="programs-call-tree">
                    {callTree.rows.map(({ key, node, depth, cycle, reference, ancestorContinuations, isLast, conditional, expandable, isExpanded }) => {
                      const Row = expandable ? "button" : "div";
                      return <Row key={key} type={expandable ? "button" : undefined} aria-expanded={expandable ? isExpanded : undefined} onClick={expandable ? () => setCallExpansion(current => new Map(current).set(node.id, !isExpanded)) : undefined} className={`${styles.callRow} ${expandable ? styles.callToggle : ""} ${depth === 0 ? styles.callRoot : ""} ${reference ? styles.callReference : ""}`} style={{ "--call-depth": depth } as CSSProperties} data-edge-target={depth ? node.id : undefined}>
                      {depth > 0 ? <span className={styles.callGuides} aria-hidden="true">
                        {ancestorContinuations.map((continues, index) => <i key={index} className={continues ? styles.callGuideActive : ""} />)}
                        <i className={isLast ? styles.callGuideLast : styles.callGuideBranch} />
                      </span> : null}
                      <span className={styles.callIcon}>{expandable ? (isExpanded ? <ChevronDown size={16} aria-hidden="true" /> : <ChevronRight size={16} aria-hidden="true" />) : usesApplicationIcon(node) ? <Boxes size={16} /> : node.program_kind ? <Wrench size={16} /> : <FileCode size={16} />}</span><span className={styles.callLabel}><strong>{node.name}</strong><small>{node.path}</small></span><em>{conditional ? text("conditional", "条件调用") : cycle ? "cycle" : reference ? "reference" : depth === 0 ? "root" : depth === 1 ? "direct" : "transitive"}</em>
                    </Row>;})}
                    {callTree.truncated ? <div className={styles.noCalls}>{text("Additional references are hidden.", "其余引用已隐藏。")}</div> : null}
                    {logic.nodes.length === 1 && logic.analysis_complete !== false ? <div className={styles.noCalls}>{text("This Program has no detected Program or Agentic Programming calls.", "未检测到这个 Program 对其他 Program 或 Agentic Programming 原语的调用。")}</div> : null}
                  </div>
                ) : (
                  <div className={styles.callGraph} data-testid="programs-call-graph">
                    {graphLayout ? <div className="min-w-[100%] relative" style={{ width: graphLayout.width, height: graphLayout.height }} aria-label={text("Program dependency graph", "Program 依赖图") }>
                      <svg className={`${styles.graphLines} absolute inset-[0] overflow-visible`} width={graphLayout.width} height={graphLayout.height} aria-hidden="true">
                        <defs><marker id="program-graph-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto"><path d="M0 0 L8 4 L0 8 Z" /></marker></defs>
                        {graphLayout.edges.map(({ source, target }) => {
                          const x1 = source.x + GRAPH_NODE_WIDTH;
                          const y1 = source.y + GRAPH_NODE_HEIGHT / 2;
                          const x2 = target.x;
                          const y2 = target.y + GRAPH_NODE_HEIGHT / 2;
                          const sameColumn = source.x === target.x;
                          const skip = x2 - x1 > GRAPH_NODE_WIDTH + 24;
                          const bend = Math.max(16, (x2 - x1) / 2);
                          const d = sameColumn
                            ? `M ${x1} ${y1} C ${x1 + 22} ${y1}, ${x1 + 22} ${y2}, ${x1} ${y2}`
                            : `M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x2 - bend} ${y2}, ${x2} ${y2}`;
                          return <path key={`${source.id}->${target.id}`} className={skip ? styles.graphSkipEdge : undefined} data-edge={`${source.id}->${target.id}`} d={d} strokeDasharray={logic.edges.some((edge) => edge.source === source.id && edge.target === target.id && edge.kind === "conditional") ? "5 4" : undefined} markerEnd="url(#program-graph-arrow)" />;
                        })}
                      </svg>
                      {graphLayout.nodes.map((node) => <div key={node.id} className={`${styles.graphNode} ${node.id === logic.root ? styles.graphRoot : ""}`} style={{ left: node.x, top: node.y }} data-graph-node={node.id}>
                        <span className={styles.graphNodeIcon}>{usesApplicationIcon(node) ? <Boxes size={16} /> : node.program_kind ? <Wrench size={16} /> : <FileCode size={16} />}</span>
                        <span><strong>{node.name}</strong><small>{node.path}</small></span>
                      </div>)}
                    </div> : null}
                  </div>
                )}
  </>;

  const workspace = (
        <div className={styles.workspace}>
          <SecondaryRail as="aside" label={text("Programs catalog", "Programs 目录")} className={styles.catalog} data-testid="programs-explorer">
            {/* Embedded: the hub header owns search + refresh, so the
                48px explorer header would only repeat the refresh button. */}
            {!embedded && <ExplorerHeader
              rootName="Programs"
              rootPath={ROOT_LABEL}
              showRootPath={false}
              hideSearch={false}
              searchOpen={searchOpen}
              onSearchOpenChange={setSearchOpen}
              query={search}
              onQueryChange={setSearch}
              mode={searchMode}
              onModeChange={setSearchMode}
              fuzzy={fuzzySearch}
              onFuzzyChange={setFuzzySearch}
              resultCount={searchMatches.length}
              resultIndex={searchMatches.length ? searchMatchIndex % searchMatches.length : 0}
              onMoveResult={moveSearchResult}
              actions={
                <button className={fileStyles.iconBtn} type="button" title={text("Refresh", "刷新")} onClick={() => { void refreshPrograms(); }}>
                  <RefreshCw />
                </button>
              }
            />}
            <div className={styles.tree}>{renderTree()}</div>
          </SecondaryRail>
          <section className="min-w-0 min-h-0 relative overflow-auto">
            {error ? (
              <div role="alert">
                <ManageEmptyState
                  compact
                  icon={<Network />}
                  title={text("Programs are unavailable", "暂时无法加载 Program")}
                  description={error}
                  action={<Button variant="elevated" onClick={() => void refreshPrograms()}>{text("Retry", "重试")}</Button>}
                />
              </div>
            ) : !selected ? (
              <ManageEmptyState
                compact
                icon={<Network />}
                title={text("No Programs found", "没有找到 Program")}
                description={text("Install an application or add a Tool.", "安装一个应用，或添加 Tool。")}
              />
            ) : loadingLogic ? (
              <div className={styles.empty}><RefreshCw className={styles.spin} size={24} /><span>{text("Loading call logic…", "正在加载调用逻辑…")}</span></div>
            ) : !logic || !selectedNode ? (
              <ManageEmptyState compact icon={<Network />} title={text("Call logic is unavailable", "调用逻辑不可用")} />
            ) : (
              <div className={`${styles.logicContent} w-[min(100%,_1040px)] m-[0_auto] p-[24px_30px_60px]`}>
                <div className={styles.breadcrumb}>{selectedNode.path}</div>
                {selectedNode.entity_kind === "harness" && <div className={styles.harnessNote}>
                  <p>{text("Source", "来源")}: {selectedNode.source_path}</p>
                  <p>{text("Manage this application from the CLI environment that owns its dependencies. Packaged Desktop dependencies are read-only.", "在拥有此应用依赖的 CLI 环境中管理。打包桌面版的依赖环境只读。")}</p>
                  <pre>{selectedNode.management_commands?.join("\n")}</pre>
                </div>}
                {launcher.dialog}
                {launcher.error ? <div className={styles.loadState} role="alert">{launcher.error}</div> : null}
                {detailEntry ? (
                  <ProgramDetail
                    entry={detailEntry}
                    path={selectedNode.path}
                    window={programWindow}
                    invocationName={invocationName}
                    isFavorite={isFavorite}
                    onFavorite={() => void toggleFavorite()}
                    onUse={useInChat}
                    onEnabled={detailEntry.program_kind === "application" && detailEntry.enabled !== undefined
                      ? (enabled) => void setEnabled(detailEntry, enabled)
                      : null}
                    busy={changing}
                    status={loadStateLine}
                    callLogicCount={callLogicSummary(logic, text)}
                    callLogic={renderCallLogic(logic)}
                  />
                ) : <>
                  <div className={styles.entityHeader}>
                    <span className={styles.entityIcon}>{usesApplicationIcon(selectedEntry || selectedNode) ? <Boxes size={20} /> : invocationName ? <Wrench size={20} /> : <FileCode size={20} />}</span>
                    <div><h2>{selectedNode.name}</h2><p>{kindLabel(selectedNode.program_kind, text, selectedEntry || selectedNode)}</p></div>
                    {invocationName ? (
                      <div className="flex items-center gap-[6px]">
                        <Button
                          variant="ghost"
                          size="icon"
                          aria-label={isFavorite ? text("Unfavorite", "取消收藏") : text("Favorite", "收藏")}
                          aria-pressed={isFavorite}
                          onClick={() => void toggleFavorite()}
                        >
                          <Star className={isFavorite ? styles.favoriteIconActive : styles.favoriteIcon} fill={isFavorite ? "currentColor" : "none"} />
                        </Button>
                        <Button onClick={useInChat}>{text("Use", "使用")}</Button>
                      </div>
                    ) : null}
                  </div>
                  {loadStateLine}
                  <div className="mt-[20px]">{renderCallLogic(logic)}</div>
                </>}
              </div>
            )}
          </section>
        </div>
  );

  if (embedded) return workspace;

  return (
    <div className="main">
      <div className={managePageStyles.view}>
        <ManagePageHeader title={t("nav.programs")} />
        {workspace}
      </div>
    </div>
  );
}
