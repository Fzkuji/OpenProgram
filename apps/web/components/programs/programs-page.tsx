"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import {
  Boxes,
  ChevronDown,
  ChevronRight,
  FileCode,
  Folder,
  FolderOpen,
  GitBranch,
  Network,
  RefreshCw,
  Star,
  Workflow,
  Wrench,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { ManagePageHeader, managePageStyles } from "@/components/ui/manage-page";
import {
  ExplorerHeader,
  ExplorerMatchText,
  type ExplorerSearchMode,
} from "@/components/files/explorer-header";
import {
  EXPLORER_BASE_PAD,
  EXPLORER_INDENT,
  matchingIndexes,
  visibleSearchPaths,
} from "@/components/files/explorer-search";
import { useTranslation } from "@/lib/i18n";
import { jsonFetch } from "@/lib/net/fetch-client";
import { runtimeState } from "@/lib/runtime-bridge/state";
import { useFunctions } from "@/lib/state/functions-store";
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
  type ProgramExplorerEntry,
} from "./programs-catalog";
import {
  isUserManualWorkflowEntry,
  isWorkflowCapability,
  type ProgramSourceEntry,
} from "./programs-source";

import styles from "./programs-page.module.css";
import fileStyles from "@/components/files/files-panel.module.css";

type ExplorerEntry = ProgramExplorerEntry;

type ExplorerResponse = {
  path: string;
  entries: ExplorerEntry[];
  default_selection?: string | null;
};

const ROOT_LABEL = "openprogram/programs";

function catalogLabel(
  entry: { name: string; path: string },
  text: (en: string, zh: string) => string,
) {
  if (entry.path === "tools") return text("Tools", "工具");
  if (entry.path === "workflow") return text("Workflow", "工作流");
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
  if (isUserManualWorkflowEntry(entry)) return text("Auto entry · user only", "自动入口 · 仅用户手动");
  if (isWorkflowCapability(entry)) return text("Workflow capability", "Workflow 管理能力");
  if (kind === "workflow") return text("Workflow", "工作流");
  if (kind === "application") return text("Application", "应用");
  if (kind === "agentic_function") return text("Workflow", "工作流");
  if (kind === "runtime_primitive") return text("Agentic Programming primitive", "Agentic Programming 原语");
  if (kind === "vanilla_function") return text("Tool", "工具");
  if (!kind && entry && !entry.callable_name) return text("Source module", "源码模块");
  return text("Tool", "工具");
}

function usesWorkflowIcon(entry: ProgramSourceEntry) {
  return entry.program_kind === "workflow" || isWorkflowCapability(entry);
}

function EntryIcon({ entry, expanded }: { entry: ExplorerEntry; expanded: boolean }) {
  if (usesWorkflowIcon(entry)) return <Workflow size={15} className={fileStyles.treeIcon} />;
  if (entry.program_kind === "application") return <Boxes size={15} className={fileStyles.treeIcon} />;
  if (entry.program_kind?.endsWith("function")) return <Wrench size={15} className={fileStyles.treeIcon} />;
  if (entry.kind === "folder") {
    return expanded
      ? <FolderOpen size={15} className={fileStyles.treeIconFolder} />
      : <Folder size={15} className={fileStyles.treeIconFolder} />;
  }
  return <FileCode size={15} className={fileStyles.treeIcon} />;
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
  const [directories, setDirectories] = useState<Record<string, ExplorerEntry[]>>({});
  const [selected, setSelected] = useState<string | null>(null);
  const [logic, setLogic] = useState<LogicResponse | null>(null);
  const [loadingTree, setLoadingTree] = useState(true);
  const [loadingLogic, setLoadingLogic] = useState(false);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchMode, setSearchMode] = useState<ExplorerSearchMode>("filter");
  const [fuzzySearch, setFuzzySearch] = useState(true);
  const [searchMatchIndex, setSearchMatchIndex] = useState(0);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [view, setView] = useState<"tree" | "graph">("tree");
  const [callExpansion, setCallExpansion] = useState<Map<string, boolean>>(new Map());
  useEffect(() => setCallExpansion(new Map()), [selected]);
  const [reloadToken, setReloadToken] = useState(0);
  const [meta, setMeta] = useState<FunctionsMeta>({ favorites: [], folders: {}, icons: {} });
  const explorerRef = useRef<HTMLElement>(null);

  const loadDirectory = useCallback(async (path: string, signal?: AbortSignal) => {
    const query = path ? `?${new URLSearchParams({ path })}` : "";
    const response = await jsonFetch<ExplorerResponse>(`/api/programs/explorer${query}`, { signal });
    setDirectories((current) => ({ ...current, [path]: response.entries }));
    return response;
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    let cancelled = false;
    async function initialise() {
      setLoadingTree(true);
      setError("");
      setDirectories({});
      setExpanded(new Set());
      try {
        const root = await loadDirectory("", controller.signal);
        if (cancelled) return;
        const sourceDefault = root.default_selection ?? null;
        const initial = sourceDefault;
        const parents = initial ? parentPaths(initial) : [];
        for (const parent of parents) {
          await loadDirectory(parent, controller.signal);
        }
        if (cancelled) return;
        setExpanded(new Set(parents));
        setSelected(initial);
      } catch (cause) {
        if ((cause as Error).name !== "AbortError") setError(text("Programs could not be loaded.", "无法加载 Programs。"));
      } finally {
        if (!cancelled) setLoadingTree(false);
      }
    }
    void initialise();
    return () => {
      cancelled = true;
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

  useEffect(() => {
    const controller = new AbortController();
    let cancelled = false;
    if (!selected) {
      setLogic(null);
      return () => controller.abort();
    }
    setLogic(null);
    setLoadingLogic(true);
    setError("");
    const query = new URLSearchParams({ path: selectedEntry?.logic_path || selected });
    let inFlight = false;
    const refreshLogic = () => {
      if (inFlight || document.visibilityState === "hidden") return;
      inFlight = true;
      void jsonFetch<LogicResponse>(`/api/programs/logic?${query}`, { signal: controller.signal, cache: "no-store" })
      .then((result) => {
        if (!cancelled) {
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
          setLogic(null);
          setError(text("Call logic could not be loaded.", "无法加载调用逻辑。"));
        }
      })
      .finally(() => {
        inFlight = false;
        if (!cancelled) setLoadingLogic(false);
      });
    };
    refreshLogic();
    const timer = window.setInterval(refreshLogic, 5000);
    document.addEventListener("visibilitychange", refreshLogic);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", refreshLogic);
      controller.abort();
    };
  }, [selected, selectedEntry, text]);

  const selectedNode = logic?.nodes.find((node) => node.id === logic.root) ?? null;
  const invocationName = selectedEntry?.callable_name || "";
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
  const visiblePaths = useMemo(
    () => visibleSearchPaths([...treeEntriesByPath.keys()], search, fuzzySearch),
    [fuzzySearch, search, treeEntriesByPath],
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
    const timer = setTimeout(() => {
      explorerRef.current?.querySelector<HTMLElement>(
        `[data-program-path="${CSS.escape(currentSearchPath)}"]`,
      )?.scrollIntoView({ block: "nearest" });
    });
    return () => clearTimeout(timer);
  }, [currentSearchPath]);

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
    if (!entry || entry.kind !== "folder" || directories[path]) return;
    try {
      await loadDirectory(path);
    } catch {
      setError(text("Folder could not be loaded.", "无法加载文件夹。"));
    }
  }

  function toggleDirectory(entry: ExplorerEntry) {
    if (entry.program_kind) return;
    const opening = !expanded.has(entry.path);
    setExpanded((previous) => {
      const next = new Set(previous);
      if (opening) next.add(entry.path);
      else next.delete(entry.path);
      return next;
    });
    if (opening) void loadOpenedDirectory(entry.path);
  }

  function moveSearchResult(delta: number) {
    if (!searchMatches.length) return;
    setSearchMatchIndex((index) => (index + delta + searchMatches.length) % searchMatches.length);
  }

  function renderProgramDirectory(path: string, depth: number): ReactNode {
    const entries = directories[path];
    if (!entries) {
      return <div className={fileStyles.treeHint} style={{ paddingLeft: EXPLORER_BASE_PAD + 28 + depth * EXPLORER_INDENT }}>{text("Loading…", "加载中…")}</div>;
    }
    return entries.map((entry) => {
      if (search.trim() && searchMode === "filter" && !visiblePaths.has(entry.path)) return null;
      const isOpen = expanded.has(entry.path);
      const hasVisibleChild = [...visiblePaths].some((candidate) => candidate.startsWith(`${entry.path}/`));
      const displayOpen = isOpen || (searchMode === "filter" && Boolean(search.trim()) && hasVisibleChild);
      const current = currentSearchPath === entry.path;
      const isBranch = entry.kind === "folder" && !entry.program_kind;
      return (
        <div className={fileStyles.treeNode} key={entry.path}>
          <div
            className={`${fileStyles.treeRow} ${selected === (entry.logic_path || entry.path) || selected === entry.path ? fileStyles.treeRowSelected : ""}`}
            style={{ paddingLeft: EXPLORER_BASE_PAD + depth * EXPLORER_INDENT }}
            data-program-path={entry.path}
            title={entry.path}
            onClick={() => {
              if (entry.kind === "file" || entry.program_kind || entry.callable_name) {
                setSelected(entry.logic_path || entry.path);
              }
              if (isBranch) toggleDirectory(entry);
            }}
          >
            <EntryIcon entry={entry} expanded={displayOpen} />
            <ExplorerMatchText className={fileStyles.treeName} value={catalogLabel(entry, text)} query={search} fuzzy={fuzzySearch} current={current} />
          </div>
          {isBranch && displayOpen ? (
            <div
              className={fileStyles.treeKids}
              style={{ "--guide-x": `${EXPLORER_BASE_PAD + 8 + depth * EXPLORER_INDENT}px` } as CSSProperties}
            >
              {renderProgramDirectory(entry.path, depth + 1)}
            </div>
          ) : null}
        </div>
      );
    });
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

  const workspace = (
        <div className={styles.workspace}>
          <aside ref={explorerRef} className={styles.explorer} data-testid="programs-explorer">
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
            <div className={fileStyles.treeBody} aria-label={text("Programs catalog", "Programs 目录")}>
              {loadingTree
                ? <div className={fileStyles.treeHint}>{text("Loading…", "加载中…")}</div>
                : search.trim() && searchMode === "filter" && searchMatches.length === 0
                  ? <div className={fileStyles.treeHint}>{text("No matches", "无匹配")}</div>
                  : renderProgramDirectory("", 0)}
            </div>
          </aside>
          <section className={styles.logicPane}>
            {error ? <div className={styles.empty} role="alert"><span className={styles.error}>{error}</span></div> : !selected ? (
              <div className={styles.empty}><Network size={32} /><strong>{text("No Programs found", "没有找到 Program")}</strong><span>{text("Add a Tool, Workflow, or Application under openprogram/programs.", "请在 openprogram/programs 下添加 Tool、Workflow 或 Application。")}</span></div>
            ) : loadingLogic ? (
              <div className={styles.empty}><RefreshCw className={styles.spin} size={25} /><span>{text("Loading call logic…", "正在加载调用逻辑…")}</span></div>
            ) : !logic || !selectedNode ? (
              <div className={styles.empty}><Network size={32} /><span>{text("Call logic is unavailable.", "调用逻辑不可用。")}</span></div>
            ) : (
              <div className={styles.logicContent}>
                <div className={styles.breadcrumb}>{ROOT_LABEL}/{selectedNode.path}</div>
                <div className={styles.entityHeader}>
                  <span className={styles.entityIcon}>{usesWorkflowIcon(selectedEntry || selectedNode) ? <Workflow size={20} /> : selectedNode.program_kind === "application" ? <Boxes size={20} /> : invocationName ? <Wrench size={20} /> : <FileCode size={20} />}</span>
                  <div><h2>{selectedNode.name}</h2><p>{kindLabel(selectedNode.program_kind, text, selectedEntry || selectedNode)}</p></div>
                  {invocationName ? (
                    <div className={styles.entityActions}>
                      <Button
                        variant="ghost"
                        size="icon"
                        aria-label={isFavorite ? text("Unfavorite", "取消收藏") : text("Favorite", "收藏")}
                        aria-pressed={isFavorite}
                        title={isFavorite ? text("Unfavorite", "取消收藏") : text("Favorite", "收藏")}
                        onClick={() => void toggleFavorite()}
                      >
                        <Star className={isFavorite ? styles.favoriteIconActive : styles.favoriteIcon} fill={isFavorite ? "currentColor" : "none"} />
                      </Button>
                      <Button onClick={() => router.push(`/chat?${new URLSearchParams({ run: invocationName, cat: selectedNode.program_kind || "" })}`)}>
                        {text("Use", "使用")}
                      </Button>
                    </div>
                  ) : null}
                </div>
                <div className={styles.summary}><span>{logic.edges.filter((edge) => edge.source === logic.root).length} {text("direct calls", "个直接调用")}</span><span>{Math.max(0, logic.nodes.length - 1)} {text("reachable nodes", "个可达节点")}</span><span>{logic.edges.length} {text("edges", "条边")}</span></div>
                {logic.analysis_kind === "function_calls" ? <div className={styles.noCalls}>{text("Current source · possible function calls, not execution order · refreshes automatically. External libraries and object methods are not expanded.", "当前源码 · 展示可能的函数调用，不代表执行顺序 · 自动刷新。外部库与对象方法不展开。")}</div> : null}
                {logic.analysis_complete === false ? <div className={styles.analysisWarning} role="status">{text("Incomplete analysis", "分析不完整")}: {logic.analysis_warnings?.join(", ")}</div> : null}
                <div className={styles.logicToolbar}><div><strong>{text("Call logic", "调用逻辑")}</strong><small>{logic.analysis_kind === "function_calls" ? text("Source-derived function calls", "从当前源码识别的函数调用") : text("Static Program imports and Agentic Programming calls", "静态 Program import 与 Agentic Programming 调用")}</small></div><div className={styles.viewSwitch} aria-label={text("Call logic view", "调用逻辑视图")}><button className={view === "tree" ? styles.viewActive : ""} type="button" aria-pressed={view === "tree"} onClick={() => setView("tree")}><GitBranch size={13} />Call tree</button><button className={view === "graph" ? styles.viewActive : ""} type="button" aria-pressed={view === "graph"} onClick={() => setView("graph")}><Network size={13} />Graph</button></div></div>
                {view === "tree" ? (
                  <div className={styles.callTree} data-testid="programs-call-tree">
                    {callTree.rows.map(({ key, node, depth, cycle, reference, ancestorContinuations, isLast, conditional, expandable, isExpanded }) => {
                      const Row = expandable ? "button" : "div";
                      return <Row key={key} type={expandable ? "button" : undefined} aria-expanded={expandable ? isExpanded : undefined} onClick={expandable ? () => setCallExpansion(current => new Map(current).set(node.id, !isExpanded)) : undefined} className={`${styles.callRow} ${expandable ? styles.callToggle : ""} ${depth === 0 ? styles.callRoot : ""} ${reference ? styles.callReference : ""}`} style={{ "--call-depth": depth } as CSSProperties} data-edge-target={depth ? node.id : undefined}>
                      {depth > 0 ? <span className={styles.callGuides} aria-hidden="true">
                        {ancestorContinuations.map((continues, index) => <i key={index} className={continues ? styles.callGuideActive : ""} />)}
                        <i className={isLast ? styles.callGuideLast : styles.callGuideBranch} />
                      </span> : null}
                      <span className={styles.callIcon}>{expandable ? (isExpanded ? <ChevronDown size={15} aria-hidden="true" /> : <ChevronRight size={15} aria-hidden="true" />) : usesWorkflowIcon(node) ? <Workflow size={15} /> : node.program_kind === "application" ? <Boxes size={15} /> : node.program_kind ? <Wrench size={15} /> : <FileCode size={15} />}</span><span className={styles.callLabel}><strong>{node.name}</strong><small>{node.path}</small></span><em>{conditional ? text("conditional", "条件调用") : cycle ? "cycle" : reference ? "reference" : depth === 0 ? "root" : depth === 1 ? "direct" : "transitive"}</em>
                    </Row>;})}
                    {callTree.truncated ? <div className={styles.noCalls}>{text("Additional references are hidden.", "其余引用已隐藏。")}</div> : null}
                    {logic.nodes.length === 1 && logic.analysis_complete !== false ? <div className={styles.noCalls}>{text("This Program has no detected Program or Agentic Programming calls.", "未检测到这个 Program 对其他 Program 或 Agentic Programming 原语的调用。")}</div> : null}
                  </div>
                ) : (
                  <div className={styles.callGraph} data-testid="programs-call-graph">
                    {graphLayout ? <div className={styles.graphCanvas} style={{ width: graphLayout.width, height: graphLayout.height }} aria-label={text("Program dependency graph", "Program 依赖图") }>
                      <svg className={styles.graphLines} width={graphLayout.width} height={graphLayout.height} aria-hidden="true">
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
                        <span className={styles.graphNodeIcon}>{usesWorkflowIcon(node) ? <Workflow size={15} /> : node.program_kind === "application" ? <Boxes size={15} /> : node.program_kind ? <Wrench size={15} /> : <FileCode size={15} />}</span>
                        <span><strong>{node.name}</strong><small>{node.path}</small></span>
                      </div>)}
                    </div> : null}
                  </div>
                )}
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
