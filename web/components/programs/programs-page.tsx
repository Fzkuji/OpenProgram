"use client";

/**
 * /programs — Functions catalog page.
 *
 * No built-in categories: every entry is "a function". Organisation is
 * entirely user-driven — built-in folders (All / Favorites /
 * Uncategorized) + user folders with rename / delete; drag-and-drop
 * into folders; favourites toggle; per-function emoji icon override
 * (right-click → Change Icon…); search; sort; grid/list view toggle.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import styles from "./programs-page.module.css";
import { Button } from "@/components/ui/button";
import { CustomSelect } from "./custom-select";
import { CtxMenu, type CtxItem, type CtxMenuState } from "./ctx-menu";
import { IconPicker, DEFAULT_ICON } from "./icon-picker";
import { ProgramCard, cardGridClass, cardListClass } from "./program-card";

type Program = {
  name: string;
  category?: string;
  description?: string;
  mtime?: number;
};

interface ProgramsMeta {
  favorites: string[];
  folders: Record<string, string[]>;
  icons: Record<string, string>;
}

export function ProgramsPage() {
  const router = useRouter();
  const [programs, setPrograms] = useState<Program[]>([]);
  const [meta, setMeta] = useState<ProgramsMeta>({
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
  const draggedRef = useRef<string | null>(null);

  // Initial data load (functions list + saved meta).
  const reload = useCallback(async () => {
    try {
      const [a, b] = await Promise.all([
        fetch("/api/functions").then((r) => r.json()),
        fetch("/api/programs/meta").then((r) => r.json()),
      ]);
      setPrograms(a as Program[]);
      const m = b as Partial<ProgramsMeta>;
      setMeta({
        favorites: m.favorites ?? [],
        folders: m.folders ?? {},
        icons: m.icons ?? {},
      });
    } catch {
      setPrograms([]);
      setMeta({ favorites: [], folders: {}, icons: {} });
    }
  }, []);

  useEffect(() => {
    reload();
  }, [reload]);

  const saveMeta = useCallback(async (next: ProgramsMeta) => {
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
    // Sync the legacy sidebar's in-memory programsMeta and re-render.
    const w = window as unknown as Record<string, unknown>;
    if (typeof w.programsMeta === "object") {
      (w.programsMeta as Record<string, unknown>).favorites = [...next.favorites];
      (w.programsMeta as Record<string, unknown>).folders = { ...next.folders };
      (w.programsMeta as Record<string, unknown>).icons = { ...next.icons };
    }
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

  function getProgramsInFolder(id: string): Program[] {
    if (id === "__all__") return programs;
    if (id === "__uncategorized__") {
      const assigned = new Set<string>();
      for (const k of Object.keys(meta.folders))
        for (const n of meta.folders[k] || []) assigned.add(n);
      return programs.filter((p) => !assigned.has(p.name));
    }
    if (id === "__favorites__") {
      const fav = new Set(meta.favorites);
      return programs.filter((p) => fav.has(p.name));
    }
    const arr = new Set(meta.folders[id] || []);
    return programs.filter((p) => arr.has(p.name));
  }

  function formatDate(ts?: number): string {
    if (!ts) return "";
    const diff = Date.now() - ts * 1000;
    if (diff < 3_600_000) return Math.floor(diff / 60_000) + "m ago";
    if (diff < 86_400_000) return Math.floor(diff / 3_600_000) + "h ago";
    if (diff < 604_800_000) return Math.floor(diff / 86_400_000) + "d ago";
    return new Date(ts * 1000).toLocaleDateString();
  }

  // ---- derived list ---------------------------------------------------
  const visiblePrograms = useMemo(() => {
    let arr = getProgramsInFolder(folder);
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
  }, [programs, meta, folder, search, filter, sort]);

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
    // hand-off effect drains __pendingRunFunction to open the fn-form.
    (window as unknown as {
      __pendingRunFunction?: { name: string; cat: string };
    }).__pendingRunFunction = { name, cat: category || "" };
    router.push(chatTarget());
  }

  function editProgram(name: string) {
    (window as unknown as {
      __pendingRunFunction?: { name: string; cat: string; fn?: string };
    }).__pendingRunFunction = { name: "edit", cat: "", fn: name };
    router.push(chatTarget());
  }

  function cloneMeta(): ProgramsMeta {
    return {
      favorites: [...meta.favorites],
      folders: Object.fromEntries(
        Object.entries(meta.folders).map(([k, v]) => [k, [...v]]),
      ),
      icons: { ...meta.icons },
    };
  }

  async function toggleFav(name: string, e: React.MouseEvent) {
    e.stopPropagation();
    const next = cloneMeta();
    const idx = next.favorites.indexOf(name);
    if (idx >= 0) next.favorites.splice(idx, 1);
    else next.favorites.push(name);
    await saveMeta(next);
  }

  async function moveToFolder(name: string, target: string | null) {
    const next = cloneMeta();
    for (const k of Object.keys(next.folders)) {
      next.folders[k] = next.folders[k].filter((x) => x !== name);
    }
    if (target) next.folders[target] = [...(next.folders[target] || []), name];
    await saveMeta(next);
  }

  async function deleteFolder(name: string) {
    if (
      !confirm(
        `Delete folder "${name}"? Functions will be moved to Uncategorized.`,
      )
    )
      return;
    const next = cloneMeta();
    delete next.folders[name];
    if (folder === name) setFolder("__all__");
    await saveMeta(next);
  }

  async function createFolder(name: string) {
    name = name.trim();
    if (!name || meta.folders[name]) return;
    const next = cloneMeta();
    next.folders[name] = [];
    await saveMeta(next);
    setFolder(name);
  }

  async function renameFolder(oldName: string, newName: string) {
    newName = newName.trim();
    if (!newName || newName === oldName || meta.folders[newName]) return;
    const next = cloneMeta();
    next.folders[newName] = next.folders[oldName] || [];
    delete next.folders[oldName];
    if (folder === oldName) setFolder(newName);
    await saveMeta(next);
  }

  async function applyIcon(name: string, icon: string | null) {
    const next = cloneMeta();
    if (icon) next.icons[name] = icon;
    else delete next.icons[name];
    await saveMeta(next);
  }

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
        label: fav ? "★ Unfavorite" : "☆ Favorite",
        action: () =>
          toggleFav(name, {
            stopPropagation: () => {},
          } as unknown as React.MouseEvent),
      },
      { label: "🎨 Change icon...", action: () => setIconPickerFor(name) },
      { label: "✎ Edit...", action: () => editProgram(name) },
      { type: "sep" },
    ];
    for (const f of Object.keys(meta.folders).sort()) {
      items.push({
        label: `📁 Move to ${f}`,
        action: () => moveToFolder(name, f),
      });
    }
    if (getFolderForProgram(name)) {
      items.push({
        label: "📂 Remove from folder",
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
        { label: "Rename", action: () => setRenamingFolder(name) },
        { label: "Delete", action: () => deleteFolder(name) },
        { type: "sep" },
        {
          label: "📁 New folder",
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
          label: "📁 New folder",
          action: () => setCreatingFolder(true),
        },
      ],
    });
  }

  function contentCtx(e: React.MouseEvent) {
    if ((e.target as HTMLElement).closest("[data-program-card]")) return;
    e.preventDefault();
    setCtx({
      x: e.clientX,
      y: e.clientY,
      items: [
        {
          label: "📁 New folder",
          action: () => setCreatingFolder(true),
        },
      ],
    });
  }

  // ---- Render ---------------------------------------------------------
  const existingNames = useMemo(
    () => new Set(programs.map((p) => p.name)),
    [programs],
  );
  const liveCount = (names: string[] | undefined) =>
    (names || []).filter((n) => existingNames.has(n)).length;
  const builtinFolders = [
    {
      id: "__all__",
      name: "All Functions",
      icon: "📋",
      count: programs.length,
    },
    {
      id: "__favorites__",
      name: "Favorites",
      icon: "★",
      count: liveCount(meta.favorites),
    },
    {
      id: "__uncategorized__",
      name: "Uncategorized",
      icon: "📂",
      count: getProgramsInFolder("__uncategorized__").length,
    },
  ];
  const userFolders = Object.keys(meta.folders).sort();

  return (
    <div className="main">
      <div className={styles.view}>
        <div className={styles.topbar}>
          <span className={styles.title}>Functions</span>
          <div className={styles.toolbar}>
            <input
              type="text"
              className={styles.search}
              placeholder="Search functions..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            <CustomSelect
              value={sort}
              onChange={(v) => setSort(v)}
              options={[
                { value: "name", label: "Sort: Name" },
                { value: "recent", label: "Sort: Recent" },
              ]}
            />
            <CustomSelect
              value={filter}
              onChange={(v) => setFilter(v)}
              options={[
                { value: "all", label: "All" },
                { value: "favorites", label: "Favorites" },
              ]}
            />
            <Button
              variant="outline"
              size="sm"
              onClick={() => setView((v) => (v === "grid" ? "list" : "grid"))}
            >
              {view === "grid" ? "List" : "Grid"}
            </Button>
          </div>
        </div>

        <div className={styles.body}>
          <div
            className={styles.foldersNav}
            onContextMenu={sidebarCtx}
          >
            {builtinFolders.map((f) => (
              <div
                key={f.id}
                className={cls(
                  styles.folderItem,
                  folder === f.id && styles.active,
                  dragOver === f.id && styles.dragOver,
                )}
                onClick={() => setFolder(f.id)}
                onDragOver={(e) => onFolderDragOver(e, f.id)}
                onDragLeave={onFolderDragLeave}
                onDrop={(e) => onFolderDrop(e, f.id)}
              >
                <span className={styles.folderIcon}>{f.icon}</span>
                <span className={styles.folderName}>{f.name}</span>
                <span className={styles.folderCount}>{f.count}</span>
              </div>
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
                    <span className={styles.folderIcon}>📁</span>
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
                <div
                  key={name}
                  className={cls(
                    styles.folderItem,
                    folder === name && styles.active,
                    dragOver === name && styles.dragOver,
                  )}
                  onClick={() => setFolder(name)}
                  onDragOver={(e) => onFolderDragOver(e, name)}
                  onDragLeave={onFolderDragLeave}
                  onDrop={(e) => onFolderDrop(e, name)}
                  onContextMenu={(e) => folderCtx(e, name)}
                >
                  <span className={styles.folderIcon}>📁</span>
                  <span className={styles.folderName}>{name}</span>
                  <span className={styles.folderCount}>{count}</span>
                </div>
              );
            })}
            {creatingFolder && (
              <div className={cls(styles.folderItem, styles.active)}>
                <span className={styles.folderIcon}>📁</span>
                <RenameInput
                  initial=""
                  placeholder="New folder"
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
              title="Create a new folder"
            >
              <span className={styles.folderIcon}>+</span>
              <span className={styles.folderName}>New folder</span>
            </div>
          </div>

          <div
            className={styles.content}
            onContextMenu={contentCtx}
          >
            {visiblePrograms.length === 0 ? (
              <div className={styles.empty}>
                <div className={styles.emptyIcon}>📂</div>
                <div className={styles.emptyText}>
                  {search ? "No matching functions" : "This folder is empty"}
                </div>
                <div className={styles.emptyHint}>
                  Drag functions here to organize
                </div>
              </div>
            ) : (
              <div className={view === "grid" ? cardGridClass : cardListClass}>
                {visiblePrograms.map((p) => (
                  <ProgramCard
                    key={p.name}
                    p={p}
                    icon={meta.icons[p.name] || DEFAULT_ICON}
                    fav={isFavorite(p.name)}
                    folderName={getFolderForProgram(p.name)}
                    formatDate={formatDate}
                    onClick={() => runProgram(p.name, p.category)}
                    onContextMenu={(e) => programCtx(e, p.name)}
                    onDragStart={(e) => onProgramDragStart(e, p.name)}
                    onToggleFav={(e) => toggleFav(p.name, e)}
                  />
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
      {ctx && <CtxMenu state={ctx} onClose={() => setCtx(null)} />}
      {iconPickerFor && (
        <IconPicker
          name={iconPickerFor}
          current={meta.icons[iconPickerFor] || DEFAULT_ICON}
          onPick={(icon) => applyIcon(iconPickerFor, icon)}
          onClose={() => setIconPickerFor(null)}
        />
      )}
    </div>
  );
}

function RenameInput({
  initial,
  placeholder,
  onCommit,
  onCancel,
}: {
  initial: string;
  placeholder?: string;
  onCommit: (name: string) => void;
  onCancel: () => void;
}) {
  const [value, setValue] = useState(initial);
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => {
    ref.current?.focus();
    ref.current?.select();
  }, []);
  return (
    <input
      ref={ref}
      className={styles.renameInput}
      type="text"
      placeholder={placeholder}
      value={value}
      onChange={(e) => setValue(e.target.value)}
      onKeyDown={(e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          onCommit(value);
        } else if (e.key === "Escape") {
          e.preventDefault();
          onCancel();
        }
        e.stopPropagation();
      }}
      onBlur={() => onCommit(value)}
    />
  );
}

function cls(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}
