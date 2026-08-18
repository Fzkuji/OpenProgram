"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { useRouter } from "next/navigation";
import { Tree, type NodeRendererProps, type TreeApi } from "react-arborist";
import useMeasure from "react-use-measure";
import {
  Boxes,
  ChevronRight,
  File,
  FileCode2,
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
import { SearchInput } from "@/components/ui/search-input";
import { useTranslation } from "@/lib/i18n";
import { jsonFetch } from "@/lib/net/fetch-client";
import { runtimeState } from "@/lib/runtime-bridge/state";
import { useFunctions } from "@/lib/state/functions-store";
import type { FunctionsMeta } from "@/lib/types";

import {
  buildCallTreeRows,
  type LogicResponse,
  type ProgramKind,
} from "./programs-logic";

import styles from "./programs-page.module.css";

type ExplorerEntry = {
  name: string;
  path: string;
  kind: "folder" | "file";
  program_kind: ProgramKind | null;
  has_children: boolean;
};

type ExplorerResponse = {
  path: string;
  entries: ExplorerEntry[];
  default_selection?: string | null;
};

type ProgramTreeEntry = ExplorerEntry & {
  children?: ProgramTreeEntry[];
};

const ROOT_LABEL = "openprogram/programs";

function parentPaths(path: string) {
  const parts = path.split("/");
  return parts.slice(0, -1).map((_, index) => parts.slice(0, index + 1).join("/"));
}

function kindLabel(kind: ProgramKind, text: (en: string, zh: string) => string) {
  if (kind === "workflow") return text("Workflow", "工作流");
  if (kind === "application") return text("Application", "应用");
  if (kind === "agentic_function") return text("Agentic Function", "Agentic 函数");
  return text("Function", "函数");
}

function EntryIcon({ entry, expanded }: { entry: ExplorerEntry; expanded: boolean }) {
  if (entry.program_kind === "workflow") return <Workflow size={15} />;
  if (entry.program_kind === "application") return <Boxes size={15} />;
  if (entry.program_kind?.endsWith("function")) return <Wrench size={15} />;
  if (entry.kind === "file") return entry.name.endsWith(".py") ? <FileCode2 size={15} /> : <File size={15} />;
  return expanded ? <FolderOpen size={15} /> : <Folder size={15} />;
}

function ProgramTreeNode({ node, style, dragHandle }: NodeRendererProps<ProgramTreeEntry>) {
  const entry = node.data;
  return (
    <div
      ref={dragHandle}
      className={`${styles.fileTreeNode} ${node.isSelected ? styles.fileTreeNodeSelected : ""} ${node.isOpen ? styles.fileTreeNodeOpen : ""}`}
      style={style}
      title={entry.path}
    >
      {node.isInternal ? (
        <button
          className={styles.fileTreeToggle}
          type="button"
          aria-label={node.isOpen ? "Collapse folder" : "Expand folder"}
          onClick={(event) => {
            event.stopPropagation();
            node.toggle();
          }}
        >
          <ChevronRight size={13} aria-hidden="true" />
        </button>
      ) : <span className={styles.fileTreeToggleSpacer} />}
      <span className={styles.fileTreeIcon}>
        <EntryIcon entry={entry} expanded={node.isOpen} />
      </span>
      <span className={styles.fileTreeName}>{entry.name}</span>
    </div>
  );
}

export function ProgramsPage() {
  const { t, text } = useTranslation();
  const router = useRouter();
  const [directories, setDirectories] = useState<Record<string, ExplorerEntry[]>>({});
  const [selected, setSelected] = useState<string | null>(null);
  const [logic, setLogic] = useState<LogicResponse | null>(null);
  const [loadingTree, setLoadingTree] = useState(true);
  const [loadingLogic, setLoadingLogic] = useState(false);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [view, setView] = useState<"tree" | "graph">("tree");
  const [reloadToken, setReloadToken] = useState(0);
  const [meta, setMeta] = useState<FunctionsMeta>({ favorites: [], folders: {}, icons: {} });
  const treeRef = useRef<TreeApi<ProgramTreeEntry>>();
  const [treeMeasureRef, treeBounds] = useMeasure();

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
      try {
        const root = await loadDirectory("", controller.signal);
        if (cancelled) return;
        const initial = root.default_selection ?? null;
        const parents = initial ? parentPaths(initial) : [];
        for (const parent of parents) {
          await loadDirectory(parent, controller.signal);
        }
        if (cancelled) return;
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
    const query = new URLSearchParams({ path: selected });
    void jsonFetch<LogicResponse>(`/api/programs/logic?${query}`, { signal: controller.signal })
      .then((result) => {
        if (!cancelled) setLogic(result);
      })
      .catch((cause) => {
        if (!cancelled && (cause as Error).name !== "AbortError") {
          setLogic(null);
          setError(text("Call logic could not be loaded.", "无法加载调用逻辑。"));
        }
      })
      .finally(() => {
        if (!cancelled) setLoadingLogic(false);
      });
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [selected, text]);

  const selectedNode = logic?.nodes.find((node) => node.id === logic.root) ?? null;
  const isFavorite = selectedNode ? meta.favorites.includes(selectedNode.name) : false;
  const callTree = useMemo(() => logic ? buildCallTreeRows(logic) : { rows: [], truncated: false }, [logic]);
  const treeEntriesByPath = useMemo(() => {
    const entries = new Map<string, ExplorerEntry>();
    for (const directoryEntries of Object.values(directories)) {
      for (const entry of directoryEntries) entries.set(entry.path, entry);
    }
    return entries;
  }, [directories]);
  const treeData = useMemo(() => {
    const build = (path: string): ProgramTreeEntry[] => (directories[path] ?? []).map((entry) => ({
      ...entry,
      children: entry.kind === "folder" ? build(entry.path) : undefined,
    }));
    return build("");
  }, [directories]);

  useEffect(() => {
    if (selected) treeRef.current?.openParents(selected);
  }, [selected, treeData]);

  async function loadOpenedDirectory(path: string) {
    const entry = treeEntriesByPath.get(path);
    if (!entry || entry.kind !== "folder" || directories[path]) return;
    try {
      await loadDirectory(path);
    } catch {
      setError(text("Folder could not be loaded.", "无法加载文件夹。"));
    }
  }

  async function refreshPrograms() {
    await fetch("/api/programs/refresh", { method: "POST" }).catch(() => undefined);
    setReloadToken((value) => value + 1);
  }

  async function toggleFavorite() {
    if (!selectedNode) return;
    const favorites = isFavorite
      ? meta.favorites.filter((name) => name !== selectedNode.name)
      : [...meta.favorites, selectedNode.name];
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

  return (
    <div className="main">
      <div className={managePageStyles.view}>
        <ManagePageHeader
          title={t("nav.programs")}
          toolbar={<SearchInput className="w-[min(320px,32vw)]" placeholder={text("Search loaded files…", "搜索已加载文件…")} value={search} onChange={setSearch} />}
          actions={[{ label: text("Refresh", "刷新"), onClick: () => { void refreshPrograms(); } }]}
        />
        <div className={styles.workspace}>
          <aside className={styles.explorer} data-testid="programs-explorer">
            <div ref={treeMeasureRef} className={styles.tree}>
              {loadingTree ? <div className={styles.treeMessage}>{text("Loading…", "加载中…")}</div> : (
                <Tree<ProgramTreeEntry>
                  ref={treeRef}
                  data={treeData}
                  idAccessor="path"
                  childrenAccessor="children"
                  width={treeBounds.width}
                  height={treeBounds.height}
                  rowHeight={28}
                  indent={16}
                  padding={6}
                  openByDefault={false}
                  selection={selected ?? undefined}
                  searchTerm={search}
                  searchMatch={(node, term) => node.data.name.toLowerCase().includes(term.trim().toLowerCase())}
                  aria-label={text("Programs files", "Programs 文件")}
                  rowClassName={styles.fileTreeRow}
                  disableMultiSelection
                  disableDrag
                  disableDrop
                  disableEdit
                  onToggle={(path) => { void loadOpenedDirectory(path); }}
                  onActivate={(node) => {
                    if (node.data.program_kind) setSelected(node.data.path);
                    else if (node.isInternal) node.toggle();
                  }}
                >
                  {ProgramTreeNode}
                </Tree>
              )}
            </div>
          </aside>
          <section className={styles.logicPane}>
            {error ? <div className={styles.empty} role="alert"><span className={styles.error}>{error}</span></div> : !selected ? (
              <div className={styles.empty}><Network size={32} /><strong>{text("No Programs found", "没有找到 Program")}</strong><span>{text("Add a Function, Workflow, or Application under openprogram/programs.", "请在 openprogram/programs 下添加 Function、Workflow 或 Application。")}</span></div>
            ) : loadingLogic ? (
              <div className={styles.empty}><RefreshCw className={styles.spin} size={25} /><span>{text("Loading call logic…", "正在加载调用逻辑…")}</span></div>
            ) : !logic || !selectedNode ? (
              <div className={styles.empty}><Network size={32} /><span>{text("Call logic is unavailable.", "调用逻辑不可用。")}</span></div>
            ) : (
              <div className={styles.logicContent}>
                <div className={styles.breadcrumb}>{ROOT_LABEL}/{selectedNode.path}</div>
                <div className={styles.entityHeader}>
                  <span className={styles.entityIcon}>{selectedNode.program_kind === "workflow" ? <Workflow size={20} /> : selectedNode.program_kind === "application" ? <Boxes size={20} /> : <Wrench size={20} />}</span>
                  <div><h2>{selectedNode.name}</h2><p>{kindLabel(selectedNode.program_kind, text)}</p></div>
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
                    <Button onClick={() => router.push(`/chat?${new URLSearchParams({ run: selectedNode.name, cat: selectedNode.program_kind })}`)}>
                      {text("Use", "使用")}
                    </Button>
                    <span className={styles.headBadge}>HEAD</span>
                  </div>
                </div>
                <div className={styles.summary}><span>{logic.edges.filter((edge) => edge.source === logic.root).length} {text("direct calls", "个直接调用")}</span><span>{Math.max(0, logic.nodes.length - 1)} {text("reachable Programs", "个可达 Program")}</span><span>{logic.edges.length} {text("edges", "条边")}</span></div>
                {logic.analysis_complete === false ? <div className={styles.analysisWarning} role="status">{text("Partial result: some source files were not analyzed because they exceed the analysis limits.", "部分结果：部分源码超过分析限制，未纳入调用关系。")}</div> : null}
                <div className={styles.logicToolbar}><div><strong>{text("Call logic", "调用逻辑")}</strong><small>{text("Static imports at the current source state", "当前源码状态下的静态 import")}</small></div><div className={styles.viewSwitch} aria-label={text("Call logic view", "调用逻辑视图")}><button className={view === "tree" ? styles.viewActive : ""} type="button" aria-pressed={view === "tree"} onClick={() => setView("tree")}><GitBranch size={13} />Call tree</button><button className={view === "graph" ? styles.viewActive : ""} type="button" aria-pressed={view === "graph"} onClick={() => setView("graph")}><Network size={13} />Graph</button></div></div>
                {view === "tree" ? (
                  <div className={styles.callTree} data-testid="programs-call-tree">
                    {callTree.rows.map(({ key, node, depth, cycle, reference }) => <div key={key} className={`${styles.callRow} ${depth === 0 ? styles.callRoot : ""} ${reference ? styles.callReference : ""}`} style={{ "--call-depth": depth } as CSSProperties} data-edge-target={depth ? node.id : undefined}><span className={styles.callIcon}>{node.program_kind === "workflow" ? <Workflow size={15} /> : node.program_kind === "application" ? <Boxes size={15} /> : <Wrench size={15} />}</span><span><strong>{node.name}</strong><small>{node.path}</small></span><em>{cycle ? "cycle" : reference ? "reference" : depth === 0 ? "root" : depth === 1 ? "direct" : "transitive"}</em></div>)}
                    {callTree.truncated ? <div className={styles.noCalls}>{text("Additional references are hidden.", "其余引用已隐藏。")}</div> : null}
                    {logic.nodes.length === 1 && logic.analysis_complete !== false ? <div className={styles.noCalls}>{text("This Program has no imports of another Program.", "这个 Program 没有导入其他 Program。")}</div> : null}
                  </div>
                ) : (
                  <div className={styles.callGraph} data-testid="programs-call-graph">
                    <div className={styles.graphEdges} aria-label={text("Graph connections", "图连接关系")}>{logic.edges.length ? logic.edges.map((edge) => {
                      const source = logic.nodes.find((node) => node.id === edge.source);
                      const target = logic.nodes.find((node) => node.id === edge.target);
                      return <div key={`${edge.source}->${edge.target}`} className={styles.graphEdge} data-edge={`${edge.source}->${edge.target}`}><span className={styles.graphNode}><strong>{source?.name ?? edge.source}</strong><small>{source ? kindLabel(source.program_kind, text) : edge.source}</small></span><ChevronRight size={16} /><span className={styles.graphNode}><strong>{target?.name ?? edge.target}</strong><small>{target ? kindLabel(target.program_kind, text) : edge.target}</small></span></div>;
                    }) : <div className={styles.graphNode}><strong>{selectedNode.name}</strong><small>{logic.analysis_complete === false ? text("Partial analysis", "分析不完整") : text("No outgoing calls", "没有向外调用")}</small></div>}</div>
                  </div>
                )}
              </div>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}
