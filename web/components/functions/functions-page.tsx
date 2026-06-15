"use client";

/**
 * /functions — Functions catalog page.
 *
 * No built-in categories: every entry is "a function". Organisation is
 * entirely user-driven — built-in folders (All / Favorites /
 * Uncategorized) + user folders with rename / delete; drag-and-drop
 * into folders; favourites toggle; per-function flat-icon override
 * (right-click → Change Icon…); search; sort; grid/list view toggle.
 */
import {
  cloneElement,
  isValidElement,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactElement,
} from "react";
import { useRouter } from "next/navigation";
import styles from "./functions-page.module.css";
import { cls, RenameInput, FolderNavRow } from "./functions-page-parts";
import { Button } from "@/components/ui/button";
import { useTranslation } from "@/lib/i18n";
import {
  type AnimatedNavIconHandle,
  FileTextIcon,
  FolderOpenIcon,
  FolderPlusIcon,
  FoldersIcon,
  HeartIcon,
} from "@/components/animated-icons";
import { CustomSelect } from "./custom-select";
import { CtxMenu, type CtxItem, type CtxMenuState } from "./ctx-menu";
import { IconPicker, normalizeIcon } from "./icon-picker";
import { FunctionCard, ToolCard, cardGridClass, cardListClass } from "./function-card";
import { useFolderMeta } from "./use-folder-meta";
import type { FunctionInfo, FunctionsMeta } from "./types";

export function FunctionsPage() {
  const { t, text, locale } = useTranslation();
  const router = useRouter();
  const [functions, setFunctions] = useState<FunctionInfo[]>([]);
  const [meta, setMeta] = useState<FunctionsMeta>({
    favorites: [],
    folders: {},
    icons: {},
  });
  const [folder, setFolder] = useState<string>("__all__");
  const [view, setView] = useState<"grid" | "list">("grid");
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<"name" | "recent">("name");
  const [filter, setFilter] = useState<"all" | "favorites">("all");
  const [ctx, setCtx] = useState<CtxMenuState | null>(null);
  const [creatingFolder, setCreatingFolder] = useState(false);
  const [renamingFolder, setRenamingFolder] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState<string | null>(null);
  const [iconPickerFor, setIconPickerFor] = useState<string | null>(null);
  const [tools, setTools] = useState<{ name: string; description: string; disabled?: boolean }[]>([]);
  const draggedRef = useRef<string | null>(null);

  // Initial data load (functions list + saved meta). ``signal`` is
  // optional so the manual "refresh" callers (if any are added later)
  // can still invoke without an abort. The effect below wires one
  // through so an unmount mid-fetch doesn't ``setFunctions`` on a
  // destroyed component.
  const reload = useCallback(async (signal?: AbortSignal) => {
    try {
      const [a, b, c] = await Promise.all([
        fetch("/api/functions", { signal }).then((r) => r.json()),
        fetch("/api/programs/meta", { signal }).then((r) => r.json()),
        fetch("/api/tools", { signal }).then((r) => r.json()).catch(() => []),
      ]);
      if (signal?.aborted) return;
      setFunctions(a as FunctionInfo[]);
      setTools(Array.isArray(c) ? c : []);
      const m = b as Partial<FunctionsMeta>;
      setMeta({
        favorites: m.favorites ?? [],
        folders: m.folders ?? {},
        icons: m.icons ?? {},
      });
    } catch (e) {
      if ((e as Error).name === "AbortError") return;
      setFunctions([]);
      setMeta({ favorites: [], folders: {}, icons: {} });
    }
  }, []);

  useEffect(() => {
    const ac = new AbortController();
    void reload(ac.signal);
    return () => ac.abort();
  }, [reload]);

  // Manual "refresh" — ask the backend to re-scan agentics/ for harnesses
  // installed since boot (clone / `programs install`), then reload the
  // list. The auto-watcher does this on its own; this button is the
  // reliable fallback (and instant feedback right after installing).
  const [refreshing, setRefreshing] = useState(false);
  const refreshPrograms = useCallback(async () => {
    setRefreshing(true);
    try {
      await fetch("/api/programs/refresh", { method: "POST" });
    } catch {
      /* ignore — reload below still reflects current state */
    }
    await reload();
    setRefreshing(false);
  }, [reload]);

  const saveMeta = useCallback(async (next: FunctionsMeta) => {
    setMeta(next);
    try {
      await fetch("/api/programs/meta", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(next),
      });
    } catch {
      /* ignore */
    }
    // Replace window.programsMeta with a fresh object so React
    // subscribers (useWindowGlobals does a ref-identity compare) see
    // the change immediately. In-place mutation keeps the same ref
    // and the sidebar would stay stale until a manual page reload.
    const w = window as unknown as Record<string, unknown>;
    w.programsMeta = {
      favorites: [...next.favorites],
      folders: Object.fromEntries(
        Object.entries(next.folders).map(([k, v]) => [k, [...v]]),
      ),
      icons: { ...next.icons },
    };
    // Notify any non-polling listeners instantly.
    window.dispatchEvent(new CustomEvent("wah:meta-changed"));
    if (typeof w.renderFunctions === "function") (w.renderFunctions as () => void)();
  }, []);

  // Close context menu on any outside click.
  useEffect(() => {
    if (!ctx) return;
    const close = () => setCtx(null);
    document.addEventListener("click", close);
    return () => document.removeEventListener("click", close);
  }, [ctx]);

  // ---- helpers --------------------------------------------------------
  const isFavorite = (name: string) =>
    (meta.favorites || []).includes(name);

  function getFolderForProgram(name: string): string | null {
    for (const key of Object.keys(meta.folders)) {
      if ((meta.folders[key] || []).includes(name)) return key;
    }
    return null;
  }

  function getFunctionsInFolder(id: string): FunctionInfo[] {
    if (id === "__all__") return functions;
    if (id === "__uncategorized__") {
      const assigned = new Set<string>();
      for (const k of Object.keys(meta.folders))
        for (const n of meta.folders[k] || []) assigned.add(n);
      return functions.filter((p) => !assigned.has(p.name));
    }
    if (id === "__favorites__") {
      const fav = new Set(meta.favorites);
      return functions.filter((p) => fav.has(p.name));
    }
    const arr = new Set(meta.folders[id] || []);
    return functions.filter((p) => arr.has(p.name));
  }

  function formatDate(ts?: number): string {
    if (!ts) return "";
    const diff = Date.now() - ts * 1000;
    if (locale === "zh") {
      if (diff < 3_600_000) return Math.floor(diff / 60_000) + " 分钟前";
      if (diff < 86_400_000) return Math.floor(diff / 3_600_000) + " 小时前";
      if (diff < 604_800_000) return Math.floor(diff / 86_400_000) + " 天前";
      return new Date(ts * 1000).toLocaleDateString("zh-CN");
    }
    if (diff < 3_600_000) return Math.floor(diff / 60_000) + "m ago";
    if (diff < 86_400_000) return Math.floor(diff / 3_600_000) + "h ago";
    if (diff < 604_800_000) return Math.floor(diff / 86_400_000) + "d ago";
    return new Date(ts * 1000).toLocaleDateString();
  }

  // ---- derived list ---------------------------------------------------
  const visibleFunctions = useMemo(() => {
    let arr = getFunctionsInFolder(folder);
    const q = search.toLowerCase();
    if (q) {
      arr = arr.filter(
        (p) =>
          p.name.toLowerCase().includes(q) ||
          (p.description || "").toLowerCase().includes(q),
      );
    }
    if (filter === "favorites") {
      const fav = new Set(meta.favorites);
      arr = arr.filter((p) => fav.has(p.name));
    }
    if (sort === "recent")
      arr = [...arr].sort((a, b) => (b.mtime || 0) - (a.mtime || 0));
    else arr = [...arr].sort((a, b) => a.name.localeCompare(b.name));
    return arr;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [functions, meta, folder, search, filter, sort]);

  // ---- actions --------------------------------------------------------
  // Return to the conversation the user came from (not a blank /chat),
  // so the run opens inside that existing session.
  function chatTarget(): string {
    return (
      (window as unknown as { __lastChatPath?: string }).__lastChatPath ||
      "/chat"
    );
  }

  function runProgram(name: string, category?: string) {
    // SPA soft-nav. Stash the request on window; the page-shell
    // hand-off hook drains __pendingRunFunction to open the fn-form.
    (window as unknown as {
      __pendingRunFunction?: { name: string; cat: string };
    }).__pendingRunFunction = { name, cat: category || "" };
    router.push(chatTarget());
    // Explicit drain signal: covers the "target route == current route"
    // case (already on a chat page → push doesn't change pathname, so the
    // route-keyed drain in usePendingRunFunction wouldn't re-fire). The
    // listener (PageShell) is always mounted on chat routes, so dispatch
    // immediately; the cross-route case is covered by the pathname drain.
    try { window.dispatchEvent(new CustomEvent("op:run-function")); } catch { /* noop */ }
  }

  /** Toggle a built-in tool on/off for the LLM. ``enabled`` = the new
   *  desired state. Writes ``tools.disabled.<name>`` via /api/settings
   *  (agent_tools() hides disabled tools from every LLM toolset), and
   *  optimistically patches local state so the switch flips instantly. */
  function toggleTool(name: string, enabled: boolean) {
    setTools((prev) =>
      prev.map((t) => (t.name === name ? { ...t, disabled: !enabled } : t)),
    );
    void fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key: `tools.disabled.${name}`, value: enabled }),
    }).catch(() => {
      // revert on failure
      setTools((prev) =>
        prev.map((t) => (t.name === name ? { ...t, disabled: enabled } : t)),
      );
    });
  }

  function editProgram(name: string) {
    (window as unknown as {
      __pendingRunFunction?: { name: string; cat: string; fn?: string };
    }).__pendingRunFunction = { name: "edit", cat: "", fn: name };
    router.push(chatTarget());
  }

  // Folder + favorites + icons mutations live in ./use-folder-meta.
  // The hook needs the live meta + saveMeta and the current ``folder``
  // selection (so delete/create/rename can move the user off a folder
  // they just removed or renamed).
  const {
    toggleFav,
    moveToFolder,
    deleteFolder,
    createFolder,
    renameFolder,
    applyIcon,
  } = useFolderMeta(meta, saveMeta, folder, setFolder);

  // ---- DnD ------------------------------------------------------------
  function onProgramDragStart(e: React.DragEvent, name: string) {
    draggedRef.current = name;
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", name);
  }
  function onFolderDragOver(e: React.DragEvent, target: string) {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    setDragOver(target);
  }
  function onFolderDragLeave() {
    setDragOver(null);
  }
  function onFolderDrop(e: React.DragEvent, target: string) {
    e.preventDefault();
    setDragOver(null);
    const name = draggedRef.current;
    draggedRef.current = null;
    if (!name) return;
    if (
      target === "__all__" ||
      target === "__uncategorized__" ||
      target === "__favorites__"
    ) {
      moveToFolder(name, null);
    } else {
      moveToFolder(name, target);
    }
  }

  // ---- Context menus --------------------------------------------------
  function programCtx(e: React.MouseEvent, name: string) {
    e.preventDefault();
    e.stopPropagation();
    const fav = isFavorite(name);
    const items: CtxItem[] = [
      {
        label: fav ? `★ ${text("Unfavorite", "取消收藏")}` : `☆ ${text("Favorite", "收藏")}`,
        action: () =>
          toggleFav(name, {
            stopPropagation: () => {},
          } as unknown as React.MouseEvent),
      },
      { label: text("Change icon...", "更换图标..."), action: () => setIconPickerFor(name) },
      { label: `✎ ${text("Edit...", "编辑...")}`, action: () => editProgram(name) },
      { type: "sep" },
    ];
    for (const f of Object.keys(meta.folders).sort()) {
      items.push({
        label: text(`Move to ${f}`, `移动到 ${f}`),
        action: () => moveToFolder(name, f),
      });
    }
    if (getFolderForProgram(name)) {
      items.push({
        label: text("Remove from folder", "从文件夹中移除"),
        action: () => moveToFolder(name, null),
      });
    }
    setCtx({ x: e.clientX, y: e.clientY, items });
  }

  function folderCtx(e: React.MouseEvent, name: string) {
    e.preventDefault();
    e.stopPropagation();
    setCtx({
      x: e.clientX,
      y: e.clientY,
      items: [
        { label: text("Rename", "重命名"), action: () => setRenamingFolder(name) },
        { label: t("sidebar.delete"), action: () => deleteFolder(name) },
        { type: "sep" },
        {
          label: text("New folder", "新建文件夹"),
          action: () => setCreatingFolder(true),
        },
      ],
    });
  }

  function sidebarCtx(e: React.MouseEvent) {
    if ((e.target as HTMLElement).closest(`.${styles.folderItem}`)) return;
    e.preventDefault();
    setCtx({
      x: e.clientX,
      y: e.clientY,
      items: [
        {
          label: text("New folder", "新建文件夹"),
          action: () => setCreatingFolder(true),
        },
      ],
    });
  }

  function contentCtx(e: React.MouseEvent) {
    if ((e.target as HTMLElement).closest("[data-function-card]")) return;
    e.preventDefault();
    setCtx({
      x: e.clientX,
      y: e.clientY,
      items: [
        {
          label: text("New folder", "新建文件夹"),
          action: () => setCreatingFolder(true),
        },
      ],
    });
  }

  // ---- Render ---------------------------------------------------------
  const existingNames = useMemo(
    () => new Set(functions.map((p) => p.name)),
    [functions],
  );
  const liveCount = (names: string[] | undefined) =>
    (names || []).filter((n) => existingNames.has(n)).length;
  const builtinFolders = [
    {
      id: "__all__",
      name: text("All Functions", "全部函数"),
      icon: <FileTextIcon size={16} />,
      count: functions.length,
    },
    {
      id: "__favorites__",
      name: text("Favorites", "收藏"),
      icon: <HeartIcon size={16} />,
      count: liveCount(meta.favorites),
    },
    {
      id: "__uncategorized__",
      name: text("Uncategorized", "未分类"),
      icon: <FolderOpenIcon size={16} />,
      count: getFunctionsInFolder("__uncategorized__").length,
    },
  ];
  const userFolders = Object.keys(meta.folders).sort();

  return (
    <div className="main">
      <div className={styles.view}>
        <div className={styles.topbar}>
          <span className={styles.title}>{t("nav.functions")}</span>
          <div className={styles.toolbar}>
            <input
              type="text"
              className={styles.search}
              placeholder={text("Search functions...", "搜索函数...")}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            <CustomSelect
              value={sort}
              onChange={(v) => setSort(v)}
              options={[
                { value: "name", label: text("Sort: Name", "排序：名称") },
                { value: "recent", label: text("Sort: Recent", "排序：最近") },
              ]}
            />
            <CustomSelect
              value={filter}
              onChange={(v) => setFilter(v)}
              options={[
                { value: "all", label: text("All", "全部") },
                { value: "favorites", label: text("Favorites", "收藏") },
              ]}
            />
            <Button
              variant="outline"
              size="sm"
              onClick={() => setView((v) => (v === "grid" ? "list" : "grid"))}
            >
              {view === "grid" ? text("List", "列表") : text("Grid", "网格")}
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={refreshPrograms}
              disabled={refreshing}
              title={text(
                "Re-scan for newly installed programs",
                "重新扫描新安装的程序",
              )}
            >
              {refreshing ? text("Refreshing…", "刷新中…") : text("Refresh", "刷新")}
            </Button>
          </div>
        </div>

        <div className={styles.body}>
          <div
            className={styles.foldersNav}
            onContextMenu={sidebarCtx}
          >
            {builtinFolders.map((f) => (
              <FolderNavRow
                key={f.id}
                icon={f.icon}
                name={f.name}
                count={f.count}
                active={folder === f.id}
                dragOver={dragOver === f.id}
                onClick={() => setFolder(f.id)}
                onDragOver={(e) => onFolderDragOver(e, f.id)}
                onDragLeave={onFolderDragLeave}
                onDrop={(e) => onFolderDrop(e, f.id)}
              />
            ))}
            <div className={styles.folderSep} />
            {userFolders.map((name) => {
              if (renamingFolder === name) {
                return (
                  <div
                    key={name}
                    className={cls(
                      styles.folderItem,
                      folder === name && styles.active,
                    )}
                  >
                    <span className={styles.folderIcon}><FoldersIcon size={16} /></span>
                    <RenameInput
                      initial={name}
                      onCommit={(n) => {
                        setRenamingFolder(null);
                        renameFolder(name, n);
                      }}
                      onCancel={() => setRenamingFolder(null)}
                    />
                  </div>
                );
              }
              const count = liveCount(meta.folders[name]);
              return (
                <FolderNavRow
                  key={name}
                  icon={<FoldersIcon size={16} />}
                  name={name}
                  count={count}
                  active={folder === name}
                  dragOver={dragOver === name}
                  onClick={() => setFolder(name)}
                  onDragOver={(e) => onFolderDragOver(e, name)}
                  onDragLeave={onFolderDragLeave}
                  onDrop={(e) => onFolderDrop(e, name)}
                  onContextMenu={(e) => folderCtx(e, name)}
                />
              );
            })}
            {creatingFolder && (
              <div className={cls(styles.folderItem, styles.active)}>
                <span className={styles.folderIcon}><FoldersIcon size={16} /></span>
                <RenameInput
                  initial=""
                  placeholder={text("New folder", "新建文件夹")}
                  onCommit={(n) => {
                    setCreatingFolder(false);
                    createFolder(n);
                  }}
                  onCancel={() => setCreatingFolder(false)}
                />
              </div>
            )}
            <div
              className={cls(styles.folderItem, styles.folderNew)}
              onClick={() => setCreatingFolder(true)}
              title={text("Create a new folder", "新建文件夹")}
            >
              <span className={styles.folderIcon}><FolderPlusIcon size={16} /></span>
              <span className={styles.folderName}>{text("New folder", "新建文件夹")}</span>
            </div>
          </div>

          <div
            className={styles.content}
            onContextMenu={contentCtx}
          >
            {visibleFunctions.length === 0 ? (
              <div className={styles.empty}>
                <div className={styles.emptyIcon}><FolderOpenIcon size={40} /></div>
                <div className={styles.emptyText}>
                  {search ? text("No matching functions", "没有匹配的函数") : text("This folder is empty", "这个文件夹是空的")}
                </div>
                <div className={styles.emptyHint}>
                  {text("Drag functions here to organize", "拖动函数到这里进行整理")}
                </div>
              </div>
            ) : (
              <>
                {folder === "__all__" && !search && (
                  <div className={styles.toolsHeader}>
                    {text("Agentic functions", "Agentic 函数")}
                    <span className={styles.toolsHint}>
                      {text(
                        "Invoked with @ — create, edit, and organize them here.",
                        "通过 @ 调用——可在此创建、编辑和整理。",
                      )}
                    </span>
                  </div>
                )}
              <div className={view === "grid" ? cardGridClass : cardListClass}>
                {visibleFunctions.map((p) => (
                  <FunctionCard
                    key={p.name}
                    p={p}
                    icon={normalizeIcon(meta.icons[p.name])}
                    fav={isFavorite(p.name)}
                    folderName={getFolderForProgram(p.name)}
                    formatDate={formatDate}
                    onClick={() => runProgram(p.name, p.category)}
                    onContextMenu={(e) => programCtx(e, p.name)}
                    onDragStart={(e) => onProgramDragStart(e, p.name)}
                    onToggleFav={(e) => toggleFav(p.name, e)}
                    onChangeIcon={(e) => {
                      e.stopPropagation();
                      setIconPickerFor(p.name);
                    }}
                  />
                ))}
              </div>
              </>
            )}
            {tools.length > 0 && folder === "__all__" && !search && (
              <div className={styles.toolsSection}>
                <div className={styles.toolsHeader}>
                  {text("Built-in tools", "内置工具")}
                  <span className={styles.toolsHint}>
                    {text(
                      "Toggle a tool off to hide it from the agent.",
                      "关掉某个工具即不再给 Agent 使用。",
                    )}
                  </span>
                </div>
                <div className={view === "grid" ? cardGridClass : cardListClass}>
                  {tools.map((tl) => (
                    <ToolCard
                      key={tl.name}
                      name={tl.name}
                      description={tl.description}
                      enabled={!tl.disabled}
                      onToggle={(on) => toggleTool(tl.name, on)}
                    />
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
      {ctx && <CtxMenu state={ctx} onClose={() => setCtx(null)} />}
      {iconPickerFor && (
        <IconPicker
          name={iconPickerFor}
          current={normalizeIcon(meta.icons[iconPickerFor])}
          onPick={(icon) => applyIcon(iconPickerFor, icon)}
          onClose={() => setIconPickerFor(null)}
        />
      )}
    </div>
  );
}
