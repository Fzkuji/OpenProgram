"use client";

/**
 * /programs — port of web/public/html/programs.html +
 * web/public/js/programs/programs.js (440 lines).
 *
 * Native React + scoped CSS module. Behaviors preserved verbatim:
 * built-in folders (All / Favorites / Uncategorized) + user folders
 * with rename / delete; program drag-and-drop into folders;
 * favourites toggle; search; filter; sort; category grouping;
 * grid/list view toggle; right-click context menus on programs,
 * folders, sidebar background, and content background.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import styles from "./programs-page.module.css";

interface Program {
  name: string;
  category?: string;
  description?: string;
  mtime?: number;
}

interface ProgramsMeta {
  favorites: string[];
  folders: Record<string, string[]>;
}

const CAT_ICONS: Record<string, string> = {
  app: "\u{1F4E6}",
  meta: "\u{1F6E0}",
  builtin: "⚙",
  generated: "⚙",
  user: "✎",
};
const CAT_LABELS: Record<string, string> = {
  app: "Applications",
  meta: "Meta Functions",
  builtin: "Built-in",
  generated: "Generated",
  user: "User",
};
const CAT_ORDER = ["app", "generated", "user", "meta", "builtin"] as const;

interface CtxItem {
  type?: "sep";
  label?: string;
  action?: () => void;
}
interface CtxMenuState {
  x: number;
  y: number;
  items: CtxItem[];
}

export function ProgramsPage() {
  const router = useRouter();
  const [programs, setPrograms] = useState<Program[]>([]);
  const [meta, setMeta] = useState<ProgramsMeta>({ favorites: [], folders: {} });
  const [folder, setFolder] = useState<string>("__all__");
  const [view, setView] = useState<"grid" | "list">("grid");
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<"category" | "name" | "recent">("category");
  const [filter, setFilter] = useState<
    "all" | "app" | "meta" | "builtin" | "favorites"
  >("all");
  const [ctx, setCtx] = useState<CtxMenuState | null>(null);
  const [creatingFolder, setCreatingFolder] = useState(false);
  const [renamingFolder, setRenamingFolder] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState<string | null>(null);
  const draggedRef = useRef<string | null>(null);

  // Initial data load (functions list + saved meta).
  const reload = useCallback(async () => {
    try {
      const [a, b] = await Promise.all([
        fetch("/api/functions").then((r) => r.json()),
        fetch("/api/programs/meta").then((r) => r.json()),
      ]);
      setPrograms(a as Program[]);
      const m = b as ProgramsMeta;
      setMeta({
        favorites: m.favorites ?? [],
        folders: m.folders ?? {},
      });
    } catch {
      setPrograms([]);
      setMeta({ favorites: [], folders: {} });
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
    } else if (filter !== "all") {
      arr = arr.filter((p) => p.category === filter);
    }
    if (sort === "recent")
      arr = [...arr].sort((a, b) => (b.mtime || 0) - (a.mtime || 0));
    else if (sort === "category")
      arr = [...arr].sort(
        (a, b) =>
          (CAT_ORDER.indexOf((a.category || "user") as typeof CAT_ORDER[number]) +
            (CAT_ORDER.indexOf((a.category || "user") as typeof CAT_ORDER[number]) < 0 ? 9 : 0)) -
          (CAT_ORDER.indexOf((b.category || "user") as typeof CAT_ORDER[number]) +
            (CAT_ORDER.indexOf((b.category || "user") as typeof CAT_ORDER[number]) < 0 ? 9 : 0)),
      );
    else arr = [...arr].sort((a, b) => a.name.localeCompare(b.name));
    return arr;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [programs, meta, folder, search, filter, sort]);

  // ---- actions --------------------------------------------------------
  function runProgram(name: string, category?: string) {
    // SPA soft-nav. Stash the request on window; page-shell calls
    // __triggerPendingRunFunction after the chat-route effect fires,
    // and init.js drains __pendingRunFunction to open the fn-form.
    (window as unknown as {
      __pendingRunFunction?: { name: string; cat: string };
    }).__pendingRunFunction = { name, cat: category || "" };
    router.push("/chat");
  }

  function editProgram(name: string) {
    (window as unknown as {
      __pendingRunFunction?: { name: string; cat: string; fn?: string };
    }).__pendingRunFunction = { name: "edit", cat: "", fn: name };
    router.push("/chat");
  }

  async function toggleFav(name: string, e: React.MouseEvent) {
    e.stopPropagation();
    const next = { ...meta, favorites: [...(meta.favorites || [])] };
    const idx = next.favorites.indexOf(name);
    if (idx >= 0) next.favorites.splice(idx, 1);
    else next.favorites.push(name);
    await saveMeta(next);
  }

  async function moveToFolder(name: string, target: string | null) {
    const next: ProgramsMeta = {
      favorites: [...meta.favorites],
      folders: Object.fromEntries(
        Object.entries(meta.folders).map(([k, v]) => [
          k,
          v.filter((x) => x !== name),
        ]),
      ),
    };
    if (target) next.folders[target] = [...(next.folders[target] || []), name];
    await saveMeta(next);
  }

  async function deleteFolder(name: string) {
    if (
      !confirm(
        `Delete folder "${name}"? Programs will be moved to Uncategorized.`,
      )
    )
      return;
    const next: ProgramsMeta = {
      favorites: [...meta.favorites],
      folders: { ...meta.folders },
    };
    delete next.folders[name];
    if (folder === name) setFolder("__all__");
    await saveMeta(next);
  }

  async function createFolder(name: string) {
    name = name.trim();
    if (!name || meta.folders[name]) return;
    const next: ProgramsMeta = {
      favorites: [...meta.favorites],
      folders: { ...meta.folders, [name]: [] },
    };
    await saveMeta(next);
    setFolder(name);
  }

  async function renameFolder(oldName: string, newName: string) {
    newName = newName.trim();
    if (!newName || newName === oldName || meta.folders[newName]) return;
    const next: ProgramsMeta = {
      favorites: [...meta.favorites],
      folders: { ...meta.folders },
    };
    next.folders[newName] = next.folders[oldName] || [];
    delete next.folders[oldName];
    if (folder === oldName) setFolder(newName);
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
    if ((e.target as HTMLElement).closest(`.${styles.card}`)) return;
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
  const builtinFolders = [
    {
      id: "__all__",
      name: "All Programs",
      icon: "📋",
      count: programs.length,
    },
    {
      id: "__favorites__",
      name: "Favorites",
      icon: "★",
      count: (meta.favorites || []).length,
    },
    {
      id: "__uncategorized__",
      name: "Uncategorized",
      icon: "📂",
      count: getProgramsInFolder("__uncategorized__").length,
    },
  ];
  const userFolders = Object.keys(meta.folders).sort();

  const grouped = useMemo(() => {
    if (sort !== "category") return null;
    const out: Record<string, Program[]> = {};
    for (const p of visiblePrograms) {
      const c = p.category || "user";
      (out[c] ??= []).push(p);
    }
    return out;
  }, [visiblePrograms, sort]);

  return (
    <div className="main">
      <div className={styles.view}>
        <div className={styles.topbar}>
          <span className={styles.title}>Programs</span>
          <div className={styles.toolbar}>
            <input
              type="text"
              className={styles.search}
              placeholder="Search programs..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            <select
              className={styles.select}
              value={sort}
              onChange={(e) => setSort(e.target.value as typeof sort)}
            >
              <option value="category">Sort: Category</option>
              <option value="name">Sort: Name</option>
              <option value="recent">Sort: Recent</option>
            </select>
            <select
              className={styles.select}
              value={filter}
              onChange={(e) => setFilter(e.target.value as typeof filter)}
            >
              <option value="all">All</option>
              <option value="app">Applications</option>
              <option value="meta">Meta Functions</option>
              <option value="builtin">Built-in</option>
              <option value="favorites">Favorites</option>
            </select>
            <button
              className={styles.toolbarBtn}
              onClick={() => setView((v) => (v === "grid" ? "list" : "grid"))}
            >
              {view === "grid" ? "List" : "Grid"}
            </button>
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
              const count = (meta.folders[name] || []).length;
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
                  {search ? "No matching programs" : "This folder is empty"}
                </div>
                <div className={styles.emptyHint}>
                  Drag programs here to organize
                </div>
              </div>
            ) : sort === "category" && grouped ? (
              <>
                {CAT_ORDER.filter((c) => grouped[c]).map((c) => (
                  <div className={styles.catSection} key={c}>
                    <div className={styles.catHeader}>
                      {CAT_LABELS[c] || c} ({grouped[c].length})
                    </div>
                    <div className={view === "grid" ? styles.grid : styles.list}>
                      {grouped[c].map((p) => (
                        <Card
                          key={p.name}
                          p={p}
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
                  </div>
                ))}
              </>
            ) : (
              <div className={view === "grid" ? styles.grid : styles.list}>
                {visiblePrograms.map((p) => (
                  <Card
                    key={p.name}
                    p={p}
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
    </div>
  );
}

function Card({
  p,
  fav,
  folderName,
  formatDate,
  onClick,
  onContextMenu,
  onDragStart,
  onToggleFav,
}: {
  p: Program;
  fav: boolean;
  folderName: string | null;
  formatDate: (ts?: number) => string;
  onClick: () => void;
  onContextMenu: (e: React.MouseEvent) => void;
  onDragStart: (e: React.DragEvent) => void;
  onToggleFav: (e: React.MouseEvent) => void;
}) {
  const cat = p.category || "user";
  const desc = p.description ? p.description.split(".")[0] : "";
  return (
    <div
      className={styles.card}
      draggable
      onDragStart={onDragStart}
      onClick={onClick}
      onContextMenu={onContextMenu}
    >
      <div className={cls(styles.cardIcon, styles[cat])}>
        {CAT_ICONS[cat] || "✎"}
      </div>
      <div className={styles.cardInfo}>
        <div className={styles.cardName}>{p.name}</div>
        <div className={styles.cardDesc}>{desc}</div>
        <div className={styles.cardMeta}>
          {cat}
          {folderName ? ` · 📁 ${folderName}` : ""}
          {" · "}
          {formatDate(p.mtime)}
        </div>
      </div>
      <button
        className={cls(styles.favBtn, fav && styles.favorited)}
        onClick={onToggleFav}
      >
        {fav ? "★" : "☆"}
      </button>
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

function CtxMenu({
  state,
  onClose,
}: {
  state: CtxMenuState;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState({ left: state.x, top: state.y });

  useEffect(() => {
    const r = ref.current?.getBoundingClientRect();
    if (!r) return;
    let { left, top } = pos;
    if (r.right > window.innerWidth) left = window.innerWidth - r.width - 4;
    if (r.bottom > window.innerHeight)
      top = window.innerHeight - r.height - 4;
    if (left !== pos.left || top !== pos.top) setPos({ left, top });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div
      ref={ref}
      className={styles.ctxMenu}
      style={{ left: pos.left, top: pos.top }}
      onClick={(e) => e.stopPropagation()}
    >
      {state.items.map((it, i) =>
        it.type === "sep" ? (
          <div key={i} className={styles.ctxSep} />
        ) : (
          <div
            key={i}
            className={styles.ctxItem}
            onClick={() => {
              onClose();
              it.action?.();
            }}
          >
            {it.label}
          </div>
        ),
      )}
    </div>
  );
}

function cls(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}
